# -*- coding: utf-8 -*-
r"""回归测试：切到「原生」后，「常规」区的下拉要按新样式重算最小宽度。

背景（bug，仅影响「常规」区）：下拉的 minimumWidth 是构建时按当时样式量出来的。
Windows 原生样式的箭头按钮与边框内边距比 Fusion 宽，同一个最小宽度留给文字的空间
更少；全局 _apply_theme 只重算它自己那份名单（控件样式 / CPU 优先级 / CPU 核心 /
effort），「主题」与「语言」不在其中，于是仍背着 Fusion 量出的偏小下限——窗口收到
最窄时布局按这个偏小值压缩，文字被裁切。

复现路径（重要）：启动时的持久化主题若是「原生」，_init_theme 在 UI 构建前就已生效，
下拉按原生度量定宽，不会裁；只有"启动为 原生（无闪烁）/Fusion → 会话内切到原生"
这条路径才会触发。

覆盖：
- 会话内切到「原生」时，「主题」与「语言」被重新定宽（用探针记录，与平台样式差无关）。
- 切回 Fusion 后同样重算。
- 定宽结果始终满足 minimumWidth == sizeHint().width() + 8。

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


def _install_spy(w):
    """Wrap _set_combo_min_width so we can see which combos get re-measured."""
    recorded = []
    original = w._set_combo_min_width

    def spy(combo):
        recorded.append(combo)
        return original(combo)

    w._set_combo_min_width = spy
    return recorded


def _switch_theme(w, theme):
    """Do exactly what the user does: pick another entry in the 控件样式 combo."""
    w.theme_combo.setCurrentIndex(w.theme_combo.findData(theme))


def test_regular_combos_remeasured_when_switching_to_native():
    app, _ = _bootstrap()
    w = _new_window()
    recorded = _install_spy(w)
    _switch_theme(w, "native")
    missed = [n for n in ("color_scheme_combo", "language_combo")
              if getattr(w, n, None) not in recorded]
    w.close()
    assert not missed, \
        "切到原生后这些「常规」下拉没被重新定宽，窗口收窄时文字会被裁：%r" % (missed,)
    print("PASS regular_combos_remeasured_when_switching_to_native")


def test_regular_combos_remeasured_when_switching_back():
    app, _ = _bootstrap()
    w = _new_window()
    # 启动时默认就是「原生（无闪烁）」，直接切回不会产生信号，必须先切到原生。
    _switch_theme(w, "native")
    recorded = _install_spy(w)
    _switch_theme(w, "native_noflicker")
    missed = [n for n in ("color_scheme_combo", "language_combo")
              if getattr(w, n, None) not in recorded]
    w.close()
    assert not missed, \
        "切回原生（无闪烁）后这些「常规」下拉没被重新定宽：%r" % (missed,)
    print("PASS regular_combos_remeasured_when_switching_back")


def test_min_width_matches_size_hint_after_switches():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    for theme in ("native", "fusion", "native_noflicker", "native"):
        w.theme_combo.setCurrentIndex(w.theme_combo.findData(theme))
    stale = []
    for name in ("color_scheme_combo", "theme_combo", "language_combo"):
        combo = getattr(w, name)
        if combo.minimumWidth() != combo.sizeHint().width() + 8:
            stale.append((name, combo.minimumWidth(), combo.sizeHint().width()))
    w.close()
    assert not stale, "这些「常规」下拉的最小宽度不是按当前样式量的：%r" % (stale,)
    print("PASS min_width_matches_size_hint_after_switches")


def main():
    tests = [
        test_regular_combos_remeasured_when_switching_to_native,
        test_regular_combos_remeasured_when_switching_back,
        test_min_width_matches_size_hint_after_switches,
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
