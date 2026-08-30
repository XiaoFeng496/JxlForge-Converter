# -*- coding: utf-8 -*-
r"""回归测试：设置页「常规」区新增的「语言」下拉（占位项）。

覆盖：
- 主窗口暴露 language_combo，且它位于「常规」分组内。
- 内置两项：简体中文 / English，itemData 分别为 zh_CN / en_US。
- 默认选中简体中文（zh_CN）。
- 当前为占位项：不接任何切换逻辑，也不写 QSettings（后续实现时再补）。

注：每个用例跑在独立的临时 ini 目录，避免污染真实
%APPDATA%\libjxl\libjxl-gui.ini。

⚠️ 调整「常规」区内部排布后，只需同步修改下面的 _EXPECTED_GRID_POSITIONS。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel

# 「常规」区内各下拉在内部 QGridLayout 中的落位 (row, col)。
# 这里钉死绝对坐标是有意为之：它同时充当"布局锁"，排布被意外打乱时会报警。
# 唯一维护点：移动/增删控件后改这张表即可，用例体不用动。
_EXPECTED_GRID_POSITIONS = {
    "color_scheme_combo": (0, 0),  # 主题
    "theme_combo": (0, 1),         # 控件样式（主题右侧）
    "language_combo": (1, 0),      # 语言（左列第二行）
}


def _bootstrap():
    r"""Each test runs in a brand-new isolated ini directory so QSettings never
    touches the real ``%APPDATA%\libjxl\libjxl-gui.ini``."""
    QSettings.setDefaultFormat(QSettings.IniFormat)
    tmp = tempfile.mkdtemp(prefix="libjxl_gui_language_")
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    app = QApplication.instance() or QApplication(["-platform", "offscreen"])
    return app, tmp


def _row_label_of(combo):
    r"""Return the QLabel sitting on the same row, left of ``combo``.

    「常规」分组内部是 QVBoxLayout → QGridLayout → 每行 QHBoxLayout 的多层
    嵌套（双列排布），必须递归下钻，只查一层会找不到。
    """
    def _search(layout):
        if layout is None:
            return None
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget() is combo:
                for k in range(i - 1, -1, -1):
                    cand = layout.itemAt(k).widget()
                    if isinstance(cand, QLabel):
                        return cand
                return None
            found = _search(item.layout())
            if found is not None:
                return found
        return None

    return _search(combo.parentWidget().layout())


def _grid_position_of(combo):
    r"""Return (row, col) of the row layout holding ``combo`` inside the
    QGridLayout of the 常规 group, or None when no grid is used."""
    from PySide6.QtWidgets import QGridLayout
    outer = combo.parentWidget().layout()
    for i in range(outer.count()):
        item = outer.itemAt(i)
        grid = item.layout() if item is not None else None
        if not isinstance(grid, QGridLayout):
            continue
        for j in range(grid.count()):
            cell = grid.itemAt(j)
            row_layout = cell.layout() if cell is not None else None
            if row_layout is None:
                continue
            for k in range(row_layout.count()):
                it = row_layout.itemAt(k)
                if it is not None and it.widget() is combo:
                    row, col = grid.getItemPosition(j)[:2]
                    return (row, col)
    return None


def test_language_combo_exists_in_常规():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    combo = getattr(w, "language_combo", None)
    assert combo is not None, "主窗口应暴露 language_combo"
    group = combo.parentWidget()
    while group is not None and not isinstance(group, QGroupBox):
        group = group.parentWidget()
    assert group is not None and group.title() == "常规", \
        "语言下拉必须位于「常规」分组，实际=%r" % (group.title() if group else None)
    w.close()
    print("PASS language_combo_exists_in_常规")


def test_language_row_label():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    label = _row_label_of(w.language_combo)
    assert label is not None and label.text() == "语言", \
        "语言行左侧标签应为「语言」，实际=%r" % (label.text() if label else None)
    w.close()
    print("PASS language_row_label")


def test_grid_positions_in_常规():
    r"""「常规」内部双列的落位，对照文件顶部的 _EXPECTED_GRID_POSITIONS
    逐项核对（改排布时只改那张表）。"""
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    for attr, expected in sorted(_EXPECTED_GRID_POSITIONS.items()):
        combo = getattr(w, attr, None)
        assert combo is not None, "主窗口缺少属性 %s" % attr
        actual = _grid_position_of(combo)
        assert actual == expected, \
            "%s 落位错误：期望 %r，实际 %r" % (attr, expected, actual)
    w.close()
    print("PASS grid_positions_in_常规")


def test_language_items_and_default():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    combo = w.language_combo
    items = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
    assert items == [("简体中文", "zh_CN"), ("English", "en_US")], \
        "语言项应为 简体中文/zh_CN + English/en_US，实际=%r" % (items,)
    assert combo.currentData() == "zh_CN", \
        "默认应选中简体中文，实际=%r" % combo.currentData()
    w.close()
    print("PASS language_items_and_default")


def test_language_is_placeholder_no_persist():
    r"""占位阶段：不接切换槽、不写 QSettings。后续实现语言功能时
    本用例需要同步调整。"""
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    combo = w.language_combo
    combo.setCurrentIndex(combo.findData("en_US"))
    settings = QSettings()
    settings.beginGroup("appearance")
    assert not settings.contains("language"), \
        "占位阶段不应把语言写入 QSettings"
    settings.endGroup()
    w.close()
    print("PASS language_is_placeholder_no_persist")


def main():
    tests = [
        test_language_combo_exists_in_常规,
        test_language_row_label,
        test_grid_positions_in_常规,
        test_language_items_and_default,
        test_language_is_placeholder_no_persist,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:  # noqa: BLE001 - test harness
            failed += 1
            print("ERROR %s: %s: %s" % (t.__name__, type(exc).__name__, exc))
    print("\n%d/%d passed, %d failed" % (passed, len(tests), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
