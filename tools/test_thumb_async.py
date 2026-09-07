# -*- coding: utf-8 -*-
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""Headless regression: thumbnail decoding must run off the GUI thread (Phase 2).

The old code decoded every thumbnail synchronously inside a GUI-thread timer
tick, which froze the UI when a large JXL / AVIF / PNG was dragged in. Now the
decode happens in a QThreadPool worker (producing a QImage, never a QPixmap),
and the GUI thread only does QPixmap.fromImage on the result. This test checks:

  1. The async pipeline actually fills items (DecorationRole becomes a real,
     non-placeholder pixmap) without blocking.
  2. A stale worker result (old epoch) is discarded after the list is rebuilt,
     so it never repaints a dead / wrong item.
  3. Concurrent decoding of many images completes with no leak / no crash
     (_thumb_inflight returns to 0, all cached).
  4. _decode_thumb caches its QImage per (path, px) and returns the same object.
"""
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage

app = QApplication(sys.argv)

from PIL import Image
import jxlforge.main_window as mw
from jxlforge.main_window import MainWindow

failures = []


def check(name, cond):
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name)
        failures.append(name)


def wait_idle(win, timeout=30.0):
    """Pump the event loop until all thumb workers finished and drained."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.005)
        if (win._thumb_inflight == 0
                and win._thumb_results.empty()
                and getattr(win, "_thumb_timer", None) is None):
            app.processEvents()
            if (win._thumb_inflight == 0
                    and win._thumb_results.empty()
                    and getattr(win, "_thumb_timer", None) is None):
                return True
    return False


tmpdir = tempfile.mkdtemp()

# ---- fixtures ---------------------------------------------------------------
paths = []
for i in range(30):
    p = os.path.join(tmpdir, "img_%02d.png" % i)
    # 不同尺寸/颜色，确保每张都真实解码，而非命中尺寸巧合。
    Image.new("RGB", (200 + i * 7, 150 + i * 3),
              ((i * 8) % 256, (i * 13) % 256, (i * 5) % 256)).save(p)
    paths.append(p)

win = MainWindow()
win._last_view = "缩略图"  # 图标模式才会走异步缩略图

# ---- 1) async pipeline fills items -----------------------------------------
win.input_files = paths
win._refresh_list()
# _refresh_list 只建占位 item 并启动投递节拍器（QTimer start(0)），真正投递
# 发生在随后的事件循环 tick；泵几帧让节拍器触发，证明走的是异步路径。
for _ in range(10):
    app.processEvents()
    if win._thumb_inflight > 0:
        break
check("thumb workers dispatched off the GUI thread (inflight > 0)",
      win._thumb_inflight > 0)
ok = wait_idle(win)
check("all thumb workers finished and drained", ok)
check("no stale inflight after idle", win._thumb_inflight == 0)

# 取第一项的 DecorationRole：应为真实解码出的缩略图 QPixmap（非灰色占位）。
item0 = win.input_list.item(0)
dec = item0.data(Qt.DecorationRole)
check("item0 has a real (non-null) thumbnail pixmap",
      isinstance(dec, QPixmap) and not dec.isNull())
check("decoded thumbnail cached for the file",
      any(k[0] == paths[0] for k in win._thumb_cache))

# ---- 2) epoch token discards stale results --------------------------------
# 模拟“列表已重建”：手动把 epoch 推进，再塞一条带 *旧* epoch 的假结果，
# 直接驱动 drain，验证陈旧结果被丢弃、item 不被错误覆盖。
win._thumb_epoch = 5
placeholder = win._placeholder_thumbnail(
    min(mw.GRID_SIZES["缩略图"].width(),
        mw.GRID_SIZES["缩略图"].height() - win.list_delegate.NAME_BAND)
    - 2 * mw.THUMB_PAD)
item0.setData(Qt.DecorationRole, placeholder)  # 重置为占位
fake = QImage(50, 50, QImage.Format_ARGB32)
fake.fill(Qt.red)  # 50x50 的假图，尺寸明显不同于占位
win._thumb_items = {paths[0]: item0}
win._thumb_results.put((4, paths[0], 96, fake, "STALE TIP"))
win._thumb_inflight = 1  # 模拟仍有一个在途，避免 drain 立即自停
win._drain_thumb_results()  # 直接调用，无需等待 timer
dec2 = item0.data(Qt.DecorationRole)
check("stale (old-epoch) result is discarded, not applied",
      isinstance(dec2, QPixmap) and dec2.width() != 50)

# ---- 3) concurrent decoding of many images --------------------------------
win.input_files = paths
win._refresh_list()
ok3 = wait_idle(win)
check("30 images decoded concurrently without leak", ok3)
check("inflight back to 0 after batch", win._thumb_inflight == 0)
check("all 30 decoded images cached",
      sum(1 for k in win._thumb_cache if k[0] in set(paths)) == 30)

# ---- 4) decode cache returns the same object ------------------------------
img1, _ = win._decode_thumb(paths[0], 96)
img2, _ = win._decode_thumb(paths[0], 96)
check("_decode_thumb caches QImage per (path, px)",
      img1 is not None and img1 is img2)

print()
if failures:
    print("FAILED %d check(s):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL THUMB-ASYNC CHECKS PASSED")
