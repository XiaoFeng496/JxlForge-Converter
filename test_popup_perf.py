# -*- coding: utf-8 -*-
"""Headless tests for the two UI-polish fixes:

1. The settings tab's CPU-priority combo is sized to its widest label instead
   of a hard-coded 120 px, so it is visibly narrower.
2. The custom-folder history popup is pre-warmed OFF the startup critical
   path (on first hover of the drop-down arrow, plus an idle fallback), and
   Windows menu entrance animations are off -- so the FIRST click on the
   arrow is as fast as every later one WITHOUT making application startup
   stutter.
"""

import sys

from PySide6.QtCore import Qt, QTimer, QEvent
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

# ---- 3. Warm-up is DEFERRED off the startup path -------------------------
check("not warmed before the window is shown", window._menu_warmed is False)
window.show()
# Crucially, showing the window must NOT pay the warm-up cost synchronously:
# the flag stays False and only an idle fallback timer is armed.
check("warm-up NOT paid at show() (startup stays instant)",
      window._menu_warmed is False)
check("idle fallback timer armed on first show",
      window._warm_fallback_scheduled is True)

# ---- 4. Hover on the arrow triggers the warm-up --------------------------
hover = QEvent(QEvent.Enter)
window.eventFilter(window.custom_folder_dropdown, hover)
# _maybe_prewarm defers the real work one loop turn; let it run.
QTimer.singleShot(80, _app.quit)
_app.exec()

check("hover triggered the warm-up", window._menu_warmed is True)
menu = window.folder_menu
check("popup is not visible after warm-up", not menu.isVisible())
check("WA_DontShowOnScreen was reset",
      not menu.testAttribute(Qt.WA_DontShowOnScreen))

# ---- 5. Warm-up left the popup fully usable ------------------------------
window._folder_history = [r"C:\Users\Demo\Pictures", r"D:\out"]
window._rebuild_folder_menu()
window._open_folder_menu()
check("popup opens after the warm-up", menu.isVisible())
check("popup still lists every history row", len(menu.actions()) == 2)
menu.hide()

# Calling the warm-up twice (hover again) must stay harmless.
try:
    window._maybe_prewarm()
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
