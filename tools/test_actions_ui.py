# -*- coding: utf-8 -*-
"""Checks for the redesigned actions tab: 2-row per-item widgets, per-item
buttons (上移/下移/移除), clear-all, and Delete-key removal for both the
action list and the input (image) list."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from PySide6.QtWidgets import QApplication, QListWidgetItem
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


app = QApplication([])
window = mw.MainWindow()

# --- ActionItemWidget: 2-row widget with summary + 3 buttons ------------
wi = mw.ActionItemWidget()
wi.summary_label.setText("调整大小 (800x600)")
check("item widget has summary", wi.summary_label.text() == "调整大小 (800x600)")
check("item widget has up button", wi.up_button is not None)
check("item widget has down button", wi.down_button is not None)
check("item widget has remove button", wi.remove_button is not None)
check("item widget sizeHint > 0", wi.sizeHint().height() > 0 and wi.sizeHint().width() > 0)

# --- ActionListWidget is used --------------------------------------------
check("action_list is ActionListWidget", isinstance(window.action_list, mw.ActionListWidget))


def add_item(action_type, summary):
    item = QListWidgetItem()
    item.setData(Qt.UserRole, {"type": action_type, "params": {}})
    widget = mw.ActionItemWidget()
    widget.summary_label.setText(summary)
    item.setSizeHint(widget.sizeHint())
    window.action_list.addItem(item)
    window.action_list.setItemWidget(item, widget)
    widget.item = item
    widget.up_button.clicked.connect(lambda: window._on_move_action_for(item, -1))
    widget.down_button.clicked.connect(lambda: window._on_move_action_for(item, 1))
    widget.remove_button.clicked.connect(lambda: window._on_remove_action_for(item))
    return item


# --- Add three actions ---------------------------------------------------
add_item("调整大小", "调整大小 (800x600)")
add_item("旋转", "旋转 (90)")
add_item("水印", "水印 (ABC)")
check("3 actions added", window.action_list.count() == 3)
check("collect_actions sees 3", len(window._collect_actions()) == 3)

# --- Per-item move up -----------------------------------------------------
last = window.action_list.item(2)
window._on_move_action_for(last, -1)
check("after move-up order",
      [a["type"] for a in window._collect_actions()] == ["调整大小", "水印", "旋转"])

# --- Per-item remove ------------------------------------------------------
mid = window.action_list.item(1)
window._on_remove_action_for(mid)
check("after remove -> 2 left", window.action_list.count() == 2)
check("after remove order",
      [a["type"] for a in window._collect_actions()] == ["调整大小", "旋转"])

# --- Clear all ------------------------------------------------------------
window._on_clear_actions()
check("clear -> 0", window.action_list.count() == 0)

# --- Delete-key removal (multi-select) -----------------------------------
add_item("裁剪", "裁剪")
add_item("锐化", "锐化")
for i in range(window.action_list.count()):
    window.action_list.item(i).setSelected(True)
window._on_remove_selected_actions()
check("delete-key removed selected", window.action_list.count() == 0)

# --- ActionListWidget.keyPressEvent routes Delete to removal -------------
add_item("亮度/对比度", "亮度")
window.action_list.item(0).setSelected(True)
ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
window.action_list.keyPressEvent(ev)
check("keyPressEvent Delete removed item", window.action_list.count() == 0)

# --- InputListWidget.keyPressEvent routes Delete to _on_remove_selected --
window.input_files = [r"C:\a\one.jpg", r"C:\a\two.png"]
window._refresh_input_views()
window.input_stack.setCurrentWidget(window.input_list)
window.input_list.item(0).setSelected(True)
iev = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
window.input_list.keyPressEvent(iev)
check("input Delete removed 1 file", len(window.input_files) == 1)

print("ACTIONS_UI_OK" if not failures else "FAILURES: %s" % failures)
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
