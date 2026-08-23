# -*- coding: utf-8 -*-
"""回归：预览解码分辨率不被压扁（修复「从预览窗口看图片分辨率变低」）。

根因：_start_loader 把解码上限的「下限」钉死 512，导致小窗口预览时图被压到
<=512 而明显变糊；fit 只会缩小不会放大，于是预览出来就是一张缩小图的尺寸。

断言：
1) 解码上限下限是「窗口尺寸 × DPR」（而非 512）；小窗口也不压扁。
2) 解码上限有合理余量（窗口 × _PREVIEW_DECODE_SCALE）。
3) 硬上限不超过 _PREVIEW_DECODE_CAP（防巨图 OOM）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


# 捕获 _start_loader 传给 _PreviewLoader 的 max_w / max_h。
captured = {}


class _StubLoader:
    def __init__(self, path, max_w, max_h):
        captured["max_w"] = max_w
        captured["max_h"] = max_h
        # 占位信号对象，支持 .connect()
        class _Sig:
            def connect(self, *a, **k):
                pass
        self.loaded = _Sig()
        self.failed = _Sig()
        self.finished = _Sig()

    def start(self):
        pass

    def deleteLater(self):
        pass


orig_loader = mw._PreviewLoader
mw._PreviewLoader = _StubLoader

try:
    # 用一个足够小尺寸的 dialog 模拟「小窗口预览」场景：
    # 不传 parent 时 Qt 给默认尺寸；这里直接构造并 resize 到 400x300，
    # 验证即使这么小的窗口，解码上限也不会被压到 512 以下。
    dlg = mw.PreviewDialog.__new__(mw.PreviewDialog)
    # 手动设最小属性，避免 __init__ 跑真实 IO；我们只测 _start_loader 的计算。
    dlg.path = "dummy.png"
    dlg._closed = False
    dlg._loader = None
    # 伪装成已 resize 的小窗
    from PySide6.QtCore import QSize

    class _FakeWin:
        def __init__(self, w, h):
            self._w, self._h = w, h

        def width(self):
            return self._w

        def height(self):
            return self._h

        def devicePixelRatio(self):
            return 1.0

        def _on_preview_loaded(self, *a, **k):
            pass

        def _on_preview_failed(self, *a, **k):
            pass

    fake = _FakeWin(400, 300)
    # 把 _start_loader 绑到 fake 上执行（它只用到 self.width/height/devicePixelRatio）
    mw.PreviewDialog._start_loader(fake, "dummy.png")

    mw_w = captured.get("max_w")
    mw_h = captured.get("max_h")
    check("decoded max captured", mw_w is not None and mw_h is not None)
    # 关键不变量：下限 = 窗口尺寸（400×300 × DPR1），绝不压到 512。
    check("decode max-width >= window width (not clamped to 512)",
          mw_w is not None and mw_w >= 400)
    check("decode max-height >= window height (not clamped to 512)",
          mw_h is not None and mw_h >= 300)
    # 余量：应 >= 窗口 × 1.5（_PREVIEW_DECODE_SCALE）
    check("decode max-width has fit margin (>= win*scale)",
          mw_w is not None and mw_w >= int(400 * mw._PREVIEW_DECODE_SCALE) - 1)
    check("decode max-height has fit margin (>= win*scale)",
          mw_h is not None and mw_h >= int(300 * mw._PREVIEW_DECODE_SCALE) - 1)
    # 硬上限防护
    check("decode max-width within cap",
          mw_w is not None and mw_w <= mw._PREVIEW_DECODE_CAP)
    check("decode max-height within cap",
          mw_h is not None and mw_h <= mw._PREVIEW_DECODE_CAP)
finally:
    mw._PreviewLoader = orig_loader

# 动作页同样：动态上限应随窗口尺寸放大，而非钉死 1400。
# 用一个 2500 宽的「宽屏」主窗验证动作页解码上限随之变大。
captured.clear()
mw._PreviewLoader = _StubLoader


class _FakeWin2:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h

    def devicePixelRatio(self):
        return 1.0

    def _on_preview_loaded(self, *a, **k):
        pass

    def _on_preview_failed(self, *a, **k):
        pass


try:
    fake2 = _FakeWin2(2500, 1400)
    mw.PreviewDialog._start_loader(fake2, "dummy.png")
    check("wide window decode max-width scales up (not stuck at 1400)",
          captured.get("max_w") is not None
          and captured["max_w"] > 1400)
finally:
    mw._PreviewLoader = orig_loader

print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
if failures:
    print("FAILED:")
    for f in failures:
        print("  - %s" % f)
    raise SystemExit(1)
print("ALL_OK")
