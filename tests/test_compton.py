"""
FSC — Compton scatter sub-layer test (Klein-Nishina, deterministic)

Checks:
  1. compton_energy: θ=0 → E'=E (factor 1.0); θ=π → hand-computed minimum for E=80
  2. derive_scatter_angle: same seed → same angle; different seeds → different
  3. scatter_factor: θ=0 is identity (1.0) for every Z
  4. apply_compton / reverse_compton exact round-trip, max error < 1e-9
  5. full pipeline with compton_mode=True: encrypt→decrypt round-trip holds,
     same error budget as without Compton (exact reversible factor → no new error)
"""
import sys
import numpy as np
sys.path.insert(0, '.')

# Windows consoles default to cp1250; force UTF-8 so θ/π/≈ print cleanly.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

from core import compton
from keys.keygen import generate
from core.pipeline import encrypt, decrypt, roundtrip_error

sep = "-" * 66
print(sep)
print(" FSC Compton scatter sub-layer test")
print(sep)

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# ── 1. compton_energy ──────────────────────────────────────────────────────
E = 80.0
e0 = compton.compton_energy(E, 0.0)
check("compton_energy θ=0 → E'=E", abs(float(e0) - E) < 1e-12, f"E'={float(e0):.6f}")

# hand computation for θ=π, E=80:
#   E' = 80 / (1 + (80/511)(1 - cos π)) = 80 / (1 + (80/511)*2)
#      = 80 / 1.313112... = 60.92462... keV
e_pi = float(compton.compton_energy(E, np.pi))
expected_pi = 80.0 / (1.0 + (80.0 / 511.0) * 2.0)
check("compton_energy θ=π hand value (E=80)", abs(e_pi - expected_pi) < 1e-9,
      f"E'={e_pi:.5f} keV, expected {expected_pi:.5f}")
check("compton_energy θ=π < E (energy lost)", e_pi < E, f"{e_pi:.4f} < {E}")

f0 = compton.compton_energy(E, 0.0) / E
check("energy factor at θ=0 is 1.0", abs(float(f0) - 1.0) < 1e-12)


# ── 2. derive_scatter_angle determinism ────────────────────────────────────
a1 = compton.derive_scatter_angle(123456, E)
a2 = compton.derive_scatter_angle(123456, E)
check("same seed → same angle", a1 == a2, f"θ={a1:.6f} rad")

b = compton.derive_scatter_angle(987654, E)
c = compton.derive_scatter_angle(555555, E)
distinct = len({round(a1, 9), round(b, 9), round(c, 9)}) == 3
check("different seeds → different angles", distinct,
      f"{a1:.4f}, {b:.4f}, {c:.4f}")
check("angle in [0, π]", 0.0 <= a1 <= np.pi and 0.0 <= b <= np.pi)


# ── 3. scatter_factor identity at θ=0 ──────────────────────────────────────
ident_ok = all(abs(compton.scatter_factor(Z, E, 0.0) - 1.0) < 1e-12
               for Z in (6.0, 13.0, 26.0, 82.0))
check("scatter_factor θ=0 == 1.0 for all Z (identity)", ident_ok)

# higher Z → factor further below 1 at a fixed non-zero angle
theta = 2.5
f_lowZ = compton.scatter_factor(6.0, E, theta)
f_highZ = compton.scatter_factor(82.0, E, theta)
check("higher Z → more Compton (factor lower)", f_highZ < f_lowZ,
      f"Z6={f_lowZ:.5f}  Z82={f_highZ:.5f}")


# ── 4. apply_compton / reverse_compton exact round-trip ────────────────────
rng = np.random.default_rng(0)
arr = rng.random((3, 32, 32)).astype(np.float64)
scattered, theta_s, factor_s = compton.apply_compton(arr, Z=82.0, E_keV=90.0, seed=424242)
recovered = compton.reverse_compton(scattered, factor_s)
max_err = float(np.abs(arr - recovered).max())
check("apply→reverse round-trip max error < 1e-9", max_err < 1e-9, f"max_err={max_err:.3e}")
check("scatter_factor in (0, 1]", 0.0 < factor_s <= 1.0, f"factor={factor_s:.6f}")

# identity path: θ=0 seed-independent check via factor==1 → array unchanged
sc_id, _, f_id = compton.apply_compton(arr, Z=82.0, E_keV=80.0, seed=1)
# not necessarily θ=0, but reverse must still be exact
rec_id = compton.reverse_compton(sc_id, f_id)
check("second round-trip exact", float(np.abs(arr - rec_id).max()) < 1e-9)


# ── 5a. full pipeline with compton_mode=True (random key) ──────────────────
# These checks hold for ANY key regardless of how well-conditioned the random
# material/isotope draw is: the flag flows through, Compton engages, and the
# exact algorithmic layers (OTP / Lorenz XOR) stay bit-exact.
TEXT = "FSC"
key = generate(TEXT, compton_mode=True)
check("key.compton_mode flag set", key.compton_mode is True)

enc = encrypt(TEXT, key)
any_compton = any(mp.compton_enabled for mp in enc["material_params"])
energies = [round(mp.photon_energy_keV, 2) for mp in enc["material_params"]]
angles = [round(mp.scatter_angle, 4) for mp in enc["material_params"]]
in_band = all(40.0 <= mp.photon_energy_keV <= 120.0 for mp in enc["material_params"])
check("material params have Compton enabled", any_compton,
      f"E(keV)={energies}  θ(rad)={angles}")
check("per-char photon energy in 40–120 keV", in_band)

dec = decrypt(enc, t_decrypt=key.t_encrypt)
errs = roundtrip_error(enc, dec)
check("after_otp exact (< 1e-9)", errs["after_otp"]["max"] < 1e-9,
      f'{errs["after_otp"]["max"]:.3e}')
check("after_bh exact (< 1e-9)", errs["after_bh"]["max"] < 1e-9,
      f'{errs["after_bh"]["max"]:.3e}')

# ── 5b. "no new error" on a well-conditioned key ───────────────────────────
# The physical round-trip fidelity of the FSC pipeline is key-dependent: a key
# that draws a near-opaque material (e.g. thick lead/iron, atten → 0) blows up
# 1/atten on reverse whether or not Compton is on — test_pipeline treats those
# as WARN, not failure. To isolate Compton's contribution we use a fixed,
# well-conditioned master_key (low-attenuation draw) and show that turning
# Compton ON keeps the round-trip within the same <1% budget, adding only the
# tiny 1/scatter_factor amplification of the pre-existing quantizer error.
WELL_CONDITIONED = (101).to_bytes(4, "big").ljust(32, b"\x00")


def geom_rel_error(compton_on):
    k = generate(TEXT, master_key=WELL_CONDITIONED, compton_mode=compton_on)
    e = encrypt(TEXT, k)
    d = decrypt(e, t_decrypt=k.t_encrypt)
    er = roundtrip_error(e, d)
    qp = e["quant_params"]
    span = qp.vmax - qp.vmin
    return er["geometry_final"]["max"] / span if span > 0 else er["geometry_final"]["max"]


rel_off = geom_rel_error(False)
rel_on = geom_rel_error(True)
check("well-conditioned round-trip < 1% WITHOUT Compton", rel_off < 0.01,
      f"rel={rel_off:.4%}")
check("well-conditioned round-trip < 1% WITH Compton", rel_on < 0.01,
      f"rel={rel_on:.4%}")
check("Compton adds no meaningful error (Δrel < 0.1%)", (rel_on - rel_off) < 0.001,
      f"Δrel={rel_on - rel_off:+.4%}  (Compton factor only amplifies quantizer error)")


# ── summary ────────────────────────────────────────────────────────────────
print(sep)
if failures:
    print(f"  RESULT: FAIL — {len(failures)} check(s) failed: {failures}")
    print(sep)
    sys.exit(1)
print("  RESULT: ALL CHECKS PASS")
print(sep)
