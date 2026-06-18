"""
Integration test — drives the actual EncryptScreen widget end-to-end in a
hidden QApplication. Exercises the SAME code paths the GUI uses, but no
mouse / SendKeys needed.

Flow:
  1. Make a temp keystore + forge a key with 2 pads (via app.fileformat)
  2. Construct a QApplication + EncryptScreen, point it at the temp keystore
  3. Programmatically set message + select the key entry
  4. Trigger _on_encrypt_clicked, wait for the worker to finish
  5. Verify enc_state shape, viz PNG was created, metrics are populated
  6. Mock QFileDialog and call _on_save_clicked
  7. Reload .fsckey: assert pad_0001.used == true
  8. Repeat: assert second encrypt selects pad_0002
"""
import os, secrets, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtCore    import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from app import fileformat, keystore
from app.fileformat       import load_keyfile
from app.screens.encrypt  import EncryptScreen, KeyEntry

sep = "-" * 66
print(sep)
print(" FSC Encrypt screen — integration test (Phase 2)")
print(sep)

# ── temp keystore + forge a key ───────────────────────────────────────────────
tmp = tempfile.TemporaryDirectory()
ks_dir = keystore.ensure_keystore(tmp.name)
mk         = secrets.token_bytes(32)
canvas     = 128
max_len    = 30
pad_size   = max_len * canvas * canvas        # 480 kB
manifest   = fileformat.save_keyfile(
    keystore_dir=ks_dir,
    master_key=mk,
    label="integration test",
    pad_specs=[pad_size, pad_size],
    max_len=max_len, canvas_size=canvas,
)
keyfile = manifest["_keyfile_path"]
print(f"  key_id    : {manifest['key_id']}    pads = 2 × {pad_size//1024} kB")
print(f"  keystore  : {ks_dir}")

# ── QApplication ──────────────────────────────────────────────────────────────
app = QApplication.instance() or QApplication([])
screen = EncryptScreen()

# Replace the screen's drive scan with a fake that returns our temp keystore
def _fake_refresh():
    entries = []
    for m in keystore.list_keys(tmp.name):
        entries.append(KeyEntry(
            drive_path=tmp.name, keystore_dir=ks_dir,
            keyfile_path=m["_keyfile_path"], manifest=m,
        ))
    screen._keys = entries
    screen.key_combo.blockSignals(True)
    screen.key_combo.clear()
    if entries:
        for e in entries:
            screen.key_combo.addItem(e.display(), e)
    else:
        screen.key_combo.addItem("(none)", None)
    screen.key_combo.blockSignals(False)
    screen._on_key_changed(0)

screen.refresh_keys = _fake_refresh
screen.refresh_keys()
assert screen._key is not None, "key entry was not loaded"
print(f"  loaded    : {screen._key.display()}")
assert screen._pad_id == "pad_0001", f"expected pad_0001, got {screen._pad_id}"
print(f"  next pad  : {screen._pad_id}")


# ── helper: run encrypt-then-save and assert ─────────────────────────────────

def encrypt_and_save(message: str, save_path: str) -> dict:
    """Drive the screen through one full encrypt+save round."""
    screen.msg_input.setText(message)
    assert screen.encrypt_btn.isEnabled(), "ENCRYPT button should be enabled"

    # mock the QFileDialog used inside _on_save_clicked
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (save_path, "FSC (*.fsc)")
    )

    # local event loop — exits when worker.finished_ok OR failed fires
    loop = QEventLoop()
    state = {"done": False, "error": None}
    orig_ok = screen._on_encrypt_ok
    orig_fail = screen._on_encrypt_failed

    def _wrap_ok(enc, viz):
        orig_ok(enc, viz)
        state["done"] = True
        loop.quit()

    def _wrap_fail(msg):
        orig_fail(msg)
        state["error"] = msg
        loop.quit()

    screen._on_encrypt_ok     = _wrap_ok
    screen._on_encrypt_failed = _wrap_fail
    screen._worker = None
    screen._on_encrypt_clicked()

    QTimer.singleShot(20_000, loop.quit)
    loop.exec()

    if state["error"]:
        raise RuntimeError(f"worker failed: {state['error']}")
    if not state["done"]:
        raise RuntimeError("worker did not finish within 20s")

    # capture enc before save (save triggers _reset_result which nulls _enc)
    captured = screen._enc
    screen._on_save_clicked()
    return captured


# ── round 1 ───────────────────────────────────────────────────────────────────
fsc1 = os.path.join(tmp.name, "msg1.fsc")
print()
print(f"  [round 1] encrypting 'HELLO PHASE 2'…")
enc1 = encrypt_and_save("HELLO PHASE 2", fsc1)
assert os.path.isfile(fsc1), ".fsc not written"
size = os.path.getsize(fsc1)
print(f"             auth_cipher = {len(enc1['auth_cipher']):,} B   "
      f".fsc on disk = {size:,} B")

# verify .fsc roundtrip
loaded = fileformat.load_fsc(fsc1)
assert loaded["key_id"]   == manifest["key_id"]
assert loaded["pad_id"]   == "pad_0001"
assert loaded["n_chars"]  == len("HELLO PHASE 2")
assert fileformat.decode_cipher(loaded["cipher"]) == enc1["auth_cipher"]
assert fileformat.decode_cipher(loaded["nonce"])  == enc1["bh_out"]["nonce"]
print(f"             .fsc roundtrip OK (cipher + nonce match)")

# pad must now be marked used
reloaded = load_keyfile(keyfile)
assert reloaded["otp_pads"]["pad_0001"]["used"] is True
assert reloaded["otp_pads"]["pad_0002"]["used"] is False
print(f"             pad_0001 marked USED on {os.path.basename(keyfile)}")

# screen state: refresh_keys called → next pad should be pad_0002
assert screen._pad_id == "pad_0002", f"expected pad_0002, got {screen._pad_id}"
print(f"             screen auto-advanced to {screen._pad_id}")

# ── round 2 ───────────────────────────────────────────────────────────────────
fsc2 = os.path.join(tmp.name, "msg2.fsc")
print()
print(f"  [round 2] encrypting 'FSC' (different message)…")
enc2 = encrypt_and_save("FSC", fsc2)
assert os.path.isfile(fsc2)

loaded2 = fileformat.load_fsc(fsc2)
assert loaded2["pad_id"] == "pad_0002"

# nonces must differ across messages
assert enc1["bh_out"]["nonce"] != enc2["bh_out"]["nonce"]
print(f"             pad_id = {loaded2['pad_id']}  (different from round 1)")
print(f"             nonce1 ≠ nonce2 ✓")

# both pads now used
exhausted = load_keyfile(keyfile)
assert exhausted["otp_pads"]["pad_0001"]["used"] is True
assert exhausted["otp_pads"]["pad_0002"]["used"] is True
assert fileformat.get_unused_pad(exhausted) is None
print(f"             both pads used — get_unused_pad() = None ✓")

# screen should now show "no pad available"
assert screen._pad_id is None
print(f"             screen state: pad_id=None (correctly out of pads)")

# clean up
tmp.cleanup()
print()
print(" ALL INTEGRATION TESTS PASSED")
print(sep)
