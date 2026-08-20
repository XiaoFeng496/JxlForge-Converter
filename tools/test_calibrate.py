# -*- coding: utf-8 -*-
"""Headless test for the big-image calibration logic and the in-app worker.

Verifies:
  * QSettings round-trip: has_calibration() / write_floor_px().
  * run_calibration() skips cleanly (returns None + logs) when cjxl is missing.
  * run_calibration() computes the correct pixel floor from synthetic timings.
  * CalibrateWorker emits done_signal with the floor and calls write_floor_px.

The heavy cjxl benchmark is avoided by monkeypatching calibrate.cjxl_path /
bench / run_calibration, so the suite stays fast and offline-friendly.
QSettings is redirected to a temp dir so nothing touches the real ini.
"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# Isolate QSettings BEFORE importing the modules under test. Mirror the real
# app's identity (org/app + IniFormat) so QSettings() and calibrate.write_floor_px
# (which uses the explicit "libjxl"/"libjxl-gui" identity) resolve to the same
# temp file instead of two different locations.
_TMP = tempfile.mkdtemp()
QSettings.setDefaultFormat(QSettings.IniFormat)
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])
_app.setOrganizationName("libjxl")
_app.setApplicationName("libjxl-gui")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _TMP)

from libjxl_gui import calibrate  # noqa: E402
from libjxl_gui.main_window import CalibrateWorker  # noqa: E402

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


# --- 1. QSettings round-trip ----------------------------------------------
def _read_floor():
    s = QSettings()
    s.beginGroup("conversion")
    try:
        return int(s.value("big_image_floor_px", ""))
    finally:
        s.endGroup()


check("has_calibration() initially False", calibrate.has_calibration() is False)
calibrate.write_floor_px(4_000_000)
check("has_calibration() True after write", calibrate.has_calibration() is True)
check("persisted value survives re-read", _read_floor() == 4_000_000)
check("QSettings stores big_image_floor_px", _read_floor() == 4_000_000)


# --- 2. run_calibration() skips when cjxl missing ------------------------
_real_cjxl_path = calibrate.cjxl_path
calibrate.cjxl_path = lambda: None
logs = []
floor = calibrate.run_calibration(log_cb=logs.append)
check("run_calibration returns None without cjxl", floor is None)
check("run_calibration logs skip reason", any("未找到 cjxl" in l for l in logs))
calibrate.cjxl_path = _real_cjxl_path


# --- 3. run_calibration() computes correct floor -------------------------
# 注入合成计时：单线程恒 2.0s、满核恒 1.0s -> speedup=2.0 对所有档成立，
# 故首个档（1MP）即满足 BIG_IMAGE_TARGET_SPEEDUP=2.0，地板应为 1MP。
_real_bench = calibrate.bench
calibrate.bench = lambda png, nt, effort, runs: (2.0 if nt == 1 else 1.0)
logs2 = []
floor2 = calibrate.run_calibration(log_cb=logs2.append, max_mp=8)
check("run_calibration floor = first level (1MP)", floor2 == 1_000_000)
check("run_calibration logs completion", any("校准完成" in l for l in logs2))
calibrate.bench = _real_bench


# --- 4. CalibrateWorker wiring -------------------------------------------
_real_run = calibrate.run_calibration
_real_write = calibrate.write_floor_px
captured_done = []
written = {}


def _fake_run(progress_cb=None, log_cb=None, effort=7, runs=3, max_mp=64):
    if progress_cb:
        progress_cb("校准中")
    if log_cb:
        log_cb("fake calibration")
    return 12_000_000


def _fake_write(px, scheme=None):
    written["px"] = px
    written["scheme"] = scheme


calibrate.run_calibration = _fake_run
calibrate.write_floor_px = _fake_write

w = CalibrateWorker(effort=7, runs=3, max_mp=64)
w.done_signal.connect(captured_done.append)
w.run()  # 同步在当前线程跑（不经由 thread.start()）
check("worker done_signal emitted floor", captured_done == [12_000_000])
check("worker calls write_floor_px with floor", written.get("px") == 12_000_000)
check("worker passes scheme (str or None)",
      written.get("scheme") is None or isinstance(written.get("scheme"), str))

calibrate.run_calibration = _real_run
calibrate.write_floor_px = _real_write


# --- 5. per-scheme storage + CPU signature + clear -----------------------
calibrate.clear_all_calibration()
check("clear_all leaves no calibration", calibrate.has_calibration() is False)

calibrate.write_floor_px(5_000_000, scheme="plan-aaa")
check("per-scheme write sets generic", calibrate.read_stored_floor_px() == 5_000_000)
check("per-scheme write sets scheme key",
      calibrate.read_per_scheme_floor_px("plan-aaa") == 5_000_000)
check("unknown scheme falls back to generic",
      calibrate.read_stored_floor_px(scheme="plan-bbb") == 5_000_000)
check("unknown scheme has no per-scheme key",
      calibrate.read_per_scheme_floor_px("plan-bbb") is None)

calibrate.write_floor_px(6_000_000, scheme="plan-bbb")
check("second scheme stored separately",
      calibrate.read_per_scheme_floor_px("plan-aaa") == 5_000_000)
check("second scheme overrides generic",
      calibrate.read_stored_floor_px(scheme="plan-bbb") == 6_000_000)

calibrate.write_cpu_signature("cpu-sig-1")
check("cpu signature round-trip", calibrate.read_cpu_signature() == "cpu-sig-1")

calibrate.clear_all_calibration()
check("after clear: no calibration", calibrate.has_calibration() is False)
check("after clear: per-scheme key gone",
      calibrate.read_per_scheme_floor_px("plan-aaa") is None)
check("after clear: cpu signature gone",
      calibrate.read_cpu_signature() is None)


# --- 6. power helpers -----------------------------------------------------
from libjxl_gui import power as _pw  # noqa: E402
check("cpu_signature non-empty", bool(_pw.cpu_signature()))
_scheme = _pw.get_active_power_scheme()
check("get_active_power_scheme returns GUID or None",
      _scheme is None or (isinstance(_scheme, str) and len(_scheme) == 36))


shutil.rmtree(_TMP, ignore_errors=True)
failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
