"""
app/screens/encrypt.py — ENCRYPT screen.

Selects an FSC key from the keystore, encrypts a short message, and writes a
.fsc envelope. The OTP pad consumed for the message is read from disk and
marked used ONLY on successful save (per spec).

Crypto invariants enforced here:
  - master_key from disk (32 bytes, hex) is the ROOT.
  - The OTP pad bytes used at the OTP layer MUST be the disk pad of the
    selected pad_id — never the random pad that generate() produces.
  - The Lorenz nonce (16 random bytes from BlackholeParams) is stored in the
    .fsc envelope so Bob can rebuild the keystream during decrypt.
"""

import base64
import os
import tempfile
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore    import Qt, QThread, Signal, QSize
from PySide6.QtGui     import QPixmap, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QFileDialog, QFrame, QSizePolicy, QScrollArea, QPlainTextEdit,
)

from app import fileformat, keystore
from app.fileformat import (
    FSC_VERSION, FSC_FILE_EXT,
    encode_cipher, save_fsc, mark_pad_used, read_pad, get_unused_pad,
)

# how many characters to show in the inline visualisation (full ciphertext
# is always saved — this is a display ceiling only)
VIZ_MAX_COLS = 6


# ── key-list entry ────────────────────────────────────────────────────────────

@dataclass
class KeyEntry:
    drive_path:   str       # e.g. "C:\\"
    keystore_dir: str       # e.g. "C:\\fsc_keys"
    keyfile_path: str       # absolute path to <key_id>.fsckey
    manifest:     dict      # parsed .fsckey JSON

    @property
    def key_id(self) -> str:
        return self.manifest["key_id"]

    @property
    def label(self) -> str:
        return self.manifest.get("label", "(no label)")

    @property
    def max_len(self) -> int:
        return int(self.manifest.get("max_len", 30))

    @property
    def canvas_size(self) -> int:
        return int(self.manifest.get("canvas_size", 128))

    @property
    def pads_total(self) -> int:
        return len(self.manifest.get("otp_pads", {}))

    @property
    def pads_unused(self) -> int:
        return sum(1 for p in self.manifest.get("otp_pads", {}).values()
                   if not p.get("used", False))

    @property
    def isotope_mode(self) -> str:
        return self.manifest.get("isotope_mode", "stable")

    def next_unused_pad(self):
        return get_unused_pad(self.manifest)

    def display(self) -> str:
        mode_tag = "∞ stable" if self.isotope_mode == "stable" else "⏱ ephemeral"
        return (f"{self.label}   ·   {self.key_id}   "
                f"·   {self.pads_unused}/{self.pads_total} pads   ·   {mode_tag}")


# ── encrypt worker (off-thread) ───────────────────────────────────────────────

class EncryptWorker(QThread):
    """Runs core.pipeline.encrypt + viz off the UI thread."""
    finished_ok = Signal(object, str)   # (enc_state dict, viz png path)
    failed      = Signal(str)

    def __init__(self, text: str, master_key: bytes, canvas_size: int,
                 disk_pad: bytes, viz_path: str,
                 isotope_mode: str = "stable", parent=None):
        super().__init__(parent)
        self.text         = text
        self.master_key   = master_key
        self.canvas_size  = canvas_size
        self.disk_pad     = disk_pad
        self.viz_path     = viz_path
        self.isotope_mode = isotope_mode

    def run(self):
        try:
            # imported here so module import is cheap when worker isn't used
            from keys.keygen     import generate
            from core.pipeline   import encrypt as pipeline_encrypt
            from viz.visualizer  import visualize

            # 1. build FSCKey from disk master_key
            key = generate(
                self.text,
                master_key=self.master_key,
                canvas_size=self.canvas_size,
                isotope_mode=self.isotope_mode,
            )
            # 2. CRITICAL: override key.otp_pad with the disk pad. The
            #    generate() call above produced a random pad we DO NOT use.
            #    core/otp.py slices pad[:flat.size], so disk pad ≥ needed.
            key.otp_pad = self.disk_pad

            # 3. encrypt
            enc = pipeline_encrypt(self.text, key)

            # 4. render inline visualisation (truncated for long messages)
            visualize(enc, save_path=self.viz_path, max_cols=VIZ_MAX_COLS)

            self.finished_ok.emit(enc, self.viz_path)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ── ENCRYPT screen ────────────────────────────────────────────────────────────

class EncryptScreen(QWidget):
    """Select key, type message, encrypt, save .fsc."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keys:    list[KeyEntry]    = []
        self._enc:     dict | None       = None    # last successful enc_state
        self._key:     KeyEntry | None   = None    # selected key
        self._pad_id:  str | None        = None    # auto-picked pad
        self._worker:  EncryptWorker | None = None
        self._build_ui()
        self.refresh_keys()

    # ── UI construction ───────────────────────────────────────────────────────

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

        # ── title ─────────────────────────────────────────────────────────
        title = QLabel("ENCRYPT")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        subtitle = QLabel("zašifruj správu")
        subtitle.setObjectName("SubtitleLabel")
        root.addWidget(subtitle)

        # ── key selector row ──────────────────────────────────────────────
        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

        form.addWidget(self._field_label("KEY"), 0, 0)
        self.key_combo = QComboBox()
        self.key_combo.setObjectName("MonoEdit")
        self.key_combo.currentIndexChanged.connect(self._on_key_changed)
        form.addWidget(self.key_combo, 0, 1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("IconButton")
        self.refresh_btn.setToolTip("Rescan drives for keys")
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.clicked.connect(self.refresh_keys)
        form.addWidget(self.refresh_btn, 0, 2)

        # pad / capacity info line
        self.pad_info = QLabel("—")
        self.pad_info.setObjectName("MonoLabel")
        self.pad_info.setStyleSheet("color: #888; padding-top: 4px;")
        form.addWidget(self.pad_info, 1, 1, 1, 2)

        # message
        form.addWidget(self._field_label("MESSAGE"), 2, 0)
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("napíš správu (1–30 znakov)")
        self.msg_input.setMaxLength(30)
        self.msg_input.textChanged.connect(self._on_msg_changed)
        form.addWidget(self.msg_input, 2, 1)

        self.counter_label = QLabel("0 / 30")
        self.counter_label.setObjectName("MonoLabel")
        self.counter_label.setStyleSheet("color: #888; padding: 0 6px;")
        self.counter_label.setMinimumWidth(64)
        self.counter_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addWidget(self.counter_label, 2, 2)

        root.addLayout(form)

        # ── encrypt button ────────────────────────────────────────────────
        self.encrypt_btn = QPushButton("⚡  ENCRYPT")
        self.encrypt_btn.setObjectName("PrimaryButton")
        self.encrypt_btn.setFixedHeight(54)
        self.encrypt_btn.setEnabled(False)
        self.encrypt_btn.clicked.connect(self._on_encrypt_clicked)
        root.addSpacing(4)
        root.addWidget(self.encrypt_btn)

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

        # ── result section (hidden until encrypt completes) ───────────────
        self.viz_header = QLabel("LAYER VISUALISATION")
        self.viz_header.setObjectName("SectionLabel")
        self.viz_header.hide()
        root.addWidget(self.viz_header)

        self.viz_label = QLabel()
        self.viz_label.setObjectName("PortraitLabel")
        self.viz_label.setAlignment(Qt.AlignCenter)
        self.viz_label.setMinimumHeight(360)
        self.viz_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.viz_label.hide()
        root.addWidget(self.viz_label, stretch=1)

        # metrics row
        self.metrics_row = QHBoxLayout()
        self.metrics_row.setSpacing(28)
        self._metric_widgets = {}
        for k, lbl in (
            ("entropy",     "BYTE ENTROPY"),
            ("size",        "CIPHERTEXT"),
            ("pad",         "OTP PAD"),
            ("time",        "ENCRYPT TIME"),
        ):
            box = QVBoxLayout()
            hdr = QLabel(lbl)
            hdr.setStyleSheet("color: #c1272d; font-weight: bold; "
                              "letter-spacing: 1.2px; font-size: 9px;")
            val = QLabel("—")
            val.setStyleSheet("color: #e8e8e8; font-size: 15px; "
                              "font-family: 'JetBrains Mono','Consolas',monospace;")
            box.addWidget(hdr)
            box.addWidget(val)
            holder = QWidget()
            holder.setLayout(box)
            self.metrics_row.addWidget(holder)
            self._metric_widgets[k] = val
        self.metrics_widget = QWidget()
        self.metrics_widget.setLayout(self.metrics_row)
        self.metrics_widget.hide()
        root.addWidget(self.metrics_widget)

        # save / copy buttons
        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self.save_btn = QPushButton("💾  SAVE .fsc")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_clicked)
        action_row.addWidget(self.save_btn)

        self.copy_btn = QPushButton("📋  COPY base64")
        self.copy_btn.setEnabled(False)
        self.copy_btn.setToolTip("Copy ciphertext to clipboard "
                                 "(does NOT consume the OTP pad)")
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        action_row.addWidget(self.copy_btn)

        action_row.addStretch(1)

        self.action_widget = QWidget()
        self.action_widget.setLayout(action_row)
        self.action_widget.hide()
        root.addWidget(self.action_widget)

        root.addStretch(0)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #c1272d; font-weight: bold; "
                          "letter-spacing: 1.5px; font-size: 10px;")
        lbl.setFixedWidth(120)
        return lbl

    # ── key list ──────────────────────────────────────────────────────────

    def refresh_keys(self):
        """Rescan all SCAN-safe drives (skips dead network shares), repopulate combo."""
        keys: list[KeyEntry] = []
        for drive in keystore.list_scannable_drives():
            ks_dir = keystore.keystore_path(drive.path)
            for manifest in keystore.list_keys(drive.path):
                keys.append(KeyEntry(
                    drive_path=drive.path,
                    keystore_dir=ks_dir,
                    keyfile_path=manifest["_keyfile_path"],
                    manifest=manifest,
                ))

        # remember the currently selected key_id (so refresh keeps focus)
        prior_id = self._key.key_id if self._key else None

        self._keys = keys
        self.key_combo.blockSignals(True)
        self.key_combo.clear()
        if not keys:
            self.key_combo.addItem("(no keys found — forge one first)", None)
            self.key_combo.setEnabled(False)
        else:
            self.key_combo.setEnabled(True)
            for entry in keys:
                self.key_combo.addItem(entry.display(), entry)
        self.key_combo.blockSignals(False)

        # restore previous selection if still present
        if prior_id:
            for i, e in enumerate(keys):
                if e.key_id == prior_id:
                    self.key_combo.setCurrentIndex(i)
                    break

        self._on_key_changed(self.key_combo.currentIndex())

    def _on_key_changed(self, idx: int):
        data = self.key_combo.itemData(idx)
        if isinstance(data, KeyEntry):
            self._key   = data
            self._pad_id = data.next_unused_pad()
            self.msg_input.setMaxLength(data.max_len)
            self._update_pad_info()
        else:
            self._key, self._pad_id = None, None
            self.pad_info.setText("—")
        # entering a different key invalidates the last encrypt preview
        self._reset_result()
        self._update_msg_counter()
        self._update_encrypt_enabled()

    def _update_pad_info(self):
        if not self._key:
            self.pad_info.setText("—")
            return
        k = self._key
        if self._pad_id is None:
            self.pad_info.setText(
                f"⚠  všetky pady spotrebované   ({k.pads_total}/{k.pads_total} used)"
            )
            self.pad_info.setStyleSheet("color: #ef4444; padding-top: 4px;")
        else:
            cap = k.canvas_size
            pad_kb = k.max_len * cap * cap / 1024
            self.pad_info.setText(
                f"⮕  next pad = {self._pad_id}   "
                f"({pad_kb:.0f} kB)   ·   canvas {cap}×{cap}   "
                f"·   max {k.max_len} znakov"
            )
            self.pad_info.setStyleSheet("color: #888; padding-top: 4px;")

    # ── message input ─────────────────────────────────────────────────────

    def _on_msg_changed(self, _text: str):
        self._update_msg_counter()
        self._update_encrypt_enabled()
        # editing message invalidates last preview
        if self._enc is not None:
            self._reset_result()

    def _update_msg_counter(self):
        n   = len(self.msg_input.text())
        cap = self._key.max_len if self._key else 30
        self.counter_label.setText(f"{n} / {cap}")
        if n == 0:
            self.counter_label.setStyleSheet("color: #888; padding: 0 6px;")
        elif n >= cap:
            self.counter_label.setStyleSheet("color: #ef4444; "
                                             "font-weight: bold; padding: 0 6px;")
        else:
            self.counter_label.setStyleSheet("color: #4ade80; padding: 0 6px;")

    def _update_encrypt_enabled(self):
        ok = (
            self._key is not None
            and self._pad_id is not None
            and len(self.msg_input.text().strip()) > 0
        )
        self.encrypt_btn.setEnabled(ok)

    # ── result clear ──────────────────────────────────────────────────────

    def _reset_result(self):
        self._enc = None
        self.viz_label.clear()
        self.viz_label.hide()
        self.viz_header.hide()
        self.metrics_widget.hide()
        self.action_widget.hide()
        self.save_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)

    def _set_status(self, text: str, kind: str = "info"):
        self.status_label.setText(text)
        obj = {"ok": "StatusOK", "error": "StatusError", "info": "StatusInfo"}.get(kind, "StatusInfo")
        self.status_label.setObjectName(obj)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    # ── ENCRYPT click ─────────────────────────────────────────────────────

    def _on_encrypt_clicked(self):
        if self._key is None or self._pad_id is None:
            self._set_status("⚠  no key / pad available", "error")
            return
        text = self.msg_input.text().strip()
        if not text:
            self._set_status("⚠  message is empty", "error")
            return
        if len(text) > self._key.max_len:
            self._set_status(
                f"⚠  message ({len(text)}) exceeds key max_len ({self._key.max_len})",
                "error",
            )
            return

        # ── read inputs from disk ────────────────────────────────────────
        try:
            master_key = bytes.fromhex(self._key.manifest["master_key"])
            disk_pad   = read_pad(self._key.keystore_dir,
                                   self._pad_id, keydata=self._key.manifest)
        except Exception as exc:
            self._set_status(f"✗  cannot read key/pad: {exc}", "error")
            return

        # ── launch worker ────────────────────────────────────────────────
        self.encrypt_btn.setEnabled(False)
        self._set_status(
            f"encrypting {len(text)} chars through 7 layers…", "info",
        )
        self.repaint()

        viz_path = os.path.join(
            tempfile.gettempdir(),
            f"fsc_encrypt_{self._key.key_id}_{int(time.time())}.png",
        )
        self._t_start = time.perf_counter()
        self._worker = EncryptWorker(
            text=text,
            master_key=master_key,
            canvas_size=self._key.canvas_size,
            disk_pad=disk_pad,
            viz_path=viz_path,
            isotope_mode=self._key.manifest.get("isotope_mode", "stable"),
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_encrypt_ok)
        self._worker.failed.connect(self._on_encrypt_failed)
        self._worker.start()

    # ── encrypt callbacks ─────────────────────────────────────────────────

    def _on_encrypt_ok(self, enc: dict, viz_path: str):
        elapsed_ms = (time.perf_counter() - self._t_start) * 1000.0
        self._enc = enc

        # show viz
        pix = QPixmap(viz_path)
        if not pix.isNull():
            self.viz_header.show()
            self.viz_label.show()
            self._render_viz_pixmap(pix)
            self.viz_label.setProperty("_pixmap", pix)

        # metrics
        cipher_arr = enc["otp_out"]
        auth_bytes = enc["auth_cipher"]
        entropy    = _byte_entropy(cipher_arr)

        self._metric_widgets["entropy"].setText(f"{entropy:.3f} / 8.000")
        self._metric_widgets["size"].setText(
            f"{len(auth_bytes):,} B  (auth)"
        )
        self._metric_widgets["pad"].setText(self._pad_id)
        self._metric_widgets["time"].setText(f"{elapsed_ms:.0f} ms")
        self.metrics_widget.show()

        # actions
        self.action_widget.show()
        self.save_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)

        self._set_status(
            f"✓  encrypted   {self._key.key_id} · {self._pad_id} · "
            f"{len(enc['text'])} chars · auth_cipher = {len(auth_bytes):,} B",
            "ok",
        )
        self.encrypt_btn.setEnabled(True)

    def _on_encrypt_failed(self, msg: str):
        self.encrypt_btn.setEnabled(True)
        self._set_status(f"✗  encrypt failed: {msg}", "error")

    # ── SAVE / COPY ───────────────────────────────────────────────────────

    def _build_fsc_dict(self) -> dict:
        """Compose the .fsc envelope from the cached enc_state."""
        enc         = self._enc
        nonce       = enc["bh_out"]["nonce"]      # 16 random bytes
        auth_cipher = enc["auth_cipher"]
        qp          = enc["quant_params"]
        return {
            "version":        FSC_VERSION,
            "key_id":         self._key.key_id,
            "pad_id":         self._pad_id,
            "t_encrypt":      float(enc["key"].t_encrypt),
            "canvas_size":    int(self._key.canvas_size),
            "n_chars":        int(len(enc["text"])),
            "shape":          list(enc["otp_out"].shape),
            "nonce":          encode_cipher(nonce),
            "cipher":         encode_cipher(auth_cipher),
            # quantizer params: vmin/vmax depend on the encrypted float data
            # and CANNOT be recomputed from master_key alone.
            "quant_n_levels": int(qp.n_levels),
            "quant_vmin":     float(qp.vmin),
            "quant_vmax":     float(qp.vmax),
        }

    def _on_save_clicked(self):
        if self._enc is None or self._key is None or self._pad_id is None:
            self._set_status("✗  nothing to save", "error")
            return

        suggested = os.path.join(
            os.path.expanduser("~"),
            f"{self._key.key_id}_{self._pad_id}{FSC_FILE_EXT}",
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save encrypted message", suggested,
            f"FSC ciphertext (*{FSC_FILE_EXT});;All files (*.*)",
        )
        if not path:
            return  # user cancelled

        if not path.endswith(FSC_FILE_EXT):
            path += FSC_FILE_EXT

        try:
            save_fsc(path, self._build_fsc_dict())
        except Exception as exc:
            self._set_status(f"✗  could not save: {exc}", "error")
            return

        # consume the pad on disk
        try:
            mark_pad_used(self._key.keyfile_path, self._pad_id)
        except Exception as exc:
            self._set_status(
                f"⚠  .fsc saved but failed to mark pad used: {exc}", "error",
            )
            return

        self._set_status(
            f"✓  saved → {path}\n"
            f"    pad {self._pad_id} marked used on {self._key.keyfile_path}",
            "ok",
        )

        # refresh manifests + UI: pads_unused decremented, next pad picked
        self.refresh_keys()
        self.msg_input.clear()
        self._reset_result()

    def _on_copy_clicked(self):
        if self._enc is None:
            self._set_status("✗  nothing to copy", "error")
            return
        b64 = encode_cipher(self._enc["auth_cipher"])
        QGuiApplication.clipboard().setText(b64)
        self._set_status(
            f"📋  copied {len(b64):,} base64 chars to clipboard   "
            f"⚠  pad NOT consumed — use SAVE for production",
            "info",
        )

    # ── pixmap rescaling ──────────────────────────────────────────────────

    def _render_viz_pixmap(self, pix: QPixmap):
        target = self.viz_label.size()
        if target.width() <= 1 or target.height() <= 1:
            return
        scaled = pix.scaled(
            target, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.viz_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        pix = self.viz_label.property("_pixmap")
        if isinstance(pix, QPixmap) and not pix.isNull():
            self._render_viz_pixmap(pix)


# ── helpers ───────────────────────────────────────────────────────────────────

def _byte_entropy(cipher: np.ndarray) -> float:
    """Shannon byte entropy of a uint8 array."""
    counts = np.bincount(cipher.ravel(), minlength=256)
    nz     = counts[counts > 0]
    probs  = nz / nz.sum()
    return float(-(probs * np.log2(probs)).sum())
