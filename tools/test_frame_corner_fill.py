# -*- coding: utf-8 -*-
"""Headless 回归测试：列表视图的圆角边框四角不再被内容底色盖掉。

背景（2026-10-30 真机定位）：Windows 11 原生样式给 StyledPanel 画的是圆角边框，
但 QListWidget / QListView / QTreeView 还带一个独立的子视口，子视口用 Base 色
铺满自己的矩形，在 150% 缩放下取整后正好压住四个角上那 1 逻辑像素宽的圆弧 ——
四角看起来像缺了个口子。QPlainTextEdit（状态页日志框）没有独立子视口，同样的
样式下四角完好，所以它是参照物。

修复：给列表子视口关掉 ``autoFillBackground``（见
``main_window._frame_preserving_viewport``）。本测试锁住：

1. 圆角边框样式（windows11）下两个列表的子视口都不自动填底色；
2. Fusion 下**必须保持**自动填底色 —— 关掉会让窗口色顶替 Base，实测是一处回归；
3. 换样式后 ``_refresh_view_frame_corners()`` 会把开关同步过来（来回切都正确）；
4. 显式传入 ``preserve`` 时以参数为准，便于真机探针复现旧行为；
5. 关掉底色后内容区底色仍是 Base（不能透出窗口色），且 WA_StaticContents 未被
   误伤（滚动仍走 blit 快路径）。

真机像素级复核（uxtheme 参与绘制，offscreen 测不到弧线）：
``tools/probe_frame_corner_diff.py``；成因与候选改法对照：
``tools/probe_frame_corner_cause.py``。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtCore import Qt, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="jxlforge_frame_corner_")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge import main_window as mw  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def _set_style(name):
    style = QStyleFactory.create(name)
    QApplication.setStyle(style)
    for _ in range(3):
        _app.processEvents()
    return style


def _lists(win):
    return (win.input_list, win.action_list)


def test_rounded_style_disables_fill():
    win = mw.MainWindow()
    style = _set_style("windows11")
    check("能创建 windows11 样式", style is not None and
          style.objectName() in mw.ROUNDED_FRAME_STYLES,
          style and style.objectName())
    win._refresh_view_frame_corners()
    for label, view in zip(("输入列表", "动作列表"), _lists(win)):
        vp = view.viewport()
        check("%s 在圆角边框样式下不自动填充底色" % label,
              vp is not None and not vp.autoFillBackground(),
              "autoFill=%s" % (vp and vp.autoFillBackground()))

    # 输入列表本来就带 WA_StaticContents（滚动走 blit 快路径），这次改动不能把它
    # 顺带关掉；动作列表从来没有这个属性，也不该被"顺手加上"。
    in_vp = win.input_list.viewport()
    act_vp = win.action_list.viewport()
    check("输入列表仍保留 WA_StaticContents（滚动 blit 未被误伤）",
          in_vp is not None and in_vp.testAttribute(Qt.WA_StaticContents))
    check("动作列表未被加上 WA_StaticContents（保持原状）",
          act_vp is not None and not act_vp.testAttribute(Qt.WA_StaticContents))

    img = win.input_list.grab().toImage()
    base = win.input_list.palette().color(win.input_list.palette().ColorRole.Base)
    got = img.pixelColor(img.width() // 2, img.height() - 8)
    check("关掉底色后内容区仍是 Base 色（未透出窗口色）",
          got.name() == base.name(), "got=%s base=%s" % (got.name(), base.name()))
    win.close()


def test_fusion_keeps_fill():
    win = mw.MainWindow()
    _set_style("fusion")
    win._refresh_view_frame_corners()
    for label, view in zip(("输入列表", "动作列表"), _lists(win)):
        vp = view.viewport()
        check("Fusion 下 %s 保持自动填充底色（关掉会透出 #353535）" % label,
              vp is not None and vp.autoFillBackground(),
              "autoFill=%s" % (vp and vp.autoFillBackground()))
        img = view.grab().toImage()
        base = view.palette().color(view.palette().ColorRole.Base)
        got = img.pixelColor(img.width() // 2, img.height() - 8)
        check("Fusion 下 %s 内容区底色 = Base" % label,
              got.name() == base.name(),
              "got=%s base=%s" % (got.name(), base.name()))
    win.close()


def test_style_switch_resyncs():
    win = mw.MainWindow()
    seen = []
    for name in ("windows11", "fusion", "windows11", "fusion"):
        _set_style(name)
        win._refresh_view_frame_corners()
        seen.append([v.viewport().autoFillBackground() for v in _lists(win)])
    check("来回换样式后开关始终跟随当前样式",
          seen == [[False, False], [True, True], [False, False], [True, True]],
          str(seen))
    win.close()


def test_explicit_preserve_argument():
    win = mw.MainWindow()
    _set_style("windows11")
    mw._frame_preserving_viewport(win.input_list, preserve=False)
    check("preserve=False 时不关底色（可复现修复前的行为）",
          win.input_list.viewport().autoFillBackground())
    mw._frame_preserving_viewport(win.input_list, preserve=True)
    check("preserve=True 时关底色",
          not win.input_list.viewport().autoFillBackground())
    check("唯 windows11 被列为圆角边框样式",
          tuple(mw.ROUNDED_FRAME_STYLES) == ("windows11",),
          str(mw.ROUNDED_FRAME_STYLES))
    _set_style("windowsvista")
    win._refresh_view_frame_corners()
    check("windowsvista（直角框）不关底色",
          all(v.viewport().autoFillBackground() for v in _lists(win)))
    win.close()


def test_before_ui_built():
    win = mw.MainWindow.__new__(mw.MainWindow)   # 未跑 __init__，控件都不存在
    try:
        win._refresh_view_frame_corners()
    except AttributeError as exc:
        check("UI 未建好时刷新不崩溃", False, repr(exc))
    else:
        check("UI 未建好时刷新不崩溃", True)


def main():
    test_rounded_style_disables_fill()
    test_fusion_keeps_fill()
    test_style_switch_resyncs()
    test_explicit_preserve_argument()
    test_before_ui_built()
    failed = [n for n, ok in results if not ok]
    print()
    print("%d/%d 通过" % (len(results) - len(failed), len(results)))
    if failed:
        print("失败：", ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
