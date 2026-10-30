# -*- coding: utf-8 -*-
"""真机探针：三个带边框控件（输入列表 / 动作列表 / 状态页日志框）的四角像素。

必须真机（不能 offscreen）跑，否则 uxtheme 不参与绘制：

    python tools/probe_frame_corner_diff.py [--save 前缀]

判据：三者的角部像素应**完全一致**（列表是"修复目标"，日志框是"参照物"），
且直边中段保持原生边框的 ``1e 26 38 2d``；Fusion 主题下内容底色应等于 Base，
不能透出窗口色（关子视口填充过头会踩这个坑）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from jxlforge import main_window as mw  # noqa: E402
from jxlforge.main_window import MainWindow  # noqa: E402

S = 6


def tl(img):
    return [" ".join("%02x" % img.pixelColor(x, y).red() for x in range(S))
            for y in range(S)]


def left_edge(img):
    y = img.height() // 2
    return " ".join("%02x" % img.pixelColor(x, y).red() for x in range(6))


def main():
    save = None
    if "--save" in sys.argv:
        save = sys.argv[sys.argv.index("--save") + 1]
    app = QApplication([a for a in sys.argv if a != "--save" and a != save])
    win = MainWindow()
    win.resize(900, 640)
    win.show()
    app.processEvents()

    for theme in ("native_noflicker_proto", "fusion"):
        win._apply_theme(theme)
        for scheme in ("dark", "light"):
            mw.set_app_color_scheme(scheme)
            if hasattr(win, "_apply_color_scheme"):
                win._apply_color_scheme(scheme)
            app.processEvents()
            app.processEvents()
            pal = app.palette()
            base = pal.color(pal.ColorRole.Base).name()
            print("=" * 74)
            print("theme=%s scheme=%s app.style=%s base=%s"
                  % (theme, scheme, app.style().objectName(), base))
            for name, w in (("文件列表", win.input_list),
                            ("动作列表", win.action_list),
                            ("日志框  ", win.log_edit)):
                img = w.grab().toImage()
                h, wd = img.width() and img.height(), img.width()
                interior = img.pixelColor(img.width() // 2, img.height() - 8)
                print("  %s diag=%s interior=%s 直边=[%s]"
                      % (name, img.pixelColor(3, 3).name(),
                         interior.name(), left_edge(img)))
                for r in tl(img):
                    print("      ", r)
            if save:
                win.grab().save("%s_%s_%s.png" % (save, theme, scheme))
                print("  saved %s_%s_%s.png" % (save, theme, scheme))
    return 0


if __name__ == "__main__":
    sys.exit(main())
