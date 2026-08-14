# -*- coding: utf-8 -*-
"""Application entry point: python -m libjxl_gui"""

import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def run():
    """Create the QApplication and show the main window."""
    # Persist settings to a portable .ini file instead of the Windows registry.
    # Must be set before any QSettings object is constructed.
    QSettings.setDefaultFormat(QSettings.IniFormat)

    app_start = time.perf_counter()  # 进程启动时刻（白屏基准用）
    app = QApplication([])
    # Stable identity for QSettings so persisted data survives restarts.
    app.setOrganizationName("libjxl")
    app.setApplicationName("libjxl-gui")
    window = MainWindow()
    window._app_start = app_start
    window.show()
    return app.exec()


if __name__ == "__main__":
    run()
