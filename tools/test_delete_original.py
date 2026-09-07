# -*- coding: utf-8 -*-
"""回归测试：输出标签页「选项」区域——「删除原文件」开关。

覆盖：
  * delete_original_check 控件存在、默认未勾选、文案含"删除原文件"；
  * 持久化往返：勾选/取消各存一次、新建实例恢复一致；
  * ConvertWorker 收集成功源：delete_original=True 时仅成功源入 _ok_sources，
    失败源不入列；delete_original=False 时成功源也不入列；
  * _move_to_recycle_bin：通过 mock SHFileOperationW 验证调用一次且不抛异常、
    返回非零时抛 OSError（不实际改动磁盘）。
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter-test-delorig")
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_delorig_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from jxlforge.main_window import (
    MainWindow,
    ConvertWorker,
    _move_to_recycle_bin,
    send2trash,
)

_app = QApplication.instance() or QApplication(sys.argv)


def fresh_window():
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


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


# 1) 控件存在、默认未勾选、文案含"删除原文件"
w = fresh_window()
check("delete_original_check 存在", hasattr(w, "delete_original_check"))
check("默认未勾选", not w.delete_original_check.isChecked())
check("文案含「删除原文件」", "删除原文件" in w.delete_original_check.text())

# 2) 持久化往返：勾选 → 恢复勾选；取消 → 恢复取消
w1 = fresh_window()
w1.delete_original_check.setChecked(True)
w1._save_jxl_output()
w2 = fresh_window()
check("持久化往返：勾选恢复为勾选", w2.delete_original_check.isChecked())

w3 = fresh_window()
w3.delete_original_check.setChecked(False)
w3._save_jxl_output()
w4 = fresh_window()
check("持久化往返：取消恢复为取消", not w4.delete_original_check.isChecked())

# 3) ConvertWorker 收集成功源（不实际跑转换，直接驱动 _record_result）
def make_worker(delete_original):
    wk = ConvertWorker([], [], delete_original=delete_original)
    # _record_result 依赖的运行期统计字段（run() 中初始化），测试手动补齐。
    wk._total = 1
    wk._stat_processed = 0
    wk._stat_in_bytes = 0
    wk._stat_out_bytes = 0
    wk._stat_ok = 0
    wk._stat_err = 0
    wk._ok_sources = []
    return wk


tmpd = tempfile.mkdtemp(prefix="libjxl_delorig_")
src_ok = os.path.join(tmpd, "ok.png")
src_bad = os.path.join(tmpd, "bad.png")
open(src_ok, "w").close()
open(src_bad, "w").close()

wk_on = make_worker(True)
wk_on._record_result(1, src_ok, True, "ok", 100, 50, "[tag]")
wk_on._record_result(1, src_bad, False, "fail", 100, 0, "")
check("delete=True：仅成功源入 _ok_sources",
      wk_on._ok_sources == [src_ok])
check("delete=True：失败源不入列", src_bad not in wk_on._ok_sources)

wk_off = make_worker(False)
wk_off._record_result(1, src_ok, True, "ok", 100, 50, "[tag]")
check("delete=False：成功源也不入 _ok_sources", wk_off._ok_sources == [])

# 4) _move_to_recycle_bin（mock send2trash.send2trash，不实际改动磁盘）
with mock.patch.object(send2trash, "send2trash", return_value=None) as m:
    f = os.path.join(tmpd, "to_trash.txt")
    open(f, "w").close()
    try:
        _move_to_recycle_bin(f)
    except Exception as exc:
        check("mock 成功路径不抛异常", False)
        print("    实际异常：%s" % exc)
    else:
        check("mock 成功路径不抛异常", True)
    check("send2trash.send2trash 被调用一次", m.call_count == 1)
    check("mock 路径下原文件仍在（未真正删除）", os.path.exists(f))

with mock.patch.object(send2trash, "send2trash",
                      side_effect=OSError("模拟回收站失败")) as m2:
    f2 = os.path.join(tmpd, "to_trash2.txt")
    open(f2, "w").close()
    raised = False
    try:
        _move_to_recycle_bin(f2)
    except OSError:
        raised = True
    check("send2trash 抛异常时本函数透传（调用方据此保留原文件）", raised)
    check("错误路径下原文件仍在（安全保留）", os.path.exists(f2))


print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
