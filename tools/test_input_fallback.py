# -*- coding: utf-8 -*-
"""Headless tests for input-format routing in ConvertWorker._encode_source.

cjxl's native reader only accepts PNG/APNG/GIF/JPEG/EXR/PPM/PFM/PAM/PGX/JXL.
Formats like BMP/TIFF/WebP (and AVIF with pillow-avif) are NOT readable by
cjxl, so _encode_source must route them through Pillow (decode -> temp PNG ->
cjxl) WITHOUT first attempting a guaranteed-fail native cjxl call. Native-
supported formats still try cjxl first and fall back to Pillow only on failure.

These tests stub converter.encode so they run engine-independent on any box.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import jxlforge.converter as conv_mod
from jxlforge.main_window import ConvertWorker

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


from PIL import Image

tmpdir = tempfile.mkdtemp()

# Fake converter.encode: succeeds only when the input is a .png temp file
# (simulating cjxl happily encoding a Pillow-produced PNG); fails on any other
# input (simulating cjxl rejecting an unsupported/raw source).
calls = []


def fake_encode(input_path, output_path, **kwargs):
    calls.append(input_path)
    if input_path.lower().endswith(".png"):
        return True, "操作成功完成。", ""
    return False, "命令返回错误（退出码 1）：... Getting pixel data failed.", ""


def fake_encode_fail_first(input_path, output_path, **kwargs):
    # Fails on the first (native) call, succeeds on any subsequent (temp png) call.
    calls.append(input_path)
    ok = len(calls) > 1
    return ok, "操作成功完成。" if ok else "Getting pixel data failed", ""


_real = conv_mod.encode
conv_mod.encode = fake_encode


def run_encode(src_ext):
    calls.clear()
    src = os.path.join(tmpdir, "sample" + src_ext)
    Image.new("RGB", (16, 16), (10, 20, 30)).save(src, "PNG")
    worker = ConvertWorker([], [])
    tmp_files = []
    out_path = os.path.join(tmpdir, "sample.jxl")
    return worker._encode_source(src, out_path, tmp_files), tmp_files


# --- Transit formats: BMP/TIFF/WebP -> Pillow, single encode call on temp png ---
for ext in (".bmp", ".tif", ".tiff", ".webp"):
    (ok, msg, _tag), tmp_files = run_encode(ext)
    check("transit %s routes via Pillow (success)" % ext, ok is True)
    check("transit %s calls encode exactly once" % ext, len(calls) == 1)
    check("transit %s encode input is temp .png" % ext,
          bool(calls) and calls[0].lower().endswith(".png"))
    check("transit %s temp png registered for cleanup" % ext,
          any(t.lower().endswith(".png") for t in tmp_files))

# --- ICO transit: Pillow 原生支持 ICO，无需额外依赖，直接走中转 ---
calls.clear()
src_ico = os.path.join(tmpdir, "sample.ico")
Image.new("RGB", (16, 16), (10, 20, 30)).save(src_ico, "ICO")
worker = ConvertWorker([], [])
tmp_files = []
out_ico = os.path.join(tmpdir, "sample_ico.jxl")
ok_ico, _mico, _tico = worker._encode_source(src_ico, out_ico, tmp_files)
check("transit .ico routes via Pillow (success)", ok_ico is True)
check("transit .ico calls encode exactly once", len(calls) == 1)
check("transit .ico encode input is temp .png",
      bool(calls) and calls[0].lower().endswith(".png"))

# --- HEIC/HEIF 中转：需 pi-heif；缺失给精准提示，已装则走中转 ---
from jxlforge.main_window import _ensure_heif_opener, _decode_to_temp_file
heic_src = os.path.join(tmpdir, "sample.heic")
if not _ensure_heif_opener():
    calls.clear()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(heic_src, "PNG")  # 占位名
    worker = ConvertWorker([], [])
    ok_h, msg_h, _th = worker._encode_source(heic_src, os.path.join(tmpdir, "h.jxl"), [])
    check("HEIC without pi-heif returns False with hint",
          ok_h is False and "pi-heif" in msg_h)
else:
    import pi_heif as _ph
    _ph.register_heif_opener()
    # pi_heif 是*解码专用*版，不注册 HEIF 保存句柄（故不能 Image.save(...,"HEIF") 造样例）。
    # 改用随仓库提交的测试资源做真实解码中转验证。
    _sample = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "test_assets", "sample.heic")
    if not os.path.isfile(_sample):
        check("HEIC transit skipped (sample.heic missing)", True)
    else:
        shutil.copy(_sample, heic_src)
        calls.clear()
        worker = ConvertWorker([], [])
        tmp_files = []
        out_h = os.path.join(tmpdir, "sample.heic.jxl")
        ok_h, _mh, _th = worker._encode_source(heic_src, out_h, tmp_files)
        check("transit .heic routes via Pillow (success)", ok_h is True)
        check("transit .heic calls encode exactly once", len(calls) == 1)
        check("transit .heic encode input is temp .png",
              bool(calls) and calls[0].lower().endswith(".png"))
        # 预览解码：HEIC 应被解码为临时 PNG（缩略图可用）
        dec_h = _decode_to_temp_file(heic_src)
        check("HEIC preview decodes to temp png",
              isinstance(dec_h, str) and dec_h.lower().endswith(".png"))

# --- Native format: PNG tries cjxl directly, succeeds without Pillow ---
calls.clear()
(ok, msg, _tag), tmp_files = run_encode(".png")
check("native .png uses cjxl directly (success)", ok is True)
check("native .png encode called once on src",
      len(calls) == 1 and calls[0].lower().endswith(".png")
      and "sample.png" in calls[0].lower())
check("native .png does NOT create a transit temp png",
      not any(t.lower().endswith(".png") and "tmp" in t.lower() for t in tmp_files))

# --- Native format failure falls back to Pillow ---
conv_mod.encode = fake_encode_fail_first
calls.clear()
src = os.path.join(tmpdir, "broken.png")
Image.new("RGB", (16, 16), (10, 20, 30)).save(src, "PNG")
worker = ConvertWorker([], [])
tmp_files = []
out_path = os.path.join(tmpdir, "broken.jxl")
ok, msg, _tag = worker._encode_source(src, out_path, tmp_files)
check("native failure falls back to Pillow (success)", ok is True)
check("native failure calls encode twice (src + temp png)", len(calls) == 2)
check("native failure second call is temp .png", calls[1].lower().endswith(".png"))
check("native failure temp png registered for cleanup",
      any(t.lower().endswith(".png") for t in tmp_files))

conv_mod.encode = _real
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
