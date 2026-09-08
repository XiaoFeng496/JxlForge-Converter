# -*- coding: utf-8 -*-
"""回归：添加动作不应强制让预览区适应窗口（除非是首次加载）。

背景：
- 用户反馈「添加动作时会强制让预览区图片适应窗口」，根因是
  ``_insert_action_item`` 在 render_preview 时调 ``_render_action_preview()``
  走了默认 fit=True，等效于「图源切换/首次加载」的语义，与「清空/删除/拖动
  参数」的 fit=False 不一致。
- 添加动作是「同图刷新」（源图不变、仅动作链多一步），应保留用户当前缩放；
  仅当此前没有任何预览图（首次加载）才 fit，让用户先看到完整图。
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from PySide6.QtWidgets import QApplication
from unittest import mock

import jxlforge.main_window as mw

app = QApplication.instance() or QApplication(sys.argv)

failures = []


def check(name, cond, extra=""):
    if cond:
        print("PASS:", name)
    else:
        failures.append(name)
        print("FAIL:", name, extra)


def _captured_fit(win):
    """触发一次添加动作，返回 _render_action_preview 被调用的 fit 实参。"""
    with mock.patch.object(win, "_render_action_preview") as m:
        win._add_action_item(
            {"type": "亮度", "params": {"factor": 1.2}, "enabled": True})
        args, kwargs = m.call_args
        return kwargs.get("fit", args[0] if args else True)


win = mw.MainWindow()

# --- 场景 A：已有预览图时添加动作 → 不强制 fit（保留缩放）---
# 预览渲染被 mock 掉，这里只需一个「非空对象」让 had_preview 为真即可。
win._preview_processed_pixmap = object()
fit_a = _captured_fit(win)
check("已有预览图添加动作 → fit=False（保留用户缩放）",
      fit_a is False, "fit=%r" % fit_a)

# --- 场景 B：首次加载（无预览图）添加动作 → 应 fit（先看完整图）---
win._preview_processed_pixmap = None
fit_b = _captured_fit(win)
check("首次加载（无预览图）添加动作 → fit=True（先看完整图）",
      fit_b is True, "fit=%r" % fit_b)

total = 2
print("\n%d/%d passed" % (total - len(failures), total))
sys.exit(1 if failures else 0)
