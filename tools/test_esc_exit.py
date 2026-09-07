# -*- coding: utf-8 -*-
"""回归测试：主界面 ESC 退出行为（2026-08-27 新增）。

覆盖：
1. 转换进行中按 ESC：不做任何操作（不退出、不关闭窗口）。
2. 非转换中按 ESC：触发 close()（正常退出）。

不依赖 QSettings 持久化偏好（使用独立 organization/app），不真正退出测试进程
（通过 monkeypatch close 记录调用，避免 headless 下结束事件循环）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QKeyEvent

QCoreApplication.setOrganizationName("jxlforge_test_esc")
QCoreApplication.setApplicationName("jxlforge_test_esc")
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge.main_window import MainWindow

failures = []


def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


def main():
    from PySide6.QtCore import Qt
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)

    # ---- 场景 1：转换进行中按 ESC，不做任何操作 ----
    w = MainWindow()
    # 伪造一个"正在运行"的 worker
    w._convert_worker = types.SimpleNamespace(isRunning=lambda: True)
    seen_close = []
    orig_close = w.close
    w.close = lambda: seen_close.append(True)

    w.keyPressEvent(esc)
    check("转换中按ESC不调用close（窗口不关闭）", len(seen_close) == 0)

    w.close = orig_close

    # ---- 场景 2：非转换中按 ESC，触发 close() ----
    w2 = MainWindow()
    w2._convert_worker = None
    seen_close2 = []
    w2.close = lambda: seen_close2.append(True)

    w2.keyPressEvent(esc)
    check("非转换中按ESC调用close（正常退出）", len(seen_close2) == 1)
    w2.close = lambda: None  # 防止后续析构真正退出

    # ---- 场景 3：worker 已存在但不在运行（已完成）按 ESC，触发 close() ----
    w3 = MainWindow()
    w3._convert_worker = types.SimpleNamespace(isRunning=lambda: False)
    seen_close3 = []
    w3.close = lambda: seen_close3.append(True)

    w3.keyPressEvent(esc)
    check("worker已存在但非运行态按ESC调用close", len(seen_close3) == 1)
    w3.close = lambda: None

    if failures:
        print("\nFAILED:", failures)
        raise SystemExit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    main()
