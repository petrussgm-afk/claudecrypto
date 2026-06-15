"""
FSC — Isotope / Decay Layer
Vrstva 3: nukleárny rozpad — časová dimenzia

N(t) = N₀ * exp(-lambda * t)
lambda = ln(2) / half_life

Každý znak dostane izotop s unikátnym polčasom rozpadu.
Čas šifrovania (t_encrypt) je súčasť kľúča.
Po uplynutí dostatočného počtu polčasov je správa nedešifrovateľná.
"""

import numpy as np
from dataclasses import dataclass
import time

# Knižnica izotopov: (polčas rozpadu v sekundách)
ISOTOPES: dict[str, float] = {
    "Po-214":    1.64e-4,       # 164 µs — takmer okamžitý zánik
    "Ra-224":    3.14e5,        # 3.6 dňa
    "I-131":     6.93e5,        # 8 dní
    "Co-60":     1.66e8,        # 5.27 roka
    "Cs-137":    9.46e8,        # 30 rokov
    "Ra-226":    5.06e10,       # 1600 rokov
    "C-14":      1.81e11,       # 5730 rokov
    "U-235":     2.22e16,       # 704 miliónov rokov
    "U-238":     1.41e17,       # 4.5 miliardy rokov
    "Bi-209":    5.98e26,       # prakticky stabilný
}

ISOTOPE_NAMES = list(ISOTOPES.keys())


@dataclass
class IsotopeParams:
    isotope: str
    half_life: float    # polčas v sekundách
    lambda_decay: float # konštanta rozpadu [1/s]
    t_encrypt: float    # unix timestamp momentu šifrovania
    n0: float           # počiatočná "intenzita" (normalizovaná)


def assign_isotope(char_index: int, seed: int, t_encrypt: float = None) -> IsotopeParams:
    """
    Každému znaku priradí deterministický izotop.
    t_encrypt: unix timestamp šifrovania (default = teraz)
    """
    rng = np.random.default_rng(seed)
    isotope = rng.choice(ISOTOPE_NAMES)
    half_life = ISOTOPES[isotope]
    lambda_decay = np.log(2) / half_life
    n0 = float(rng.uniform(0.8, 1.0))
    t = t_encrypt if t_encrypt is not None else time.time()

    return IsotopeParams(
        isotope=isotope,
        half_life=half_life,
        lambda_decay=lambda_decay,
        t_encrypt=t,
        n0=n0,
    )


def apply_decay(array: np.ndarray, params: IsotopeParams, t_decrypt: float = None) -> np.ndarray:
    """
    Aplikuje časový rozpad na matricu znaku.
    delta_t = čas od šifrovania [sekundy]

    decay_factor = exp(-lambda * delta_t)
    """
    t = t_decrypt if t_decrypt is not None else time.time()
    delta_t = t - params.t_encrypt
    decay_factor = params.n0 * np.exp(-params.lambda_decay * delta_t)
    return array * decay_factor


def reverse_decay(decayed: np.ndarray, params: IsotopeParams, t_decrypt: float = None) -> np.ndarray:
    """
    Inverzia časového rozpadu — dešifrovanie izotopovej vrstvy.
    Vyžaduje presný t_encrypt a t_decrypt z kľúča.
    """
    t = t_decrypt if t_decrypt is not None else time.time()
    delta_t = t - params.t_encrypt
    decay_factor = params.n0 * np.exp(-params.lambda_decay * delta_t)

    if decay_factor < 1e-15:
        raise ValueError(
            f"Izotop {params.isotope} sa rozpadol — informácia je nedešifrovateľná. "
            f"Prešlo {delta_t:.2f}s, polčas={params.half_life:.2e}s"
        )
    return decayed / decay_factor


def encrypt(geometry_stack: np.ndarray, isotope_params: list[IsotopeParams]) -> dict:
    """
    Vstup:  geometry_stack (n_chars, H, W), isotope_params pre každý znak
    Výstup: dict s rozpadnutou geometriou + timestamp šifrovania
    """
    t_encrypt = time.time()
    decayed = np.stack([
        apply_decay(geometry_stack[i], isotope_params[i], t_encrypt)
        for i in range(len(isotope_params))
    ])
    return {
        "decayed": decayed,
        "params": isotope_params,
        "t_encrypt": t_encrypt,
    }


def decrypt(isotope_output: dict, t_decrypt: float = None) -> np.ndarray:
    """
    Reverzia izotopovej vrstvy.
    t_decrypt: ak None, použije sa aktuálny čas.
    """
    params = isotope_output["params"]
    decayed = isotope_output["decayed"]
    t = t_decrypt if t_decrypt is not None else time.time()

    return np.stack([
        reverse_decay(decayed[i], params[i], t)
        for i in range(len(params))
    ])
