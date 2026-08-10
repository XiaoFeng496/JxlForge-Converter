# -*- coding: utf-8 -*-
"""Headless tests for the QThread-based conversion pipeline and Popen backend.

No Qt rendering / no real cjxl needed: we stub converter.encode/decode and
Pillow so the worker runs end-to-end in a real (offscreen) QThread, and we
assert the signals, button state and temp-file cleanup behave correctly.
"""

import os
import sys
import tempfile

from PySide6.QtWidgets import QApplication

# Make sure a QApplication exists before importing the main window module.
_app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui import converter as conv_mod
from libjxl_gui.main_window import MainWindow, ConvertWorker

failures = []

def check(name, ok):
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)

# --- Stub converter.encode/decode so no real cjxl/djxl is required ----------
_real_encode = conv_mod.encode
_real_decode = conv_mod.decode

def _fake_encode(src, out_path, **kwargs):
    # Pretend cjxl ran; write a tiny file so downstream reads would work.
    with open(out_path, "wb") as f:
        f.write(b"jxl")
    return True, "fake cjxl ok"

def _fake_decode(src, out_path):
    with open(out_path, "wb") as f:
        f.write(b"png")
    return True, "fake djxl ok"

conv_mod.encode = _fake_encode
conv_mod.decode = _fake_decode

# --- Build a MainWindow and feed it a couple of fake files ----------------
window = MainWindow()

tmpdir = tempfile.mkdtemp()
src_a = os.path.join(tmpdir, "a.jpg")
src_b = os.path.join(tmpdir, "b.jpg")
for p in (src_a, src_b):
    with open(p, "wb") as f:
        f.write(b"x")

window.input_files = [src_a, src_b]
# Override output dir to tmpdir so files land somewhere writable & observable.
window.custom_folder_radio.setChecked(True)
window.custom_folder_combo.setEditText(tmpdir)
window.format_combo.setCurrentText("JXL")

# --- Drive the conversion and wait for the worker thread -------------------
collected_logs = []
collected_status = []

_main_log = []
def _log(s):
    collected_logs.append(s)
    _main_log.append(s)
window.log_edit.appendPlainText = _log
window.statusBar().showMessage = lambda s: collected_status.append(s)

window._on_convert()

worker = window._convert_worker
check("worker started", worker is not None and worker.isRunning())
check("convert button disabled during run",
      window.convert_button.isEnabled() is False)

# Wait for the thread to finish, then pump the event loop so the queued
# cross-thread signals (log_signal / status_signal / finished_signal) are
# actually delivered and _on_convert_finished runs via its signal connection.
worker.wait(10000)
check("worker finished", not worker.isRunning())
app = QApplication.instance()
for _ in range(100):
    app.processEvents()
    if not worker.isRunning() and window.convert_button.isEnabled():
        break

check("convert button re-enabled after finish",
      window.convert_button.isEnabled() is True)
# 开始/汇总 由 ConvertWorker 经信号回传（跨线程，队列连接）。
check("start log line emitted", any("开始转换:" in s for s in collected_logs))
check("per-file progress logged", sum(">>> [1/2]" in s for s in collected_logs) == 1
      and sum(">>> [2/2]" in s for s in collected_logs) == 1)
check("per-file size change line logged",
      any("->" in s and "%" in s for s in collected_logs))
check("summary: 已输入文件 2",
      any("已输入文件： 2" in s for s in collected_logs))
check("summary: 已输出文件 2",
      any("已输出文件： 2" in s for s in collected_logs))
check("summary: 错误 0", any("错误： 0" in s for s in collected_logs))
check("summary: total input size",
      any("输入文件总大小：" in s for s in collected_logs))
check("summary: total output size",
      any("输出文件总大小：" in s for s in collected_logs))
check("summary: size ratio", any("文件大小比例：" in s for s in collected_logs))
check("summary: duration", any("总持续时间：" in s for s in collected_logs))
check("completion log emitted", any("转换完成:" in s for s in collected_logs))
check("output files produced",
      os.path.exists(os.path.join(tmpdir, "a.jxl"))
      and os.path.exists(os.path.join(tmpdir, "b.jxl")))
# No-action fast path uses converter.encode/decode directly (no temp PNG),
# so a clean temp dir is the correct outcome here.
check("temp files cleaned up (fast path)",
      len([f for f in os.listdir(tmpdir) if f.endswith(".png")]) == 0)

# --- Scenario B: actions path really creates + cleans temp PNG ------------
# (Kept under the stubbed converter so the run is deterministic; the temp PNG
# lifecycle is exercised regardless of whether real cjxl exists.)
from PIL import Image as _PILImage
tmpdir2 = tempfile.mkdtemp()
src_c = os.path.join(tmpdir2, "c.png")
_PILImage.new("RGB", (8, 8), (255, 0, 0)).save(src_c, "PNG")
window2 = MainWindow()
window2.input_files = [src_c]
window2.custom_folder_radio.setChecked(True)
window2.custom_folder_combo.setEditText(tmpdir2)
window2.format_combo.setCurrentText("JXL")
# Inject a single action so the worker takes the _process_with_actions path.
window2._collect_actions = lambda: [{"type": "旋转", "params": {"angle": 90, "expand": True}}]
logs_b = []
window2.log_edit.appendPlainText = lambda s: logs_b.append(s)
window2.statusBar().showMessage = lambda s: None
window2._on_convert()
worker_b = window2._convert_worker
worker_b.wait(10000)
# finished_signal drives _on_convert_finished via its queued connection.
QApplication.instance().processEvents()
check("actions path produced output", os.path.exists(os.path.join(tmpdir2, "c.jxl")))
# The worker's _process_with_actions created a temp PNG then removed it in
# finally; only the source c.png should remain (no stray temp .png).
stray_png = [f for f in os.listdir(tmpdir2) if f.endswith(".png") and f != "c.png"]
check("actions path temp png cleaned up", stray_png == [])

# --- Scenario C: JPG 无损重编码 mode skips non-JPG inputs -----------------
tmpdir3 = tempfile.mkdtemp()
src_jpg = os.path.join(tmpdir3, "keep.jpg")
src_png = os.path.join(tmpdir3, "skip.png")
src_webp = os.path.join(tmpdir3, "skip2.webp")
for p in (src_jpg, src_png, src_webp):
    with open(p, "wb") as f:
        f.write(b"x")
window3 = MainWindow()
window3.input_files = [src_jpg, src_png, src_webp]
window3.custom_folder_radio.setChecked(True)
window3.custom_folder_combo.setEditText(tmpdir3)
window3.format_combo.setCurrentText("JXL")
window3.lossless_jpeg_radio.setChecked(True)  # JPG 无损重编码 模式
logs_c = []
status_c = []
window3.log_edit.appendPlainText = lambda s: logs_c.append(s)
window3.statusBar().showMessage = lambda s: status_c.append(s)
window3._on_convert()
worker_c = window3._convert_worker
check("C: worker started (jpg-only job)",
      worker_c is not None and worker_c.isRunning())
worker_c.wait(10000)
# finished_signal drives _on_convert_finished via its queued connection.
app3 = QApplication.instance()
for _ in range(100):
    app3.processEvents()
    if not worker_c.isRunning():
        break
check("C: skip notice in status bar",
      any("已跳过 2 个非 JPG" in s for s in status_c))
check("C: skip notice in log",
      any("JPG 无损重编码模式仅支持 JPG 输入" in s for s in logs_c))
check("C: skipped png listed", any("skip.png" in s for s in logs_c))
check("C: skipped webp listed", any("skip2.webp" in s for s in logs_c))
check("C: only jpg entered summary (已输入文件 1)",
      any("已输入文件： 1" in s for s in logs_c))
check("C: jpg output produced",
      os.path.exists(os.path.join(tmpdir3, "keep.jxl")))
check("C: non-jpg outputs NOT produced",
      not os.path.exists(os.path.join(tmpdir3, "skip.jxl"))
      and not os.path.exists(os.path.join(tmpdir3, "skip2.jxl")))

# --- Scenario D: a .jxl input in the default mode is transcoded by cjxl into
# a .jxl output (re-compress), NOT decoded to PNG. cjxl natively reads JXL,
# so the large-JXL -> smaller-JXL use case works without a djxl round-trip.
tmpdir4 = tempfile.mkdtemp()
src_jxl = os.path.join(tmpdir4, "dec.jxl")
with open(src_jxl, "wb") as f:
    f.write(b"x")
window4 = MainWindow()
window4.input_files = [src_jxl]
window4.custom_folder_radio.setChecked(True)
window4.custom_folder_combo.setEditText(tmpdir4)
window4.format_combo.setCurrentText("JXL")
logs_d = []
window4.log_edit.appendPlainText = lambda s: logs_d.append(s)
window4.statusBar().showMessage = lambda s: None
window4._on_convert()
worker_d = window4._convert_worker
worker_d.wait(10000)
for _ in range(100):
    QApplication.instance().processEvents()
    if not worker_d.isRunning():
        break
check("D: jxl input transcoded to jxl output (no decode to png)",
      os.path.exists(os.path.join(tmpdir4, "dec.jxl"))
      and not os.path.exists(os.path.join(tmpdir4, "dec.png")))
check("D: jxl transcode succeeds (no 处理失败 in log)",
      not any("处理失败" in s for s in logs_d))

# --- Scenario E: a non-jxl input with output format PNG must be saved as a
# REAL png via Pillow, never routed through cjxl (which only emits JXL and
# would otherwise produce a mislabeled "fake" png).
tmpdir5 = tempfile.mkdtemp()
src_e = os.path.join(tmpdir5, "photo.png")
_PILImage.new("RGB", (16, 16), (0, 128, 255)).save(src_e, "PNG")
outdir5 = os.path.join(tmpdir5, "out")
os.makedirs(outdir5, exist_ok=True)
window5 = MainWindow()
window5.input_files = [src_e]
window5.custom_folder_radio.setChecked(True)
window5.custom_folder_combo.setEditText(outdir5)
window5.format_combo.setCurrentText("PNG (*.png)")
logs_e = []
window5.log_edit.appendPlainText = lambda s: logs_e.append(s)
window5.statusBar().showMessage = lambda s: None
window5._on_convert()
worker_e = window5._convert_worker
worker_e.wait(10000)
for _ in range(100):
    QApplication.instance().processEvents()
    if not worker_e.isRunning():
        break
out_e = os.path.join(outdir5, "photo.png")
check("E: non-jxl input + PNG output is a real png (Pillow, not cjxl stub)",
      os.path.exists(out_e) and b"PNG" in open(out_e, "rb").read(8))
check("E: no 处理失败 in conversion log",
      not any("处理失败" in s for s in logs_e))

# --- Popen backend: ensure encode still produces a tuple via Popen ---------
conv_mod.encode = _real_encode  # restore real (uses Popen internally)
conv_mod.decode = _real_decode
ok, msg = conv_mod.encode(src_a, os.path.join(tmpdir, "real.jxl"))
check("converter.encode returns (bool, str) via Popen",
      isinstance(ok, bool) and isinstance(msg, str))

# Cleanup
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
shutil.rmtree(tmpdir2, ignore_errors=True)
shutil.rmtree(tmpdir4, ignore_errors=True)
shutil.rmtree(tmpdir5, ignore_errors=True)

print()
if failures:
    print("FAILURES=%d: %s" % (len(failures), failures))
    sys.exit(1)
print("ALL_OK")
