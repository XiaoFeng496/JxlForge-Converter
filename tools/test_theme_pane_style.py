# -*- coding: utf-8 -*-
"""Fusion + 暗色配色覆盖（全局背景 + tab 面板）的回归测试 + 主题默认值/顺序。

覆盖四项改动：
  1. Fusion + 暗色下：
     - QPalette::Window 提亮为 FUSION_DARK_WINDOW_BG（#353535，全局背景）；
     - QTabWidget 面板（tab 区域）经样式表钉为 FUSION_DARK_TAB_PANE_BG
       （#434343），切走主题 / 切亮色后样式表清空。
     亮色 / 非 Fusion 主题两者都不动。
  2. 默认主题 = DEFAULT_THEME（原生（NoFlicker框））。
  3. 主题下拉顺序：原生（NoFlicker框）在原生（Fusion框）之前。
  4. 原生绘制主题（原生 / 原生（NoFlicker框））的一键 6×3 额外高度
     由 test_output_settings.py Group 15 覆盖，此处不重复。

⚠️ 平台限制（详见 main_window.py 的 FUSION_DARK_* 常量块注释）：
   offscreen 下「反复清/挂 tabs QSS + 切主题」几轮后 view.setStyleSheet 段错误
   （真机 windows 平台 8 轮交替稳定）。本测试因此最多跑 **2 轮**完整
   _apply_theme，其余分支用 _apply_tab_pane_style / _apply_app_style 直调覆盖；
   pane 像素（#434343）与多轮稳定性由真机探针验证。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QPalette, QColor

_app = QApplication.instance() or QApplication(sys.argv)
# Mirror __main__.run(): persist to a .ini file (not the registry) so running
# these tests doesn't write into the user's Windows registry.
QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini
# （%APPDATA%\JxlForge\JxlForge-Converter.ini）与受保护的 big_image_floor_px。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_pane_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

import jxlforge.main_window as mw
from jxlforge.main_window import (
    MainWindow, DEFAULT_THEME, _THEME_ORDER, _THEME_LABELS,
    FUSION_DARK_WINDOW_BG, FUSION_DARK_TAB_PANE_BG, FUSION_DARK_TAB_PANE_BORDER,
)

total = 0
failures = []


def check(name, cond):
    global total
    total += 1
    if cond:
        print("PASS %s" % name)
    else:
        failures.append(name)
        print("FAIL %s" % name)


# --- Group 1: 默认主题与下拉顺序 -------------------------------------------
check("默认主题 = 原生（NoFlicker框）",
      mw._APP_THEME == DEFAULT_THEME == "native_noflicker_proto")
check("主题顺序：原生（NoFlicker框）在原生（Fusion框）之前",
      _THEME_ORDER.index("native_noflicker_proto")
      < _THEME_ORDER.index("native_noflicker"))
check("主题标签齐全（4 项）",
      len(_THEME_ORDER) == 4 and set(_THEME_ORDER) == set(_THEME_LABELS))

# --- Group 2: Fusion + 暗色的配色覆盖 --------------------------------------
_orig_is_dark = mw._is_dark_palette
_orig_theme = mw._APP_THEME
try:
    mw._is_dark_palette = lambda pal=None: True   # offscreen 无暗色，模拟之
    win = MainWindow()
    # 默认主题（原生（NoFlicker框））下：无样式表覆盖
    check("默认主题下 tab 面板无样式表", win.tabs.styleSheet() == "")

    # 完整链第 1 轮（共 2 轮上限，见文件头平台限制说明）
    win._apply_theme("fusion")
    qss = win.tabs.styleSheet()
    check("Fusion + 暗色：面板 QSS 含目标背景色", FUSION_DARK_TAB_PANE_BG in qss)
    check("Fusion + 暗色：面板 QSS 带 border（不带则 Qt 忽略 background）",
          "border" in qss and FUSION_DARK_TAB_PANE_BORDER in qss)
    check("Fusion + 暗色：全局背景 Window 角色已提亮",
          QApplication.palette().color(QPalette.Window) == QColor(FUSION_DARK_WINDOW_BG))

    # 完整链第 2 轮（最后一轮完整链）
    win._apply_theme("native")
    check("切回原生主题：面板样式表清空", win.tabs.styleSheet() == "")

    # 以下分支用直调覆盖，不再跑完整 _apply_theme（offscreen 段错误规避）
    win._apply_tab_pane_style("fusion")
    check("再切回 Fusion（直调）：面板样式表重新挂上",
          FUSION_DARK_TAB_PANE_BG in win.tabs.styleSheet())

    mw._is_dark_palette = lambda pal=None: False  # 模拟亮色
    win._apply_tab_pane_style("fusion")
    check("Fusion + 亮色（直调）：不覆盖面板", win.tabs.styleSheet() == "")

    win._apply_app_style("fusion")
    check("Fusion + 亮色（直调）：全局背景未被改",
          QApplication.palette().color(QPalette.Window) != QColor(FUSION_DARK_WINDOW_BG))
    win.close()
finally:
    mw._is_dark_palette = _orig_is_dark
    mw.set_app_theme(_orig_theme)

print("\n%d/%d checks passed" % (total - len(failures), total))
print("ALL_OK" if not failures else "FAILED: " + ", ".join(failures))
sys.exit(1 if failures else 0)
