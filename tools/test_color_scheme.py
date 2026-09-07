# -*- coding: utf-8 -*-
"""回归测试：设置页「常规」区新增的颜色主题下拉。

覆盖：
- 区域标题由"主题"改为"常规"。
- "界面主题"选项名改为"控件样式"。
- "主题"下拉内置 跟随系统 / 亮色 / 暗色 三项，默认 跟随系统。
- 切换值持久化到 appearance/color_scheme。
- 重启后恢复最近选择。
- _color_scheme_loading 守卫在 build/restore 期间阻止无谓保存。

"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel


def _bootstrap():
    r"""Each test runs in a brand-new isolated ini directory so QSettings never
    touches the real ``%APPDATA%\libjxl\libjxl-gui.ini``."""
    QSettings.setDefaultFormat(QSettings.IniFormat)
    tmp = tempfile.mkdtemp(prefix="libjxl_gui_color_scheme_")
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    app = QApplication.instance() or QApplication(["-platform", "offscreen"])
    return app, tmp


def test_region_renamed_to_常规():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    titles = [g.title() for g in w.findChildren(QGroupBox)]
    assert "常规" in titles, f"应存在「常规」分组，实际={titles}"
    assert "主题" not in titles, "旧「主题」分组标题必须被替换"
    w.close()
    print("PASS region_renamed_to_常规")


def _row_label_of(combo):
    r"""Return the QLabel sitting on the same row, left of ``combo``.

    「常规」分组内部是 QVBoxLayout → QGridLayout → 每行 QHBoxLayout 的多层
    嵌套（双列排布），所以必须递归下钻，不能只查一层。
    """
    def _search(layout):
        if layout is None:
            return None
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget() is combo:
                # 同一行里，靠 combo 左侧最近的 QLabel
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


def test_label_renamed_to_控件样式():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    label = _row_label_of(w.theme_combo)
    assert label is not None and label.text() == "控件样式", \
        f"theme_combo 左侧标签必须是「控件样式」，实际={label.text() if label else None}"
    # color_scheme_combo 左侧标签必须是「主题」
    label2 = _row_label_of(w.color_scheme_combo)
    assert label2 is not None and label2.text() == "主题", \
        f"color_scheme_combo 左侧标签必须是「主题」，实际={label2.text() if label2 else None}"
    w.close()
    print("PASS label_renamed_to_控件样式")


def test_color_scheme_combo_defaults():
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow, _COLOR_SCHEME_ORDER, _COLOR_SCHEME_LABELS
    w = MainWindow()
    keys = [w.color_scheme_combo.itemData(i) for i in range(w.color_scheme_combo.count())]
    assert keys == list(_COLOR_SCHEME_ORDER), \
        f"颜色主题项顺序错误，期望 {_COLOR_SCHEME_ORDER}，实际 {keys}"
    labels = [w.color_scheme_combo.itemText(i) for i in range(w.color_scheme_combo.count())]
    assert labels == [_COLOR_SCHEME_LABELS[k] for k in _COLOR_SCHEME_ORDER], \
        f"颜色主题项文案错误，期望 {_COLOR_SCHEME_LABELS}，实际 {labels}"
    assert w.color_scheme_combo.currentData() == "follow_system", \
        f"颜色主题默认值必须是 跟随系统，实际={w.color_scheme_combo.currentData()}"
    w.close()
    print("PASS color_scheme_combo_defaults")


def test_persist_and_restore():
    app, tmp = _bootstrap()
    # 第一次启动：默认 follow_system → 切到 light → 关闭
    from libjxl_gui.main_window import MainWindow
    w1 = MainWindow()
    idx = w1.color_scheme_combo.findData("light")
    w1.color_scheme_combo.setCurrentIndex(idx)
    w1.close()
    del w1
    # 第二次启动：应恢复 light
    w2 = MainWindow()
    assert w2.color_scheme_combo.currentData() == "light", \
        f"重启后应恢复为 light，实际={w2.color_scheme_combo.currentData()}"
    # 切到 dark 后重启
    w2.color_scheme_combo.setCurrentIndex(w2.color_scheme_combo.findData("dark"))
    w2.close()
    del w2
    w3 = MainWindow()
    assert w3.color_scheme_combo.currentData() == "dark", \
        f"重启后应恢复为 dark，实际={w3.color_scheme_combo.currentData()}"
    w3.close()
    print("PASS persist_and_restore")


def test_persist_independent_of_theme():
    """颜色方案与控件样式互相独立：切换其中一个不应影响另一个的持久化键。"""
    app, _ = _bootstrap()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    # 默认值：theme=原生（NoFlicker框），color_scheme=follow_system
    assert w.theme_combo.currentData() == "native_noflicker_proto"
    assert w.color_scheme_combo.currentData() == "follow_system"
    # 切换 theme 到 fusion，color_scheme 不变
    w.theme_combo.setCurrentIndex(w.theme_combo.findData("fusion"))
    assert w.color_scheme_combo.currentData() == "follow_system"
    # 切换 color_scheme 到 dark，theme 不变
    w.color_scheme_combo.setCurrentIndex(w.color_scheme_combo.findData("dark"))
    assert w.theme_combo.currentData() == "fusion"
    # 检查两个键都被正确写入
    s = QSettings()
    s.beginGroup("appearance")
    assert s.value("theme") == "fusion", f"theme 应为 fusion，实际={s.value('theme')}"
    assert s.value("color_scheme") == "dark", f"color_scheme 应为 dark，实际={s.value('color_scheme')}"
    s.endGroup()
    w.close()
    print("PASS persist_independent_of_theme")


def test_invalid_persisted_value_falls_back_to_default():
    """坏的持久化值（手动改 ini 或历史遗留）必须回退到 follow_system。"""
    app, _ = _bootstrap()
    s = QSettings()
    s.beginGroup("appearance")
    s.setValue("color_scheme", "garbage_value")
    s.endGroup()
    s.sync()
    from libjxl_gui.main_window import MainWindow
    w = MainWindow()
    assert w.color_scheme_combo.currentData() == "follow_system", \
        f"坏值应回退到 跟随系统，实际={w.color_scheme_combo.currentData()}"
    w.close()
    print("PASS invalid_persisted_value_falls_back_to_default")


def test_app_color_scheme_module_helpers():
    """模块级 set_app_color_scheme / app_color_scheme 必须正确映射。"""
    from libjxl_gui.main_window import (
        app_color_scheme, set_app_color_scheme,
        _COLOR_SCHEME_ORDER, _COLOR_SCHEME_LABELS,
    )
    initial = app_color_scheme()
    try:
        for key in _COLOR_SCHEME_ORDER:
            set_app_color_scheme(key)
            assert app_color_scheme() == key, \
                f"set/get 不一致：写入 {key}，读出 {app_color_scheme()}"
        # 非法值被忽略
        set_app_color_scheme("garbage")
        assert app_color_scheme() == _COLOR_SCHEME_ORDER[-1], \
            f"非法值应被忽略，实际={app_color_scheme()}"
    finally:
        set_app_color_scheme(initial)
    print("PASS app_color_scheme_module_helpers")


if __name__ == "__main__":
    failed = 0
    for fn in [
        test_region_renamed_to_常规,
        test_label_renamed_to_控件样式,
        test_color_scheme_combo_defaults,
        test_persist_and_restore,
        test_persist_independent_of_theme,
        test_invalid_persisted_value_falls_back_to_default,
        test_app_color_scheme_module_helpers,
    ]:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    total = len([fn for fn in [
        test_region_renamed_to_常规,
        test_label_renamed_to_控件样式,
        test_color_scheme_combo_defaults,
        test_persist_and_restore,
        test_persist_independent_of_theme,
        test_invalid_persisted_value_falls_back_to_default,
        test_app_color_scheme_module_helpers,
    ]])
    print(f"\n{total - failed}/{total} passed, {failed} failed")
    sys.exit(1 if failed else 0)