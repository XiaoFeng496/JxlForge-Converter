# -*- coding: utf-8 -*-
r"""回归测试：设置页「常规」区新增的「语言」下拉（占位项）。

覆盖：
- 主窗口暴露 language_combo，且它位于「常规」分组内。
- 内置两项：简体中文 / English，itemData 分别为 zh_CN / en_US。
- 默认选中简体中文（zh_CN）。
- 选择后立即写入 appearance/language，并在下次启动时由
  ``__main__._apply_persisted_language()`` 于构造窗口前装进 i18n。
- 语言代码不在白名单时回退 zh_CN。

语言是「重启生效」的：界面文本在窗口构建时取定，运行时改字典会让新旧
语言混在一起。所以这里测的是「持久化 + 下次启动应用」这条链路，
而不是「切换后立刻变英文」。

注：每个用例跑在独立的临时 ini 目录，避免污染真实
%APPDATA%\libjxl\libjxl-gui.ini。

⚠️ 调整「常规」区内部排布后，只需同步修改下面的 _EXPECTED_GRID_POSITIONS。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel

# 「常规」区内各下拉在内部 QGridLayout 中的落位 (row, col)。
# 这里钉死绝对坐标是有意为之：它同时充当"布局锁"，排布被意外打乱时会报警。
# 唯一维护点：移动/增删控件后改这张表即可，用例体不用动。
_EXPECTED_GRID_POSITIONS = {
    "color_scheme_combo": (0, 0),  # 主题（首行左列）
    "theme_combo": (1, 0),         # 控件样式（第二行，跨两列 colspan=2）
    "language_combo": (0, 1),      # 语言（首行右列）
}


def _bootstrap():
    r"""Each test runs in a brand-new isolated ini directory so QSettings never
    touches the real ``%APPDATA%\libjxl\libjxl-gui.ini``."""
    QSettings.setDefaultFormat(QSettings.IniFormat)
    tmp = tempfile.mkdtemp(prefix="libjxl_gui_language_")
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    app = QApplication.instance() or QApplication(["-platform", "offscreen"])
    return app, tmp


def _row_label_of(combo):
    r"""Return the QLabel sitting on the same row, left of ``combo``.

    「常规」分组内部是 QVBoxLayout → QGridLayout → 每行 QHBoxLayout 的多层
    嵌套（双列排布），必须递归下钻，只查一层会找不到。
    """
    def _search(layout):
        if layout is None:
            return None
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget() is combo:
                for k in range(i - 1, -1, -1):
                    cand = layout.itemAt(k).widget()
                    if isinstance(cand, QLabel):
                        return cand
                return None
            found = _search(item.layout())
            if found is not None:
                return found
        return None

    return _search(combo.parentWidget().layout())


def _grid_position_of(combo):
    r"""Return (row, col) of the row layout holding ``combo`` inside the
    QGridLayout of the 常规 group, or None when no grid is used."""
    from PySide6.QtWidgets import QGridLayout
    outer = combo.parentWidget().layout()
    for i in range(outer.count()):
        item = outer.itemAt(i)
        grid = item.layout() if item is not None else None
        if not isinstance(grid, QGridLayout):
            continue
        for j in range(grid.count()):
            cell = grid.itemAt(j)
            row_layout = cell.layout() if cell is not None else None
            if row_layout is None:
                continue
            for k in range(row_layout.count()):
                it = row_layout.itemAt(k)
                if it is not None and it.widget() is combo:
                    row, col = grid.getItemPosition(j)[:2]
                    return (row, col)
    return None


def test_language_combo_exists_in_常规():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    combo = getattr(w, "language_combo", None)
    assert combo is not None, "主窗口应暴露 language_combo"
    group = combo.parentWidget()
    while group is not None and not isinstance(group, QGroupBox):
        group = group.parentWidget()
    assert group is not None and group.title() == "常规", \
        "语言下拉必须位于「常规」分组，实际=%r" % (group.title() if group else None)
    w.close()
    print("PASS language_combo_exists_in_常规")


def test_language_row_label():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    label = _row_label_of(w.language_combo)
    assert label is not None and label.text() == "语言", \
        "语言行左侧标签应为「语言」，实际=%r" % (label.text() if label else None)
    w.close()
    print("PASS language_row_label")


def test_grid_positions_in_常规():
    r"""「常规」内部双列的落位，对照文件顶部的 _EXPECTED_GRID_POSITIONS
    逐项核对（改排布时只改那张表）。"""
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    for attr, expected in sorted(_EXPECTED_GRID_POSITIONS.items()):
        combo = getattr(w, attr, None)
        assert combo is not None, "主窗口缺少属性 %s" % attr
        actual = _grid_position_of(combo)
        assert actual == expected, \
            "%s 落位错误：期望 %r，实际 %r" % (attr, expected, actual)
    w.close()
    print("PASS grid_positions_in_常规")


def test_language_items_and_default():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow, _LANGUAGE_COMBO_ORDER, _LANGUAGE_DEFAULT
    w = MainWindow()
    combo = w.language_combo
    items = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
    assert items == [
        ("跟随系统", "follow_system"),
        ("简体中文", "zh_CN"),
        ("繁體中文", "zh_TW"),
        ("English", "en_US"),
    ], "语言项应为 跟随系统/follow_system + 简体中文/zh_CN + 繁體中文/zh_TW + English/en_US，实际=%r" % (items,)
    # 默认未保存过偏好时回退到 follow_system（跟随系统）
    assert combo.currentData() == _LANGUAGE_DEFAULT, \
        "默认应选中跟随系统(follow_system)，实际=%r" % combo.currentData()
    # 顺序须与下拉顺序定义一致
    assert [combo.itemData(i) for i in range(combo.count())] == list(_LANGUAGE_COMBO_ORDER), \
        "下拉顺序须等于 _LANGUAGE_COMBO_ORDER，实际=%r" % [
            combo.itemData(i) for i in range(combo.count())]
    w.close()
    print("PASS language_items_and_default")


def test_language_persists_on_change():
    r"""选择语言后应立即写入 appearance/language（切换本身重启后生效）。"""
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    combo = w.language_combo
    combo.setCurrentIndex(combo.findData("en_US"))
    settings = QSettings()
    settings.beginGroup("appearance")
    stored = settings.value("language")
    settings.endGroup()
    assert stored == "en_US", \
        "选择 English 后应写入 appearance/language=en_US，实际=%r" % (stored,)
    w.close()
    print("PASS language_persists_on_change")


def test_follow_system_persists_on_change():
    r"""选择「跟随系统」哨兵项也必须写入 appearance/language。

    回归：此前保存处理器用 _LANGUAGE_ORDER（仅真实语言）做白名单，
    follow_system 不在其中被直接 return，导致该选项永远落不了盘、重启回退。
    修复后守卫改用 _LANGUAGE_COMBO_ORDER（含 follow_system / zh_TW 哨兵）。
    """
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    combo = w.language_combo
    # 跟随系统是默认选中项（index 0），直接 setCurrentIndex(0) 不会触发
    # currentIndexChanged（索引没变）。模拟真实用户：先从已选的 English 切到
    # 跟随系统（索引变化才会发信号、走保存链路）。
    combo.setCurrentIndex(combo.findData("en_US"))
    combo.setCurrentIndex(combo.findData("follow_system"))
    settings = QSettings()
    settings.beginGroup("appearance")
    stored = settings.value("language")
    settings.endGroup()
    assert stored == "follow_system", \
        "选择「跟随系统」后应写入 appearance/language=follow_system，实际=%r" % (stored,)
    w.close()
    print("PASS follow_system_persists_on_change")


def test_language_applied_on_next_launch():
    r"""重启路径：__main__._apply_persisted_language() 要在构造窗口前
    把持久化的语言装进 i18n，否则界面仍是中文。"""
    app, _ = _bootstrap()
    from libjxl_gui import i18n
    from libjxl_gui.__main__ import _apply_persisted_language
    from libjxl_gui.main_window import MainWindow

    # 先模拟「用户在设置页选了 English」
    s = QSettings()
    s.beginGroup("appearance")
    s.setValue("language", "en_US")
    s.endGroup()
    s.sync()

    try:
        code = _apply_persisted_language()
        assert code == "en_US", "应加载 en_US，实际=%r" % (code,)
        assert i18n.current_language() == "en_US", \
            "i18n 当前语言应为 en_US，实际=%r" % i18n.current_language()
        # 新窗口的界面文本必须是英文，且内部 ID 仍是中文
        w = MainWindow()
        # 动作类型下拉框已改为「添加动作▶」弹出的 add_action_menu；
        # 菜单项 text = 译文，data = 原始中文 ID。
        assert w.add_action_menu.actions()[0].text() == "Resize", \
            "重启后动作类型应为英文，实际=%r" % w.add_action_menu.actions()[0].text()
        assert w.add_action_menu.actions()[0].data() == "调整大小", \
            "内部 ID 必须是中文，实际=%r" % w.add_action_menu.actions()[0].data()
        # 设置页的语言下拉要回显当前语言，而不是复位成默认
        assert w.language_combo.currentData() == "en_US", \
            "语言下拉应回显 en_US，实际=%r" % w.language_combo.currentData()
        w.close()
        print("PASS language_applied_on_next_launch")
    finally:
        i18n.set_language("zh_CN")


def test_invalid_language_falls_back():
    r"""ini 被写坏（语言代码不在清单）时回退到有效语言，不能让启动崩掉。

    非法代码 → 回落默认偏好 follow_system → 解析成系统语言（本机是 zh_CN 或
    en_US 之一）。这里只断言「回落到一个可加载的已有语言」，不写死具体值，
    以免在英文系统上误报。
    """
    app, _ = _bootstrap()
    from libjxl_gui import i18n
    from libjxl_gui.__main__ import _apply_persisted_language

    s = QSettings()
    s.beginGroup("appearance")
    s.setValue("language", "mars_MARS")
    s.endGroup()
    s.sync()
    try:
        code = _apply_persisted_language()
        assert code in ("zh_CN", "en_US"), \
            "非法语言应回退到已有语言(zh_CN/en_US)，实际=%r" % (code,)
        print("PASS invalid_language_falls_back")
    finally:
        i18n.set_language("zh_CN")


def main():
    tests = [
        test_language_combo_exists_in_常规,
        test_language_row_label,
        test_grid_positions_in_常规,
        test_language_items_and_default,
        test_language_persists_on_change,
        test_follow_system_persists_on_change,
        test_language_applied_on_next_launch,
        test_invalid_language_falls_back,
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
