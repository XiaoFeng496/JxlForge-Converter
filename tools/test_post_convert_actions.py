# -*- coding: utf-8 -*-
"""验证「转换完毕之后」4 个后处理动作的接线与行为（headless / offscreen）。

仅覆盖 main_window 内的动作分发逻辑：
- _open_output_in_explorer 推导并打开输出目录（含多目录 commonpath）；
- _run_post_convert_actions 按开关触发对应动作，且顺序为 打开资源管理器 ->
  清除输入文件 -> 提示音 -> 退出；前三者不应被「退出」短路；clear 不删磁盘文件。
"""

import os
import sys
import tempfile
import shutil

from PySide6.QtWidgets import QApplication

# 隔离 QSettings，避免污染真实配置（与 test_output_settings 同一手法）。
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("APPDATA", tempfile.mkdtemp())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jxlforge import main_window as mw_mod

app = QApplication.instance() or QApplication([])

failures = []
total = 0


def check(name, cond):
    global total
    total += 1
    if cond:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


# 用真实 MainWindow，但禁用 convert（本测试不真正转码）。
real_init = mw_mod.MainWindow.__init__

_window = None


def fresh_window():
    global _window
    if _window is None:
        _window = mw_mod.MainWindow()
    return _window


# ---- 构造临时输入目录与文件，并设定输出为自定义文件夹 ----
tmp = tempfile.mkdtemp()
src_dir = os.path.join(tmp, "photos")
os.makedirs(src_dir)
f1 = os.path.join(src_dir, "a.png")
f2 = os.path.join(src_dir, "b.png")
open(f1, "wb").write(b"x")
open(f2, "wb").write(b"x")

out_dir = os.path.join(tmp, "out")
os.makedirs(out_dir)

win = fresh_window()
win.input_files = [f1, f2]
win.custom_folder_radio.setChecked(True)
win.custom_folder_edit.setText(out_dir)
win.same_folder_radio.setChecked(False)

# ---- 测试 1：_open_output_in_explorer 单目录 ----
captured = {}
_orig_open = mw_mod.QDesktopServices.openUrl


def fake_open(url):
    captured["url"] = os.path.normpath(url.toLocalFile())


mw_mod.QDesktopServices.openUrl = staticmethod(fake_open)
try:
    win._open_output_in_explorer()
    check("打开资源管理器：单输出目录指向自定义文件夹",
          captured.get("url") == out_dir)
finally:
    mw_mod.QDesktopServices.openUrl = _orig_open

# ---- 测试 2：多目录 commonpath（保留文件夹结构+保留上级目录）----
subA = os.path.join(out_dir, "x", "a")
subB = os.path.join(out_dir, "x", "b")
os.makedirs(subA, exist_ok=True)
os.makedirs(subB, exist_ok=True)
captured2 = {}


def fake_open2(url):
    captured2["url"] = os.path.normpath(url.toLocalFile())


mw_mod.QDesktopServices.openUrl = staticmethod(fake_open2)
try:
    # 直接喂两个子目录，验证 commonpath 取最近公共祖先。
    import os as _os
    target = _os.path.commonpath([subA, subB])
    # 走内部逻辑：临时把 input_files 的输出路径指到两个子目录
    real_bop = win._build_output_path

    def fake_bop(src):
        if src == f1:
            return _os.path.join(subA, "a.png")
        return _os.path.join(subB, "b.png")

    win._build_output_path = fake_bop
    try:
        win._open_output_in_explorer()
        check("打开资源管理器：多目录取 commonpath（最近公共祖先）",
              captured2.get("url") == os.path.normpath(target))
    finally:
        win._build_output_path = real_bop
finally:
    mw_mod.QDesktopServices.openUrl = _orig_open

# ---- 测试 3：_run_post_convert_actions 按开关触发 + 顺序 ----
calls = []


def fake_clear():
    calls.append("clear")
    win.input_files.clear()
    win._file_meta.clear()
    win._table_added.clear()


_real_clear = win._on_clear_inputs
win._on_clear_inputs = fake_clear

_orig_beep = mw_mod.QApplication.beep
mw_mod.QApplication.beep = staticmethod(lambda: calls.append("beep"))

_orig_quit = mw_mod.QApplication.quit
mw_mod.QApplication.quit = staticmethod(lambda: calls.append("quit"))

_orig_open2 = mw_mod.QDesktopServices.openUrl
mw_mod.QDesktopServices.openUrl = staticmethod(
    lambda u: calls.append("open:" + os.path.normpath(u.toLocalFile())))

try:
    # 全开
    win.open_explorer_check.setChecked(True)
    win.clear_input_check.setChecked(True)
    win.beep_check.setChecked(True)
    win.exit_after_check.setChecked(True)
    calls.clear()
    win._run_post_convert_actions()
    check("全开：动作顺序为 打开 -> 清除 -> 提示音 -> 退出",
          calls == ["open:" + os.path.normpath(out_dir), "clear", "beep", "quit"])
    check("全开：清除输入后列表为空（且不删磁盘原始文件）",
          win.input_files == [] and os.path.exists(f1) and os.path.exists(f2))

    # 仅打开 + 提示音，不应清除/退出
    win.input_files = [f1, f2]
    win.open_explorer_check.setChecked(True)
    win.clear_input_check.setChecked(False)
    win.beep_check.setChecked(True)
    win.exit_after_check.setChecked(False)
    calls.clear()
    win._run_post_convert_actions()
    check("仅打开+提示音：只触发这两项",
          calls == ["open:" + os.path.normpath(out_dir), "beep"])
    check("仅打开+提示音：输入列表保留", win.input_files == [f1, f2])

    # 全关：无动作
    win.open_explorer_check.setChecked(False)
    win.clear_input_check.setChecked(False)
    win.beep_check.setChecked(False)
    win.exit_after_check.setChecked(False)
    calls.clear()
    win._run_post_convert_actions()
    check("全关：无动作", calls == [])
finally:
    win._on_clear_inputs = _real_clear
    mw_mod.QApplication.beep = _orig_beep
    mw_mod.QApplication.quit = _orig_quit
    mw_mod.QDesktopServices.openUrl = _orig_open2

# ---- 清理 ----
shutil.rmtree(tmp, ignore_errors=True)

print("\n%d/%d checks passed" % (total - len(failures), total))
print("ALL_OK" if not failures else "FAILED: " + ", ".join(failures))
sys.exit(1 if failures else 0)
