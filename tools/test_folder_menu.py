# -*- coding: utf-8 -*-
"""回归测试：自定义文件夹历史下拉的「就地删除」。

覆盖一个曾导致闪烁的 bug —— 每删除一行都会 clear()+全量重建整个菜单，
而重建发生在可见 popup 上时会塌缩并重绘，读起来就是「闪一下」。

修复后删除只移除那一行 widget 并重排剩余行索引，因此：
  * 删除中间行后菜单只剩 (N-1) 行（不是整菜单重建）；
  * 剩余每行 HistoryRowWidget._row 仍等于其新位置，连续删除不会错位；
  * 删到空时显示「(无历史记录)」占位，而不是空白菜单。
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings
QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")

from libjxl_gui.main_window import MainWindow, HistoryRowWidget

_app = QApplication.instance() or QApplication(sys.argv)


def fresh_window():
    w = MainWindow()
    w.show()
    _app.processEvents()
    return w


def _row_widgets(menu):
    out = []
    for act in menu.actions():
        w = act.defaultWidget()
        if isinstance(w, HistoryRowWidget):
            out.append(w)
    return out


def test_inplace_delete_no_flicker():
    w = fresh_window()
    # 隔离：强制一段已知历史，覆盖加载态
    w._folder_history = ["C:/a", "C:/b", "C:/c", "D:/d"]
    w._rebuild_folder_menu()
    assert len(w.folder_menu.actions()) == 4, "rebuild 应列出 4 行"

    # 删除中间一行（C:/b，index 1）
    w._on_folder_history_delete(1)
    acts = w.folder_menu.actions()
    assert len(acts) == 3, "就地删除应只移除 1 行，而非整菜单重建（会闪烁）"
    assert w._folder_history == ["C:/a", "C:/c", "D:/d"], w._folder_history
    rows = _row_widgets(w.folder_menu)
    assert [r._row for r in rows] == [0, 1, 2], [r._row for r in rows]

    # 连续再删首行，验证索引不串
    w._on_folder_history_delete(0)
    acts = w.folder_menu.actions()
    assert len(acts) == 2, "连续删除应保留 2 行"
    rows = _row_widgets(w.folder_menu)
    assert [r._row for r in rows] == [0, 1], [r._row for r in rows]
    assert w._folder_history == ["C:/c", "D:/d"], w._folder_history

    # 删空 -> 出现占位
    w._on_folder_history_delete(0)
    w._on_folder_history_delete(0)
    assert w._folder_history == [], "应全部删空"
    labels = [a.text() for a in w.folder_menu.actions()]
    assert "(无历史记录)" in labels, labels
    print("PASS: 自定义文件夹就地删除（无闪烁）回归通过")


if __name__ == "__main__":
    test_inplace_delete_no_flicker()
    print("ALL_OK")
