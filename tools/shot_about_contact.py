# -*- coding: utf-8 -*-
"""真机抓一张「关于」页截图，用于肉眼核对头部排版与配色。

要点（与其余真机探针一致）：

* 必须在 **真机** 平台跑：offscreen 下 ``isVisible()`` 恒 False，量不出真实
  布局，也抓不到屏幕合成结果（用户看到的颜色来自 ``grab()``）。
* QSettings 先重定向到临时目录，避免污染真实 ini（受保护的
  ``big_image_floor_px`` 绝不能被测试夹具重置）。
* 主题从命令行写入 ini 再建窗口 —— MainWindow 在构造期就读主题，事后改
  样式表不如重建干净；因此全程只建一个窗口，多余的窗口会拖住进程退出。

用法::

    python tools/shot_about_contact.py [--theme native_noflicker_proto]
                                       [--scheme dark|light]
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_THIS)
sys.path.insert(0, _REPO)

# ⚠️ 必须在 import PySide6 之前决定平台插件。
_p = argparse.ArgumentParser(add_help=False)
_p.add_argument("--theme", default="native_noflicker_proto")
_p.add_argument("--scheme", default="dark", choices=("dark", "light"))
_P = _p.parse_known_args()[0]

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import QSettings  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="jxlforge_shot_about_")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)
# 主题 / 明暗必须在建窗口前落盘：MainWindow 构造期就读这两项。
# ⚠️ 必须用无参 QSettings()（与 MainWindow 同一读写通道，落到 setPath
# 重定向后的 org ini）——写成显式路径的 shot.ini 窗口根本不读，light 参数
# 会静默失效、截图永远是真实 ini 里的主题。
_s = QSettings()
_s.beginGroup("appearance")
_s.setValue("theme", _P.theme)
_s.setValue("color_scheme", _P.scheme)
_s.endGroup()
_s.sync()

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

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

out_dir = os.path.join(tempfile.gettempdir(), "jxlforge_about_contact")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, "about_%s__%s.png" % (_P.theme, _P.scheme))
if not win.grab().save(out):
    print("SAVE FAILED")

# 顺带打印关键几何，核对「作者名与网址左对齐」（两行第 1 列的 x 应相同）。
print("--- head widgets (geometry) ---")
for lab in win.about_tab.findChildren(QLabel):
    t = lab.text()
    if "href" in t or "Xiaofeng496" in t or "作者" in t or "项目主页" in t:
        r = lab.geometry()
        print("x=%-4d y=%-4d w=%-4d  %r" % (r.x(), r.y(), r.width(), t[:60]))
print("saved:", out)
# 先走一遍正常的事件循环把 deleteLater 排空，再硬退：直接 os._exit 会跳过
# Qt 的清理，在仍存活的 C++ 对象上直接杀解释器会段错误。
win.close()
win.deleteLater()
pump(0.4)
os._exit(0)
