# -*- coding: utf-8 -*-
"""Headless 回归测试：底部三个按钮的绘制内缩（_InsetPushButtonStyle）。

背景（2026-10-30 实测定位）：PySide6 的 qmodernwindowsstyle 插件从 6.11.2 起把
圆角药丸改成「几乎填满按钮矩形」，6.11.1 是「四周内缩约 2 逻辑像素」。v0.3.0 的
发行包因混合安装收进了 6.11.1 插件，所以那个包的按钮小一圈、间隙更大；环境升到
6.11.3 后按钮"变大了一点点"（药丸 116×45 → 119×49，间隙 15 → 11，槽位不变）。

修复方式：不用 Qt 版本、也不改控件几何，而是给原生主题套一层 QProxyStyle，只对
**显式带上 BUTTON_INSET_PROPERTY 的按钮**（底部 转换/停止/关闭）把**绘制**矩形
向内收 BUTTON_DRAW_INSET；页面内其它按钮保持当前 Qt 的默认观感。本测试锁住：

1. 原生主题下全局 style 是代理、Fusion 主题下不是；
2. 代理透传 base 的 objectName（避免把 "windows11" 报成空串误导诊断）；
3. 内缩量恰好是 BUTTON_DRAW_INSET，嵌套调用不会二次内缩；
4. 没有该属性的按钮**不**内缩（这次改动的核心约束）；
5. 极小按钮不内缩（防止图标按钮被压没）；
6. 真控件：底部按钮墨迹 = 控件矩形 - 2*inset，而工具栏按钮墨迹 = 控件矩形。

真机（150% 缩放、windows11 插件）的像素级复核见
``JxlForge-Build/_cmp/probe_inset.py``：药丸 116×45、间隙 15px，与 v0.3.0 逐项一致。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtCore import QRect, QSettings  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QPushButton,
    QStyle,
    QStyleFactory,
    QStyleOptionButton,
)

_tmp = tempfile.mkdtemp(prefix="jxlforge_btn_inset_")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge import main_window as mw  # noqa: E402

results = []

MAGENTA = QColor(255, 0, 255)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def _make_button(opt_in):
    btn = QPushButton()
    if opt_in:
        btn.setProperty(mw.BUTTON_INSET_PROPERTY, True)
    return btn


def _painted_bbox(style, rect, widget, text="转换"):
    """把按钮画到品红底图上，返回实际被绘制区域的边界。"""
    img = QImage(160, 64, QImage.Format_RGB32)
    img.fill(MAGENTA)
    painter = QPainter(img)
    opt = QStyleOptionButton()
    opt.rect = QRect(rect)
    opt.state = QStyle.State_Enabled
    opt.text = text
    style.drawControl(QStyle.CE_PushButton, opt, painter, widget)
    painter.end()
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y) != MAGENTA:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return QRect(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def test_inset_amount():
    box = QRect(8, 8, 120, 44)
    plain = _painted_bbox(QStyleFactory.create("Fusion"), box, _make_button(False))
    proxy = mw._InsetPushButtonStyle(QStyleFactory.create("Fusion"))
    inset = _painted_bbox(proxy, box, _make_button(True))
    if plain is None or inset is None:
        check("两种情形都能绘制出内容", False, "plain=%s inset=%s" % (plain, inset))
        return
    d = mw.BUTTON_DRAW_INSET
    check("绘制宽度收窄 2*inset", plain.width() - inset.width() == 2 * d,
          "%d -> %d (inset=%d)" % (plain.width(), inset.width(), d))
    check("绘制高度收窄 2*inset", plain.height() - inset.height() == 2 * d,
          "%d -> %d (inset=%d)" % (plain.height(), inset.height(), d))
    check("左右各收 inset（居中，未偏移）",
          (inset.left() - plain.left() == d)
          and (plain.right() - inset.right() == d),
          "left %d->%d right %d->%d" % (plain.left(), inset.left(),
                                        plain.right(), inset.right()))
    check("嵌套调用没有二次内缩（收窄量恰为 2*inset，不是 4*inset）",
          plain.width() - inset.width() < 4 * d)


def test_unmarked_button_untouched():
    box = QRect(8, 8, 120, 44)
    proxy = mw._InsetPushButtonStyle(QStyleFactory.create("Fusion"))
    bare = _painted_bbox(proxy, box, _make_button(False))
    raw = _painted_bbox(QStyleFactory.create("Fusion"), box, _make_button(False))
    check("没有内缩属性的按钮绘制范围不变", bare == raw, "%s vs %s" % (bare, raw))
    check("没有内缩属性的按钮铺满给定矩形",
          bare is not None and bare.width() == box.width()
          and bare.height() == box.height(), "%s" % (bare,))


def test_tiny_button_untouched():
    proxy = mw._InsetPushButtonStyle(QStyleFactory.create("Fusion"))
    box = QRect(8, 8, 2 * mw.BUTTON_DRAW_INSET + 4, 2 * mw.BUTTON_DRAW_INSET + 4)
    try:
        rect = _painted_bbox(proxy, box, _make_button(True), text="")
    except Exception as exc:  # noqa: BLE001
        check("极小按钮不内缩且不崩溃", False, repr(exc))
        return
    check("极小按钮不内缩且不崩溃",
          rect is not None and rect.width() <= box.width(), "bbox=%s" % (rect,))


def _ink_bbox(widget):
    img = widget.grab().toImage()
    bg = widget.palette().color(widget.backgroundRole())
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if (abs(c.red() - bg.red()) > 6 or abs(c.green() - bg.green()) > 6
                    or abs(c.blue() - bg.blue()) > 6):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None, img.devicePixelRatio()
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1), img.devicePixelRatio()


def test_theme_wiring():
    win = mw.MainWindow()
    native_name = win._native_style_name
    win.theme_combo.setCurrentIndex(win.theme_combo.findData("native"))
    for _ in range(3):
        _app.processEvents()
    style = _app.style()
    check("原生主题使用内缩代理", isinstance(style, mw._InsetPushButtonStyle),
          type(style).__name__)
    check("代理透传 base 的 objectName", style.objectName() == native_name,
          "objectName=%r native=%r" % (style.objectName(), native_name))

    mine = (win.convert_button, win.stop_button, win.close_button)
    check("底部三个按钮都带内缩属性",
          all(b.property(mw.BUTTON_INSET_PROPERTY) for b in mine))
    others = (win.add_files_button, win.add_folder_button, win.remove_button,
              win.clear_button)
    check("页面内其它按钮不带内缩属性",
          not any(b.property(mw.BUTTON_INSET_PROPERTY) for b in others))

    dpr = win.convert_button.grab().devicePixelRatio()
    shrink = 2 * mw.BUTTON_DRAW_INSET * dpr
    got, _ = _ink_bbox(win.convert_button)
    check("转换按钮墨迹宽 = 控件宽 - 2*inset",
          got is not None and abs(got[0] - (win.convert_button.width() * dpr - shrink)) <= 2,
          "got %s want %.1f" % (got and got[0],
                                win.convert_button.width() * dpr - shrink))
    got_add, _ = _ink_bbox(win.add_files_button)
    check("工具栏按钮墨迹宽 = 控件宽（不受影响）",
          got_add is not None
          and abs(got_add[0] - win.add_files_button.width() * dpr) <= 1,
          "got %s want %.1f" % (got_add and got_add[0],
                                win.add_files_button.width() * dpr))

    win.theme_combo.setCurrentIndex(win.theme_combo.findData("fusion"))
    for _ in range(3):
        _app.processEvents()
    style = _app.style()
    check("Fusion 主题不套代理（保持原样）",
          not isinstance(style, mw._InsetPushButtonStyle),
          type(style).__name__)

    win.theme_combo.setCurrentIndex(win.theme_combo.findData("native"))
    for _ in range(3):
        _app.processEvents()
    check("切回原生主题重新套上代理",
          isinstance(_app.style(), mw._InsetPushButtonStyle))
    win.close()


def main():
    test_inset_amount()
    test_unmarked_button_untouched()
    test_tiny_button_untouched()
    test_theme_wiring()
    failed = [n for n, ok in results if not ok]
    print()
    print("%d/%d 通过" % (len(results) - len(failed), len(results)))
    if failed:
        print("失败：", ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
