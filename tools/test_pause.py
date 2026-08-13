# -*- coding: utf-8 -*-
"""Headless smoke test: verify the thumbnail decode batch is paused while the
input list is being interacted with (rubber-band selection / drag), and resumed
afterwards. No pixmaps are created, so it is safe under the offscreen platform.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from libjxl_gui.main_window import InputListWidget


class DummyMainWindow:
    """Mirrors the pause/resume bookkeeping of MainWindow without the heavy UI."""
    def __init__(self):
        self._thumb_timer = None
        self._thumb_queue = [1, 2, 3]  # pretend there is work left
        self._thumb_paused = False
        self._fired = 0

    def _make_timer(self):
        t = QTimer()
        t.timeout.connect(self._tick)
        return t

    def _tick(self):
        self._fired += 1

    def _pause_thumb_batch(self):
        timer = getattr(self, "_thumb_timer", None)
        if timer is not None:
            timer.stop()
            self._thumb_timer = None
        self._thumb_paused = True

    def _resume_thumb_batch(self):
        if not getattr(self, "_thumb_paused", False):
            return
        self._thumb_paused = False
        queue = getattr(self, "_thumb_queue", None)
        if queue and getattr(self, "_thumb_timer", None) is None:
            self._thumb_timer = self._make_timer()
            self._thumb_timer.start(0)


def main():
    app = QApplication(sys.argv)
    mw = DummyMainWindow()
    mw._thumb_timer = mw._make_timer()
    mw._thumb_timer.start(0)

    lst = InputListWidget(mw)
    lst.show()

    # Let the timer fire a bit (simulating decoding in the background).
    QTimer.singleShot(20, lambda: None)
    app.processEvents()
    fired_before = mw._fired
    assert fired_before > 0, "timer should have fired before interaction"

    # Simulate a mouse press -> decoding must pause.
    press = QMouseEvent(QEvent.MouseButtonPress,
                        __import__("PySide6").QtCore.QPointF(5, 5),
                        __import__("PySide6").QtCore.Qt.LeftButton,
                        __import__("PySide6").QtCore.Qt.LeftButton,
                        __import__("PySide6").QtCore.Qt.NoModifier)
    lst.mousePressEvent(press)
    assert mw._thumb_paused is True, "batch should be paused after press"
    assert mw._thumb_timer is None, "timer should be stopped after press"

    paused_fired = mw._fired
    # Process events while "holding" the button: timer must NOT fire.
    app.processEvents()
    assert mw._fired == paused_fired, "decode must not run while interacting"

    # Simulate mouse release -> resume.
    release = QMouseEvent(QEvent.MouseButtonRelease,
                          __import__("PySide6").QtCore.QPointF(5, 5),
                          __import__("PySide6").QtCore.Qt.LeftButton,
                          __import__("PySide6").QtCore.Qt.NoButton,
                          __import__("PySide6").QtCore.Qt.NoModifier)
    lst.mouseReleaseEvent(release)
    assert mw._thumb_paused is False, "batch should resume after release"
    assert mw._thumb_timer is not None, "timer should restart after release"

    QTimer.singleShot(20, lambda: None)
    app.processEvents()
    assert mw._fired > paused_fired, "decode should resume after release"

    print("ALL_OK: pause/resume wiring works")


if __name__ == "__main__":
    main()
