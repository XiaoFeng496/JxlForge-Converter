# -*- coding: utf-8 -*-
r"""回归测试：「常规」区下拉在切到原生后**不随原生样式变宽**（页面不向右膨胀）。

背景（设计取舍，2026-09-05 由用户定夺）：下拉的 minimumWidth 是构建时按当时样式
量出的。Windows 原生样式的箭头按钮与边框内边距比 Fusion 宽，同一个最小宽度留给
文字的空间更少——若按原生重新量宽（老做法），下拉框会**随原生样式变宽**把设置页
顶向右（"页面向右膨胀"），且全局 _apply_theme 只重算它自己那份名单（控件样式 /
CPU 优先级 / CPU 核心 / effort），「主题」与「语言」不在其中。

新做法（`_set_combo_min_width(..., cap=True)`）：构建时把 max-width 也钉成量出的
宽度，下拉框锁定在建構(Fusion)尺寸，切到原生后**不会变宽**——闭合框在原生下可能
轻微裁切文字，但弹出列表仍按最宽项完整展开，且页面不再向右膨胀。这是用户要的取舍
（"缩到合适大小" 优先于 "原生下不裁切"）。

覆盖：
- 会话内切到「原生」时，「主题」与「语言」被重新钉宽（max-width 重新钉到 min-width）。
- 切回 Fusion / 原生（无闪烁）后同样重新钉。
- 多次切换后这三个「常规」下拉始终 pinned（maximumWidth == minimumWidth），
  即不会随样式变宽把页面顶出去。

注：每个用例跑在独立的临时 ini 目录，避免污染真实
%APPDATA%\libjxl\libjxl-gui.ini。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import QApplication


def _bootstrap():
    r"""Each test runs in a brand-new isolated ini directory so QSettings never
    touches the real ``%APPDATA%\libjxl\libjxl-gui.ini``."""
    QSettings.setDefaultFormat(QSettings.IniFormat)
    tmp = tempfile.mkdtemp(prefix="libjxl_gui_regular_width_")
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    app = QApplication.instance() or QApplication(["-platform", "offscreen"])
    return app, tmp


def _new_window():
    from libjxl_gui.main_window import MainWindow
    return MainWindow()


def _install_pin_spy(w):
    """Wrap setMaximumWidth on the two re-pinned combos so we can see which
    combos get re-pinned when the theme switches."""
    recorded = []
    for name in ("color_scheme_combo", "language_combo"):
        combo = getattr(w, name)
        original = combo.setMaximumWidth

        def spy(width, _orig=original, _name=name):
            recorded.append(_name)
            return _orig(width)

        combo.setMaximumWidth = spy
    return recorded


def _switch_theme(w, theme):
    """Do exactly what the user does: pick another entry in the 控件样式 combo."""
    w.theme_combo.setCurrentIndex(w.theme_combo.findData(theme))


def test_regular_combos_repinned_when_switching_to_native():
    app, _ = _bootstrap()
    w = _new_window()
    recorded = _install_pin_spy(w)
    _switch_theme(w, "native")
    missed = [n for n in ("color_scheme_combo", "language_combo")
              if n not in recorded]
    # 钉宽后 max-width 必须等于 min-width：原生下才不会随样式变宽顶出页面。
    unpinned = [n for n in ("color_scheme_combo", "language_combo")
                if getattr(w, n).maximumWidth() != getattr(w, n).minimumWidth()]
    w.close()
    assert not missed, \
        "切到原生后这些「常规」下拉没被重新钉宽：%r" % (missed,)
    assert not unpinned, \
        "切到原生后这些「常规」下拉未钉宽（max≠min），原生下会顶宽页面：%r" % (unpinned,)
    print("PASS regular_combos_repinned_when_switching_to_native")


def test_regular_combos_repinned_when_switching_back():
    app, _ = _bootstrap()
    w = _new_window()
    # 启动时默认就是「原生（无闪烁）」，直接切回不会产生信号，必须先切到原生。
    _switch_theme(w, "native")
    recorded = _install_pin_spy(w)
    _switch_theme(w, "native_noflicker")
    missed = [n for n in ("color_scheme_combo", "language_combo")
              if n not in recorded]
    unpinned = [n for n in ("color_scheme_combo", "language_combo")
                if getattr(w, n).maximumWidth() != getattr(w, n).minimumWidth()]
    w.close()
    assert not missed, \
        "切回原生（无闪烁）后这些「常规」下拉没被重新钉宽：%r" % (missed,)
    assert not unpinned, \
        "切回原生（无闪烁）后这些「常规」下拉未钉宽（max≠min）：%r" % (unpinned,)
    print("PASS regular_combos_repinned_when_switching_back")


def test_combos_stay_pinned_after_switches():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    for theme in ("native", "fusion", "native_noflicker", "native"):
        w.theme_combo.setCurrentIndex(w.theme_combo.findData(theme))
    stale = []
    for name in ("color_scheme_combo", "theme_combo", "language_combo"):
        combo = getattr(w, name)
        if combo.maximumWidth() != combo.minimumWidth():
            stale.append((name, combo.maximumWidth(), combo.minimumWidth()))
    w.close()
    assert not stale, "这些「常规」下拉未钉宽（max≠min），原生下会顶宽页面：%r" % (stale,)
    print("PASS combos_stay_pinned_after_switches")


def main():
    tests = [
        test_regular_combos_repinned_when_switching_to_native,
        test_regular_combos_repinned_when_switching_back,
        test_combos_stay_pinned_after_switches,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:  # noqa: BLE001 - test harness
            failed += 1
            print("ERROR %s: %s: %s" % (t.__name__, type(exc).__name__, exc))
    print("\n%d/%d passed, %d failed" % (passed, len(tests), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
