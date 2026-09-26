# -*- coding: utf-8 -*-
"""Headless test for the two-stage 停止 (stop) feature on the conversion worker.

两段式停止：
- 第一段（点「停止」）：优雅停止——置位 _stopped，调度层不再派发新文件，
  当前在途任务自然跑完；按钮变为「强制停止」并保持可点击，此时不杀进程。
- 第二段（点「强制停止」）：请求 request_force_stop，立即杀掉在途子进程。

Verifies:
- 停止 button created, disabled at idle, enabled during conversion.
- First press: button stays enabled, text -> 强制停止, _stopped set,
  terminate_current NOT yet called (在途进程未被杀).
- Second press: button disabled, terminate_current called, FakeProc terminated.
- 并行池下日志顺序不变量（>>> [N] 头紧接大小/失败行）。
- finished 后 convert button 重新启用、stop button 禁用且文案复位为「停止」。
"""
import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import jxlforge.converter as conv_mod
from jxlforge.main_window import MainWindow


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
    return (proc.returncode == 0), (out_ or err_), ""


_real_encode = conv_mod.encode
_real_decode = conv_mod.decode
conv_mod.encode = fake_run
conv_mod.decode = lambda *a, **k: (True, "")

# spy on terminate_current：统计是否真的杀过进程
_terminate_calls = []
_real_terminate = conv_mod.terminate_current


def _spy_terminate():
    _terminate_calls.append(1)
    _real_terminate()


conv_mod.terminate_current = _spy_terminate

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
check("空闲时 stop 按钮禁用", window.stop_button.isEnabled() is False)
check("空闲时 convert 按钮启用", window.convert_button.isEnabled() is True)

# --- Start conversion -----------------------------------------------------
window._on_convert()
worker = window._convert_worker
check("点转换后 worker 在跑", worker is not None and worker.isRunning())
check("运行中 convert 按钮禁用", window.convert_button.isEnabled() is False)
check("运行中 stop 按钮启用", window.stop_button.isEnabled() is True)
check("启动时 stop 按钮文案为「停止」", window.stop_button.text() == "停止")

# --- First press: 优雅停止 -------------------------------------------------
time.sleep(0.15)
window._on_convert_stop()
check("第一段后按钮仍可用（可点强制停止）", window.stop_button.isEnabled() is True)
check("第一段后按钮文案变为「强制停止」", window.stop_button.text() == "强制停止")
check("第一段后 worker _stopped 置位", worker._stopped is True)
check("第一段未调用 terminate_current（不杀进程）", len(_terminate_calls) == 0)
check("第一段后 FakeProc 未被终止",
      conv_mod._current_process is None or conv_mod._current_process._terminated is False)

# --- Second press: 强制停止 -----------------------------------------------
window._on_convert_stop()
check("第二段后按钮禁用", window.stop_button.isEnabled() is False)
check("第二段调用了 terminate_current", len(_terminate_calls) >= 1)
check("第二段后 FakeProc 被终止",
      conv_mod._current_process is None or conv_mod._current_process._terminated is True)

worker.wait(10000)
# finished_signal (queued, cross-thread) is delivered here, which runs
# _on_convert_finished via its signal connection — do NOT call it manually.
for _ in range(50):
    _app.processEvents()

# --- Post-stop state ------------------------------------------------------
check("停止后 convert 按钮重新启用", window.convert_button.isEnabled() is True)
check("结束后 stop 按钮禁用", window.stop_button.isEnabled() is False)
check("结束后 stop 按钮文案复位为「停止」", window.stop_button.text() == "停止")
check("log 报告 已停止", any("已停止" in s for s in logs))
check("log 报告 转换停止", any("转换停止：" in s for s in logs))
# 顺序修复不变量：每个 >>> [N] 头必紧接其大小/失败行（头以 \t 或「处理失败」开头）。
header_idx = [i for i, s in enumerate(logs) if s.startswith(">>> [")]
orphan = False
for i in header_idx:
    nxt = logs[i + 1] if i + 1 < len(logs) else ""
    if not (nxt.startswith("\t") or nxt.startswith("处理失败")):
        orphan = True
        break
check("每个 >>> [N] 头紧接其大小/失败行（无串位）", not orphan)

# Restore and cleanup
conv_mod.encode = _real_encode
conv_mod.decode = _real_decode
conv_mod.terminate_current = _real_terminate
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
