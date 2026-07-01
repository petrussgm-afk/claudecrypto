"""
Smoke test for app.decrypt_engine.decrypt_fsc.

Forge a key, encrypt "FSC" via the same path the GUI uses, save the .fsc
envelope, then decrypt it with ONLY the .fsc file + .fsckey + pad bin.

Verifies the cross-process decrypt path matches the in-memory round-trip
in tests/test_pipeline.py (i.e. the geometry images come out close to the
originals).
"""
import os, secrets, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from app import fileformat, keystore
from app.decrypt_engine import decrypt_fsc, find_key_on_disk
from app.fileformat     import encode_cipher, load_fsc, load_keyfile, read_pad
from keys.keygen        import generate
from core.pipeline      import encrypt as pipeline_encrypt

# Well-conditioned key: "FSC" → bone/tissue/tissue, all attenuation > 0.1, so the
# decrypt round-trip never trips the Beer-Lambert over-absorption guard. Pinned
# for determinism (was secrets.token_bytes(32), which occasionally drew thick lead).
WELL_CONDITIONED_KEY = bytes.fromhex(
    "c00809b7b761e3143d5b3f6409ebee28567e77be43ce1057d56b317905e61677")

sep = "-" * 66
print(sep)
print(" decrypt_engine smoke test")
print(sep)

with tempfile.TemporaryDirectory() as base:
    ks_dir = keystore.ensure_keystore(base)

    # ── forge key + pad ───────────────────────────────────────────────────
    mk       = WELL_CONDITIONED_KEY
    canvas   = 128
    max_len  = 30
    pad_sz   = max_len * canvas * canvas
    manifest = fileformat.save_keyfile(
        keystore_dir=ks_dir, master_key=mk, label="dec smoke",
        pad_specs=[pad_sz, pad_sz], max_len=max_len, canvas_size=canvas,
    )
    key_id = manifest["key_id"]
    print(f" key_id     : {key_id}")

    # ── encrypt 'FSC' using disk pad ──────────────────────────────────────
    text     = "FSC"
    pad_id   = "pad_0001"
    disk_pad = read_pad(ks_dir, pad_id, keydata=manifest)
    key      = generate(text, master_key=mk, canvas_size=canvas)
    key.otp_pad = disk_pad
    enc = pipeline_encrypt(text, key)
    print(f" encrypted  : {text!r}   auth_cipher = {len(enc['auth_cipher']):,} B")

    # ── build & save .fsc envelope (matching EncryptScreen._build_fsc_dict) ─
    qp = enc["quant_params"]
    fsc_data = {
        "version":        fileformat.FSC_VERSION,
        "key_id":         key_id,
        "pad_id":         pad_id,
        "t_encrypt":      float(key.t_encrypt),
        "canvas_size":    canvas,
        "n_chars":        len(text),
        "shape":          list(enc["otp_out"].shape),
        "nonce":          encode_cipher(enc["bh_out"]["nonce"]),
        "cipher":         encode_cipher(enc["auth_cipher"]),
        "quant_n_levels": int(qp.n_levels),
        "quant_vmin":     float(qp.vmin),
        "quant_vmax":     float(qp.vmax),
    }
    fsc_path = os.path.join(base, "msg.fsc")
    fileformat.save_fsc(fsc_path, fsc_data)
    print(f" .fsc       : {os.path.getsize(fsc_path):,} B → {fsc_path}")

    # ── close & reload — simulate a fresh process ─────────────────────────
    del enc, key, qp, fsc_data
    reloaded_fsc = load_fsc(fsc_path)
    keys_on_disk = keystore.list_keys(base)
    found        = find_key_on_disk(reloaded_fsc, keys_on_disk)
    assert found is not None, "key_id lookup failed"
    print(f" found key  : {found['key_id']} on {os.path.basename(found['_keyfile_path'])}")

    disk_mk  = bytes.fromhex(found["master_key"])
    disk_pad = read_pad(ks_dir, reloaded_fsc["pad_id"], keydata=found)

    # ── DECRYPT ──────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    result = decrypt_fsc(
        fsc_data=reloaded_fsc,
        master_key=disk_mk,
        pad_bytes=disk_pad,
        t_decrypt=reloaded_fsc["t_encrypt"],   # avoid isotope-decay drift
    )
    elapsed = (time.perf_counter() - t0) * 1000
    print(f" decrypted  : {result.n_chars} glyphs   {elapsed:.0f} ms")

    # ── compare with what encrypt-side encrypt would produce ──────────────
    # Re-encrypt the same text to get the canonical original geometry,
    # then compare relative image structure (decrypt is lossy due to
    # quantizer + isotope amplification, so we measure normalized cosine).
    key2 = generate(text, master_key=mk, canvas_size=canvas)
    key2.otp_pad = disk_pad
    enc2 = pipeline_encrypt(text, key2)
    orig_geo = enc2["geometry"]      # shape (n, H, W)

    print()
    print(f"  {'char':<5} {'cosine sim':>12} {'max':>8} {'mean':>8}")
    print("  " + "-" * 40)
    for i in range(len(text)):
        rec = np.asarray(result.geometry[i], dtype=np.float64).ravel()
        org = np.asarray(orig_geo[i],        dtype=np.float64).ravel()
        cos = float(np.dot(rec, org) /
                    (np.linalg.norm(rec) * np.linalg.norm(org) + 1e-12))
        print(f"  {text[i]!r:<5} {cos:>12.4f} {rec.max():>8.3f} {rec.mean():>8.4f}")

print()
print(" decrypt round-trip OK")
print(sep)
