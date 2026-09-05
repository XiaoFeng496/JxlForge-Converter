# -*- coding: utf-8 -*-
"""Application entry point: python -m libjxl_gui"""

import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import i18n
from .main_window import MainWindow, _LANGUAGE_DEFAULT, _LANGUAGE_COMBO_ORDER


def _apply_persisted_language():
    """Load the persisted language and install it **before** the UI is built.

    必须在构造 MainWindow 之前：界面文本是在构建时从字典取定的，之后再改
    字典只会让新旧语言混在一起（新弹出的控件是英文、已存在的是中文）。
    这也是本项目选择「切语言重启生效」的原因——实时刷新所有控件既容易漏，
    又要额外缓存原文。

    偏好可能含哨兵（"follow_system" / "zh_TW" 占位），先用 i18n.resolve_language()
    解析成可加载的有效代码（随系统选 / 非已有语言回落 English / 占位回落中文），
    再 set_language。返回解析后的有效代码。
    """
    settings = QSettings()
    settings.beginGroup("appearance")
    code = settings.value("language", _LANGUAGE_DEFAULT)
    settings.endGroup()
    if code not in _LANGUAGE_COMBO_ORDER:
        code = _LANGUAGE_DEFAULT
    effective = i18n.resolve_language(code)
    i18n.set_language(effective)
    return effective


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
    _apply_persisted_language()
    window = MainWindow()
    window._app_start = app_start
    # 真实启动：允许「首次启动自动校准大图阈值」（headless 测试不会置此标志，
    # 避免测试期间触发耗时的 cjxl 基准测量）。
    window._auto_calibrate_enabled = True
    window.show()
    return app.exec()


if __name__ == "__main__":
    run()
