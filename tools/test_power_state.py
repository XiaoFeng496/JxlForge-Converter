# -*- coding: utf-8 -*-
"""回归测试：电源状态（计划/插拔电/模式）三元组检测与阈值自动套用（选项1）。

覆盖行为（2026-08-22 新增）：
1. 某状态已校准 -> 切换至该状态时「自动套用」并提示，不自动跑校准。
2. 某状态未校准 -> 切换至该状态时「建议重新校准」提示。
3. 阈值记忆按完整电源状态分别存储，同计划不同模式各自精确套用（底层已在
   test_calibrate 覆盖，这里验证 MainWindow 编排层触发正确日志）。

需实例化 MainWindow（offscreen），mock power.get_power_state 以驱动 _refresh
而不依赖真实注册表。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

# 隔离 QSettings：calibrate 模块用显式 QSettings(IniFormat, UserScope, "JxlForge", "JxlForge-Converter")
# 构造（见 calibrate._settings），不走全局 setOrganizationName，因此常规把 org/app 改成
# "xxx_test" 的隔离手段对它无效；必须用 setPath 重定向 IniFormat+UserScope 才能把文件落到
# 临时目录，否则会读写真实 %APPDATA%\JxlForge\JxlForge-Converter.ini，清空/覆盖已校准的大图阈值
# （本测试调用的 clear_all_calibration / write_floor_px 正是走这条路径）。
QSettings.setDefaultFormat(QSettings.IniFormat)
_tmp_settings_dir = tempfile.mkdtemp(prefix="jxlforge_test_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

QCoreApplication.setOrganizationName("jxlforge_test")
QCoreApplication.setApplicationName("jxlforge_test")
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge import power as P          # noqa: E402
from jxlforge import calibrate as C       # noqa: E402
from jxlforge.main_window import MainWindow  # noqa: E402

failures = []
total = [0]


def check(name, ok):
    total[0] += 1
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


# 固定电源状态，避免 _refresh_calib_value_label 读真实注册表。
_state = {"v": ("scheme-A", "ac", "best_performance")}


def _fake_state():
    return _state["v"]


P.get_power_state = _fake_state

C.clear_all_calibration()

# 场景1：状态A（计划A + 插电 + 最佳性能）已校准
stateA = ("scheme-A", "ac", "best_performance")
C.write_floor_px(8_000_000, scheme="scheme-A", ac="ac", mode="best_performance")

w = MainWindow()
w.log_edit.clear()
w._last_seen_state = None  # 模拟首次轮询尚未初始化
w._on_power_state_changed(stateA)
log1 = w.log_edit.toPlainText()
check("状态A已校准 -> 自动套用提示", "自动套用" in log1)
check("状态A日志带阈值(MP)", "8" in log1)
check("状态A已记录值可读", C.read_stored_floor_px(*stateA) == 8_000_000)

# 场景2：状态B（同计划 + 插电 + 平衡）未校准 -> 建议重新校准
stateB = ("scheme-A", "ac", "balanced")
w.log_edit.clear()
w._on_power_state_changed(stateB)
log2 = w.log_edit.toPlainText()
check("状态B未校准 -> 建议重新校准提示", "重新校准" in log2)
check("状态B无专属记录", C.read_per_state_floor_px(stateB) is None)

# 场景3：为状态B校准后再次切换 -> 应自动套用（验证分模式精确套用）
C.write_floor_px(11_000_000, scheme="scheme-A", ac="ac", mode="balanced")
_state["v"] = stateB
w.log_edit.clear()
w._on_power_state_changed(stateB)
log3 = w.log_edit.toPlainText()
check("状态B校准后 -> 自动套用提示", "自动套用" in log3)
check("状态A与B阈值互不干扰", C.read_stored_floor_px(*stateA) == 8_000_000)
check("状态B精确值独立", C.read_stored_floor_px(*stateB) == 11_000_000)

C.clear_all_calibration()

print("\n%d/%d checks passed" % (total[0] - len(failures), total[0]))
print("ALL_OK" if not failures else "FAILED: " + ", ".join(failures))
sys.exit(1 if failures else 0)
