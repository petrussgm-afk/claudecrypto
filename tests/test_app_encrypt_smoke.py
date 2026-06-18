"""
Smoke test for the Phase-2 ENCRYPT data path (no Qt required).

  1. forge a key with 3 pads via app.fileformat
  2. simulate Encrypt-screen flow: build FSCKey, override otp_pad with disk pad,
     run pipeline.encrypt
  3. build the .fsc envelope and write it via app.fileformat.save_fsc
  4. mark the pad used; verify keyfile manifest reflects the change
  5. encrypt a SECOND message — verify get_unused_pad selects the next pad
"""
import os, secrets, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from app import fileformat, keystore
from keys.keygen     import generate
from core.pipeline   import encrypt as pipeline_encrypt

sep = "-" * 66
print(sep)
print(" FSC encrypt smoke test (Phase 2)")
print(sep)

with tempfile.TemporaryDirectory() as base:
    ks_dir = keystore.ensure_keystore(base)
    print(f" keystore     : {ks_dir}")

    # ── forge key + 3 pads ────────────────────────────────────────────────
    mk          = secrets.token_bytes(32)
    canvas      = 128
    max_len     = 30
    pad_size    = max_len * canvas * canvas
    manifest    = fileformat.save_keyfile(
        keystore_dir=ks_dir,
        master_key=mk,
        label="phase2 smoke",
        pad_specs=[pad_size, pad_size, pad_size],
        max_len=max_len, canvas_size=canvas,
    )
    keyfile_path = manifest["_keyfile_path"]
    print(f" key_id       : {manifest['key_id']}    pads = 3 × {pad_size/1024:.0f} kB")

    # ── round 1: encrypt + save + mark used ──────────────────────────────
    text = "HELLO WORLD"
    pad_id = fileformat.get_unused_pad(manifest)
    print()
    print(f" [round 1]  pad_id = {pad_id}   text = {text!r}")

    disk_pad = fileformat.read_pad(ks_dir, pad_id, keydata=manifest)
    assert len(disk_pad) == pad_size

    # build key, OVERRIDE otp_pad with disk pad (this is the critical step)
    key = generate(text, master_key=mk, canvas_size=canvas)
    key.otp_pad = disk_pad

    t0 = time.perf_counter()
    enc = pipeline_encrypt(text, key)
    print(f"            encrypted in {(time.perf_counter()-t0)*1000:.0f} ms"
          f"   auth_cipher = {len(enc['auth_cipher']):,} B")

    # build envelope
    fsc_dict = {
        "version":     fileformat.FSC_VERSION,
        "key_id":      manifest["key_id"],
        "pad_id":      pad_id,
        "t_encrypt":   float(key.t_encrypt),
        "canvas_size": canvas,
        "n_chars":     len(text),
        "shape":       list(enc["otp_out"].shape),
        "nonce":       fileformat.encode_cipher(enc["bh_out"]["nonce"]),
        "cipher":      fileformat.encode_cipher(enc["auth_cipher"]),
    }
    fsc_path = os.path.join(base, "msg1.fsc")
    fileformat.save_fsc(fsc_path, fsc_dict)
    print(f"            wrote {fsc_path}  ({os.path.getsize(fsc_path):,} B)")

    # read back, verify base64 roundtrip
    loaded = fileformat.load_fsc(fsc_path)
    assert loaded["pad_id"]  == pad_id
    assert loaded["nonce"]   == fsc_dict["nonce"]
    assert fileformat.decode_cipher(loaded["cipher"]) == enc["auth_cipher"]
    assert fileformat.decode_cipher(loaded["nonce"])  == enc["bh_out"]["nonce"]
    print(f"            .fsc roundtrip OK  (cipher + nonce match)")

    # mark pad used
    fileformat.mark_pad_used(keyfile_path, pad_id)
    reloaded = fileformat.load_keyfile(keyfile_path)
    assert reloaded["otp_pads"][pad_id]["used"] is True
    assert reloaded["otp_pads"]["pad_0002"]["used"] is False
    next_pad = fileformat.get_unused_pad(reloaded)
    assert next_pad == "pad_0002"
    print(f"            pad marked used   next = {next_pad}")

    # ── round 2: encrypt second message ──────────────────────────────────
    text2 = "FSC"
    pad_id2 = fileformat.get_unused_pad(reloaded)
    print()
    print(f" [round 2]  pad_id = {pad_id2}   text = {text2!r}")

    disk_pad2 = fileformat.read_pad(ks_dir, pad_id2, keydata=reloaded)
    key2 = generate(text2, master_key=mk, canvas_size=canvas)
    key2.otp_pad = disk_pad2
    enc2 = pipeline_encrypt(text2, key2)

    # the nonces of the two messages MUST differ (each encrypt creates fresh)
    assert enc["bh_out"]["nonce"] != enc2["bh_out"]["nonce"]
    print(f"            nonce1 ≠ nonce2  ✓   (each encrypt = fresh nonce)")

    # ── round 3: simulate "all pads consumed" ────────────────────────────
    fileformat.mark_pad_used(keyfile_path, pad_id2)
    fileformat.mark_pad_used(keyfile_path, "pad_0003")
    exhausted = fileformat.load_keyfile(keyfile_path)
    assert fileformat.get_unused_pad(exhausted) is None
    print()
    print(f" [exhausted] all 3 pads used  →  get_unused_pad = None  ✓")

print()
print(" ALL ENCRYPT SMOKE TESTS PASSED")
print(sep)
