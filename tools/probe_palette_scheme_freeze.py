# -*- coding: utf-8 -*-
"""probe_palette_scheme_freeze.py -- 真机探针：显式 setPalette 是否冻住
QStyleHints::setColorScheme 的调色板刷新。

背景：_apply_app_style("fusion") 里 QApplication.setPalette(pal)（哪怕只改
Accent）会显式设置应用调色板；本次改动又往这个显式调色板里写入了暗色
QPalette::Window (#353535)。怀疑 Qt6 在「应用调色板被显式设置」后，
setColorScheme(Light) 不再刷新它 → 浅色模式残留深色背景。

探针步骤（真机 windows 平台，无窗口，QSettings 用临时目录隔离）：
  A. setColorScheme(Dark)          → 调色板应变暗
  B. setPalette(显式, Window=#353535)
  C. setColorScheme(Light)         → 调色板是否回到浅色？
     若仍是 #353535 → 冻结假设成立。
对照：不显式 setPalette 的场景里 C 是否正常回浅色。
"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "windows"

tmpdir = tempfile.mkdtemp(prefix="libjxl-probe-pal-")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QPalette, QColor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmpdir)
QSettings.setDefaultFormat(QSettings.IniFormat)

app = QApplication(sys.argv[:1])


def window_color():
    return QApplication.palette().color(QPalette.Window).name()


def scheme_name():
    cs = QGuiApplication.styleHints().colorScheme()
    return {Qt.ColorScheme.Unknown: "Unknown",
            Qt.ColorScheme.Light: "Light",
            Qt.ColorScheme.Dark: "Dark"}.get(cs, str(cs))


print("== 对照组：不显式 setPalette ==")
print("startup        scheme=%-6s Window=%s" % (scheme_name(), window_color()))
QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
app.processEvents()
print("setColor Dark  scheme=%-6s Window=%s" % (scheme_name(), window_color()))
QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Light)
app.processEvents()
print("setColor Light scheme=%-6s Window=%s" % (scheme_name(), window_color()))

print("== 实验组：先显式 setPalette（模拟 _apply_app_style fusion 暗色）==")
QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
app.processEvents()
pal = QApplication.palette()
pal.setColor(QPalette.Accent, QColor(0, 0, 0))
pal.setColor(QPalette.Window, QColor("#353535"))
QApplication.setPalette(pal)
print("explicit dark  scheme=%-6s Window=%s" % (scheme_name(), window_color()))
QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Light)
app.processEvents()
print("setColor Light scheme=%-6s Window=%s  <- 若仍是 #353535 即冻结" % (
    scheme_name(), window_color()))
QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
app.processEvents()
print("setColor Dark  scheme=%-6s Window=%s" % (scheme_name(), window_color()))
