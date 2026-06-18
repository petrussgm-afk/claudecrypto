"""
End-to-end test of the stable / ephemeral isotope split.

Three rounds:
  1. STABLE key → encrypt → wait 3s → decrypt OK
     proves normal messages survive arbitrary delays
  2. EPHEMERAL key → encrypt → immediate decrypt OK
     proves ephemeral mode round-trips when fresh
  3. EPHEMERAL key → encrypt → wait 5s → decrypt returns status='expired'
     proves graceful expiry — no exception, soft message
"""
import os, secrets, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app                 import fileformat, keystore
from app.decrypt_engine  import decrypt_fsc
from app.fileformat      import encode_cipher, save_fsc, load_fsc, read_pad, load_keyfile
from core                import isotope as isotope_mod
from core.pipeline       import encrypt as pipeline_encrypt
from keys.keygen         import generate

sep = "-" * 70
print(sep)
print(" ISOTOPE MODE end-to-end test")
print(sep)


def _encrypt_and_envelope(text, mk, canvas, mode, pad_bytes):
    """Pretend to be EncryptScreen — produce a full .fsc dict."""
    key = generate(text, master_key=mk, canvas_size=canvas, isotope_mode=mode)
    key.otp_pad = pad_bytes
    enc = pipeline_encrypt(text, key)
    qp = enc["quant_params"]
    return enc, {
        "version":        fileformat.FSC_VERSION,
        "key_id":         mk[:4].hex(),
        "pad_id":         "pad_0001",
        "t_encrypt":      float(key.t_encrypt),
        "canvas_size":    canvas,
        "n_chars":        len(text),
        "shape":          list(enc["otp_out"].shape),
        "nonce":          encode_cipher(enc["bh_out"]["nonce"]),
        "cipher":         encode_cipher(enc["auth_cipher"]),
        "quant_n_levels": int(qp.n_levels),
        "quant_vmin":     float(qp.vmin),
        "quant_vmax":     float(qp.vmax),
    }


with tempfile.TemporaryDirectory() as base:
    ks_dir = keystore.ensure_keystore(base)

    # ───────────────────────────────────────────────────────────────────
    # ROUND 1: STABLE — encrypt, sleep 3s, decrypt
    # ───────────────────────────────────────────────────────────────────
    print()
    print("  ROUND 1 — STABLE key, 3s delay, expect OK")
    mk_s   = secrets.token_bytes(32)
    canvas = 128
    pad_sz = 30 * canvas * canvas
    man_s  = fileformat.save_keyfile(
        keystore_dir=ks_dir, master_key=mk_s, label="stable test",
        pad_specs=[pad_sz], max_len=30, canvas_size=canvas,
        isotope_mode="stable",
    )
    assert man_s["isotope_mode"] == "stable"
    pad_s = read_pad(ks_dir, "pad_0001", keydata=man_s)

    enc_s, fsc_s = _encrypt_and_envelope("HELLO", mk_s, canvas, "stable", pad_s)
    # Report which isotopes were chosen — should ALL be from the stable pool
    isotopes_s = [p.isotope for p in enc_s["isotope_params"]]
    print(f"             isotopes: {isotopes_s}")
    for iso in isotopes_s:
        assert iso in isotope_mod.ISOTOPES_STABLE, \
            f"stable key chose {iso} which is not in STABLE pool"
    print(f"             ✓ all isotopes from STABLE pool (≥ 5 yr half-life)")

    print(f"             sleeping 3s …")
    time.sleep(3.0)

    res = decrypt_fsc(fsc_s, mk_s, pad_s, isotope_mode="stable")
    assert res.status == "ok",          f"expected ok, got {res.status}"
    assert len(res.geometry) == 5,      f"expected 5 glyphs, got {len(res.geometry)}"
    drift = res.t_decrypt - res.t_encrypt
    print(f"             ✓ decrypted after {drift:.1f}s drift, 5 glyphs recovered")


    # ───────────────────────────────────────────────────────────────────
    # ROUND 2: EPHEMERAL — encrypt + decrypt IMMEDIATELY
    # ───────────────────────────────────────────────────────────────────
    print()
    print("  ROUND 2 — EPHEMERAL key, immediate decrypt, expect OK")
    # Reuse the STABLE round's mk so we KNOW its materials decode cleanly
    # (the material layer is a separate concern from isotope mode — random
    # keys occasionally pick opaque lead/iron that the inverse can't recover).
    # Same mk + same canvas + new mode = same material_seeds, different
    # isotope pool. Proves isotope_mode is the only delta.
    mk_e     = mk_s
    pad_sz_e = 30 * canvas * canvas
    man_e    = fileformat.save_keyfile(
        keystore_dir=ks_dir, master_key=mk_e, label="ephemeral test",
        pad_specs=[pad_sz_e], max_len=30, canvas_size=canvas,
        isotope_mode="ephemeral",
    )
    assert man_e["isotope_mode"] == "ephemeral"
    pad_e = read_pad(ks_dir, "pad_0001", keydata=man_e)

    enc_e, fsc_e = _encrypt_and_envelope("HELLO", mk_e, canvas, "ephemeral", pad_e)
    isotopes_e = [p.isotope for p in enc_e["isotope_params"]]
    print(f"             isotopes: {isotopes_e}")
    for iso in isotopes_e:
        assert iso in isotope_mod.ISOTOPES_EPHEMERAL, \
            f"ephemeral key chose {iso} which is not in EPHEMERAL pool"
    print(f"             ✓ all isotopes from EPHEMERAL pool")

    # Use t_decrypt = t_encrypt so the round-trip is exact (no decay drift).
    res2 = decrypt_fsc(fsc_e, mk_e, pad_e,
                       t_decrypt=fsc_e["t_encrypt"], isotope_mode="ephemeral")
    assert res2.status == "ok", f"expected ok, got {res2.status}: {res2.expired_info}"
    assert len(res2.geometry) == 5
    print(f"             ✓ ephemeral round-trip OK with t_decrypt=t_encrypt   "
          f"({len(res2.geometry)} glyphs)")


    # ───────────────────────────────────────────────────────────────────
    # ROUND 3: EPHEMERAL — encrypt, sleep 5s, decrypt → expired
    # ───────────────────────────────────────────────────────────────────
    print()
    print("  ROUND 3 — EPHEMERAL key, 5s delay, expect status='expired'")
    # We need at least ONE Po-214 assignment for expiry to bite at 5s.
    # Find a text that triggers Po-214. Brute force: any single char gets a
    # deterministic isotope from seed=_derive_seed(mk, 'isotope', 0). With 3
    # ephemeral isotopes, ~33% of keys get Po-214 at position 0. Loop until
    # we find one — guaranteed by SHAKE256 distribution.
    target_text = "A"
    attempts    = 0
    while attempts < 50:
        attempts  += 1
        mk_try     = secrets.token_bytes(32)
        key_probe  = generate(target_text, master_key=mk_try, canvas_size=canvas,
                              isotope_mode="ephemeral")
        iso_probe  = isotope_mod.assign_isotope(
            0, key_probe.chars[0].isotope_seed, time.time(), mode="ephemeral",
        )
        if iso_probe.isotope == "Po-214":
            break
    else:
        raise RuntimeError("could not find a master_key whose seed picks Po-214 at index 0")
    print(f"             found Po-214 key after {attempts} probes  (mk={mk_try[:4].hex()}…)")

    pad_sz_x = 30 * canvas * canvas
    man_x = fileformat.save_keyfile(
        keystore_dir=ks_dir, master_key=mk_try, label="po-214 test",
        pad_specs=[pad_sz_x], max_len=30, canvas_size=canvas,
        isotope_mode="ephemeral",
    )
    pad_x = read_pad(ks_dir, "pad_0001", keydata=man_x)
    _, fsc_x = _encrypt_and_envelope(target_text, mk_try, canvas, "ephemeral", pad_x)

    print(f"             sleeping 5s … (Po-214 half-life = 164 µs)")
    time.sleep(5.0)

    res3 = decrypt_fsc(fsc_x, mk_try, pad_x, isotope_mode="ephemeral")
    assert res3.status == "expired", f"expected expired, got {res3.status}"
    assert res3.expired_info["isotope"] == "Po-214"
    assert res3.geometry == []
    print(f"             ✓ status='expired'   isotope={res3.expired_info['isotope']}")
    print(f"             ✓ delta_t={res3.expired_info['delta_t']:.1f}s = "
          f"{res3.expired_info['n_halflives']:.0f}× polčas")
    print(f"             ✓ no exception raised — graceful expiry")

print()
print(" ALL ISOTOPE MODE TESTS PASSED")
print(sep)
