"""
FSC — Scramble Layer (optional Layer 6b)

Integer-reversible keyed substitution-permutation network (SPN), inspired by the
*topology* of fast-scrambler / tensor-network models of black-hole information
dynamics. It is a CLASSICAL reversible circuit, NOT a quantum simulation:

- Permutation networks are the classical subset of unitary evolution; we
  implement the scrambling *structure* (substitute → diffuse → permute, repeated),
  not Haar-random unitaries and not entanglement.
- Layer 7 (One-Time Pad) already provides information-theoretic secrecy. This
  layer adds **global diffusion** and **defense-in-depth against OTP pad reuse**
  (it turns a two-time-pad leak `C₁⊕C₂ = S₁⊕S₂` into scrambled noise, defeating
  crib-dragging). It does NOT add confidentiality on the happy path.

Every operation is a bijection on bytes → exact inverse, no expansion, no float.
All round material derives from SHAKE256(master_key ∥ 'scramble' ∥ nonce ∥ label),
so the network is message-unique (nonce) and exactly reproducible at decrypt time.

Per round (forward):
    1. XOR round key        (confusion, per-byte)
    2. S-box substitution   (nonlinear bijection, per-byte)
    3. diffusion            (reversible cumulative mix — REQUIRED for avalanche;
                             steps 1/2 and the permutation never mix bytes together)
    4. byte permutation     (global relocation / diffusion)

Inverse per round (reverse order): inverse permutation → inverse diffusion →
inverse S-box → XOR round key. XOR and the mod-256 mixes are all self-consistent
integer inverses, so scramble→unscramble is byte-exact (error literally 0).
"""

import hashlib
import numpy as np

ROUNDS = 4  # default SPN rounds


# ── SHAKE-derived round material ───────────────────────────────────────────────

def _seed(master_key: bytes, nonce: bytes, label: str) -> bytes:
    """Domain-separated seed material for one (round, purpose)."""
    return hashlib.shake_256(master_key + b'scramble' + nonce + label.encode()).digest(64)


def build_sbox(shake_stream: bytes):
    """
    Build a keyed 256-entry S-box (a bijection on bytes) by Fisher-Yates shuffle
    of [0..255] driven by SHAKE bytes. Returns (sbox, inv_sbox) as uint8 arrays.
    """
    rnd = hashlib.shake_256(shake_stream + b'sbox').digest(512)  # 2 bytes / swap
    sbox = list(range(256))
    for i in range(255, 0, -1):
        j = ((rnd[2 * (255 - i)] << 8) | rnd[2 * (255 - i) + 1]) % (i + 1)
        sbox[i], sbox[j] = sbox[j], sbox[i]
    sbox = np.array(sbox, dtype=np.uint8)
    inv = np.empty(256, dtype=np.uint8)
    inv[sbox] = np.arange(256, dtype=np.uint8)
    return sbox, inv


def build_permutation(shake_stream: bytes, size: int):
    """
    Build a keyed permutation of `size` indices by argsort of SHAKE-derived 64-bit
    sort keys (a vectorised Fisher-Yates equivalent). Returns (perm, inv_perm).
    inv_perm = argsort(perm) so that x[perm][inv_perm] == x.
    """
    raw = hashlib.shake_256(shake_stream + b'perm').digest(size * 8)
    keys = np.frombuffer(raw, dtype=np.uint64)
    perm = np.argsort(keys, kind='stable').astype(np.int64)
    inv_perm = np.argsort(perm, kind='stable').astype(np.int64)
    return perm, inv_perm


def round_key(shake_stream: bytes, size: int) -> np.ndarray:
    """Raw SHAKE keystream of `size` bytes for the per-round XOR (uint8 array)."""
    return np.frombuffer(hashlib.shake_256(shake_stream + b'rkey').digest(size), dtype=np.uint8)


# ── Reversible diffusion (mod-256 cumulative mix) ──────────────────────────────
# XOR, S-box and the permutation never combine two bytes, so on their own the
# network has NO avalanche (one flipped input byte → one flipped output byte).
# A prefix-sum mod 256 makes every output byte depend on all earlier bytes; a
# prefix-difference inverts it exactly. Both are O(M) vectorised. Combined with
# the permutation across rounds this yields full ~50%-bit avalanche.

def _diffuse_forward(x: np.ndarray) -> np.ndarray:
    return (np.cumsum(x.astype(np.int64)) % 256).astype(np.uint8)


def _diffuse_inverse(y: np.ndarray) -> np.ndarray:
    y64 = y.astype(np.int64)
    x = np.empty_like(y64)
    x[0] = y64[0]
    x[1:] = (y64[1:] - y64[:-1]) % 256
    return x.astype(np.uint8)


# ── Public API ─────────────────────────────────────────────────────────────────

def scramble(byte_array: np.ndarray, master_key: bytes, nonce: bytes,
             rounds: int = ROUNDS) -> np.ndarray:
    """
    Forward SPN scramble. Returns an array of the SAME shape and dtype as the
    input, with globally diffused byte values. Byte-exact reversible via
    unscramble() with the same (master_key, nonce, rounds).
    """
    shape, dtype = byte_array.shape, byte_array.dtype
    x = byte_array.ravel().astype(np.uint8).copy()
    M = x.size
    for r in range(rounds):
        rk = round_key(_seed(master_key, nonce, f'r{r}'), M)
        sbox, _ = build_sbox(_seed(master_key, nonce, f'r{r}'))
        perm, _ = build_permutation(_seed(master_key, nonce, f'r{r}'), M)
        x = x ^ rk                 # 1. confusion
        x = sbox[x]                # 2. nonlinear substitution
        x = _diffuse_forward(x)    # 3. diffusion (mix)
        x = x[perm]                # 4. global permutation
    return x.reshape(shape).astype(dtype)


def unscramble(scrambled: np.ndarray, master_key: bytes, nonce: bytes,
               rounds: int = ROUNDS) -> np.ndarray:
    """
    Exact inverse of scramble(). Regenerates the identical SHAKE-derived
    S-boxes / permutations / round keys and undoes each round in reverse.
    """
    shape, dtype = scrambled.shape, scrambled.dtype
    x = scrambled.ravel().astype(np.uint8).copy()
    M = x.size
    for r in reversed(range(rounds)):
        rk = round_key(_seed(master_key, nonce, f'r{r}'), M)
        _, inv_sbox = build_sbox(_seed(master_key, nonce, f'r{r}'))
        _, inv_perm = build_permutation(_seed(master_key, nonce, f'r{r}'), M)
        x = x[inv_perm]            # undo 4. permutation
        x = _diffuse_inverse(x)    # undo 3. diffusion
        x = inv_sbox[x]            # undo 2. substitution
        x = x ^ rk                 # undo 1. confusion (XOR self-inverse)
    return x.reshape(shape).astype(dtype)
