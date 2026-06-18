"""
app/main.py — FSC desktop application entry point.

Layout:
    ┌──────────┬─────────────────────────────────────────┐
    │ FSC      │                                         │
    │ ▍key forge                                         │
    │  encrypt │   <screen>                              │
    │  decrypt │                                         │
    │  manager │                                         │
    └──────────┴─────────────────────────────────────────┘

Phase 1: only KEY FORGE is functional. The other tabs show a placeholder.
"""

import os
import sys
import traceback
import datetime


# ── early exception hook ─────────────────────────────────────────────────────
# In a PyInstaller --windowed build, stderr is suppressed and Python
# tracebacks are swallowed by the bootloader's "Unhandled exception" dialog.
# Mirror every uncaught exception to a log file next to the .exe so build
# failures stay diagnosable in the field.
def _crash_log_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "fsc_crash.log")


def _excepthook(exc_type, exc, tb):
    try:
        with open(_crash_log_path(), "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(f"FSC crash {datetime.datetime.now().isoformat()}\n")
            f.write("=" * 70 + "\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
            f.write("\n\n")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _excepthook


# make repo root importable regardless of cwd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore    import Qt
from PySide6.QtGui     import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QLabel, QButtonGroup, QSizePolicy,
)

from app.screens.key_forge   import KeyForgeScreen
from app.screens.encrypt     import EncryptScreen
from app.screens.decrypt     import DecryptScreen
from app.screens.key_manager import KeyManagerScreen


# ── placeholder screen for tabs not yet implemented ───────────────────────────

class PlaceholderScreen(QWidget):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        title = QLabel(name.upper())
        title.setStyleSheet("color: #c1272d; font-size: 28px; "
                            "font-weight: bold; letter-spacing: 4px;")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("coming soon — Phase 2")
        sub.setStyleSheet("color: #555; font-style: italic; padding-top: 8px;")
        sub.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addStretch(1)


# ── sidebar ──────────────────────────────────────────────────────────────────

class Sidebar(QWidget):
    def __init__(self, on_select, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        self._on_select = on_select
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QLabel("FSC")
        brand.setObjectName("SidebarBrand")
        layout.addWidget(brand)

        tag = QLabel("FRACTAL SINGULARITY CIPHER")
        tag.setObjectName("SidebarTag")
        tag.setWordWrap(True)
        layout.addWidget(tag)

        # nav buttons (checkable, exclusive)
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self._buttons: list[QPushButton] = []
        for idx, name in enumerate(("KEY FORGE", "ENCRYPT", "DECRYPT", "KEY MANAGER")):
            btn = QPushButton(f"  {name}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            layout.addWidget(btn)
            self.btn_group.addButton(btn, idx)
            self._buttons.append(btn)

        # Qt's built-in group signal — no per-button lambda needed, so PySide6
        # cannot GC the slot. Fires once per click with the button's id.
        self.btn_group.idClicked.connect(self._on_select)

        self._buttons[0].setChecked(True)

        layout.addStretch(1)

        version = QLabel("v0.1.0  ·  Phase 1")
        version.setStyleSheet("color: #333; font-size: 10px; padding: 14px 18px;")
        layout.addWidget(version)

    def select(self, index: int):
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)


# ── main window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FSC — Fractal Singularity Cipher")
        self.resize(1200, 800)
        self.setMinimumSize(1000, 650)

        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # stack: KEY FORGE, ENCRYPT, DECRYPT, KEY MANAGER
        self.stack          = QStackedWidget()
        self.key_forge      = KeyForgeScreen()
        self.encrypt_screen = EncryptScreen()
        self.decrypt_screen = DecryptScreen()
        self.key_manager    = KeyManagerScreen()
        self.stack.addWidget(self.key_forge)
        self.stack.addWidget(self.encrypt_screen)
        self.stack.addWidget(self.decrypt_screen)
        self.stack.addWidget(self.key_manager)

        # refresh keys whenever a screen that needs them becomes visible
        self.stack.currentChanged.connect(self._on_screen_changed)

        self.sidebar = Sidebar(on_select=self.stack.setCurrentIndex)
        h.addWidget(self.sidebar)
        h.addWidget(self.stack, stretch=1)

        self.stack.setCurrentIndex(0)

    # rescan drives when entering a screen that lists keys
    def _on_screen_changed(self, index: int):
        w = self.stack.widget(index)
        if hasattr(w, "refresh_keys"):
            w.refresh_keys()


# ── entry point ──────────────────────────────────────────────────────────────

def _load_stylesheet() -> str:
    """Find app/style.qss in both source-tree and PyInstaller-frozen layouts."""
    candidates = [os.path.join(os.path.dirname(__file__), "style.qss")]
    # PyInstaller copies --add-data into sys._MEIPASS at runtime.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "app", "style.qss"))
        candidates.append(os.path.join(meipass, "style.qss"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            continue
    return ""


_SCREENS = {"forge": 0, "encrypt": 1, "decrypt": 2, "manager": 3}


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FSC")
    app.setOrganizationName("FSC")
    app.setStyle("Fusion")
    app.setStyleSheet(_load_stylesheet())

    # Optional: jump directly to a screen at startup, e.g.  py app/main.py decrypt
    start = 0
    for arg in sys.argv[1:]:
        if arg.lower() in _SCREENS:
            start = _SCREENS[arg.lower()]
            break

    win = MainWindow()
    win.stack.setCurrentIndex(start)
    win.sidebar.select(start)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
