# -*- coding: utf-8 -*-
"""回归测试：下拉弹窗的配色 / 样式在主题与深浅色切换后必须即时刷新。

覆盖三个同源 bug（根因都是「popup 的 palette 与容器样式是一次性快照，切换后无人刷新」）：

1. **竖线变黑**（原始 bug）：控件样式 = 「原生」时，Qt 把 popup 当 top-level
   喂 Inactive palette，native Windows style 把当前行指示条画成黑色。
   修复：把 view 的 palette 钉到 QApplication.palette()（Active 组）。
2. **切深浅色后下拉颜色不生效**：钉住的 palette 是快照，palette 一变就过期。
   修复：_apply_color_scheme / changeEvent 调 MainWindow._refresh_combo_styles()。
3. **切控件样式后下拉样式要重启才更新**：popup 容器（frameless flags + QSS）只在
   showPopup 时被单向设置，切回「原生」不会还原。
   修复：_apply_popup_container_style() 做成可重复运行的状态机（能设也能还原）。
4. **切样式后第一次点击无反应**：showPopup 里每次都 setWindowFlags 会销毁并重建
   窗口，丢掉 combobox 持有的 mouse grab。修复：仅在 flags 真的需要改变时才改，
   且主题切换时提前改好。

测试只验证代码层面的不变量（offscreen 无 native popup、无法验证像素），
视觉差异仍需真机 `run.bat` 验收。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


#: 与「弹窗配色 / 选中行指示条」直接相关的角色。
#: 不比较全部角色：QStyle::polish 会改写 Button / Window 等少数角色
#: （实测 offscreen+Fusion 下 app=#efefef 而 widget=#ffffff），那是正常的
#: style 行为，与本 bug 无关，纳入比较只会制造假失败。
RELEVANT_ROLES = (
    QPalette.Accent,
    QPalette.Highlight,
    QPalette.HighlightedText,
    QPalette.Text,
    QPalette.Base,
)

_SENTINEL = QColor(255, 0, 0)


def _bootstrap():
    QSettings.setDefaultFormat(QSettings.IniFormat)
    tmp = tempfile.mkdtemp(prefix="libjxl_gui_popup_palette_")
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    app = QApplication.instance() or QApplication(["-platform", "offscreen"])
    return app, tmp


def _diff_roles(pal_a, pal_b):
    """Return the list of RELEVANT_ROLES that differ between two palettes."""
    out = []
    for role in RELEVANT_ROLES:
        if pal_a.color(QPalette.ColorGroup.Active, role) != \
                pal_b.color(QPalette.ColorGroup.Active, role):
            out.append(role)
    return out


def _new_combo(theme):
    """Build a NoFlickerComboBox fully applied under ``theme``."""
    from libjxl_gui import main_window as mw
    mw.set_app_theme(theme)
    combo = mw.NoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    combo._apply_fusion_style()
    return combo


# --------------------------------------------------------------------------
# 1. palette 钉住（竖线变黑修复）
# --------------------------------------------------------------------------
def test_view_palette_pinned_under_every_theme():
    """三种控件样式下，view 的相关角色都必须 == app palette 的 Active 组。"""
    _bootstrap()
    from libjxl_gui import main_window as mw
    for theme in mw._THEME_ORDER:
        combo = _new_combo(theme)
        diff = _diff_roles(combo.view().palette(), QApplication.palette())
        assert not diff, (
            f"{theme}: view.palette 未同步到 app.palette，差异角色={diff}"
        )
    print("PASS view_palette_pinned_under_every_theme")


def test_theme_switch_keeps_view_palette_in_sync():
    """主题来回切（native -> noflicker -> native -> fusion）后仍保持同步。"""
    _bootstrap()
    from libjxl_gui import main_window as mw
    mw.set_app_theme("native")
    combo = mw.NoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    for theme in ("native", "native_noflicker", "native", "fusion", "native"):
        mw.set_app_theme(theme)
        combo._apply_fusion_style()
        diff = _diff_roles(combo.view().palette(), QApplication.palette())
        assert not diff, f"切到 {theme} 后 view.palette 失同步，差异角色={diff}"
    print("PASS theme_switch_keeps_view_palette_in_sync")


# --------------------------------------------------------------------------
# 2. 深浅色切换后刷新（bug：切深浅色下拉颜色不生效）
# --------------------------------------------------------------------------
def test_color_scheme_switch_refreshes_combo_palettes():
    """切深浅色必须走 _refresh_combo_styles()，否则弹窗颜色不跟着变。"""
    _bootstrap()
    from libjxl_gui.main_window import MainWindow
    win = MainWindow()
    calls = []
    orig = win._refresh_combo_styles

    def _spy():
        calls.append(1)
        orig()

    win._refresh_combo_styles = _spy
    try:
        win._apply_color_scheme("dark")
        assert calls, "_apply_color_scheme 未调用 _refresh_combo_styles（bug 2 回归）"
        calls.clear()
        win._apply_color_scheme("light")
        assert calls, "_apply_color_scheme(light) 未调用 _refresh_combo_styles"
    finally:
        win._refresh_combo_styles = orig
    win.close()
    print("PASS color_scheme_switch_refreshes_combo_palettes")


def test_palette_change_event_refreshes_combo_palettes():
    """系统深浅色切换（PaletteChange）也必须刷新弹窗配色。"""
    _bootstrap()
    from libjxl_gui.main_window import MainWindow
    win = MainWindow()
    calls = []
    orig = win._refresh_combo_styles

    def _spy():
        calls.append(1)
        orig()

    win._refresh_combo_styles = _spy
    try:
        win.changeEvent(QEvent(QEvent.PaletteChange))
    finally:
        win._refresh_combo_styles = orig
    assert calls, "changeEvent(PaletteChange) 未刷新下拉弹窗（系统切主题回归）"
    win.close()
    print("PASS palette_change_event_refreshes_combo_palettes")


def test_refresh_combo_styles_picks_up_new_app_palette():
    """_refresh_combo_styles 的实际效果：app palette 一变，每个弹窗都要跟上。"""
    _bootstrap()
    from libjxl_gui.main_window import MainWindow, NoFlickerComboBox
    win = MainWindow()
    combos = win.findChildren(NoFlickerComboBox)
    assert len(combos) >= 3, f"设置页/输出页下拉太少，只有 {len(combos)} 个"

    original = QApplication.palette()
    sentinel = QPalette(original)
    sentinel.setColor(QPalette.Highlight, _SENTINEL)
    QApplication.setPalette(sentinel)
    try:
        win._refresh_combo_styles()
        stale = [
            cb for cb in combos
            if cb.view().palette().color(QPalette.Highlight) != _SENTINEL
        ]
        assert not stale, (
            f"{len(stale)} 个下拉的弹窗 palette 未跟上新的 app palette"
        )
    finally:
        QApplication.setPalette(original)
    win.close()
    print("PASS refresh_combo_styles_picks_up_new_app_palette")


# --------------------------------------------------------------------------
# 3. popup 容器样式可还原（bug：切样式后要重启才更新）
# --------------------------------------------------------------------------
def test_popup_container_style_follows_theme_both_ways():
    """容器样式是双向的：noflicker 加 frameless+QSS，切回原生必须还原干净。"""
    _bootstrap()
    from libjxl_gui import main_window as mw

    combo = _new_combo("native_noflicker")
    container = combo._popup_container
    assert container is not None, "popup 容器应在构造后即可解析（不必先打开过）"
    assert container.windowFlags() & Qt.FramelessWindowHint, \
        "noflicker 主题下容器应带 FramelessWindowHint"
    assert container.styleSheet(), "noflicker 主题下容器应有实色背景 QSS"

    mw.set_app_theme("native")
    combo._apply_fusion_style()
    assert not (container.windowFlags() & Qt.FramelessWindowHint), \
        "切回原生后容器仍带 FramelessWindowHint（需重启才恢复的 bug 回归）"
    assert not container.styleSheet(), \
        "切回原生后容器 QSS 未清空（需重启才恢复的 bug 回归）"

    # 再切回去，必须能重新加上
    mw.set_app_theme("native_noflicker")
    combo._apply_fusion_style()
    assert container.windowFlags() & Qt.FramelessWindowHint, \
        "再从原生切回 noflicker 后未重新加上 FramelessWindowHint"
    assert container.styleSheet(), "再次切回 noflicker 后 QSS 未恢复"
    print("PASS popup_container_style_follows_theme_both_ways")


def test_theme_switch_refreshes_container_without_opening_popup():
    """主题切换时，即使从没打开过的下拉也要被刷新（容器构造后即存在）。"""
    _bootstrap()
    from libjxl_gui.main_window import MainWindow, NoFlickerComboBox
    win = MainWindow()
    mw_set = win._apply_theme
    mw_set("native")
    frameless = [
        cb for cb in win.findChildren(NoFlickerComboBox)
        if cb._popup_container is not None
        and cb._popup_container.windowFlags() & Qt.FramelessWindowHint
    ]
    assert not frameless, (
        f"切到原生后仍有 {len(frameless)} 个未打开过的下拉带着 frameless 容器"
    )
    win.close()
    print("PASS theme_switch_refreshes_container_without_opening_popup")


# --------------------------------------------------------------------------
# 4. 幂等：点击时不再重建窗口（bug：切样式后第一次点击无反应）
# --------------------------------------------------------------------------
def test_show_popup_does_not_rebuild_window_when_flags_already_match():
    """flags 已是目标值时，showPopup 不得再调 setWindowFlags（会丢 mouse grab）。

    这就是「切完样式第一次点击没反应」的根因：setWindowFlags 销毁并重建底层
    窗口，combobox 在打开期间持有的 mouse grab 随之丢失。
    """
    _bootstrap()
    from libjxl_gui import main_window as mw

    combo = _new_combo("native")
    # 切到 noflicker：此刻 flags 被一次性改好（提前应用，而不是等到点击）
    mw.set_app_theme("native_noflicker")
    combo._apply_fusion_style()
    container = combo._popup_container
    assert container.windowFlags() & Qt.FramelessWindowHint, \
        "前置条件：切主题时容器 flags 应已改好"

    # 现在模拟用户点击：不应再动 flags
    calls = []
    orig = container.setWindowFlags

    def _spy(flags):
        calls.append(flags)
        return orig(flags)

    container.setWindowFlags = _spy
    try:
        combo.showPopup()
    finally:
        combo.hidePopup()
        container.setWindowFlags = orig
    assert not calls, (
        f"点击弹出时仍调用了 setWindowFlags {len(calls)} 次，"
        "会重建窗口并丢掉 mouse grab（首次点击无反应的 bug 回归）"
    )
    print("PASS show_popup_does_not_rebuild_window_when_flags_already_match")


def test_show_popup_applies_flags_once_when_they_are_stale():
    """反例：flags 确实落后时，showPopup 必须补上（且只改一次）。"""
    _bootstrap()
    from libjxl_gui import main_window as mw

    mw.set_app_theme("native")
    combo = mw.NoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    combo._apply_fusion_style()
    container = combo._popup_container
    assert not (container.windowFlags() & Qt.FramelessWindowHint)

    # 主题变成 noflicker，但没人通知这个 combobox（模拟遗漏刷新的路径）
    mw.set_app_theme("native_noflicker")
    calls = []
    orig = container.setWindowFlags

    def _spy(flags):
        calls.append(flags)
        return orig(flags)

    container.setWindowFlags = _spy
    try:
        combo.showPopup()
    finally:
        combo.hidePopup()
        container.setWindowFlags = orig
    assert len(calls) == 1, (
        f"flags 落后时应恰好补一次 setWindowFlags，实际 {len(calls)} 次"
    )
    assert container.windowFlags() & Qt.FramelessWindowHint
    print("PASS show_popup_applies_flags_once_when_they_are_stale")


if __name__ == "__main__":
    failed = 0
    tests = [
        test_view_palette_pinned_under_every_theme,
        test_theme_switch_keeps_view_palette_in_sync,
        test_color_scheme_switch_refreshes_combo_palettes,
        test_palette_change_event_refreshes_combo_palettes,
        test_refresh_combo_styles_picks_up_new_app_palette,
        test_popup_container_style_follows_theme_both_ways,
        test_theme_switch_refreshes_container_without_opening_popup,
        test_show_popup_does_not_rebuild_window_when_flags_already_match,
        test_show_popup_applies_flags_once_when_they_are_stale,
    ]
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001 - 测试运行器需兜住所有异常
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed, {failed} failed")
    sys.exit(1 if failed else 0)
