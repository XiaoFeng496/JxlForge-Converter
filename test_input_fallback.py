# -*- coding: utf-8 -*-
"""Headless test for the cjxl-unsupported-input fallback in ConvertWorker.

cjxl cannot read formats like WebP/BMP/TIFF. When a direct encode fails, the
worker should decode the source with Pillow into a temp PNG and re-encode it.
This test stubs converter.encode to fail on the first (native) call and succeed
on the second (temp PNG) call, verifying the fallback path is taken and the
temp file is registered for cleanup.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import libjxl_gui.converter as conv_mod
from libjxl_gui.main_window import ConvertWorker


results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


# Build a tiny source image Pillow can open. Name it .webp to simulate an
# unsupported cjxl input (Pillow reads by content, not extension).
tmpdir = tempfile.mkdtemp()
from PIL import Image

src = os.path.join(tmpdir, "sample.webp")
Image.new("RGB", (16, 16), (10, 20, 30)).save(src, "PNG")

# Stub converter.encode: fail on the first (native) call, succeed on the
# second (temp PNG) call. Record every invocation path.
calls = []


def fake_encode(input_path, output_path, **kwargs):
    calls.append(input_path)
    if len(calls) == 1:
        return False, "命令返回错误（退出码 1）：... Getting pixel data failed."
    return True, "操作成功完成。"


_real = conv_mod.encode
conv_mod.encode = fake_encode

worker = ConvertWorker([], [])
tmp_files = []
out_path = os.path.join(tmpdir, "sample.jxl")
ok, msg = worker._encode_source(src, out_path, tmp_files)

check("fallback reports success", ok is True)
check("fallback message mentions Pillow", "Pillow" in msg)
check("converter.encode called twice (native + temp png)", len(calls) == 2)
check("second call used a temp .png", calls[1].lower().endswith(".png"))
check("temp png registered for cleanup", any(t.lower().endswith(".png") for t in tmp_files))
check("temp png was actually created", os.path.exists(calls[1]))

# Restore and cleanup
conv_mod.encode = _real
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
