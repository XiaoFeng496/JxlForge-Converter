# -*- coding: utf-8 -*-
"""Headless checks for the custom smooth edge auto-scroll in InputListWidget.

We use a plain-text QListWidget (no thumbnails / QPixmap) so the test runs under
the offscreen platform plugin, where creating QPixmap crashes. The auto-scroll
logic itself never touches QPixmap, so this exercises the real code paths.

The tick's viewport scroll is verified against a MockScrollBar to avoid two
offscreen quirks: (1) QCursor.pos() is uncontrollable, and (2) QListWidget takes
over its internal scrollbar's range from the content height. Neither affects the
real desktop behavior.
"""
import os
import sys

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QApplication,
    QListWidgetItem,
    QAbstractItemView,
    QScrollBar,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from libjxl_gui.main_window import (
    InputListWidget,
    AUTO_SCROLL_INTERVAL,
    AUTO_SCROLL_MARGIN,
    AUTO_SCROLL_MAX_SPEED,
)

app = QApplication.instance() or QApplication(sys.argv)


class DummyMW:
    def _pause_thumb_batch(self):
        pass

    def _resume_thumb_batch(self):
        pass


class MockScrollBar(QScrollBar):
    """A real QScrollBar subclass so internal C++ callers stay type-safe, but
    with a controlled value range so the tick math can be asserted exactly
    (offscreen QListWidget otherwise takes over the scrollbar range)."""

    def __init__(self, v=0, mn=0, mx=2000, parent=None):
        super().__init__(parent)
        self._v = v
        self._mn = mn
        self._mx = mx

    def value(self):
        return self._v

    def minimum(self):
        return self._mn

    def maximum(self):
        return self._mx

    def setValue(self, v):
        self._v = max(self._mn, min(int(v), self._mx))


def main():
    w = InputListWidget(DummyMW())
    w.resize(400, 300)
    w.show()
    for i in range(100):
        w.addItem(QListWidgetItem("item %d" % i))

    checks = []

    # 1) Qt's built-in jumpy auto-scroll is disabled; our timer is wired.
    checks.append(("setAutoScroll(False)", w.hasAutoScroll() is False))
    checks.append(("timer exists", hasattr(w, "_auto_scroll_timer")))
    checks.append(("timer interval", w._auto_scroll_timer.interval() == AUTO_SCROLL_INTERVAL))
    checks.append(("speed init 0", w._auto_scroll_speed == 0.0))

    # 2) Edge distance -> speed direction + timer start/stop.
    vp_h = w.viewport().height()
    checks.append(("viewport has height", vp_h > 0))
    if vp_h > 0:
        w._update_auto_scroll_target(QPoint(10, 2))          # near top
        checks.append(("top edge -> negative speed", w._auto_scroll_speed < 0))
        checks.append(("top edge -> timer started", w._auto_scroll_timer.isActive()))
        w._update_auto_scroll_target(QPoint(10, vp_h - 2))   # near bottom
        checks.append(("bottom edge -> positive speed", w._auto_scroll_speed > 0))
        w._update_auto_scroll_target(QPoint(10, vp_h // 2))  # middle
        checks.append(("middle -> zero speed", w._auto_scroll_speed == 0.0))
        checks.append(("middle -> timer stopped", not w._auto_scroll_timer.isActive()))

    # 3) A tick scrolls by exactly the (small) speed -> smooth, not a jump.
    mock = MockScrollBar(500, 0, 2000)
    w.verticalScrollBar = lambda: mock
    w._auto_scroll_speed = -AUTO_SCROLL_MAX_SPEED  # scroll up
    w._auto_scroll_tick()
    checks.append(("tick scrolls up", mock.value() < 500))
    checks.append(("tick step == speed (smooth)", mock.value() == 500 - AUTO_SCROLL_MAX_SPEED))

    # 4) At the boundary the tick clamps instead of overshooting.
    mock._v = 0
    w._auto_scroll_speed = -AUTO_SCROLL_MAX_SPEED
    w._auto_scroll_tick()
    checks.append(("boundary clamp", mock.value() == 0))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS" if ok else "FAIL"), name)
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL_OK: custom smooth auto-scroll works")
    sys.exit(0)


if __name__ == "__main__":
    main()
