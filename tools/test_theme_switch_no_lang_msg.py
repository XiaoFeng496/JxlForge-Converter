# -*- coding: utf-8 -*-
"""回归测试：切换「控件样式」时，状态栏不得误报语言 / 颜色方案已切换。

历史 bug
========
``_remeasure_regular_combos`` 挂在 ``theme_combo.currentIndexChanged`` 上，
切样式时会临时把 ``language_combo`` / ``color_scheme_combo`` 的索引切到「最长项」
量宽度、再切回原项。这两个 ``setCurrentIndex`` 会触发对应下拉的
``currentIndexChanged``，从而误弹「语言已切换为…」/「颜色方案已切换为…」
提示（用户 2026-09-09 真机反馈：切控件样式却报「语言已切换为跟随系统，
重启程序后生效。」）。

修复（main_window.py ``_remeasure_regular_combos``）：量宽度期间对这两个
combo 用 ``blockSignals(True)`` 屏蔽信号，量完恢复。

锁住的不变量
============
1. 切控件样式后，状态栏**必须**出现「主题已切换为：…」（合法提示）。
2. 切控件样式后，状态栏**不得**出现「语言已切换为…」。
3. 切控件样式后，状态栏**不得**出现「颜色方案已切换为…」。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jxlforge import i18n  # noqa: E402
from jxlforge import main_window as mw  # noqa: E402

PASS = 0
FAIL = 0


def check(label_, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   %s" % label_)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (label_, detail))


def _isolate_qsettings():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="JxlForge-Converter-test-theme-")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    return tmp


def _patch_status_capture(window):
    captured = []
    window._captured_status_msgs = captured
    window.statusBar().showMessage = lambda text, timeout=0: captured.append(text)
    return captured


def _build_window():
    win = mw.MainWindow()
    win._auto_calibrate_enabled = False
    return win


def _switch_theme(win, target_key):
    """强制触发 theme_combo 的 currentIndexChanged（先选别的再选回目标，
    保证信号确实发出）。返回捕获的全部状态栏消息。"""
    other = "fusion" if target_key != "fusion" else "native"
    win.theme_combo.setCurrentIndex(win.theme_combo.findData(other))
    win.theme_combo.setCurrentIndex(win.theme_combo.findData(target_key))
    QApplication.processEvents()
    return win._captured_status_msgs


def test_theme_switch_no_lang_or_scheme_msg(app):
    """切到 fusion 主题后，不应再误报语言 / 颜色方案已切换。"""
    i18n.set_language("zh_CN")
    win = _build_window()
    captured = _patch_status_capture(win)

    cur = win.theme_combo.currentData()
    target = "fusion" if cur != "fusion" else "native"
    msgs = _switch_theme(win, target)

    check("切控件样式后弹「主题已切换为」合法提示",
          any("主题已切换为" in m for m in msgs),
          detail="实际 statusBar=%r" % msgs)
    check("切控件样式后误报「语言已切换为」已消失",
          not any("语言已切换为" in m for m in msgs),
          detail="实际 statusBar=%r" % msgs)
    check("切控件样式后误报「颜色方案已切换为」已消失",
          not any("颜色方案已切换为" in m for m in msgs),
          detail="实际 statusBar=%r" % msgs)


def main():
    _isolate_qsettings()
    app = QApplication.instance() or QApplication([])
    i18n.set_language("zh_CN")
    test_theme_switch_no_lang_or_scheme_msg(app)

    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
