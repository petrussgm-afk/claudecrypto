"""
app/screens/key_forge.py — KEY FORGE screen.

Creates a fresh 256-bit FSC master key + N OTP pads on the chosen drive,
then renders a Lorenz attractor portrait inline as a "key fingerprint".
"""

import os
import secrets
import tempfile

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui  import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QPushButton,
    QFileDialog, QFrame, QSizePolicy, QScrollArea,
)

from app import fileformat, keystore
from art.lorenz_portrait import lorenz_portrait_from_master


CUSTOM_FOLDER_SENTINEL = "__custom__"


# ── portrait worker (background thread) ───────────────────────────────────────

class PortraitWorker(QThread):
    """Renders a Lorenz portrait off the UI thread."""
    finished_path = Signal(str)
    failed        = Signal(str)

    def __init__(self, master_key: bytes, save_path: str, parent=None):
        super().__init__(parent)
        self.master_key = master_key
        self.save_path  = save_path

    def run(self):
        try:
            out = lorenz_portrait_from_master(self.master_key, save_path=self.save_path)
            self.finished_path.emit(out)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ── main screen ───────────────────────────────────────────────────────────────

class KeyForgeScreen(QWidget):
    """Create a new FSC key and write it (plus OTP pads) to the chosen drive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._custom_folder: str = ""
        self._worker: PortraitWorker | None = None
        self._build_ui()
        self._populate_drives()
        self._update_pad_size_label()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # outer: scroll area wrapping a single content widget so the portrait
        # is always reachable even when the window is short
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

        # ── title ─────────────────────────────────────────────────────────
        title = QLabel("KEY FORGE")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        subtitle = QLabel("vytvor nový šifrovací kľúč")
        subtitle.setObjectName("SubtitleLabel")
        root.addWidget(subtitle)

        # ── form grid ─────────────────────────────────────────────────────
        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)
        row = 0

        # label
        form.addWidget(self._field_label("LABEL"), row, 0)
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("napr. Alice ↔ Bob 2026")
        self.label_input.setMaxLength(120)
        form.addWidget(self.label_input, row, 1, 1, 2)
        row += 1

        # target drive
        form.addWidget(self._field_label("TARGET"), row, 0)
        self.drive_combo = QComboBox()
        self.drive_combo.setObjectName("MonoEdit")
        self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
        form.addWidget(self.drive_combo, row, 1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("IconButton")
        self.refresh_btn.setToolTip("Refresh drives")
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.clicked.connect(self._populate_drives)
        form.addWidget(self.refresh_btn, row, 2)
        row += 1

        # custom folder display
        self.custom_path_label = QLabel("")
        self.custom_path_label.setObjectName("MonoLabel")
        self.custom_path_label.setStyleSheet("color: #666; font-size: 11px;")
        self.custom_path_label.hide()
        form.addWidget(self.custom_path_label, row, 1, 1, 2)
        row += 1

        # max message length
        form.addWidget(self._field_label("MAX MSG LEN"), row, 0)
        self.maxlen_spin = QSpinBox()
        self.maxlen_spin.setRange(10, 30)
        self.maxlen_spin.setValue(30)
        self.maxlen_spin.setSuffix("  chars")
        self.maxlen_spin.valueChanged.connect(self._update_pad_size_label)
        form.addWidget(self.maxlen_spin, row, 1, 1, 2)
        row += 1

        # canvas size
        form.addWidget(self._field_label("CANVAS"), row, 0)
        self.canvas_combo = QComboBox()
        for v in (64, 128, 256):
            self.canvas_combo.addItem(f"{v} × {v}", v)
        self.canvas_combo.setCurrentIndex(1)   # default 128
        self.canvas_combo.currentIndexChanged.connect(self._update_pad_size_label)
        form.addWidget(self.canvas_combo, row, 1, 1, 2)
        row += 1

        # number of pads
        form.addWidget(self._field_label("OTP PADS"), row, 0)
        self.pads_spin = QSpinBox()
        self.pads_spin.setRange(1, 100)
        self.pads_spin.setValue(10)
        self.pads_spin.setSuffix("  pads")
        self.pads_spin.valueChanged.connect(self._update_pad_size_label)
        form.addWidget(self.pads_spin, row, 1, 1, 2)
        row += 1

        # isotope mode — stable vs ephemeral
        form.addWidget(self._field_label("REŽIM SPRÁV"), row, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Stabilný (správa vždy dešifrovateľná)", "stable")
        self.mode_combo.addItem("Sebazničujúci (správa expiruje)",       "ephemeral")
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setToolTip(
            "Stabilný režim používa izotopy s polčasom ≥ 1 rok — správa zostáva "
            "dešifrovateľná neobmedzene.\n"
            "Sebazničujúci režim používa krátko-žijúce izotopy — správa sa po "
            "niekoľkých polčasoch (sekundy až dni) stane nedešifrovateľnou."
        )
        form.addWidget(self.mode_combo, row, 1, 1, 2)
        row += 1

        # computed size
        self.size_label = QLabel("—")
        self.size_label.setObjectName("MonoLabel")
        self.size_label.setStyleSheet("color: #888; padding-top: 4px;")
        form.addWidget(self.size_label, row, 1, 1, 2)
        row += 1

        root.addLayout(form)

        # ── forge button ──────────────────────────────────────────────────
        self.forge_btn = QPushButton("⚡  FORGE KEY")
        self.forge_btn.setObjectName("PrimaryButton")
        self.forge_btn.setFixedHeight(54)
        self.forge_btn.clicked.connect(self._on_forge_clicked)
        root.addSpacing(4)
        root.addWidget(self.forge_btn)

        # ── status ────────────────────────────────────────────────────────
        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusInfo")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status_label)

        # ── divider ───────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #1a1a1a; background: #1a1a1a; max-height: 1px;")
        root.addWidget(line)

        # ── portrait ──────────────────────────────────────────────────────
        self.portrait_header = QLabel("LORENZ FINGERPRINT")
        self.portrait_header.setObjectName("SectionLabel")
        self.portrait_header.hide()
        root.addWidget(self.portrait_header)

        self.portrait_label = QLabel()
        self.portrait_label.setObjectName("PortraitLabel")
        self.portrait_label.setAlignment(Qt.AlignCenter)
        self.portrait_label.setMinimumHeight(280)
        self.portrait_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.portrait_label.hide()
        root.addWidget(self.portrait_label, stretch=1)

        root.addStretch(0)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #c1272d; font-weight: bold; "
                          "letter-spacing: 1.5px; font-size: 10px;")
        lbl.setFixedWidth(120)
        return lbl

    # ── drive enumeration ───────────────────────────────────────────────────

    def _populate_drives(self):
        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()

        drives = keystore.list_drives(removable_only=False)
        # Prioritise removable drives, then fixed.
        drives.sort(key=lambda d: (not d.removable, d.path))

        if not drives:
            self.drive_combo.addItem("(no drives detected)", None)
        else:
            for d in drives:
                prefix = "💾 " if d.removable else "🖴 "
                self.drive_combo.addItem(prefix + d.display(), d.path)

        self.drive_combo.insertSeparator(self.drive_combo.count())
        self.drive_combo.addItem("📁  Custom folder…", CUSTOM_FOLDER_SENTINEL)

        self.drive_combo.blockSignals(False)
        self._on_drive_changed(self.drive_combo.currentIndex())

    def _on_drive_changed(self, index: int):
        data = self.drive_combo.itemData(index)
        if data == CUSTOM_FOLDER_SENTINEL:
            folder = QFileDialog.getExistingDirectory(
                self, "Choose folder for fsc_keys/", self._custom_folder or os.path.expanduser("~"),
            )
            if folder:
                self._custom_folder = folder
                self.custom_path_label.setText(f"  → {folder}")
                self.custom_path_label.show()
            else:
                # user cancelled — fall back to first drive
                self.drive_combo.setCurrentIndex(0)
                self.custom_path_label.hide()
        else:
            self.custom_path_label.hide()

    # ── pad-size readout ─────────────────────────────────────────────────────

    def _pad_size_bytes(self) -> int:
        canvas = int(self.canvas_combo.currentData())
        return int(self.maxlen_spin.value()) * canvas * canvas

    def _update_pad_size_label(self):
        n_pads   = int(self.pads_spin.value())
        per_pad  = self._pad_size_bytes()
        total    = per_pad * n_pads
        self.size_label.setText(
            f"každý pad = {per_pad/1024:.1f} kB    "
            f"spolu = {total/1024/1024:.2f} MB ({n_pads} pads)"
        )

    # ── forge action ────────────────────────────────────────────────────────

    def _selected_drive_path(self) -> str | None:
        data = self.drive_combo.currentData()
        if data == CUSTOM_FOLDER_SENTINEL:
            return self._custom_folder or None
        return data

    def _set_status(self, text: str, kind: str = "info"):
        self.status_label.setText(text)
        obj = {"ok": "StatusOK", "error": "StatusError", "info": "StatusInfo"}.get(kind, "StatusInfo")
        self.status_label.setObjectName(obj)
        # force restyle
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _on_forge_clicked(self):
        label = self.label_input.text().strip()
        if not label:
            self._set_status("⚠  label cannot be empty", "error")
            return

        drive_path = self._selected_drive_path()
        if not drive_path:
            self._set_status("⚠  pick a target drive or folder", "error")
            return
        if not os.path.isdir(drive_path):
            self._set_status(f"⚠  path not accessible: {drive_path}", "error")
            return

        # ── generate master key + pads ────────────────────────────────────
        try:
            master_key  = secrets.token_bytes(32)
            keystore_dir = keystore.ensure_keystore(drive_path)
            pad_size    = self._pad_size_bytes()
            n_pads      = int(self.pads_spin.value())
            pad_specs   = [pad_size] * n_pads

            self.forge_btn.setEnabled(False)
            self._set_status(
                f"forging key + {n_pads} pads "
                f"({n_pads*pad_size/1024/1024:.1f} MB)…", "info",
            )
            self.repaint()   # flush UI before blocking write

            manifest = fileformat.save_keyfile(
                keystore_dir=keystore_dir,
                master_key=master_key,
                label=label,
                pad_specs=pad_specs,
                max_len=int(self.maxlen_spin.value()),
                canvas_size=int(self.canvas_combo.currentData()),
                isotope_mode=str(self.mode_combo.currentData()),
            )
        except Exception as exc:
            self.forge_btn.setEnabled(True)
            self._set_status(f"✗  forge failed: {type(exc).__name__}: {exc}", "error")
            return

        key_id   = manifest["key_id"]
        keyfile  = manifest["_keyfile_path"]
        mode     = manifest["isotope_mode"]
        mode_tag = "stable ∞" if mode == "stable" else "ephemeral ⏱"
        self._set_status(
            f"✓  KEY FORGED   id={key_id}   mode={mode_tag}\n"
            f"    {keyfile}\n"
            f"    rendering Lorenz fingerprint…",
            "ok",
        )

        # ── render portrait off the UI thread ─────────────────────────────
        portrait_path = os.path.join(
            tempfile.gettempdir(), f"fsc_portrait_{key_id}.png",
        )
        self._worker = PortraitWorker(master_key, portrait_path, parent=self)
        self._worker.finished_path.connect(self._on_portrait_ready)
        self._worker.failed.connect(self._on_portrait_failed)
        self._worker.start()

    # ── portrait callbacks ───────────────────────────────────────────────────

    def _on_portrait_ready(self, path: str):
        pix = QPixmap(path)
        if pix.isNull():
            self._on_portrait_failed(f"could not load image at {path}")
            return
        self.portrait_header.show()
        self.portrait_label.show()
        self._render_portrait_pixmap(pix)
        self.portrait_label.setProperty("_pixmap", pix)
        self.forge_btn.setEnabled(True)
        # append portrait info to status
        current = self.status_label.text()
        head    = current.split("\n", 1)[0]
        self._set_status(head + "\n    Lorenz fingerprint rendered ↓", "ok")

    def _on_portrait_failed(self, msg: str):
        self.forge_btn.setEnabled(True)
        current = self.status_label.text()
        head    = current.split("\n", 1)[0]
        self._set_status(head + f"\n    ⚠  portrait failed: {msg}", "error")

    def _render_portrait_pixmap(self, pix: QPixmap):
        target = self.portrait_label.size()
        if target.width() <= 1 or target.height() <= 1:
            return
        scaled = pix.scaled(
            target, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.portrait_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        pix = self.portrait_label.property("_pixmap")
        if isinstance(pix, QPixmap) and not pix.isNull():
            self._render_portrait_pixmap(pix)
