# -*- coding: utf-8 -*-
"""回归测试：每次开始转换时，状态页日志自动滚到底部。

背景：用户在上一批转换中向上翻看历史后，日志可视区会停在中段；新批次打出的
分隔线与「开始转换」因此不可见。appendPlainText 只在光标原本贴底时才跟随滚动，
无法可靠复位，故在 _on_convert 里显式调用 _scroll_log_to_bottom()。

覆盖：
  1. _scroll_log_to_bottom() 把滚动条拉到最大值（能从顶部/中段复位到底部）；
  2. 可重复复位：连续调用每次都回到最底；
  3. 调用点在 worker 启动之前：走完 _on_convert 前置校验后，spy 恰好被调 1 次，
     且滚动条已到底。
"""

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter-test-autoscroll")
_tmp_settings_dir = tempfile.mkdtemp(prefix="jxlforge_test_autoscroll_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from jxlforge import converter                     # noqa: E402
from jxlforge.main_window import MainWindow, ConvertWorker  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

total = 0
failures = []


def check(name, cond, detail=""):
    global total
    total += 1
    if cond:
        print("PASS  %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s  %s" % (name, detail))


def fresh_window():
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    w.tabs.setCurrentWidget(w.status_tab)
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


def fill_log(w, n=300):
    w.log_edit.clear()
    for i in range(n):
        w.log_edit.appendPlainText("历史日志行 %04d" % i)
    for _ in range(2):
        QApplication.instance().processEvents()


def sb_value(w):
    return w.log_edit.verticalScrollBar().value()


def sb_max(w):
    return w.log_edit.verticalScrollBar().maximum()


# --- 1. 单元：复位到底部 ----------------------------------------------------
w = fresh_window()
fill_log(w)
check("日志可滚动（maximum > 0）", sb_max(w) > 0, "maximum=%d" % sb_max(w))

w.log_edit.verticalScrollBar().setValue(0)
QApplication.instance().processEvents()
check("先把滚动条拉到顶部", sb_value(w) == 0, "value=%d" % sb_value(w))

w._scroll_log_to_bottom()
QApplication.instance().processEvents()
check("_scroll_log_to_bottom 后回到最底部",
      sb_value(w) == sb_max(w) and sb_value(w) > 0,
      "value=%d maximum=%d" % (sb_value(w), sb_max(w)))

# 中段位置也要能复位
sb = w.log_edit.verticalScrollBar()
sb.setValue(max(0, (sb.maximum() // 2)))
QApplication.instance().processEvents()
mid = sb_value(w)
w._scroll_log_to_bottom()
QApplication.instance().processEvents()
check("从中间位置也能复位到底部",
      mid < sb_max(w) and sb_value(w) == sb_max(w),
      "mid=%d maximum=%d" % (mid, sb_max(w)))

# 连续调用可靠：多来几轮复位都贴底
ok_repeat = True
for _ in range(3):
    sb.setValue(0)
    w._scroll_log_to_bottom()
    if sb_value(w) != sb_max(w):
        ok_repeat = False
        break
check("连续复位 3 次均贴底", ok_repeat)

# --- 2. 集成：_on_convert 会调用复位（且发生在 worker 启动前） -------------
tmp = tempfile.mkdtemp(prefix="jxlforge_autoscroll_")
src = os.path.join(tmp, "in.png")
open(src, "wb").close()

calls = {"n": 0}
orig_scroll = MainWindow._scroll_log_to_bottom


def spy(self):
    calls["n"] += 1
    return orig_scroll(self)


real_start = ConvertWorker.start
ConvertWorker.start = lambda self: None   # 不真跑线程
try:
    with mock.patch.object(converter, "check_tools",
                           return_value={"cjxl": True, "djxl": True}):
        w2 = fresh_window()
        fill_log(w2)
        w2.log_edit.verticalScrollBar().setValue(0)
        QApplication.instance().processEvents()
        before = sb_value(w2)
        w2.input_files = [src]

        MainWindow._scroll_log_to_bottom = spy
        w2._on_convert()
        QApplication.instance().processEvents()
finally:
    MainWindow._scroll_log_to_bottom = orig_scroll
    ConvertWorker.start = real_start

check("转换前滚动条不在底部（前置条件）",
      before == 0, "before=%d" % before)
check("_on_convert 触发了一次日志复位", calls["n"] == 1, "calls=%d" % calls["n"])
check("复位发生在 worker 启动前（worker 已建且未跑）",
      w2._convert_worker is not None)
check("_on_convert 后滚动条已到底",
      sb_value(w2) == sb_max(w2) and sb_value(w2) > 0,
      "value=%d maximum=%d" % (sb_value(w2), sb_max(w2)))

print("")
if failures:
    print("FAILED: %d/%d" % (len(failures), total))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK (%d/%d)" % (total, total))
