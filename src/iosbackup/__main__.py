"""Startpunkt: ``iosbackuplinux`` bzw. ``python -m iosbackup``."""

from __future__ import annotations

import signal
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_ID, APP_NAME, __version__
from .tools import INSTALL_HINT, missing_tools


def main() -> int:
    if "--version" in sys.argv:
        print(f"{APP_NAME} {__version__}")
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    missing = missing_tools()
    if missing:
        QMessageBox.critical(
            None, APP_NAME,
            "Folgende Programme von libimobiledevice fehlen:\n  "
            + ", ".join(missing)
            + f"\n\nBitte installieren mit:\n  {INSTALL_HINT}")
        return 1

    from .ui.mainwindow import MainWindow, app_icon

    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
