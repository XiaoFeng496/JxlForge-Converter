# -*- coding: utf-8 -*-
"""Headless benchmark (offscreen, no QPixmap) to isolate what makes the
thumbnail list's rubber-band auto-scroll janky. We replicate the EXACT view
settings used in main_window.py (IconMode + Snap + Adjust + uniformItemSizes
True + gridSize + spacing 0 + ExtendedSelection) with plain text items and
measure (a) per-step scroll cost and (b) rubber-band selection cost."""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QRect, QSize, QItemSelectionModel
from PySide6.QtWidgets import QApplication, QListWidget

N = 3000


def apply_settings(lst):
    lst.setViewMode(QListWidget.IconMode)
    lst.setMovement(QListWidget.Snap)
    lst.setResizeMode(QListWidget.Adjust)
    lst.setUniformItemSizes(True)
    lst.setGridSize(QSize(122, 154))
    lst.setSpacing(0)
    lst.setSelectionMode(QListWidget.ExtendedSelection)


def main():
    app = QApplication(sys.argv)
    lst = QListWidget()
    apply_settings(lst)
    lst.resize(800, 600)
    for i in range(N):
        lst.addItem("image_%05d.png" % i)
    lst.show()
    app.processEvents()

    maxv = lst.verticalScrollBar().maximum()
    print("items=%d  scrollMax=%d" % (N, maxv))

    # (a) Scroll cost: simulate auto-scroll by stepping the scrollbar.
    t0 = time.perf_counter()
    steps = 0
    v = 0
    while v < maxv:
        v += 60
        lst.verticalScrollBar().setValue(min(v, maxv))
        app.processEvents()
        steps += 1
    t1 = time.perf_counter()
    per = (t1 - t0) / max(1, steps) * 1000.0
    print("SCROLL(uniform): total=%.1fms steps=%d perStep=%.2fms" %
          ((t1 - t0) * 1000, steps, per))

    # (b) Rubber-band selection cost: grow a selection rect like Qt does.
    lst.verticalScrollBar().setValue(0)
    app.processEvents()
    t0 = time.perf_counter()
    steps = 0
    for k in range(1, 40):
        rect = QRect(0, 0, 800, 40 * k)
        lst.setSelection(rect, QItemSelectionModel.Select)
        app.processEvents()
        steps += 1
    t1 = time.perf_counter()
    per = (t1 - t0) / steps * 1000.0
    print("SELECT: total=%.1fms steps=%d perStep=%.2fms" %
          ((t1 - t0) * 1000, steps, per))

    # (c) Selection over a HUGE rect (auto-scroll reveals everything).
    t0 = time.perf_counter()
    lst.setSelection(QRect(0, 0, 800, 600 * 20), QItemSelectionModel.Select)
    app.processEvents()
    t1 = time.perf_counter()
    print("SELECT_ALL_BIG: %.1fms" % ((t1 - t0) * 1000))

    # (d) Same scroll cost but WITHOUT uniformItemSizes.
    lst2 = QListWidget()
    apply_settings(lst2)
    lst2.setUniformItemSizes(False)
    lst2.resize(800, 600)
    for i in range(N):
        lst2.addItem("image_%05d.png" % i)
    lst2.show()
    app.processEvents()
    maxv2 = lst2.verticalScrollBar().maximum()
    t0 = time.perf_counter()
    steps = 0
    v = 0
    while v < maxv2:
        v += 60
        lst2.verticalScrollBar().setValue(min(v, maxv2))
        app.processEvents()
        steps += 1
    t1 = time.perf_counter()
    per = (t1 - t0) / max(1, steps) * 1000.0
    print("SCROLL(noUniform): total=%.1fms steps=%d perStep=%.2fms" %
          ((t1 - t0) * 1000, steps, per))


if __name__ == "__main__":
    main()
