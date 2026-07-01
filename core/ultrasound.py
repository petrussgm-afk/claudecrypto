"""
FSC — Ultrasound frequency-dependent attenuation (material sub-layer)

A third physical modality for Layer 2, alongside Beer-Lambert (X-ray) and the
optional Compton scatter. Ultrasound attenuation grows with frequency:

    α(f) = a · f^b        [dB/cm],  a in dB/cm/MHz^b,  b typically 1.0–2.0

Intensity after thickness x, pulse-echo (factor 2 for the round trip there and
back):

    I(f, x) = I₀(f) · e^(−2·α(f)·x)

Higher frequencies attenuate more strongly, so the returned echo spectrum shifts
"redder" (loses its high-frequency content).

Honest FSC framing:
- Unlike Compton, this modality is DETERMINISTIC by nature — no Monte-Carlo dice
  roll — so exact reversibility is straightforward: it is a single multiply by a
  scalar us_factor ∈ (0, 1], inverted by a single divide.
- Real pulse-echo ultrasound also has reflection / reverberation / mode
  conversion at interfaces which we do NOT fully simulate. We provide the
  acoustic-impedance / reflection helpers for reference, but the encryption path
  uses only the *attenuation envelope*, which stays exactly invertible.
- Same FSC principle as the other layers: a real physics equation adapted so the
  cipher can undo it exactly.
"""

import numpy as np

# Simplified acoustic material table.
#   rho : density            [kg/m³]
#   c   : speed of sound     [m/s]
#   a   : attenuation coeff  [dB/cm/MHz^b]
#   b   : frequency exponent [-]
ACOUSTIC: dict[str, dict[str, float]] = {
    "water":  {"rho": 1000, "c": 1480, "a": 0.002, "b": 2.0},
    "tissue": {"rho": 1050, "c": 1540, "a": 0.5,   "b": 1.1},
    "bone":   {"rho": 1900, "c": 4080, "a": 20.0,  "b": 1.0},
    "fat":    {"rho": 950,  "c": 1450, "a": 0.6,   "b": 1.0},
    "muscle": {"rho": 1060, "c": 1580, "a": 1.0,   "b": 1.1},
    "metal":  {"rho": 7800, "c": 5900, "a": 0.02,  "b": 1.5},
}


def attenuation_coeff(a: float, b: float, freq_mhz: float) -> float:
    """Frequency-dependent attenuation coefficient α(f) = a · f^b  [dB/cm]."""
    return a * freq_mhz ** b


def acoustic_impedance(rho: float, c: float) -> float:
    """Acoustic impedance Z = ρ · c  [kg/m²/s = Rayl]."""
    return rho * c


def reflection_coeff(Z1: float, Z2: float) -> float:
    """
    Intensity reflection coefficient at a Z1→Z2 interface:

        R = ((Z2 − Z1) / (Z2 + Z1))²   ∈ [0, 1]
    """
    return ((Z2 - Z1) / (Z2 + Z1)) ** 2


def ultrasound_factor(material_name: str, freq_mhz: float, thickness_cm: float,
                      seed=None) -> float:
    """
    Deterministic pulse-echo attenuation factor for one material slab.

        α       = a · f^b
        factor  = exp(−2 · α · thickness)   ∈ (0, 1]

    `seed` (optional) applies a small ±5% deterministic per-char frequency
    micro-variation so identical materials on different characters differ
    slightly; it is folded into the returned factor and needs nothing at reverse
    time. Same (material, freq, thickness, seed) → same factor.
    """
    props = ACOUSTIC[material_name]
    f = freq_mhz
    if seed is not None:
        # deterministic ±5% frequency jitter, independent of Compton's use of seed
        jitter = (np.random.default_rng(int(seed) ^ 0x5D).random() - 0.5) * 0.1
        f = max(0.1, freq_mhz * (1.0 + jitter))
    alpha = attenuation_coeff(props["a"], props["b"], f)
    return float(np.exp(-2.0 * alpha * thickness_cm))


def apply_ultrasound(intensity_array, material_name: str, freq_mhz: float,
                     thickness_cm: float, seed=None):
    """
    Apply frequency-dependent ultrasound attenuation to an intensity array.

    Returns (attenuated_array, us_factor) where us_factor ∈ (0, 1] is exactly
    what reverse_ultrasound needs to invert this operation.
    """
    us_factor = ultrasound_factor(material_name, freq_mhz, thickness_cm, seed)
    return intensity_array * us_factor, us_factor


def reverse_ultrasound(attenuated_array, us_factor: float):
    """
    Exact inverse of apply_ultrasound: divide out the attenuation factor.

        intensity = attenuated / us_factor
    """
    if us_factor <= 1e-12:
        raise ValueError(
            f"us_factor {us_factor:.3e} too small — ultrasound attenuation "
            f"saturated (high a·f^b·thickness), reversal not possible"
        )
    return attenuated_array / us_factor
