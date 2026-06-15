"""
FSC — Renderer
Vrstva 1: text → vektorová geometria

Každý znak dostane unikátne parametre:
- font, veľkosť, farba (RGB), priehľadnosť (alpha)
Tieto parametre sú súčasťou kľúča.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from dataclasses import dataclass
from typing import Tuple


@dataclass
class CharRenderParams:
    char: str
    font_name: str
    size: int
    color: Tuple[int, int, int]
    alpha: float  # 0.0 – 1.0


def generate_render_params(char: str, seed: int) -> CharRenderParams:
    """
    Pre daný znak vygeneruje deterministické, pseudonáhodné parametre
    renderovania na základe seed hodnoty (súčasť kľúča).
    """
    rng = np.random.default_rng(seed)

    fonts = ["DejaVuSans", "DejaVuSerif", "DejaVuSansMono"]
    font_name = rng.choice(fonts)
    size = int(rng.integers(28, 72))
    color = tuple(rng.integers(0, 255, size=3).tolist())
    alpha = float(rng.uniform(0.4, 1.0))

    return CharRenderParams(
        char=char,
        font_name=font_name,
        size=size,
        color=color,
        alpha=alpha,
    )


def render_char(params: CharRenderParams, canvas_size: int = 128) -> np.ndarray:
    """
    Vykreslí jeden znak ako numpy array (grayscale float32).
    Výstup: 2D array [canvas_size x canvas_size], hodnoty 0.0–1.0
    """
    img = Image.new("L", (canvas_size, canvas_size), color=0)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(f"{params.font_name}.ttf", params.size)
    except (IOError, OSError):
        font = ImageFont.load_default()

    brightness = int(
        0.299 * params.color[0] + 0.587 * params.color[1] + 0.114 * params.color[2]
    )
    effective_brightness = int(brightness * params.alpha)

    bbox = draw.textbbox((0, 0), params.char, font=font)
    x = (canvas_size - (bbox[2] - bbox[0])) // 2
    y = (canvas_size - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), params.char, font=font, fill=effective_brightness)

    return np.array(img, dtype=np.float32) / 255.0


def encrypt(text: str, seeds: list[int], canvas_size: int = 128) -> dict:
    """
    Vstup:  text (string), seeds (list int, jeden per znak)
    Výstup: dict s geometrickými maticami a parametrami
    """
    assert len(seeds) >= len(text), "Každý znak potrebuje vlastný seed"

    params_list = []
    arrays = []

    for i, char in enumerate(text):
        params = generate_render_params(char, seeds[i])
        arr = render_char(params, canvas_size)
        params_list.append(params)
        arrays.append(arr)

    return {
        "geometry": np.stack(arrays),     # shape: (n_chars, H, W)
        "params": params_list,
        "text_len": len(text),
    }


def decrypt(render_output: dict) -> list[np.ndarray]:
    """
    Vracia geometrické matice (render_output['geometry']).
    Vrstva renderovania je reverzibilná — parametre sú v kľúči.
    """
    return [render_output["geometry"][i] for i in range(render_output["text_len"])]
