"""Dry-run of all demo/app.py logic without the Streamlit runtime."""
import sys, os, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from keys.keygen import generate
from core.pipeline import encrypt
from core.blackhole import BlackholeParams, _generate_keystream
from viz.visualizer import visualize


def _entropy(cipher):
    counts = np.bincount(cipher.ravel(), minlength=256)
    probs  = counts / counts.sum()
    return float(-np.sum(p * np.log2(p) for p in probs if p > 0))


def _fmt_halflife(s):
    if s < 1e-3:              return f"{s*1e6:.2g} us"
    if s < 1:                 return f"{s*1e3:.2g} ms"
    if s < 3600:              return f"{s/3600:.2g} h"
    if s < 365.25 * 86400:   return f"{s/86400:.2g} d"
    return f"{s/(365.25*86400):.2e} yr"


def _hex_dump(data, n=32, cols=16):
    flat = data.ravel()[:n]
    lines = []
    for i in range(0, len(flat), cols):
        chunk    = flat[i:i+cols]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        lines.append(f"{i:04X}  {hex_part}")
    return "\n".join(lines)


TEXT = "Hi!"
key  = generate(TEXT, master_seed=99, canvas_size=64, planck_resolution=256)
t0   = time.perf_counter()
enc  = encrypt(TEXT, key)
t_ms = (time.perf_counter() - t0) * 1000

cipher = enc["bh_out"]["cipher"]
qp     = enc["quant_params"]

print(f"Pipeline OK  {t_ms:.1f} ms  text={TEXT!r}")
print(f"entropy={_entropy(cipher):.3f}")
print(f"ciphertext={cipher.size} bytes")

for i, ch in enumerate(TEXT):
    mp = enc["material_params"][i]
    ip = enc["isotope_params"][i]
    fp = enc["fractal_params"][i]
    hl = _fmt_halflife(ip.half_life)
    print(f"  [{ch}] {mp.material:10s} {ip.isotope:8s} hl={hl:12s} phi={fp.phi_angle:.3f}")

init  = key.lorenz_init
nudge = [init[0] + 1e-10, init[1], init[2]]
k1 = _generate_keystream(BlackholeParams(lorenz_init=init),  500)
k2 = _generate_keystream(BlackholeParams(lorenz_init=nudge), 500)
diff = int(np.sum(k1 != k2))
print(f"butterfly: {diff}/500 bytes differ ({diff/5:.1f}%)")

print("hex dump (first 32 bytes):")
print(_hex_dump(cipher, n=32))

tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
tmp.close()
visualize(enc, save_path=tmp.name)
sz = os.path.getsize(tmp.name)
os.unlink(tmp.name)
print(f"visualize OK  png={sz//1024} kB")
print("ALL OK")
