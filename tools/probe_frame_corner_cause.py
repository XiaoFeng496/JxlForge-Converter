# -*- coding: utf-8 -*-
"""真机探针：圆角边框「四角缺口」的成因与候选改法对照（纯 Qt 控件）。

必须真机（不能 offscreen）跑，否则 uxtheme 不参与绘制：

    python tools/probe_frame_corner_cause.py

结论（2026-10-30 实测，150% 缩放 + windows11 样式）：
- QPlainTextEdit / QTextEdit 四角完整；QListWidget / QListView / QTreeView 四角
  被内容底色方方正正地盖住 —— 差别不在样式，而在**有没有独立子视口**。
- 只需关掉子视口的 ``autoFillBackground``，四角像素就与文本控件逐字节一致；
  套 QSS border-radius、或用 QFrame 包一层都不行（见下方候选）。
"""
import sys

from PySide6.QtWidgets import (
    QApplication, QFrame, QListView, QListWidget, QPlainTextEdit, QTextEdit,
    QTreeView, QVBoxLayout, QWidget,
)

S = 6


class MarginedList(QListWidget):
    """候选 E：给子视口加 2px 边距，让出圆弧的位置（会挤压内容，未采用）。"""

    def __init__(self, margins):
        super().__init__()
        self.setViewportMargins(*margins)


def tl(img, s=S):
    return [" ".join("%02x" % img.pixelColor(x, y).red() for x in range(s))
            for y in range(s)]


def left_edge(img):
    y = img.height() // 2
    return " ".join("%02x" % img.pixelColor(x, y).red() for x in range(6))


def report(name, w):
    img = w.grab().toImage()
    print("-" * 72)
    print("%-34s %dx%d dpr=%s" % (name, img.width(), img.height(),
                                  img.devicePixelRatio()))
    for r in tl(img):
        print("   ", r)
    print("    直边:", left_edge(img))


def make_list():
    w = QListWidget()
    w.addItems(["alpha", "beta", "gamma"])
    return w


def main():
    app = QApplication(sys.argv)
    host = QWidget()
    lay = QVBoxLayout(host)

    items = []

    def add(label, widget):
        lay.addWidget(widget)
        items.append((label, widget))

    add("1 QListWidget 基线", make_list())

    b = make_list()
    b.viewport().setAutoFillBackground(False)
    add("2 QListWidget + 关子视口自动底色", b)

    c = make_list()
    c.setStyleSheet("QListWidget { border: 1px solid #383838; "
                    "border-radius: 4px; }")
    add("3 候选 QSS border-radius", c)

    wrapper = QFrame()
    wrapper.setFrameShape(QFrame.StyledPanel)
    wl = QVBoxLayout(wrapper)
    wl.setContentsMargins(0, 0, 0, 0)
    inner = make_list()
    inner.setFrameShape(QFrame.NoFrame)
    wl.addWidget(inner)
    add("4 候选 QFrame 包 NoFrame 列表", wrapper)

    add("5 候选 子视口 2px 边距", MarginedList((2, 2, 2, 2)))

    add("6 QListView", QListView())
    add("7 QTreeView", QTreeView())
    add("8 参照 QPlainTextEdit", QPlainTextEdit())
    add("9 参照 QTextEdit", QTextEdit())

    host.resize(420, 1400)
    host.show()
    app.processEvents()
    app.processEvents()

    print("app.style() =", app.style().objectName())
    for label, w in items:
        report(label, w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
