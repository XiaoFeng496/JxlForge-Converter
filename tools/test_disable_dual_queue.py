# -*- coding: utf-8 -*-
"""回归测试：高级参数子项「停用大图双队列（仅建议 effort10 下启用）」。

effort 10 下 libjxl 会禁用 chunked encoding（见 libjxl 官方 doc/encode_effort.md），
单张图能吃到的并行线程数很低，此时「大图独占满核 + 逐张串行」会把空闲核心白放着。
本开关启用后整批进入同一个并行池，进程数受子选项「并行进程覆盖上限」约束。

覆盖：
  - 生效条件（母开关 + 子项两者同时勾选）；
  - _flat_pool_size 取 min(文件数, 有效核心数, 进程上限)，不超订；
  - run() 走全批并行池（不再分大图 / 小图），状态页日志行正确；
  - 「并行进程上限」子选项随母开关 / 子项联动置灰；
  - 勾选态与进程上限值持久化往返。

QSettings 重定向到临时目录，测试永不触碰真实 ini。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# QSettings 隔离必须早于被测模块导入，否则会污染真实 ini。
_TMP = tempfile.mkdtemp(prefix="jxlforge_flat_pool_")
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _TMP)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter")
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge.main_window import (  # noqa: E402
    DEFAULT_FLAT_POOL_CAP,
    ConvertWorker,
)

from PIL import Image  # noqa: E402

_results = []


def check(name, cond):
    _results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


def _new_window():
    """新建一个隔离实例（MainWindow 构造会读回 QSettings）。

    启动时先屏蔽「首次启用高级参数」的注意事项弹窗：``_on_adv_threads_toggled``
    在非加载期首次勾选会 ``_maybe_warn_adv_params()`` 弹模态 ``QMessageBox.exec()``，
    offscreen 下没有事件循环会直接卡死（超时被 SIGTERM 杀掉）。
    """
    from jxlforge.main_window import MainWindow
    win = MainWindow()
    win._adv_warning_suppressed = True
    return win


def _make_jobs(dims):
    d = tempfile.mkdtemp()
    jobs = []
    for i, (w, h) in enumerate(dims):
        src = os.path.join(d, "img%d.png" % i)
        Image.new("RGB", (w, h), (i * 20 % 256, 50, 100)).save(src, "PNG")
        jobs.append((src, src + ".jxl", False))
    return jobs


# --- 1. 生效条件：母开关 + 子项同时勾选 -------------------------------------
w_off = ConvertWorker([], [], cpu_cores=8)
check("_flat_pool_enabled 默认关闭", w_off._flat_pool_enabled() is False)

w_master_only = ConvertWorker([], [], cpu_cores=8, adv_threads_enabled=True)
check("只开母开关仍不生效", w_master_only._flat_pool_enabled() is False)

w_sub_only = ConvertWorker([], [], cpu_cores=8, adv_disable_dual_queue=True)
check("只开子项仍不生效（母开关为前提）", w_sub_only._flat_pool_enabled() is False)

w_on = ConvertWorker([], [], cpu_cores=8,
                     adv_threads_enabled=True,
                     adv_disable_dual_queue=True)
check("母开关 + 子项 -> 生效", w_on._flat_pool_enabled() is True)

# --- 2. _flat_pool_size 取三者最小值，不超订 --------------------------------
check("size: k=10/cores=20/cap=默认 -> 4（上限生效）",
      w_on._flat_pool_size(10) == min(10, 20, DEFAULT_FLAT_POOL_CAP))
check("size: k=2 -> 文件数优先", w_on._flat_pool_size(2) == 2)
check("size: k=0/None -> 至少 1", w_on._flat_pool_size(0) == 1
      and w_on._flat_pool_size(None) == 1)

w_lowcore = ConvertWorker([], [], cpu_cores=2, adv_threads_enabled=True,
                          adv_disable_dual_queue=True, flat_pool_cap=16)
check("size: 核心数封顶（2 核下不会开 16 进程）",
      w_lowcore._flat_pool_size(10) == 2)
check("size: 不受未生效的 cap=16 放大", w_on._flat_pool_size(100) <= 20)

w_cap2 = ConvertWorker([], [], cpu_cores=20, adv_threads_enabled=True,
                       adv_disable_dual_queue=True, flat_pool_cap=2)
check("size: 自定义 cap=2 生效", w_cap2._flat_pool_size(10) == 2)

# --- 3. run() 走全批并行池（不再分大图 / 小图）-----------------------------
jobs = _make_jobs([(64, 64), (64, 64), (64, 64)])
w_run = ConvertWorker(jobs, [], cpu_cores=8,
                      adv_threads_enabled=True,
                      adv_disable_dual_queue=True,
                      flat_pool_cap=3)
logs = []
w_run.log_signal.connect(logs.append)
w_run.run()  # 同步跑（不 start），与 test_dual_queue 一致
log_text = "\n".join(logs)
check("日志：报出「已停用大图双队列」", "已停用大图双队列" in log_text)
check("日志：不再报「小图 / 大图」双队列分类行",
      "小图" not in log_text or "调度分类：小图" not in log_text)

# 对照：同样批次未启用时仍走双队列分类行
w_dual = ConvertWorker(jobs, [], cpu_cores=8)
logs2 = []
w_dual.log_signal.connect(logs2.append)
w_dual.run()
check("对照：未启用时日志为双队列分类行",
      "调度分类：小图" in "\n".join(logs2))

# --- 4. UI 联动：子选项可用性 -----------------------------------------------
mw = _new_window()
cap_combo = mw.adv_flat_pool_cap_combo
check("子选项初始置灰（母开关未开）", cap_combo.isEnabled() is False)
mw.adv_threads_toggle.setChecked(True)
check("母开关开后子项按钮解锁", mw.adv_disable_dual_queue_toggle.isEnabled() is True)
check("母开关开后子选项仍置灰（子项未勾）", cap_combo.isEnabled() is False)
mw.adv_disable_dual_queue_toggle.setChecked(True)
check("子项勾选后子选项解锁", cap_combo.isEnabled() is True)
mw.adv_threads_toggle.setChecked(False)
check("母开关关 -> 子选项重新置灰", cap_combo.isEnabled() is False)
mw.close()

# --- 5. 持久化往返 ----------------------------------------------------------
mw2 = _new_window()
mw2.adv_threads_toggle.setChecked(True)
mw2.adv_disable_dual_queue_toggle.setChecked(True)
mw2.adv_flat_pool_cap_combo.setCurrentIndex(
    mw2.adv_flat_pool_cap_combo.findData(2)
)
mw2.adv_threads_toggle.setChecked(False)
mw2.close()

mw3 = _new_window()
check("持久化：子项勾选恢复", mw3.adv_disable_dual_queue_toggle.isChecked() is True)
check("持久化：进程上限恢复为 2",
      mw3.adv_flat_pool_cap_combo.currentData() == 2)
mw3.adv_threads_toggle.setChecked(True)
cap = mw3._flat_pool_cap_value()
check("_flat_pool_cap_value 返回 int 2", isinstance(cap, int) and cap == 2)
# 非法值回退默认值
mw3._set_flat_pool_cap(999)
check("越界值回退默认上限", mw3._flat_pool_cap_value() == DEFAULT_FLAT_POOL_CAP)
mw3.close()

# --- 6. UI → worker 传参链路（防止开关取错导致「勾了却不生效」）-------------
# 回归背景：_build_convert_worker_kwargs 里的 adv_threads_enabled 曾经误取子项
# 「手动设置每文件线程数」的勾选态，而 worker 的 _flat_pool_enabled() 又要求它
# 为真 —— 结果用户只勾「停用大图双队列」而不勾「手动设置每文件线程数」时，
# 界面上明明勾了、跑起来却仍走双队列。这里锁死「UI 勾选态 → worker 参数」。
mw4 = _new_window()
mw4.adv_threads_toggle.setChecked(True)
mw4.adv_disable_dual_queue_toggle.setChecked(True)
# 故意不勾子项「手动设置每文件线程数」：这正是原先失效的那个组合。
mw4.adv_num_threads_toggle.setChecked(False)
kw = mw4._build_convert_worker_kwargs(
    cpu_priority=mw4.cpu_priority_combo.currentData(),
    advanced={}, custom_cmd=None,
    cpu_cores=mw4.cpu_cores_combo.currentData(),
    out_fmt="jxl",
)
check("传参：adv_threads_enabled 取母开关（非子项）", kw["adv_threads_enabled"] is True)
check("传参：adv_disable_dual_queue 为真", kw["adv_disable_dual_queue"] is True)
worker_kw = ConvertWorker([], [], 7, 0.0, -1, False, **kw)
check("传参：worker 侧判定生效", worker_kw._flat_pool_enabled() is True)
check("传参：进程上限透传", worker_kw.flat_pool_cap == DEFAULT_FLAT_POOL_CAP)

# 母开关关闭时整条链路都要退回不生效
mw4.adv_threads_toggle.setChecked(False)
kw_off = mw4._build_convert_worker_kwargs(
    cpu_priority=mw4.cpu_priority_combo.currentData(),
    advanced={}, custom_cmd=None,
    cpu_cores=mw4.cpu_cores_combo.currentData(),
    out_fmt="jxl",
)
check("传参：母开关关 -> adv_threads_enabled 假", kw_off["adv_threads_enabled"] is False)
check("传参：母开关关 -> 停用双队列不生效", kw_off["adv_disable_dual_queue"] is False)
mw4.close()

# --- 汇总 -------------------------------------------------------------------
failed = [n for n, ok in _results if not ok]
print("\n%d/%d passed" % (len(_results) - len(failed), len(_results)))
if failed:
    print("FAILED:")
    for n in failed:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
