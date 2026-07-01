"""
FSC — Key Generator
Generuje kompletný kľúč pre daný text.

Každý znak dostane vlastné seedy pre každú vrstvu.
Master key (256-bit) → SHAKE256 derivácia všetkých pod-seedov.
"""

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class CharKey:
    char: str
    renderer_seed: int
    material_seed: int
    isotope_seed: int
    fractal_seed: int


@dataclass
class FSCKey:
    text: str
    canvas_size: int
    planck_resolution: int
    lorenz_init: list       # [x0, y0, z0] pre blackhole vrstvu
    master_key: bytes       # 32 bytes (256-bit)
    t_encrypt: float        # unix timestamp
    chars: list             # list of CharKey
    otp_pad: bytes          # Layer 7 — One-Time Pad (n_chars × canvas² bytes)
    isotope_mode: str = "stable"   # "stable" or "ephemeral" (Layer 3 pool)
    compton_mode: bool = False     # Layer 2 — deterministic Compton scatter sub-layer
    ultrasound_mode: bool = False  # Layer 2 — ultrasound frequency-dependent attenuation sub-layer
    scramble_mode: bool = False    # Layer 6b — integer-reversible SPN scramble (diffusion, before Lorenz XOR)

    @property
    def key_hex(self) -> str:
        return self.master_key.hex()

    @property
    def master_seed(self) -> int:
        """Backward compat — first 4 bytes of master_key as int."""
        return int.from_bytes(self.master_key[:4], 'big')

    @property
    def otp_pad_kb(self) -> float:
        return len(self.otp_pad) / 1024


def _derive_seed(master_key: bytes, purpose: str, index: int) -> int:
    h = hashlib.shake_256(master_key + purpose.encode() + index.to_bytes(4, 'big'))
    return int.from_bytes(h.digest(4), 'big')


def generate(
    text: str,
    master_key: Optional[Union[bytes, int]] = None,
    canvas_size: int = 128,
    planck_resolution: int = 256,
    isotope_mode: str = "stable",
    compton_mode: bool = False,
    ultrasound_mode: bool = False,
    scramble_mode: bool = False,
    master_seed: Optional[int] = None,  # deprecated alias, kept for backward compat
) -> FSCKey:
    """
    Vygeneruje úplný kľúč pre dané vstupné slovo.

    master_key:      32 raw bytes, int (4-byte padded), or None → secrets.token_bytes(32).
    isotope_mode:    "stable"     — long-lived isotopes, message always decryptable
                     "ephemeral"  — short-lived, message self-destructs after a few halflives
    compton_mode:    True         — enable the deterministic Compton scatter sub-layer in
                                    Layer 2. Per-char photon energy (40–120 keV) and scatter
                                    angle are derived from each char's material_seed, so the
                                    refinement stays fully reversible.
    ultrasound_mode: True         — enable the ultrasound frequency-dependent attenuation
                                    sub-layer in Layer 2. Per-char frequency (2–15 MHz) is
                                    derived from each char's material_seed; α = a·f^b is
                                    deterministic, so the sub-layer is exactly invertible.
    scramble_mode:   True         — enable the optional integer-reversible SPN scramble
                                    (Layer 6b) that runs after the quantizer and before the
                                    Lorenz XOR. Adds global diffusion + defense-in-depth
                                    against OTP pad reuse; byte-exact, no expansion.
    """
    if isotope_mode not in ("stable", "ephemeral"):
        raise ValueError(f"isotope_mode must be 'stable' or 'ephemeral', got {isotope_mode!r}")
    # backward compat: master_seed kwarg maps to master_key
    if master_key is None and master_seed is not None:
        master_key = master_seed

    if master_key is None:
        master_key = secrets.token_bytes(32)
    elif isinstance(master_key, int):
        master_key = master_key.to_bytes(4, 'big').ljust(32, b'\x00')

    chars = [
        CharKey(
            char=char,
            renderer_seed=_derive_seed(master_key, 'renderer', i),
            material_seed=_derive_seed(master_key, 'material', i),
            isotope_seed=_derive_seed(master_key, 'isotope', i),
            fractal_seed=_derive_seed(master_key, 'fractal', i),
        )
        for i, char in enumerate(text)
    ]

    # Derive lorenz_init via SHAKE256 using integer scaling to avoid subnormal floats
    h = hashlib.shake_256(master_key + b'lorenz')
    raw = h.digest(24)
    x = int.from_bytes(raw[0:8],  'big') / 2**64 * 40.0 - 20.0   # [-20, 20]
    y = int.from_bytes(raw[8:16], 'big') / 2**64 * 40.0 - 20.0   # [-20, 20]
    z = int.from_bytes(raw[16:24],'big') / 2**64 * 50.0           # [0, 50]
    lorenz_init = [x, y, z]

    otp_pad = secrets.token_bytes(len(text) * canvas_size * canvas_size)

    return FSCKey(
        text=text,
        canvas_size=canvas_size,
        planck_resolution=planck_resolution,
        lorenz_init=lorenz_init,
        master_key=master_key,
        t_encrypt=time.time(),
        chars=chars,
        otp_pad=otp_pad,
        isotope_mode=isotope_mode,
        compton_mode=compton_mode,
        ultrasound_mode=ultrasound_mode,
        scramble_mode=scramble_mode,
    )
