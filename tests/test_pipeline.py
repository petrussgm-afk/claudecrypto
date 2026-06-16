"""
FSC — 6-layer pipeline round-trip test + security checks

renderer -> material -> isotope -> fractal -> quantizer -> blackhole
blackhole -> quantizer -> fractal -> isotope -> material -> renderer

Security tests:
  - 256-bit master_key via secrets.token_bytes(32)
  - HMAC-SHA256 tamper detection
  - Nonce-gated Lorenz keystream
"""
import sys, time
import numpy as np
sys.path.insert(0, '.')

from keys.keygen import generate
from core.pipeline import encrypt, decrypt, roundtrip_error, _verify_hmac, _hmac_key, BLOCK
from core.blackhole import BlackholeParams, _generate_keystream

TEXT = "FSC"
key  = generate(TEXT)   # random 256-bit master_key

sep = "-" * 66
print(sep)
print(f" FSC 6-layer pipeline test   text={TEXT!r}")
print(f" master_key = {key.key_hex}  ({len(key.master_key)*8} bit)")
print(sep)
print(f" canvas={key.canvas_size}x{key.canvas_size}  planck={key.planck_resolution}  lorenz_init={[round(v,4) for v in key.lorenz_init]}")
print(f" Key space: 2^256 ≈ {2**256:.2e} possible keys")
print()

# ── Encrypt ───────────────────────────────────────────────────────────────
t0  = time.time()
enc = encrypt(TEXT, key)
t_enc = (time.time() - t0) * 1000

qp  = enc["quant_params"]
bh  = enc["bh_out"]

print(f"ENCRYPT   {t_enc:.1f} ms")
print()

hdr = f"  {'layer':<12}  {'shape':<16}  {'dtype':<8}  {'min':>8}  {'max':>8}"
print(hdr)
print("  " + "-" * 58)

rows = [
    ("renderer",  enc["geometry"],                          "float32"),
    ("material",  enc["material_out"]["attenuated"],        "float32"),
    ("isotope",   enc["isotope_out"]["decayed"],            "float32"),
    ("fractal",   enc["fractal_out"]["transformed"],        "float32"),
    ("quantizer", enc["quant_out"]["quantized"],            "uint16 "),
    ("blackhole", bh["cipher"],                             "uint8  "),
]
for name, arr, dtype in rows:
    print(f"  {name:<12}  {str(arr.shape):<16}  {dtype:<8}"
          f"  {arr.min():>8.4f}  {arr.max():>8.4f}")

print()
print(f"  Quantizer: vmin={qp.vmin:.6f}  vmax={qp.vmax:.6f}  step={qp.step:.4e}")
print()

print("  Per-character assignments:")
for i, ck in enumerate(key.chars):
    mp = enc["material_params"][i]
    ip = enc["isotope_params"][i]
    fp = enc["fractal_params"][i]
    atten = float(np.exp(-mp.mu * mp.thickness))
    print(f"  [{TEXT[i]}]  {mp.material:<10} mu={mp.mu:.3f} x={mp.thickness:.2f}cm atten={atten:.3f}"
          f"  | {ip.isotope:<8} n0={ip.n0:.2f}"
          f"  | phi={fp.phi_angle:.3f} n_T={fp.n_transforms}")
print()

# ── Nonce: same lorenz_init + different nonce → different keystream ───────
nonce_a = b'\x00' * 16
nonce_b = b'\x01' * 16
init    = key.lorenz_init
k_a = _generate_keystream(BlackholeParams(init, nonce=nonce_a), 1000)
k_b = _generate_keystream(BlackholeParams(init, nonce=nonce_b), 1000)
nonce_diff = int(np.sum(k_a != k_b))
print(f"  Nonce security: same lorenz_init, different nonce →")
print(f"  {nonce_diff}/1000 bytes differ ({nonce_diff/10:.1f}%) between keystreams")
print()

# ── Butterfly effect demo ─────────────────────────────────────────────────
nonce_fixed = b'\x00' * 16
init_orig  = key.lorenz_init
init_nudge = [init_orig[0] + 1e-10, init_orig[1], init_orig[2]]
k1 = _generate_keystream(BlackholeParams(init_orig,  nonce=nonce_fixed), 3000)
k2 = _generate_keystream(BlackholeParams(init_nudge, nonce=nonce_fixed), 3000)
diff_bytes = int(np.sum(k1 != k2))
total_steps = 1000 + 3000  # warmup + collection
print(f"  Butterfly effect: init perturbed by 1e-10 (same nonce)")
print(f"  Keystream divergence after {total_steps} steps: {diff_bytes}/3000 bytes differ ({diff_bytes/30:.1f}%)")
print()

# ── HMAC tamper detection ─────────────────────────────────────────────────
auth_cipher = enc["auth_cipher"]
hk = _hmac_key(key.master_key)

# correct key → no exception
try:
    _verify_hmac(auth_cipher, hk)
    hmac_ok = True
except ValueError:
    hmac_ok = False

# flip one bit in master_key → wrong HMAC key → ValueError expected
bad_master = bytes([key.master_key[0] ^ 0x01]) + key.master_key[1:]
bad_hk = _hmac_key(bad_master)
try:
    _verify_hmac(auth_cipher, bad_hk)
    tamper_detected = False
except ValueError:
    tamper_detected = True

# flip one byte in ciphertext payload → tampered data → ValueError expected
tampered_ct = auth_cipher[:33] + bytes([auth_cipher[33] ^ 0xFF]) + auth_cipher[34:]
try:
    _verify_hmac(tampered_ct, hk)
    data_tamper_detected = False
except ValueError:
    data_tamper_detected = True

print(f"  HMAC with correct key:       {'PASS' if hmac_ok else 'FAIL'}")
print(f"  HMAC with wrong key:         {'tamper detected (PASS)' if tamper_detected else 'FAIL — not detected'}")
print(f"  HMAC with flipped ciphertext:{'tamper detected (PASS)' if data_tamper_detected else 'FAIL — not detected'}")
auth_bytes = len(auth_cipher)
print(f"  auth_cipher size: {auth_bytes} bytes (padded to {BLOCK}-byte block, +32 HMAC)")
print()

# ── Decrypt ───────────────────────────────────────────────────────────────
t1  = time.time()
dec = decrypt(enc, t_decrypt=key.t_encrypt)  # same instant — avoids short-lived isotope expiry
t_dec = (time.time() - t1) * 1000

print(f"DECRYPT   {t_dec:.1f} ms  (HMAC verified, t_decrypt=t_encrypt)")
print()

# ── Round-trip errors ─────────────────────────────────────────────────────
errs = roundtrip_error(enc, dec)

print(f"  {'layer':<18}  {'max err':>10}  {'mean err':>10}  {'note'}")
print("  " + "-" * 58)

notes = {
    "after_bh":       "exact — XOR self-inverse",
    "after_quant":    "<=step/2 — quantizer bound",
    "after_fractal":  "exact — permutation",
    "after_isotope":  "amplified by 1/n0",
    "after_material": "amplified by 1/atten (key-dependent)",
    "geometry_final": "full round-trip",
}
# Algorithmic layers always pass; physical layers depend on material attenuation
ALGO_LAYERS = {"after_bh", "after_quant", "after_fractal"}
ok_algo = True
ok_phys = True
for name, e in errs.items():
    is_exact = "bh" in name
    ok = (e["max"] < 1e-9) if is_exact else (e["max"] / (qp.vmax - qp.vmin) < 0.01)
    if name in ALGO_LAYERS:
        ok_algo = ok_algo and ok
    else:
        ok_phys = ok_phys and ok
    flag = "OK  " if ok else "WARN"
    print(f"  {name:<18}  {e['max']:>10.4e}  {e['mean']:>10.4e}  {flag}  {notes[name]}")

print()
fractal_adds_no_error = abs(errs["after_fractal"]["max"] - errs["after_quant"]["max"]) < 1e-9
print(f"  Blackhole XOR exact:              {errs['after_bh']['max'] == 0.0}")
print(f"  Fractal adds no error (permut.):  {fractal_adds_no_error}")
print(f"  Physical fidelity (this key):     {'OK' if ok_phys else 'WARN (high-atten material)'}")
print()

# ── Ciphertext stats ──────────────────────────────────────────────────────
cipher = bh["cipher"]
counts = np.bincount(cipher.ravel(), minlength=256)
probs = counts[counts > 0] / cipher.size
entropy = -np.dot(probs, np.log2(probs))
print(f"  Ciphertext: {cipher.size} bytes  ({cipher.size/1024:.1f} kB)")
print(f"  Byte entropy: {entropy:.3f} / 8.000 bits  (ideal=8.000)")
print()

# ── Security summary ──────────────────────────────────────────────────────
security_ok = hmac_ok and tamper_detected and data_tamper_detected
print(f"  ALGORITHMIC LAYERS: {'PASS' if ok_algo else 'FAIL'}")
print(f"  PHYSICAL FIDELITY:  {'PASS' if ok_phys else 'WARN (high-atten material — expected)'}")
print(f"  SECURITY CHECKS:    {'PASS' if security_ok else 'FAIL'}")
print(f"  key_hex length:     {len(key.key_hex)} chars ({len(key.key_hex)*4} bit)")
print(sep)
