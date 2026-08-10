# -*- coding: utf-8 -*-
"""Headless tests for the two UI-polish fixes:

1. The settings tab's CPU-priority combo is sized to its widest label instead
   of a hard-coded 120 px, so it is visibly narrower.
2. The custom-folder history popup is pre-warmed once after the window is
   shown (and Windows menu entrance animations are off), so the FIRST click on
   the drop-down arrow is as fast as every later one.
"""

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui.main_window import MainWindow

failures = []


def check(name, ok):
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


window = MainWindow()

# ---- 1. CPU-priority combo width -----------------------------------------
combo = window.cpu_priority_combo
labels = [combo.itemText(i) for i in range(combo.count())]
fm = combo.fontMetrics()
widest = max(fm.horizontalAdvance(t) for t in labels)
check("combo width follows the widest label",
      combo.width() == widest + 34)
check("combo is narrower than the old 120 px", combo.width() < 120)
check("combo still wide enough for its text", combo.width() >= widest + 20)
check("combo width is fixed (min == max)",
      combo.minimumWidth() == combo.maximumWidth() == combo.width())
check("all five priorities still present",
      labels == ["空闲", "低于正常", "正常", "高于正常", "高"])
check("default selection unchanged (below_normal)",
      combo.currentData() == "below_normal")

# ---- 2. Menu animation disabled ------------------------------------------
check("menu slide animation disabled",
      not QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateMenu))
check("menu fade animation disabled",
      not QApplication.isEffectEnabled(Qt.UIEffect.UI_FadeMenu))

# ---- 3. Pre-warm is scheduled on first show ------------------------------
check("not warmed before the window is shown", window._menu_warmed is False)
window.show()
check("warm-up scheduled on first show", window._menu_warmed is True)
# Let the queued singleShot(0) callbacks run.
QTimer.singleShot(150, _app.quit)
_app.exec()

# ---- 4. Warm-up left the popup fully usable ------------------------------
menu = window.folder_menu
check("popup is not visible after warm-up", not menu.isVisible())
check("WA_DontShowOnScreen was reset",
      not menu.testAttribute(Qt.WA_DontShowOnScreen))

window._folder_history = [r"C:\Users\Demo\Pictures", r"D:\out"]
window._rebuild_folder_menu()
window._open_folder_menu()
check("popup opens after the warm-up", menu.isVisible())
check("popup still lists every history row", len(menu.actions()) == 2)
menu.hide()

# Calling the warm-up twice must stay harmless (idempotent, no exception).
try:
    window._prewarm_folder_menu()
    window._prewarm_folder_menu()
    ok = not menu.isVisible()
except Exception as exc:  # pragma: no cover - regression guard
    ok = False
    print("   exception: %r" % (exc,))
check("warm-up is idempotent and never shows the popup", ok)

window._open_folder_menu()
check("popup still opens after repeated warm-ups", menu.isVisible())
menu.hide()

print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
