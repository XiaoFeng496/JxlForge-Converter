# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless tests for the JXL output-parameter controls and their persistence.

Covers:
  * output-tab widgets exist with correct defaults (mode=有损, quality=90, effort=7);
  * switching to 无损 / JPG 无损重编码 disables the quality control but KEEPS
    its displayed value so toggling back reuses it;
  * _on_convert builds the right cjxl kwargs per mode (--quality / -d 0 /
    --lossless_jpeg=1) and effort is shared;
  * the mode / quality / effort survive a fresh MainWindow (QSettings round-trip).
"""

import sys
import tempfile

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

_app = QApplication.instance() or QApplication(sys.argv)
# Mirror __main__.run(): persist to a .ini file (not the registry) so running
# these tests doesn't write into the user's Windows registry.
QSettings.setDefaultFormat(QSettings.IniFormat)
# Mirror __main__.run() so QSettings (used for persistence) has a stable,
# writable location — otherwise the round-trip fails under a default name.
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini
# （%APPDATA%\libjxl\libjxl-gui.ini），否则测试残留值会让 GUI 下次启动异常。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui import converter as conv_mod
from libjxl_gui.main_window import MainWindow


def fresh_window():
    """构造 MainWindow 并触发首帧，使断言环境与真实启动一致。

    showEvent 会延迟恢复持久化的输出/输出位置/转换优先级设置，因此构造后
    必须 show() + processEvents 让首帧发生，否则这些控件停在 build 默认态、
    恢复类断言失败。跳过环境探测(_env_refreshed)避免测试内反复 subprocess。
    """
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w

failures = []
total = 0


def clear_jxl_output_settings():
    """Remove the persisted jxl_output group so the test starts from defaults
    and does not pollute the real QSettings store across runs."""
    settings = QSettings()
    settings.beginGroup("jxl_output")
    settings.remove("")  # clear every key inside the group
    settings.endGroup()


# Start from a clean slate so the "default" assertions are meaningful, and so
# a previous test run does not leak its persisted mode into this one.
clear_jxl_output_settings()


def check(name, ok):
    global total
    total += 1
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


# --- Stub converter.encode to capture the kwargs the worker passes ---------
_captured = []


def _fake_encode(input_path, output_path, **kwargs):
    _captured.append((input_path, output_path, dict(kwargs)))
    return True, "fake cjxl ok", ""


_real_encode = conv_mod.encode
conv_mod.encode = _fake_encode


def run_convert_and_capture(window):
    """Drive one conversion on the given window and return the last captured
    encode kwargs (or None if nothing was encoded). The worker's
    finished_signal drives _on_convert_finished via the event loop, so we only
    pump processEvents() (no manual call, which would double-invoke it)."""
    _captured.clear()
    tmpdir = tempfile.mkdtemp()
    # Use a .jpg source: in JPG 无损重编码 mode only JPG inputs are kept
    # (non-JPG are skipped), so a .jpg keeps the capture valid for every mode.
    src = tmpdir + "/a.jpg"
    open(src, "w").close()
    window.input_files = [src]
    window.custom_folder_radio.setChecked(True)
    window.custom_folder_edit.setText(tmpdir)
    window.format_combo.setCurrentText("JPEG XL (*.jxl)")
    window._on_convert()
    worker = window._convert_worker
    if worker is not None:
        worker.wait(10000)
    # Pump the event loop so the queued finished_signal -> _on_convert_finished
    # is delivered (it resets _convert_worker itself).
    for _ in range(50):
        QApplication.instance().processEvents()
    return _captured[-1][2] if _captured else None


# ---------------------------------------------------------------------------
# 1. Widget existence + defaults
# ---------------------------------------------------------------------------
w = fresh_window()
check("mode radios exist",
      hasattr(w, "lossy_radio") and hasattr(w, "lossless_radio")
      and hasattr(w, "lossless_jpeg_radio"))
check("default mode is 有损 (lossy)",
      w._current_encode_mode() == "lossy")
check("quality slider range 0-100",
      w.quality_slider.minimum() == 0 and w.quality_slider.maximum() == 100)
check("quality default 90",
      w.quality_spin.value() == 90 and w.quality_slider.value() == 90)
check("quality enabled in lossy mode", w.quality_spin.isEnabled() is True)
check("effort combo default 7",
      w.effort_combo.currentText() == "7")
check("effort combo covers 1..9",
      [w.effort_combo.itemText(i) for i in range(w.effort_combo.count())]
      == [str(i) for i in range(1, 10)])

# ---------------------------------------------------------------------------
# 2. Mode switching disables quality but keeps its value
# ---------------------------------------------------------------------------
w.quality_spin.setValue(75)
w.lossless_radio.setChecked(True)
check("quality disabled in 无损 mode",
      w.quality_spin.isEnabled() is False and w.quality_slider.isEnabled() is False)
check("quality value retained (75) while disabled",
      w.quality_spin.value() == 75)
check("mode reports lossless", w._current_encode_mode() == "lossless")

w.lossless_jpeg_radio.setChecked(True)
check("quality disabled in JPG 无损重编码 mode",
      w.quality_spin.isEnabled() is False)
check("mode reports lossless_jpeg", w._current_encode_mode() == "lossless_jpeg")

w.lossy_radio.setChecked(True)
check("quality re-enabled back in lossy mode",
      w.quality_spin.isEnabled() is True)
check("quality value still 75 after toggle back",
      w.quality_spin.value() == 75)

# ---------------------------------------------------------------------------
# 3. _on_convert builds correct cjxl kwargs per mode
# ---------------------------------------------------------------------------
# lossy
w.lossy_radio.setChecked(True)
w.quality_spin.setValue(80)
w.effort_combo.setCurrentText("5")
kw = run_convert_and_capture(w)
check("lossy kwargs: quality=80", kw.get("quality") == 80)
check("lossy kwargs: effort=5", kw.get("effort") == 5)
check("lossy kwargs: distance is None", kw.get("distance") is None)
check("lossy kwargs: lossless_jpeg False", kw.get("lossless_jpeg") is False)

# lossless
w.lossless_radio.setChecked(True)
w.effort_combo.setCurrentText("9")
kw = run_convert_and_capture(w)
check("lossless kwargs: distance=0", kw.get("distance") == 0)
check("lossless kwargs: quality is None", kw.get("quality") is None)
check("lossless kwargs: lossless_jpeg False", kw.get("lossless_jpeg") is False)
check("lossless kwargs: effort=9 still applied", kw.get("effort") == 9)

# lossless_jpeg
w.lossless_jpeg_radio.setChecked(True)
kw = run_convert_and_capture(w)
check("lossless_jpeg kwargs: lossless_jpeg True",
      kw.get("lossless_jpeg") is True)
check("lossless_jpeg kwargs: distance is None", kw.get("distance") is None)
check("lossless_jpeg kwargs: quality is None", kw.get("quality") is None)

# ---------------------------------------------------------------------------
# 4. Persistence: a fresh window restores the last params
# ---------------------------------------------------------------------------
# Set a distinct state on w and persist it.
w.lossless_jpeg_radio.setChecked(True)
w.quality_spin.setValue(42)  # retained even though disabled
w.effort_combo.setCurrentText("8")
w._save_jxl_output()

w2 = fresh_window()  # its __init__ calls _load_jxl_output()
check("persisted mode restored (lossless_jpeg)",
      w2.lossless_jpeg_radio.isChecked() is True)
check("persisted effort restored (8)",
      w2.effort_combo.currentText() == "8")
check("persisted quality restored (42) on disabled control",
      w2.quality_spin.value() == 42
      and w2.quality_spin.isEnabled() is False)

# Reset mode to lossy on w2 -> quality control should re-enable with 42.
w2.lossy_radio.setChecked(True)
check("quality re-enabled after restoring to lossy",
      w2.quality_spin.isEnabled() is True and w2.quality_spin.value() == 42)

# Restore the real encode so other test modules (if any) are unaffected.
conv_mod.encode = _real_encode

# Clean up the persisted group so running this test does not leave stale
# jxl_output values in the user's QSettings (which the real app would read).
clear_jxl_output_settings()

print("\n%d/%d checks passed" % (total - len(failures), total))
print("ALL_OK" if not failures else "FAILED: " + ", ".join(failures))
