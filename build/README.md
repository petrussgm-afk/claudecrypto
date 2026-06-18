# FSC — Windows packaging

Produces a stand-alone `dist/FSC/FSC.exe` that runs without a Python install.

## Requirements

- Python 3.10+ on PATH
- All project dependencies installed (`pip install -r requirements.txt`)
- PyInstaller (`py -m pip install pyinstaller`) — version 6+ recommended

## Build

```powershell
# from project root
py build/build_windows.py
```

For a clean rebuild (wipe previous output first):

```powershell
py build/build_windows.py --clean
```

Output:

```
dist/
  FSC/
    FSC.exe              ← launch this
    _internal/           ← Qt plugins, matplotlib data, Python runtime
```

Distribute the whole `dist/FSC/` folder — `FSC.exe` won't run alone.

## Build layout

| Path | Purpose |
|---|---|
| `build/build_windows.py` | PyInstaller invocation |
| `build/_work/` | PyInstaller intermediate output (analysis cache) |
| `build/FSC.spec` | auto-generated spec file (can be edited for advanced cases) |
| `dist/FSC/` | shippable application bundle |

## Bundled components

PyInstaller is invoked with:

- `--windowed` — no console window when the .exe runs
- `--onedir` — folder-bundle (not single .exe) for faster startup and reliable Qt plugin discovery
- `--collect-all PySide6` — full Qt runtime, including the `platforms/qwindows.dll` plugin (without this the .exe crashes immediately with *"could not find or load any Qt platform plugin"*)
- `--collect-data matplotlib` + `--collect-submodules matplotlib.backends` — matplotlib's `mpl-data` and the Agg backend used by `viz/visualizer.py` and `art/lorenz_portrait.py`
- `--add-data app/style.qss;app` — the QSS stylesheet, loaded at runtime by `app/main.py`
- `--paths <project_root>` — so `core`, `keys`, `viz`, `art`, `app` are discoverable as top-level packages

## Verifying the build

After the build finishes:

```powershell
.\dist\FSC\FSC.exe
```

The FSC window should open on KEY FORGE. Switch through ENCRYPT, DECRYPT, KEY MANAGER to verify all 4 screens render.

To start on a specific screen:

```powershell
.\dist\FSC\FSC.exe encrypt
.\dist\FSC\FSC.exe decrypt
.\dist\FSC\FSC.exe manager
```

## Troubleshooting

- **"Could not find or load any Qt platform plugin 'windows'"** — `--collect-all PySide6` should prevent this. If it still happens, check that `dist/FSC/_internal/PySide6/plugins/platforms/qwindows.dll` exists.
- **Blank Lorenz portrait / matplotlib error** — `viz/visualizer.py` and `art/lorenz_portrait.py` both call `matplotlib.use("Agg")` before any pyplot import. Confirm those lines are present.
- **Antivirus flags the .exe** — common with PyInstaller. The bundle is unsigned; submit a sample to your vendor or sign with `signtool` if distributing.
