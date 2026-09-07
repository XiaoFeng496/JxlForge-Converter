# -*- coding: utf-8 -*-
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless regression tests for the "CPU 核心使用数" + 文件级并行池 feature.

Covers:
  1. 设置标签暴露 cpu_cores_combo（默认 自动 / "auto"，选项 1..逻辑核心数）。
  2. 暴露 adv_threads_toggle（默认未勾选），且联动隐藏/启用高级参数
     num_threads 控件 + 提示文字随之切换。
  3. 单文件：--num_threads 用满全部核心（pool_size=1）。
  4. 多文件：K 个并行进程，每文件 --num_threads=1（不超订）。
  5. 启用高级参数后，每文件线程数取用户设定值，并行进程数随之收缩。
  6. num_threads 特殊档位 -1（机器决定）/ 0（禁用多线程）/ 超出核心数，
     以及默认值为 -1（与 cjxl 不传该参数的行为一致）。
  7. 解码侧线程控制：默认关闭时不给 djxl 传 --num_threads；开启后按每文件
     线程预算注入，并与编码侧同口径。

不启动任何真实的 cjxl/djxl 二进制——只验证设置→ConvertWorker→
_encode_kwargs 的映射与 _resolve_concurrency 的分发逻辑。
"""

from PySide6.QtWidgets import QApplication, QCheckBox, QSpinBox
from PySide6.QtCore import QCoreApplication, QSettings

# 隔离 QSettings：写入临时目录，避免污染真实 ini（%APPDATA%\JxlForge\JxlForge-Converter.ini）
# 也被共享 QSettings 状态反向污染导致偶发失败。须在首个 QSettings() 使用前置好。
QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter")
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

# 必须在线程创建前确保有 QApplication 实例。
_app = QApplication.instance() or QApplication(sys.argv)

from jxlforge.main_window import (
    MainWindow,
    ConvertWorker,
    _ADVANCED_SCHEMA,
    _LOGICAL_CORES,
)
from jxlforge import converter

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

# 2b. 暴露 decode_threads_check（解码侧线程控制），默认未勾选。
decode_check = getattr(window, "decode_threads_check", None)
check("暴露 decode_threads_check", decode_check is not None)
if decode_check is not None:
    check("decode_threads_check 默认未勾选",
          decode_check.isChecked() is False)


# 3. adv_threads_toggle 联动启用/隐藏高级参数 num_threads 控件，且说明文字并入
#    开关的悬停浮窗（不再有独立小字 QLabel）。
num_check = QCheckBox()
num_val = QSpinBox()
# 模拟 _collect_advanced 写入的 _adv_widgets 结构：(check, val_w, schema)
# 先保留真实 _adv_widgets 引用，供第 7 段（effort 门控）恢复。
real_adv_widgets = window._adv_widgets
# 关键：只覆盖 num_threads 一项为隔离假控件，其余保留真实控件——否则后续
# setChecked 触发 _save_jxl_output -> _save_advanced 会遍历完整 schema，残字典
# 缺 distance 等键而 KeyError（此前批量运行偶发崩溃的根因）。
window._adv_widgets = dict(real_adv_widgets)
window._adv_widgets["num_threads"] = (num_check, num_val, {})
tip_toggle = window.adv_threads_toggle

# 关闭高级参数：num_threads 整体禁用；tooltip 说明由并行池自动控核。
window._apply_adv_threads_state(False)
check("关闭时 num_threads check 被禁用", num_check.isEnabled() is False)
check("关闭时 num_threads 值控件被禁用", num_val.isEnabled() is False)
check("关闭时说明文字并入开关悬停浮窗（锁定默认行为）",
      "默认行为" in tip_toggle.toolTip() or "解锁下方子项" in tip_toggle.toolTip())

# 先抑制首次开启的注意事项弹窗（本段不测弹窗，避免阻塞），统一用真实联动。
window._maybe_warn_adv_params = lambda: None

# 打开母开关：仅解锁子项按钮，子项默认未勾选 -> num_threads check 仍禁用。
window.adv_threads_toggle.setChecked(True)
check("打开母开关时子项解锁但 num_threads check 仍禁用",
      num_check.isEnabled() is False)
check("打开母开关时子项按钮已启用",
      window.adv_num_threads_toggle.isEnabled() is True)

# 勾选子项「手动设置每文件线程数」：num_threads check 启用，值控件仍禁用。
window.adv_num_threads_toggle.setChecked(True)
check("勾选子项后 num_threads check 被启用", num_check.isEnabled() is True)
check("子项勾选但 check 未勾选时值控件仍禁用",
      num_val.isEnabled() is False)

# 勾选 num_threads check：值控件随之启用。
num_check.setChecked(True)
window._sync_num_threads_row()
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

# 6c. 高级参数打开但 num_threads 非法（非 int / 越界负数）=> 退化为自动调度。
w_adv_bad = ConvertWorker([], [], 7, None, None, False,
                          cpu_cores=8, adv_threads_enabled=True)
w_adv_bad.advanced = {"num_threads": "oops"}  # 非 int
# 高级参数打开但 num_threads 非法 => adv_num 退化为 None => 每文件线程=1。
check("高级-非法线程数退化为每文件1", w_adv_bad._resolve_concurrency(4)[1] == 1)

# 6d. 新增档位 -1 / 0：libjxl 的特殊语义（见 _NUM_THREADS_TIP）。
#     -1 = 交工具按机器决定（会吃满核心）-> 只能串行，pool=1；
#      0 = 禁用多线程 -> 进程不占线程预算，按核心数开满。
def adv_worker(value, cores=8):
    w = ConvertWorker([], [], 7, None, None, False,
                      cpu_cores=cores, adv_threads_enabled=True)
    w.advanced = {"num_threads": value}
    return w


w_neg1 = adv_worker(-1)
per_neg1, pool_neg1 = w_neg1._resolve_concurrency(10)[1:]
check("高级--1档（多文件）：每文件线程=-1", per_neg1 == -1)
check("高级--1档（多文件）：并行进程=1（防超订）", pool_neg1 == 1)
check("高级--1档（多文件）：num_threads=-1", kw_for(w_neg1, 10) == -1)
per_neg1_1 = w_neg1._resolve_concurrency(1)[1]
check("高级--1档（单文件）：每文件线程=-1", per_neg1_1 == -1)

w_zero = adv_worker(0)
per_zero, pool_zero = w_zero._resolve_concurrency(10)[1:]
check("高级-0档（多文件）：每文件线程=0（禁用多线程）", per_zero == 0)
check("高级-0档（多文件）：并行进程=min(10,8)=8", pool_zero == 8)
check("高级-0档（多文件）：num_threads=0", kw_for(w_zero, 10) == 0)
check("高级-0档（单文件）：每文件线程=0", w_zero._resolve_concurrency(1)[1] == 0)

# 6e. 超出核心数：仍接受该值（cjxl 自行降级），但进程数收缩到 1。
w_over = adv_worker(LOGICAL * 2, cores=LOGICAL)
per_over, pool_over = w_over._resolve_concurrency(10)[1:]
check("高级-超出核心数：每文件线程原样保留", per_over == LOGICAL * 2)
check("高级-超出核心数：并行进程=1", pool_over == 1)

# 6f. 布尔值不能当线程数（bool 是 int 子类，True 会被误当成 1 个线程）。
w_bool = adv_worker(True)
check("高级-布尔值被拒绝，退化为自动调度",
      w_bool._resolve_concurrency(4)[1] == 1)

# 6g. _classify_jobs 的 _small_pool 在 -1 / 0 档位下与 _resolve_concurrency 一致。
#     空任务列表时全部当作小图，正好可以单独取出 (nt, pool) 判定。
nt_s_neg1, pool_s_neg1 = adv_worker(-1)._classify_jobs([])[2:]
check("分类--1档：小图线程=-1", nt_s_neg1 == -1)
check("分类--1档：小图并行进程=1", pool_s_neg1 == 1)
nt_s_zero, pool_s_zero = adv_worker(0)._classify_jobs([])[2:]
check("分类-0档：小图线程=0", nt_s_zero == 0)
check("分类-0档：小图并行进程=1（无任务时不放大）", pool_s_zero == 1)

# 6h. schema 取值区间：-1..逻辑核心数，默认 -1（与 cjxl 不传该参数时行为一致）。
nt_spec = next(s for s in _ADVANCED_SCHEMA if s["key"] == "num_threads")
check("schema num_threads min == -1", nt_spec["min"] == -1)
check("schema num_threads max == 逻辑核心数", nt_spec["max"] == LOGICAL)
check("schema num_threads default == -1（与 cjxl 默认一致）",
      nt_spec["default"] == -1)
check("schema num_threads default 在区间内",
      nt_spec["min"] <= nt_spec["default"] <= nt_spec["max"])
check("_LOGICAL_CORES 与 os.cpu_count 一致", _LOGICAL_CORES == LOGICAL)


# 7. 「启用高级参数」联动输出页 effort 可选范围门控。
#    启用 -> 1..10（含 10 档）；禁用 -> 仅 1..9，且越界值（如 10）被夹到 9。
# 恢复真实 _adv_widgets，使 setCurrentText 触发的持久化不缺键（避免测试假阳性）。
window._adv_widgets = real_adv_widgets
effort = getattr(window, "effort_combo", None)
check("暴露 effort_combo", effort is not None)
if effort is not None:
    # effort 第 10 档需「母开关 + 子项解锁 effort 第 10 档」同时勾选。
    window.adv_threads_toggle.setChecked(True)
    window.adv_effort10_toggle.setChecked(True)
    items_on = [effort.itemText(i) for i in range(effort.count())]
    check("启用高级参数：effort 含 1..10",
          items_on == [str(i) for i in range(1, 11)])

    effort.setCurrentText("10")
    # 关闭母开关 -> 仅 1..9，且越界值（10）被夹到 9。
    window.adv_threads_toggle.setChecked(False)
    items_off = [effort.itemText(i) for i in range(effort.count())]
    check("禁用高级参数：effort 仅 1..9",
          items_off == [str(i) for i in range(1, 10)])
    check("禁用时原 effort=10 被夹到 9", effort.currentText() == "9")

    window.adv_threads_toggle.setChecked(True)
    items_on2 = [effort.itemText(i) for i in range(effort.count())]
    check("重新启用：effort 恢复 1..10",
          items_on2 == [str(i) for i in range(1, 11)])
    check("重新启用后保留上次选择 9", effort.currentText() == "9")


# 8. 解码侧线程控制（djxl --num_threads）：默认关闭，开启后按每文件线程预算注入。
_captured = []


def _fake_run(args, priority=None):
    """捕获实际拼出的命令行，不启动真实 djxl 进程。"""
    _captured.append(list(args))
    return True, "", ""


_real_run = getattr(converter, "_run", None)
converter._run = _fake_run
try:
    # 8a. converter.decode 默认不传 --num_threads（与改动前行为完全一致）。
    _captured.clear()
    converter.decode("in.jxl", "out.png")
    check("decode 默认不传 --num_threads", "--num_threads" not in _captured[0])
    _captured.clear()
    converter.decode("in.jxl", "out.png", num_threads=4)
    check("decode 传 num_threads=4 拼出 --num_threads 4",
          _captured[0][-2:] == ["--num_threads", "4"])
    _captured.clear()
    converter.decode("in.jxl", "out.png", num_threads=0)
    check("decode 传 num_threads=0 拼出 --num_threads 0（0 是有效档位）",
          _captured[0][-2:] == ["--num_threads", "0"])
    _captured.clear()
    converter.decode("in.jxl", "out.png", num_threads=-1)
    check("decode 传 num_threads=-1 拼出 --num_threads -1",
          _captured[0][-2:] == ["--num_threads", "-1"])
finally:
    if _real_run is not None:
        converter._run = _real_run

# 8b. ConvertWorker._decode_kwargs：默认关闭 -> 不带 num_threads。
w_doff = ConvertWorker([], [], 7, None, None, False, cpu_cores=8)
w_doff._per_file_threads = 4
check("解码线程控制默认关闭：不带 num_threads",
      "num_threads" not in w_doff._decode_kwargs())
check("解码 kwargs 始终带 priority",
      w_doff._decode_kwargs()["priority"] == w_doff.priority)

w_don = ConvertWorker([], [], 7, None, None, False, cpu_cores=8,
                      decode_threads_enabled=True)
w_don._per_file_threads = 4
check("解码线程控制开启：沿用当前每文件线程预算",
      w_don._decode_kwargs()["num_threads"] == 4)
w_don._per_file_threads = None
check("解码线程控制开启但未解析并发：不带 num_threads",
      "num_threads" not in w_don._decode_kwargs())
w_don._per_file_threads = True
check("解码线程控制排除 bool（bool 是 int 子类）",
      "num_threads" not in w_don._decode_kwargs())
w_don._per_file_threads = -1
check("解码线程控制保留 -1 档", w_don._decode_kwargs()["num_threads"] == -1)
w_don._per_file_threads = 0
check("解码线程控制保留 0 档", w_don._decode_kwargs()["num_threads"] == 0)

# 8c. 与 _resolve_concurrency 联动：开启后解码线程数 == 编码线程数（同一口径）。
w_don2 = ConvertWorker([], [], 7, None, None, False,
                       cpu_cores=8, decode_threads_enabled=True)
_cores_d, per_d, _pool_d = w_don2._resolve_concurrency(4)
w_don2._per_file_threads = per_d
check("开启后解码线程数 == 编码每文件线程数",
      w_don2._decode_kwargs()["num_threads"]
      == w_don2._encode_kwargs()["num_threads"])

# 8d. 设置页开关：随母开关置灰 + 勾选态持久化往返。
if decode_check is not None:
    window.adv_threads_toggle.setChecked(False)
    check("母开关关闭时 decode_threads_check 被置灰",
          decode_check.isEnabled() is False)
    window.adv_threads_toggle.setChecked(True)
    check("母开关开启时 decode_threads_check 可用",
          decode_check.isEnabled() is True)

    decode_check.setChecked(True)
    window._save_conversion_settings()
    # 复位时屏蔽信号：toggled handler 会立即把 False 写回 ini，覆盖刚存的 True。
    decode_check.blockSignals(True)
    decode_check.setChecked(False)
    decode_check.blockSignals(False)
    window._load_conversion_settings()
    check("decode_threads 勾选态持久化往返",
          window.decode_threads_check.isChecked() is True)
    # 复位，避免影响后续可能新增的测试。
    window.decode_threads_check.setChecked(False)
    window._save_conversion_settings()

print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
