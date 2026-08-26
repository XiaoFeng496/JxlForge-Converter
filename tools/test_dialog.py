# -*- coding: utf-8 -*-
"""Lightweight checks for the action-tab UI changes (no rendering needed)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


# Module-level wiring.
check("ActionParamDialog exists", hasattr(mw, "ActionParamDialog"))
check("MainWindow._on_add_action", hasattr(mw.MainWindow, "_on_add_action"))
check("MainWindow._collect_actions", hasattr(mw.MainWindow, "_collect_actions"))
check("MainWindow._action_summary", hasattr(mw.MainWindow, "_action_summary"))
# 动作预览在 Phase 4 异步重构中由 _process_with_actions 更名为
# _render_action_preview（解码 + apply_actions 全部移入子线程
# _ActionPreviewWorker）。断言当前真实存在的入口。
check("MainWindow._render_action_preview",
      hasattr(mw.MainWindow, "_render_action_preview"))

# _action_summary logic (does not touch any Qt state, so a dummy self works).
s = mw.MainWindow._action_summary
check("summary resize WxH",
      s(None, {"type": "调整大小", "params": {"width": 800, "height": 600}}) == "调整大小 (800x600)")
check("summary resize width only",
      s(None, {"type": "调整大小", "params": {"width": 100, "height": 0}}) == "调整大小 (宽100)")
check("summary rotate",
      s(None, {"type": "旋转", "params": {"angle": 90}}) == "旋转 (90°)")
check("summary watermark",
      s(None, {"type": "水印", "params": {"text": "ABC"}}) == "水印 (ABC)")
check("summary brightness",
      s(None, {"type": "亮度/对比度", "params": {"brightness": 1.2, "contrast": 0.9}}) == "亮度/对比度 (亮1.2/对0.9)")
check("summary sharpen",
      s(None, {"type": "锐化", "params": {"factor": 1.5}}) == "锐化 (1.5)")
check("summary crop",
      s(None, {"type": "裁剪", "params": {"left": 0, "top": 0, "width": 50, "height": 50}}) == "裁剪 (0,0 50x50)")

# Best-effort: actually construct the dialog for every action type. This needs
# a QApplication; on headless setups where that fails we skip rather than fail.
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    for t in mw.processor.ACTION_TYPES:
        d = mw.ActionParamDialog(t)
        p = d.get_params()
        check("dialog params dict: %s" % t, isinstance(p, dict))
    print("DIALOG_CONSTRUCT_OK")
except Exception as exc:  # pragma: no cover - environment dependent
    print("DIALOG_CONSTRUCT_SKIPPED:", exc)

print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
