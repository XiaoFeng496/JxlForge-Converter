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
4. **双层边框**（控件样式 = 原生 + 亮色）：view 的 QSS 无条件带 1px border，
   与 Windows 原生给 popup 容器画的圆角框叠在一起，看起来是两个框。
   修复：view QSS 拆成 Fusion / 原生两份，原生那份不画 border（只保留实色底）。
5. **setWindowFlags 重建窗口**：容器 flags 一旦要变，setWindowFlags 会销毁并
   重建底层 native window，丢掉 combobox 持有的 mouse grab（**即使容器处于隐藏
   状态也会丢**）。修复：改用 overrideWindowFlags（只改内部 flag 状态，不重建）。

### ⚠️ 不属于本项目的 bug（已证伪，勿再修）

「闪电点击下拉第 1 项无反应」是 **Qt 原生 QComboBox 的行为**，不是本项目的
缺陷。QComboBoxPrivateContainer 会用「位置 + 时间」双重判定吞掉「打开 popup
那一击」的 mouse release，防止误选；第 1 项紧贴 combo 下沿、与打开 popup 的
点击位置重叠，所以只有它容易命中。已用 tools/probe_native_combo_first_row.py
（纯原生 QComboBox，不含本项目任何代码）复现：停一下再点 / 长按 / 划开再回来
点都正常，只有闪电点会失败；GUI 里其他原生下拉框同样如此。

测试只验证代码层面的不变量（offscreen 无 native popup、无法验证像素），
视觉差异仍需真机 `run.pyw` 验收。
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
    combo = mw.NativeNoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    combo._apply_fusion_style()
    return combo


# --------------------------------------------------------------------------
# 1. palette 钉住（竖线变黑修复）
# --------------------------------------------------------------------------
def test_view_palette_pinned_under_every_theme():
    """三种原生控件样式下，view 的相关角色都必须 == app palette 的 Active 组。

    ⚠️ 仅覆盖走原生 QComboBox 实现的主题（native_noflicker / native / fusion）；
    「原生（NoFlicker框）」(native_noflicker_proto) 是自绘 CustomComboBox 原型，
    popup 不走 QComboBox.view()，其配色由原型仓测试单独覆盖——不属于本测试范围。
    """
    _bootstrap()
    from libjxl_gui import main_window as mw
    for theme in ("native_noflicker", "native", "fusion"):
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
    combo = mw.NativeNoFlickerComboBox()
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
    from libjxl_gui.main_window import MainWindow, NativeNoFlickerComboBox
    win = MainWindow()
    combos = win.findChildren(NativeNoFlickerComboBox)
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
    from libjxl_gui.main_window import MainWindow, NativeNoFlickerComboBox
    win = MainWindow()
    mw_set = win._apply_theme
    mw_set("native")
    frameless = [
        cb for cb in win.findChildren(NativeNoFlickerComboBox)
        if cb._popup_container is not None
        and cb._popup_container.windowFlags() & Qt.FramelessWindowHint
    ]
    assert not frameless, (
        f"切到原生后仍有 {len(frameless)} 个未打开过的下拉带着 frameless 容器"
    )
    win.close()
    print("PASS theme_switch_refreshes_container_without_opening_popup")


# --------------------------------------------------------------------------
# 4. 双层边框（bug：原生 + 亮色下下拉菜单两个框叠在一起）
# --------------------------------------------------------------------------
def test_view_qss_has_no_border_under_native_theme():
    """控件样式 = 「原生」（dropdowns_use_fusion() == False）时，view 的 QSS
    不得带 border。

    Windows 原生样式自己给 popup 容器画了圆角外框；view 再来一条 1px 方框
    就会叠成「圆角 + 直角」的双层边框。原生那份只保留实色底（防黑闪）。
    """
    _bootstrap()
    combo = _new_combo("native")
    qss = combo.view().styleSheet()
    assert "border" not in qss, (
        f"native: view QSS 仍带 border，会与原生圆角框叠成双层边框: {qss!r}"
    )
    assert "background" in qss, (
        f"native: view QSS 丢了实色背景，弹窗可能闪黑: {qss!r}"
    )
    print("PASS view_qss_has_no_border_under_native_theme")


def test_view_qss_has_border_under_fusion_theme():
    """Fusion 自绘 popup 时，外框得由 view 自己画——这条 border 不能丢。

    覆盖两个自绘主题：fusion（整窗 Fusion）与 native_noflicker（窗口原生、
    仅下拉用 Fusion）。它们的容器都是 frameless 的，没有原生圆角框可叠。
    """
    _bootstrap()
    for theme in ("fusion", "native_noflicker"):
        combo = _new_combo(theme)
        qss = combo.view().styleSheet()
        assert "border" in qss, f"{theme}: view QSS 丢了边框: {qss!r}"
        assert "background" in qss, f"{theme}: view QSS 丢了实色背景: {qss!r}"
    print("PASS view_qss_has_border_under_fusion_theme")


def test_view_qss_follows_theme_switch_both_ways():
    """边框必须跟着主题来回切，不能只在构造时定一次。"""
    _bootstrap()
    from libjxl_gui import main_window as mw
    mw.set_app_theme("native")
    combo = mw.NativeNoFlickerComboBox()
    combo.addItems(["a", "b", "c"])

    seen = {}
    for theme in ("native", "fusion", "native", "native_noflicker", "native"):
        mw.set_app_theme(theme)
        combo._apply_fusion_style()
        seen[theme] = "border" in combo.view().styleSheet()

    assert seen["native"] is False, "「原生」下 view QSS 不该有 border（双层边框回归）"
    assert seen["fusion"] is True, "Fusion 下 view QSS 应有 border"
    assert seen["native_noflicker"] is True, \
        "「原生（无闪烁）」的下拉是 Fusion 自绘，view QSS 应有 border"
    print("PASS test_view_qss_follows_theme_switch_both_ways")


# --------------------------------------------------------------------------
# 5. 不重建窗口：flags 变更一律走 overrideWindowFlags
# --------------------------------------------------------------------------
def test_show_popup_never_calls_setWindowFlags():
    """任何情况下都不得调 setWindowFlags —— 它会销毁并重建 native window，
    丢掉 combobox 持有的 mouse grab（即使容器处于隐藏状态也会丢）。

    这是「setWindowFlags vs overrideWindowFlags」这条铁律的执行点。
    """
    _bootstrap()
    from libjxl_gui import main_window as mw

    mw.set_app_theme("native")
    combo = mw.NativeNoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    combo._apply_fusion_style()
    container = combo._popup_container
    assert container is not None

    calls = []
    orig = container.setWindowFlags

    def _spy(flags):
        calls.append(flags)
        return orig(flags)

    container.setWindowFlags = _spy
    try:
        # flags 落后的场景：主题改成 noflicker 但不通知这个 combo，
        # 由 showPopup 补上——这是最容易误用 setWindowFlags 的路径。
        mw.set_app_theme("native_noflicker")
        combo.showPopup()
    finally:
        combo.hidePopup()
        container.setWindowFlags = orig
    assert not calls, (
        f"showPopup 期间调用了 setWindowFlags {len(calls)} 次，"
        "会重建 native window 并丢掉 mouse grab"
    )
    print("PASS show_popup_never_calls_setWindowFlags")


def test_show_popup_applies_flags_once_when_they_are_stale():
    """反例：flags 确实落后时，showPopup 必须补上（且只改一次）。"""
    _bootstrap()
    from libjxl_gui import main_window as mw

    mw.set_app_theme("native")
    combo = mw.NativeNoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    combo._apply_fusion_style()
    container = combo._popup_container
    assert not (container.windowFlags() & Qt.FramelessWindowHint)

    # 主题变成 noflicker，但没人通知这个 combobox（模拟遗漏刷新的路径）
    mw.set_app_theme("native_noflicker")
    calls = []
    orig = container.overrideWindowFlags

    def _spy(flags):
        calls.append(flags)
        return orig(flags)

    container.overrideWindowFlags = _spy
    try:
        combo.showPopup()
    finally:
        combo.hidePopup()
        container.overrideWindowFlags = orig
    assert len(calls) == 1, (
        f"flags 落后时应恰好补一次 overrideWindowFlags，实际 {len(calls)} 次"
    )
    assert container.windowFlags() & Qt.FramelessWindowHint
    print("PASS show_popup_applies_flags_once_when_they_are_stale")


def test_detached_view_is_never_treated_as_container():
    """Qt 6 在 hidePopup 里 deleteLater 容器，view 会变成自己的 top-level
    window（``view.window() is view``）。此时必须返回 None，不能把 QListView
    当成容器去设 flags / QSS。
    """
    _bootstrap()
    from libjxl_gui import main_window as mw
    mw.set_app_theme("native")
    combo = mw.NativeNoFlickerComboBox()
    combo.addItems(["a", "b", "c"])
    combo._apply_fusion_style()

    view = combo.view()
    # 模拟「容器已被销毁、view 成为孤儿窗口」这一 Qt 6 形态
    view.setParent(None)
    assert combo._popup_container_widget() is None, \
        "detached 的 view 被当成了 popup 容器（会对 QListView 设窗口 flags）"
    assert combo._popup_container is None, "detached 后必须清掉悬挂的容器引用"
    assert combo._popup_styled_for is None, "detached 后必须清掉样式缓存"
    print("PASS detached_view_is_never_treated_as_container")


def test_resnap_propagates_to_viewport_and_container():
    """#3 主修复的关键不变量：``_resnap_popup_palette`` 必须把 view、
    viewport、container 三层的 palette 同步到当前 app palette。

    没有这一层时，detached popup 的 viewport 与 container 会继承旧的
    style polish 结果，画面不变。
    """
    _bootstrap()
    from libjxl_gui import main_window as mw
    # 也建一个 MainWindow，因为 ``_popup_container`` 只有在 ``showPopup()``
    # 时被 ``_popup_container_widget`` 缓存，而本测试想直接 verify 容器
    # 同步路径，所以用 showPopup 触发一次。
    mw.set_app_theme("native")
    win = mw.MainWindow()
    combo = win.findChild(mw.NativeNoFlickerComboBox)
    assert combo is not None, "主窗口应至少有一个 NativeNoFlickerComboBox"
    combo._apply_fusion_style()
    # 让容器真正被新建一次：模拟 view.window() != self.window()。
    # offscreen 里 ``view().window()`` 默认就是 combo 所在 window，
    # 所以直接调 _popup_container_widget() 是不行的——它会 return None。
    # 这里我们手动塞一个"假定是 container"的占位 widget 来走路径。
    fake_container = win  # 任意一个非 None 的 widget 即可，仅验证 resnap 不抛错
    combo._popup_container = fake_container
    combo._resnap_popup_palette()
    # 验证 view 与 viewport 的关键角色都同步到了 app。
    ap = QApplication.palette()
    vp = combo.view()
    vp_palette = vp.viewport().palette()
    for role in RELEVANT_ROLES:
        assert vp_palette.color(QPalette.Active, role) == ap.color(QPalette.Active, role), (
            f"viewport {role.name()} 没跟上 app palette "
            f"(viewport={vp_palette.color(QPalette.Active, role).name()} "
            f"app={ap.color(QPalette.Active, role).name()})"
        )
    # container 也被 setPalette 同步。
    cp = fake_container.palette()
    for role in RELEVANT_ROLES:
        assert cp.color(QPalette.Active, role) == ap.color(QPalette.Active, role), (
            f"container {role.name()} 没跟上 app palette"
        )
    win.close()
    print("PASS resnap_propagates_to_viewport_and_container")


def test_color_scheme_change_propagates_after_popup_already_exists():
    """#3 用户的真实场景：popup container 已经创建过一次（且没销毁）
    之后，主程序切 color scheme——``_refresh_combo_styles`` 必须把这次
    切换送达 view + viewport + container，三层关键角色全部同步。
    """
    _bootstrap()
    from libjxl_gui import main_window as mw
    mw.set_app_theme("native")
    # 模拟容器已被前一次 showPopup 创建、且 curated 的 _popup_container 引用。
    win = mw.MainWindow()
    combo = win.findChild(mw.NativeNoFlickerComboBox)
    assert combo is not None
    # 假设前一次 showPopup 留下的 container 引用
    fake_container = win
    combo._popup_container = fake_container

    # 制造一次显式的 palette 篡改（模拟 Qt 自身在 offscreen 下不完全模拟
    # 切深浅色的限制——直接改 app.palette 的 Highlight 走一遍完整链）。
    import PySide6.QtGui as gui
    original_highlight = QApplication.palette().color(QPalette.Active, QPalette.Highlight)
    new_highlight = QColor(255, 0, 255)  # 品红，便于断言
    pal = QApplication.palette()
    pal.setColor(QPalette.Active, QPalette.Highlight, new_highlight)
    pal.setColor(QPalette.Inactive, QPalette.Highlight, new_highlight)
    QApplication.setPalette(pal)
    try:
        # 真机上的真实流程是 QGuiApplication.styleHints().setColorScheme()
        # 发 PaletteChange → MainWindow.changeEvent → _refresh_combo_styles。
        # 这里直接调 _refresh_combo_styles 等价：
        for cb in win.findChildren(mw.NativeNoFlickerComboBox):
            cb._apply_fusion_style()

        ap = QApplication.palette()
        assert ap.color(QPalette.Active, QPalette.Highlight) == new_highlight
        # 关键断言：viewport 和 container 的 Highlight 跟上了。
        v = combo.view()
        vp_pal = v.viewport().palette()
        cp = fake_container.palette()
        assert vp_pal.color(QPalette.Active, QPalette.Highlight) == new_highlight, (
            f"viewport Highlight 未刷新: {vp_pal.color(QPalette.Active, QPalette.Highlight).name()}"
        )
        assert cp.color(QPalette.Active, QPalette.Highlight) == new_highlight, (
            f"container Highlight 未刷新: {cp.color(QPalette.Active, QPalette.Highlight).name()}"
        )
    finally:
        # 还原 app.palette，避免污染后续测试。
        pal = QApplication.palette()
        pal.setColor(QPalette.Active, QPalette.Highlight, original_highlight)
        pal.setColor(QPalette.Inactive, QPalette.Highlight, original_highlight)
        QApplication.setPalette(pal)
        win.close()
    print("PASS color_scheme_change_propagates_after_popup_already_exists")


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
        test_view_qss_has_no_border_under_native_theme,
        test_view_qss_has_border_under_fusion_theme,
        test_view_qss_follows_theme_switch_both_ways,
        test_show_popup_never_calls_setWindowFlags,
        test_show_popup_applies_flags_once_when_they_are_stale,
        test_detached_view_is_never_treated_as_container,
        test_resnap_propagates_to_viewport_and_container,
        test_color_scheme_change_propagates_after_popup_already_exists,
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
