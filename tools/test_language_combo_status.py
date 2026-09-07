# -*- coding: utf-8 -*-
"""回归测试：语言下拉切档后，状态栏消息的哨兵项与模板都走当前界面语言。

锁住的不变量
============
1. **模板串本身**始终走 ``i18n.t``（之前已经走，没退化）。
2. **哨兵项 ``follow_system``** 的 label 必须随界面语言翻译——它是功能描述
   （「自动跟随系统 UI 语言」），不是语言自称名。英文界面下应显示
   ``Follow system``，与下拉里看到的选项文字一致。
3. **真实语言项**的 label 按约定保持自称名原样——中文界面下
   ``English`` 不应被翻成「英语」/``English``，英文界面下
   ``简体中文`` 不应被翻成 ``Simplified Chinese``，否则用户认不出。

历史 bug
========
旧实现 ``main_window.py:_on_language_combo_changed`` 直接从 ``_LANGUAGE_LABELS``
取中文源串填入 ``%s``，**哨兵项 ``follow_system`` 也漏走了 i18n**——结果就是：
英文界面下拉显示 ``Follow system``，状态栏却显示 ``Language switched to 跟随系统``，
不一致（用户 2026-09-07 真机截图反馈）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

# offscreen + QSettings 隔离：测试期间不污染真实 ini。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from libjxl_gui import i18n  # noqa: E402
from libjxl_gui import main_window as mw  # noqa: E402

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
    """测试期间把 QSettings 重定向到临时目录，结束后还原（不影响真实 ini）。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="libjxl-gui-test-lang-")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    return tmp


def _patch_status_capture(window):
    """替换 ``statusBar().showMessage`` 为列表追加，方便断言。"""
    captured = []
    window._captured_status_msgs = captured  # 给 _trigger_change 取用
    sb = window.statusBar()
    sb.showMessage = lambda text, timeout=0: captured.append(text)
    return captured


def _build_window():
    """最小化建窗：不要校准、不要真实尺寸计算。"""
    win = mw.MainWindow()
    win._auto_calibrate_enabled = False  # 跳过 cjxl 基准测量
    return win


def _trigger_change(win, code):
    """强制触发 currentIndexChanged：先选个别的，再选回目标 code。
    返回 statusBar 捕获的最后一条消息（目标 code 对应的那条）。"""
    # 先选个与目标不同的 code，保证连续两次 setCurrentIndex 都触发信号。
    other = "zh_CN" if code != "zh_CN" else "en_US"
    win.language_combo.setCurrentIndex(
        win.language_combo.findData(other))
    win.language_combo.setCurrentIndex(
        win.language_combo.findData(code))
    # 信号是直连，事件循环里没有排队，processEvents 走一次即可。
    QApplication.processEvents()
    return win._captured_status_msgs[-1]


def test_follow_system_translates_with_ui(app):
    """英文界面下，follow_system 哨兵项在状态栏里也应是英文。"""
    i18n.set_language("en_US")
    win = _build_window()
    _patch_status_capture(win)
    msg = _trigger_change(win, "follow_system")
    check("英文界面 follow_system 走翻译",
          "Language switched to Follow system" in msg,
          detail="实际 statusBar=%r" % msg)
    check("英文界面 follow_system 不再泄漏中文源串",
          "跟随系统" not in msg,
          detail="实际 statusBar=%r" % msg)


def test_follow_system_chinese_unchanged(app):
    """中文界面下 follow_system 显示「跟随系统」（与下拉一致）。"""
    i18n.set_language("zh_CN")
    win = _build_window()
    _patch_status_capture(win)
    msg = _trigger_change(win, "follow_system")
    check("中文界面 follow_system 哨兵项仍是「跟随系统」",
          "语言已切换为跟随系统" in msg,
          detail="实际 statusBar=%r" % msg)


def test_real_language_self_name_preserved(app):
    """真实语言项（简体中文 / English）按约定保持自称名原样，
    模板串本身随语言翻译。"""
    # 英文界面下选简体中文：模板应是英文、label 仍是「简体中文」。
    i18n.set_language("en_US")
    win = _build_window()
    _patch_status_capture(win)
    msg = _trigger_change(win, "zh_CN")
    check("英文界面 zh_CN：模板走翻译",
          "Language switched to" in msg,
          detail="实际 statusBar=%r" % msg)
    check("英文界面 zh_CN：label 仍是自称名「简体中文」（不被译成 Simplified Chinese）",
          "Language switched to 简体中文" in msg,
          detail="实际 statusBar=%r" % msg)


def main():
    _isolate_qsettings()
    app = QApplication.instance() or QApplication([])

    # 默认走 zh_CN 让窗口 UI 顺利构造；下面的用例按需切语言。
    i18n.set_language("zh_CN")
    test_follow_system_translates_with_ui(app)
    test_follow_system_chinese_unchanged(app)
    test_real_language_self_name_preserved(app)

    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()