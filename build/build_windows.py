"""
build/build_windows.py — produce dist/FSC/FSC.exe via PyInstaller.

Usage:
    py build/build_windows.py            (default build)
    py build/build_windows.py --clean    (wipe previous output first)

Notes
-----
- Output: dist/FSC/FSC.exe  (one-dir mode — faster startup, more reliable
  for PySide6 + matplotlib than one-file).
- PyInstaller intermediate work goes to build/_work/ so it doesn't trample
  our own build/__init__.py or this script.
- --collect-all PySide6 ensures Qt plugins (platforms/qwindows.dll,
  imageformats/, etc.) are bundled — without these the .exe fails to find
  a display platform plugin on launch.
- --collect-data matplotlib pulls matplotlib's mpl-data (font metrics,
  rcParams) into the bundle.
- The whole project root is added to --paths so PyInstaller can discover
  the `core`, `keys`, `viz`, `art`, `app` packages by name.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT   = os.path.dirname(HERE)
MAIN   = os.path.join(ROOT, "app", "main.py")
QSS    = os.path.join(ROOT, "app", "style.qss")
WORK   = os.path.join(ROOT, "build", "_work")
DIST   = os.path.join(ROOT, "dist")
SPEC   = HERE                       # where FSC.spec lands

APP_NAME = "FSC"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build FSC desktop .exe")
    ap.add_argument("--clean", action="store_true",
                    help="wipe dist/ and build/_work/ before building")
    args = ap.parse_args()

    if not os.path.isfile(MAIN):
        print(f"FATAL: app/main.py not found at {MAIN}", file=sys.stderr)
        return 1
    if not os.path.isfile(QSS):
        print(f"FATAL: app/style.qss not found at {QSS}", file=sys.stderr)
        return 1

    if args.clean:
        for path in (WORK, DIST, os.path.join(SPEC, f"{APP_NAME}.spec")):
            if os.path.isdir(path):
                print(f"[clean] rm -rf {path}")
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                print(f"[clean] rm {path}")
                os.unlink(path)

    # Always recreate WORK so PyInstaller's --workpath exists
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)

    # ── PyInstaller invocation ───────────────────────────────────────────
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name",        APP_NAME,
        "--windowed",                     # no console window
        "--onedir",                       # dist/FSC/FSC.exe + _internal/
        # data: app/style.qss → bundle as <root>/app/style.qss
        "--add-data",    f"{QSS}{os.pathsep}app",
        # let the module finder see our top-level packages
        "--paths",       ROOT,
        # output locations
        "--workpath",    WORK,
        "--distpath",    DIST,
        "--specpath",    SPEC,
        # always overwrite
        "--noconfirm",
        # bundle the entire Qt runtime (plugins, translations) — without
        # this, the .exe usually crashes with "no Qt platform plugin found"
        "--collect-all", "PySide6",
        # matplotlib needs mpl-data + Agg backend
        "--collect-data",      "matplotlib",
        "--collect-submodules", "matplotlib.backends",
        # things we don't want.
        # NOTE: do NOT exclude 'unittest' — pyparsing (transitive dep of
        # matplotlib) imports unittest at module load time, so excluding it
        # crashes the .exe before the GUI shows.
        "--exclude-module", "tkinter",
        "--exclude-module", "test",
        "--exclude-module", "tests",
        # entry point
        MAIN,
    ]

    print()
    print(" PyInstaller command:")
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print()

    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print(f"\n[build] PyInstaller failed (exit {r.returncode})", file=sys.stderr)
        return r.returncode

    exe = os.path.join(DIST, APP_NAME, f"{APP_NAME}.exe")
    if not os.path.isfile(exe):
        print(f"\n[build] WARNING: expected {exe} not found", file=sys.stderr)
        return 2

    size_mb = os.path.getsize(exe) / 1024 / 1024
    bundle_mb = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(os.path.join(DIST, APP_NAME)) for f in fs
    ) / 1024 / 1024

    print()
    print(" " + "-" * 66)
    print(f"  BUILD OK   {exe}")
    print(f"  exe size       : {size_mb:6.1f} MB")
    print(f"  bundle size    : {bundle_mb:6.1f} MB  (whole dist/{APP_NAME}/ folder)")
    print(f"  launch command : {exe}")
    print(" " + "-" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
