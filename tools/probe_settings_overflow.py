# -*- coding: utf-8 -*-
"""诊断设置页在 en_US 下的「向右膨胀 / 右侧截断」。

参照 tools/probe_output_overflow.py：offscreen 下窗口宽度受平台屏幕尺寸限制，
所以「可视宽」不可信，可靠的对比指标是内容固有宽度需求
inner.sizeHint().width() / minimumSizeHint().width()。

设置页结构：settings_tab(外层 QVBoxLayout) -> scroll(QScrollArea,
ScrollBarAsNeeded) -> inner(QVBoxLayout) -> [grid(2列) / 选项区 / 高级参数区]。

本脚本分解每个顶层区块、再钻进「主题」分组框（控件样式/语言所在），定位最宽的
下拉框，验证 en_US 下是否超宽、以及缩窄目标。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import QApplication, QTabWidget, QScrollArea, QGroupBox
from PySide6.QtGui import QFontDatabase, QFont

from libjxl_gui import i18n
from libjxl_gui.main_window import MainWindow


def make(lang):
    tmp = tempfile.mkdtemp(prefix="libjxl_probe_set_")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    s = QSettings()
    s.beginGroup("appearance")
    s.setValue("language", lang)
    s.endGroup()
    s.sync()
    i18n.set_language(lang)
    w = MainWindow()
    w._env_refreshed = True
    w.setFixedSize(880, 640)
    w.show()
    for _ in range(40):
        app.processEvents()
    return w


app = QApplication.instance() or QApplication(["-platform", "offscreen"])
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\segoeui.ttf",
           r"C:\Windows\Fonts\arial.ttf"):
    if os.path.isfile(fp):
        QFontDatabase.addApplicationFont(fp)
app.setFont(QFont("Microsoft YaHei UI", 10))


def find_settings_scroll(w):
    outer = w.settings_tab.layout()
    item = outer.itemAt(0)
    if item is not None and isinstance(item.widget(), QScrollArea):
        return item.widget()
    return None


def measure(lang):
    w = make(lang)
    tw = w.findChild(QTabWidget)
    for i in range(tw.count()):
        if tw.tabText(i) in ("Settings", "设置"):
            tw.setCurrentIndex(i)
            break
    for _ in range(30):
        app.processEvents()

    scroll = find_settings_scroll(w)
    inner = scroll.widget()
    hsb = scroll.horizontalScrollBar()
    vp_w = scroll.viewport().width()
    need = inner.sizeHint().width()
    overflow = hsb.maximum() > 0
    print("\n" + "=" * 74)
    print("语言 = %s   窗口 = %dx%d   viewport 可视宽 = %d"
          % (lang, w.width(), w.height(), vp_w))
    print("=" * 74)
    print("内容固有宽度需求 sizeHint=%d  minimumSizeHint=%d"
          % (need, inner.minimumSizeHint().width()))
    print("横向滚动条：%s   余量 = %d px"
          % ("【超宽·出滚动条】" if overflow else "不需要（未超宽）",
             vp_w - need))

    print("\n按顶层区块分解（区块单独所需宽度）：")
    lay = inner.layout()
    for i in range(lay.count()):
        item = lay.itemAt(i)
        if item is None:
            continue
        if item.widget() is not None:
            wd = item.widget()
            title = wd.title() if isinstance(wd, QGroupBox) else wd.__class__.__name__
            sh = wd.sizeHint().width()
            print("  %-34s %5d" % (title, sh))
            if isinstance(wd, QGroupBox):
                _drill_group(wd)
        elif item.layout() is not None:
            lo = item.layout()
            print("  %-34s %5d" % ("<layout>", lo.sizeHint().width()))
    return inner.sizeHint().width(), inner.minimumSizeHint().width()


def _drill_group(gb):
    lay = gb.layout()
    if lay is None:
        return
    for i in range(lay.count()):
        item = lay.itemAt(i)
        if item is None:
            continue
        if item.layout() is not None:
            sub = item.layout()
            print("        └─ 布局行 %-22s %5d" % ("", sub.sizeHint().width()))
            for j in range(sub.count()):
                it = sub.itemAt(j)
                if it is None:
                    continue
                if it.widget() is not None:
                    kw = it.widget()
                    nm = kw.title() if isinstance(kw, QGroupBox) else kw.__class__.__name__
                    print("             └─ %-26s %5d"
                          % (nm, kw.sizeHint().width()))


zh = measure("zh_CN")
en = measure("en_US")

print("\n" + "=" * 74)
print("结论：内容固有宽度需求  中文 %d  →  英文 %d   （+%d px, +%.0f%%）"
      % (zh[0], en[0], en[0] - zh[0],
         100.0 * (en[0] - zh[0]) / max(zh[0], 1)))
print("      minimumSizeHint   中文 %d  →  英文 %d   （+%d px）"
      % (zh[1], en[1], en[1] - zh[1]))
print("=" * 74)

# 兜底：窗口调窄时，出现的是横向滚动条（可滚动）而非截断
w = make("en_US")
_tw = w.findChild(QTabWidget)
for i in range(_tw.count()):
    if _tw.tabText(i) in ("Settings", "设置"):
        _tw.setCurrentIndex(i)
        break
for _ in range(30):
    app.processEvents()
print("\n--- 不同窗口宽度下的兜底检查（en_US）---")
for width in (880, 820, 760, 700, 640, 560):
    w.setFixedSize(width, 640)
    for _ in range(25):
        app.processEvents()
    sc = find_settings_scroll(w)
    hsb = sc.horizontalScrollBar()
    vp = sc.viewport().width()
    need = sc.widget().sizeHint().width()
    print("  窗口 %4d → 可视宽 %4d / 内容需求 %4d → %s"
          % (width, vp, need,
             "横向滚动条出现（可滚动查看，不截断）" if hsb.maximum() > 0
             else "未超宽"))
