# -*- coding: utf-8 -*-
"""Generate README screenshots straight from the real UI.

为什么不用画示意图：README 的截图必须和用户实际看到的界面一致，手工拼图
必然会和真实控件布局漂移。这里直接构造 MainWindow、灌几个样例输入、抓
QWidget.grab()，所以图里的每一处文字/间距都是程序真实渲染的结果。

用法::

    python tools/shot_readme.py                 # 中英各一套，输出到 docs/images/
    python tools/shot_readme.py --lang zh_CN    # 只出中文
    python tools/shot_readme.py --real          # 用真实窗口（默认 offscreen）

坑位备注：
  * offscreen 下 isVisible() 恒 False、minimumWidth() 恒 0，但 grab() 走的是
    QWidget::render()，不依赖合成器，所以抓图是有效的（本项目已实测）。
  * 缩略图由 QThreadPool 异步产出，必须反复 processEvents 等它落盘，否则截图
    里输入列表是空的。这里固定等 --wait 秒（默认 3.5s）。
  * QSettings 必须重定向到临时目录：否则会把用户的真实 ini 覆盖成
    「样例输入 + 两个动作」，下次启动程序就会莫名其妙带着这些动作。
"""

import argparse
import os
import sys
import tempfile
import time

# 必须在 import PySide6 之前设置平台插件。
for _i, _a in enumerate(sys.argv):
    if _a == "--real":
        os.environ.pop("QT_QPA_PLATFORM", None)
        break
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# 抓图前要展示的动作（用 ACTION_TYPES 里的中文 ID），挑参数控件类型不同的两个，
# 这样「动作」页能同时展示数值行 + 下拉行两种布局。
SHOT_ACTIONS = ["调整大小", "锐化"]
SHOT_TABS = [("input", "输入"), ("actions", "动作"), ("output", "输出"),
             ("status", "状态"), ("settings", "设置")]

# 语言码 → 文件名后缀。三版 README 各配自己语言的界面图。
_SUFFIX = {"zh_CN": "zh", "zh_TW": "tw", "en_US": "en"}
LANGS = ["zh_CN", "zh_TW", "en_US"]


def _make_samples(folder):
    """造几张尺寸/比例不同的样例图，让输入列表看起来像真实使用场景。"""
    from PIL import Image, ImageDraw

    specs = [
        ("sample_landscape.png", 1920, 1200, (58, 122, 200)),
        ("sample_portrait.png", 1080, 1620, (196, 108, 72)),
        ("sample_square.png", 1400, 1400, (86, 156, 108)),
    ]
    paths = []
    for name, w, h, base in specs:
        img = Image.new("RGB", (w, h), base)
        draw = ImageDraw.Draw(img)
        # 一点几何图形，避免纯色缩略图看起来像加载失败。
        for i in range(6):
            pad = int(min(w, h) * (0.08 + i * 0.07))
            draw.rectangle([pad, pad, w - pad, h - pad],
                           outline=(255, 255, 255), width=max(2, w // 300))
        draw.ellipse([w * 0.32, h * 0.32, w * 0.68, h * 0.68],
                     fill=(245, 245, 240))
        path = os.path.join(folder, name)
        img.save(path)
        paths.append(path)
    return paths


def _pump(app, seconds):
    """跑满 seconds 秒的事件循环（缩略图线程要靠它回填）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def capture(lang, out_dir, wait, size, tabs):
    from jxlforge import i18n
    from jxlforge.main_window import MainWindow

    if lang and lang != "zh_CN":
        i18n.set_language(i18n.resolve_language(lang))

    app = QApplication.instance() or QApplication(sys.argv)
    mw = MainWindow()
    mw.resize(*size)
    mw.show()
    _pump(app, 1.0)

    tmp = tempfile.mkdtemp(prefix="shots_")
    mw._add_input_paths(_make_samples(tmp))
    for action in SHOT_ACTIONS:
        mw._add_action_by_id(action)
    mw.tabs.setCurrentIndex(mw._input_tab_index)
    _pump(app, wait)

    # 不能只取语言码前半段：zh_CN 和 zh_TW 都会得到 "zh"，繁体截图会覆盖简体。
    suffix = _SUFFIX.get(lang or "zh_CN", (lang or "zh").split("_")[0].lower())
    written = []
    # 标签页顺序固定（输入/动作/输出/状态/设置），直接按下标取，
    # 不去匹配 tabText——切到英文后标题变了，字符串匹配会失效。
    for idx, (key, _label) in enumerate(SHOT_TABS):
        if idx >= mw.tabs.count() or key not in tabs:
            continue
        mw.tabs.setCurrentIndex(idx)
        _pump(app, 0.6)
        path = os.path.join(out_dir, "ui_%s_%s.png" % (key, suffix))
        pix = mw.grab()
        if not pix.save(path):
            raise RuntimeError("failed to save %s" % path)
        written.append((path, pix.width(), pix.height()))
    mw.close()
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", action="append",
                        help="要抓的语言，可重复；默认 zh_CN + en_US")
    parser.add_argument("--out", default=os.path.join(_REPO, "docs", "images"))
    parser.add_argument("--wait", type=float, default=3.5,
                        help="等缩略图线程落盘的秒数")
    parser.add_argument("--size", default="880x640")
    parser.add_argument("--tabs", default="input,actions,output",
                        help="要抓的标签页，逗号分隔：input,actions,output,status,settings；"
                             "默认只抓 README 用到的三页，传 all 抓全部")
    parser.add_argument("--real", action="store_true",
                        help="用真实窗口抓图（默认 offscreen）")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    # 隔离 QSettings：绝不把样例输入/动作写进用户真实 ini。
    ini_dir = tempfile.mkdtemp(prefix="shots_ini_")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, ini_dir)

    w, h = (int(x) for x in args.size.lower().split("x"))
    langs = args.lang or LANGS
    if args.tabs.strip().lower() == "all":
        tabs = {k for k, _ in SHOT_TABS}
    else:
        tabs = {t.strip() for t in args.tabs.split(",") if t.strip()}
    unknown = tabs - {k for k, _ in SHOT_TABS}
    if unknown:
        raise SystemExit("unknown tab(s): %s" % ", ".join(sorted(unknown)))
    for lang in langs:
        for path, pw, ph in capture(lang, args.out, args.wait, (w, h), tabs):
            print("[OK] %s  %dx%d" % (os.path.relpath(path, _REPO), pw, ph))
    print("platform =", os.environ.get("QT_QPA_PLATFORM", "<native>"))


if __name__ == "__main__":
    main()
