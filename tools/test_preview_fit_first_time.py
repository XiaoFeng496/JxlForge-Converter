# -*- coding: utf-8 -*-
"""回归：放大后点「适应窗口」第一次就正确，不需点两次。

根因：PreviewScroll.fit() 在放大后调用时，viewport 的最终尺寸尚未被祖先 layout
异步投递，导致第一次 fit 拿到偏小的 viewport 而缩得偏小（「比适应窗口小一点」），
第二次调用时布局已全部落定才正确。

修复：fit() 在真正 fitInView 前，沿祖先链 activate 所有 layout 并 pump 一拍
QApplication.processEvents()，强制 viewport 拿到最终尺寸，第一次即正确。

断言：
1) 放大后第一次 fit 的缩放系数 == 第二次 fit 的缩放系数（不需点两次）。
2) fit 后图片完整落在 viewport 内（fitInView 语义：不裁剪）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtCore import Qt

app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui.main_window import PreviewScroll

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


def make_scroll(w, h):
    scroll = PreviewScroll(None)
    # 给一个确定尺寸，避免 offscreen 下 viewport 为 0。
    scroll.resize(w, h)
    pix = QPixmap(2560, 1440)
    pix.fill(QColor(128, 128, 128))
    scroll.set_pixmap(pix)
    scroll._pixmap_item.setTransformationMode(Qt.SmoothTransformation)
    scroll.show()
    # 多 pump 几拍，让 offscreen 平台下 viewport 尺寸 settle 到最终值
    # （真实桌面环境 viewport 在 show 后即稳定，这里只是兼容 headless）。
    for _ in range(5):
        app.processEvents()
    return scroll


# 构造一个宽屏预览区（模拟大图 + 大预览区）
scroll = make_scroll(900, 600)
check("scroll visible", scroll.isVisible())

# 先放大（模拟用户点过「放大 +」）
scroll.zoom(4.0)
# 让 zoom 后的布局状态完全 settle（模拟用户稳定状态下点适应窗口）
for _ in range(5):
    app.processEvents()
zoomed = scroll.transform().m11()
check("zoom applied (>1)", zoomed > 1)

# 第一次 fit：放大后点「适应窗口」
scroll.fit()
app.processEvents()
first = scroll.transform().m11()

# 再次 fit（模拟再点一次）
scroll.fit()
app.processEvents()
second = scroll.transform().m11()

# 关键不变量：第一次 fit 的缩放系数应与第二次一致
# （旧 bug：第一次偏小，需要再点一次才正确）
check("first fit scale == second fit scale (no double-click needed)",
      abs(first - second) < 1e-6)

# fit 后图片应完整 fit 进 viewport（不放大超过 fit-in-view 语义）。
# 用 viewport 的 rect（而非 width，后者含 frame 边距）估算期望缩放系数，
# 允许 5% 容差（fitInView 会留极小余量）。
img_w, img_h = 2560, 1440
vp_rect = scroll.viewport().rect()
vp_w, vp_h = vp_rect.width(), vp_rect.height()
expected = min(vp_w / img_w, vp_h / img_h) if vp_w > 0 and vp_h > 0 else 0
check("fit scale matches fit-in-view (image fully visible, not cropped)",
      expected > 0 and abs(first - expected) / expected < 0.05)

print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
if failures:
    print("FAILED:")
    for f in failures:
        print("  - %s" % f)
    raise SystemExit(1)
print("ALL_OK")
