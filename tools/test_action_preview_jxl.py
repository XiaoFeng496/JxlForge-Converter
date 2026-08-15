# -*- coding: utf-8 -*-
"""Headless regression: 动作标签页预览区不会因错误信息被撑大；预览路径走
_display_path 让 PIL 能读 JXL/AVIF；解码失败时只显示文件名而非完整路径。

修复了两个 bug：

1) 动作标签页如果有文件无法读取时，会把窗口撑大
   原因：preview_msg 是默认 QLabel（Preferred/Preferred、无 word wrap），
   错误信息中含 PIL 抛出的完整文件路径，导致 QLabel 横向拉到与路径同宽，
   又因为 QVBoxLayout 中无高度上限，整窗跟着长高。
   修复：setWordWrap(True) + Preferred/Maximum + setMaximumHeight(120)。

2) 动作标签页无法预览 JXL
   原因：_render_action_preview 直接 PIL.Image.open(path)；PIL 不支持 JXL。
   修复：先用 _display_path() 走 djxl 解到 PNG 再读。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QSizePolicy

app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
        print("PASS:", name)
    else:
        failures.append(name)
        print("FAIL:", name)


win = mw.MainWindow()

# --- Bug 1: preview_msg 的尺寸策略不会把窗口撑大 -----------------
check("preview_msg exists", hasattr(win, "preview_msg"))
msg = win.preview_msg
sp = msg.sizePolicy()
check("preview_msg uses Preferred horizontal",
      sp.horizontalPolicy() == QSizePolicy.Preferred)
check("preview_msg uses Maximum vertical (no vertical expansion)",
      sp.verticalPolicy() == QSizePolicy.Maximum)
check("preview_msg has word wrap enabled",
      msg.wordWrap() is True)
check("preview_msg has bounded maximum height",
      msg.maximumHeight() <= 200 and msg.maximumHeight() > 0)

# --- Bug 2: JXL/AVIF 预览路径走 _display_path ----------------------
# 用真实 PNG 模拟「JXL 解码后产生的 PNG」，验证 _render_action_preview 会通过
# _display_path 拿到可读路径。
import tempfile
from PIL import Image

tmpdir = tempfile.mkdtemp()
png_path = os.path.join(tmpdir, "fake_jxl_payload.png")
Image.new("RGB", (80, 60), (200, 100, 50)).save(png_path)

# 替换 _display_path，让它把 .jxl 当作 PNG 返回（模拟 djxl 解码后的产物）。
# 同时验证 _render_action_preview 在出错路径下不抛异常、能给出"文件名级"错误。
called = {"n": 0}
original_display_path = mw._display_path


def fake_display_path(path):
    called["n"] += 1
    if path.lower().endswith(".jxl"):
        return png_path
    return original_display_path(path)


mw._display_path = fake_display_path

try:
    win.input_files = [os.path.join(tmpdir, "sample.jxl")]
    win._refresh_preview_sources()
    check("combo populated for jxl",
          win.preview_source_combo.count() == 1)

    # 不抛异常即视为修复生效。
    win._render_action_preview()
    check("_display_path was consulted by render",
          called["n"] >= 1)
    check("jxl preview shows the canvas (not the message)",
          win.preview_view.isHidden() is False
          and win.preview_msg.isHidden() is True)
    check("jxl preview produced a non-null pixmap",
          win._preview_original_pixmap is not None
          and not win._preview_original_pixmap.isNull())
finally:
    mw._display_path = original_display_path

# --- Bug 2 副带：解码失败时错误信息只显示文件名 -----------------
# 让 _display_path 返回 None，模拟 JXL 解码失败 / 不支持的格式。
def failing_display_path(path):
    return None


mw._display_path = failing_display_path
try:
    # 找一个真实存在的文件路径以便错误信息里包含可识别的 basename。
    bogus_dir = os.path.join(tmpdir, "极长中文目录_用于触发路径撑大测试")
    os.makedirs(bogus_dir, exist_ok=True)
    bogus_jxl = os.path.join(bogus_dir, "132119265_p0汉化.jxl")
    win.input_files = [bogus_jxl]
    win._refresh_preview_sources()
    win.preview_source_combo.setCurrentIndex(0)
    win._render_action_preview()

    check("decode-failed -> canvas hidden",
          win.preview_view.isHidden() is True)
    check("decode-failed -> message visible",
          win.preview_msg.isHidden() is False)
    err = win.preview_msg.text()
    check("error message uses basename not full path",
          os.path.basename(bogus_jxl) in err
          and bogus_jxl not in err,
          )
    # 关键：消息中绝对不能带"父目录路径"，否则旧 bug 的窗口撑大问题就回来了。
    check("error message excludes parent dir prefix",
          bogus_dir not in err)
finally:
    mw._display_path = original_display_path

print()
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
sys.exit(0 if not failures else 1)
