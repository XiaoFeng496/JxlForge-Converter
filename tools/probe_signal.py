# -*- coding: utf-8 -*-
"""Probe how PySide6 resolves overloaded QComboBox signals on connect."""
from PySide6.QtWidgets import QApplication, QComboBox

app = QApplication([])
c = QComboBox()
c.addItems(["a", "b", "c"])
log = []


def make(tag):
    def f(v):
        log.append((tag, type(v).__name__))
    return f


# Default (no overload specified) connection.
c.currentIndexChanged.connect(make("changed"))
c.activated.connect(make("activated"))
print("default connect: OK")
c.setCurrentIndex(2)
c.activated.emit(1)
print("after emit(1):", log)

# Explicit int overload syntax.
try:
    c.currentIndexChanged[int].connect(make("changed_int"))
    c.activated[int].connect(make("activated_int"))
    print("int overload syntax: OK")
except Exception as e:
    print("int overload syntax: ERR", repr(e))

c.setCurrentIndex(0)
c.activated.emit(2)
print("final log:", log)
