# -*- coding: utf-8 -*-
"""回归测试：输出页「保留文件夹结构」+「保留上级目录」。

覆盖：
- 控件存在 / 默认未勾选 / 文案正确。
- 可用状态联动：仅「文件夹」输出模式启用主选项；主勾选 + 文件夹模式启用子选项。
- 持久化往返（preserve_structure / preserve_parent）。
- _build_output_path 行为：默认拍平 / 勾结构镜像 / 勾结构+上级目录 /
  混合来源各自正确 / 根恰为盘符时上级目录为 no-op。
- 与现有功能兼容：输出路径仍为字符串，丢弃/删除原文件逻辑不受影响。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter-test-preserve-structure")
_tmp_settings = tempfile.mkdtemp()
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings)

from jxlforge.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
w = MainWindow()
w._env_refreshed = True
w.show()
for _ in range(5):
    app.processEvents()

total = 0
failures = []


def check(desc, cond):
    global total
    total += 1
    if cond:
        print("PASS  %s" % desc)
    else:
        print("FAIL  %s" % desc)
        failures.append(desc)


# ---------------------------------------------------------------------------
# 1) 控件存在 / 默认 / 文案
# ---------------------------------------------------------------------------
check("structure_check 控件存在", hasattr(w, "structure_check"))
check("parent_check 控件存在", hasattr(w, "parent_check"))
check("默认未勾选 structure_check", w.structure_check.isChecked() is False)
check("默认未勾选 parent_check", w.parent_check.isChecked() is False)
check("主选项文案=保留文件夹结构", w.structure_check.text() == "保留文件夹结构")
check("子选项文案=保留上级目录", w.parent_check.text() == "保留上级目录")

# ---------------------------------------------------------------------------
# 2) 可用状态联动
# ---------------------------------------------------------------------------
# 默认是「原文件夹」模式 → 主选项应禁用。
w.same_folder_radio.setChecked(True)
w._update_structure_checkbox_state()
check("原文件夹模式下 structure_check 禁用", w.structure_check.isEnabled() is False)
check("原文件夹模式下 parent_check 禁用", w.parent_check.isEnabled() is False)

# 切到「文件夹」模式 → 主选项启用、子选项仍禁用（主未勾）。
w.custom_folder_radio.setChecked(True)
w._update_structure_checkbox_state()
check("文件夹模式下 structure_check 启用", w.structure_check.isEnabled() is True)
check("文件夹模式下主未勾时 parent_check 禁用", w.parent_check.isEnabled() is False)

# 勾选主选项 → 子选项启用。
w.structure_check.setChecked(True)
w._update_structure_checkbox_state()
check("主勾选后 parent_check 启用", w.parent_check.isEnabled() is True)

# 取消主选项 → 子选项再次禁用（且保留其勾选状态，不强制清零）。
w.parent_check.setChecked(True)
w.structure_check.setChecked(False)
w._update_structure_checkbox_state()
check("主取消后 parent_check 禁用", w.parent_check.isEnabled() is False)
check("子选项勾选状态在置灰时保留", w.parent_check.isChecked() is True)

# ---------------------------------------------------------------------------
# 3) 持久化往返
# ---------------------------------------------------------------------------
w.custom_folder_radio.setChecked(True)
w.structure_check.setChecked(True)
w.parent_check.setChecked(True)
w._save_jxl_output()
# 模拟重启：用 blockSignals 清零内存态（避免 toggled→_save_jxl_output 把刚存的
# True 覆盖成 False），再 _load_jxl_output 从 QSettings 读回。
w.structure_check.blockSignals(True)
w.parent_check.blockSignals(True)
w.structure_check.setChecked(False)
w.parent_check.setChecked(False)
w.structure_check.blockSignals(False)
w.parent_check.blockSignals(False)
w._load_jxl_output()
check("持久化：preserve_structure 恢复", w.structure_check.isChecked() is True)
check("持久化：preserve_parent 恢复", w.parent_check.isChecked() is True)

# ---------------------------------------------------------------------------
# 辅助：准备自定义输出目录并设定模式
# ---------------------------------------------------------------------------
_out = tempfile.mkdtemp(prefix="jxl_out_")
w.custom_folder_radio.setChecked(True)
w.custom_folder_edit.setText(_out)
w.structure_check.setChecked(False)
w.parent_check.setChecked(False)
w._update_structure_checkbox_state()


def out_of(src, root, structure=False, parent=False):
    """在 input_roots 中登记并调用 _build_output_path。"""
    w.input_roots[src] = root
    w.structure_check.setChecked(structure)
    w.parent_check.setChecked(parent)
    return w._build_output_path(src)


# ---------------------------------------------------------------------------
# 4) _build_output_path 行为
# ---------------------------------------------------------------------------
# 4a) 默认（不勾结构）：拍平进自定义文件夹。
p_a = out_of("D:/photos/2024/a.png", "D:/photos")
check("默认拍平：out/a.jxl", p_a == os.path.join(_out, "a.jxl"))

# 4b) 勾结构：镜像子路径 2024/。
p_b = out_of("D:/photos/2024/a.png", "D:/photos", structure=True)
check("勾结构：out/2024/a.jxl", p_b == os.path.join(_out, "2024", "a.jxl"))

# 4c) 勾结构 + 上级目录：根上移一级 → photos/2024/。
p_c = out_of("D:/photos/2024/a.png", "D:/photos", structure=True, parent=True)
check("结构+上级：out/photos/2024/a.jxl",
      p_c == os.path.join(_out, "photos", "2024", "a.jxl"))

# 4d) 混合来源：各自根独立上移，互不干扰。
src1 = "D:/photos/2024/a.png"
src2 = "D:/video/b.png"
w.input_roots[src1] = "D:/photos"
w.input_roots[src2] = "D:/video"
w.structure_check.setChecked(True)
w.parent_check.setChecked(True)
p_d1 = w._build_output_path(src1)
p_d2 = w._build_output_path(src2)
check("混合来源1：out/photos/2024/a.jxl",
      p_d1 == os.path.join(_out, "photos", "2024", "a.jxl"))
check("混合来源2：out/video/b.jxl",
      p_d2 == os.path.join(_out, "video", "b.jxl"))

# 4e) 根恰为盘符：上级目录上移是 no-op（dir 不变），输出保持拍平。
p_e = out_of("D:/a.png", "D:/", structure=True, parent=True)
check("根为盘符时上级目录 no-op：out/a.jxl", p_e == os.path.join(_out, "a.jxl"))

# ---------------------------------------------------------------------------
# 5) 兼容性：输出路径为字符串，下游逻辑不受影响
# ---------------------------------------------------------------------------
check("输出路径为字符串", isinstance(p_b, str))
check("跨盘符无相对路径时退化为拍平（不抛异常）",
      isinstance(out_of("C:/x/y.png", "D:/photos", structure=True), str))

# ---------------------------------------------------------------------------
print()
print("TOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
