# -*- coding: utf-8 -*-
"""Application entry point: python -m jxlforge"""

import os
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import i18n
from .main_window import MainWindow, _LANGUAGE_DEFAULT, _LANGUAGE_COMBO_ORDER

# 更名前的 QSettings 标识。项目从 libjxl_GUI 改名为 JxlForge Converter 后
# org/app 变了，ini 路径随之改变；老用户的配置若不做迁移就会静默丢失。
_LEGACY_SETTINGS_ORG = "libjxl"
_LEGACY_SETTINGS_APP = "libjxl-gui"
# 哨兵键：迁移只做一次。放在独立的 _meta 组，避免和真正的设置混在一起。
_SETTINGS_MIGRATED_KEY = "_meta/migrated_from_libjxl"


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


def _migrate_legacy_settings():
    """把旧标识下的配置一次性搬到新标识，老用户的设置不因改名而丢失。

    要保的不只是主题和窗口几何，还有校准出来的 ``big_image_floor_px``
    （跑一次基准要几分钟，丢了只能重跑）。

    只在旧 ini 确实存在时才搬；搬完写哨兵，之后每次启动直接返回。
    旧文件保留不删 —— 迁移是只读复制，出错也不至于两头都没有。
    """
    current = QSettings()
    already = str(current.value(_SETTINGS_MIGRATED_KEY, "")).lower()
    if already in ("true", "1"):
        return

    legacy = QSettings(QSettings.IniFormat, QSettings.UserScope,
                       _LEGACY_SETTINGS_ORG, _LEGACY_SETTINGS_APP)
    legacy_path = legacy.fileName()
    if not legacy_path or not os.path.exists(legacy_path):
        # 新装用户，或测试环境（UserScope 已被指向临时目录）。
        current.setValue(_SETTINGS_MIGRATED_KEY, True)
        current.sync()
        return

    for key in legacy.allKeys():
        current.setValue(key, legacy.value(key))
    current.setValue(_SETTINGS_MIGRATED_KEY, True)
    current.sync()


def run():
    """Create the QApplication and show the main window."""
    # Persist settings to a portable .ini file instead of the Windows registry.
    # Must be set before any QSettings object is constructed.
    QSettings.setDefaultFormat(QSettings.IniFormat)

    app_start = time.perf_counter()  # 进程启动时刻（白屏基准用）
    app = QApplication([])
    # Stable identity for QSettings so persisted data survives restarts.
    app.setOrganizationName("JxlForge")
    app.setApplicationName("JxlForge-Converter")
    # 必须在读任何设置之前：语言偏好、主题、校准值都在旧 ini 里。
    _migrate_legacy_settings()
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
