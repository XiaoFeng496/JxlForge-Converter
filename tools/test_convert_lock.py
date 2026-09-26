# -*- coding: utf-8 -*-
"""回归测试：转换中进行时锁定「影响转换」的控件 / 结束后解锁。

覆盖行为（_lock_ui_for_convert / _unlock_ui_after_convert）：
1. 转换进行中：输入/动作/输出 三页整页禁用；设置页整页仍可用，但其中「转换进程」
   「大图并发校准」「高级参数」三个分组框禁用，其余（常规 / 窗口布局 / 选项）
   与转换无关，保持可用；关于页整页可用；状态页与底部按钮不受影响。
2. 转换结束后：被锁定的三页与三个分组框恢复可用，且高级参数按母开关重建子项
   细分态（母开关关闭时子项仍置灰），输出页按「自定义命令」勾选态重建细分 enabled。
3. 边界：自定义命令模式下锁定再解锁，编码参数仍保持禁用、开关本身可用。

不启动真实转换；仅验证 UI 锁定/解锁逻辑。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication

QCoreApplication.setOrganizationName("jxlforge_test")
QCoreApplication.setApplicationName("jxlforge_test")
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge.main_window import MainWindow

failures = []


def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


def _tab_text(w, page):
    return w.tabs.tabText(w.tabs.indexOf(page))


def main():
    w = MainWindow()

    # 强制回到已知初始态：避免被其他测试脚本共享同一 QSettings
    # (organization/app = "jxlforge_test") 持久化到磁盘的勾选态污染。
    # 本测试只验证锁定/解锁逻辑，不依赖任何持久化偏好。
    w.custom_cmd_check.blockSignals(True)
    w.custom_cmd_check.setChecked(False)
    w.custom_cmd_check.blockSignals(False)
    w._on_custom_cmd_toggled(False)

    # 输入 / 动作 / 输出 三页初始可用。
    for page in (w.input_tab, w.actions_tab, w.output_tab):
        check("初始[%s]可用" % _tab_text(w, page), page.isEnabled() is True)
    # 设置页 / 关于页整页与状态页初始可用。
    check("初始[设置]可用", w.settings_tab.isEnabled() is True)
    check("初始[关于]可用", w.about_tab.isEnabled() is True)
    check("初始[状态]可用", w.status_tab.isEnabled() is True)
    # 设置页里三个转换相关分组框初始可用。
    for g in w._settings_convert_lock_widgets:
        check("初始[设置分组]可用", g.isEnabled() is True)

    conv_before = w.convert_button.isEnabled()
    stop_before = w.stop_button.isEnabled()
    w._lock_ui_for_convert()

    # 输入 / 动作 / 输出 三页锁定。
    for page in (w.input_tab, w.actions_tab, w.output_tab):
        check("锁定[%s]禁用" % _tab_text(w, page), page.isEnabled() is False)

    # 设置页整页仍可用，但转换相关分组框锁定、其余分组（常规 / 窗口布局 / 选项）可用。
    check("锁定[设置]整页仍可用", w.settings_tab.isEnabled() is True)
    for g in w._settings_convert_lock_widgets:
        check("锁定[设置分组]禁用", g.isEnabled() is False)
    # 常规 / 窗口布局 / 选项 与转换无关，保持可用。
    check("锁定[主题下拉]可用", w.color_scheme_combo.isEnabled() is True)
    check("锁定[控件样式下拉]可用", w.theme_combo.isEnabled() is True)
    check("锁定[语言下拉]可用", w.language_combo.isEnabled() is True)
    check("锁定[退出保存动作]可用", w.save_actions_on_exit_check.isEnabled() is True)
    # 关于页整页可用（复制诊断按钮等保持可交互）。
    check("锁定[关于]整页仍可用", w.about_tab.isEnabled() is True)

    check("锁定[状态]仍可用", w.status_tab.isEnabled() is True)
    # 底部栏不被锁定波及。
    check("锁定不动转换按钮", w.convert_button.isEnabled() == conv_before)
    check("锁定不动停止按钮", w.stop_button.isEnabled() == stop_before)

    # 模拟转换结束。
    w._unlock_ui_after_convert()

    for page in (w.input_tab, w.actions_tab, w.output_tab):
        check("解锁[%s]恢复" % _tab_text(w, page), page.isEnabled() is True)
    for g in w._settings_convert_lock_widgets:
        check("解锁[设置分组]恢复", g.isEnabled() is True)
    check("解锁[设置]整页可用", w.settings_tab.isEnabled() is True)
    check("解锁[关于]整页可用", w.about_tab.isEnabled() is True)
    # 默认非自定义命令：输出页编码控件可用（不依赖具体编码模式）。
    check("解锁后 format_combo 可用", w.format_combo.isEnabled() is True)
    check("解锁后 effort_combo 可用", w.effort_combo.isEnabled() is True)
    check("解锁后 lossy_radio 可用", w.lossy_radio.isEnabled() is True)
    # 高级参数母开关默认关 → 子项仍置灰（与转换前一致）。
    check("解锁后 adv 母开关可用", w.adv_threads_toggle.isEnabled() is True)
    check("解锁后 adv 子项仍置灰(母开关关)",
          w.adv_num_threads_toggle.isEnabled() is False)

    # 边界：自定义命令模式下锁定再解锁，应保持编码参数禁用、开关自身可用。
    w.custom_cmd_check.blockSignals(True)
    w.custom_cmd_check.setChecked(True)
    w.custom_cmd_check.blockSignals(False)
    w._on_custom_cmd_toggled(True)  # 模拟 slot，进入自定义命令禁用态
    check("自定义命令: format_combo 禁用", w.format_combo.isEnabled() is False)

    w._lock_ui_for_convert()
    w._unlock_ui_after_convert()
    check("自定义命令锁定后解锁: format_combo 仍禁用",
          w.format_combo.isEnabled() is False)
    check("自定义命令锁定后解锁: custom_cmd_check 可用",
          w.custom_cmd_check.isEnabled() is True)

    if failures:
        print("\nFAILURES:", failures)
        raise SystemExit(1)
    print("\nALL_OK")


if __name__ == "__main__":
    main()
