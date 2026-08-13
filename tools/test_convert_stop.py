# -*- coding: utf-8 -*-
"""Headless test for the 停止 (stop) feature on the conversion worker.

Verifies:
- 停止 button is created, disabled at idle, enabled during conversion.
- Pressing 停止 requests a stop: the loop breaks early (only the first file
  starts) and the log reports 已停止.
- The in-flight child process is interrupted via converter.terminate_current().
- convert button is re-enabled and stop button disabled once finished.
"""
import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import libjxl_gui.converter as conv_mod
from libjxl_gui.main_window import MainWindow


results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


class FakeProc:
    """Stand-in for a long-running cjxl/djxl child process."""

    def __init__(self):
        self._terminated = False
        self.returncode = 0

    def poll(self):
        return None if not self._terminated else 1

    def terminate(self):
        self._terminated = True

    def communicate(self):
        # Block until termination (or up to ~1s as a safety cap).
        for _ in range(100):
            if self._terminated:
                self.returncode = 1
                return "", "terminated by user"
            time.sleep(0.01)
        return "ok", ""


def fake_run(src, out, **kwargs):
    proc = FakeProc()
    conv_mod._current_process = proc
    out_, err_ = proc.communicate()
    conv_mod._current_process = None
    return (proc.returncode == 0), (out_ or err_)


_real_encode = conv_mod.encode
_real_decode = conv_mod.decode
conv_mod.encode = fake_run
conv_mod.decode = fake_run

tmpdir = tempfile.mkdtemp()
files = []
for i in range(5):
    p = os.path.join(tmpdir, "f%d.png" % i)
    open(p, "w").close()
    files.append(p)

window = MainWindow()
window.input_files = files
window.custom_folder_radio.setChecked(True)
window.custom_folder_edit.setText(tmpdir)
window.format_combo.setCurrentText("JPEG XL (*.jxl)")

logs = []
window.log_edit.appendPlainText = lambda s: logs.append(s)
window.statusBar().showMessage = lambda s: None

# --- Idle state -----------------------------------------------------------
check("stop button disabled at idle", window.stop_button.isEnabled() is False)
check("convert button enabled at idle", window.convert_button.isEnabled() is True)

# --- Start conversion -----------------------------------------------------
window._on_convert()
worker = window._convert_worker
check("worker running after 转换", worker is not None and worker.isRunning())
check("convert button disabled during run", window.convert_button.isEnabled() is False)
check("stop button enabled during run", window.stop_button.isEnabled() is True)

# Let the first (slow) job begin, then request a stop.
time.sleep(0.15)
window._on_convert_stop()
check("stop button disabled right after press", window.stop_button.isEnabled() is False)
check("worker stop flag set", worker._stopped is True)
check("current process terminated",
      conv_mod._current_process is None or conv_mod._current_process._terminated is True)

worker.wait(10000)
# finished_signal (queued, cross-thread) is delivered here, which runs
# _on_convert_finished via its signal connection — do NOT call it manually.
for _ in range(50):
    _app.processEvents()

# --- Post-stop state ------------------------------------------------------
check("convert button re-enabled after stop", window.convert_button.isEnabled() is True)
check("stop button disabled after finish", window.stop_button.isEnabled() is False)
check("log reports 已停止", any("已停止" in s for s in logs))
check("log reports 转换停止", any("转换停止:" in s for s in logs))
check("only first file started before stop",
      sum(">>> [1/5]" in s for s in logs) == 1
      and sum(">>> [2/5]" in s for s in logs) == 0)

# Restore and cleanup
conv_mod.encode = _real_encode
conv_mod.decode = _real_decode
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
