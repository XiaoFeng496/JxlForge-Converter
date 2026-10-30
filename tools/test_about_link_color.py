# -*- coding: utf-8 -*-
"""Headless 回归测试：「关于」页网址链接色跟随明暗方案。

背景（真机实测，与 PySide6 版本无关）：QLabel 富文本锚点把 QApplication
palette 的 Link 色**在首帧固化**，之后明暗切换不重解析；且对控件
setPalette(Link=...) 完全无效（锚点取的是 app palette，不看控件 palette）。
表现为：暗色启动切到亮色后，网址仍是暗色那支淡青蓝（对比度 1.21:1，隐形）。

修复：链接色显式写进 HTML 的 span style，并在方案 / 主题 / 系统明暗切换后
重建富文本。本测试锁住这条逻辑（颜色本身属真机像素范畴，offscreen 不覆盖，
由 tools/probe_about_link.py 真机验证）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

# ⚠️ 隔离 QSettings：否则 MainWindow 构造会读写用户真实 ini。
_tmp = tempfile.mkdtemp(prefix="jxlforge_link_test_")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge.main_window import MainWindow  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def _color_in_html(html):
    """取出 html 里 span style 的 #rrggbb（找不到返回 None）。"""
    import re
    m = re.search(r'style="color:\s*(#[0-9a-fA-F]{6})', html or "")
    return m.group(1).lower() if m else None


def test_initial_html_uses_palette_link():
    win = MainWindow()
    lab = getattr(win, "_about_home_label", None)
    check("关于页保存了网址 label 引用", lab is not None)
    if lab is None:
        return None
    html = lab.text()
    cur = _app.palette().color(QPalette.Link).name().lower()
    check("初始 html 显式着色（span style）", _color_in_html(html) is not None,
          html)
    check("初始颜色 == 当前 palette Link", _color_in_html(html) == cur,
          "html=%s palette=%s" % (_color_in_html(html), cur))
    check("链接仍可点击打开（setOpenExternalLinks 保留）",
          lab.openExternalLinks() is True)
    check("href 仍是完整项目网址",
          "https://github.com/XiaoFeng496/JxlForge-Converter" in html)
    return win


def test_refresh_follows_new_palette(win):
    """palette 的 Link 变了以后，重建必须取到新色（这正是修复的核心）。"""
    if win is None:
        check("重建跟随新 palette（前置失败，跳过）", False)
        return
    lab = win._about_home_label
    before = _color_in_html(lab.text())
    pal = QApplication.palette()
    pal.setColor(QPalette.Link, QColor("#ff0000"))
    QApplication.setPalette(pal)
    QApplication.processEvents()
    win._refresh_about_link()
    after = _color_in_html(lab.text())
    cur = QApplication.palette().color(QPalette.Link).name().lower()
    check("重建后的颜色跟随新 palette", after == cur,
          "before=%s after=%s palette=%s" % (before, after, cur))
    check("重建确实换了色（不是被短路）", before != after,
          "%s -> %s" % (before, after))
    check("重建后仍是同一个 label 对象（未泄漏旧控件）",
          getattr(win, "_about_home_label", None) is lab)


def test_scheme_switch_keeps_consistent(win):
    """方案切换后，html 颜色必须与当前 palette 自洽（不残留旧方案色）。"""
    if win is None:
        check("方案切换后自洽（前置失败，跳过）", False)
        return
    for scheme in ("light", "dark", "light"):
        try:
            win._apply_color_scheme(scheme)
        except Exception as exc:
            check("切换 %s 不抛异常" % scheme, False, str(exc))
            continue
        QApplication.processEvents()
        after = _color_in_html(win._about_home_label.text())
        cur = QApplication.palette().color(QPalette.Link).name().lower()
        check("切到 %s 后颜色与当前 palette 一致" % scheme, after == cur,
              "html=%s palette=%s" % (after, cur))


def test_refresh_is_defensive():
    """缺字段时静默返回，不抛异常（label 尚未构建 / 被改动时）。"""
    win = MainWindow.__new__(MainWindow)  # 不跑 __init__，模拟缺字段
    try:
        MainWindow._refresh_about_link(win)
        check("缺字段时 _refresh_about_link 静默返回", True)
    except Exception as exc:
        check("缺字段时 _refresh_about_link 静默返回", False, str(exc))


def main():
    win = test_initial_html_uses_palette_link()
    test_refresh_follows_new_palette(win)
    test_scheme_switch_keeps_consistent(win)
    test_refresh_is_defensive()
    passed = sum(1 for _, ok in results if ok)
    failed = [n for n, ok in results if not ok]
    print("\n%d/%d 通过" % (passed, len(results)))
    if failed:
        print("失败：", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
