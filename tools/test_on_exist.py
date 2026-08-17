# -*- coding: utf-8 -*-
"""回归测试：输出标签页「选项」区域——输出文件已存在时的冲突策略。

覆盖：
  * on_exist_combo 控件存在、含 4 项（替换/询问/跳过/重命名）、默认"替换"；
  * 持久化往返：四种策略各存一次、新建实例恢复一致；
  * _uniquify_path：已存在时返回 name (1).ext，再存在返回 name (2).ext；
  * _resolve_existing_outputs：在 替换/跳过/重命名 下对 jobs 的正确改写
    （"替换"原样保留、"跳过"移出、"重命名"改路径）。不触发"询问"弹窗。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui-test-onexist")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_onexist_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def fresh_window():
    """构造 MainWindow 并触发首帧，使断言环境与真实启动一致。"""
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


total = 0
failures = []


def check(name, cond):
    global total
    total += 1
    if not cond:
        failures.append(name)
        print("FAIL  %s" % name)
    else:
        print("PASS  %s" % name)


# 1) 控件存在、4 项、默认"替换"
w = fresh_window()
check("on_exist_combo 存在", hasattr(w, "on_exist_combo"))
check("冲突策略含 4 项", w.on_exist_combo.count() == 4)
check("默认冲突策略为「替换」", w.on_exist_combo.currentText() == "替换")
expected = ["替换", "询问", "跳过", "重命名"]
got = [w.on_exist_combo.itemText(i) for i in range(w.on_exist_combo.count())]
check("4 项文本正确", got == expected)

# 2) 持久化往返
for policy in ["替换", "询问", "跳过", "重命名"]:
    w1 = fresh_window()
    w1.on_exist_combo.setCurrentText(policy)
    w1._save_jxl_output()
    w2 = fresh_window()
    check("持久化往返：%s" % policy, w2.on_exist_combo.currentText() == policy)

# 3) _uniquify_path
tmpd = tempfile.mkdtemp(prefix="libjxl_uq_")
a = os.path.join(tmpd, "a.txt")
open(a, "w").close()
check("已存在 a.txt → a (1).txt",
      w._uniquify_path(a) == os.path.join(tmpd, "a (1).txt"))
open(os.path.join(tmpd, "a (1).txt"), "w").close()
check("已存在 a (1).txt → a (2).txt",
      w._uniquify_path(a) == os.path.join(tmpd, "a (2).txt"))
b = os.path.join(tmpd, "b.txt")  # 不存在
check("不存在 b.txt → 原样", w._uniquify_path(b) == b)

# 4) _resolve_existing_outputs（不触发"询问"弹窗）
tmpd2 = tempfile.mkdtemp(prefix="libjxl_res_")
a_jxl = os.path.join(tmpd2, "a.jxl")
open(a_jxl, "w").close()  # 已存在
b_jxl = os.path.join(tmpd2, "b.jxl")  # 不存在
jobs = [("src1", a_jxl, True), ("src2", b_jxl, True)]

# 替换：原样保留（即使已存在，cjxl/djxl 默认覆盖）
w.on_exist_combo.setCurrentText("替换")
resolved, skipped, cancelled = w._resolve_existing_outputs(jobs)
check("替换：jobs 不变", resolved == jobs and not skipped and not cancelled)

# 跳过：已存在的 a.jxl 被移出 jobs
w.on_exist_combo.setCurrentText("跳过")
resolved, skipped, cancelled = w._resolve_existing_outputs(jobs)
check("跳过：仅剩不存在的 b.jxl",
      resolved == [("src2", b_jxl, True)])
check("跳过：a.jxl 进入 skipped、未取消",
      skipped == [a_jxl] and not cancelled)

# 重命名：已存在的 a.jxl 被改写为 a (1).jxl
w.on_exist_combo.setCurrentText("重命名")
resolved, skipped, cancelled = w._resolve_existing_outputs(jobs)
check("重命名：b.jxl 不变", ("src2", b_jxl, True) in resolved)
check("重命名：a.jxl → a (1).jxl",
      ("src1", os.path.join(tmpd2, "a (1).jxl"), True) in resolved)
check("重命名：无 skipped、未取消", not skipped and not cancelled)


print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
