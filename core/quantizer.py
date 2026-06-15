"""
FSC — Quantizer Layer
Vrstva 5: Planck-pixelizácia — diskretizácia na minimálnu jednotku

Analógia: v kvantovej mechanike energia prichádza v diskrétnych kvantách E = n·hν.
Tu pixel hodnoty prichádzajú v diskrétnych "Planckových jednotkách" — krok = (vmax−vmin)/N.

Šifrovanie:  float [vmin, vmax] → integer [0, N−1]  (kvantizácia)
Dešifrovanie: integer [0, N−1] → float (stredy intervalov → deterministická reverzia)
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class QuantizerParams:
    n_levels: int    # planck_resolution — počet diskrétnych úrovní
    vmin: float      # minimum dát (potrebné pre reverziu)
    vmax: float      # maximum dát (potrebné pre reverziu)

    @property
    def step(self) -> float:
        """Veľkosť jedného kvantizačného kroku (Planckova jednotka)."""
        span = self.vmax - self.vmin
        return span / self.n_levels if span > 0 else 1.0


def quantize(array: np.ndarray, params: QuantizerParams) -> np.ndarray:
    """
    Mapuje spojité hodnoty na diskrétne úrovne.
    Výstup: uint16 array rovnakého tvaru, hodnoty v [0, n_levels−1].
    """
    span = params.vmax - params.vmin
    if span < 1e-12:
        return np.zeros(array.shape, dtype=np.uint16)
    normalized = (array - params.vmin) / span
    levels = np.floor(normalized * params.n_levels).astype(np.int32)
    return np.clip(levels, 0, params.n_levels - 1).astype(np.uint16)


def dequantize(levels: np.ndarray, params: QuantizerParams) -> np.ndarray:
    """
    Mapuje diskrétne úrovne naspäť na spojité hodnoty (stredy intervalov).
    Presnosť reverzie: ±step/2 = ±(vmax−vmin)/(2·n_levels)
    """
    span = params.vmax - params.vmin
    bin_centers = (levels.astype(np.float32) + 0.5) / params.n_levels
    return (bin_centers * span + params.vmin).astype(np.float32)


def encrypt(geometry_stack: np.ndarray, n_levels: int = 256) -> dict:
    """
    Vstup:  geometry_stack (n_chars, H, W) — float array
    Výstup: dict s kvantizovanými dátami (uint16) a parametrami pre reverziu

    Rozsah vmin/vmax sa počíta globálne cez všetky znaky naraz,
    aby sa zachovalo relatívne porovnanie hodnôt medzi znakmi.
    """
    vmin = float(geometry_stack.min())
    vmax = float(geometry_stack.max())

    # zabezpeč nenulový rozsah
    if vmax - vmin < 1e-12:
        vmax = vmin + 1.0

    params = QuantizerParams(n_levels=n_levels, vmin=vmin, vmax=vmax)
    quantized = quantize(geometry_stack, params)

    return {
        "quantized": quantized,   # uint16 array (n_chars, H, W)
        "params": params,
    }


def decrypt(quantizer_output: dict) -> np.ndarray:
    """
    Reverzia kvantizačnej vrstvy.
    Vyžaduje params z kľúča (vmin, vmax, n_levels).
    Vracia float32 array — hodnoty sú stredy kvantizačných intervalov.
    """
    return dequantize(quantizer_output["quantized"], quantizer_output["params"])
