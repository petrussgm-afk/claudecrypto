"""
FSC — pipeline random-key stress test

Complements test_pipeline.py (which pins a well-conditioned key). Here we sweep
MANY random master keys and assert the Beer-Lambert over-absorption guard is a
*feature*, not a flake:

  - A key whose material draw saturates (some char has attenuation factor
    exp(-mu*thickness) < 1e-10, i.e. mu*thickness > ln(1e10)) MUST raise
    ValueError on decrypt — the message is intentionally unrecoverable.
  - A key that does NOT saturate MUST round-trip, with the algorithmic layers
    (OTP / Lorenz XOR) bit-exact.

The key sweep uses a FIXED RNG seed so this test is itself deterministic while
still exercising both branches.
"""
import sys
import numpy as np
sys.path.insert(0, '.')

try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

from keys.keygen import generate
from core.pipeline import encrypt, decrypt, roundtrip_error
from core import material

TEXT = "FSC"
GUARD_LN = np.log(1e10)          # mu*thickness above this → factor < 1e-10 → guard raises
N_KEYS = 80
sep = "-" * 66

print(sep)
print(f" FSC pipeline random-key stress test   ({N_KEYS} keys, text={TEXT!r})")
print(sep)

failures = []


def predicts_saturation(key):
    """True if any char's Beer-Lambert factor falls below the 1e-10 guard."""
    for i in range(len(TEXT)):
        mp = material.assign_material(i, key.chars[i].material_seed)
        if mp.mu * mp.thickness > GUARD_LN:
            return True
    return False


rng = np.random.default_rng(2025)   # fixed → deterministic sweep
n_saturating = 0
n_clean = 0

for _ in range(N_KEYS):
    mk = bytes(rng.integers(0, 256, 32, dtype=np.uint8).tolist())
    key = generate(TEXT, master_key=mk, canvas_size=48)
    predicted = predicts_saturation(key)

    enc = encrypt(TEXT, key)   # encrypt never raises on attenuation
    raised = False
    try:
        dec = decrypt(enc, t_decrypt=key.t_encrypt)
    except ValueError:
        raised = True

    if raised != predicted:
        failures.append(f"key {mk.hex()[:12]}… predicted_saturation={predicted} but raised={raised}")
        continue

    if predicted:
        n_saturating += 1
    else:
        n_clean += 1
        errs = roundtrip_error(enc, dec)
        if errs["after_otp"]["max"] > 1e-9 or errs["after_bh"]["max"] > 1e-9:
            failures.append(f"key {mk.hex()[:12]}… clean but algorithmic layers not exact")


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(name)


print(f"  swept {N_KEYS} keys → {n_clean} clean round-trips, {n_saturating} saturating (guard raised)")
print()
check("guard behaviour matches prediction for every key", len(failures) == 0)
check("observed at least one clean round-trip", n_clean > 0, f"{n_clean} clean")
check("observed at least one saturating key (guard exercised)", n_saturating > 0,
      f"{n_saturating} saturating")

print(sep)
if failures:
    print(f"  RESULT: FAIL — {failures[:3]}")
    print(sep)
    sys.exit(1)
print("  RESULT: ALL CHECKS PASS")
print(sep)
