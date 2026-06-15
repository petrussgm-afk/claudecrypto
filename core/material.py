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

MATERIALS = list(MATERIAL_MU.keys())


@dataclass
class MaterialParams:
    material: str
    thickness: float    # hrúbka vrstvy v cm
    mu: float           # koeficient útlmu [1/cm]


def assign_material(char_index: int, seed: int) -> MaterialParams:
    """
    Každému znaku (indexu) priradí deterministický materiál a hrúbku.
    """
    rng = np.random.default_rng(seed)
    material = rng.choice(MATERIALS)
    thickness = float(rng.uniform(0.5, 5.0))
    mu = MATERIAL_MU[material]
    return MaterialParams(material=material, thickness=thickness, mu=mu)


def apply_attenuation(geometry: np.ndarray, params: MaterialParams) -> np.ndarray:
    """
    Aplikuje Beer-Lambertov útlm na geometrickú maticu znaku.

    I_out = I_in * exp(-mu * thickness)

    geometry: 2D array [H x W], hodnoty 0.0–1.0 (intenzita vstupného žiarenia)
    Výstup:   2D array rovnakého tvaru — zmenšená intenzita po prechode materiálom
    """
    attenuation_factor = np.exp(-params.mu * params.thickness)
    return geometry * attenuation_factor


def reverse_attenuation(attenuated: np.ndarray, params: MaterialParams) -> np.ndarray:
    """
    Inverzná Beer-Lambert transformácia — dešifrovanie materiálovej vrstvy.
    I_in = I_out / exp(-mu * thickness)
    """
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
