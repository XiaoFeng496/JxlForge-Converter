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
    # 真实启动：允许「首次启动自动校准大图阈值」（headless 测试不会置此标志，
    # 避免测试期间触发耗时的 cjxl 基准测量）。
    window._auto_calibrate_enabled = True
    window.show()
    return app.exec()


if __name__ == "__main__":
    run()
