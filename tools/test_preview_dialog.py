# -*- coding: utf-8 -*-
"""Lightweight checks for the double-click PreviewDialog error messages.

Only tests the message-generating helper; constructing the actual dialog
would need a real display (QPixmap crashes under offscreen), so we avoid it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


# Patch find_tool so the two JXL branches are deterministic regardless of
# whether the host actually has libjxl on PATH.
original_find_tool = mw.converter.find_tool


def _patch_find_tool(return_value):
    def _fake(name):
        if name == "djxl":
            return return_value
        return original_find_tool(name)

    mw.converter.find_tool = _fake


try:
    _patch_find_tool(None)
    msg_no_djxl = mw.PreviewDialog._preview_error_message(
        r"C:\images\sample.jxl"
    )
    check(
        "JXL + missing djxl mentions libjxl",
        "libjxl" in msg_no_djxl,
    )
    check(
        "JXL + missing djxl mentions PATH",
        "PATH" in msg_no_djxl or "PATH 环境变量" in msg_no_djxl,
    )
    check(
        "JXL + missing djxl says cannot preview",
        "无法预览" in msg_no_djxl,
    )

    _patch_find_tool(r"C:\Program Files\libjxl\bin\djxl.exe")
    msg_decode_fail = mw.PreviewDialog._preview_error_message(
        r"C:\images\sample.jxl"
    )
    check(
        "JXL + present djxl mentions decode failure",
        "djxl 解码失败" in msg_decode_fail,
    )
    check(
        "JXL + present djxl keeps filename",
        "sample.jxl" in msg_decode_fail,
    )

    msg_other = mw.PreviewDialog._preview_error_message(
        r"C:\images\sample.png"
    )
    check(
        "Non-JXL keeps generic message",
        msg_other == "无法加载图片：sample.png",
    )

    # Case-insensitive extension handling.
    _patch_find_tool(None)
    msg_upper = mw.PreviewDialog._preview_error_message(
        r"C:\images\sample.JXL"
    )
    check(
        "Uppercase .JXL also detected",
        "无法预览 JXL" in msg_upper,
    )
finally:
    mw.converter.find_tool = original_find_tool


print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
