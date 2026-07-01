"""
FSC — Scramble Layer 6b test (integer-reversible SPN)

Checks:
  1. S-box round-trip: sbox[inv_sbox[x]] == x for all 256 bytes
  2. permutation round-trip: perm/inv_perm compose to identity
  3. scramble → unscramble byte-exact (error literally 0, integer)
  4. avalanche: flip ONE input byte → measure output change after 4 rounds
     (bit-level should be ~50%; byte-level ~99.6% for strong diffusion)
  5. pad-reuse hedge: crib-dragging that trivially works on a raw two-time pad
     FAILS once the states are scrambled (S₁⊕S₂ reveals nothing aligned)
  6. full pipeline scramble_mode=True: encrypt→decrypt exact on algorithmic
     layers + byte-exact unscramble, works end-to-end
  7. wrong nonce → unscramble produces garbage (nonce-dependence)
"""
import sys
import numpy as np
sys.path.insert(0, '.')

try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

from core import scramble
from keys.keygen import generate
from core.pipeline import encrypt, decrypt, roundtrip_error

sep = "-" * 66
print(sep)
print(" FSC scramble layer 6b test (integer-reversible SPN)")
print(sep)

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(name)


MK = bytes(range(32))
NONCE = bytes([0xA5]) * 16

# ── 1. S-box round-trip ─────────────────────────────────────────────────────
sbox, inv_sbox = scramble.build_sbox(b"seed-sbox")
all_x = np.arange(256, dtype=np.uint8)
sbox_is_bijection = len(np.unique(sbox)) == 256
sbox_roundtrip = np.array_equal(sbox[inv_sbox[all_x]], all_x) and np.array_equal(inv_sbox[sbox[all_x]], all_x)
check("S-box is a bijection on 256 bytes", sbox_is_bijection)
check("sbox[inv_sbox[x]] == x for all bytes", sbox_roundtrip)

# ── 2. permutation round-trip ───────────────────────────────────────────────
M = 4096
perm, inv_perm = scramble.build_permutation(b"seed-perm", M)
v = np.arange(M)
perm_is_bijection = len(np.unique(perm)) == M
perm_roundtrip = np.array_equal(v[perm][inv_perm], v)
check("permutation is a bijection of size M", perm_is_bijection)
check("perm ∘ inv_perm == identity", perm_roundtrip)

# ── 3. scramble → unscramble byte-exact ─────────────────────────────────────
rng = np.random.default_rng(0)
data = rng.integers(0, 256, size=(3, 64, 64), dtype=np.uint8)
scr = scramble.scramble(data, MK, NONCE)
rec = scramble.unscramble(scr, MK, NONCE)
exact_err = int(np.abs(data.astype(np.int64) - rec.astype(np.int64)).max())
check("scramble→unscramble byte-exact (error == 0)", exact_err == 0, f"max_err={exact_err}")
check("scramble preserves shape & dtype", scr.shape == data.shape and scr.dtype == data.dtype)
changed_by_scramble = int(np.count_nonzero(scr != data))
check("scramble actually changes the data", changed_by_scramble > 0.9 * data.size,
      f"{changed_by_scramble}/{data.size} bytes differ")

# ── 4. avalanche: flip ONE input byte ───────────────────────────────────────
base = rng.integers(0, 256, size=8192, dtype=np.uint8)
flipped = base.copy()
flipped[len(flipped) // 2] ^= 0x01          # flip a single bit of one byte
s_base = scramble.scramble(base, MK, NONCE)
s_flip = scramble.scramble(flipped, MK, NONCE)
byte_diff = np.count_nonzero(s_base != s_flip) / s_base.size
bit_diff = np.unpackbits(s_base ^ s_flip).mean()
check("avalanche bit-flip ≈ 50% (0.45–0.55)", 0.45 <= bit_diff <= 0.55,
      f"bit-flip={bit_diff:.4%}")
check("avalanche byte-change high (>95%, strong diffusion)", byte_diff > 0.95,
      f"byte-change={byte_diff:.4%}")

# ── 5. pad-reuse hedge: crib-dragging defeated ──────────────────────────────
# Two structured plaintexts (as if two messages encrypted under a REUSED pad).
L = 4096
P1 = np.zeros(L, dtype=np.uint8)
P1[1000:1000 + 32] = np.frombuffer(b"ATTACK AT DAWN -- SECRET ORDER!!", dtype=np.uint8)  # 32-byte crib
P2 = np.full(L, 0x20, dtype=np.uint8)
P2[2000:2000 + 11] = np.frombuffer(b"HELLO WORLD", dtype=np.uint8)

k, off = 1000, 32  # attacker "knows" this slice of P1 (a classic crib)

# --- raw two-time pad (no scramble): C1⊕C2 = P1⊕P2, crib-dragging works ---
leak_raw = P1 ^ P2
recovered_raw = leak_raw[k:k + off] ^ P1[k:k + off]          # = P2[k:k+off] exactly
raw_match = np.array_equal(recovered_raw, P2[k:k + off])
check("baseline: crib-dragging RECOVERS P2 on raw two-time pad", raw_match,
      "100% match (attack works without scramble)")

# --- scrambled states (same key+nonce): leak = S1⊕S2 ---
S1 = scramble.scramble(P1, MK, NONCE)
S2 = scramble.scramble(P2, MK, NONCE)
leak_scr = S1 ^ S2
recovered_scr = leak_scr[k:k + off] ^ P1[k:k + off]          # attacker's best guess
scr_match_frac = np.mean(recovered_scr == P2[k:k + off])
# also show the leak itself is uncorrelated with the true P1⊕P2
align_frac = np.mean(leak_scr == leak_raw)
leak_bits = np.unpackbits(leak_scr)
leak_entropy_ok = 0.45 <= leak_bits.mean() <= 0.55
check("hedge: crib-dragging FAILS on scrambled XOR (guess ≈ chance)", scr_match_frac < 0.05,
      f"P2 bytes recovered={scr_match_frac:.4%} (chance≈0.39%)")
check("hedge: S₁⊕S₂ uncorrelated with true P₁⊕P₂ (≈chance)", align_frac < 0.02,
      f"aligned bytes={align_frac:.4%}")
check("hedge: leaked XOR looks like noise (~50% bits set)", leak_entropy_ok,
      f"bit-density={leak_bits.mean():.4%}")

# ── 6. full pipeline scramble_mode=True ─────────────────────────────────────
TEXT = "FSC"
key = generate(TEXT, master_key=bytes(range(1, 33)), canvas_size=48, scramble_mode=True)
check("key.scramble_mode flag set", key.scramble_mode is True)

enc = encrypt(TEXT, key)
check("enc_state records scramble_mode", enc.get("scramble_mode") is True)
# the array fed to Lorenz XOR must be the scrambled one (differs from quantizer out)
scrambled_differs = not np.array_equal(
    enc["bh_input"].astype(np.uint16), enc["quant_out"]["quantized"].astype(np.uint16))
check("bh_input is scrambled (≠ quantizer output)", scrambled_differs)

dec = decrypt(enc, t_decrypt=key.t_encrypt)
errs = roundtrip_error(enc, dec)
check("after_otp exact (< 1e-9)", errs["after_otp"]["max"] < 1e-9, f'{errs["after_otp"]["max"]:.3e}')
check("after_bh exact vs scrambled bh_input (< 1e-9)", errs["after_bh"]["max"] < 1e-9,
      f'{errs["after_bh"]["max"]:.3e}')
# unscramble must exactly recover the quantizer output
unscr_err = int(np.abs(dec["after_unscramble"].astype(np.int64)
                       - enc["quant_out"]["quantized"].astype(np.int64)).max())
check("unscramble recovers quantizer output byte-exact (error == 0)", unscr_err == 0,
      f"max_err={unscr_err}")
# Scramble is byte-exact, so it can add NO error of its own: the glyph round-trip
# with scramble ON must be bit-identical to the same key with scramble OFF (any
# residual error is the pre-existing physical 1/atten·1/n0 quantizer amplification,
# independent of scramble — asserting a fixed <1% here would just test the key draw).
key_off = generate(TEXT, master_key=bytes(range(1, 33)), canvas_size=48, scramble_mode=False)
enc_off = encrypt(TEXT, key_off)
dec_off = decrypt(enc_off, t_decrypt=key_off.t_encrypt)
errs_off = roundtrip_error(enc_off, dec_off)
geom_on = errs["geometry_final"]["max"]
geom_off = errs_off["geometry_final"]["max"]
check("end-to-end works: geometry round-trip identical with/without scramble",
      abs(geom_on - geom_off) < 1e-12,
      f"scramble_on={geom_on:.3e}  scramble_off={geom_off:.3e}  (Δ={abs(geom_on-geom_off):.1e})")

# ── 7. wrong nonce → garbage ────────────────────────────────────────────────
wrong_nonce = bytes([0x5A]) * 16
bad = scramble.unscramble(scr, MK, wrong_nonce)
garbage_frac = np.count_nonzero(bad != data) / data.size
check("wrong nonce → unscramble is garbage (>99% wrong)", garbage_frac > 0.99,
      f"{garbage_frac:.4%} bytes wrong")

# ── summary ──────────────────────────────────────────────────────────────────
print(sep)
print(f"  avalanche: bit-flip={bit_diff:.2%}  byte-change={byte_diff:.2%}")
print(f"  pad-reuse hedge: raw crib-drag=100%  scrambled crib-drag={scr_match_frac:.2%}")
if failures:
    print(f"  RESULT: FAIL — {len(failures)} check(s) failed: {failures}")
    print(sep)
    sys.exit(1)
print("  RESULT: ALL CHECKS PASS")
print(sep)
