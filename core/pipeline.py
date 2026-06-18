"""
FSC — Pipeline
Orchestrácia celého šifrovacieho toku (7 vrstiev):

    renderer → material → isotope → fractal → quantizer → blackhole → otp
                                                                         ↓
    renderer ← material ← isotope ← fractal ← quantizer ← blackhole ← otp  (decrypt)
"""

import hashlib
import hmac
import secrets
import struct
import time
import numpy as np

from core import renderer, material, isotope, fractal, quantizer, blackhole, otp
from core.blackhole import BlackholeParams
from keys.keygen import FSCKey

# ── Authentication + padding ──────────────────────────────────────────────────

BLOCK = 256  # pad ciphertext to next multiple of this many bytes


def _hmac_key(master_key: bytes) -> bytes:
    return hashlib.shake_256(master_key + b'hmac').digest(32)


def _add_hmac(data: bytes, key: bytes) -> bytes:
    mac = hmac.new(key, data, hashlib.sha256).digest()
    return mac + data


def _verify_hmac(data: bytes, key: bytes) -> bytes:
    mac, payload = data[:32], data[32:]
    expected = hmac.new(key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("HMAC verification failed — ciphertext tampered or wrong key")
    return payload


def _seal_cipher(cipher: np.ndarray, master_key: bytes) -> bytes:
    """Serialize cipher array → length-hidden, HMAC-authenticated bytes."""
    raw = cipher.ravel().tobytes()
    original_size = len(raw)
    # header: original_size (4 bytes) + shape n_chars,H,W (3×4 bytes) = 16 bytes
    header = struct.pack('>I3I', original_size, *cipher.shape)
    mask = hashlib.shake_256(master_key + b'header').digest(len(header))
    enc_header = bytes(a ^ b for a, b in zip(header, mask))
    payload = enc_header + raw
    rem = len(payload) % BLOCK
    if rem:
        payload += secrets.token_bytes(BLOCK - rem)
    return _add_hmac(payload, _hmac_key(master_key))


def _unseal_cipher(ct_bytes: bytes, master_key: bytes) -> np.ndarray:
    """Verify HMAC, strip padding, reconstruct cipher array."""
    payload = _verify_hmac(ct_bytes, _hmac_key(master_key))
    header_size = 16
    mask = hashlib.shake_256(master_key + b'header').digest(header_size)
    header_dec = bytes(a ^ b for a, b in zip(payload[:header_size], mask))
    original_size, n_chars, H, W = struct.unpack('>I3I', header_dec)
    raw = payload[header_size:header_size + original_size]
    return np.frombuffer(raw, dtype=np.uint8).reshape(n_chars, H, W)


# ── Encrypt ──────────────────────────────────────────────────────────────────

def encrypt(text: str, key: FSCKey) -> dict:
    """Úplné šifrovanie textu cez všetkých 7 vrstiev."""
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
    # mode chosen by the key (stable for normal messages, ephemeral for
    # self-destructing). Default "stable" if key dataclass lacks the field.
    iso_mode = getattr(key, "isotope_mode", "stable")
    isotope_params = [
        isotope.assign_isotope(i, key.chars[i].isotope_seed, key.t_encrypt, mode=iso_mode)
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

    # ── Vrstva 7: OTP ────────────────────────────────────────────────────
    otp_out = otp.encrypt(bh_out["cipher"], key.otp_pad)

    # ── Authentication + padding (over final OTP ciphertext) ──────────────
    auth_cipher = _seal_cipher(otp_out, key.master_key)

    return {
        "geometry":        geometry,
        "material_out":    material_out,
        "isotope_out":     isotope_out,
        "fractal_out":     fractal_out,
        "quant_out":       quant_out,
        "bh_out":          bh_out,
        "otp_out":         otp_out,
        "auth_cipher":     auth_cipher,
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
    """Úplné dešifrovanie — reverzia 7 vrstiev v opačnom poradí."""
    t = t_decrypt if t_decrypt is not None else time.time()

    # ── HMAC verification (fail fast before any decryption) ───────────────
    if "auth_cipher" in enc_state:
        _verify_hmac(enc_state["auth_cipher"], _hmac_key(enc_state["key"].master_key))

    # ── Reverzia 7: OTP ───────────────────────────────────────────────────
    after_otp = otp.decrypt(enc_state["otp_out"], enc_state["key"].otp_pad)

    # ── Reverzia 6: Blackhole ─────────────────────────────────────────────
    bh_for_decrypt = {**enc_state["bh_out"], "cipher": after_otp.astype(np.uint8)}
    after_bh = blackhole.decrypt(bh_for_decrypt)

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
        "after_otp":      after_otp,
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

      after_otp      == bh_out["cipher"]                (presné — OTP XOR)
      after_bh       == quant_out["quantized"]          (presné — Lorenz XOR)
      after_quant    ≈  fractal_out["transformed"]      (kvantz. chyba ≤ step/2)
      after_fractal  ≈  isotope_out["decayed"]          (presné — permutácia)
      after_isotope  ≈  material_out["attenuated"]      (amplifikovaná kvantz. ch.)
      after_material ≈  geometry                         (plný round-trip)
      geometry_final ≈  geometry                         (plný round-trip)
    """
    refs = {
        "after_otp":      enc_state["bh_out"]["cipher"].astype(np.uint8),
        "after_bh":       enc_state["quant_out"]["quantized"].astype(np.uint16),
        "after_quant":    enc_state["fractal_out"]["transformed"],
        "after_fractal":  enc_state["isotope_out"]["decayed"],
        "after_isotope":  enc_state["material_out"]["attenuated"],
        "after_material": enc_state["geometry"],
        "geometry_final": enc_state["geometry"],
    }
    vals = {
        "after_otp":      dec_state["after_otp"].astype(np.uint8),
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
