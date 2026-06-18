"""
app/screens/key_manager.py — KEY MANAGER screen.

Lists all FSC keys on a chosen drive's fsc_keys/ directory, with:
  - per-key pad budget (red/amber/normal cell colour)
  - selection-driven detail panel: created date, mode, per-pad list, Lorenz
    portrait of the key
  - actions: refresh portrait, delete key, export public profile (.fscpub)

A delete is irreversible — any .fsc encrypted by that key becomes lost
forever, so the confirmation dialog spells this out in Slovak.
"""

import datetime
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui  import QColor, QBrush, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QFrame, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea,
    QMessageBox, QFileDialog, QPlainTextEdit,
)

from app                import fileformat, keystore
from app.fileformat     import load_keyfile

# Thresholds (re-used as cell colours)
PADS_RED   = 0
PADS_AMBER = 5

KEYSTORE_DIRNAME = keystore.KEYSTORE_DIRNAME


# ── portrait worker (off-thread Lorenz render) ────────────────────────────────

class _PortraitWorker(QThread):
    """Render a Lorenz portrait for the selected key off the UI thread."""
    finished_path = Signal(str, str)      # (key_id, png path)
    failed        = Signal(str, str)      # (key_id, error msg)

    def __init__(self, master_key: bytes, key_id: str, save_path: str, parent=None):
        super().__init__(parent)
        self.master_key = master_key
        self.key_id     = key_id
        self.save_path  = save_path

    def run(self):
        try:
            from art.lorenz_portrait import lorenz_portrait_from_master
            # 60k steps is fast (<2 s) and still produces a recognisable butterfly
            out = lorenz_portrait_from_master(self.master_key, save_path=self.save_path, n_steps=60_000)
            self.finished_path.emit(self.key_id, out)
        except Exception as exc:
            self.failed.emit(self.key_id, f"{type(exc).__name__}: {exc}")


# ── KEY MANAGER screen ────────────────────────────────────────────────────────

class KeyManagerScreen(QWidget):
    """Browse, inspect, delete and export FSC keys from a drive."""

    # display columns in the key table
    COL_LABEL  = 0
    COL_KEY_ID = 1
    COL_MODE   = 2
    COL_PADS   = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drives:    list = []
        self._keys:      list = []         # list of manifest dicts
        self._selected_idx: Optional[int] = None
        self._portrait_worker: Optional[_PortraitWorker] = None
        self._build_ui()
        self.refresh_keys()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        root = QVBoxLayout(content)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(14)

        title = QLabel("KEY MANAGER")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        subtitle = QLabel("správa šifrovacích kľúčov")
        subtitle.setObjectName("SubtitleLabel")
        root.addWidget(subtitle)

        # ── warning banner (hidden until a key falls below threshold) ─────
        self.warn_banner = QLabel("")
        self.warn_banner.setWordWrap(True)
        self.warn_banner.setStyleSheet(
            "QLabel { background: #2a1305; color: #f59e0b; "
            "border: 1px solid #f59e0b; border-radius: 6px; padding: 8px 12px; }"
        )
        self.warn_banner.hide()
        root.addWidget(self.warn_banner)

        # ── drive selector ───────────────────────────────────────────────
        drive_row = QHBoxLayout()
        drive_row.setSpacing(10)
        drive_row.addWidget(self._field_label("DRIVE"))
        self.drive_combo = QComboBox()
        self.drive_combo.setObjectName("MonoEdit")
        self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
        drive_row.addWidget(self.drive_combo, stretch=1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("IconButton")
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.setToolTip("Rescan drives")
        self.refresh_btn.clicked.connect(self.refresh_keys)
        drive_row.addWidget(self.refresh_btn)
        root.addLayout(drive_row)

        # ── key table ─────────────────────────────────────────────────────
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["LABEL", "KEY_ID", "MODE", "PADS"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setStyleSheet(
            "QTableWidget {"
            "  background: #0d0d0d; color: #e8e8e8;"
            "  alternate-background-color: #131313;"
            "  selection-background-color: #c1272d;"
            "  selection-color: #ffffff;"
            "  gridline-color: #1a1a1a;"
            "  border: 1px solid #1a1a1a; border-radius: 6px;"
            "  font-family: 'JetBrains Mono','Consolas',monospace;"
            "  font-size: 12px;"
            "}"
            "QHeaderView::section {"
            "  background: #0a0a0a; color: #c1272d; padding: 6px;"
            "  border: 0; border-bottom: 1px solid #2a2a2a;"
            "  font-weight: bold; letter-spacing: 1.2px; font-size: 10px;"
            "}"
            "QTableWidget::item { padding: 6px 8px; }"
        )
        hdr = self.table.horizontalHeader()
        # All columns interactive with explicit widths — Stretch inside a
        # parent QScrollArea causes the table to grow past the viewport and
        # push the right-side columns off-screen.
        for col in (self.COL_LABEL, self.COL_KEY_ID, self.COL_MODE, self.COL_PADS):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        self.table.setColumnWidth(self.COL_LABEL,  300)
        self.table.setColumnWidth(self.COL_KEY_ID, 110)
        self.table.setColumnWidth(self.COL_MODE,   130)
        self.table.setColumnWidth(self.COL_PADS,    90)
        # cap the table width so the columns always fit visibly
        self.table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.table.setMinimumHeight(180)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self.table)

        # ── divider ───────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #1a1a1a; background: #1a1a1a; max-height: 1px;")
        root.addWidget(line)

        # ── detail panel ──────────────────────────────────────────────────
        self.detail_header = QLabel("KEY DETAIL")
        self.detail_header.setObjectName("SectionLabel")
        self.detail_header.hide()
        root.addWidget(self.detail_header)

        self.detail_frame = QFrame()
        d_lay = QGridLayout(self.detail_frame)
        d_lay.setHorizontalSpacing(14)
        d_lay.setVerticalSpacing(6)
        d_lay.setColumnStretch(1, 1)
        d_lay.setColumnStretch(2, 0)

        self._detail_rows: dict[str, QLabel] = {}
        for r, (k, lbl) in enumerate((
            ("key_id",   "KEY ID"),
            ("label",    "LABEL"),
            ("created",  "CREATED"),
            ("mode",     "MODE"),
            ("canvas",   "CANVAS"),
            ("maxlen",   "MAX MSG"),
            ("padfile",  "KEYFILE"),
        )):
            hdr_lbl = QLabel(lbl)
            hdr_lbl.setStyleSheet("color: #c1272d; font-weight: bold; "
                                  "letter-spacing: 1.2px; font-size: 9px;")
            hdr_lbl.setFixedWidth(120)
            d_lay.addWidget(hdr_lbl, r, 0)
            val = QLabel("—")
            val.setStyleSheet("color: #ccc; font-family: 'JetBrains Mono','Consolas',monospace;")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setWordWrap(True)
            d_lay.addWidget(val, r, 1)
            self._detail_rows[k] = val

        # Lorenz portrait on the right column, spanning all rows
        self.portrait_label = QLabel()
        self.portrait_label.setObjectName("PortraitLabel")
        self.portrait_label.setAlignment(Qt.AlignCenter)
        self.portrait_label.setFixedSize(280, 200)
        self.portrait_label.setText("(žiadny kľúč nevybraný)")
        self.portrait_label.setStyleSheet(
            "QLabel { background: #050505; color: #555; "
            "border: 1px solid #2a2a2a; border-radius: 6px; "
            "font-style: italic; font-size: 11px; }"
        )
        d_lay.addWidget(self.portrait_label, 0, 2, 7, 1, alignment=Qt.AlignTop)

        self.detail_frame.hide()
        root.addWidget(self.detail_frame)

        # ── pads sub-table ───────────────────────────────────────────────
        self.pads_header = QLabel("OTP PADS")
        self.pads_header.setObjectName("SectionLabel")
        self.pads_header.hide()
        root.addWidget(self.pads_header)

        self.pads_text = QPlainTextEdit()
        self.pads_text.setReadOnly(True)
        self.pads_text.setMaximumHeight(140)
        self.pads_text.setStyleSheet(
            "QPlainTextEdit { background: #0d0d0d; color: #c0c0c0; "
            "border: 1px solid #1a1a1a; border-radius: 6px; "
            "font-family: 'JetBrains Mono','Consolas',monospace; font-size: 11px; "
            "padding: 6px; }"
        )
        self.pads_text.hide()
        root.addWidget(self.pads_text)

        # ── action buttons ────────────────────────────────────────────────
        actions = QHBoxLayout()
        actions.setSpacing(10)

        self.portrait_btn = QPushButton("🔄  Obnoviť portrét")
        self.portrait_btn.setEnabled(False)
        self.portrait_btn.clicked.connect(self._on_refresh_portrait_clicked)
        actions.addWidget(self.portrait_btn)

        self.export_btn = QPushButton("📤  Export verejný profil")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_clicked)
        actions.addWidget(self.export_btn)

        self.delete_btn = QPushButton("🗑  Vymazať kľúč")
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet(
            "QPushButton { border: 1px solid #ef4444; color: #ef4444; }"
            "QPushButton:hover { background: #ef4444; color: #ffffff; }"
            "QPushButton:disabled { color: #444; border-color: #2a2a2a; }"
        )
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        actions.addWidget(self.delete_btn)

        actions.addStretch(1)
        root.addLayout(actions)

        # ── status ────────────────────────────────────────────────────────
        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusInfo")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status_label)

        root.addStretch(0)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #c1272d; font-weight: bold; "
                          "letter-spacing: 1.5px; font-size: 10px;")
        lbl.setFixedWidth(120)
        return lbl

    # ── status helper ─────────────────────────────────────────────────────

    def _set_status(self, text: str, kind: str = "info"):
        self.status_label.setText(text)
        obj = {"ok": "StatusOK", "error": "StatusError", "info": "StatusInfo"}.get(kind, "StatusInfo")
        self.status_label.setObjectName(obj)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    # ── drive scan + key list ─────────────────────────────────────────────

    def refresh_keys(self):
        """Reload drive list, then reload keys for the currently selected drive."""
        prior_path = self.drive_combo.currentData() if self.drive_combo.count() else None

        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()
        self._drives = keystore.list_scannable_drives()
        if not self._drives:
            self.drive_combo.addItem("(no scannable drives)", None)
        for d in self._drives:
            prefix = "💾 " if d.removable else "🖴 "
            self.drive_combo.addItem(prefix + d.display(), d.path)
        # restore prior selection if possible
        if prior_path:
            for i, d in enumerate(self._drives):
                if d.path == prior_path:
                    self.drive_combo.setCurrentIndex(i)
                    break
        self.drive_combo.blockSignals(False)

        self._on_drive_changed(self.drive_combo.currentIndex())

    def _on_drive_changed(self, _idx: int):
        path = self.drive_combo.currentData()
        if not path:
            self._keys = []
        else:
            try:
                self._keys = keystore.list_keys(path)
            except Exception as exc:
                self._keys = []
                self._set_status(f"✗  could not scan {path}: {exc}", "error")
        self._populate_table()
        self._update_warn_banner()
        self._clear_detail()

    def _populate_table(self):
        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(len(self._keys))

        for row, m in enumerate(self._keys):
            label   = m.get("label", "?")
            key_id  = m.get("key_id", "?")
            mode    = m.get("isotope_mode", "stable")
            mode_str = "∞ stable" if mode == "stable" else "⏱ ephemeral"
            pads_total = len(m.get("otp_pads", {}))
            pads_used  = sum(1 for p in m.get("otp_pads", {}).values()
                             if p.get("used", False))
            pads_left  = pads_total - pads_used
            pads_str   = f"{pads_left} / {pads_total}"

            cells = [
                QTableWidgetItem(label),
                QTableWidgetItem(key_id),
                QTableWidgetItem(mode_str),
                QTableWidgetItem(pads_str),
            ]
            for c in cells:
                c.setFlags(c.flags() & ~Qt.ItemIsEditable)

            # colour the pads cell
            if pads_left <= PADS_RED:
                cells[self.COL_PADS].setForeground(QBrush(QColor("#ef4444")))
            elif pads_left < PADS_AMBER:
                cells[self.COL_PADS].setForeground(QBrush(QColor("#f59e0b")))
            else:
                cells[self.COL_PADS].setForeground(QBrush(QColor("#4ade80")))

            # colour the mode cell
            if mode == "ephemeral":
                cells[self.COL_MODE].setForeground(QBrush(QColor("#f59e0b")))

            for col, item in enumerate(cells):
                self.table.setItem(row, col, item)

        self.table.blockSignals(False)
        if self._keys:
            self.table.selectRow(0)

    def _update_warn_banner(self):
        low_keys = [m for m in self._keys
                    if (len(m.get("otp_pads", {})) -
                        sum(1 for p in m.get("otp_pads", {}).values()
                            if p.get("used", False))) < PADS_AMBER]
        if not low_keys:
            self.warn_banner.hide()
            return
        if len(low_keys) == 1:
            m = low_keys[0]
            left = (len(m["otp_pads"]) -
                    sum(1 for p in m["otp_pads"].values() if p.get("used", False)))
            self.warn_banner.setText(
                f"⚠  Kľúč {m.get('label','?')!r} má len {left} voľných padov."
            )
        else:
            names = ", ".join(repr(m.get("label", "?")) for m in low_keys[:3])
            extra = f" (+{len(low_keys)-3} ďalších)" if len(low_keys) > 3 else ""
            self.warn_banner.setText(
                f"⚠  Niektoré kľúče majú málo voľných padov: {names}{extra}"
            )
        self.warn_banner.show()

    # ── selection / detail ────────────────────────────────────────────────

    def _clear_detail(self):
        self._selected_idx = None
        self.detail_header.hide()
        self.detail_frame.hide()
        self.pads_header.hide()
        self.pads_text.hide()
        self.portrait_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.portrait_label.clear()
        self.portrait_label.setText("(žiadny kľúč nevybraný)")
        self.portrait_label.setProperty("_pixmap", None)

    def _on_selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._clear_detail()
            return
        idx = rows[0].row()
        if idx < 0 or idx >= len(self._keys):
            self._clear_detail()
            return

        self._selected_idx = idx
        m = self._keys[idx]

        created_ts = float(m.get("created", 0))
        created_str = (
            datetime.datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S")
            if created_ts else "—"
        )
        mode = m.get("isotope_mode", "stable")
        mode_str = "∞ stable" if mode == "stable" else "⏱ ephemeral (sebazničujúci)"
        cs = m.get("canvas_size", "—")

        self._detail_rows["key_id"].setText(m.get("key_id", "—"))
        self._detail_rows["label"].setText(m.get("label", "—"))
        self._detail_rows["created"].setText(created_str)
        self._detail_rows["mode"].setText(mode_str)
        self._detail_rows["canvas"].setText(f"{cs} × {cs}" if cs != "—" else "—")
        self._detail_rows["maxlen"].setText(str(m.get("max_len", "—")) + "  chars")
        self._detail_rows["padfile"].setText(
            os.path.basename(m.get("_keyfile_path", "?"))
        )

        # per-pad list
        lines = []
        for pad_id in sorted(m.get("otp_pads", {})):
            info = m["otp_pads"][pad_id]
            tag = "🟥 použitý" if info.get("used", False) else "🟩 voľný"
            size_kb = int(info.get("size", 0)) / 1024
            lines.append(f"{pad_id}   {size_kb:>6.0f} kB   {tag}")
        self.pads_text.setPlainText("\n".join(lines) or "(žiadne pady)")

        self.detail_header.show()
        self.detail_frame.show()
        self.pads_header.show()
        self.pads_text.show()
        self.portrait_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        self.export_btn.setEnabled(True)

        # render portrait (cached if same key still selected)
        self._render_portrait_for(m)

    # ── portrait ──────────────────────────────────────────────────────────

    def _portrait_path(self, key_id: str) -> str:
        return os.path.join(tempfile.gettempdir(), f"fsc_mgr_portrait_{key_id}.png")

    def _render_portrait_for(self, manifest: dict, force: bool = False):
        key_id = manifest.get("key_id", "?")
        path   = self._portrait_path(key_id)

        if os.path.isfile(path) and not force:
            self._show_portrait(path)
            return

        try:
            mk = bytes.fromhex(manifest["master_key"])
        except Exception as exc:
            self.portrait_label.setText(f"(invalid key: {exc})")
            return

        self.portrait_label.setText("rendering…")
        self.portrait_label.setProperty("_pixmap", None)
        self._portrait_worker = _PortraitWorker(mk, key_id, path, parent=self)
        self._portrait_worker.finished_path.connect(self._on_portrait_ready)
        self._portrait_worker.failed.connect(self._on_portrait_failed)
        self._portrait_worker.start()

    def _on_portrait_ready(self, key_id: str, png_path: str):
        # only show if user is still on the same key
        if self._selected_idx is None:
            return
        if self._keys[self._selected_idx].get("key_id") != key_id:
            return
        self._show_portrait(png_path)

    def _on_portrait_failed(self, key_id: str, msg: str):
        if self._selected_idx is None:
            return
        if self._keys[self._selected_idx].get("key_id") != key_id:
            return
        self.portrait_label.setText(f"(portrait failed: {msg})")

    def _show_portrait(self, png_path: str):
        pix = QPixmap(png_path)
        if pix.isNull():
            self.portrait_label.setText("(could not load portrait)")
            return
        scaled = pix.scaled(self.portrait_label.size(),
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.portrait_label.setPixmap(scaled)
        self.portrait_label.setProperty("_pixmap", pix)

    def _on_refresh_portrait_clicked(self):
        if self._selected_idx is None:
            return
        self._render_portrait_for(self._keys[self._selected_idx], force=True)
        self._set_status("rendering Lorenz portrait…", "info")

    # ── delete ────────────────────────────────────────────────────────────

    def _on_delete_clicked(self):
        if self._selected_idx is None:
            return
        m = self._keys[self._selected_idx]
        label   = m.get("label", "?")
        key_id  = m.get("key_id", "?")
        keyfile = m.get("_keyfile_path")
        if not keyfile or not os.path.isfile(keyfile):
            self._set_status(f"✗  keyfile missing: {keyfile}", "error")
            return

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Vymazať kľúč")
        confirm.setIcon(QMessageBox.Warning)
        confirm.setText(
            f"Naozaj vymazať kľúč '{label}' ({key_id})?"
        )
        confirm.setInformativeText(
            "Správy zašifrované týmto kľúčom už NIKDY nepôjdu dešifrovať.\n"
            f"Vymaže sa keyfile + všetky pad_*.bin súbory.\n\n"
            f"Súbor: {keyfile}"
        )
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        confirm.setDefaultButton(QMessageBox.Cancel)
        if confirm.exec() != QMessageBox.Yes:
            return

        # delete pad files first
        keydir = os.path.dirname(keyfile)
        errors: list = []
        deleted: int = 0
        for pad_id, info in m.get("otp_pads", {}).items():
            pad_path = os.path.join(keydir, info.get("file", ""))
            try:
                if os.path.isfile(pad_path):
                    os.unlink(pad_path)
                    deleted += 1
            except OSError as exc:
                errors.append(f"{pad_path}: {exc}")
        try:
            os.unlink(keyfile)
        except OSError as exc:
            errors.append(f"{keyfile}: {exc}")

        # cached portrait (in temp) — best-effort cleanup
        try:
            cp = self._portrait_path(key_id)
            if os.path.isfile(cp):
                os.unlink(cp)
        except OSError:
            pass

        if errors:
            self._set_status(
                f"⚠  partial delete   removed {deleted} pad(s) + maybe keyfile, "
                f"errors: {'; '.join(errors[:3])}", "error",
            )
        else:
            self._set_status(
                f"✓  vymazaný kľúč {label!r} ({key_id})   "
                f"odstránených {deleted} padov + keyfile", "ok",
            )

        self.refresh_keys()

    # ── export public profile (.fscpub) ───────────────────────────────────

    def _on_export_clicked(self):
        if self._selected_idx is None:
            return
        m = self._keys[self._selected_idx]
        suggested = os.path.join(
            os.path.expanduser("~"),
            f"{m.get('key_id','key')}.fscpub",
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export verejný profil", suggested,
            "FSC public profile (*.fscpub);;All files (*.*)",
        )
        if not path:
            return
        if not path.endswith(".fscpub"):
            path += ".fscpub"

        pads = m.get("otp_pads", {})
        pads_total = len(pads)
        pads_unused = sum(1 for p in pads.values() if not p.get("used", False))

        # PUBLIC fields only — no master_key, no pad bytes, no key material
        public_profile = {
            "version":      1,
            "kind":         "fscpub",
            "key_id":       m.get("key_id"),
            "label":        m.get("label"),
            "isotope_mode": m.get("isotope_mode", "stable"),
            "canvas_size":  m.get("canvas_size"),
            "max_len":      m.get("max_len"),
            "created":      m.get("created"),
            "pads_total":   pads_total,
            "pads_unused":  pads_unused,
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(public_profile, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            self._set_status(f"✗  export failed: {exc}", "error")
            return
        self._set_status(
            f"✓  exportovaný verejný profil → {path}\n"
            f"    (žiadne tajné údaje — len key_id, label, mode, počty padov)",
            "ok",
        )
