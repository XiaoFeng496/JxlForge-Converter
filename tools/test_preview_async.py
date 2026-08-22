# -*- coding: utf-8 -*-
"""Headless regression for the async preview loader (Phase 1 freeze fix).

The old PreviewDialog decoded the full bitmap on the GUI thread, so double-
clicking a large image froze the event loop ("顿一下 / 短暂无响应"). The fix
moves decoding into a background ``_PreviewLoader`` QThread that returns a
``QImage`` (never a QPixmap off-thread) and is capped to the preview viewport
size. This test checks:

  1. a native image smaller than the cap decodes at its original size,
  2. an image larger than the cap is decoded scaled-down (no full bitmap),
  3. an undecodable file emits ``failed`` instead of crashing,
  4. concurrent ``_display_path`` calls are crash-free (cache lock is safe).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile
import threading

try:
    from PIL import Image
    pillow_ok = True
except Exception as exc:
    print("SKIP: Pillow not available:", exc)
    pillow_ok = False

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

import libjxl_gui.main_window as mw
from libjxl_gui.main_window import _PreviewLoader

failures = []


def check(name, cond):
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name)
        failures.append(name)


def run_loader(path, max_w, max_h):
    """Start a _PreviewLoader, wait for it, return ('ok', QImage) or ('fail', path)."""
    received = []
    loader = _PreviewLoader(path, max_w, max_h)
    loader.loaded.connect(lambda img: received.append(("ok", img)))
    loader.failed.connect(lambda p: received.append(("fail", p)))
    loader.start()
    loader.wait()
    app.processEvents()  # deliver the queued signal to the GUI thread
    return received[0] if received else ("none", None)


if not pillow_ok:
    print("SKIP: Pillow absent, cannot build fixture")
else:
    tmpdir = tempfile.mkdtemp()
    src = os.path.join(tmpdir, "src.png")
    Image.new("RGB", (300, 200), (120, 80, 40)).save(src)

    # 1) 原图 < 上限：按原尺寸解码（验证不无谓缩放、1:1 可用）
    kind, img = run_loader(src, 2000, 2000)
    check("native image emits loaded signal", kind == "ok")
    if kind == "ok":
        check("native decode non-null", not img.isNull())
        check("native decode preserves original size",
              img.width() == 300 and img.height() == 200)

    # 2) 上限小于原图：按比例缩到 <= 上限（验证大图不再整图解码）
    kind, img = run_loader(src, 80, 80)
    check("oversized image still loads", kind == "ok")
    if kind == "ok":
        check("decode width capped to max_w", img.width() <= 80)
        check("decode height capped to max_h", img.height() <= 80)
        check("aspect preserved: width == 80", img.width() == 80)
        check("aspect preserved: height == 53", img.height() == 53)

    # 3) 无法解码的文件 -> failed 信号（垃圾 .jxl）
    bad = os.path.join(tmpdir, "bad.jxl")
    with open(bad, "wb") as f:
        f.write(b"not a real jxl file\x00\x01\x02")
    kind, payload = run_loader(bad, 1000, 1000)
    check("undecodable file emits failed signal", kind == "fail")
    if kind == "fail":
        check("failed payload is the path", payload == bad)

    # 4) 并发访问 _DECODE_TEMP_CACHE 不崩溃（线程安全锁）
    errs = []

    def hammer():
        try:
            for _ in range(50):
                mw._display_path(src)
        except Exception as e:  # noqa: BLE001 - we just want to record crashes
            errs.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("concurrent _display_path calls are crash-free", not errs)
    if errs:
        for e in errs[:3]:
            print("   err:", e)

print()
if failures:
    print("FAILED %d check(s):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL PREVIEW ASYNC CHECKS PASSED")
