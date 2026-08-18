# -*- coding: utf-8 -*-
"""回归测试：设置页「高级参数」母开关禁用联动 + 弹窗文本动态化。

覆盖（针对 2026-08-19 修复的两处 bug）：
1. 禁用「启用高级参数」母开关时，jpeg_hard_skip_check 一并置灰（Bug1）；
   重新启用后恢复可用。
2. 首次勾选弹出的注意事项框，子项列表由「高级参数」组动态生成，
   新增子项（如 jpeg_hard_skip）自动出现，不再手写死列表（Bug2）。

不启动真实转换；GUI 行为以 monkeypatch QMessageBox 做纯逻辑验证。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication

QCoreApplication.setOrganizationName("libjxl_gui_test")
QCoreApplication.setApplicationName("libjxl_gui_test")
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from libjxl_gui.main_window import MainWindow

failures = []


def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


def main():
    w = MainWindow()

    # Bug1：母开关禁用联动（jpeg_hard_skip_check 原设计为「独立于母开关常驻可用」，
    # 现已修正为跟随母开关置灰）。
    w._apply_adv_threads_state(False)
    check("母关: jpeg_hard_skip_check 禁用", w.jpeg_hard_skip_check.isEnabled() is False)
    w._apply_adv_threads_state(True)
    check("母开: jpeg_hard_skip_check 可用", w.jpeg_hard_skip_check.isEnabled() is True)

    # Bug2：弹窗子项列表动态从「高级参数」组收集（排除母开关自身），
    # 新增子项自动出现，无需每次手动同步文本。
    captured = {}
    orig_set = QMessageBox.setText
    orig_exec = QMessageBox.exec

    def _fake_set_text(self, text):
        captured["text"] = text

    def _fake_exec(self):
        return QMessageBox.Ok

    QMessageBox.setText = _fake_set_text
    QMessageBox.exec = _fake_exec
    try:
        w._adv_warning_suppressed = False
        w._maybe_warn_adv_params()
    finally:
        QMessageBox.setText = orig_set
        QMessageBox.exec = orig_exec

    txt = captured.get("text", "")
    check("弹窗含 每文件线程数 子项", "每文件线程数" in txt)
    check("弹窗含 effort 第 10 档 子项", "effort 第 10 档" in txt)
    check("弹窗含 jpeg_hard_skip 子项(动态)", "不可无损重建" in txt)
    check("弹窗无硬编码旧罗列", "影响并行进程数与 CPU 占用" not in txt)

    if failures:
        print("\nFAILURES:", failures)
        raise SystemExit(1)
    print("\nALL_OK")


if __name__ == "__main__":
    main()
