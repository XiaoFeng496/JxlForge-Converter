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
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini
# （%APPDATA%\libjxl\libjxl-gui.ini）。此前本测试在 line 104 将 adv_threads_toggle
# 设 True 后会把 adv_threads_enabled=true 写进真实 ini，导致 GUI 下次启动被自动开启。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui import converter as conv_mod
from libjxl_gui.main_window import (
    MainWindow, ConvertWorker, _ADVANCED_SCHEMA,
)


def fresh_window():
    """构造 MainWindow 并触发首帧，使断言环境与真实启动一致。

    showEvent 会延迟恢复持久化的输出/输出位置/转换优先级设置，因此构造后
    必须 show() + processEvents 让首帧发生，否则这些控件停在 build 默认态、
    恢复类断言失败。跳过环境探测(_env_refreshed)避免测试内反复 subprocess。
    """
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


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
# 启用「启用高级参数」母开关，并勾选子项「手动设置每文件线程数」：num_threads 受
# 二者共同门控，关闭或子项未勾选时控件被禁用、_collect_advanced 会跳过。
w._maybe_warn_adv_params = lambda: None  # 抑制首次开启弹窗，避免测试阻塞
w.adv_threads_toggle.setChecked(True)
w.adv_num_threads_toggle.setChecked(True)
w._adv_widgets["modular"][0].setChecked(True)
w._adv_widgets["num_threads"][0].setChecked(True)
w._adv_widgets["num_threads"][1].setValue(8)
w._update_cmd_preview(force=True)  # 强制按当前（已启用 num_threads）状态刷新预览
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
# 回归：未勾选自定义命令时，quality/effort 变化应通过信号自动刷新预览
#（曾漏连 valueChanged -> _update_cmd_preview，导致改 quality 不刷新）。
w.lossy_radio.setChecked(True)
w.quality_spin.setValue(50)
check("改 quality 自动刷新预览（含 --quality 50）", "--quality 50" in w.cmd_edit.text())
w.quality_spin.setValue(75)
check("再次改 quality 继续刷新预览", "--quality 75" in w.cmd_edit.text())
w.effort_combo.setCurrentText("9")
check("改 effort 自动刷新预览（含 -e 9）", "-e 9" in w.cmd_edit.text())
# 复位，避免影响后续依赖默认 effort/quality 的断言
w.effort_combo.setCurrentText("7")
w.quality_spin.setValue(90)
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
w2 = fresh_window()
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
w3 = fresh_window()
w3.custom_cmd_check.setChecked(False)
w3._save_jxl_output()
w4 = fresh_window()
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
    return True, "fake cjxl ok", ""


_real_run = conv_mod._run
conv_mod._run = _fake_run
cwc = ConvertWorker(
    [("in.png", "out.jxl", True)], [], custom_cmd="cjxl <输入> <输出> -e 7 --xcustom"
)
check("自定义命令：_encode_tag 标记正确", cwc._encode_tag() == "[自定义命令]")
ok_cc, msg_cc, tag_cc = cwc._run_custom_command("C:/in/photo.jpg", "C:/out/photo.jxl")
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
    return True, "fake cjxl ok", ""


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


w3 = fresh_window()
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
from PySide6.QtWidgets import QScrollArea, QFrame, QCheckBox
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
# 四个分组框也必须透明，否则真实 Windows 深色主题下会被填成 Base，
# 整页比其它标签页暗一截，且不随主题切换。
from PySide6.QtWidgets import QGroupBox
_groups = [c for c in _inn.children() if isinstance(c, QGroupBox)]
check("输出页四个分组框透明（透出面板色、跟随主题）",
      len(_groups) == 4
      and all(g.autoFillBackground() is False for g in _groups))


# ---------------------------------------------------------------------------
# 9. 回归：启用高级参数关闭时 num_threads 复选框必须禁用
#    历史 bug：编码模式联动(_on_encode_mode_changed)遍历所有高级参数 check 设
#    enabled（num_threads 三种模式恒 True），覆盖了 adv_threads 开关的禁用，
#    导致启动默认(开关关闭)时 num_threads 仍可被勾选。需两个因素共同决定。
# ---------------------------------------------------------------------------
def clear_conversion():
    settings = QSettings()
    settings.beginGroup("conversion")
    settings.remove("")
    settings.endGroup()
    settings.sync()

clear_conversion()
w_nt = fresh_window()
nt_entry = w_nt._adv_widgets.get("num_threads")
check("num_threads 控件已构建 (_adv_widgets)", nt_entry is not None)
if nt_entry is not None:
    nt_check = nt_entry[0]
    # 启动默认：conversation 组无 adv_threads_enabled → 开关关闭 → num_threads 禁用
    check("启动默认(高级参数关闭)时 num_threads 复选框禁用",
          (not w_nt.adv_threads_toggle.isChecked()) and (not nt_check.isEnabled()))
    # 开启高级参数（母开关 + 子项「手动设置每文件线程数」）→ num_threads 可用
    w_nt._maybe_warn_adv_params = lambda: None  # 抑制首次开启弹窗
    w_nt.adv_threads_toggle.setChecked(True)
    w_nt.adv_num_threads_toggle.setChecked(True)
    check("启用高级参数后 num_threads 复选框可用", nt_check.isEnabled())
    # 关闭 → 再次禁用（且不应被切换编码模式覆盖）
    w_nt.adv_threads_toggle.setChecked(False)
    w_nt.lossless_radio.setChecked(True)
    check("关闭高级参数且切换编码模式后 num_threads 仍禁用",
          not nt_check.isEnabled())
clear_conversion()


# ---------------------------------------------------------------------------
# 10. 回归：勾选「自定义命令」后整体禁用全部编码参数控件
#     诉求：自定义命令取代自动拼装的 cjxl 参数，勾选后所有编码参数
#     (输出格式/编码模式/effort/quality/高级参数/启用高级参数开关/重置按钮)
#     应置灰，禁止调节；取消勾选后按编码模式恢复正确启用态；
#     持久化勾选态重启后同样禁用。开关本身与命令正文仍须可操作。
# ---------------------------------------------------------------------------
clear_jxl_output()
w_cc = fresh_window()
check("自定义命令复选框已构建", w_cc.custom_cmd_check is not None)
check("默认未勾选自定义命令", not w_cc.custom_cmd_check.isChecked())
# 默认(未勾选)态：有损模式下 quality 可用、effort/格式/模式/高级开关/重置按钮可用
check("默认态 quality_spin 可用（有损模式）", w_cc.quality_spin.isEnabled())
check("默认态 effort_combo 可用", w_cc.effort_combo.isEnabled())
check("默认态 format_combo 可用", w_cc.format_combo.isEnabled())
check("默认态 三个编码模式 radio 可用",
      w_cc.lossy_radio.isEnabled()
      and w_cc.lossless_radio.isEnabled()
      and w_cc.lossless_jpeg_radio.isEnabled())
check("默认态 adv_threads_toggle 可用", w_cc.adv_threads_toggle.isEnabled())
check("默认态 reset_adv_button 可用", w_cc.reset_adv_button.isEnabled())

# 勾选自定义命令 -> 全部编码参数禁用
w_cc.custom_cmd_check.setChecked(True)
check("勾选后 format_combo 禁用", not w_cc.format_combo.isEnabled())
check("勾选后 三个编码模式 radio 禁用",
      not w_cc.lossy_radio.isEnabled()
      and not w_cc.lossless_radio.isEnabled()
      and not w_cc.lossless_jpeg_radio.isEnabled())
check("勾选后 effort_combo 禁用", not w_cc.effort_combo.isEnabled())
check("勾选后 quality_slider 禁用", not w_cc.quality_slider.isEnabled())
check("勾选后 quality_spin 禁用", not w_cc.quality_spin.isEnabled())
check("勾选后 adv_threads_toggle 禁用", not w_cc.adv_threads_toggle.isEnabled())
check("勾选后 reset_adv_button 禁用", not w_cc.reset_adv_button.isEnabled())
# 全部高级参数 check + value 一并禁用
_all_adv_disabled = True
for s in _ADVANCED_SCHEMA:
    _c, _v, _ = w_cc._adv_widgets[s["key"]]
    if _c.isEnabled():
        _all_adv_disabled = False
    if _v is not None and _v.isEnabled():
        _all_adv_disabled = False
check("勾选后 全部高级参数控件禁用", _all_adv_disabled)
# 开关本身与命令正文仍可操作（用于取消勾选 / 编辑）
check("勾选后 自定义命令复选框本身仍可用", w_cc.custom_cmd_check.isEnabled())
check("勾选后 命令正文 cmd_edit 仍可用且非只读",
      w_cc.cmd_edit.isEnabled() and not w_cc.cmd_edit.isReadOnly())

# 取消勾选 -> 恢复正确启用态（有损模式 quality 应恢复可用）
w_cc.custom_cmd_check.setChecked(False)
check("取消勾选后 format_combo 恢复可用", w_cc.format_combo.isEnabled())
check("取消勾选后 三个编码模式 radio 恢复可用",
      w_cc.lossy_radio.isEnabled()
      and w_cc.lossless_radio.isEnabled()
      and w_cc.lossless_jpeg_radio.isEnabled())
check("取消勾选后 effort_combo 恢复可用", w_cc.effort_combo.isEnabled())
check("取消勾选后 quality_spin 恢复可用（有损模式）", w_cc.quality_spin.isEnabled())
check("取消勾选后 adv_threads_toggle 恢复可用", w_cc.adv_threads_toggle.isEnabled())
check("取消勾选后 reset_adv_button 恢复可用", w_cc.reset_adv_button.isEnabled())

# 持久化勾选态：写入 jxl_output 组后新窗口应直接禁用
w_cc.custom_cmd_check.setChecked(True)  # 触发 _save_jxl_output 写入临时 ini
w_cc2 = fresh_window()  # 从临时 ini 恢复持久化勾选态
check("持久化勾选态：新窗口默认已勾选自定义命令",
      w_cc2.custom_cmd_check.isChecked())
check("持久化勾选态：新窗口编码参数整体禁用",
      (not w_cc2.format_combo.isEnabled())
      and (not w_cc2.effort_combo.isEnabled())
      and (not w_cc2.quality_spin.isEnabled())
      and (not w_cc2.adv_threads_toggle.isEnabled())
      and (not w_cc2.reset_adv_button.isEnabled()))
clear_jxl_output()



# ---------------------------------------------------------------------------
# 11. 回归：复选框在窗口失焦时不应变黑（与 radio/progress_bar 同源）
#     根因：Qt 对失焦窗口的控件用 QPalette.Inactive 组绘制。原生 Windows 样式下
#     勾选框的选中指示器由 QPalette.Accent 驱动，Active 组是系统强调色(蓝)，
#     而 Inactive 组解析为黑，导致失焦变黑。_sync_inactive_palette 把 Inactive
#     组同步成 Active 组，须覆盖 QCheckBox（此前只覆盖了 QRadioButton）。
# ---------------------------------------------------------------------------
def _all_cb_inactive_eq_active(win):
    app_pal = QApplication.palette()
    ok = True
    for cb in win.findChildren(QCheckBox):
        if cb.palette().color(QPalette.ColorGroup.Inactive, QPalette.Accent) != \
                app_pal.color(QPalette.ColorGroup.Active, QPalette.Accent):
            ok = False
            break
    return ok

w_cb = fresh_window()
check("复选框 Inactive 组 Accent 已同步为 Active（失焦不变黑）",
      _all_cb_inactive_eq_active(w_cb))
check("radio 仍保持 Inactive==Active（同源保护不被破坏）",
      w_cb.progress_bar.palette().color(QPalette.ColorGroup.Inactive, QPalette.Accent)
      == QApplication.palette().color(QPalette.ColorGroup.Active, QPalette.Accent))


# ---------------------------------------------------------------------------
# 12. 回归：高级参数「JXL 容器 (--container)」复选框悬停提示说明保留元数据
#     根因：ICC 颜色配置只能存于 JXL 容器（jxlC box），纯码流(--container=0)会丢弃；
#     故在容器开关上注明启用即保留 Exif/XMP/ICC 等元数据，呼应此约束。
# ---------------------------------------------------------------------------
_container_check = w_cc2._adv_widgets["container"][0]
check("JXL 容器复选框悬停提示说明保留元数据",
      _container_check.toolTip() == "启用可保留元数据（如 Exif、XMP、ICC 颜色配置等）")


print()
print("TOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
