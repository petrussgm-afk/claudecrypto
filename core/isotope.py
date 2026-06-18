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

# Knižnica izotopov rozdelená podľa použiteľnosti pre šifrovanie.
#
# STABLE  — half_life ≥ 1 rok. Správy zostávajú dešifrovateľné neobmedzene,
#           izotopová vrstva pridáva fyzikálnu hlbku bez praktického časového
#           okna pre rozpad. Toto je default pre normálne správy.
#
# EPHEMERAL — krátko žijúce izotopy (µs až dni). Šifrovaná správa sa po
#             niekoľkých polčasoch stane nedešifrovateľnou — funkcia
#             "sebazničujúce správy", v duchu Hawkingovho žiarenia.

ISOTOPES_STABLE: dict[str, float] = {
    "Co-60":     1.66e8,        # 5.27 roka
    "Cs-137":    9.46e8,        # 30 rokov
    "Ra-226":    5.06e10,       # 1600 rokov
    "C-14":      1.81e11,       # 5730 rokov
    "U-235":     2.22e16,       # 704 miliónov rokov
    "U-238":     1.41e17,       # 4.5 miliardy rokov
    "Bi-209":    5.98e26,       # prakticky stabilný
}

ISOTOPES_EPHEMERAL: dict[str, float] = {
    "Po-214":    1.64e-4,       # 164 µs — takmer okamžitý zánik
    "Ra-224":    3.14e5,        # 3.6 dňa
    "I-131":     6.93e5,        # 8 dní
}

# Spojený slovník — zachované pre spätnú kompatibilitu so starým API
# (test_pipeline.py a podobne sa nemusia meniť).
ISOTOPES: dict[str, float] = {**ISOTOPES_EPHEMERAL, **ISOTOPES_STABLE}

ISOTOPE_NAMES           = list(ISOTOPES.keys())
ISOTOPE_NAMES_STABLE    = list(ISOTOPES_STABLE.keys())
ISOTOPE_NAMES_EPHEMERAL = list(ISOTOPES_EPHEMERAL.keys())

VALID_MODES = ("stable", "ephemeral")


class IsotopeExpiredError(ValueError):
    """Raised when reverse_decay can no longer recover the signal — the
    encoded character has effectively decayed beyond reconstruction.
    Subclass of ValueError so existing `except ValueError:` handlers still
    catch it, but the app layer can distinguish 'expired' from real errors.
    """
    def __init__(self, isotope: str, half_life: float, delta_t: float, n_halflives: float):
        self.isotope     = isotope
        self.half_life   = half_life
        self.delta_t     = delta_t
        self.n_halflives = n_halflives
        super().__init__(
            f"Izotop {isotope} sa rozpadol — informácia je nedešifrovateľná. "
            f"Prešlo {delta_t:.3g}s ({n_halflives:.1f}× polčas={half_life:.2e}s)."
        )


@dataclass
class IsotopeParams:
    isotope: str
    half_life: float    # polčas v sekundách
    lambda_decay: float # konštanta rozpadu [1/s]
    t_encrypt: float    # unix timestamp momentu šifrovania
    n0: float           # počiatočná "intenzita" (normalizovaná)


def assign_isotope(
    char_index: int,
    seed: int,
    t_encrypt: float = None,
    mode: str = "stable",
) -> IsotopeParams:
    """
    Každému znaku priradí deterministický izotop.

    mode      — "stable" (default) volí z dlho-žijúcich izotopov, správa
                zostáva dešifrovateľná neobmedzene.
              — "ephemeral" volí z krátko-žijúcich, správa sa po niekoľkých
                polčasoch znehodnotí — sebazničujúca správa.
    t_encrypt — unix timestamp momentu šifrovania (default = teraz).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"isotope mode must be one of {VALID_MODES}, got {mode!r}")

    pool_names = ISOTOPE_NAMES_STABLE if mode == "stable" else ISOTOPE_NAMES_EPHEMERAL
    pool_dict  = ISOTOPES_STABLE      if mode == "stable" else ISOTOPES_EPHEMERAL

    rng = np.random.default_rng(seed)
    isotope = rng.choice(pool_names)
    half_life = pool_dict[isotope]
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
        raise IsotopeExpiredError(
            isotope=params.isotope,
            half_life=params.half_life,
            delta_t=delta_t,
            n_halflives=(delta_t / params.half_life) if params.half_life > 0 else float('inf'),
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
