# -*- coding: utf-8 -*-
"""Headless test for the dual-queue scheduler (big-image exclusive / small-image
parallel) added to ConvertWorker.

Verifies:
  * pixel-count classification (relative median + absolute floor) puts outlier
    large images into the big queue and uniform batches into the small queue;
  * _effective_cores() resolves "auto" -> logical cores;
  * small-pool sizing: each small file gets cores // min(k, cores) threads;
  * floor fallback: low-core machines disable big-image exclusivity;
  * an end-to-end small-file conversion logs the dual-queue banner and produces
    output files (requires cjxl).

QSettings is redirected to a temp dir so the test never touches the real ini.
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

# Isolate QSettings BEFORE importing the module under test.
_TMP = tempfile.mkdtemp()
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _TMP)

from libjxl_gui.main_window import (  # noqa: E402
    ConvertWorker,
    read_big_image_floor_px,
    estimate_floor_px,
)

from PIL import Image  # noqa: E402

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


def _cjxl():
    p = shutil.which("cjxl")
    if p:
        return p
    cand = r"C:\Program Files\libjxl\bin\cjxl.exe"
    return cand if os.path.isfile(cand) else None


def _make_pngs(dims):
    """dims: list of (w, h). Returns list of (src, out) job tuples."""
    d = tempfile.mkdtemp()
    jobs = []
    for i, (w, h) in enumerate(dims):
        src = os.path.join(d, "img%d.png" % i)
        Image.new("RGB", (w, h), (i * 20 % 256, 50, 100)).save(src, "PNG")
        jobs.append((src, src + ".jxl", False))
    return jobs


def _classify(dims, cores):
    jobs = _make_pngs(dims)
    indexed = list(enumerate(jobs, start=1))
    w = ConvertWorker(jobs, [], cpu_cores=cores)
    return w._classify_jobs(indexed)


# --- 1. classification: outlier large image isolated -----------------------
small2, big1, nt, pool = _classify([(1024, 1024), (1024, 1024), (8000, 6000)], 20)
# 48MP outlier vs 1MP median -> 1 big, 2 small; 20-core floor ~3MP.
check("outlier 48MP -> 1 big / 2 small", len(small2) == 2 and len(big1) == 1)

# --- 2. classification: uniform big batch stays parallel ------------------
s3, b3, _, _ = _classify([(6000, 4000)] * 3, 20)
# 24MP uniform: median==each, none > 2.5x median -> all small.
check("uniform 24MP x3 -> 3 small / 0 big", len(s3) == 3 and len(b3) == 0)

# --- 3. single file is never "big" ---------------------------------------
s1, b1, _, _ = _classify([(2000, 1000)], 20)
check("single 2MP -> 1 small / 0 big", len(s1) == 1 and len(b1) == 0)

# --- 4. small-pool sizing ------------------------------------------------
_, _, nt2, pool2 = _classify([(1024, 1024)] * 2, 4)
check("2 small @4 cores -> nt=2 pool=2", nt2 == 2 and pool2 == 2)
_, _, nt4, pool4 = _classify([(1024, 1024)] * 8, 4)
check("8 small @4 cores -> nt=1 pool=4 (no oversub)", nt4 == 1 and pool4 == 4)

# --- 5. _effective_cores -------------------------------------------------
w_auto = ConvertWorker([], [], cpu_cores="auto")
w_int = ConvertWorker([], [], cpu_cores=4)
check("_effective_cores auto -> logical", w_auto._effective_cores() == (os.cpu_count() or 1))
check("_effective_cores int -> as-is", w_int._effective_cores() == 4)

# --- 6. floor fallback ---------------------------------------------------
check("floor fallback low-core(4) disables big", read_big_image_floor_px(4) == 10 ** 18)
check("floor fallback 20-core ~3MP", read_big_image_floor_px(20) == estimate_floor_px(20))
check("estimate_floor_px(20)==3MP", estimate_floor_px(20) == 3_000_000)

# --- 7. end-to-end small-file conversion (needs cjxl) --------------------
cjxl = _cjxl()
if cjxl:
    jobs = _make_pngs([(1024, 1024), (1024, 1024)])
    w = ConvertWorker(jobs, [], cpu_cores=4)
    logs = []
    w.log_signal.connect(logs.append)
    w.run()  # synchronous on this thread (no thread.start())
    log_text = "\n".join(logs)
    check("banner mentions dual-queue", "双队列调度" in log_text)
    check("classify line: 2 small / 0 big",
          "小图 2 张（每图 2 线程并行）/ 大图 0 张" in log_text)
    check("outputs produced", all(os.path.exists(j[1]) for j in jobs))
else:
    print("SKIP end-to-end: cjxl not found")


shutil.rmtree(_TMP, ignore_errors=True)
failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
