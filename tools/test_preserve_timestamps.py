# -*- coding: utf-8 -*-
"""回归测试：输出标签页「选项」——「保持原创建时间」/「保持原修改时间」开关。

覆盖：
  * preserve_ctime_check / preserve_mtime_check 控件存在、默认未勾选、文案正确；
  * 持久化往返：分别勾选/取消各存一次、新建实例恢复一致；
  * ConvertWorker 参数透传：preserve_ctime / preserve_mtime 正确进入 worker；
  * _preserve_mtime：真实把 dst 的修改时间设为与 src 一致；
  * _preserve_ctime：非 Windows 静默返回；Windows + 注入假 pywin32 验证调用 SetFileTime
    且传入的创建时间参数等于 src 的 st_ctime；
  * _process_job 集成：成功转换（mock converter.encode）后自动保持修改时间。
"""
import os
import sys
import types
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter-test-prests")
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_prests_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from jxlforge.main_window import (
    MainWindow,
    ConvertWorker,
    _preserve_mtime,
    _preserve_ctime,
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


# 1) 控件存在、默认未勾选、文案
w = fresh_window()
check("preserve_ctime_check 存在", hasattr(w, "preserve_ctime_check"))
check("preserve_mtime_check 存在", hasattr(w, "preserve_mtime_check"))
check("默认未勾选：创建时间", not w.preserve_ctime_check.isChecked())
check("默认未勾选：修改时间", not w.preserve_mtime_check.isChecked())
check("文案含「保持原创建时间」", "保持原创建时间" in w.preserve_ctime_check.text())
check("文案含「保持原修改时间」", "保持原修改时间" in w.preserve_mtime_check.text())

# 2) 持久化往返
w1 = fresh_window()
w1.preserve_ctime_check.setChecked(True)
w1._save_jxl_output()
w2 = fresh_window()
check("持久化：创建时间勾选恢复", w2.preserve_ctime_check.isChecked())

w3 = fresh_window()
w3.preserve_mtime_check.setChecked(True)
w3._save_jxl_output()
w4 = fresh_window()
check("持久化：修改时间勾选恢复", w4.preserve_mtime_check.isChecked())

w5 = fresh_window()
w5.preserve_ctime_check.setChecked(False)
w5.preserve_mtime_check.setChecked(False)
w5._save_jxl_output()
w6 = fresh_window()
check("持久化：取消恢复到取消",
      not w6.preserve_ctime_check.isChecked()
      and not w6.preserve_mtime_check.isChecked())

# 3) worker 参数透传
wk = ConvertWorker([], [], preserve_ctime=True, preserve_mtime=False)
check("worker.preserve_ctime 透传=True", wk.preserve_ctime is True)
check("worker.preserve_mtime 透传=False", wk.preserve_mtime is False)
wk2 = ConvertWorker([], [], preserve_ctime=False, preserve_mtime=True)
check("worker.preserve_mtime 透传=True", wk2.preserve_mtime is True)

# 4) _preserve_mtime 真实生效
tmpd = tempfile.mkdtemp(prefix="libjxl_prests_")
src = os.path.join(tmpd, "src.png")
open(src, "w").close()
dst = os.path.join(tmpd, "dst.png")
open(dst, "w").close()
old_ns = 1_600_000_000_000_000_000  # 约 2020-09
os.utime(src, ns=(old_ns, old_ns))
_preserve_mtime(src, dst)
check("_preserve_mtime：dst.mtime == src.mtime",
      abs(os.stat(dst).st_mtime - os.stat(src).st_mtime) < 2.0)

# 5) _preserve_ctime：非 Windows 静默返回
with mock.patch("sys.platform", "linux"):
    try:
        _preserve_ctime(src, dst)
        check("_preserve_ctime：非 Windows 静默返回", True)
    except Exception:
        check("_preserve_ctime：非 Windows 静默返回", False)

# 6) _preserve_ctime：Windows + 注入假 pywin32 验证调用
fake_pywintypes = types.ModuleType("pywintypes")
fake_pywintypes.Time = lambda x: x
fake_win32file = types.ModuleType("win32file")
fake_win32file.FILE_WRITE_ATTRIBUTES = 1
fake_win32file.FILE_SHARE_READ = 2
fake_win32file.FILE_SHARE_WRITE = 4
fake_win32file.FILE_SHARE_DELETE = 8
fake_win32file.OPEN_EXISTING = 3
fake_win32file.FILE_ATTRIBUTE_NORMAL = 128
_calls = {}


class _FakeHandle:
    """模拟 pywin32 的 PyHANDLE：需提供 Close() 方法。"""
    def Close(self):
        _calls["closed"] = True


def _fake_create(*a, **k):
    _calls["create"] = (a, k)
    return _FakeHandle()


def _fake_settime(h, ctime, atime, mtime):
    _calls["settime"] = (ctime, atime, mtime)


fake_win32file.CreateFile = _fake_create
fake_win32file.SetFileTime = _fake_settime
src2 = os.path.join(tmpd, "src2.png")
open(src2, "w").close()
dst2 = os.path.join(tmpd, "dst2.png")
open(dst2, "w").close()
st = os.stat(src2)
with mock.patch.dict(sys.modules, {"pywintypes": fake_pywintypes,
                                   "win32file": fake_win32file}), \
     mock.patch("sys.platform", "win32"):
    _preserve_ctime(src2, dst2)
check("_preserve_ctime：Windows 调用 SetFileTime 一次", "settime" in _calls)
check("_preserve_ctime：创建时间参数==src.st_ctime",
      _calls.get("settime", (None, None, None))[0] == st.st_ctime)

# 7) _process_job 集成：成功转换后自动保持修改时间
import jxlforge.main_window as mw

src3 = os.path.join(tmpd, "pj_src.png")
open(src3, "w").close()
dst3 = os.path.join(tmpd, "pj_dst.jxl")
open(dst3, "w").close()  # 预创建；真正由 mock encode 写入
os.utime(src3, ns=(old_ns, old_ns))
wk3 = ConvertWorker([], [], preserve_mtime=True, preserve_ctime=False)
wk3._stopped = False
wk3._total = 1


def _fake_encode(enc_src, enc_dst, **kw):
    # 模拟 cjxl 成功生成输出文件（否则 _preserve_mtime 找不到目标）。
    open(enc_dst, "w").close()
    return (True, "ok", "tag")


with mock.patch.object(mw.converter, "encode", side_effect=_fake_encode):
    res = wk3._process_job(1, src3, dst3, False)
check("_process_job：集成返回成功", res[0] is True)
check("_process_job：dst 修改时间已保持为 src",
      abs(os.stat(dst3).st_mtime - os.stat(src3).st_mtime) < 2.0)

print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
