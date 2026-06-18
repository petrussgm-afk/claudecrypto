"""
Smoke test for the Phase-1 desktop-app data path (no Qt required).

  - generates a master key
  - writes a keyfile + N pads via app.fileformat.save_keyfile
  - loads it back with load_keyfile
  - marks one pad used, re-reads, verifies
  - renders the Lorenz portrait via lorenz_portrait_from_master
  - lists drives via app.keystore.list_drives
"""
import os
import secrets
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import fileformat, keystore
from art.lorenz_portrait import lorenz_portrait_from_master

sep = "-" * 66
print(sep)
print(" FSC desktop app — smoke test (Phase 1)")
print(sep)

# ── temp keystore ────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as base:
    drive_root = base
    keystore_dir = keystore.ensure_keystore(drive_root)
    print(f" keystore dir : {keystore_dir}")

    # ── forge a key ───────────────────────────────────────────────────────────
    mk = secrets.token_bytes(32)
    pad_size = 30 * 128 * 128  # 480 kB per pad
    n_pads = 3
    manifest = fileformat.save_keyfile(
        keystore_dir=keystore_dir,
        master_key=mk,
        label="smoke test key",
        pad_specs=[pad_size] * n_pads,
        max_len=30,
        canvas_size=128,
    )
    print(f" key_id       : {manifest['key_id']}")
    print(f" keyfile      : {manifest['_keyfile_path']}")
    print(f" pads written : {len(manifest['otp_pads'])} × {pad_size/1024:.1f} kB"
          f" = {n_pads*pad_size/1024/1024:.2f} MB")

    # ── verify files on disk ────────────────────────────────────────────────
    files = sorted(os.listdir(keystore_dir))
    print(f" files        : {files}")
    assert any(f.endswith(".fsckey") for f in files)
    assert sum(1 for f in files if f.endswith(".bin")) == n_pads
    for pad_id, info in manifest["otp_pads"].items():
        full = os.path.join(keystore_dir, info["file"])
        assert os.path.getsize(full) == info["size"], f"{pad_id} size mismatch"

    # ── reload + state mutation ─────────────────────────────────────────────
    loaded = fileformat.load_keyfile(manifest["_keyfile_path"])
    assert loaded["master_key"] == mk.hex()
    assert loaded["key_id"]     == manifest["key_id"]
    print(f" reload OK    : master_key + key_id match")

    first_pad = fileformat.get_unused_pad(loaded)
    print(f" unused pad   : {first_pad}")
    fileformat.mark_pad_used(manifest["_keyfile_path"], first_pad)
    reloaded = fileformat.load_keyfile(manifest["_keyfile_path"])
    assert reloaded["otp_pads"][first_pad]["used"] is True
    assert fileformat.get_unused_pad(reloaded) != first_pad
    print(f" mark used    : OK   next unused = {fileformat.get_unused_pad(reloaded)}")

    # ── read pad bytes ──────────────────────────────────────────────────────
    pad_bytes = fileformat.read_pad(keystore_dir, first_pad, keydata=reloaded)
    assert len(pad_bytes) == pad_size
    print(f" read pad     : {len(pad_bytes)} bytes")

    # ── Lorenz portrait ────────────────────────────────────────────────────
    portrait_path = os.path.join(base, "smoke_portrait.png")
    out = lorenz_portrait_from_master(mk, save_path=portrait_path, n_steps=20_000)
    assert os.path.isfile(out)
    size_kb = os.path.getsize(out) / 1024
    print(f" portrait     : {out}  ({size_kb:.1f} kB)")

# ── drive enumeration ─────────────────────────────────────────────────────────
print()
print(" drives detected:")
for d in keystore.list_drives():
    flag = "USB" if d.removable else "FIX"
    print(f"   [{flag}] {d.display()}")

print()
print(" ALL SMOKE TESTS PASSED")
print(sep)
