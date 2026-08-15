# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless regression tests for the "CPU 核心使用数" + 文件级并行池 feature.

Covers:
  1. 设置标签暴露 cpu_cores_combo（默认 自动 / "auto"，选项 1..逻辑核心数）。
  2. 暴露 adv_threads_toggle（默认未勾选），且联动隐藏/启用高级参数
     num_threads 控件 + 提示文字随之切换。
  3. 单文件：--num_threads 用满全部核心（pool_size=1）。
  4. 多文件：K 个并行进程，每文件 --num_threads=1（不超订）。
  5. 启用高级参数后，每文件线程数取用户设定值，并行进程数随之收缩。

不启动任何真实的 cjxl/djxl 二进制——只验证设置→ConvertWorker→
_encode_kwargs 的映射与 _resolve_concurrency 的分发逻辑。
"""

from PySide6.QtWidgets import QApplication, QCheckBox, QSpinBox

# 必须在线程创建前确保有 QApplication 实例。
_app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui.main_window import MainWindow, ConvertWorker

failures = []

LOGICAL = os.cpu_count() or 1


def check(name, ok):
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


# 1. 设置标签暴露 cpu_cores_combo，默认 "自动"。
window = MainWindow()
combo = getattr(window, "cpu_cores_combo", None)
check("设置标签暴露 cpu_cores_combo", combo is not None)
if combo is not None:
    check("cpu_cores_combo 默认 data == auto",
          combo.currentData() == "auto")
    idx = combo.findData("auto")
    check("cpu_cores_combo 默认标签为 自动",
          idx >= 0 and combo.itemText(idx) == "自动")
    # 选项应覆盖 1..逻辑核心数。
    texts = [combo.itemText(i) for i in range(combo.count())]
    expected_texts = ["自动"] + [str(n) for n in range(1, LOGICAL + 1)]
    check("cpu_cores_combo 选项 = [自动, 1..逻辑核心数]", texts == expected_texts)
    # 选定整数后 currentData 立即反映该 int。
    combo.setCurrentIndex(combo.findData(LOGICAL))
    check("cpu_cores_combo 选定整数后 currentData 为 int",
          combo.currentData() == LOGICAL)


# 2. 暴露 adv_threads_toggle，且默认未勾选。
toggle = getattr(window, "adv_threads_toggle", None)
check("暴露 adv_threads_toggle", toggle is not None)
if toggle is not None:
    check("adv_threads_toggle 默认未勾选",
          toggle.isChecked() is False)


# 3. adv_threads_toggle 联动启用/隐藏高级参数 num_threads 控件，且说明文字并入
#    开关的悬停浮窗（不再有独立小字 QLabel）。
num_check = QCheckBox()
num_val = QSpinBox()
# 模拟 _collect_advanced 写入的 _adv_widgets 结构：(check, val_w, schema)
# 先保留真实 _adv_widgets 引用，供第 7 段（effort 门控）恢复，避免误触 save 时缺键。
real_adv_widgets = window._adv_widgets
window._adv_widgets = {"num_threads": (num_check, num_val, {})}
tip_toggle = window.adv_threads_toggle

# 关闭高级参数：num_threads 整体禁用；tooltip 说明由并行池自动控核。
window._apply_adv_threads_state(False)
check("关闭时 num_threads check 被禁用", num_check.isEnabled() is False)
check("关闭时 num_threads 值控件被禁用", num_val.isEnabled() is False)
check("关闭时说明文字并入开关悬停浮窗（并行池自动控制）",
      "自动分配" in tip_toggle.toolTip() or "自动" in tip_toggle.toolTip())

# 打开高级参数，但 num_threads check 未勾选：check 启用，值控件仍禁用。
num_check.setChecked(False)
window._apply_adv_threads_state(True)
check("打开时 num_threads check 被启用", num_check.isEnabled() is True)
check("打开但 check 未勾选时值控件仍禁用",
      num_val.isEnabled() is False)

# 勾选 num_threads check：值控件随之启用。
num_check.setChecked(True)
window._apply_adv_threads_state(True)
check("勾选后 num_threads 值控件被启用", num_val.isEnabled() is True)
check("打开时说明文字并入开关悬停浮窗（手动 --num_threads）",
      "手动" in tip_toggle.toolTip() or "--num_threads" in tip_toggle.toolTip())


# 4. _resolve_concurrency + _encode_kwargs 映射。
def kw_for(worker, n_jobs):
    """模拟 run() 开头的并发解析，并取该 worker 实际编码参数。"""
    _cores, per_file, _pool = worker._resolve_concurrency(n_jobs)
    worker._per_file_threads = per_file
    return worker._encode_kwargs()["num_threads"]


# 4a. 单文件（n_jobs<=1），cpu_cores="auto" => 用满全部核心。
w_auto_1 = ConvertWorker([], [], 7, None, None, False,
                         cpu_cores="auto", adv_threads_enabled=False)
check("单文件 自动：num_threads == 逻辑核心数",
      kw_for(w_auto_1, 1) == LOGICAL)

# 4b. 单文件，cpu_cores=4 => num_threads=4。
w_fixed_1 = ConvertWorker([], [], 7, None, None, False,
                          cpu_cores=4, adv_threads_enabled=False)
check("单文件 固定4核：num_threads == 4",
      kw_for(w_fixed_1, 1) == 4)

# 5a. 多文件，cpu_cores=2，3 个任务 => pool_size=min(3, 2//1)=2，num_threads=1。
w_multi = ConvertWorker([], [], 7, None, None, False,
                        cpu_cores=2, adv_threads_enabled=False)
cores2, per2, pool2 = w_multi._resolve_concurrency(3)
check("多文件 2核/3任务：每文件线程=1", per2 == 1)
check("多文件 2核/3任务：并行进程=2", pool2 == 2)
check("多文件 2核/3任务：num_threads=1", kw_for(w_multi, 3) == 1)

# 5b. 多文件，cpu_cores=4，5 个任务 => pool_size=min(5, 4)=4。
w_multi4 = ConvertWorker([], [], 7, None, None, False,
                         cpu_cores=4, adv_threads_enabled=False)
_cores4, _per4, pool4 = w_multi4._resolve_concurrency(5)
check("多文件 4核/5任务：并行进程=4", pool4 == 4)

# 6. 启用高级参数，手动设定每文件线程数=4，cpu_cores=8，10 个任务。
w_adv = ConvertWorker([], [], 7, None, None, False,
                      cpu_cores=8, adv_threads_enabled=True)
w_adv.advanced = {"num_threads": 4}
cores_a, per_a, pool_a = w_adv._resolve_concurrency(10)
check("高级-多文件：每文件线程=4", per_a == 4)
check("高级-多文件：并行进程=min(10, 8//4)=2", pool_a == 2)
check("高级-多文件：num_threads=4", kw_for(w_adv, 10) == 4)

# 6b. 高级参数下仍为单文件：每文件线程=4，pool_size=1。
cores_a1, per_a1, pool_a1 = w_adv._resolve_concurrency(1)
check("高级-单文件：每文件线程=4", per_a1 == 4)
check("高级-单文件：并行进程=1", pool_a1 == 1)
check("高级-单文件：num_threads=4", kw_for(w_adv, 1) == 4)

# 6c. 高级参数打开但 num_threads 非法（非 int 或 <1）=> 退化为每文件 1。
w_adv_bad = ConvertWorker([], [], 7, None, None, False,
                          cpu_cores=8, adv_threads_enabled=True)
w_adv_bad.advanced = {"num_threads": "oops"}  # 非 int
# 高级参数打开但 num_threads 非法 => adv_num 退化为 None => 每文件线程=1。
check("高级-非法线程数退化为每文件1", w_adv_bad._resolve_concurrency(4)[1] == 1)


# 7. 「启用高级参数」联动输出页 effort 可选范围门控。
#    启用 -> 1..10（含 10 档）；禁用 -> 仅 1..9，且越界值（如 10）被夹到 9。
# 恢复真实 _adv_widgets，使 setCurrentText 触发的持久化不缺键（避免测试假阳性）。
window._adv_widgets = real_adv_widgets
effort = getattr(window, "effort_combo", None)
check("暴露 effort_combo", effort is not None)
if effort is not None:
    window._apply_adv_threads_state(True)
    items_on = [effort.itemText(i) for i in range(effort.count())]
    check("启用高级参数：effort 含 1..10",
          items_on == [str(i) for i in range(1, 11)])

    effort.setCurrentText("10")
    window._apply_adv_threads_state(False)
    items_off = [effort.itemText(i) for i in range(effort.count())]
    check("禁用高级参数：effort 仅 1..9",
          items_off == [str(i) for i in range(1, 10)])
    check("禁用时原 effort=10 被夹到 9", effort.currentText() == "9")

    window._apply_adv_threads_state(True)
    items_on2 = [effort.itemText(i) for i in range(effort.count())]
    check("重新启用：effort 恢复 1..10",
          items_on2 == [str(i) for i in range(1, 11)])
    check("重新启用后保留上次选择 9", effort.currentText() == "9")


print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
