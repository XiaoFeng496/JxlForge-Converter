# -*- coding: utf-8 -*-
"""回归：动作项启用勾选框 + 上移/下移不闪退 + 标签标题数量统计。

覆盖三个改动：

1) 动作项启用勾选框
   - 每个动作项有 QCheckBox；取消勾选的动作不参与处理（_collect_actions
     不含它），但仍保留在列表与持久化数据中（_all_action_data 含它）。
   - 旧数据（无 "enabled" 键）默认启用，向后兼容。

2) 上移/下移闪退
   - 旧实现把 takeItem 摘下来的 widget 原样 setItemWidget 塞回去；Qt 已把
     该 indexWidget 从内部表摘除并会在刷新中释放，之后再被视图访问 →
     真实桌面点击上移/下移直接闪退（offscreen 不绘制，测不出来）。
   - 修复：移动 = 摘除旧项（widget 一并退场）+ 在目标行插入全新 item/widget。
     本测试断言「移动后的 widget 是新建的、与旧 widget 不是同一对象」。

3) 标签标题数量统计
   - 输入 → 「输入 [N个]」，动作 → 「动作 [启用数/总数]」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from jxlforge import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
        print("PASS:", name)
    else:
        failures.append(name)
        print("FAIL:", name)


win = mw.MainWindow()


def types_all():
    return [a["type"] for a in win._all_action_data()]


def types_enabled():
    return [a["type"] for a in win._collect_actions()]


def widget_at(row):
    return win.action_list.itemWidget(win.action_list.item(row))


# --- 1) 启用勾选框 ------------------------------------------------------
win._add_action_item({"type": "旋转", "params": {"angle": 90}, "enabled": True},
                     render_preview=False)
win._add_action_item({"type": "锐化", "params": {"factor": 1.5}, "enabled": True},
                     render_preview=False)
win._add_action_item({"type": "水印", "params": {"text": "x"}, "enabled": True},
                     render_preview=False)

check("每个动作项都有启用勾选框",
      all(widget_at(i).enable_check is not None
          for i in range(win.action_list.count())))
check("默认全部勾选",
      all(widget_at(i).enable_check.isChecked()
          for i in range(win.action_list.count())))
check("启用数 3/3", win._action_counts() == (3, 3))

# 取消勾选第 1 项 → 不参与处理，但仍留在列表与持久化里
widget_at(1).enable_check.setChecked(False)
check("取消勾选后不参与处理", types_enabled() == ["旋转", "水印"])
check("取消勾选后仍保留在列表", types_all() == ["旋转", "锐化", "水印"])
check("启用数 2/3", win._action_counts() == (2, 3))

# 重新勾选 → 恢复参与
widget_at(1).enable_check.setChecked(True)
check("重新勾选后恢复参与", types_enabled() == ["旋转", "锐化", "水印"])
check("启用数恢复 3/3", win._action_counts() == (3, 3))

# 旧数据（无 enabled 键）默认启用
win._on_clear_actions()
win._add_action_item({"type": "裁剪", "params": {"width": 10}},
                     render_preview=False)
check("旧数据无 enabled 键时默认启用",
      len(win._collect_actions()) == 1 and widget_at(0).enable_check.isChecked())

# 持久化必须包含被停用的动作（否则下次启动凭空消失）
win._on_clear_actions()
win._add_action_item({"type": "旋转", "params": {"angle": 90}, "enabled": True},
                     render_preview=False)
win._add_action_item({"type": "锐化", "params": {"factor": 2.0}, "enabled": False},
                     render_preview=False)
win._add_action_item({"type": "水印", "params": {"text": "w"}, "enabled": True},
                     render_preview=False)
check("持久化用全量列表（含停用项）",
      [a.get("enabled") for a in win._all_action_data()] == [True, False, True])
check("处理用启用列表（不含停用项）",
      [a["type"] for a in win._collect_actions()] == ["旋转", "水印"])

# --- 2) 上移/下移：必须新建 item + widget，绝不复用被摘除的 widget -------
win._on_clear_actions()
win._add_action_item({"type": "A", "params": {}}, render_preview=False)
win._add_action_item({"type": "B", "params": {}}, render_preview=False)
win._add_action_item({"type": "C", "params": {}}, render_preview=False)

old_widget_a = widget_at(0)
old_widget_b = widget_at(1)
old_item_a = win.action_list.item(0)

# 点击第 0 项的「下移」
old_widget_a.down_button.click()
check("下移后顺序正确", types_all() == ["B", "A", "C"])
new_widget_a = widget_at(1)
check("移动后的 widget 是新建的（不复用旧 widget → 不闪退）",
      new_widget_a is not old_widget_a)
check("移动后旧 widget 已排入销毁（不再与任何 item 关联）",
      win.action_list.itemWidget(old_item_a) is None)
check("未移动项的 widget 不受影响", widget_at(0) is old_widget_b)

# 连续点击同一按钮可持续移动（选中跟随数据走）
widget_at(1).down_button.click()
check("连续下移：A 走到第 3 位", types_all() == ["B", "C", "A"])
widget_at(2).up_button.click()
check("再上移：A 回到第 2 位", types_all() == ["B", "A", "C"])

# 边界：首项上移 / 末项下移 不应越界或崩溃
before = types_all()
widget_at(0).up_button.click()
check("首项上移无操作（不越界）", types_all() == before)
widget_at(2).down_button.click()
check("末项下移无操作（不越界）", types_all() == before)

# 移动后勾选状态跟随数据
win._on_clear_actions()
win._add_action_item({"type": "X", "params": {}}, render_preview=False)
win._add_action_item({"type": "Y", "params": {}, "enabled": False},
                     render_preview=False)
widget_at(1).enable_check.setChecked(False)
widget_at(1).up_button.click()
check("移动后启用状态跟随数据",
      types_all() == ["Y", "X"]
      and widget_at(0).enable_check.isChecked() is False
      and widget_at(1).enable_check.isChecked() is True)

# --- 3) 标签标题数量统计 -------------------------------------------------
win._on_clear_actions()
win._update_tab_titles()
check("无动作时标题为 动作 [0/0]",
      win.tabs.tabText(win._actions_tab_index) == "动作 [0/0]")

win._add_action_item({"type": "旋转", "params": {"angle": 90}}, render_preview=False)
win._add_action_item({"type": "锐化", "params": {"factor": 1.5}}, render_preview=False)
win._add_action_item({"type": "水印", "params": {"text": "t"}}, render_preview=False)
check("3 个动作全启用 → 动作 [3/3]",
      win.tabs.tabText(win._actions_tab_index) == "动作 [3/3]")

widget_at(1).enable_check.setChecked(False)
check("停用 1 个 → 动作 [2/3]",
      win.tabs.tabText(win._actions_tab_index) == "动作 [2/3]")

widget_at(0).remove_button.click()
check("移除 1 个 → 动作 [1/2]",
      win.tabs.tabText(win._actions_tab_index) == "动作 [1/2]")

win._on_clear_actions()
check("清空 → 动作 [0/0]",
      win.tabs.tabText(win._actions_tab_index) == "动作 [0/0]")

# 输入数量
win.input_files = []
win._update_tab_titles()
check("输入为空 → 输入 [0个]",
      win.tabs.tabText(win._input_tab_index) == "输入 [0个]")
win.input_files = ["/tmp/a.png", "/tmp/b.jpg", "/tmp/c.jxl"]
win._update_tab_titles()
check("3 个输入 → 输入 [3个]",
      win.tabs.tabText(win._input_tab_index) == "输入 [3个]")
win.input_files = []
win._update_tab_titles()

print()
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
sys.exit(0 if not failures else 1)
