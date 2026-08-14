# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Regression test: 输出标签「高级参数」折叠分组。

覆盖：
  * 控件齐全（折叠分组 / 命令预览 / 重置链接 / 11 个高级参数控件）；
  * 默认全不勾选 -> 不产生任何额外 cjxl 参数；
  * 勾选后正确收集并透传给 converter.encode（含值布尔开关 --modular=1 形式）；
  * 按编码模式置灰（质量精细/保真合成在 JPG 无损重编码模式下不可用，编码策略/容器输出可用）；
  * 显式 -d 距离覆盖 --quality（二者互斥）；
  * 命令预览随控件实时更新；
  * 重置恢复默认；
  * 持久化：勾选状态 + 值跨窗口恢复；
  * ConvertWorker._encode_kwargs 合并 advanced dict。
"""

import sys
import os
import tempfile

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)
# 与 test_output_settings 一致：QSettings 写 ini，不污染注册表。
from PySide6.QtCore import QCoreApplication, QSettings
QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")

from libjxl_gui import converter as conv_mod
from libjxl_gui.main_window import (
    MainWindow, ConvertWorker, _ADVANCED_SCHEMA,
)


def clear_jxl_output():
    settings = QSettings()
    settings.beginGroup("jxl_output")
    settings.remove("")
    settings.endGroup()


clear_jxl_output()

failures = []
total = 0


def check(name, ok):
    global total
    total += 1
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


# ---------------------------------------------------------------------------
# 1. 控件齐全
# ---------------------------------------------------------------------------
w = MainWindow()
check("折叠分组 adv_group 存在（非 checkable，避免误禁用子控件）",
      hasattr(w, "adv_group") and not w.adv_group.isCheckable())
check("折叠箭头 adv_toggle 存在", hasattr(w, "adv_toggle"))
check("命令预览 cmd_edit 存在", hasattr(w, "cmd_edit"))
check("重置链接 reset_adv_button 存在", hasattr(w, "reset_adv_button"))
check("11 个高级参数控件全部构建",
      len(w._adv_widgets) == len(_ADVANCED_SCHEMA) == 11)
check("默认折叠（内容区隐藏）", w.adv_content.isVisible() is False)


# ---------------------------------------------------------------------------
# 2. 默认：全不勾选 -> 无额外参数
# ---------------------------------------------------------------------------
check("默认 lossy 收集为空", w._collect_advanced("lossy") == {})
check("默认 lossless_jpeg 收集为空", w._collect_advanced("lossless_jpeg") == {})
check("默认预览不含 --modular", "--modular" not in w.cmd_edit.text())


# ---------------------------------------------------------------------------
# 3. 勾选后正确收集（含值布尔开关）
# ---------------------------------------------------------------------------
w.lossy_radio.setChecked(True)
w._adv_widgets["modular"][0].setChecked(True)
w._adv_widgets["num_threads"][0].setChecked(True)
w._adv_widgets["num_threads"][1].setValue(8)
adv = w._collect_advanced("lossy")
check("modular 勾选 -> modular=1", adv.get("modular") == 1)
check("num_threads 勾选 -> 8", adv.get("num_threads") == 8)
check("未勾选的 progressive 不出现", "progressive" not in adv)
check("预览含 --modular=1", "--modular=1" in w.cmd_edit.text())
check("预览含 --num_threads 8", "--num_threads" in w.cmd_edit.text() and "8" in w.cmd_edit.text())


# ---------------------------------------------------------------------------
# 4. 按模式置灰
# ---------------------------------------------------------------------------
w.lossless_jpeg_radio.setChecked(True)
check("lossless_jpeg 模式：distance 禁用",
      not w._adv_widgets["distance"][0].isEnabled())
check("lossless_jpeg 模式：epf 禁用（保真合成）",
      not w._adv_widgets["epf"][0].isEnabled())
check("lossless_jpeg 模式：progressive 禁用（质量精细）",
      not w._adv_widgets["progressive"][0].isEnabled())
check("lossless_jpeg 模式：modular 仍可用（编码策略）",
      w._adv_widgets["modular"][0].isEnabled())
check("lossless_jpeg 模式：container 仍可用（容器输出）",
      w._adv_widgets["container"][0].isEnabled())
adv_lj = w._collect_advanced("lossless_jpeg")
check("lossless_jpeg：禁用项不被收集（distance）", "distance" not in adv_lj)
check("lossless_jpeg：禁用项不被收集（epf）", "epf" not in adv_lj)
check("lossless_jpeg：已勾选的 modular 仍收集", adv_lj.get("modular") == 1)
check("lossless_jpeg：已勾选的 num_threads 仍收集", adv_lj.get("num_threads") == 8)


# ---------------------------------------------------------------------------
# 5. 显式 -d 距离覆盖 --quality（互斥）
# ---------------------------------------------------------------------------
w.lossy_radio.setChecked(True)
# 复位其它，仅留 distance
w._reset_advanced()
w._adv_widgets["distance"][0].setChecked(True)
w._adv_widgets["distance"][1].setValue(2.5)
adv_d = w._collect_advanced("lossy")
check("distance 勾选被收集", adv_d.get("distance") == 2.5)
check("distance 模式下仍不出现 quality 键（由 _on_convert 处理）",
      "quality" not in adv_d)


# ---------------------------------------------------------------------------
# 6. 命令预览实时性 + 重置
# ---------------------------------------------------------------------------
w._adv_widgets["modular"][0].setChecked(True)
w._update_cmd_preview()
check("勾 modular 后预览出现 --modular=1", "--modular=1" in w.cmd_edit.text())
w._reset_advanced()
check("重置后 modular 取消勾选", not w._adv_widgets["modular"][0].isChecked())
check("重置后 distance 取消勾选", not w._adv_widgets["distance"][0].isChecked())
check("重置后收集为空", w._collect_advanced("lossy") == {})
check("重置后预览不含 --modular", "--modular" not in w.cmd_edit.text())

# 6b. 命令预览应复刻 encode() 的 --lossless_jpeg 解析：
#     有损 + 批次含 JPG 输入时，实际命令补 --lossless_jpeg=0（cjxl>=0.12 兼容垫片）。
#     预览用占位符路径，故改看选中输入是否含 JPG。
w.lossy_radio.setChecked(True)
w.input_files = ["C:/x/photo.jpg"]
w._update_cmd_preview()
check("有损 + JPG 输入：预览含 --lossless_jpeg=0",
      "--lossless_jpeg=0" in w.cmd_edit.text())
w.input_files = ["C:/x/photo.png"]
w._update_cmd_preview()
check("有损 + 仅 PNG 输入：预览不含 --lossless_jpeg=0",
      "--lossless_jpeg=0" not in w.cmd_edit.text())
w.input_files = ["C:/x/a.png", "C:/x/b.jpg"]  # 混合列表含 JPG
w._update_cmd_preview()
check("有损 + 混合输入（含 JPG）：预览含 --lossless_jpeg=0",
      "--lossless_jpeg=0" in w.cmd_edit.text())
w.lossless_radio.setChecked(True)
w.input_files = ["C:/x/photo.jpg"]
w._update_cmd_preview()
check("无损模式 + JPG：预览不含 --lossless_jpeg=0（仅 -d 0）",
      "--lossless_jpeg=0" not in w.cmd_edit.text())
# 复位输入集合，避免影响后续测试
w.input_files = []
w.lossy_radio.setChecked(True)
w._update_cmd_preview()


# ---------------------------------------------------------------------------
# 6c. 自定义命令：未勾选=只读预览；勾选=可编辑并预填；取消=恢复只读预览
# ---------------------------------------------------------------------------
check("默认未勾选『自定义命令』", w.custom_cmd_check.isChecked() is False)
check("默认 cmd_edit 只读（等同命令预览）", w.cmd_edit.isReadOnly() is True)
check("默认 cmd_edit 显示命令预览", "cjxl" in w.cmd_edit.text())
# 勾选后：可编辑 + 预填当前生成的命令
w.custom_cmd_check.setChecked(True)
check("勾选后 cmd_edit 可编辑", w.cmd_edit.isReadOnly() is False)
check("勾选后预填生成命令（含占位符）",
      "cjxl" in w.cmd_edit.text() and "<输入>" in w.cmd_edit.text())
w.cmd_edit.setText("cjxl <输入> <输出> -e 9 --my-custom-flag")
# 改动其它控件不应覆盖用户已编辑的文本
w._adv_widgets["modular"][0].setChecked(True)
w._update_cmd_preview()
check("勾选态下 _update_cmd_preview 不覆盖用户文本",
      "my-custom-flag" in w.cmd_edit.text())
# 取消勾选：恢复只读 + 重新同步实时预览
w.custom_cmd_check.setChecked(False)
check("取消勾选后 cmd_edit 恢复只读", w.cmd_edit.isReadOnly() is True)
check("取消勾选后重新同步预览", "<输入>" in w.cmd_edit.text())
w._reset_advanced()  # 复位，避免影响后续测试


# ---------------------------------------------------------------------------
# 7. 持久化：勾选状态 + 值跨窗口恢复
# ---------------------------------------------------------------------------
w.lossless_radio.setChecked(True)
w._adv_widgets["modular"][0].setChecked(True)
w._adv_widgets["epf"][0].setChecked(True)
w._adv_widgets["epf"][1].setValue(1)
w.custom_cmd_check.setChecked(True)
w.cmd_edit.setText("cjxl <输入> <输出> -e 3 --persisted-flag")
w._save_jxl_output()
w2 = MainWindow()
check("恢复：modular 勾选状态保留", w2._adv_widgets["modular"][0].isChecked())
check("恢复：epf 勾选状态保留", w2._adv_widgets["epf"][0].isChecked())
check("恢复：epf 值保留=1", w2._adv_widgets["epf"][1].value() == 1)
check("恢复：未勾选的 num_threads 仍为 False",
      not w2._adv_widgets["num_threads"][0].isChecked())
check("恢复：自定义命令勾选状态保留", w2.custom_cmd_check.isChecked())
check("恢复：自定义命令文本保留", "persisted-flag" in w2.cmd_edit.text())
check("恢复：勾选态下 cmd_edit 可编辑", w2.cmd_edit.isReadOnly() is False)
clear_jxl_output()

# 7b. 回归：取消勾选后重新打开必须保持未勾选
# （曾因 bool("false") 误判为 True，导致取消勾选又自动勾上）
w3 = MainWindow()
w3.custom_cmd_check.setChecked(False)
w3._save_jxl_output()
w4 = MainWindow()
check("回归：取消『自定义命令』后重新打开仍为未勾选",
      w4.custom_cmd_check.isChecked() is False)
clear_jxl_output()

# 7c. 回归：重置按钮不得 flat（Fusion 主题下 flat 会看不见边框）
check("重置按钮非 flat（Fusion 主题下需可见边框）",
      w4.reset_adv_button.isFlat() is False)


# ---------------------------------------------------------------------------
# 8. ConvertWorker._encode_kwargs 合并 advanced
# ---------------------------------------------------------------------------
cw = ConvertWorker([], [], effort=7, distance=None, quality=90,
                   advanced={"modular": 1, "epf": 2, "noise": 0})
ek = cw._encode_kwargs()
check("worker 合并 modular", ek.get("modular") == 1)
check("worker 合并 epf", ek.get("epf") == 2)
check("worker 合并 noise", ek.get("noise") == 0)
check("worker 基础 quality 保留", ek.get("quality") == 90)
check("worker 基础 effort 保留", ek.get("effort") == 7)


# ---------------------------------------------------------------------------
# 8b. 自定义命令：worker 标记 + 占位符替换 + 交给 converter._run
# ---------------------------------------------------------------------------
_captured_cmd = []


def _fake_run(args, priority=None):
    _captured_cmd.append((list(args), priority))
    return True, "fake cjxl ok"


_real_run = conv_mod._run
conv_mod._run = _fake_run
cwc = ConvertWorker(
    [("in.png", "out.jxl", True)], [], custom_cmd="cjxl <输入> <输出> -e 7 --xcustom"
)
check("自定义命令：_encode_tag 标记正确", cwc._encode_tag() == "[自定义命令]")
ok_cc, msg_cc = cwc._run_custom_command("C:/in/photo.jpg", "C:/out/photo.jxl")
check("自定义命令：占位符已替换",
      _captured_cmd and _captured_cmd[0][0][1] == "C:/in/photo.jpg"
      and _captured_cmd[0][0][2] == "C:/out/photo.jxl")
check("自定义命令：执行返回 ok", ok_cc is True)
conv_mod._run = _real_run


# ---------------------------------------------------------------------------
# 9. 端到端：勾选参数确实进入 converter.encode 的 kwargs
# ---------------------------------------------------------------------------
_captured = []


def _fake_encode(input_path, output_path, **kwargs):
    _captured.append((input_path, output_path, dict(kwargs)))
    return True, "fake cjxl ok"


conv_mod.encode = _fake_encode


def run_convert_and_capture(window):
    _captured.clear()
    tmpdir = tempfile.mkdtemp()
    src = os.path.join(tmpdir, "a.jpg")
    open(src, "w").close()
    window.input_files = [src]
    window.custom_folder_radio.setChecked(True)
    window.custom_folder_edit.setText(tmpdir)
    window.format_combo.setCurrentText("JPEG XL (*.jxl)")
    window._on_convert()
    worker = window._convert_worker
    if worker is not None:
        worker.wait(10000)
    for _ in range(50):
        QApplication.instance().processEvents()
    return _captured[-1][2] if _captured else None


w3 = MainWindow()
w3.lossy_radio.setChecked(True)
w3._adv_widgets["modular"][0].setChecked(True)
w3._adv_widgets["faster_decoding"][0].setChecked(True)
w3._adv_widgets["faster_decoding"][1].setValue(2)
w3._adv_widgets["epf"][0].setChecked(True)
w3._adv_widgets["epf"][1].setValue(2)
kw3 = run_convert_and_capture(w3)
check("端到端：modular=1 进入 encode", kw3.get("modular") == 1)
check("端到端：faster_decoding=2 进入 encode（多档位 0–4）", kw3.get("faster_decoding") == 2)
check("端到端：epf=2 进入 encode", kw3.get("epf") == 2)
check("端到端：quality 仍为 90", kw3.get("quality") == 90)

# 显式 -d 距离覆盖 quality
w3._adv_widgets["modular"][0].setChecked(False)
w3._adv_widgets["faster_decoding"][0].setChecked(False)
w3._adv_widgets["epf"][0].setChecked(False)
w3._adv_widgets["distance"][0].setChecked(True)
w3._adv_widgets["distance"][1].setValue(3.0)
kw4 = run_convert_and_capture(w3)
check("端到端：distance 覆盖 -> 3.0", kw4.get("distance") == 3.0)
check("端到端：distance 覆盖 -> quality=None", kw4.get("quality") is None)
check("端到端：distance 后仍保留 effort", kw4.get("effort") == 7)


# ---------------------------------------------------------------------------
# 9. faster_decoding 多档位校验（2026-08-14 纠正：原误作 bool_value 开关，
# 仅暴露 =1 一档；实测 cjxl v0.12.0 为 --faster_decoding=0..4 整数档位）。
# ---------------------------------------------------------------------------
from PySide6.QtWidgets import QSpinBox
_fd_schema = next(s for s in _ADVANCED_SCHEMA if s["key"] == "faster_decoding")
check("faster_decoding schema: kind=int", _fd_schema.get("kind") == "int")
check("faster_decoding schema: min=0, max=4, default=0",
      _fd_schema.get("min") == 0 and _fd_schema.get("max") == 4
      and _fd_schema.get("default") == 0)
_fd_w = w3._adv_widgets["faster_decoding"]
check("faster_decoding 控件为 QSpinBox（多档位）", isinstance(_fd_w[1], QSpinBox))
check("faster_decoding 默认不勾选（不传参）", _fd_w[0].isChecked() is False)
_fd_w[0].setChecked(True)
check("faster_decoding 档位边界：min=0", _fd_w[1].minimum() == 0)
check("faster_decoding 档位边界：max=4", _fd_w[1].maximum() == 4)
# 显式最低档 0：证明 cjxl 接受 --faster_decoding=0（= 默认，合法，与
# modular/container 的「=0 无意义」不同，这正是本次纠正的核心）。
_fd_w[1].setValue(0)
kw_fd0 = run_convert_and_capture(w3)
check("端到端：faster_decoding=0 进入 encode（cjxl 合法默认档）",
      kw_fd0.get("faster_decoding") == 0)
# 最高档 4
_fd_w[1].setValue(4)
kw_fd = run_convert_and_capture(w3)
check("端到端：faster_decoding=4 进入 encode（最高档）",
      kw_fd.get("faster_decoding") == 4)
_fd_w[0].setChecked(False)


# ---------------------------------------------------------------------------
# 10. 输出页整页滚动（方案①：高级参数展开不再撑大窗口，靠滚动条）
# ---------------------------------------------------------------------------
from PySide6.QtWidgets import QScrollArea, QFrame
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
check("输出页已包进 QScrollArea", isinstance(w.output_scroll, QScrollArea))
check("滚动区 widgetResizable=True（内容短则填满、长则滚动）",
      w.output_scroll.widgetResizable() is True)
check("关闭横向滚动条",
      w.output_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff)
check("滚动区无边框（融入标签页）",
      w.output_scroll.frameShape() == QFrame.NoFrame)
# 输出页（滚动区/视口/内容容器/分组框）必须全部透明，直接透出 QTabWidget
# 的面板底色——与其它标签页一致，且自动跟随系统深浅色主题。写死任何颜色或
# 强制 palette 角色都会造成「切换主题后输出页不变色」的冻结 bug。
_inn = w.output_scroll.widget()
check("滚动区/视口/内容容器均透明（透出面板色、跟随主题）",
      w.output_scroll.autoFillBackground() is False
      and w.output_scroll.viewport().autoFillBackground() is False
      and _inn is not None
      and _inn.autoFillBackground() is False)
check("滚动区已绑定内部内容 widget", _inn is not None)
_out_idx = -1
for i in range(w.tabs.count()):
    if w.tabs.tabText(i) == "输出":
        _out_idx = i
        break
_out_page = w.tabs.widget(_out_idx)
check("滚动区位于『输出』标签页",
      _out_idx >= 0 and _out_page.layout() is not None
      and _out_page.layout().itemAt(0).widget() is w.output_scroll)
# 三个分组框也必须透明，否则真实 Windows 深色主题下会被填成 Base，
# 整页比其它标签页暗一截，且不随主题切换。
from PySide6.QtWidgets import QGroupBox
_groups = [c for c in _inn.children() if isinstance(c, QGroupBox)]
check("输出页三个分组框透明（透出面板色、跟随主题）",
      len(_groups) == 3
      and all(g.autoFillBackground() is False for g in _groups))


print()
print("TOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
