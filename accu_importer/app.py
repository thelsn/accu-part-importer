from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Accu Part Importer")
    app.setOrganizationName("Tiny Air")
    window = MainWindow()
    window.show()
    return app.exec()
