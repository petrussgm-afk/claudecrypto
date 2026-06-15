"""
FSC — 6-layer pipeline round-trip test

renderer -> material -> isotope -> fractal -> quantizer -> blackhole
blackhole -> quantizer -> fractal -> isotope -> material -> renderer
"""
import sys, time, numpy as np
sys.path.insert(0, '.')

from keys.keygen import generate
from core.pipeline import encrypt, decrypt, roundtrip_error

TEXT = "FSC"
key  = generate(TEXT, master_seed=42)

sep = "-" * 62
print(sep)
print(f" FSC 6-layer pipeline test   text={TEXT!r}   seed={key.master_seed}")
print(sep)
print(f" canvas={key.canvas_size}x{key.canvas_size}  planck={key.planck_resolution}  lorenz_init={[round(v,4) for v in key.lorenz_init]}")
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

# ── Butterfly effect demo ─────────────────────────────────────────────────
from core.blackhole import BlackholeParams, _generate_keystream
init_orig  = key.lorenz_init
init_nudge = [init_orig[0] + 1e-10, init_orig[1], init_orig[2]]
# 3000 steps needed for 1e-10 perturbation to reach O(1) with Lyapunov ~0.9
k1 = _generate_keystream(BlackholeParams(init_orig),  3000)
k2 = _generate_keystream(BlackholeParams(init_nudge), 3000)
diff_bytes = int(np.sum(k1 != k2))
print(f"  Butterfly effect: init perturbed by 1e-10")
print(f"  Keystream divergence after {1000+3000} steps: {diff_bytes}/3000 bytes differ ({diff_bytes/30:.1f}%)")
print()

# ── Decrypt ───────────────────────────────────────────────────────────────
t1  = time.time()
dec = decrypt(enc, t_decrypt=time.time())
t_dec = (time.time() - t1) * 1000

print(f"DECRYPT   {t_dec:.1f} ms")
print()

# ── Round-trip errors ─────────────────────────────────────────────────────
errs = roundtrip_error(enc, dec)

print(f"  {'layer':<18}  {'max err':>10}  {'mean err':>10}  {'note'}")
print("  " + "-" * 58)

notes = {
    "after_bh":       "exact (XOR self-inverse)",
    "after_quant":    "<=step/2 (quantizer bound)",
    "after_fractal":  "exact (permutation)",
    "after_isotope":  "amplified by 1/n0",
    "after_material": "amplified by 1/atten",
    "geometry_final": "full round-trip",
}
ok_all = True
for name, e in errs.items():
    ok = (e["max"] < 1e-9) if "bh" in name else (e["max"] / (qp.vmax - qp.vmin) < 0.01)
    ok_all = ok_all and ok
    flag = "OK  " if ok else "WARN"
    print(f"  {name:<18}  {e['max']:>10.4e}  {e['mean']:>10.4e}  {flag}  {notes[name]}")

print()
fractal_adds_no_error = abs(errs["after_fractal"]["max"] - errs["after_quant"]["max"]) < 1e-9
print(f"  Blackhole XOR exact:              {errs['after_bh']['max'] == 0.0}")
print(f"  Fractal adds no error (permut.):  {fractal_adds_no_error}")
print(f"  Final error < 1% signal range:    {errs['geometry_final']['max'] / (qp.vmax - qp.vmin) < 0.01}")
print()

# ── Ciphertext stats ──────────────────────────────────────────────────────
cipher = bh["cipher"]
entropy = -np.sum(
    [p * np.log2(p) for p in np.bincount(cipher.ravel(), minlength=256) / cipher.size if p > 0]
)
print(f"  Ciphertext: {cipher.size} bytes  ({cipher.size/1024:.1f} kB)")
print(f"  Byte entropy: {entropy:.3f} / 8.000 bits  (ideal=8.000)")
print()
print(f"  ALL LAYERS PASS: {ok_all}")
print(sep)
