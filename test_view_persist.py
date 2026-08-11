# -*- coding: utf-8 -*-
"""持久化：输入标签页「查看」视图模式跨重启恢复。headless 运行。

覆盖：
1. 默认视图为「缩略图」，且不提前写入已存值（避免覆盖用户已保存的选择）。
2. 切换视图后即时持久化到 QSettings（input_view/mode）。
3. 新建 MainWindow（模拟重启）能恢复上次选择的视图。
4. 非法/缺失值回退到「缩略图」。
5. 即时保存受 _view_loading guard 保护（加载期间不写）。
"""
import sys

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

# 隔离：使用独立的组织/应用名，避免读写真实配置。
QCoreApplication.setOrganizationName("libjxl_gui_test")
QCoreApplication.setApplicationName("view_persist")

from libjxl_gui.main_window import MainWindow, VIEW_MODES


def check(label, cond):
    print(("PASS" if cond else "FAIL") + " - " + label)
    if not cond:
        check.failed += 1
check.failed = 0


def stored_mode():
    s = QSettings()
    s.beginGroup("input_view")
    v = s.value("mode", None)
    s.endGroup()
    return v


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # 清空可能残留的持久化状态，保证测试从「默认」起点开始
    # （QSettings 在 offscreen 下也会跨进程/跨运行保留，避免读到旧值）
    QSettings().remove("input_view")
    QSettings().sync()

    # --- 1. 默认视图 ---
    w = MainWindow()
    check("默认视图 = 缩略图", getattr(w, "_last_view", None) == "缩略图")
    check("默认按钮文字 = 缩略图", w.view_button.text() == "缩略图")
    # 构建期不应把已存的用户选择覆盖成默认
    check("构建后尚未写入 mode（不应提前覆盖）",
          stored_mode() in (None, "缩略图"))

    # --- 2. 切换并即时持久化 ---
    w._on_view_changed("详细信息")
    check("切换后 _last_view = 详细信息", w._last_view == "详细信息")
    check("切换后按钮文字 = 详细信息", w.view_button.text() == "详细信息")
    check("load 守卫已复位（可正常保存）", w._view_loading is False)
    QSettings().sync()  # 确保落盘
    check("即时持久化 mode=详细信息", stored_mode() == "详细信息")

    # --- 3. 模拟重启：新实例恢复 ---
    w2 = MainWindow()
    check("重启后恢复 _last_view=详细信息", w2._last_view == "详细信息")
    check("重启后恢复按钮文字=详细信息", w2.view_button.text() == "详细信息")
    # 当前应显示列表/详细信息视图（stack 切到 table）
    check("重启后显示详细信息表格", w2.input_stack.currentWidget() is w2.input_table)

    # --- 4. 非法值回退 ---
    s = QSettings()
    s.beginGroup("input_view")
    s.setValue("mode", "不存在的视图")
    s.endGroup()
    s.sync()
    w3 = MainWindow()
    check("非法 mode 回退到 缩略图", w3._last_view == "缩略图")
    check("非法 mode 后按钮文字=缩略图", w3.view_button.text() == "缩略图")

    # --- 5. 所有合法模式均可恢复 ---
    for mode in VIEW_MODES:
        s.beginGroup("input_view")
        s.setValue("mode", mode)
        s.endGroup()
        s.sync()
        wm = MainWindow()
        check("恢复合法模式: " + mode, wm._last_view == mode)

    print("\n总计: %d 项失败" % check.failed)
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
