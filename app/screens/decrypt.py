"""
app/screens/decrypt.py — DECRYPT screen.

Loads a .fsc envelope (drag-drop, file picker, or clipboard paste), finds the
matching .fsckey + pad on the keystore drives, and recovers the rendered
glyph images.

By FSC design, "decryption" recovers IMAGES of the rendered characters, not
the original Unicode string. The human reads the message visually. The
ciphertext never carried plaintext — only what was needed to reverse the
seven physical-simulation layers.
"""

import datetime
import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PySide6.QtCore    import Qt, QThread, Signal
from PySide6.QtGui     import QPixmap, QImage, QGuiApplication, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFileDialog, QFrame, QSizePolicy,
    QScrollArea, QPlainTextEdit,
)

from app                import fileformat, keystore
from app.fileformat     import load_fsc, read_pad
from app.decrypt_engine import decrypt_fsc, find_key_on_disk, DecryptResult


# how big each recovered glyph thumbnail is drawn (px)
GLYPH_SIZE = 96


def _format_seconds(s: float) -> str:
    """Human-readable duration for the expiry status line."""
    if s != s or s in (float("inf"), -float("inf")):
        return "?"
    if s < 1e-3:        return f"{s*1e6:.0f} µs"
    if s < 1:           return f"{s*1e3:.0f} ms"
    if s < 60:          return f"{s:.1f} s"
    if s < 3_600:       return f"{s/60:.1f} min"
    if s < 86_400:      return f"{s/3_600:.1f} h"
    if s < 31_557_600:  return f"{s/86_400:.1f} d"
    return f"{s/31_557_600:.1f} yr"


# ── drag-and-drop zone ────────────────────────────────────────────────────────

class FscDropZone(QLabel):
    """A QLabel that emits the path of a .fsc file dropped onto it."""
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(110)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.setText("📥   pretiahni sem .fsc súbor")
        self.setStyleSheet(
            "QLabel#DropZone {"
            "   border: 2px dashed #c1272d; border-radius: 10px;"
            "   background-color: #0d0d0d; color: #888;"
            "   font-size: 13px; padding: 24px;"
            "}"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls() and any(
            u.toLocalFile().lower().endswith(".fsc")
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            self.setStyleSheet(
                "QLabel#DropZone {"
                "   border: 2px solid #c1272d; border-radius: 10px;"
                "   background-color: #1a0808; color: #e8e8e8;"
                "   font-size: 13px; padding: 24px;"
                "}"
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        # restore default style
        self.setStyleSheet(
            "QLabel#DropZone {"
            "   border: 2px dashed #c1272d; border-radius: 10px;"
            "   background-color: #0d0d0d; color: #888;"
            "   font-size: 13px; padding: 24px;"
            "}"
        )

    def dropEvent(self, event: QDropEvent):
        self.dragLeaveEvent(event)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".fsc") and os.path.isfile(path):
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


# ── decrypt worker (off-thread) ───────────────────────────────────────────────

class DecryptWorker(QThread):
    """Runs decrypt_fsc off the UI thread (Lorenz integration is the slow part)."""
    finished_ok = Signal(object)        # DecryptResult (may have status='expired')
    failed      = Signal(str)

    def __init__(self, fsc_data: dict, master_key: bytes, pad_bytes: bytes,
                 isotope_mode: str = "stable", parent=None):
        super().__init__(parent)
        self.fsc_data     = fsc_data
        self.master_key   = master_key
        self.pad_bytes    = pad_bytes
        self.isotope_mode = isotope_mode

    def run(self):
        try:
            res = decrypt_fsc(self.fsc_data, self.master_key, self.pad_bytes,
                              isotope_mode=self.isotope_mode)
            self.finished_ok.emit(res)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ── glyph rendering ──────────────────────────────────────────────────────────

def _glyph_to_qimage(arr: np.ndarray, size: int = GLYPH_SIZE) -> QImage:
    """
    Convert a recovered float32 (H, W) image to an 8-bit grayscale QImage.
    Per-image autoscaling makes the recovered glyph visible even when the
    material+isotope layers crushed the dynamic range.
    """
    a = np.asarray(arr, dtype=np.float32)
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-9:
        a8 = np.zeros_like(a, dtype=np.uint8)
    else:
        a8 = ((a - lo) / (hi - lo) * 255.0).clip(0, 255).astype(np.uint8)

    # ensure contiguous, big enough buffer
    a8 = np.ascontiguousarray(a8)
    h, w = a8.shape
    img = QImage(a8.data, w, h, w, QImage.Format_Grayscale8).copy()  # copy so np array can free
    if w != size or h != size:
        img = img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return img


# ── DECRYPT screen ───────────────────────────────────────────────────────────

class DecryptScreen(QWidget):
    """Drop a .fsc, find key+pad on disk, recover glyph images."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fsc_path: Optional[str]   = None
        self._fsc_data: Optional[dict]  = None
        self._key_manifest: Optional[dict] = None
        self._key_dir:  Optional[str]   = None
        self._pad_bytes: Optional[bytes] = None
        self._worker: Optional[DecryptWorker] = None
        self._build_ui()
        self._reset_state()

    # ── UI ────────────────────────────────────────────────────────────────

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

        title = QLabel("DECRYPT")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        subtitle = QLabel("dešifruj správu")
        subtitle.setObjectName("SubtitleLabel")
        root.addWidget(subtitle)

        # ── drop / open / paste row ───────────────────────────────────────
        self.drop_zone = FscDropZone()
        self.drop_zone.file_dropped.connect(self._on_fsc_loaded_from_disk)
        root.addWidget(self.drop_zone)

        actions = QHBoxLayout()
        actions.setSpacing(10)

        self.open_btn = QPushButton("📂   Open .fsc")
        self.open_btn.clicked.connect(self._on_open_clicked)
        actions.addWidget(self.open_btn)

        self.paste_btn = QPushButton("📋   Paste base64")
        self.paste_btn.setToolTip("Paste a .fsc JSON envelope from the clipboard")
        self.paste_btn.clicked.connect(self._on_paste_clicked)
        actions.addWidget(self.paste_btn)

        actions.addStretch(1)
        root.addLayout(actions)

        # ── envelope info ─────────────────────────────────────────────────
        info_frame = QFrame()
        info_layout = QGridLayout(info_frame)
        info_layout.setHorizontalSpacing(14)
        info_layout.setVerticalSpacing(6)
        info_layout.setContentsMargins(0, 8, 0, 0)
        info_layout.setColumnStretch(1, 1)

        self._info_rows: dict[str, QLabel] = {}
        for r, (k, lbl) in enumerate((
            ("key_id",   "KEY ID"),
            ("pad_id",   "PAD"),
            ("when",     "ENCRYPTED"),
            ("chars",    "CHARS"),
            ("canvas",   "CANVAS"),
            ("keyfound", "KEY ON DISK"),
            ("padfound", "PAD ON DISK"),
        )):
            hdr = QLabel(lbl)
            hdr.setStyleSheet("color: #c1272d; font-weight: bold; "
                              "letter-spacing: 1.2px; font-size: 9px;")
            hdr.setFixedWidth(120)
            info_layout.addWidget(hdr, r, 0)
            val = QLabel("—")
            val.setObjectName("MonoLabel")
            val.setStyleSheet("color: #ccc; font-family: 'JetBrains Mono','Consolas',monospace;")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info_layout.addWidget(val, r, 1)
            self._info_rows[k] = val

        self.info_frame = info_frame
        self.info_frame.hide()
        root.addWidget(info_frame)

        # ── DECRYPT button ────────────────────────────────────────────────
        self.decrypt_btn = QPushButton("🔓   DECRYPT")
        self.decrypt_btn.setObjectName("PrimaryButton")
        self.decrypt_btn.setFixedHeight(54)
        self.decrypt_btn.setEnabled(False)
        self.decrypt_btn.clicked.connect(self._on_decrypt_clicked)
        root.addSpacing(4)
        root.addWidget(self.decrypt_btn)

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

        # ── recovered glyph row ───────────────────────────────────────────
        self.result_header = QLabel("RECOVERED GLYPHS")
        self.result_header.setObjectName("SectionLabel")
        self.result_header.hide()
        root.addWidget(self.result_header)

        # scroll horizontally if many chars
        self.glyph_scroll = QScrollArea()
        self.glyph_scroll.setFrameShape(QFrame.NoFrame)
        self.glyph_scroll.setWidgetResizable(True)
        self.glyph_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.glyph_scroll.setFixedHeight(GLYPH_SIZE + 30)
        self.glyph_scroll.hide()

        self.glyph_container = QWidget()
        self.glyph_layout = QHBoxLayout(self.glyph_container)
        self.glyph_layout.setSpacing(6)
        self.glyph_layout.setContentsMargins(2, 2, 2, 2)
        self.glyph_layout.addStretch(1)
        self.glyph_scroll.setWidget(self.glyph_container)
        root.addWidget(self.glyph_scroll)

        root.addStretch(0)

    # ── state helpers ─────────────────────────────────────────────────────

    def _reset_state(self):
        self._fsc_path = None
        self._fsc_data = None
        self._key_manifest = None
        self._key_dir = None
        self._pad_bytes = None
        self.info_frame.hide()
        self.result_header.hide()
        self.glyph_scroll.hide()
        self._clear_glyphs()
        self.decrypt_btn.setEnabled(False)

    def _clear_glyphs(self):
        # remove all but the trailing stretch
        while self.glyph_layout.count() > 1:
            item = self.glyph_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _set_status(self, text: str, kind: str = "info"):
        self.status_label.setText(text)
        obj = {"ok": "StatusOK", "error": "StatusError", "info": "StatusInfo"}.get(kind, "StatusInfo")
        self.status_label.setObjectName(obj)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    # ── load .fsc from various sources ────────────────────────────────────

    def _on_open_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open .fsc envelope",
            os.path.expanduser("~"),
            "FSC ciphertext (*.fsc);;All files (*.*)",
        )
        if path:
            self._on_fsc_loaded_from_disk(path)

    def _on_paste_clicked(self):
        txt = QGuiApplication.clipboard().text() or ""
        if not txt.strip():
            self._set_status("⚠  clipboard is empty", "error")
            return
        try:
            data = json.loads(txt)
        except Exception as exc:
            self._set_status(f"⚠  clipboard is not a valid .fsc JSON: {exc}", "error")
            return
        self._load_fsc_data(data, source="<clipboard>")

    def _on_fsc_loaded_from_disk(self, path: str):
        try:
            data = load_fsc(path)
        except Exception as exc:
            self._set_status(f"✗  could not read {os.path.basename(path)}: {exc}", "error")
            return
        self._fsc_path = path
        self._load_fsc_data(data, source=path)

    def _load_fsc_data(self, data: dict, source: str):
        """Validate envelope, look up key+pad on disk, populate info panel."""
        self._fsc_data = data
        self._key_manifest = None
        self._key_dir = None
        self._pad_bytes = None

        when = data.get("t_encrypt", 0)
        when_str = (
            datetime.datetime.fromtimestamp(float(when))
            .strftime("%Y-%m-%d %H:%M:%S") if when else "—"
        )

        self._info_rows["key_id"].setText(str(data.get("key_id", "—")))
        self._info_rows["pad_id"].setText(str(data.get("pad_id", "—")))
        self._info_rows["when"].setText(when_str)
        self._info_rows["chars"].setText(str(data.get("n_chars", "—")))
        cs = data.get("canvas_size", "—")
        self._info_rows["canvas"].setText(f"{cs} × {cs}" if cs != "—" else "—")
        self.info_frame.show()

        # ── scan drives for the matching key ──────────────────────────────
        manifests = []
        key_dir_for_match = None
        for drive in keystore.list_scannable_drives():
            for m in keystore.list_keys(drive.path):
                manifests.append(m)
                if m.get("key_id") == data.get("key_id"):
                    key_dir_for_match = keystore.keystore_path(drive.path)

        found = find_key_on_disk(data, manifests)
        if found is None:
            self._info_rows["keyfound"].setText("✗  not found — insert correct USB")
            self._info_rows["keyfound"].setStyleSheet("color: #ef4444;")
            self._info_rows["padfound"].setText("—")
            self._info_rows["padfound"].setStyleSheet("color: #888;")
            self.decrypt_btn.setEnabled(False)
            self._set_status(
                f"⚠  key {data.get('key_id')} not found on any drive. "
                f"Insert the USB that contains it.", "error",
            )
            return

        self._key_manifest = found
        self._key_dir      = key_dir_for_match
        kf = os.path.basename(found.get("_keyfile_path", "?"))
        self._info_rows["keyfound"].setText(f"✓  {kf}")
        self._info_rows["keyfound"].setStyleSheet("color: #4ade80;")

        # ── try to read the pad ───────────────────────────────────────────
        pad_id = data.get("pad_id")
        try:
            self._pad_bytes = read_pad(self._key_dir, pad_id, keydata=found)
        except Exception as exc:
            self._info_rows["padfound"].setText(f"✗  {exc}")
            self._info_rows["padfound"].setStyleSheet("color: #ef4444;")
            self.decrypt_btn.setEnabled(False)
            self._set_status(
                f"⚠  pad {pad_id} could not be read: {exc}", "error",
            )
            return

        is_used = found.get("otp_pads", {}).get(pad_id, {}).get("used", False)
        flag = "  (already used — OK for decrypt)" if is_used else ""
        self._info_rows["padfound"].setText(f"✓  {pad_id}  ({len(self._pad_bytes):,} B){flag}")
        self._info_rows["padfound"].setStyleSheet("color: #4ade80;")

        self.decrypt_btn.setEnabled(True)
        if source == "<clipboard>":
            self._set_status("✓  envelope loaded from clipboard — ready to decrypt", "ok")
        else:
            self._set_status(f"✓  loaded {os.path.basename(source)} — ready to decrypt", "ok")

    # ── DECRYPT click ─────────────────────────────────────────────────────

    def _on_decrypt_clicked(self):
        if (self._fsc_data is None or self._key_manifest is None
                or self._pad_bytes is None):
            self._set_status("✗  load a .fsc first", "error")
            return

        try:
            mk = bytes.fromhex(self._key_manifest["master_key"])
        except Exception as exc:
            self._set_status(f"✗  key file invalid: {exc}", "error")
            return

        self.decrypt_btn.setEnabled(False)
        self._clear_glyphs()
        self.result_header.hide()
        self.glyph_scroll.hide()
        self._set_status("decrypting … reversing all 7 layers", "info")
        self.repaint()

        self._t_start = time.perf_counter()
        mode = self._key_manifest.get("isotope_mode", "stable")
        self._worker = DecryptWorker(
            self._fsc_data, mk, self._pad_bytes,
            isotope_mode=mode, parent=self,
        )
        self._worker.finished_ok.connect(self._on_decrypt_ok)
        self._worker.failed.connect(self._on_decrypt_failed)
        self._worker.start()

    def _on_decrypt_ok(self, result: DecryptResult):
        elapsed = (time.perf_counter() - self._t_start) * 1000
        self.decrypt_btn.setEnabled(True)
        self._clear_glyphs()

        # ── ephemeral-message expiry: render the friendly "decayed" panel ──
        if result.status == "expired":
            info = result.expired_info or {}
            iso  = info.get("isotope", "?")
            hl   = info.get("half_life", float("nan"))
            dt   = info.get("delta_t", float("nan"))
            nhl  = info.get("n_halflives", float("nan"))

            # show a single big "expired" tile in the glyph row
            tomb = QLabel("⏱  ☠")
            tomb.setAlignment(Qt.AlignCenter)
            tomb.setFixedHeight(GLYPH_SIZE)
            tomb.setMinimumWidth(GLYPH_SIZE * 3)
            tomb.setStyleSheet(
                "color: #c1272d; font-size: 40px; "
                "background: #100404; border: 1px dashed #c1272d;"
            )
            self.glyph_layout.insertWidget(self.glyph_layout.count() - 1, tomb)

            self.result_header.setText("MESSAGE EXPIRED")
            self.result_header.show()
            self.glyph_scroll.show()
            self._set_status(
                "⏱  Táto správa expirovala — izotopy sa rozpadli, "
                "správa je nenávratne stratená.\n"
                f"    {iso}   polčas {_format_seconds(hl)}   "
                f"prešlo {_format_seconds(dt)} ({nhl:.1f}× polčas)\n"
                "    (toto je správanie sebazničujúcich kľúčov — nie chyba)",
                "info",
            )
            return

        # ── normal success — render recovered glyphs ──────────────────────
        for i, img in enumerate(result.geometry):
            qimg = _glyph_to_qimage(img, size=GLYPH_SIZE)
            lbl = QLabel()
            lbl.setPixmap(QPixmap.fromImage(qimg))
            lbl.setFixedSize(GLYPH_SIZE, GLYPH_SIZE)
            lbl.setStyleSheet("border: 1px solid #2a2a2a; background: #050505;")
            lbl.setToolTip(f"glyph #{i+1} of {result.n_chars}")
            self.glyph_layout.insertWidget(self.glyph_layout.count() - 1, lbl)

        self.result_header.setText("RECOVERED GLYPHS")
        self.result_header.show()
        self.glyph_scroll.show()
        drift = max(0.0, result.t_decrypt - result.t_encrypt)
        mode_tag = "∞ stable" if result.isotope_mode == "stable" else "⏱ ephemeral"
        self._set_status(
            f"✓  decrypted   {result.n_chars} glyphs in {elapsed:.0f} ms   "
            f"key={result.key_id}   pad={result.pad_id}   "
            f"mode={mode_tag}   drift={drift:.2f}s",
            "ok",
        )

    def _on_decrypt_failed(self, msg: str):
        self.decrypt_btn.setEnabled(True)
        if "HMAC" in msg:
            hint = "\n    → ciphertext was tampered with, OR the key on this USB does not match this .fsc"
        else:
            hint = ""
        self._set_status(f"✗  decrypt failed: {msg}{hint}", "error")

    # ── nav refresh hook ─────────────────────────────────────────────────

    def refresh_keys(self):
        """Called by main.py when the screen becomes visible. Re-runs the
        key+pad lookup against the current drives so newly inserted USBs are
        detected without reloading the .fsc.
        """
        if self._fsc_data is not None:
            self._load_fsc_data(self._fsc_data, source=self._fsc_path or "<reloaded>")
