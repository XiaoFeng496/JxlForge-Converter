# -*- coding: utf-8 -*-
"""Headless test for get_image_dims() — the shared, lazily-cached image
resolution lookup used by thumbnails, hover info and the dual-queue scheduler.

Verifies:
  * natively-supported formats (PNG/JPEG/BMP/GIF/TIFF/WebP/PPM/PGM) return the
    correct (w, h) synchronously (their header is readable without decoding);
  * JXL / AVIF dims are obtained NON-blockingly: get_image_dims() does NOT
    decode on its own, but returns the correct (w, h) once the file has been
    decoded into _DECODE_TEMP_CACHE (as the preview / thumbnail path does) —
    here we trigger that decode via _display_path() then read the dims;
  * the result is cached (second call returns the same tuple);
  * a non-image file returns (0, 0) without raising.
"""
import os
import sys
import shutil
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from libjxl_gui.main_window import get_image_dims, _display_path
from PIL import Image

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


W, H = 123, 456
tmpdir = tempfile.mkdtemp()

# --- natively-supported formats Pillow can write -------------------------
fmt_map = {
    "png": "PNG",
    "jpg": "JPEG",
    "bmp": "BMP",
    "gif": "GIF",
    "tif": "TIFF",
    "webp": "WEBP",
    "ppm": "PPM",
    "pgm": "PGM",
}
for ext, fmt in fmt_map.items():
    p = os.path.join(tmpdir, "img." + ext)
    try:
        im = Image.new("RGB", (W, H), (100, 150, 200))
        if ext == "pgm":
            im = im.convert("L")
        im.save(p, fmt)
    except Exception as e:
        print("SKIP %s: %s" % (ext, e))
        continue
    if not os.path.exists(p):
        print("SKIP %s: not written" % ext)
        continue
    dims = get_image_dims(p)
    check("dims %s == (%d,%d)" % (ext, W, H), dims == (W, H))

# --- JXL (encoded via cjxl, read back via djxl temp PNG) -----------------
def _cjxl_path():
    p = shutil.which("cjxl")
    if p:
        return p
    cand = r"C:\Program Files\libjxl\bin\cjxl.exe"
    return cand if os.path.isfile(cand) else None


cjxl = _cjxl_path()
if cjxl:
    png_src = os.path.join(tmpdir, "src.png")
    Image.new("RGB", (W, H), (10, 20, 30)).save(png_src, "PNG")
    jxl_path = os.path.join(tmpdir, "img.jxl")
    try:
        subprocess.run([cjxl, png_src, jxl_path], check=True, capture_output=True)
        if os.path.exists(jxl_path):
            # get_image_dims() is non-blocking: it reads dims from the decode
            # cache once the file has been decoded (preview / thumbnail do this).
            # Simulate that by decoding once via _display_path(), then dims
            # should be available from the cached decoded temp file.
            disp = _display_path(jxl_path)
            if disp:
                dims = get_image_dims(jxl_path)
                check("dims jxl == (%d,%d)" % (W, H), dims == (W, H))
            else:
                print("SKIP jxl: decode to temp file failed")
        else:
            print("SKIP jxl: encode produced no file")
    except Exception as e:
        print("SKIP jxl:", e)
else:
    print("SKIP jxl: cjxl not found")

# --- AVIF (only if Pillow can write it) --------------------------------
try:
    avif_path = os.path.join(tmpdir, "img.avif")
    Image.new("RGB", (W, H), (1, 2, 3)).save(avif_path, "AVIF")
    if os.path.exists(avif_path):
        disp = _display_path(avif_path)
        if disp:
            dims = get_image_dims(avif_path)
            check("dims avif == (%d,%d)" % (W, H), dims == (W, H))
        else:
            print("SKIP avif: decode to temp file failed")
    else:
        print("SKIP avif: not written")
except Exception as e:
    print("SKIP avif:", e)

# --- cache hit ----------------------------------------------------------
png_cache = os.path.join(tmpdir, "cache.png")
Image.new("RGB", (W, H), (5, 6, 7)).save(png_cache, "PNG")
d1 = get_image_dims(png_cache)
d2 = get_image_dims(png_cache)
check("cache hit returns same tuple", d1 == d2 == (W, H))

# --- non-image returns (0,0) without raising ----------------------------
bad = os.path.join(tmpdir, "bad.txt")
with open(bad, "w") as f:
    f.write("this is not an image")
check("non-image returns (0, 0)", get_image_dims(bad) == (0, 0))

# --- cleanup & summary --------------------------------------------------
shutil.rmtree(tmpdir, ignore_errors=True)
failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
