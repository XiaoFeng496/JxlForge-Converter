# -*- coding: utf-8 -*-
"""最小对照实验：判定「下拉里闪电点第 1 项没反应」的归属。

本脚本**不含本项目任何代码**——只有一个纯原生 ``QComboBox``。用途是判定
那个 bug 到底是 Qt 原生 popup 的行为（``QComboBoxPrivateContainer`` 的
"吞掉打开 popup 那一击的 release" 保护），还是我们 ``NoFlickerComboBox``
改出来的。

跑法：双击本脚本，或 ``python tools\\probe_native_combo_first_row.py``。

操作（下拉共 3 项，当前选中第 2 项）：
  A. 点开下拉 → 鼠标几乎不动 → 闪电点第 1 项
  B. 点开下拉 → 闪电点第 3 项
  C. 点开下拉 → 停 1 秒 → 点第 1 项
  D. 点开下拉 → 按住第 1 项 1 秒再松开
  E. 点开下拉 → 鼠标先划到别处再回到第 1 项 → 闪电点

每次点击后底部标签会打出：按住时长 → 目标第几项 → 是否生效。
A 若在原生 QComboBox 上同样失败，说明这是 Qt 行为，与本项目代码无关。

## ✅ 实测结论（2026-09-01，Windows 11 / PySide6 6.11.1）

**这个 bug 是 Qt 原生 QComboBox 的行为，不是本项目的缺陷，不要再去修。**

纯原生对照组复现结果：A（闪电点第 1 项）频繁失败；B（闪电点第 3 项）、
C（停 1 秒再点）、D（按住 1 秒）、E（划开再回来点）均 100% 成功。同一现象
在 libjxl GUI 的**其他原生下拉框**上同样存在，越靠下的项越难触发——因为鼠标
划过去的停留时间已经够长了。

机制：``QComboBoxPrivateContainer`` 会吞掉「打开 popup 那一击」的
mouse release（``initialClickPosition`` + ``startDragDistance`` 的位置判定，
外加与 ``doubleClickInterval`` 同量级、约 400~500ms 的时间判定），以防用户
点开下拉的那一下被误判成选中了某项。第 1 项紧贴 combo 下沿，与打开 popup 的
点击位置重叠，且是鼠标位移最小的目标，所以只有它容易命中这个保护窗口。

**保留本脚本的价值**：它是上述结论的可复现证据。将来若有人再报「下拉点击没
反应」，先跑它——若在纯原生控件上也能复现，就直接结案，不要再去给
``NoFlickerComboBox`` 打补丁（历史上为此白打了五轮，全部无效并已撤回）。
"""

import sys
import time

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QLabel, QVBoxLayout,
                               QWidget)


class ClickTracker(QObject):
    """全局过滤鼠标 press/release，把每次点击的「按住时长 / 目标项 /
    是否生效」打到标签上。

    用全局 filter 而不是装在 combo 上，是因为 popup 是另一个顶层窗口，
    而且 Qt 6 每次 ``hidePopup`` 都会 ``deleteLater`` 它——逐个安装太脆。
    """

    def __init__(self, combo, label, parent=None):
        super().__init__(parent)
        self._combo = combo
        self._label = label
        self._t0 = None
        self._index_before = combo.currentIndex()

    def eventFilter(self, _obj, event):
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress:
            self._t0 = time.perf_counter()
            self._index_before = self._combo.currentIndex()
            return False
        if etype == QEvent.Type.MouseButtonRelease and self._t0 is not None:
            hold_ms = (time.perf_counter() - self._t0) * 1000.0
            self._t0 = None
            row = self._target_row(event)
            # 值的提交发生在 release 之后，稍等一拍再判定
            QTimer.singleShot(
                150, lambda h=hold_ms, r=row: self._report(h, r))
            return False
        return False

    def _target_row(self, event):
        """点击落在下拉第几行；落在下拉之外返回 ``None``。"""
        view = self._combo.view()
        viewport = view.viewport()
        try:
            gp = event.globalPosition().toPoint()
        except AttributeError:  # Qt 5 回退
            gp = event.globalPos()
        local = viewport.mapFromGlobal(gp)
        if not viewport.rect().contains(local):
            return None
        index = view.indexAt(local)
        return index.row() if index.isValid() else None

    def _report(self, hold_ms, row):
        if row is None:
            self._label.setText(
                "按住 %d ms → 点在了下拉之外（打开/关闭下拉的那一击，不计）"
                % hold_ms)
            return
        before, after = self._index_before, self._combo.currentIndex()
        if before == row:
            verdict = "・点的就是当前项，请换另一项测"
        elif after == row:
            verdict = "✅ 生效"
        else:
            verdict = "❌ 没反应"
        self._label.setText(
            "按住 %d ms → 目标第 %d 项 → %s（%d → %d）"
            % (hold_ms, row, verdict, before, after))


def main():
    app = QApplication(sys.argv)
    window = QWidget()
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel(
        "这是<b>纯原生 QComboBox</b>，不含本项目任何代码。<br>"
        "请在下面的下拉里做这几组（当前选中第 2 项）：<br>"
        "A 闪电点<b>第 1 项</b>　B 闪电点<b>第 3 项</b>　"
        "C 停 1 秒点第 1 项　D 按住第 1 项 1 秒　E 先划走再回来点第 1 项"))
    combo = QComboBox()
    combo.addItems(["A 第一项", "B 第二项（当前）", "C 第三项"])
    combo.setCurrentIndex(1)
    layout.addWidget(combo)
    label = QLabel("等待点击…")
    layout.addWidget(label)
    tracker = ClickTracker(combo, label)
    app.installEventFilter(tracker)
    window.setWindowTitle("原生 QComboBox 连击对照实验")
    window.resize(460, 200)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
