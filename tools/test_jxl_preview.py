# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless regression: JXL and AVIF images must produce thumbnails and a preview.

Qt's built-in image readers cannot load JPEG XL or AVIF, so such files
previously yielded a null pixmap — no thumbnail and no double-click preview.
The fix decodes them to a temporary PNG (via djxl for JXL, via Pillow for AVIF)
and lets Qt read that. This test builds real .jxl / .avif fixtures and asserts
the thumbnail, image info, and preview all work; it skips gracefully when the
required tools (cjxl/djxl or Pillow AVIF support) are absent.
"""
import os
import sys
import tempfile

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from jxlforge import converter
import jxlforge.main_window as mw
from jxlforge.main_window import MainWindow


failures = []


def check(name, cond):
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name)
        failures.append(name)


tools = converter.check_tools()
have_jxl_tools = bool(tools.get("cjxl") and tools.get("djxl"))

# Pillow is needed both for the AVIF fixture and the AVIF decode path.
try:
    from PIL import Image
    from PIL import features as _pil_features
    pillow_ok = True
    pillow_avif = bool(_pil_features.check("avif"))
except Exception as exc:
    print("SKIP: Pillow not available:", exc)
    pillow_ok = False
    pillow_avif = False

tmpdir = tempfile.mkdtemp()
src_png = os.path.join(tmpdir, "src.png")
if pillow_ok:
    Image.new("RGB", (120, 80), (200, 100, 50)).save(src_png)
jxl_path = os.path.join(tmpdir, "sample.jxl")
avif_path = os.path.join(tmpdir, "avif_sample.avif")

# --- JXL fixture -------------------------------------------------------------
if have_jxl_tools and pillow_ok and os.path.isfile(src_png):
    eok, _e, _tag = converter.encode(src_png, jxl_path, effort=1)
    if not (eok and os.path.isfile(jxl_path)):
        have_jxl_tools = False
        print("SKIP: failed to create JXL fixture")
else:
    have_jxl_tools = False
    print("SKIP: cjxl/djxl or Pillow absent, cannot build JXL fixture")

# --- AVIF fixture ------------------------------------------------------------
have_avif = False
if pillow_ok and pillow_avif:
    try:
        Image.new("RGB", (96, 64), (60, 140, 210)).save(avif_path, "AVIF")
        have_avif = os.path.isfile(avif_path) and os.path.getsize(avif_path) > 0
    except Exception as exc:
        print("SKIP: failed to create AVIF fixture:", exc)
if not have_avif:
    print("SKIP: Pillow AVIF support absent, cannot build AVIF fixture")

win = MainWindow()

# --- JXL checks (only when the fixture exists) -------------------------------
if have_jxl_tools:
    # 1) _display_path decodes .jxl to a real PNG and caches it.
    disp = mw._display_path(jxl_path)
    check("jxl decodes to a temporary PNG",
          disp is not None and disp.lower().endswith(".png") and os.path.isfile(disp))
    check("decoded png is cached for reuse",
          mw._DECODE_TEMP_CACHE.get(jxl_path) == disp)

    # 2) Thumbnail is not null for a .jxl file.
    thumb = win._make_thumbnail(jxl_path, 96)
    check("thumbnail is non-null for jxl", not thumb.isNull())

    # 3) Image info reports JXL format and parsed dimensions.
    info = win._image_info(jxl_path)
    check("image info labels format JXL", "JXL" in info)
    check("image info parses dimensions", "120 x 80" in info)

    # 4) Preview dialog no longer decodes synchronously in __init__ (the freeze
    #    fix): it returns immediately with no pixmap, then loads async.
    dlg = mw.PreviewDialog(jxl_path, win)
    check("preview dialog returns without synchronous decode (jxl)",
          dlg.scroll is None)
    # Drive the background loader to completion and let the GUI thread apply it.
    if dlg._loader is not None:
        dlg._loader.wait()
        app.processEvents()
    check("preview loads after async decode (jxl)", dlg.scroll is not None)
else:
    print("(JXL checks skipped)")

# --- AVIF checks (only when the fixture exists) ------------------------------
if have_avif:
    # 5) _display_path decodes .avif to a real PNG and caches it.
    disp_a = mw._display_path(avif_path)
    check("avif decodes to a temporary PNG",
          disp_a is not None and disp_a.lower().endswith(".png")
          and os.path.isfile(disp_a))
    check("avif decoded png is cached for reuse",
          mw._DECODE_TEMP_CACHE.get(avif_path) == disp_a)

    # 6) Thumbnail is not null for an .avif file.
    thumb_a = win._make_thumbnail(avif_path, 96)
    check("thumbnail is non-null for avif", not thumb_a.isNull())

    # 7) Image info reports AVIF format and parsed dimensions.
    info_a = win._image_info(avif_path)
    check("image info labels format AVIF", "AVIF" in info_a)
    check("image info parses avif dimensions", "96 x 64" in info_a)

    # 8) Preview dialog loads asynchronously for .avif too.
    dlg_a = mw.PreviewDialog(avif_path, win)
    check("preview dialog returns without synchronous decode (avif)",
          dlg_a.scroll is None)
    if dlg_a._loader is not None:
        dlg_a._loader.wait()
        app.processEvents()
    check("preview loads after async decode (avif)", dlg_a.scroll is not None)
else:
    print("(AVIF checks skipped)")

# 9) Non-decodable paths pass through unchanged (no spurious decode).
check("non-special path passes through unchanged",
      mw._display_path(src_png) == src_png)

print()
if failures:
    print("FAILED %d check(s):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL JXL/AVIF PREVIEW CHECKS PASSED")
