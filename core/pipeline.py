"""
FSC — Pipeline
Orchestrácia celého šifrovacieho toku (6 vrstiev):

    renderer → material → isotope → fractal → quantizer → blackhole
                                                               ↓
    renderer ← material ← isotope ← fractal ← quantizer ← blackhole  (decrypt)
"""

import time
import numpy as np

from core import renderer, material, isotope, fractal, quantizer, blackhole
from core.blackhole import BlackholeParams
from keys.keygen import FSCKey


# ── Encrypt ──────────────────────────────────────────────────────────────────

def encrypt(text: str, key: FSCKey) -> dict:
    """Úplné šifrovanie textu cez všetkých 6 vrstiev."""
    assert len(text) == len(key.chars), "Text sa nezhoduje s kľúčom"
    n  = len(text)
    cs = key.canvas_size

    # ── Vrstva 1: Renderer ────────────────────────────────────────────────
    renderer_params = [
        renderer.generate_render_params(text[i], key.chars[i].renderer_seed)
        for i in range(n)
    ]
    geometry = np.stack([renderer.render_char(p, cs) for p in renderer_params])

    # ── Vrstva 2: Material ────────────────────────────────────────────────
    material_params = [
        material.assign_material(i, key.chars[i].material_seed)
        for i in range(n)
    ]
    material_out = material.encrypt(geometry, material_params)

    # ── Vrstva 3: Isotope ─────────────────────────────────────────────────
    isotope_params = [
        isotope.assign_isotope(i, key.chars[i].isotope_seed, key.t_encrypt)
        for i in range(n)
    ]
    isotope_out = isotope.encrypt(material_out["attenuated"], isotope_params)

    # ── Vrstva 4: Fractal ─────────────────────────────────────────────────
    fractal_params = [
        fractal.generate_fractal_params(key.chars[i].fractal_seed, cs)
        for i in range(n)
    ]
    fractal_out = fractal.encrypt(isotope_out["decayed"], fractal_params)

    # ── Vrstva 5: Quantizer ───────────────────────────────────────────────
    quant_out = quantizer.encrypt(fractal_out["transformed"], key.planck_resolution)

    # ── Vrstva 6: Blackhole ───────────────────────────────────────────────
    bh_params = BlackholeParams(lorenz_init=key.lorenz_init)
    bh_out    = blackhole.encrypt(quant_out["quantized"], bh_params)

    return {
        "geometry":        geometry,
        "material_out":    material_out,
        "isotope_out":     isotope_out,
        "fractal_out":     fractal_out,
        "quant_out":       quant_out,
        "bh_out":          bh_out,
        "renderer_params": renderer_params,
        "material_params": material_params,
        "isotope_params":  isotope_params,
        "fractal_params":  fractal_params,
        "quant_params":    quant_out["params"],
        "bh_params":       bh_params,
        "text":            text,
        "key":             key,
    }


# ── Decrypt ───────────────────────────────────────────────────────────────────

def decrypt(enc_state: dict, t_decrypt: float = None) -> dict:
    """Úplné dešifrovanie — reverzia 6 vrstiev v opačnom poradí."""
    t = t_decrypt if t_decrypt is not None else time.time()

    # ── Reverzia 6: Blackhole ─────────────────────────────────────────────
    after_bh = blackhole.decrypt(enc_state["bh_out"])

    # ── Reverzia 5: Quantizer ─────────────────────────────────────────────
    after_quant = quantizer.decrypt({
        "quantized": after_bh,
        "params":    enc_state["quant_params"],
    })

    # ── Reverzia 4: Fractal ───────────────────────────────────────────────
    after_fractal = fractal.decrypt({
        "transformed": after_quant,
        "params":      enc_state["fractal_params"],
    })

    # ── Reverzia 3: Isotope ───────────────────────────────────────────────
    after_isotope = isotope.decrypt({
        "decayed":   after_fractal,
        "params":    enc_state["isotope_params"],
        "t_encrypt": enc_state["key"].t_encrypt,
    }, t_decrypt=t)

    # ── Reverzia 2: Material ──────────────────────────────────────────────
    after_material = material.decrypt({
        "attenuated": after_isotope,
        "params":     enc_state["material_params"],
    })

    # ── Reverzia 1: Renderer ──────────────────────────────────────────────
    after_renderer = renderer.decrypt({
        "geometry": after_material,
        "params":   enc_state["renderer_params"],
        "text_len": len(enc_state["text"]),
    })

    return {
        "after_bh":       after_bh,
        "after_quant":    after_quant,
        "after_fractal":  after_fractal,
        "after_isotope":  after_isotope,
        "after_material": after_material,
        "geometry":       after_renderer,
    }


# ── Diagnostika ───────────────────────────────────────────────────────────────

def roundtrip_error(enc_state: dict, dec_state: dict) -> dict:
    """
    Každá decryptovaná vrstva vs. jej správna referencia z encryption state.

      after_bh       == quant_out["quantized"]          (presné — XOR)
      after_quant    ≈  fractal_out["transformed"]      (kvantz. chyba ≤ step/2)
      after_fractal  ≈  isotope_out["decayed"]          (presné — permutácia)
      after_isotope  ≈  material_out["attenuated"]      (amplifikovaná kvantz. ch.)
      after_material ≈  geometry                         (plný round-trip)
      geometry_final ≈  geometry                         (plný round-trip)
    """
    refs = {
        "after_bh":       enc_state["quant_out"]["quantized"].astype(np.uint16),
        "after_quant":    enc_state["fractal_out"]["transformed"],
        "after_fractal":  enc_state["isotope_out"]["decayed"],
        "after_isotope":  enc_state["material_out"]["attenuated"],
        "after_material": enc_state["geometry"],
        "geometry_final": enc_state["geometry"],
    }
    vals = {
        "after_bh":       dec_state["after_bh"].astype(np.uint16),
        "after_quant":    dec_state["after_quant"],
        "after_fractal":  dec_state["after_fractal"],
        "after_isotope":  dec_state["after_isotope"],
        "after_material": dec_state["after_material"],
        "geometry_final": np.stack(dec_state["geometry"]),
    }

    errors = {}
    for name in refs:
        diff = np.abs(refs[name].astype(np.float64) - vals[name].astype(np.float64))
        errors[name] = {"max": float(diff.max()), "mean": float(diff.mean())}
    return errors
