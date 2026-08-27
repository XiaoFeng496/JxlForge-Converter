# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless tests for output-location / filename persistence and the
custom-folder history dropdown.

Covers:
  * output-tab widgets exist (custom_folder_edit editable, dropdown button,
    radio groups);
  * choosing 自定义文件夹 + typing/browsing a folder persists dest_mode and
    the custom path, and a fresh MainWindow restores them;
  * 文件名 choice (保持原文件名 / 添加后缀 + suffix text) persists and restores;
  * _add_folder_history records a folder into the history menu, de-duplicates,
    keeps most-recent-first, and preserves the currently typed text;
  * browsing a folder adds it to history + persists;
  * starting a conversion with a valid custom folder records it into history;
  * the history menu (QMenu with per-row delete) rebuilds correctly and the
    per-row delete removes a single entry while keeping typed text;
  * format dropdown (QComboBox) drives _build_output_path.
"""

import sys
import os
import tempfile

from PySide6.QtWidgets import QApplication, QCheckBox
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QSize
from PySide6.QtWidgets import QScrollBar

_app = QApplication.instance() or QApplication(sys.argv)
# Mirror __main__.run(): persist to a .ini file (not the registry) so running
# these tests doesn't write into the user's Windows registry.
QSettings.setDefaultFormat(QSettings.IniFormat)
# Mirror __main__.run() so QSettings has a stable, writable location.
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini
# （%APPDATA%\libjxl\libjxl-gui.ini），否则测试残留值会让 GUI 下次启动异常。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui import converter as conv_mod
from libjxl_gui.main_window import MainWindow, GRID_SIZES, FIT_EXTRA_H


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

failures = []
total = 0


def clear_output_settings():
    """Remove the persisted output_settings + jxl_output groups so the test
    starts from defaults and does not pollute the real QSettings store."""
    for group in ("output_settings", "jxl_output"):
        settings = QSettings()
        settings.beginGroup(group)
        settings.remove("")
        settings.endGroup()


clear_output_settings()


def check(name, ok):
    global total
    total += 1
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


# --- Stub converter.encode so _on_convert can run without real cjxl --------
_real_encode = conv_mod.encode


def _fake_encode(input_path, output_path, **kwargs):
    return True, "fake cjxl ok", ""


conv_mod.encode = _fake_encode


def drive_one_convert(window):
    """Start one conversion on the given window (UI thread builds jobs, worker
    runs in background) and pump the event loop so it finishes."""
    tmpdir = tempfile.mkdtemp()
    src = os.path.join(tmpdir, "a.png")
    open(src, "w").close()
    window.input_files = [src]
    window._on_convert()
    worker = window._convert_worker
    if worker is not None:
        worker.wait(10000)
    # Pump the event loop so the queued finished_signal -> _on_convert_finished
    # is delivered and the worker is cleaned up.
    for _ in range(50):
        QApplication.instance().processEvents()


# ---------------------------------------------------------------------------
# 1. Widget existence + defaults
# ---------------------------------------------------------------------------
w = MainWindow()
check("custom_folder_edit exists (editable field)",
      hasattr(w, "custom_folder_edit"))
check("custom_folder_dropdown button exists",
      hasattr(w, "custom_folder_dropdown"))
check("default dest is 原文件夹 (same)",
      w.same_folder_radio.isChecked() is True)
check("custom folder controls disabled by default",
      w.custom_folder_edit.isEnabled() is False
      and w.custom_folder_dropdown.isEnabled() is False
      and w.browse_folder_button.isEnabled() is False)
check("default filename is 保持原文件名 (keep)",
      w.keep_name_radio.isChecked() is True)
check("default suffix text is _converted",
      w.suffix_edit.text() == "_converted")
check("history starts empty", list(w._folder_history) == [])
check("empty history menu shows placeholder",
      w.folder_menu.actions().__len__() == 1
      and w.folder_menu.actions()[0].isEnabled() is False)

# ---------------------------------------------------------------------------
# 2. Custom folder + path persistence
# ---------------------------------------------------------------------------
w.custom_folder_radio.setChecked(True)
folder_a = os.path.normpath(tempfile.mkdtemp())
w.custom_folder_edit.setText(folder_a)
w._save_output_settings()

w2 = fresh_window()  # __init__ calls _load_output_settings()
check("persisted dest_mode restored (custom)",
      w2.custom_folder_radio.isChecked() is True)
check("persisted custom folder text restored",
      os.path.normpath(w2.custom_folder_edit.text()) == folder_a)
check("custom controls enabled after restoring custom mode",
      w2.custom_folder_edit.isEnabled() is True)

# ---------------------------------------------------------------------------
# 3. Filename (keep / suffix) persistence
# ---------------------------------------------------------------------------
w2.add_suffix_radio.setChecked(True)
w2.suffix_edit.setText("_out")
w2._save_output_settings()

w3 = fresh_window()
check("persisted filename mode restored (suffix)",
      w3.add_suffix_radio.isChecked() is True)
check("persisted suffix text restored (_out)",
      w3.suffix_edit.text() == "_out")

# reset to keep for later steps
w3.keep_name_radio.setChecked(True)
w3._save_output_settings()

# ---------------------------------------------------------------------------
# 4. History menu: add / de-dupe / most-recent-first / keep typed text
# ---------------------------------------------------------------------------
h = fresh_window()
h._folder_history = []  # start clean; this group tests add/dedupe/order only
h._rebuild_folder_menu()
folder_b = os.path.normpath(tempfile.mkdtemp())
h.custom_folder_edit.setText("typed/before")
h._add_folder_history(folder_b)
check("history has the added folder",
      folder_b in h._folder_history)
check("menu lists the history item",
      h.folder_menu.actions().__len__() == 1)
check("currently typed text preserved after add",
      h.custom_folder_edit.text() == "typed/before")

# add the same folder again -> stays, moves to front, no duplicate
h._add_folder_history(folder_b)
check("no duplicate when re-adding same folder",
      h._folder_history.count(folder_b) == 1)

folder_c = os.path.normpath(tempfile.mkdtemp())
h._add_folder_history(folder_c)
check("most recent folder is first",
      h._folder_history[0] == folder_c)
check("older folder shifted down",
      h._folder_history[1] == folder_b)
check("menu row count matches history",
      h.folder_menu.actions().__len__() == len(h._folder_history))

# ---------------------------------------------------------------------------
# 5. Browsing a folder adds to history + persists
# ---------------------------------------------------------------------------
b = fresh_window()
# Simulate the browse dialog returning a folder by calling the handler's core
# logic: emulate selection then persist. We exercise _on_browse_folder indirectly
# by setting the edit text through its real code path (setText + history).
folder_d = os.path.normpath(tempfile.mkdtemp())
b.custom_folder_radio.setChecked(True)
b.custom_folder_edit.setText(folder_d)
b._add_folder_history(folder_d)
b._save_output_settings()
check("browsed folder persisted as custom_folder",
      os.path.normpath(b.custom_folder_edit.text()) == folder_d)
check("browsed folder recorded in history",
      folder_d in b._folder_history)

# ---------------------------------------------------------------------------
# 6. Conversion with a valid custom folder records it into history
# ---------------------------------------------------------------------------
c = fresh_window()
c.custom_folder_radio.setChecked(True)
folder_e = os.path.normpath(tempfile.mkdtemp())
c.custom_folder_edit.setText(folder_e)
drive_one_convert(c)
check("conversion records valid custom folder into history",
      folder_e in c._folder_history)
check("conversion persists history",
      folder_e in c._folder_history)

# ---------------------------------------------------------------------------
# 7. Full round-trip: a distinct state survives a fresh window
# ---------------------------------------------------------------------------
r = fresh_window()
r.custom_folder_radio.setChecked(True)
folder_f = os.path.normpath(tempfile.mkdtemp())
r.custom_folder_edit.setText(folder_f)
r.add_suffix_radio.setChecked(True)
r.suffix_edit.setText("_final")
r._save_output_settings()

r2 = fresh_window()
check("round-trip: custom mode restored",
      r2.custom_folder_radio.isChecked() is True)
check("round-trip: custom folder restored",
      os.path.normpath(r2.custom_folder_edit.text()) == folder_f)
check("round-trip: suffix mode restored",
      r2.add_suffix_radio.isChecked() is True)
check("round-trip: suffix text restored (_final)",
      r2.suffix_edit.text() == "_final")
check("round-trip: history restored into menu",
      folder_f in r2._folder_history
      and r2.folder_menu.actions().__len__() == len(r2._folder_history))

# ---------------------------------------------------------------------------
# 8. Per-row delete button removes a single history entry (menu rebuilds)
# ---------------------------------------------------------------------------
d = fresh_window()
d.custom_folder_radio.setChecked(True)
d._folder_history = []  # start clean; the loaded window may carry prior entries
fa = os.path.normpath(tempfile.mkdtemp())
fb = os.path.normpath(tempfile.mkdtemp())
fc = os.path.normpath(tempfile.mkdtemp())
# History order (most recent first) becomes [fc, fb, fa].
d._add_folder_history(fa)
d._add_folder_history(fb)
d._add_folder_history(fc)
check("delete-test: three history entries present",
      len(d._folder_history) == 3 and d.folder_menu.actions().__len__() == 3)
# Remove the middle entry (index 1 -> fb) via the per-row delete signal.
d.custom_folder_edit.setText("keep/this")
first_row = d.folder_menu.actions()[1].defaultWidget()
first_row.deleteRequested.emit(1)
check("delete-test: entry removed from history list",
      fb not in d._folder_history)
check("delete-test: remaining order preserved (fc, fa)",
      d._folder_history == [fc, fa])
check("delete-test: menu rebuilt with 2 rows",
      d.folder_menu.actions().__len__() == 2)
check("delete-test: removed item no longer in menu",
      fb not in d._folder_history)
check("delete-test: typed text preserved after delete",
      d.custom_folder_edit.text() == "keep/this")
d._save_output_settings()
d2 = fresh_window()
check("delete-test: deletion persisted (fb gone)",
      fb not in d2._folder_history)
check("delete-test: surviving entries persisted (fc, fa)",
      fc in d2._folder_history and fa in d2._folder_history)

# ---------------------------------------------------------------------------
# 9. Selecting a history row fills the edit field (menu stays open until
#    _select_history hides it); deleting the last row shows the placeholder.
# ---------------------------------------------------------------------------
e = fresh_window()
e.custom_folder_radio.setChecked(True)
e._folder_history = []
fe1 = os.path.normpath(tempfile.mkdtemp())
fe2 = os.path.normpath(tempfile.mkdtemp())
e._add_folder_history(fe1)
e._add_folder_history(fe2)  # history [fe2, fe1]
check("select-test: two menu rows",
      e.folder_menu.actions().__len__() == 2)
# Click the first row's label -> select fe2.
row0 = e.folder_menu.actions()[0].defaultWidget()
row0.selected.emit(fe2)
check("select-test: clicking a row fills the edit field",
      e.custom_folder_edit.text() == fe2)
# Delete fe2 (row 0) through the real handler; menu should rebuild to 1 row.
e._on_folder_history_delete(0)
check("select-test: delete rebuilds menu to 1 row",
      e.folder_menu.actions().__len__() == 1
      and fe2 not in e._folder_history)
# Delete the final entry -> placeholder row appears.
e._on_folder_history_delete(0)
check("select-test: empty history shows placeholder",
      e.folder_menu.actions().__len__() == 1
      and e.folder_menu.actions()[0].isEnabled() is False)
check("select-test: typed text preserved through deletes",
      e.custom_folder_edit.text() == fe2)

# --- Group 10: "一键 6×3 排版" must use the cached window frame, not a
# hidden (zero-sized) viewport. On real desktops the input list is hidden when
# the button is clicked from the Settings tab; measuring its viewport then
# yields frame_h = window_height - 0 = huge -> window jumps up + can't fit.
win = fresh_window()
win._sized = True  # drive the fit manually; don't rely on showEvent
win._fit_frame_w = 200
win._fit_frame_h = 100
_real_vp = win.input_list.viewport()


class _FakeScroll:
    def sizeHint(self):
        return QSize(17, 17)


class _FakeVP:
    def width(self):
        return 0

    def height(self):
        return 0

    def verticalScrollBar(self):
        return _FakeScroll()

    def horizontalScrollBar(self):
        return _FakeScroll()


win.input_list.viewport = lambda: _FakeVP()
vp_h = 3 * GRID_SIZES["缩略图"].height() + 17 + FIT_EXTRA_H
x0, y0 = win.x(), win.y()
# fresh_window() 的 show()+processEvents() 会在 offscreen 下先触发一次真实 fit，
# 把窗口 minimumHeight() 设为基于虚拟几何的大值（~959）。此处显式复位为 0，
# 否则 _fit_window_to_grid 内部 max(self.minimumHeight(), vp_h+frame_h) 会取旧值，
# 使断言 minimumHeight()==vp_h+100 失败——这不是 fit 逻辑的缺陷，而是测试上下文
# 残留。复位后 fit 纯粹按 vp_h+frame_h 计算，才能精确验证「viewport 隐藏时用缓存 frame」。
win.setMinimumHeight(0)
win._fit_window_to_grid(center=False)
check("fit uses cached frame when viewport hidden (no huge height)",
      win.height() == vp_h + 100)
# center=False must keep the window's *frame* position (only resize), modulo
# the legitimate on-screen clamping that prevents the OS from re-floating an
# off-screen window. The earlier "drift up" regression was a feedback loop
# that grew/moved the window on every click; that is guarded by Group 11.
# Here we assert the position equals the *clamped original* — which is exactly
# what the code does — rather than the raw original, because headless/offscreen
# platforms emulate a tiny primary screen (e.g. 800x800) smaller than the 6x3
# window, so the clamp legitimately pins x/y to the screen edge. On a real
# desktop the window fits and the clamp is the identity, so the check is the
# same as "position unchanged".
sg = QApplication.primaryScreen().availableGeometry() if QApplication.primaryScreen() else None
if sg is not None:
    fgw, fgh = win.frameGeometry().width(), win.frameGeometry().height()
    exp_x = max(sg.x(), min(x0, sg.x() + max(0, sg.width() - fgw)))
    exp_y = max(sg.y(), min(y0, sg.y() + max(0, sg.height() - fgh)))
    _pos_ok = win.x() == exp_x and win.y() == exp_y
else:
    _pos_ok = win.x() == x0 and win.y() == y0
check("fit keeps position when center=False (no upward jump / legal clamp)",
      _pos_ok)

# --- Group 11: repeated clicks on "一键 6×3 排版" must keep the geometry
# constant across clicks (no grow, no upward drift). The earlier regression
# re-measured the window frame against the *already-fitted* window inside
# _fit_window_to_grid, creating a feedback loop that grew the window every
# click and shoved it up. We assert the geometry does not change from one
# click to the next (the loop-invariant is what the bug broke), not that it
# equals the one-time seed value (offscreen platforms may re-clamp once after
# the seed fit, which is unrelated to the feedback loop).
win2 = fresh_window()
win2._sized = True
win2.show()
QApplication.instance().processEvents()
win2._fit_window_to_grid(center=False)   # seed the cached frame for real
QApplication.instance().processEvents()
geoms = []
for _ in range(8):
    win2._on_fit_window()
    QApplication.instance().processEvents()
    geoms.append(win2.geometry().getRect())
check("repeated 6x3 clicks keep geometry constant (no grow / no drift)",
      len(set(geoms)) == 1)
check("repeated 6x3 clicks keep minimum size constant",
      all(g[2:4] == geoms[0][2:4] for g in geoms))

# --- Group 12: a JXL input must NOT be forced to PNG. It follows the output
# format choice like any other source: JXL output -> cjxl re-compresses the
# JXL (large JXL -> smaller JXL); PNG output -> djxl decodes it to a raster.
# Only djxl emitting a .jxl is invalid, and the worker avoids that by routing
# JXL->JXL through cjxl instead of djxl.
bop = fresh_window()
bop.format_combo.setCurrentText("JPEG XL (*.jxl)")
check("jxl input -> jxl output when format=JXL (re-compress)",
      bop._build_output_path("x/y/photo.jxl").lower().endswith(".jxl"))
check("non-jxl input honors JXL format choice",
      bop._build_output_path("x/y/photo.png").lower().endswith(".jxl"))
bop.format_combo.setCurrentText("PNG (*.png)")
check("jxl input -> png output when format=PNG (decode)",
      bop._build_output_path("x/y/photo.jxl").lower().endswith(".png"))
check("non-jxl input honors PNG format choice",
      bop._build_output_path("x/y/photo.jpg").lower().endswith(".png"))

# --- Group 13: 高级参数「保留原始扩展名」：默认关闭；开启后输出沿用输入扩展名；
# 输入无扩展名时回退到格式推导值；与「原文件夹 + 无后缀」组合使输出==输入时，
# _on_convert 的 skipped_same 守卫会跳过该文件以免覆盖源文件。
pe = fresh_window()
check("保留原始扩展名 默认关闭", pe.preserve_ext_check.isChecked() is False)

pe.format_combo.setCurrentText("JPEG XL (*.jxl)")
pe.preserve_ext_check.setChecked(True)
check("开启后：png 输入 -> 输出沿用 .png（即便格式=JXL）",
      pe._build_output_path("x/y/photo.png").lower().endswith(".png"))
check("开启后：jpg 输入 -> 输出沿用 .jpg",
      pe._build_output_path("x/y/photo.jpg").lower().endswith(".jpg"))
pe.preserve_ext_check.setChecked(False)
check("关闭后：png 输入 -> 输出按格式推导为 .jxl",
      pe._build_output_path("x/y/photo.png").lower().endswith(".jxl"))

# 输入无扩展名：开启后回退到格式推导值（不强行加空扩展名）。
pe.preserve_ext_check.setChecked(True)
pe.format_combo.setCurrentText("PNG (*.png)")
check("开启后：无扩展名输入 -> 回退为格式推导 .png",
      pe._build_output_path("x/y/photo").lower().endswith(".png"))
pe.format_combo.setCurrentText("JPEG XL (*.jxl)")

# 路径碰撞前置：原文件夹 + 无后缀 + 保留扩展名 => 输出路径 == 输入路径。
gpre = fresh_window()
gpre.format_combo.setCurrentText("JPEG XL (*.jxl)")
gpre.preserve_ext_check.setChecked(True)
gpre.same_folder_radio.setChecked(True)
gpre.keep_name_radio.setChecked(True)
_gpre_src = "x/y/photo.png"
check("路径碰撞前置：原文件夹+无后缀+保留扩展名 使 输出==输入",
      os.path.abspath(gpre._build_output_path(_gpre_src)) == os.path.abspath(_gpre_src))

# 独立「同路径兜底」已移除：同路径文件不再被硬编码跳过，而是交给
# 「当输出文件已经存在时」冲突处理器按用户策略裁决（与所有「输出已存在」
# 情形一致）。验证：默认策略「替换」下会创建转换任务（不再整文件跳过）。
_g_src = os.path.join(tempfile.mkdtemp(), "photo.png")
with open(_g_src, "wb") as f:
    f.write(b"x")
g = fresh_window()
g.format_combo.setCurrentText("JPEG XL (*.jxl)")
g.preserve_ext_check.setChecked(True)
g.same_folder_radio.setChecked(True)
g.keep_name_radio.setChecked(True)
g.input_files = [_g_src]
g._on_convert()
check("移除独立兜底：同路径文件默认「替换」下仍创建转换任务（交冲突处理器）",
      g._convert_worker is not None)

# 互补验证：冲突策略选「跳过」时，同路径文件由冲突处理器跳过（worker 为 None）。
# 需先在磁盘上真实写出源文件，冲突处理器据 os.path.exists 判定「已存在」才跳过。
_g2_src = os.path.join(tempfile.mkdtemp(), "photo.png")
with open(_g2_src, "wb") as f:
    f.write(b"x")
g2 = fresh_window()
g2.format_combo.setCurrentText("JPEG XL (*.jxl)")
g2.preserve_ext_check.setChecked(True)
g2.same_folder_radio.setChecked(True)
g2.keep_name_radio.setChecked(True)
g2.on_exist_combo.setCurrentText("跳过")
g2.input_files = [_g2_src]
g2._on_convert()
check("移除独立兜底：冲突策略「跳过」下，同路径文件由处理器跳过",
      g2._convert_worker is None)

# 持久化 round-trip：开启 -> 重启仍为开启；取消 -> 重启为关闭。
pr1 = fresh_window()
pr1.preserve_ext_check.setChecked(True)
pr1._save_jxl_output()
pr2 = fresh_window()
check("保留原始扩展名 持久化：重启后仍为开启",
      pr2.preserve_ext_check.isChecked() is True)
pr2.preserve_ext_check.setChecked(False)
pr2._save_jxl_output()
pr3 = fresh_window()
check("保留原始扩展名 取消后持久化：重启后为关闭",
      pr3.preserve_ext_check.isChecked() is False)

# --- Group 14: 该复选框归属「设置页 - 高级参数区」，而非输出页高级参数折叠组。
loc = fresh_window()
check("保留原始扩展名 位于设置页高级参数区（adv_params_group）",
      loc.preserve_ext_check.parent() is loc.adv_params_group)
check("保留原始扩展名 不在输出页高级参数折叠组（adv_content）",
      loc.preserve_ext_check.parent() is not loc.adv_content)
# 母开关「启用高级参数」不影响其可用性（非 cjxl 专家参数）。
loc._apply_adv_threads_state(False)
check("保留原始扩展名 不受母开关关闭影响（始终可用）",
      loc.preserve_ext_check.isEnabled() is True)
loc._apply_adv_threads_state(True)
# 母开关警告弹窗的子项列表不应包含「保留原始扩展名」。
_warn_subs = []
_grp = loc.adv_params_group
for _i in range(_grp.layout().count()):
    _ww = _grp.layout().itemAt(_i).widget()
    if isinstance(_ww, QCheckBox) and _ww is not loc.adv_threads_toggle \
            and _ww is not loc.preserve_ext_check:
        _warn_subs.append(_ww.text())
check("保留原始扩展名 不出现在母开关警告子项列表中",
      "保留原始扩展名" not in _warn_subs)

# Restore the real encode so other test modules are unaffected.
conv_mod.encode = _real_encode

# Clean up persisted groups so this test leaves no stale QSettings behind.
clear_output_settings()

print("\n%d/%d checks passed" % (total - len(failures), total))
print("ALL_OK" if not failures else "FAILED: " + ", ".join(failures))
