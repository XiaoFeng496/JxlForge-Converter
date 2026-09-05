# -*- coding: utf-8 -*-
"""量动作页预览工具栏各按钮的宽度：固有需求 vs 实际分配到的宽度（是否被裁切）。

用 setFixedSize(880, 640) 复现默认窗口；offscreen 平台下窗口不会被限制。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp())
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")

_s = QSettings()
_s.beginGroup("appearance")
_s.setValue("language", "en_US")
_s.endGroup()
_s.sync()

from libjxl_gui import i18n
i18n.set_language("en_US")

from libjxl_gui.main_window import MainWindow
from PySide6.QtGui import QFontMetrics


def measure(lang):
    _s.beginGroup("appearance")
    _s.setValue("language", lang)
    _s.endGroup()
    _s.sync()
    i18n.set_language(lang)

    app = QApplication.instance() or QApplication(["-platform", "offscreen"])
    w = MainWindow()
    w._env_refreshed = True
    w.setFixedSize(880, 640)
    w.show()
    for _ in range(40):
        app.processEvents()

    tabs = w.tabs
    idx = next(i for i in range(tabs.count())
               if tabs.tabText(i) in ("Actions", "动作"))
    tabs.setCurrentIndex(idx)
    for _ in range(40):
        app.processEvents()

    btns = [
        ("zoom_in", w.zoom_in_button),
        ("zoom_out", w.zoom_out_button),
        ("1:1", w.zoom_actual_button),
        ("fit", w.zoom_fit_button),
        ("original", w.show_original_button),
    ]
    fm = QFontMetrics(w.zoom_fit_button.font())
    print("\n" + "=" * 72)
    print("语言 = %s" % lang)
    print("=" * 72)
    print("%-10s %-22s %8s %8s %6s  %s"
          % ("控件", "文案", "需求", "实得", "差", "状态"))
    total_need = 0
    total_got = 0
    for name, b in btns:
        need = b.sizeHint().width()
        got = b.width()
        txt = b.text()
        tw = fm.horizontalAdvance(txt)
        total_need += need
        total_got += got
        state = "OK" if got >= need else "裁切"
        print("%-10s %-22s %8d %8d %6d  %s"
              % (name, repr(txt), need, got, got - need, state))
    print("-" * 72)
    print("工具栏合计 需求 %d  实得 %d  差 %d" % (total_need, total_got,
                                                 total_got - total_need))

    row = w.zoom_fit_button.parentWidget().layout()
    print("pbar 可用宽 = %d, 间距 = %d, 项数 = %d"
          % (row.geometry().width(), row.spacing(), row.count()))
    return w


measure("zh_CN")
measure("en_US")
