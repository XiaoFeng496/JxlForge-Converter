# -*- coding: utf-8 -*-
"""回归：预览解码分辨率不被压扁（修复「从预览窗口看图片分辨率变低」）。

策略：预览图默认按「原生分辨率」解码（不再为 fit 而缩小），fit 到窗口时永远是
「从大到小缩」，清晰无糊。仅对单边超过 _PREVIEW_OOM_CAP 的超巨图等比缩到上限
以内，作 OOM 保护。

断言：
1) 一般图片（单边 ≤ OOM cap）解码上限 = OOM cap，即原生解码（不被窗口大小压扁）。
2) OOM 硬上限不超过 _PREVIEW_OOM_CAP（防巨图吃光内存）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from jxlforge import main_window as mw

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
    # 新策略：解码上限 = OOM cap（原生分辨率解码），小窗也不被压扁。
    check("decode max-width == OOM cap (native decode)",
          mw_w is not None and mw_w == mw._PREVIEW_OOM_CAP)
    check("decode max-height == OOM cap (native decode)",
          mw_h is not None and mw_h == mw._PREVIEW_OOM_CAP)
    # OOM 硬上限防护
    check("decode max-width within OOM cap",
          mw_w is not None and mw_w <= mw._PREVIEW_OOM_CAP)
    check("decode max-height within OOM cap",
          mw_h is not None and mw_h <= mw._PREVIEW_OOM_CAP)
finally:
    mw._PreviewLoader = orig_loader

# 动作页同样：解码上限 = OOM cap（原生分辨率），不随窗口大小被压扁。
# 用一个 2500 宽的「宽屏」主窗验证动作页解码上限是 OOM cap 而非窗口衍生值。
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
    check("wide window decode max == OOM cap (native, not window-derived)",
          captured.get("max_w") is not None
          and captured["max_w"] == mw._PREVIEW_OOM_CAP)
finally:
    mw._PreviewLoader = orig_loader

print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
if failures:
    print("FAILED:")
    for f in failures:
        print("  - %s" % f)
    raise SystemExit(1)
print("ALL_OK")
