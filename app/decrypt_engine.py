"""
app/decrypt_engine.py — cross-process .fsc decryption.

Given:
  - a parsed .fsc envelope (auth_cipher b64, nonce, quant params, t_encrypt …)
  - the master_key from a matching .fsckey
  - the OTP pad bytes from a matching pad_*.bin

reconstructs the enc_state dict that core.pipeline.decrypt() consumes and
runs it. Returns a list of recovered glyph images (one float32 array per
character).

Why this is even possible without knowing the plaintext:

  * per-layer seeds (`material_seed`, `isotope_seed`, `fractal_seed`) depend
    only on (master_key, purpose, index) — NOT on the character itself.
  * `renderer.decrypt()` only uses `text_len`, so we can pass placeholder
    chars for the renderer params.
  * the only data-dependent param is the quantiser's vmin/vmax — those
    travel inside the .fsc envelope (written by the Encrypt screen).
  * the Lorenz `lorenz_init` is derived from master_key by `keys.keygen`;
    the per-message nonce travels inside the .fsc envelope.

What pipeline.decrypt actually recovers: the rendered glyph images. The
human (or future OCR) reads the text by looking at those images. The
"plaintext" is never stored anywhere.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.fileformat   import decode_cipher
from core             import material, isotope, fractal, renderer
from core.blackhole   import BlackholeParams
from core.isotope     import IsotopeExpiredError
from core.pipeline    import decrypt as pipeline_decrypt, _unseal_cipher
from core.quantizer   import QuantizerParams
from keys.keygen      import generate

PLACEHOLDER_CHAR = "?"   # any char works; renderer.decrypt only uses text_len


# ── result ───────────────────────────────────────────────────────────────────

@dataclass
class DecryptResult:
    """
    Result of a cross-process decrypt.

    status="ok"       — geometry list is populated, glyphs recoverable.
    status="expired"  — the message used ephemeral isotopes and so much time
                        elapsed that the signal cannot be recovered. This is
                        the intended behaviour for ephemeral messages, not
                        an error. geometry is empty.
    """
    geometry:     list           # list of float32 (H, W) arrays, len = n_chars
    n_chars:      int
    key_id:       str
    pad_id:       str
    t_encrypt:    float
    t_decrypt:    float
    canvas_size:  int
    status:       str  = "ok"
    isotope_mode: str  = "stable"
    expired_info: dict = field(default_factory=dict)   # {isotope, half_life, delta_t, n_halflives}


# ── main entry point ──────────────────────────────────────────────────────────

def decrypt_fsc(
    fsc_data:     dict,
    master_key:   bytes,
    pad_bytes:    bytes,
    t_decrypt:    Optional[float] = None,
    isotope_mode: str = "stable",
) -> DecryptResult:
    """
    Decrypt a .fsc envelope using the given master_key + pad.

    isotope_mode MUST match the mode of the key that produced the .fsc,
    otherwise different isotopes are selected per char and recovery fails.
    The Decrypt screen reads the mode from the .fsckey manifest.

    Raises
    ------
    ValueError
        On HMAC failure (wrong key / tampered ciphertext) or any non-isotope
        layer error.
    KeyError
        If the .fsc is missing required fields.

    Returns a DecryptResult with status="expired" (NOT raises) when an
    ephemeral isotope has decayed past recovery.
    """
    # ── pull fields from envelope ──────────────────────────────────────────
    required = ("cipher", "nonce", "n_chars", "canvas_size", "t_encrypt",
                "quant_n_levels", "quant_vmin", "quant_vmax")
    for k in required:
        if k not in fsc_data:
            raise KeyError(f".fsc missing required field: {k!r}")

    canvas = int(fsc_data["canvas_size"])
    n      = int(fsc_data["n_chars"])
    t_enc  = float(fsc_data["t_encrypt"])
    nonce       = decode_cipher(fsc_data["nonce"])
    auth_cipher = decode_cipher(fsc_data["cipher"])

    if len(master_key) != 32:
        raise ValueError(f"master_key must be 32 bytes, got {len(master_key)}")
    if len(nonce) != 16:
        raise ValueError(f"nonce must be 16 bytes, got {len(nonce)}")

    # ── rebuild the FSCKey from master_key ────────────────────────────────
    # Placeholder text gives the right CharKey count. Per-char seeds depend
    # only on (master_key, purpose, index) — not on the char content — so
    # the seeds we get match the encrypt-time seeds exactly. The isotope
    # mode MUST match what was used at encrypt time so the same isotope is
    # picked from the same pool for the same seed.
    placeholder = PLACEHOLDER_CHAR * n
    key = generate(placeholder, master_key=master_key, canvas_size=canvas,
                   isotope_mode=isotope_mode)

    # CRITICAL: override the random pad/time that generate() produced.
    key.otp_pad   = pad_bytes
    key.t_encrypt = t_enc      # isotope decay uses (t_decrypt - t_encrypt)

    # ── recompute per-layer params (deterministic from seeds) ─────────────
    material_params = [
        material.assign_material(i, key.chars[i].material_seed)
        for i in range(n)
    ]
    isotope_params = [
        isotope.assign_isotope(i, key.chars[i].isotope_seed, key.t_encrypt,
                               mode=isotope_mode)
        for i in range(n)
    ]
    fractal_params = [
        fractal.generate_fractal_params(key.chars[i].fractal_seed, canvas)
        for i in range(n)
    ]
    # renderer_params: needed by pipeline.decrypt only to compute text_len.
    # The char content doesn't matter for renderer.decrypt — dummy placeholder
    # chars are fine.
    renderer_params = [
        renderer.generate_render_params(placeholder[i], key.chars[i].renderer_seed)
        for i in range(n)
    ]

    quant_params = QuantizerParams(
        n_levels=int(fsc_data["quant_n_levels"]),
        vmin=float(fsc_data["quant_vmin"]),
        vmax=float(fsc_data["quant_vmax"]),
    )

    # ── unseal: HMAC-verify + decrypt header + extract cipher array ───────
    otp_out = _unseal_cipher(auth_cipher, master_key)
    if otp_out.shape != (n, canvas, canvas):
        raise ValueError(
            f"unsealed cipher shape {otp_out.shape} does not match envelope "
            f"({n}, {canvas}, {canvas})"
        )

    # ── BlackholeParams must carry the stored nonce ───────────────────────
    bh_params = BlackholeParams(lorenz_init=key.lorenz_init, nonce=nonce)

    # ── assemble enc_state for pipeline.decrypt ───────────────────────────
    enc_state = {
        "auth_cipher":     auth_cipher,
        "otp_out":         otp_out,
        "bh_out": {
            "cipher":         otp_out,            # placeholder; overridden internally
            "params":         bh_params,
            "nonce":          nonce,
            "original_shape": otp_out.shape,
        },
        "quant_out":       {"params": quant_params, "quantized": None},
        "quant_params":    quant_params,
        "renderer_params": renderer_params,
        "material_params": material_params,
        "isotope_params":  isotope_params,
        "fractal_params":  fractal_params,
        "text":            placeholder,
        "key":             key,
    }

    # ── run decrypt (HMAC verify happens inside) ──────────────────────────
    t_now = float(t_decrypt) if t_decrypt is not None else time.time()
    common = dict(
        n_chars=n,
        key_id=str(fsc_data.get("key_id", master_key[:4].hex())),
        pad_id=str(fsc_data.get("pad_id", "?")),
        t_encrypt=t_enc,
        t_decrypt=t_now,
        canvas_size=canvas,
        isotope_mode=isotope_mode,
    )

    try:
        result = pipeline_decrypt(enc_state, t_decrypt=t_now)
    except IsotopeExpiredError as exc:
        # Intended behaviour for ephemeral messages — return a soft "expired"
        # result instead of propagating an error. The screen renders this
        # as a friendly "message has decayed" notice.
        return DecryptResult(
            geometry=[],
            status="expired",
            expired_info={
                "isotope":     exc.isotope,
                "half_life":   exc.half_life,
                "delta_t":     exc.delta_t,
                "n_halflives": exc.n_halflives,
            },
            **common,
        )

    return DecryptResult(
        geometry=result["geometry"],
        status="ok",
        **common,
    )


# ── helpers for the Decrypt screen ────────────────────────────────────────────

def find_key_on_disk(fsc_data: dict, scannable_keys: list) -> Optional[dict]:
    """
    Given a list of key manifests (from keystore.list_keys across drives),
    return the manifest whose key_id matches the .fsc envelope.
    """
    target = fsc_data.get("key_id")
    if not target:
        return None
    for m in scannable_keys:
        if m.get("key_id") == target:
            return m
    return None
