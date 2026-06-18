"""
app/fileformat.py — FSC keyfile and OTP-pad on-disk format.

A "keystore" lives in a directory (typically <USB>/fsc_keys/). Each FSC key
consists of one JSON manifest and one or more binary pad files:

    <keystore>/
        <key_id>.fsckey         JSON manifest
        <key_id>_pad_0001.bin   raw OTP pad (size bytes)
        <key_id>_pad_0002.bin
        ...

key_id = first 4 bytes of master_key, hex-encoded (8 chars).

Manifest schema (version 1):
{
  "version":     1,
  "key_id":      "ab12cd34",
  "created":     1718000000.0,
  "label":       "Alice <-> Bob 2026",
  "max_len":     30,
  "canvas_size": 128,
  "master_key":  "<64 hex chars>",
  "otp_pads": {
    "pad_0001": {"size": 115200, "used": false, "file": "ab12cd34_pad_0001.bin"},
    ...
  }
}
"""

import base64
import json
import os
import secrets
import time
from typing import Optional

FSCKEY_VERSION = 1
FSC_VERSION    = 1
FSC_FILE_EXT   = ".fsc"


# ── base64 helpers ────────────────────────────────────────────────────────────

def encode_cipher(data: bytes) -> str:
    """Encode raw bytes to ASCII-safe base64 string for JSON storage."""
    return base64.b64encode(data).decode("ascii")


def decode_cipher(b64: str) -> bytes:
    """Inverse of encode_cipher."""
    return base64.b64decode(b64.encode("ascii"))


# ── save ──────────────────────────────────────────────────────────────────────

def save_keyfile(
    keystore_dir: str,
    master_key: bytes,
    label: str,
    pad_specs: list,
    max_len: int = 30,
    canvas_size: int = 128,
    isotope_mode: str = "stable",
) -> dict:
    """
    Write a .fsckey JSON manifest + N pad binary files into keystore_dir.

    Parameters
    ----------
    keystore_dir : directory (must exist) where the keyfile + pads are written.
    master_key   : 32 bytes (256-bit).
    label        : human-readable label (e.g. "Alice <-> Bob 2026").
    pad_specs    : list of pad sizes in bytes, one entry per pad to generate.
                   e.g. [115200, 115200, 115200] for 3 pads of max_len*canvas².
    max_len      : maximum message length this key supports.
    canvas_size  : canvas side length (px) the key was forged for.

    Returns
    -------
    dict — the full manifest written to disk (also includes "_keyfile_path").
    """
    if len(master_key) != 32:
        raise ValueError(f"master_key must be 32 bytes, got {len(master_key)}")
    if isotope_mode not in ("stable", "ephemeral"):
        raise ValueError(f"isotope_mode must be 'stable' or 'ephemeral', got {isotope_mode!r}")
    if not os.path.isdir(keystore_dir):
        raise FileNotFoundError(f"keystore_dir does not exist: {keystore_dir}")

    key_id = master_key[:4].hex()

    pads: dict = {}
    for i, size in enumerate(pad_specs, start=1):
        pad_id   = f"pad_{i:04d}"
        pad_file = f"{key_id}_{pad_id}.bin"
        pad_path = os.path.join(keystore_dir, pad_file)
        with open(pad_path, "wb") as f:
            f.write(secrets.token_bytes(int(size)))
        pads[pad_id] = {"size": int(size), "used": False, "file": pad_file}

    manifest = {
        "version":      FSCKEY_VERSION,
        "key_id":       key_id,
        "created":      time.time(),
        "label":        label,
        "max_len":      int(max_len),
        "canvas_size":  int(canvas_size),
        "isotope_mode": isotope_mode,
        "master_key":   master_key.hex(),
        "otp_pads":     pads,
    }

    keyfile_path = os.path.join(keystore_dir, f"{key_id}.fsckey")
    with open(keyfile_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    manifest["_keyfile_path"] = keyfile_path
    return manifest


# ── load ──────────────────────────────────────────────────────────────────────

def load_keyfile(path: str) -> dict:
    """Read and validate a .fsckey JSON manifest. Adds _keyfile_path field."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    version = data.get("version")
    if version != FSCKEY_VERSION:
        raise ValueError(
            f"Unsupported keyfile version {version} (expected {FSCKEY_VERSION})"
        )
    for required in ("key_id", "master_key", "otp_pads"):
        if required not in data:
            raise ValueError(f"keyfile missing required field: {required}")

    data["_keyfile_path"] = os.path.abspath(path)
    return data


# ── pad state ─────────────────────────────────────────────────────────────────

def mark_pad_used(keyfile_path: str, pad_id: str) -> dict:
    """Set otp_pads[pad_id].used = True and rewrite the JSON in place."""
    data = load_keyfile(keyfile_path)
    if pad_id not in data["otp_pads"]:
        raise KeyError(f"pad_id {pad_id!r} not in keyfile {keyfile_path}")
    data["otp_pads"][pad_id]["used"] = True
    data.pop("_keyfile_path", None)

    with open(keyfile_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    data["_keyfile_path"] = keyfile_path
    return data


def get_unused_pad(keydata: dict) -> Optional[str]:
    """Return the first unused pad_id (sorted), or None if all are used."""
    pads = keydata.get("otp_pads", {})
    for pad_id in sorted(pads):
        if not pads[pad_id].get("used", False):
            return pad_id
    return None


def read_pad(keydir: str, pad_id: str, keydata: dict = None) -> bytes:
    """
    Read raw pad bytes from disk.

    keydir   : directory containing the pad binary.
    pad_id   : e.g. "pad_0001".
    keydata  : optional manifest dict; used to resolve filename. If omitted,
               filename is reconstructed as "<key_id>_<pad_id>.bin" — caller
               must ensure keydir contains the matching keyfile.
    """
    if keydata is not None:
        pad_entry = keydata["otp_pads"].get(pad_id)
        if pad_entry is None:
            raise KeyError(f"pad_id {pad_id!r} not in keydata")
        fname = pad_entry["file"]
    else:
        raise ValueError("read_pad requires keydata to resolve pad filename")

    pad_path = os.path.join(keydir, fname)
    with open(pad_path, "rb") as f:
        return f.read()


# ── .fsc ciphertext envelope ──────────────────────────────────────────────────

def save_fsc(path: str, fsc_data: dict) -> str:
    """
    Write a .fsc JSON envelope. Returns the absolute path written.

    fsc_data schema (version 1):
    {
      "version":     1,
      "key_id":      "8b52629f",
      "pad_id":      "pad_0001",
      "t_encrypt":   <unix float>,
      "canvas_size": 128,
      "n_chars":     12,
      "shape":       [12, 128, 128],
      "nonce":       "<base64 of 16 bytes>",
      "cipher":      "<base64 of auth_cipher>"
    }
    """
    required = {"version", "key_id", "pad_id", "cipher"}
    missing = required - set(fsc_data)
    if missing:
        raise ValueError(f"save_fsc: missing required fields: {missing}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(fsc_data, f, indent=2, ensure_ascii=False)
    return os.path.abspath(path)


def load_fsc(path: str) -> dict:
    """Read and validate a .fsc envelope."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    version = data.get("version")
    if version != FSC_VERSION:
        raise ValueError(f"Unsupported .fsc version {version} (expected {FSC_VERSION})")
    return data
