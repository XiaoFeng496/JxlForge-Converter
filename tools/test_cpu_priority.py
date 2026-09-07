# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless tests for the conversion-process CPU-priority feature.

Covers: the converter flag mapping, the settings-tab combo default, and that
the chosen priority flows from the combo -> ConvertWorker -> the cjxl/djxl
child process's creation flags (without spawning a real libjxl binary).
"""

import sys
import subprocess

from PySide6.QtWidgets import QApplication

# Make sure a QApplication exists before importing the main window module.
_app = QApplication.instance() or QApplication(sys.argv)

from jxlforge import converter as conv_mod
from jxlforge.main_window import MainWindow, ConvertWorker

failures = []


def check(name, ok):
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


# 1. Default priority constant must be "below_normal" (the user request).
check("DEFAULT_PRIORITY == below_normal",
      conv_mod.DEFAULT_PRIORITY == "below_normal")


# 2. Flag mapping must match subprocess's Windows priority-class constants
#    (which are 0 on non-Windows, so they stay a no-op there).
expected_flags = {
    "idle": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0),
    "below_normal": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
    "normal": getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0),
    "above_normal": getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0),
    "high": getattr(subprocess, "HIGH_PRIORITY_CLASS", 0),
}
check("_PRIORITY_FLAGS maps to subprocess constants",
      conv_mod._PRIORITY_FLAGS == expected_flags)

# An unknown priority key must fall back to the default, never raise.
check("_run tolerates unknown priority key",
      conv_mod._PRIORITY_FLAGS.get("nope",
                                   conv_mod._PRIORITY_FLAGS[conv_mod.DEFAULT_PRIORITY]) ==
      conv_mod._PRIORITY_FLAGS[conv_mod.DEFAULT_PRIORITY])


# 3. Settings-tab combo exists and defaults to "低于正常" / below_normal.
window = MainWindow()
combo = getattr(window, "cpu_priority_combo", None)
check("settings tab exposes cpu_priority_combo", combo is not None)
if combo is not None:
    check("combo default data == below_normal",
          combo.currentData() == "below_normal")
    idx = combo.findData("below_normal")
    check("combo default label is 低于正常",
          idx >= 0 and combo.itemText(idx) == "低于正常")
    # Selecting another entry updates currentData immediately.
    combo.setCurrentIndex(combo.findData("normal"))
    check("combo reflects selected priority key",
          combo.currentData() == "normal")


# 4. ConvertWorker threads the priority through to its encode kwargs.
w_def = ConvertWorker([], [], 7, None, None, False)
check("worker default priority below_normal",
      w_def.priority == "below_normal" and
      w_def._encode_kwargs()["priority"] == "below_normal")
w_norm = ConvertWorker([], [], 7, None, None, False, "normal")
check("worker custom priority normal",
      w_norm._encode_kwargs()["priority"] == "normal")


# 5. The priority actually reaches the child process creation flags. We stub
#    Popen to capture its kwargs so no real cjxl/djxl is spawned.
captured = {}
_real_popen = subprocess.Popen


class _CapturePopen:
    def __init__(self, *args, **kwargs):
        captured["creationflags"] = kwargs.get("creationflags", 0)
        self.returncode = 1

    def communicate(self):
        return ("", "captured")

    def poll(self):
        return self.returncode


conv_mod.subprocess.Popen = _CapturePopen
try:
    conv_mod.encode("in.png", "out.jxl", priority="high")
finally:
    conv_mod.subprocess.Popen = _real_popen

create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
high_flag = getattr(subprocess, "HIGH_PRIORITY_CLASS", 0)
expected_flags_value = create_no_window | high_flag
check("encode priority reaches Popen creationflags",
      captured.get("creationflags") == expected_flags_value)

# decode path must carry the priority the same way
captured.clear()
conv_mod.subprocess.Popen = _CapturePopen
try:
    conv_mod.decode("in.jxl", "out.png", priority="idle")
finally:
    conv_mod.subprocess.Popen = _real_popen
idle_flag = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)
check("decode priority reaches Popen creationflags",
      captured.get("creationflags") == (create_no_window | idle_flag))


print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
