# -*- coding: utf-8 -*-
"""probe_palette_fix_verify.py -- 真机验证：调色板快照修复后,
Fusion 暗色 ↔ 浅色 方案切换不再残留深色背景。

复现路径（修复前实测卡死）：
  _apply_app_style("fusion") 显式 setPalette(Window=#353535)
  → _apply_color_scheme("light") → Qt 不再刷新被冻住的调色板
  → 浅色模式仍是 #353535。
修复：__init__ 捕获亮/暗快照,所有 setPalette 从快照重建。
"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "windows"

tmpdir = tempfile.mkdtemp(prefix="libjxl-probe-fix-")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QPalette, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmpdir)
QSettings.setDefaultFormat(QSettings.IniFormat)

app = QApplication(sys.argv[:1])

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from libjxl_gui import main_window as mw  # noqa: E402

failures = []


def check(label, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (tag, label, detail))
    if not cond:
        failures.append(label)


def window_hex():
    return QApplication.palette().color(QPalette.Window).name().upper()


win = mw.MainWindow()
try:
    # 用 Fusion 主题走完整路径（用户存储的 theme 也可能是 fusion）
    win._apply_theme("fusion")
    print("startup-ish: scheme=%s Window=%s" % (
        mw._effective_scheme_key(), window_hex()))

    # 强制暗色 → 应挂 #353535 覆盖
    win._apply_color_scheme("dark")
    check("Fusion+暗色 Window=#353535", window_hex() == "#353535",
          "实际 %s" % window_hex())
    check("暗色 pane QSS 已挂", "434343" in win.tabs.styleSheet(),
          win.tabs.styleSheet()[:60])

    # 切浅色 → 全局背景必须回浅色、pane QSS 必须摘掉（修复点）
    win._apply_color_scheme("light")
    hex_light = window_hex()
    check("切浅色后 Window 回浅色", int(hex_light[1:3], 16) > 128,
          "实际 %s" % hex_light)
    check("浅色 pane QSS 已摘", win.tabs.styleSheet() == "",
          repr(win.tabs.styleSheet()[:60]))

    # 切回暗色 → 覆盖恢复
    win._apply_color_scheme("dark")
    check("切回暗色 Window=#353535", window_hex() == "#353535",
          "实际 %s" % window_hex())
    check("暗色 pane QSS 恢复", "434343" in win.tabs.styleSheet(),
          win.tabs.styleSheet()[:60])

    # 原生主题往返：方案切换后原生底色也应跟手
    win._apply_color_scheme("light")
    win._apply_theme("native_noflicker_proto")
    hex_native_light = window_hex()
    check("原生主题浅色底色为浅", int(hex_native_light[1:3], 16) > 128,
          "实际 %s" % hex_native_light)
    win._apply_color_scheme("dark")
    win._apply_theme("native_noflicker_proto")
    hex_native_dark = window_hex()
    check("原生主题暗色底色为暗", int(hex_native_dark[1:3], 16) < 128,
          "实际 %s" % hex_native_dark)
finally:
    win.deleteLater()
    del win
    app.processEvents()

print("RESULT:", "ALL PASS" if not failures else "FAILED: %s" % failures)
sys.exit(0 if not failures else 1)
