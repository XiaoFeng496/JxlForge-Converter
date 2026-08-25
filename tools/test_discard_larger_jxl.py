# -*- coding: utf-8 -*-
"""回归测试：输出标签页「选项」——「编码结果更大时丢弃输出（保留原文件）」开关。

覆盖：
  * discard_if_larger_check 控件存在、默认未勾选、文案正确；
  * 持久化往返：勾选/取消各存一次、新建实例恢复一致；
  * 输出格式联动：JXL 时启用、PNG/JPEG 时自动置灰禁用（仅 JXL 适用）；
  * ConvertWorker 参数透传：discard_if_larger 正确进入 worker；
  * _process_job 集成（mock _encode_source 成功，输出文件预创建为既定字节数）：
      - JXL 输出且更大 → 丢弃输出、保留原文件、discarded=True、ok=True；
      - JXL 输出且更小 → 保留输出、discarded=False；
      - PNG 输出且更大 → 不丢弃（选项对非 JXL 输出无效）、discarded=False；
  * 与「删除原文件」联动：丢弃时源不被登记（不删源）；保留时源被登记（将移回收站）。
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui-test-discard")
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_discard_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui.main_window import MainWindow, ConvertWorker

_app = QApplication.instance() or QApplication(sys.argv)


def fresh_window():
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


def _pump():
    for _ in range(3):
        QApplication.instance().processEvents()


total = 0
failures = []


def check(name, cond):
    global total
    total += 1
    if not cond:
        failures.append(name)
        print("FAIL  %s" % name)
    else:
        print("PASS  %s" % name)


# 1) 控件存在、默认未勾选、文案
w = fresh_window()
check("discard_if_larger_check 存在", hasattr(w, "discard_if_larger_check"))
check("默认未勾选", not w.discard_if_larger_check.isChecked())
check("文案含「丢弃输出」", "丢弃输出" in w.discard_if_larger_check.text())
check("文案含「保留原文件」", "保留原文件" in w.discard_if_larger_check.text())

# 2) 持久化往返
w1 = fresh_window()
w1.discard_if_larger_check.setChecked(True)
w1._save_jxl_output()
w2 = fresh_window()
check("持久化：勾选恢复", w2.discard_if_larger_check.isChecked())

w3 = fresh_window()
w3.discard_if_larger_check.setChecked(False)
w3._save_jxl_output()
w4 = fresh_window()
check("持久化：取消恢复", not w4.discard_if_larger_check.isChecked())

# 3) 输出格式联动：JXL 启用、PNG/JPEG 置灰
w5 = fresh_window()
w5.format_combo.setCurrentText("PNG (*.png)")
_pump()
check("PNG 输出时复选框禁用", not w5.discard_if_larger_check.isEnabled())
w5.format_combo.setCurrentText("JPEG (*.jpg)")
_pump()
check("JPEG 输出时复选框禁用", not w5.discard_if_larger_check.isEnabled())
w5.format_combo.setCurrentText("JPEG XL (*.jxl)")
_pump()
check("JXL 输出时复选框启用", w5.discard_if_larger_check.isEnabled())

# 4) worker 参数透传
wk = ConvertWorker([], [], discard_if_larger=True)
check("worker.discard_if_larger 透传=True", wk.discard_if_larger is True)
wk2 = ConvertWorker([], [], discard_if_larger=False)
check("worker.discard_if_larger 透传=False", wk2.discard_if_larger is False)

import libjxl_gui.main_window as mw

tmpd = tempfile.mkdtemp(prefix="libjxl_discard_")


def _make_files(src_size, dst_size):
    src = os.path.join(tmpd, "src_%d_%d.png" % (src_size, dst_size))
    dst = os.path.join(tmpd, "dst_%d_%d.jxl" % (src_size, dst_size))
    with open(src, "wb") as f:
        f.write(b"\0" * src_size)
    with open(dst, "wb") as f:
        f.write(b"\0" * dst_size)
    return src, dst


def run_job(src_size, dst_size, out_fmt="jxl", discard=True, delete_original=False):
    src, dst = _make_files(src_size, dst_size)
    wk = ConvertWorker([], [], out_fmt=out_fmt,
                       discard_if_larger=discard,
                       delete_original=delete_original)
    wk._stopped = False
    wk._total = 1
    # _record_result 会累加 run() 里初始化的统计字段；此处直接调用前先置零，
    # 避免 AttributeError（隔离测试不跑完整 run()）。
    wk._stat_started = 0
    wk._stat_processed = 0
    wk._stat_ok = 0
    wk._stat_err = 0
    wk._stat_in_bytes = 0
    wk._stat_out_bytes = 0
    # 直接打桩编码成功（不实际调用 cjxl），输出文件已由 _make_files 预创建
    # 为既定字节数，从而确定性地驱动「更大/更小」分支。
    with mock.patch.object(mw.ConvertWorker, "_encode_source",
                           return_value=(True, "ok", "tag")):
        res = wk._process_job(1, src, dst, True)
    ok, message, tag, in_size, out_size, stopped, discarded = res
    return src, dst, res, wk


# 5) JXL 输出且更大 → 丢弃
src, dst, res, wk = run_job(100, 200, out_fmt="jxl", discard=True)
ok, message, tag, in_size, out_size, stopped, discarded = res
check("JXL 更大：ok=True", ok is True)
check("JXL 更大：discarded=True", discarded is True)
check("JXL 更大：输出文件已删除", not os.path.exists(dst))
check("JXL 更大：原文件保留", os.path.exists(src))
check("JXL 更大：返回 out_size=0", out_size == 0)

# 6) JXL 输出且更小 → 保留
src, dst, res, wk = run_job(200, 100, out_fmt="jxl", discard=True)
ok, message, tag, in_size, out_size, stopped, discarded = res
check("JXL 更小：discarded=False", discarded is False)
check("JXL 更小：输出文件保留", os.path.exists(dst))

# 7) PNG 输出且更大 → 不丢弃（选项对非 JXL 无效）
src, dst, res, wk = run_job(100, 200, out_fmt="png", discard=True)
ok, message, tag, in_size, out_size, stopped, discarded = res
check("PNG 更大：discarded=False（非 JXL 不适用）", discarded is False)
check("PNG 更大：输出文件保留", os.path.exists(dst))

# 8) 与「删除原文件」联动
# 8a) 丢弃时：源不被登记（不删源）
src, dst, res, wk = run_job(100, 200, out_fmt="jxl", discard=True,
                            delete_original=True)
ok, message, tag, in_size, out_size, stopped, discarded = res
wk._record_result(1, src, ok, message, in_size, out_size, tag, discarded)
check("丢弃+删原：源未被登记（不删源）", src not in wk._ok_sources)

# 8b) 保留时：源被登记（将移回收站）
src, dst, res, wk = run_job(200, 100, out_fmt="jxl", discard=True,
                            delete_original=True)
ok, message, tag, in_size, out_size, stopped, discarded = res
wk._record_result(1, src, ok, message, in_size, out_size, tag, discarded)
check("保留+删原：源被登记（将移回收站）", src in wk._ok_sources)

print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
