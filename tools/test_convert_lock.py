# -*- coding: utf-8 -*-
"""回归测试：转换中进行时锁定编辑控件 / 结束后解锁。

覆盖行为（2026-08-22 新增的 _lock_ui_for_convert / _unlock_ui_after_convert）：
1. 转换进行中：输入/输出/动作/设置四页全部禁用，状态页与底部按钮不受影响。
2. 转换结束后：四页恢复可用，且输出页按「自定义命令」勾选态重建细分 enabled。
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


def main():
    w = MainWindow()

    # 强制回到已知初始态：避免被其他测试脚本共享同一 QSettings
    # (organization/app = "jxlforge_test") 持久化到磁盘的勾选态污染。
    # 本测试只验证锁定/解锁逻辑，不依赖任何持久化偏好。
    w.custom_cmd_check.blockSignals(True)
    w.custom_cmd_check.setChecked(False)
    w.custom_cmd_check.blockSignals(False)
    w._on_custom_cmd_toggled(False)

    # 非状态页 = 输入/动作/输出/设置；状态页单独保留可用。
    def _non_status_pages():
        for i in range(w.tabs.count()):
            wid = w.tabs.widget(i)
            if wid is not w.status_tab:
                yield w.tabs.tabText(i), wid

    # 初始：四页均可用，状态页可用。
    for name, wid in _non_status_pages():
        check("初始[%s]可用" % name, wid.isEnabled() is True)
    check("初始[状态]可用", w.status_tab.isEnabled() is True)

    # 模拟转换进行中。
    conv_before = w.convert_button.isEnabled()
    stop_before = w.stop_button.isEnabled()
    w._lock_ui_for_convert()

    for name, wid in _non_status_pages():
        check("锁定[%s]禁用" % name, wid.isEnabled() is False)
    check("锁定[状态]仍可用", w.status_tab.isEnabled() is True)
    # 底部栏不被锁定波及。
    check("锁定不动转换按钮", w.convert_button.isEnabled() == conv_before)
    check("锁定不动停止按钮", w.stop_button.isEnabled() == stop_before)

    # 模拟转换结束。
    w._unlock_ui_after_convert()

    for name, wid in _non_status_pages():
        check("解锁[%s]恢复" % name, wid.isEnabled() is True)
    # 默认非自定义命令：输出页编码控件可用（不依赖具体编码模式）。
    check("解锁后 format_combo 可用", w.format_combo.isEnabled() is True)
    check("解锁后 effort_combo 可用", w.effort_combo.isEnabled() is True)
    check("解锁后 lossy_radio 可用", w.lossy_radio.isEnabled() is True)

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
