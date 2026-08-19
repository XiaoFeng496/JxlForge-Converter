# -*- coding: utf-8 -*-
"""Regression test: 输出格式新增 JPEG，针对「无损 JPEG 转码的 JXL」重建为 JPG。

覆盖：
  * 输出格式下拉新增 "JPEG (*.jpg)"，且 _build_output_path 对 JPG 输出返回 .jpg；
  * JPG 输出时 JXL 输入走 converter.decode（输出 .jpg），非 JXL 输入被跳过且不 encode；
  * JPG 输出且输入含 JXL 时，命令预览显示 djxl 解码命令（而非 cjxl 编码命令）。

沿用 test_advanced_params 的 QSettings 隔离与 fresh_window 约定；仅打桩 converter.encode /
converter.decode，不触碰真实 cjxl/djxl。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication, QSettings, QTimer

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui-test-jpg")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_jpg_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui import converter as conv_mod
from libjxl_gui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


# Headless 测试用：自动点击任何 QMessageBox 的 AcceptRole 按钮（「继续」/「确定」），
# 避免阻塞式确认框（JPEG 输出质量提示、jxlinfo 推荐框）在无交互环境下卡死
# _on_convert。仅用于测试，不影响产品行为。
# 注意：PySide6 的 QMessageBox.button() 只接受 StandardButton，不接受 ButtonRole；
# 故按 buttonRole() 遍历按钮查找 AcceptRole。
_orig_msgbox_exec = QMessageBox.exec
def _auto_accept_exec(self):
    btn = None
    for b in self.buttons():
        if self.buttonRole(b) == QMessageBox.AcceptRole:
            btn = b
            break
    if btn is not None:
        # 先让原始 exec 进入模态循环，再于下一轮事件循环点击（时序正确）。
        QTimer.singleShot(0, btn.click)
    return _orig_msgbox_exec(self)
QMessageBox.exec = _auto_accept_exec



def fresh_window():
    """构造 MainWindow 并触发首帧，使断言环境与真实启动一致。"""
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


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
# 1. 下拉含 JPG 项，输出扩展名正确
# ---------------------------------------------------------------------------
w = fresh_window()
items = [w.format_combo.itemText(i) for i in range(w.format_combo.count())]
check("输出格式下拉包含 JPEG (*.jpg)", "JPEG (*.jpg)" in items)

w.format_combo.setCurrentText("JPEG (*.jpg)")
check("JPG 输出 _build_output_path 返回 .jpg",
      w._build_output_path("C:/x/photo.jxl").endswith(".jpg"))

w.format_combo.setCurrentText("JPEG XL (*.jxl)")
check("JXL 输出 _build_output_path 仍返回 .jxl",
      w._build_output_path("C:/x/photo.png").endswith(".jxl"))

w.format_combo.setCurrentText("PNG (*.png)")
check("PNG 输出 _build_output_path 仍返回 .png",
      w._build_output_path("C:/x/photo.jxl").endswith(".png"))


# ---------------------------------------------------------------------------
# 2. JPG 输出：JXL 输入走 decode（.jpg 输出），非 JXL 输入被跳过
# ---------------------------------------------------------------------------
_real_decode = conv_mod.decode
_real_encode = conv_mod.encode
_decode_calls = []
_encode_calls = []


def _fake_decode(input_path, output_path, priority=conv_mod.DEFAULT_PRIORITY):
    _decode_calls.append((input_path, output_path))
    return True, "fake djxl ok", ""


def _fake_encode(input_path, output_path, **kwargs):
    _encode_calls.append((input_path, output_path))
    return True, "fake cjxl ok", ""


conv_mod.decode = _fake_decode
conv_mod.encode = _fake_encode
try:
    tmp = tempfile.mkdtemp()
    jxl_src = os.path.join(tmp, "a.jxl")
    png_src = os.path.join(tmp, "b.png")
    open(jxl_src, "w").close()
    open(png_src, "w").close()

    w2 = fresh_window()
    w2.input_files = [jxl_src, png_src]
    w2.format_combo.setCurrentText("JPEG (*.jpg)")
    w2._on_convert()
    worker = w2._convert_worker
    if worker is not None:
        worker.wait(10000)
    for _ in range(50):
        QApplication.instance().processEvents()

    check("JPG 输出：JXL 输入走 converter.decode", len(_decode_calls) == 1)
    check("JPG 输出：decode 输出为 .jpg",
          bool(_decode_calls) and _decode_calls[0][1].lower().endswith(".jpg"))
    check("JPG 输出：非 JXL 输入被跳过（未调用 encode）",
          len(_encode_calls) == 0)
    check("JPG 输出：仅 1 个文件入队（非 JXL 跳过）",
          worker is not None and len(getattr(worker, "jobs", [])) == 1)
finally:
    conv_mod.decode = _real_decode
    conv_mod.encode = _real_encode


# ---------------------------------------------------------------------------
# 3. 选中 JPG 输出即显示 djxl 解码命令（无需等待放入 JXL）
# ---------------------------------------------------------------------------
w3 = fresh_window()
tmp3 = tempfile.mkdtemp()
jxl_in = os.path.join(tmp3, "c.jxl")
open(jxl_in, "w").close()
w3.input_files = [jxl_in]

w3.format_combo.setCurrentText("JPEG (*.jpg)")
w3._update_cmd_preview()
check("选中 JPG 输出即显示 djxl 解码命令",
      "djxl" in w3.cmd_edit.text() and "<输入>" in w3.cmd_edit.text())

# 即使尚未放入任何文件，选中 JPG 输出也应直接显示 djxl 占位命令
w3.input_files = []
w3._update_cmd_preview()
check("JPG 输出无输入时也直接显示 djxl 命令", "djxl" in w3.cmd_edit.text())

w3.input_files = [jxl_in]
w3.format_combo.setCurrentText("JPEG XL (*.jxl)")
w3._update_cmd_preview()
check("切回 JXL 输出预览恢复 cjxl 编码命令", "cjxl" in w3.cmd_edit.text())

# 仅靠「切换输出格式」信号自动刷新预览，不手动调 _update_cmd_preview、也不点
# 「自定义命令」——验证 format_combo.currentTextChanged 已连接 _update_cmd_preview。
w3.format_combo.setCurrentText("JPEG (*.jpg)")
check("仅靠切换输出格式即自动刷新预览为 djxl（无需点自定义命令）",
      "djxl" in w3.cmd_edit.text())


# ---------------------------------------------------------------------------
# 4. 输出格式持久化：保存后新实例应恢复，清空后回到默认 JXL
# ---------------------------------------------------------------------------
# 默认（清空 ini）：新实例应为 JPEG XL (*.jxl)
_s = QSettings()
_s.beginGroup("jxl_output")
_s.remove("")  # 清空整组，确保从干净状态验证默认值
_s.endGroup()
w_def = fresh_window()
check("清空后输出格式默认 JPEG XL (*.jxl)",
      w_def.format_combo.currentText() == "JPEG XL (*.jxl)")

# 切换为 JPG 并保存，新实例构造时（_load_jxl_output）应恢复为 JPG
w4 = fresh_window()
w4.format_combo.setCurrentText("JPEG (*.jpg)")
w4._save_jxl_output()
w5 = fresh_window()
check("输出格式持久化：新实例恢复为 JPEG (*.jpg)",
      w5.format_combo.currentText() == "JPEG (*.jpg)")

# 选中 JPG 时下拉框右侧应显示限制提示；切回 JXL 时隐藏。
# 注意：输出 tab 默认非当前页，QTabWidget 会隐藏非当前页子控件，
# 故断言前需先激活「输出」tab（真实 GUI 中用户正停留在该页）。
for i in range(w5.tabs.count()):
    if w5.tabs.tabText(i) == "输出":
        w5.tabs.setCurrentIndex(i)
        break
for _ in range(3):
    _app.processEvents()
check("输出格式=JPG 时显示「仅支持无损 JPEG 转码的 JXL 重建 JPG」提示",
      w5.format_hint_label.isVisible() is True)
w5.format_combo.setCurrentText("JPEG XL (*.jxl)")
for _ in range(3):
    _app.processEvents()
check("切回 JXL 时隐藏该提示",
      w5.format_hint_label.isVisible() is False)


print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
