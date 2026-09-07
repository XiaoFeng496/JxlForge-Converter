# -*- coding: utf-8 -*-
"""Headless probe: verify the default window is pinned to a 6x3 fit even though
the actions tab is intrinsically wider."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from PySide6.QtWidgets import QApplication
from jxlforge import main_window as mw

app = QApplication([])
window = mw.MainWindow()
window.show()
app.processEvents()
# Mimic showEvent -> _fit_window_to_grid (singleShot). primaryScreen is None
# headless, so it takes the resize() branch but still applies setMinimumSize.
window._fit_window_to_grid()
app.processEvents()

grid = mw.GRID_SIZES.get("缩略图")
print("6x3 content width (no frame):", 6 * grid.width() + 17 + mw.FIT_EXTRA_W)
print("window width after fit :", window.width())
print("window min width        :", window.minimumWidth())
print("window height after fit :", window.height())
print("window min height       :", window.minimumHeight())
print("columns visible ~       :", window.width() // grid.width())
