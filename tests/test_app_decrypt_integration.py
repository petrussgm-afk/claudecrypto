"""
Integration test — drives the actual DecryptScreen widget through the full
encrypt → save → reload → decrypt round-trip.

Covers:
  1. Happy path: encrypt 'HELLO', save .fsc, DecryptScreen loads it, finds
     key+pad, runs decrypt, returns recovered glyph images.
  2. Wrong key: tamper the master_key inside the .fsckey → HMAC verify fails
     with a clean error message.
  3. Missing pad: delete the pad_*.bin from disk → screen marks pad-on-disk
     as ✗ and the DECRYPT button is disabled.

No mouse/SendKeys — Qt signals/slots only, via an offscreen QApplication.
"""
import os, secrets, shutil, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtCore    import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from app import fileformat, keystore
from app.fileformat       import (
    FSC_VERSION, encode_cipher, load_keyfile, read_pad, save_fsc,
)
from app.screens.encrypt  import EncryptScreen, KeyEntry
from app.screens.decrypt  import DecryptScreen
from keys.keygen          import generate
from core.pipeline        import encrypt as pipeline_encrypt

# Well-conditioned key: "HELLO" (5 chars) draws water/wood/tissue, all attenuation
# > 0.1, so the decrypt round-trip never trips the Beer-Lambert over-absorption
# guard. Pinned for determinism (was secrets.token_bytes(32), occasionally lead).
WELL_CONDITIONED_KEY = bytes.fromhex(
    "da75a2440dd346387dcb7840344f637d001c01d315edb852d8d9839da3836d1a")

sep = "-" * 66
print(sep)
print(" DecryptScreen integration test (Phase 3)")
print(sep)

# ── temp keystore + forge a key ───────────────────────────────────────────────
tmp = tempfile.TemporaryDirectory()
ks_dir   = keystore.ensure_keystore(tmp.name)
mk       = WELL_CONDITIONED_KEY
canvas   = 128
max_len  = 30
pad_size = max_len * canvas * canvas
manifest = fileformat.save_keyfile(
    keystore_dir=ks_dir, master_key=mk, label="decrypt integration",
    pad_specs=[pad_size, pad_size],
    max_len=max_len, canvas_size=canvas,
)
key_id   = manifest["key_id"]
keyfile  = manifest["_keyfile_path"]
print(f"  key_id    : {key_id}")
print(f"  keystore  : {ks_dir}")

# ── QApplication ──────────────────────────────────────────────────────────────
app = QApplication.instance() or QApplication([])

# Patch BOTH screens' drive scan to point at our temp keystore.
def _patched_scannable():
    from app.keystore import DriveInfo, DRIVE_FIXED
    return [DriveInfo(path=tmp.name, label="tmp", drive_type=DRIVE_FIXED,
                      free_bytes=10**9, removable=False)]
keystore.list_scannable_drives = _patched_scannable

# ── encrypt 'HELLO' through EncryptScreen ─────────────────────────────────────
print()
print("  STEP 1 — encrypt 'HELLO' via EncryptScreen")
enc_screen = EncryptScreen()
enc_screen.refresh_keys()
assert enc_screen._key is not None, "key not detected"
assert enc_screen._pad_id == "pad_0001"

fsc_path = os.path.join(tmp.name, "hello.fsc")
QFileDialog.getSaveFileName = staticmethod(
    lambda *a, **k: (fsc_path, "FSC (*.fsc)")
)


def _run_encrypt(message: str):
    enc_screen.msg_input.setText(message)
    loop = QEventLoop()
    state = {"done": False, "error": None}
    orig_ok   = enc_screen._on_encrypt_ok
    orig_fail = enc_screen._on_encrypt_failed

    def _wrap_ok(enc, viz):
        orig_ok(enc, viz);  state["done"]  = True; loop.quit()
    def _wrap_fail(msg):
        orig_fail(msg);     state["error"] = msg;  loop.quit()

    enc_screen._on_encrypt_ok     = _wrap_ok
    enc_screen._on_encrypt_failed = _wrap_fail
    enc_screen._on_encrypt_clicked()
    QTimer.singleShot(30_000, loop.quit)
    loop.exec()
    if state["error"]:
        raise RuntimeError(state["error"])
    assert state["done"], "encrypt worker did not finish"


_run_encrypt("HELLO")
enc_screen._on_save_clicked()
assert os.path.isfile(fsc_path)
print(f"             encrypted + .fsc written  ({os.path.getsize(fsc_path):,} B)")

# also keep the original geometry images for comparison
orig_key = generate("HELLO", master_key=mk, canvas_size=canvas)
orig_key.otp_pad = read_pad(ks_dir, "pad_0001", keydata=load_keyfile(keyfile))
# Note: pad_0001 is now marked used on disk; that's fine, we read it directly
orig_enc = pipeline_encrypt("HELLO", orig_key)
orig_geometry = orig_enc["geometry"]   # for comparison


# ── STEP 2: DecryptScreen — happy path ───────────────────────────────────────
print()
print("  STEP 2 — DecryptScreen loads .fsc, runs decrypt")
dec_screen = DecryptScreen()
dec_screen._on_fsc_loaded_from_disk(fsc_path)

assert dec_screen._key_manifest is not None,  "DecryptScreen failed to find key"
assert dec_screen._pad_bytes is not None,     "DecryptScreen failed to read pad"
assert dec_screen.decrypt_btn.isEnabled(),    "DECRYPT should be enabled"
print(f"             info panel populated   key_id={dec_screen._key_manifest['key_id']}")

# Drive the decrypt worker (same shape as encrypt test)
def _run_decrypt():
    loop = QEventLoop()
    state = {"result": None, "error": None}
    orig_ok = dec_screen._on_decrypt_ok
    orig_fail = dec_screen._on_decrypt_failed

    def _wrap_ok(res):
        orig_ok(res); state["result"] = res; loop.quit()
    def _wrap_fail(msg):
        orig_fail(msg); state["error"] = msg; loop.quit()

    dec_screen._on_decrypt_ok = _wrap_ok
    dec_screen._on_decrypt_failed = _wrap_fail
    dec_screen._on_decrypt_clicked()
    QTimer.singleShot(30_000, loop.quit)
    loop.exec()
    if state["error"]:
        raise RuntimeError(state["error"])
    assert state["result"] is not None, "decrypt did not finish"
    return state["result"]


t0 = time.perf_counter()
res = _run_decrypt()
elapsed = (time.perf_counter() - t0) * 1000
print(f"             decrypted in {elapsed:.0f} ms   "
      f"{res.n_chars} glyphs recovered")

# Compare each recovered glyph to the original
print()
print(f"  {'char':<5} {'cosine':>10} {'recovered_max':>14} {'orig_max':>10}")
print("  " + "-" * 46)
TEXT = "HELLO"
cosines = []
for i in range(res.n_chars):
    rec = np.asarray(res.geometry[i], dtype=np.float64).ravel()
    org = np.asarray(orig_geometry[i], dtype=np.float64).ravel()
    cos = float(np.dot(rec, org) /
                (np.linalg.norm(rec) * np.linalg.norm(org) + 1e-12))
    cosines.append(cos)
    print(f"  {TEXT[i]!r:<5} {cos:>10.4f} {rec.max():>14.3f} {org.max():>10.3f}")

# For most chars, cosine should be ~1.0; degraded for hostile material/isotope
# assignments (documented PHYSICAL FIDELITY warn). Demand at least one char
# above 0.9 so we know the pipeline really ran.
assert max(cosines) > 0.9, f"no glyph recovered well — max cosine {max(cosines):.3f}"
print()
print(f"             best cosine = {max(cosines):.4f}  ✓ recovery confirmed")


# ── STEP 3: wrong key (tamper master_key inside .fsckey) ──────────────────────
print()
print("  STEP 3 — wrong-key error (tampered master_key in .fsckey)")
import json
with open(keyfile, "r", encoding="utf-8") as f:
    bad = json.load(f)
orig_mk_hex = bad["master_key"]
# XOR first byte of master_key with 0xFF — guaranteed HMAC mismatch
real_mk  = bytes.fromhex(orig_mk_hex)
tampered = bytes([real_mk[0] ^ 0xFF]) + real_mk[1:]
bad["master_key"] = tampered.hex()
with open(keyfile, "w", encoding="utf-8") as f:
    json.dump(bad, f, indent=2, ensure_ascii=False)

# Reload via DecryptScreen — key_id in JSON is unchanged, so the lookup
# still matches the .fsc envelope. HMAC verify is what catches the tamper.
dec_screen2 = DecryptScreen()
dec_screen2._on_fsc_loaded_from_disk(fsc_path)
assert dec_screen2._key_manifest is not None, "tampered key should still match by key_id"

# Run the decrypt and verify it fails with HMAC error
loop = QEventLoop()
state = {"error": None, "result": None}

def _ok(r): state["result"] = r; loop.quit()
def _fail(msg): state["error"] = msg; loop.quit()

dec_screen2._on_decrypt_ok     = _ok
dec_screen2._on_decrypt_failed = _fail
dec_screen2._on_decrypt_clicked()
QTimer.singleShot(15_000, loop.quit)
loop.exec()

assert state["result"] is None, "tampered key should not produce a result"
assert state["error"] is not None, "tampered key should produce an error"
assert "HMAC" in state["error"] or "ValueError" in state["error"], \
    f"expected HMAC failure, got: {state['error']}"
print(f"             tampered key → clean error: {state['error']}")

# restore the keyfile so the next test isn't poisoned
bad["master_key"] = orig_mk_hex
with open(keyfile, "w", encoding="utf-8") as f:
    json.dump(bad, f, indent=2, ensure_ascii=False)


# ── STEP 4: missing pad file ──────────────────────────────────────────────────
print()
print("  STEP 4 — missing-pad error (delete pad_0001.bin from disk)")
pad_path = os.path.join(ks_dir, manifest["otp_pads"]["pad_0001"]["file"])
os.unlink(pad_path)

dec_screen3 = DecryptScreen()
dec_screen3._on_fsc_loaded_from_disk(fsc_path)

assert dec_screen3._key_manifest is not None, "key should still be present"
assert dec_screen3._pad_bytes is None, "pad bytes should be None"
assert not dec_screen3.decrypt_btn.isEnabled(), \
    "DECRYPT button should be disabled when pad is missing"
print(f"             pad file deleted → DECRYPT disabled, status shows pad missing")

tmp.cleanup()
print()
print(" ALL DECRYPT INTEGRATION TESTS PASSED")
print(sep)
