"""
FSC — Material Layer
Vrstva 2: materiálová fyzika — röntgenový útlm

Beer-Lambert zákon: I_out = I_0 * exp(-mu * thickness)

mu = lineárny koeficient útlmu [1/cm]
     závisí od materiálu A zdroja žiarenia (energia v keV)

Hodnoty mu sú zjednodušené (pre 80 keV röntgen).
Presné hodnoty: NIST XCOM databáza (https://physics.nist.gov/PhysRefData/Xcom/)
"""

import numpy as np
from dataclasses import dataclass

from core import compton as _compton
from core import ultrasound as _ultrasound

# Zjednodušená tabuľka koeficientov útlmu pri 80 keV [1/cm]
# Zdroj: NIST XCOM (aproximácia)
MATERIAL_MU: dict[str, float] = {
    "water":     0.184,
    "bone":      0.480,
    "aluminum":  0.620,
    "iron":      3.441,
    "lead":     10.200,
    "glass":     0.330,
    "rubber":    0.162,
    "wood":      0.120,
    "tissue":    0.190,
    "air":       0.000,
}

# Efektívne atómové čísla Z (aproximácia) — vyššie Z → silnejší Comptonov rozptyl.
MATERIAL_Z: dict[str, float] = {
    "water":     7.42,
    "bone":     13.8,
    "aluminum": 13.0,
    "iron":     26.0,
    "lead":     82.0,
    "glass":    11.0,
    "rubber":    6.0,
    "wood":      7.0,
    "tissue":    7.4,
    "air":       7.6,
}

# Mapovanie röntgenových materiálov na akustický materiál (pre ultrazvuk).
# Akustická tabuľka nemá "air" — mapujeme na water ako neutrálny fallback.
MATERIAL_ACOUSTIC: dict[str, str] = {
    "water":     "water",
    "bone":      "bone",
    "aluminum":  "metal",
    "iron":      "metal",
    "lead":      "metal",
    "glass":     "metal",
    "rubber":    "fat",
    "wood":      "muscle",
    "tissue":    "tissue",
    "air":       "water",
}

MATERIALS = list(MATERIAL_MU.keys())


@dataclass
class MaterialParams:
    material: str
    thickness: float                 # hrúbka vrstvy v cm
    mu: float                        # koeficient útlmu [1/cm]
    Z: float = 0.0                   # efektívne atómové číslo (pre Compton)
    seed: int = 0                    # material seed (zdroj Comptonovho uhla)
    compton_enabled: bool = False    # zapnutá Comptonova pod-vrstva?
    photon_energy_keV: float = 80.0  # energia fotónu [keV]
    scatter_angle: float = 0.0       # deterministický Comptonov uhol θ [rad]
    scatter_factor: float = 1.0      # výsledný Comptonov faktor ∈ (0, 1]
    ultrasound_enabled: bool = False # zapnutá ultrazvuková pod-vrstva?
    acoustic_material: str = "water" # akustický materiál (z ACOUSTIC tabuľky)
    us_freq_mhz: float = 5.0         # frekvencia ultrazvuku [MHz]
    us_factor: float = 1.0           # ultrazvukový útlmový faktor ∈ (0, 1]


def assign_material(char_index: int, seed: int, compton: bool = False,
                    ultrasound: bool = False) -> MaterialParams:
    """
    Každému znaku (indexu) priradí deterministický materiál a hrúbku.

    compton=True zapne Comptonovu pod-vrstvu: odvodí per-znak energiu fotónu
    (40–120 keV) a deterministický rozptylový uhol θ z toho istého material_seedu,
    a predpočíta škálovací faktor pre exaktnú reverziu.

    ultrasound=True zapne ultrazvukovú pod-vrstvu: odvodí per-znak frekvenciu
    (2–15 MHz) z material_seedu a predpočíta útlmový faktor exp(−2·α·x),
    α = a·f^b (deterministický, exaktne reverzibilný).
    """
    rng = np.random.default_rng(seed)
    material = rng.choice(MATERIALS)
    thickness = float(rng.uniform(0.5, 5.0))
    mu = MATERIAL_MU[material]
    Z = MATERIAL_Z[material]

    params = MaterialParams(
        material=material, thickness=thickness, mu=mu,
        Z=Z, seed=int(seed), compton_enabled=bool(compton),
        ultrasound_enabled=bool(ultrasound),
        acoustic_material=MATERIAL_ACOUSTIC[material],
    )

    if compton:
        # per-znak energia fotónu z material_seedu (nezasahuje do výberu materiálu/hrúbky)
        params.photon_energy_keV = float(rng.uniform(40.0, 120.0))
        theta = _compton.derive_scatter_angle(seed, params.photon_energy_keV)
        params.scatter_angle = theta
        params.scatter_factor = _compton.scatter_factor(Z, params.photon_energy_keV, theta)

    if ultrasound:
        # per-znak frekvencia z material_seedu (po Comptone, aby draw-poradie ostalo stabilné)
        params.us_freq_mhz = float(rng.uniform(2.0, 15.0))
        params.us_factor = _ultrasound.ultrasound_factor(
            params.acoustic_material, params.us_freq_mhz, thickness, seed
        )

    return params


def apply_attenuation(geometry: np.ndarray, params: MaterialParams) -> np.ndarray:
    """
    Aplikuje Beer-Lambertov útlm na geometrickú maticu znaku.

    I_out = I_in * exp(-mu * thickness)

    Poradie pri zapnutých pod-vrstvách:
        Beer-Lambert → Compton → ultrazvuk

    geometry: 2D array [H x W], hodnoty 0.0–1.0 (intenzita vstupného žiarenia)
    Výstup:   2D array rovnakého tvaru — zmenšená intenzita po prechode materiálom
    """
    attenuation_factor = np.exp(-params.mu * params.thickness)
    out = geometry * attenuation_factor
    if params.compton_enabled:
        out, _theta, _factor = _compton.apply_compton(
            out, params.Z, params.photon_energy_keV, params.seed
        )
    if params.ultrasound_enabled:
        out, _us = _ultrasound.apply_ultrasound(
            out, params.acoustic_material, params.us_freq_mhz, params.thickness, params.seed
        )
    return out


def reverse_attenuation(attenuated: np.ndarray, params: MaterialParams) -> np.ndarray:
    """
    Inverzná materiálová transformácia — dešifrovanie.

    Poradie je presne opačné voči apply_attenuation:
      1. reverzia ultrazvuku (delenie us_factor)     — ak bol zapnutý
      2. reverzia Comptonu (delenie scatter_factor)  — ak bol zapnutý
      3. inverzný Beer-Lambert:  I_in = I_out / exp(-mu * thickness)
    """
    if params.ultrasound_enabled:
        attenuated = _ultrasound.reverse_ultrasound(attenuated, params.us_factor)

    if params.compton_enabled:
        attenuated = _compton.reverse_compton(attenuated, params.scatter_factor)

    attenuation_factor = np.exp(-params.mu * params.thickness)
    if attenuation_factor < 1e-10:
        raise ValueError(f"Materiál {params.material} s hrúbkou {params.thickness} cm absorbuje príliš veľa — reverzia nemožná")
    return attenuated / attenuation_factor


def encrypt(geometry_stack: np.ndarray, material_params: list[MaterialParams]) -> dict:
    """
    Vstup:  geometry_stack (n_chars, H, W), material_params pre každý znak
    Výstup: dict s atenuovanou geometriou
    """
    attenuated = np.stack([
        apply_attenuation(geometry_stack[i], material_params[i])
        for i in range(len(material_params))
    ])
    return {
        "attenuated": attenuated,
        "params": material_params,
    }


def decrypt(material_output: dict) -> np.ndarray:
    """
    Reverzia materiálovej vrstvy — vyžaduje material_params z kľúča.
    """
    params = material_output["params"]
    attenuated = material_output["attenuated"]
    return np.stack([
        reverse_attenuation(attenuated[i], params[i])
        for i in range(len(params))
    ])
