# -*- coding: utf-8 -*-
"""Headless regression: 拖入大图时主线程绝不同步解码（根因消除）。

此前 `_refresh_input_views` 无条件调 `_refresh_table`，其每行同步调
`_image_info` -> `get_image_dims` -> `_display_path`（JXL 在此同步跑 `djxl`
子进程），导致拖入大图时 GUI 冻结。Phase 3 把 `get_image_dims` 改为不阻塞 +
异步预取（`_ensure_dims` / `_DimsWorker`），主线程永不在此路径上解码。

本测试：
1) 详情视图刷新时主线程不调用 `_display_path`（证明无同步解码阻塞）；
2) `_ensure_dims` 异步派发 worker，解码完成后回填 `_DIMS_CACHE` 并刷新
   tooltip / 分辨率列；
3) 同一 path 重复 `_ensure_dims` 只派发一次（去重）。
工具缺失时优雅跳过。
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

app = QApplication(sys.argv)

from libjxl_gui import converter
import libjxl_gui.main_window as mw
from libjxl_gui.main_window import MainWindow

failures = []


def check(name, cond):
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name)
        failures.append(name)


tools = converter.check_tools()
have_jxl = bool(tools.get("cjxl") and tools.get("djxl"))
try:
    from PIL import Image
    pillow_ok = True
except Exception as exc:
    print("SKIP: Pillow unavailable:", exc)
    pillow_ok = False

# 跟踪「主线程」对 _display_path 的调用：子线程（worker）调用不算。
_main_id = threading.get_ident()
_main_calls = []
_orig_display = mw._display_path


def _track_display(path):
    if threading.get_ident() == _main_id:
        _main_calls.append(path)
    return _orig_display(path)


mw._display_path = _track_display

win = MainWindow()

W, H = 2000, 1500
if have_jxl and pillow_ok:
    tmp = tempfile.mkdtemp()
    src_png = os.path.join(tmp, "src.png")
    Image.new("RGB", (W, H), (10, 20, 30)).save(src_png)
    jxl_path = os.path.join(tmp, "big.jxl")
    ok, _e, _t = converter.encode(src_png, jxl_path, effort=1)
    if not (ok and os.path.isfile(jxl_path)):
        have_jxl = False
        print("SKIP: failed to build JXL fixture")
else:
    print("SKIP: cjxl/djxl or Pillow absent, cannot build JXL fixture")
    jxl_path = None

if have_jxl:
    # ---- Test 1: 详情视图刷新时主线程不同步解码 ----
    _main_calls.clear()
    win.input_files = [jxl_path]
    win._last_view = "详细信息"
    win.input_stack.setCurrentWidget(win.input_table)
    win._refresh_table()
    check("refresh_table does NOT synchronously decode on main thread",
          _main_calls == [])
    check("dims worker dispatched for unknown-size jxl (inflight)",
          jxl_path in win._dims_inflight)

    # ---- Test 2: 异步补齐尺寸并刷新 UI ----
    if win._thumb_pool is not None:
        win._thumb_pool.waitForDone()
    for _ in range(30):
        app.processEvents()
    QTest.qWait(80)
    cached = mw._DIMS_CACHE.get(jxl_path)
    check("dims cache filled after async decode", cached == (W, H))
    # tooltip 被刷新为含正确尺寸
    tbl = win.input_table
    tip_ok = False
    for r in range(tbl.rowCount()):
        it = tbl.item(r, 0)
        if it is not None and it.data(mw.Qt.UserRole) == jxl_path:
            if "%d x %d" % (W, H) in it.toolTip():
                tip_ok = True
            break
    check("tooltip refreshed with real dimensions after async decode", tip_ok)

    # ---- Test 3: 去重（在未派发完成前同步连调两次，应只入队一次）----
    with mw._DIMS_CACHE_LOCK:
        mw._DIMS_CACHE.pop(jxl_path, None)   # 清空缓存，迫使走「未知尺寸」分支
    win._dims_inflight.discard(jxl_path)
    win._ensure_dims(jxl_path)               # 首次：派发 worker
    n1 = len(win._dims_inflight)
    win._ensure_dims(jxl_path)               # 重复：应去重，不再派发
    n2 = len(win._dims_inflight)
    check("ensure_dims dedups same path (no double dispatch)", n1 == 1 and n2 == 1)
    # 清掉本次在途 worker 的残留，避免影响后续断言
    win._dims_inflight.discard(jxl_path)

    # ---- Test 4: 原生 PNG 仅读头即得尺寸，不触发 _display_path ----
    _main_calls.clear()
    dims = mw.get_image_dims(src_png)
    check("native png dims via header only", dims == (W, H))
    check("native png does not call _display_path", _main_calls == [])
else:
    print("(dims async checks skipped)")

print()
if failures:
    print("FAILED %d check(s):" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL DIMS-ASYNC CHECKS PASSED")
