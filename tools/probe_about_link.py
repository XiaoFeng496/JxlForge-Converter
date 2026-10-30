# -*- coding: utf-8 -*-
"""临时探针：验证「关于」页网址锚点色在明暗切换后是否真的更新。

真机跑（offscreen 拿不到 uxtheme 真实绘制）。用法：

    python tools/probe_about_link.py [--scheme dark|light] [--switch dark|light]

截图落系统临时目录，不污染仓库。打印：app palette 的 Link 色、控件富文本、
以及从控件渲染像素里测出的实际笔画色与对比度。
"""
import argparse
import os
import sys
import time
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# ⚠️ 必须在 import PySide6 之前决定平台插件。
_p = argparse.ArgumentParser(add_help=False)
_p.add_argument("--theme", default="native_noflicker_proto")
_p.add_argument("--scheme", default="dark", choices=("dark", "light"))
_p.add_argument("--switch", default=None, choices=("dark", "light"))
_P = _p.parse_known_args()[0]

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="jxlforge_probe_link_")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)
# 主题 / 明暗必须在建窗口前落盘（MainWindow 构造期就读）。
_s = QSettings()
_s.beginGroup("appearance")
_s.setValue("theme", _P.theme)
_s.setValue("color_scheme", _P.scheme)
_s.endGroup()
_s.sync()

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from jxlforge import main_window as mw  # noqa: E402


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


win = mw.MainWindow()
win.resize(960, 700)
win.show()
pump(0.6)
win.tabs.setCurrentIndex(win.tabs.indexOf(win.about_tab))
pump(0.4)

lab = win._about_home_label
out_dir = os.path.join(tempfile.gettempdir(), "jxlforge_probe_link")
os.makedirs(out_dir, exist_ok=True)


def analyze(tag):
    """取控件渲染像素的「核心笔画色」与对比度（抗锯齿会稀释，故取极值分位）。"""
    from PIL import Image
    import numpy as np
    p = os.path.join(out_dir, "link_%s.png" % tag)
    lab.grab().save(p)
    a = np.asarray(Image.open(p).convert("RGB")).astype(np.int32)
    bg = tuple(int(x) for x in a[0, 0])
    ink = a[np.abs(a - np.array(bg)).sum(axis=2) > 40]
    if not len(ink):
        return p, bg, None, None
    lum = ink[:, 0] * 0.299 + ink[:, 1] * 0.587 + ink[:, 2] * 0.114
    dark_bg = sum(bg) > 380          # 背景亮 → 取最暗笔画；背景暗 → 取最亮
    core = ink[lum <= np.percentile(lum, 5)] if dark_bg else \
        ink[lum >= np.percentile(lum, 95)]
    c = tuple(int(x) for x in core.mean(axis=0))

    def L(col):
        def f(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = [f(x) for x in col]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    l1, l2 = L(c), L(bg)
    cr = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    return p, bg, c, cr


def report(tag, note):
    p, bg, c, cr = analyze(tag)
    print("[%s] %s" % (tag, note))
    print("   app.palette Link = %s" % app.palette().color(QPalette.Link).name())
    print("   html             = %s" % lab.text())
    if c is None:
        print("   ⚠️ 未测到字形像素")
    else:
        print("   背景=#%02x%02x%02x  笔画=#%02x%02x%02x  对比度=%.2f:1" %
              (bg[0], bg[1], bg[2], c[0], c[1], c[2], cr))
    print("   截图: %s" % p)


report(_P.scheme, "启动（未切换）")
if _P.switch:
    win._apply_color_scheme(_P.switch)
    pump(1.5)
    report(_P.switch, "运行时切换到 %s" % _P.switch)

# ⚠️ 末尾不能用 os._exit()：会跳过 stdout 刷新，print 全丢。
sys.stdout.flush()
