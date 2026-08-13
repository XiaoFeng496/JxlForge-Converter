# -*- coding: utf-8 -*-
"""Regression test for the thumbnail drag auto-scroll performance fix.

In the thumbnail (IconMode) grid, uniformItemSizes used to be OFF. That made
Qt recompute the geometry of EVERY item on each layout pass (O(n)). When you
drag a tile to the viewport edge, Qt's auto-scroll re-lays-out the whole list
every frame, which stutters badly with many images. Every thumbnail cell is
the same grid size, so uniformItemSizes MUST be True -> O(1) layout -> smooth
auto-scroll.

Note: PySide6 6.11.1 does not expose QAbstractItemView.setViewportUpdateMode
on QListWidget, so the fix relies on uniformItemSizes alone (the actual root
cause). This test locks that setting in.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from PySide6.QtWidgets import QApplication, QListWidget
from libjxl_gui import main_window as mw

# Build a headless QApplication once.
app = QApplication.instance() or QApplication(sys.argv[:1])
app.setApplicationName("test_drag_perf")

results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("OK  " if cond else "FAIL") + " - " + name)

window = mw.MainWindow()

# Thumbnail view: uniformItemSizes must be True (the O(1) layout fix).
window._on_view_changed("缩略图")
check("thumbnail: uniformItemSizes is True", window.input_list.uniformItemSizes() is True)
check("thumbnail: IconMode", window.input_list.viewMode() == QListWidget.IconMode)
check("thumbnail: InternalMove (drag-sort)",
      window.input_list.dragDropMode() == QListWidget.InternalMove)

# List view: still uniform (inherited True) and drag-select only.
window._on_view_changed("列表")
check("list: uniformItemSizes still True", window.input_list.uniformItemSizes() is True)
check("list: NoDragDrop (rubber-band select)",
      window.input_list.dragDropMode() == QListWidget.NoDragDrop)

# Round trip back to thumbnails keeps the fix.
window._on_view_changed("缩略图")
check("thumbnail (round-trip): uniformItemSizes is True",
      window.input_list.uniformItemSizes() is True)

print("")
if all(ok for _, ok in results):
    print("ALL_OK (%d checks passed)" % len(results))
else:
    print("SOME_FAILED (%d/%d passed)" % (sum(1 for _, ok in results if ok), len(results)))
    sys.exit(1)
