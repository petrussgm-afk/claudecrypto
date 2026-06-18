"""
app/keystore.py — drive enumeration and on-disk key listing.

On Windows, removable drives (DRIVE_REMOVABLE = 2) are detected via the
Win32 API. Non-Windows hosts get a best-effort fallback (every drive listed).
"""

import ctypes
import os
import string
import sys
from dataclasses import dataclass
from typing import Optional

from app.fileformat import load_keyfile

KEYSTORE_DIRNAME = "fsc_keys"

# Win32 DRIVE_TYPE constants
DRIVE_UNKNOWN     = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE   = 2
DRIVE_FIXED       = 3
DRIVE_REMOTE      = 4
DRIVE_CDROM       = 5
DRIVE_RAMDISK     = 6

_DRIVE_TYPE_NAMES = {
    DRIVE_UNKNOWN:     "unknown",
    DRIVE_NO_ROOT_DIR: "no root",
    DRIVE_REMOVABLE:   "removable",
    DRIVE_FIXED:       "fixed",
    DRIVE_REMOTE:      "remote",
    DRIVE_CDROM:       "cdrom",
    DRIVE_RAMDISK:     "ramdisk",
}


@dataclass
class DriveInfo:
    path:        str       # e.g. "E:\\"
    label:       str       # volume label or "" if none
    drive_type:  int       # DRIVE_REMOVABLE etc.
    free_bytes:  int       # 0 if unknown
    removable:   bool

    @property
    def type_name(self) -> str:
        return _DRIVE_TYPE_NAMES.get(self.drive_type, "unknown")

    def display(self) -> str:
        free_gb = self.free_bytes / (1024**3) if self.free_bytes else 0
        lbl = f" — {self.label}" if self.label else ""
        return f"{self.path}{lbl}  ({free_gb:.1f} GB free, {self.type_name})"


# ── Win32 helpers ─────────────────────────────────────────────────────────────

def _get_volume_label(drive_root: str) -> str:
    if sys.platform != "win32":
        return ""
    buf       = ctypes.create_unicode_buffer(261)
    fs_buf    = ctypes.create_unicode_buffer(261)
    serial    = ctypes.c_ulong(0)
    max_comp  = ctypes.c_ulong(0)
    fs_flags  = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(drive_root),
        buf, ctypes.sizeof(buf),
        ctypes.byref(serial),
        ctypes.byref(max_comp),
        ctypes.byref(fs_flags),
        fs_buf, ctypes.sizeof(fs_buf),
    )
    return buf.value if ok else ""


def _get_free_bytes(drive_root: str) -> int:
    if sys.platform != "win32":
        try:
            st = os.statvfs(drive_root)
            return st.f_bavail * st.f_frsize
        except (AttributeError, OSError):
            return 0
    free_avail = ctypes.c_ulonglong(0)
    total      = ctypes.c_ulonglong(0)
    total_free = ctypes.c_ulonglong(0)
    ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(drive_root),
        ctypes.byref(free_avail),
        ctypes.byref(total),
        ctypes.byref(total_free),
    )
    return free_avail.value if ok else 0


# ── drive enumeration ────────────────────────────────────────────────────────

def list_drives(removable_only: bool = False) -> list:
    """
    Enumerate mounted drives. Returns a list of DriveInfo.

    On Windows: uses GetLogicalDrives + GetDriveTypeW + GetVolumeInformationW.
    Elsewhere: returns [DriveInfo(path="/", ...)] as a fallback.
    """
    drives: list = []

    if sys.platform == "win32":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        # Drive types where label/free probes can hang (disconnected network
        # shares, empty CD drives). GetDriveTypeW itself is instant; we just
        # skip the slow follow-ups for these.
        SLOW_PROBE_SKIP = {DRIVE_REMOTE, DRIVE_CDROM, DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR}
        for i, letter in enumerate(string.ascii_uppercase):
            if not (bitmask & (1 << i)):
                continue
            root = f"{letter}:\\"
            dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
            if removable_only and dtype != DRIVE_REMOVABLE:
                continue
            if dtype in SLOW_PROBE_SKIP:
                label, free = "", 0
            else:
                try:
                    label = _get_volume_label(root)
                except Exception:
                    label = ""
                try:
                    free = _get_free_bytes(root)
                except Exception:
                    free = 0
            drives.append(DriveInfo(
                path=root, label=label, drive_type=dtype,
                free_bytes=free, removable=(dtype == DRIVE_REMOVABLE),
            ))
    else:
        # POSIX fallback — single root, marked non-removable
        free = _get_free_bytes("/")
        drives.append(DriveInfo(
            path="/", label="", drive_type=DRIVE_FIXED,
            free_bytes=free, removable=False,
        ))

    return drives


# ── keystore directory ───────────────────────────────────────────────────────

def keystore_path(drive_path: str) -> str:
    """Return the conventional fsc_keys/ path inside a drive."""
    return os.path.join(drive_path, KEYSTORE_DIRNAME)


def ensure_keystore(drive_path: str) -> str:
    """Create <drive>/fsc_keys/ if missing. Returns the absolute path."""
    ks = keystore_path(drive_path)
    os.makedirs(ks, exist_ok=True)
    return ks


def list_keys(drive_path: str) -> list:
    """
    Scan <drive>/fsc_keys/ for *.fsckey manifests. Returns a list of dicts
    (the parsed manifests, each with _keyfile_path). Silently skips
    unreadable / invalid files.
    """
    ks = keystore_path(drive_path)
    if not os.path.isdir(ks):
        return []
    out = []
    for fname in sorted(os.listdir(ks)):
        if not fname.endswith(".fsckey"):
            continue
        try:
            out.append(load_keyfile(os.path.join(ks, fname)))
        except Exception:
            continue
    return out


# Drive types we will NOT walk into when searching for fsc_keys/ directories.
# Dead network shares can hang os.path.isdir for tens of seconds — never probe
# them. Removable + fixed local drives are the only realistic key stores.
SCAN_SKIP_TYPES = {
    DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK,
}


def list_scannable_drives() -> list:
    """Return only drives we can safely walk into looking for fsc_keys/."""
    return [d for d in list_drives() if d.drive_type not in SCAN_SKIP_TYPES]
