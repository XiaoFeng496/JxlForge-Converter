# -*- coding: utf-8 -*-
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""Headless tests for the 设置页「选项」区「退出时保存动作列表」功能。

Covers:
  * 复选框存在且位于设置页「选项」区（不在「高级参数」区）；
  * 默认关闭；
  * 勾选后写入 QSettings actions/save_on_exit=true；
  * 添加动作 + closeEvent 把动作列表以 JSON 写入 actions/list；
  * 全新 MainWindow 启动恢复动作列表与勾选态；
  * 取消勾选清除已存动作列表（关闭 = 不保存，下次不残留）；
  * 退出时勾选关闭同样保持下次启动为空。
"""

from PySide6.QtWidgets import QApplication, QCheckBox
from PySide6.QtCore import QCoreApplication, QSettings

_app = QApplication.instance() or QApplication(sys.argv)
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini。
QSettings.setDefaultFormat(QSettings.IniFormat)
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_actions_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)
QCoreApplication.setOrganizationName("jxlforge_test_actions")
QCoreApplication.setApplicationName("jxlforge_test_actions")

from jxlforge.main_window import MainWindow

failures = []
total = 0


def check(name, ok):
    global total
    total += 1
    if ok:
        print("  PASS", name)
    else:
        print("  FAIL", name)
        failures.append(name)


def _clear_actions_settings():
    s = QSettings()
    s.remove("actions")


# 1) 复选框存在且位于「选项」区，默认关闭，不在「高级参数」区。
w = MainWindow()
check("退出时保存动作列表 复选框存在", isinstance(w.save_actions_on_exit_check, QCheckBox))
check("复选框位于设置页「选项」区", w.save_actions_on_exit_check.parent() is w.options_group)
check("复选框不在「高级参数」区", w.save_actions_on_exit_check.parent() is not w.adv_params_group)
check("默认关闭", w.save_actions_on_exit_check.isChecked() is False)

# 2) 勾选后持久化 save_on_exit。
_clear_actions_settings()
w.save_actions_on_exit_check.setChecked(True)
QSettings().sync()
s = QSettings()
s.beginGroup("actions")
check("勾选后 save_on_exit 持久化为 true", s.value("save_on_exit", False, type=bool) is True)
s.endGroup()

# 3) 添加动作 + 退出时写入 JSON 列表。
w._add_action_item({"type": "旋转", "params": {"angle": 90}}, render_preview=False)
w._add_action_item({"type": "水印", "params": {"text": "demo", "size": 24}}, render_preview=False)
acts = w._collect_actions()
check("已收集 2 个动作", len(acts) == 2)
w.close()
QSettings().sync()
raw = QSettings().value("actions/list", "", type=str)
stored = json.loads(raw) if raw else []
check("closeEvent 写入动作列表（数量=2）", len(stored) == 2)
check("动作顺序与类型正确", stored[0]["type"] == "旋转" and stored[1]["type"] == "水印")

# 4) 全新启动恢复列表与勾选态。
w2 = MainWindow()
restored = w2._collect_actions()
check("重启恢复动作列表（数量=2）", len(restored) == 2)
check("重启恢复第 2 个动作类型=水印", restored[1]["type"] == "水印")
check("重启恢复勾选态=开启", w2.save_actions_on_exit_check.isChecked() is True)
w2.close()

# 5) 取消勾选清除已存动作列表。
w3 = MainWindow()
w3.save_actions_on_exit_check.setChecked(False)
QSettings().sync()
raw2 = QSettings().value("actions/list", None, type=str)
check("取消勾选后已存动作列表被清除", raw2 is None or raw2 == "")
w3.close()

# 6) 退出时若已取消勾选，下次启动动作列表仍为空（不残留旧数据）。
w4 = MainWindow()
w4._add_action_item({"type": "锐化", "params": {"factor": 1.5}}, render_preview=False)
w4.save_actions_on_exit_check.setChecked(False)
w4.close()
QSettings().sync()
w5 = MainWindow()
check("退出时关闭：下次启动动作列表为空", w5._collect_actions() == [])
check("退出时关闭：下次启动勾选态为关闭", w5.save_actions_on_exit_check.isChecked() is False)
w5.close()

# 7) 关闭守卫：加载期间不应写回（_actions_loading 期间 _save_actions_setting 直接返回）。
w6 = MainWindow()
check("加载完成后 _actions_loading 复位为 False", getattr(w6, "_actions_loading", "MISSING") is False)
w6.close()

print("\n%d/%d checks passed" % (total - len(failures), total))
print("ALL_OK" if not failures else "FAILED: " + ", ".join(failures))
sys.exit(1 if failures else 0)
