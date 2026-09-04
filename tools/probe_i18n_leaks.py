# -*- coding: utf-8 -*-
"""英文界面中文泄漏扫描：en_US 模式下遍历整个控件树，报告所有仍含汉字的文本。

与 probe_i18n_en_ui.py（只截图不断言）互补——这里做程序化断言，给「阶段 3
回填到底翻干净没有」一个确定答案。

用法::

    python tools/probe_i18n_leaks.py

判定：
- 含汉字的文本 → 候选泄漏
- 命中允许名单（语言自称名等刻意保留中文的）→ 排除
- 其余 → 真泄漏，列出来人工修
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QSettings, QCoreApplication, QTimer
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QAbstractButton, QGroupBox,
    QTabWidget, QComboBox, QLineEdit, QTableWidget, QHeaderView,
    QMenu, QMenuBar,
)
from PySide6.QtGui import QFontDatabase, QFont

QSettings.setDefaultFormat(QSettings.IniFormat)
_tmp = tempfile.mkdtemp(prefix="libjxl_probe_leak_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")

from libjxl_gui import i18n
from libjxl_gui.__main__ import _apply_persisted_language
from libjxl_gui.main_window import MainWindow

# ---- 字体（offscreen 默认 0 字体，载入系统字体只为控件能正常建文本） ----
app = QApplication.instance() or QApplication(["-platform", "offscreen"])
for fp in (r"C:\Windows\Fonts\msyh.ttc",
           r"C:\Windows\Fonts\segoeui.ttf",
           r"C:\Windows\Fonts\arial.ttf"):
    if os.path.isfile(fp):
        QFontDatabase.addApplicationFont(fp)
app.setFont(QFont("Microsoft YaHei UI", 10))

# ---- 模拟用户在设置页选 English 并重启 ----
s = QSettings()
s.beginGroup("appearance")
s.setValue("language", "en_US")
s.endGroup()
s.sync()
code = _apply_persisted_language()
print("applied language =", code)

w = MainWindow()
w._env_refreshed = True
w.resize(1180, 820)
w.show()
for _ in range(40):
    app.processEvents()

# 语言下拉里显示的是「自称名」（endonym），刻意保留中文，不算泄漏
ALLOW = {"简体中文", "English"}


def has_cjk(t):
    return any("\u4e00" <= ch <= "\u9fff" for ch in t)


def collect(widget, out):
    # 窗口标题
    try:
        wt = widget.windowTitle()
        if wt and has_cjk(wt):
            out.append(("windowTitle", widget.__class__.__name__, wt))
    except Exception:
        pass
    # toolTip / statusTip / whatsThis
    for meth in ("toolTip", "statusTip", "whatsThis"):
        try:
            v = getattr(widget, meth)()
            if v and has_cjk(v):
                out.append((meth, widget.__class__.__name__, v))
        except Exception:
            pass
    # 常见文本承载控件
    if isinstance(widget, QLabel):
        t = widget.text()
        if t and has_cjk(t):
            out.append(("text", "QLabel", t))
    if isinstance(widget, QAbstractButton):
        t = widget.text()
        if t and has_cjk(t):
            out.append(("text", widget.__class__.__name__, t))
    if isinstance(widget, QGroupBox):
        t = widget.title()
        if t and has_cjk(t):
            out.append(("title", "QGroupBox", t))
    if isinstance(widget, QTabWidget):
        for i in range(widget.count()):
            t = widget.tabText(i)
            if t and has_cjk(t):
                out.append(("tabText[%d]" % i, "QTabWidget", t))
    if isinstance(widget, QComboBox):
        for i in range(widget.count()):
            t = widget.itemText(i)
            if t and has_cjk(t):
                out.append(("itemText[%d]" % i, "QComboBox", t))
        try:
            ph = widget.placeholderText()
            if ph and has_cjk(ph):
                out.append(("placeholderText", "QComboBox", ph))
        except Exception:
            pass
    if isinstance(widget, QLineEdit):
        try:
            ph = widget.placeholderText()
            if ph and has_cjk(ph):
                out.append(("placeholderText", "QLineEdit", ph))
        except Exception:
            pass
    if isinstance(widget, QTableWidget):
        for c in range(widget.columnCount()):
            hh = widget.horizontalHeaderItem(c)
            if hh and has_cjk(hh.text()):
                out.append(("hHeader[%d]" % c, "QTableWidget", hh.text()))


def walk_menus(widget, out):
    for m in widget.findChildren(QMenu):
        for act in m.actions():
            t = act.text()
            if t and has_cjk(t):
                out.append(("menuAction", m.objectName() or "QMenu", t))
    mb = widget.menuBar()
    if mb:
        for act in mb.actions():
            t = act.text()
            if t and has_cjk(t):
                out.append(("menuBar", "QMenuBar", t))


found = []
collect(w, found)
for ww in QApplication.allWidgets():
    collect(ww, found)
walk_menus(w, found)

# 去重
seen = set()
real = []
for kind, cls, txt in found:
    key = (kind, cls, txt)
    if key in seen:
        continue
    seen.add(key)
    if txt in ALLOW:
        continue
    real.append((kind, cls, txt))

print("\n=== 含汉字文本候选数：%d（排除允许名单后 %d） ===" % (len(found), len(real)))
if not real:
    print("✅ 未发现中文泄漏（排除语言自称名后）")
else:
    for kind, cls, txt in real:
        print("  [%-14s %-14s] %s" % (cls, kind, txt))

sys.exit(1 if real else 0)
