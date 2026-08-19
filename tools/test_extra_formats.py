# -*- coding: utf-8 -*-
"""Headless test for the extra-format support added in formats.py:

  * PFM / PAM / PGX pure-Python decoders -> valid binary PPM (struct only);
  * the decoded PPM is actually loadable by QImageReader (integration);
  * get_image_dims() returns correct (w, h) for PFM / PAM / PGX / EXR;
  * EXR header parsing yields width / height / channels / compression;
  * EXR metadata text renders the key fields;
  * IMAGE_EXTENSIONS now includes .exr / .pfm / .pam / .pgx.

No external deps (no OpenEXR, no numpy, no Pillow) — every sample is built
with the standard library, exactly like the production decoder path.
"""
import os
import sys
import struct
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImageReader

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from libjxl_gui.main_window import get_image_dims, IMAGE_EXTENSIONS
from libjxl_gui import formats

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


W, H = 4, 3
tmpdir = tempfile.mkdtemp()

# --- 1. PFM Gray (4x3, little-endian) --------------------------------------
pfm_g = os.path.join(tmpdir, "a.pfm")
with open(pfm_g, "wb") as f:
    f.write(b"Pf\n4 3\n-1.0\n")
    f.write(struct.pack("<12f", *[float(i) for i in range(12)]))
b = formats.pfm_to_ppm_bytes(pfm_g)
check("PFM gray -> P5 4x3", b.startswith(b"P5\n4 3\n255\n") and len(b) == len(b"P5\n4 3\n255\n") + 12)

# --- 2. PFM RGB (2x2) ------------------------------------------------------
pfm_c = os.path.join(tmpdir, "b.pfm")
with open(pfm_c, "wb") as f:
    f.write(b"PF\n2 2\n-1.0\n")
    f.write(struct.pack("<12f", *[float(i) for i in range(12)]))
check("PFM rgb -> P6 2x2", formats.pfm_to_ppm_bytes(pfm_c).startswith(b"P6\n2 2\n255\n"))

# --- 3. PAM RGB (2x2), first data byte is 0x0A (must NOT be eaten) ----------
pam = os.path.join(tmpdir, "c.pam")
with open(pam, "wb") as f:
    f.write(b"P7\nWIDTH 2\nHEIGHT 2\nDEPTH 3\nMAXVAL 255\nTUPLTYPE RGB\nENDHDR\n")
    f.write(bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]))
b3 = formats.pam_to_ppm_bytes(pam)
check("PAM rgb -> P6 2x2", b3.startswith(b"P6\n2 2\n255\n") and len(b3) == len(b"P6\n2 2\n255\n") + 12)

# --- 4. PAM 16-bit grayscale ----------------------------------------------
pam16 = os.path.join(tmpdir, "g.pam")
with open(pam16, "wb") as f:
    f.write(b"P7\nWIDTH 1\nHEIGHT 2\nDEPTH 1\nMAXVAL 65535\nTUPLTYPE GRAYSCALE\nENDHDR\n")
    f.write(struct.pack(">HH", 0, 65535))
check("PAM 16bit -> P5 1x2", formats.pam_to_ppm_bytes(pam16).startswith(b"P5\n1 2\n255\n"))

# --- 5. PGX unsigned 8-bit gray (4x3) --------------------------------------
pgx = os.path.join(tmpdir, "e.pgx")
with open(pgx, "wb") as f:
    f.write(b"PG ML U 8 4 3\n")
    f.write(bytes(list(range(12))))
check("PGX u8 -> P5 4x3", formats.pgx_to_ppm_bytes(pgx).startswith(b"P5\n4 3\n255\n"))

# --- 6. PGX signed 16-bit gray (2x2) ---------------------------------------
pgx2 = os.path.join(tmpdir, "h.pgx")
with open(pgx2, "wb") as f:
    f.write(b"PG LM S 16 2 2\n")
    f.write(struct.pack("<4h", -100, 0, 100, 300))
check("PGX s16 -> P5 2x2", formats.pgx_to_ppm_bytes(pgx2).startswith(b"P5\n2 2\n255\n"))

# --- 7. decoded PPM is loadable by QImageReader (integration) ---------------
from libjxl_gui.main_window import _display_path
for src, exp in [(pfm_g, (4, 3)), (pam, (2, 2)), (pgx, (4, 3))]:
    # 走真实管线：_display_path 解码为临时 PPM，QImageReader 读取
    dp = _display_path(src)
    ok_disp = dp is not None
    if ok_disp:
        reader = QImageReader(dp)
        ok_read = reader.canRead()
        sz = reader.size()
        ok_size = (sz.width(), sz.height()) == exp
    else:
        ok_read = ok_size = False
    check("decode+load %s" % os.path.basename(src), ok_disp and ok_read and ok_size)

# --- 8. get_image_dims for PFM / PAM / PGX ---------------------------------
check("dims PFM gray == (4,3)", get_image_dims(pfm_g) == (4, 3))
check("dims PAM == (2,2)", get_image_dims(pam) == (2, 2))
check("dims PGX == (4,3)", get_image_dims(pgx) == (4, 3))

# --- 9. EXR header parsing (synthetic, pure-python) -------------------------
def _exr_attr(name, typ, value):
    return (name.encode() + b"\x00" + typ.encode() + b"\x00"
            + struct.pack("<I", len(value)) + value)


_chlist = (b"A\x00" + struct.pack("<iB3xii", 2, 0, 1, 1)
           + b"B\x00" + struct.pack("<iB3xii", 2, 0, 1, 1) + b"\x00")
exr = os.path.join(tmpdir, "f.exr")
with open(exr, "wb") as f:
    f.write(formats._EXR_MAGIC)
    f.write(struct.pack("<I", 2))
    f.write(_exr_attr("dataWindow", "box2i", struct.pack("<iiii", 0, 0, 1919, 1079)))
    f.write(_exr_attr("channels", "chlist", _chlist))
    f.write(_exr_attr("compression", "compression", struct.pack("<B", 3)))
    f.write(b"\x00")
m = formats.parse_exr_header(exr)
check("EXR width==1920", m["width"] == 1920)
check("EXR height==1080", m["height"] == 1080)
check("EXR channels==[A,B]", [c["name"] for c in m["channels"]] == ["A", "B"])
check("EXR compression==ZIP", m["compression"] == "ZIP")

# --- 10. EXR bad file degrades (raises, no crash) ---------------------------
try:
    formats.parse_exr_header(pam)
    check("EXR badfile raises", False)
except Exception:
    check("EXR badfile raises", True)

# --- 11. get_image_dims(EXR) via header fallback ---------------------------
check("dims EXR == (1920,1080)", get_image_dims(exr) == (1920, 1080))

# --- 12. EXR metadata text contains key fields -----------------------------
txt = formats.exr_metadata_text(m)
check("EXR metadata has size", "1920 x 1080" in txt)
check("EXR metadata has compression", "ZIP" in txt)
check("EXR metadata has channel", "A(FLOAT)" in txt)

# --- 13. IMAGE_EXTENSIONS includes new extensions ---------------------------
for ext in (".exr", ".pfm", ".pam", ".pgx"):
    check("IMAGE_EXTENSIONS has %s" % ext, ext in IMAGE_EXTENSIONS)

# --- cleanup & summary -----------------------------------------------------
shutil.rmtree(tmpdir, ignore_errors=True)
failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
