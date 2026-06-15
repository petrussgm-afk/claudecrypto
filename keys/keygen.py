"""
FSC — Key Generator
Generuje kompletný kľúč pre daný text.

Každý znak dostane vlastné seedy pre každú vrstvu.
Master seed → deterministická derivácia všetkých pod-seedov.
"""

import numpy as np
import time
from dataclasses import dataclass, asdict
from typing import Optional


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
    master_seed: int
    t_encrypt: float        # unix timestamp
    chars: list             # list of CharKey


def generate(
    text: str,
    master_seed: Optional[int] = None,
    canvas_size: int = 128,
    planck_resolution: int = 256,
) -> FSCKey:
    """
    Vygeneruje úplný kľúč pre dané vstupné slovo.

    master_seed → numpy RNG → deterministické seedy pre každý znak a vrstvu.
    Ak master_seed nie je zadaný, použije sa náhodný.
    """
    if master_seed is None:
        master_seed = int(np.random.default_rng().integers(0, 2**32))

    rng = np.random.default_rng(master_seed)

    chars = []
    for char in text:
        chars.append(CharKey(
            char=char,
            renderer_seed=int(rng.integers(0, 2**32)),
            material_seed=int(rng.integers(0, 2**32)),
            isotope_seed=int(rng.integers(0, 2**32)),
            fractal_seed=int(rng.integers(0, 2**32)),
        ))

    lorenz_init = rng.uniform(-0.5, 0.5, size=3).tolist()

    return FSCKey(
        text=text,
        canvas_size=canvas_size,
        planck_resolution=planck_resolution,
        lorenz_init=lorenz_init,
        master_seed=master_seed,
        t_encrypt=time.time(),
        chars=chars,
    )
