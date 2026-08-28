# -*- coding: utf-8 -*-
"""Headless regression: 动作标签页预览区不会因错误信息被撑大；预览路径走
_display_path 让 PIL 能读 JXL/AVIF；解码失败时只显示文件名而非完整路径。

修复了两个 bug：

1) 动作标签页如果有文件无法读取时，会把窗口撑大
   原因：preview_msg 是默认 QLabel（Preferred/Preferred、无 word wrap），
   错误信息中含 PIL 抛出的完整文件路径，导致 QLabel 横向拉到与路径同宽，
   又因为 QVBoxLayout 中无高度上限，整窗跟着长高。
   修复：setWordWrap(True) + Preferred/Maximum + setMaximumHeight(120)。

2) 动作标签页无法预览 JXL
   原因：_render_action_preview 直接 PIL.Image.open(path)；PIL 不支持 JXL。
   修复：先用 _display_path() 走 djxl 解到 PNG 再读。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSize, Qt, QElapsedTimer
from PySide6.QtWidgets import QApplication, QSizePolicy

app = QApplication.instance() or QApplication(sys.argv)


def pump(ms=5000):
    """Spin the event loop so the async preview worker + drain can run."""
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()
        time.sleep(0.005)

from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
        print("PASS:", name)
    else:
        failures.append(name)
        print("FAIL:", name)


win = mw.MainWindow()

# --- Bug 1: preview_msg 的尺寸策略不会把窗口撑大 -----------------
check("preview_msg exists", hasattr(win, "preview_msg"))
msg = win.preview_msg
sp = msg.sizePolicy()
check("preview_msg uses Preferred horizontal",
      sp.horizontalPolicy() == QSizePolicy.Preferred)
check("preview_msg uses Maximum vertical (no vertical expansion)",
      sp.verticalPolicy() == QSizePolicy.Maximum)
check("preview_msg has word wrap enabled",
      msg.wordWrap() is True)
check("preview_msg has bounded maximum height",
      msg.maximumHeight() <= 200 and msg.maximumHeight() > 0)

# --- Bug 2: JXL/AVIF 预览路径走 _display_path ----------------------
# 用真实 PNG 模拟「JXL 解码后产生的 PNG」，验证 _render_action_preview 会通过
# _display_path 拿到可读路径。
import tempfile
from PIL import Image

tmpdir = tempfile.mkdtemp()
png_path = os.path.join(tmpdir, "fake_jxl_payload.png")
Image.new("RGB", (80, 60), (200, 100, 50)).save(png_path)

# 替换 _display_path，让它把 .jxl 当作 PNG 返回（模拟 djxl 解码后的产物）。
# 同时验证 _render_action_preview 在出错路径下不抛异常、能给出"文件名级"错误。
called = {"n": 0}
original_display_path = mw._display_path


def fake_display_path(path):
    called["n"] += 1
    if path.lower().endswith(".jxl"):
        return png_path
    return original_display_path(path)


mw._display_path = fake_display_path

try:
    win.input_files = [os.path.join(tmpdir, "sample.jxl")]
    win._refresh_preview_sources()
    check("combo populated for jxl",
          win.preview_source_combo.count() == 1)

    # 不抛异常即视为修复生效。
    win._render_action_preview()
    pump()  # 异步解码：等 worker + drain 回填
    check("_display_path was consulted by render",
          called["n"] >= 1)
    check("jxl preview shows the canvas (not the message)",
          win.preview_view.isHidden() is False
          and win.preview_msg.isHidden() is True)
    check("jxl preview produced a non-null pixmap",
          win._preview_original_pixmap is not None
          and not win._preview_original_pixmap.isNull())
finally:
    mw._display_path = original_display_path

# --- Bug 2 副带：解码失败时错误信息只显示文件名 -----------------
# 让 _display_path 返回 None，模拟 JXL 解码失败 / 不支持的格式。
def failing_display_path(path):
    return None


mw._display_path = failing_display_path
try:
    # 找一个真实存在的文件路径以便错误信息里包含可识别的 basename。
    bogus_dir = os.path.join(tmpdir, "极长中文目录_用于触发路径撑大测试")
    os.makedirs(bogus_dir, exist_ok=True)
    bogus_jxl = os.path.join(bogus_dir, "132119265_p0汉化.jxl")
    win.input_files = [bogus_jxl]
    win._refresh_preview_sources()
    win.preview_source_combo.setCurrentIndex(0)
    win._render_action_preview()
    pump()  # 异步：等失败结果回填到 preview_msg

    check("decode-failed -> canvas hidden",
          win.preview_view.isHidden() is True)
    check("decode-failed -> message visible",
          win.preview_msg.isHidden() is False)
    err = win.preview_msg.text()
    check("error message uses basename not full path",
          os.path.basename(bogus_jxl) in err
          and bogus_jxl not in err,
          )
    # 关键：消息中绝对不能带"父目录路径"，否则旧 bug 的窗口撑大问题就回来了。
    check("error message excludes parent dir prefix",
          bogus_dir not in err)
finally:
    mw._display_path = original_display_path

# --- Bug 3: 预览区无图时 2 行提示文本不要"往上飘" ----------------
# 根因：right_layout 是简单 QVBoxLayout，preview_view 隐藏后，
# preview_msg (Maximum 垂直策略 + MaxHeight(120)) 和 hint 都被推到顶部。
# 修复：preview_msg 包进带上下 addStretch(1) 的容器，stretch=1 占中间，
# 通过 4 个 setVisible 调用点同步切换容器可见性（与 preview_view 互斥）。
check("preview_msg_container exists",
      hasattr(win, "preview_msg_container"))
container = win.preview_msg_container

# 容器应带垂直 stretch，把 preview_msg 顶到中间而不是顶部。
container_layout = container.layout()
n_stretches = sum(
    1 for i in range(container_layout.count())
    if container_layout.itemAt(i).spacerItem() is not None
)
check("container has stretches above and below preview_msg",
      n_stretches >= 2)

# 当前状态：上一段失败场景里 preview_msg 是可见的，容器应同步可见。
# 用 isHidden() 而非 isVisible()——offscreen 下窗口未 show() 时
# isVisible() 永远返回 False，但 isHidden() 只看 widget 自身的显隐状态。
check("container visible when preview_msg visible",
      win.preview_msg.isHidden() is False
      and container.isHidden() is False)

# 模拟"成功加载图片"：preview_msg 隐藏 → 容器也应隐藏，避免抢
# preview_view 的 stretch 空间。
win.preview_msg.setVisible(False)
win.preview_msg_container.setVisible(False)
QApplication.processEvents()
check("container hidden when preview_msg hidden",
      win.preview_msg.isHidden() is True
      and container.isHidden() is True)

# 恢复原状态以便不影响后续断言 / 清理。
win.preview_msg.setVisible(True)
win.preview_msg_container.setVisible(True)

# 关键布局不变量：hint 必须在 right_layout 中排在 preview_msg_container
# 之后——这样"示意效果"那行永远贴底，不被预览消息挤到中间。
# offscreen 下 widget 几何坐标未计算，改用 layout 顺序断言（更稳定）。
right = win.tabs.widget(1)  # 动作标签是第 1 个 tab（输入=0）
from PySide6.QtWidgets import QLabel as _QL
hint_labels = [w for w in right.findChildren(_QL)
               if "示意效果" in w.text()]
check("hint label still exists", len(hint_labels) == 1)
hint = hint_labels[0]
parent_layout = hint.parent().layout()
container_idx = parent_layout.indexOf(win.preview_msg_container)
hint_idx = parent_layout.indexOf(hint)
check("hint sits below preview_msg_container in layout (anchored to bottom)",
      container_idx >= 0 and hint_idx > container_idx)

# --- Bug 4: 清空输入后预览区不应残留「预览加载中…」 ----------------
# 根因：path is None 分支只显示 preview_msg 却未重置文本；若此前进入过
# 「加载中」状态（文本被改写成"预览加载中…"），清空输入再回动作页就会
# 一直显示「加载中」。修复：None 分支把文本重置为初始提示。
class _NoOpPool:
    def start(self, worker):
        pass  # 不真正派发子线程，避免副作用影响文本断言


saved_pool = win._thumb_pool
win._thumb_pool = _NoOpPool()

EMPTY_HINT = "请先在「输入」标签添加图片，\n再在此处预览动作效果。"

# 1) 有图 → 进入「加载中」状态
win.input_files = [os.path.join(tmpdir, "sample.jxl")]
win._refresh_preview_sources()
win._render_action_preview()
check("render-with-image enters 'loading' state text",
      win.preview_msg.text() == "预览加载中…")

# 2) 清空输入 → path=None 分支应重置文本（不再是「加载中」）
win.input_files = []
win._refresh_preview_sources()
win._render_action_preview()
check("clearing input resets preview hint (no 'loading' residual)",
      "预览加载中" not in win.preview_msg.text()
      and EMPTY_HINT in win.preview_msg.text())

win._thumb_pool = saved_pool

# --- Bug 5: 切换预览图片后自动 fit 到屏幕；peek 不重置缩放 ----------
# 根因：_apply_preview_pixmap 只 set_pixmap 不 fit，切换图片沿用上一张缩放。
# 修复：fit=True 时（正常切图）调用 view.fit()；peek 传 fit=False 保留缩放。
from PySide6.QtGui import QPixmap as _QPixmap

fit_calls = {"n": 0}
_orig_fit = win.preview_view.fit


def _spy_fit():
    fit_calls["n"] += 1


win.preview_view.fit = _spy_fit
win.preview_view.isVisible = lambda: True  # offscreen 下绕过真实可见性守卫

win._preview_processed_pixmap = _QPixmap(100, 100)
win._preview_mode = "processed"
win._apply_preview_pixmap(fit=True)
# fit 已改为 singleShot(0) 延迟到下一事件循环执行（等布局落定再 fit），
# 故需 pump 事件循环让计时器触发后再断言。
QApplication.processEvents()
check("switching image triggers fit-to-screen", fit_calls["n"] == 1)
win._apply_preview_pixmap(fit=False)  # 模拟按住显示原图（peek）
QApplication.processEvents()
check("peek does not reset zoom (skips fit)", fit_calls["n"] == 1)

win.preview_view.fit = _orig_fit

# --- Bug 7: 连续多次「适应窗口」结果必须一致 ------------------------
# 根因：Qt 的 fitInView 是在「当前 transform」基础上叠加缩放系数；不重置的话
# 第二次 fit 会在第一次的缩放上再缩一层，导致首次大、再次更小。同时旧实现
# 在 fit 内 processEvents 强制重绘中间态会闪烁。
# 修复：fit() 内先 resetTransform() 再 scale()，每次从 1:1 重算；并临时关闭
# 滚动条使 viewport 尺寸不受缩放态干扰，无 processEvents 故不闪烁。
import types as _types

_scroll = win.preview_view
_scroll.set_pixmap(_QPixmap(200, 100))  # 给一个非空 pixmap 让 _pixmap_item 就绪
_orig_isvis = _scroll.isVisible
_orig_viewport = _scroll.viewport
_orig_reset = _scroll.resetTransform
_fit_seq = []


def _spy_reset():
    _fit_seq.append("reset")
    _orig_reset()


_scroll.isVisible = lambda: True  # offscreen 下绕过真实可见性守卫
_vp = _types.SimpleNamespace(
    width=lambda: 200, height=lambda: 150,
    rect=lambda: _types.SimpleNamespace(width=lambda: 200, height=lambda: 150))
_scroll.viewport = lambda: _vp
_scroll.resetTransform = _spy_reset
_scroll.setHorizontalScrollBarPolicy = lambda *a, **k: None
_scroll.setVerticalScrollBarPolicy = lambda *a, **k: None
_scroll.horizontalScrollBarPolicy = lambda: None
_scroll.verticalScrollBarPolicy = lambda: None
_scroll._fit_on_show = False

_scroll.fit()  # 第一次（当前 transform 为单位阵）
check("fit resets transform before scale (1st)",
      _fit_seq == ["reset"])

_scroll.scale(0.5, 0.5)  # 模拟第一次 fit 后已留下缩放 transform
_fit_seq.clear()
_scroll.fit()  # 第二次（必须重置后再 scale，否则更小）
check("fit resets transform before scale (2nd)",
      _fit_seq == ["reset"])
# 关键不变量：第一次 fit 与第二次 fit 的缩放系数必须一致（都从 reset 后的
# 1:1 起点重算）。offscreen 下 dpr≠1 导致绝对 m11 不是 1，故比较两次是否相等。
_m11_1 = _scroll.transform().m11()
_scroll.scale(0.3, 0.3)  # 再制造一次「已缩放」状态
_scroll.fit()
_m11_2 = _scroll.transform().m11()
check("repeated fit yields consistent scale across calls",
      abs(_m11_1 - _m11_2) < 1e-6)

_scroll.isVisible = _orig_isvis
_scroll.viewport = _orig_viewport
_scroll.resetTransform = _orig_reset

# --- Bug 8: 切换图片时预览区不再闪烁（大图小图都闪） ------------------
# 根因 A：旧逻辑每次 _render_action_preview 都无条件
#   setVisible(False) + 显示「预览加载中…」→ 解码完成后再 setVisible(True)，
# 形成「图消失 → 文字 → 图回来」三步跳变。该跳变与解码耗时无关，小图解码
# 只要几毫秒却仍会完整跳变一次，所以「不管大图小图都闪」。
# 修复 A：已有预览图时【保持旧图不动】——不隐藏视图、不切文字；只有解码超过
# _PREVIEW_LOADING_HINT_DELAY 仍未完成（真·大图）才切加载态。
# 根因 B：_apply_preview_pixmap 无条件 singleShot(0, fit)，会先按「上一张的
# transform」绘制一帧新图，下一拍 fit 再重画一次 → 又一次闪动。
# 修复 B：布局未变（viewport 稳定）时同步 fit，与 set_pixmap 同处一次事件
# 回调内，Qt 只在事件末尾绘制一次，无中间帧。

_view = win.preview_view
_orig_view_isvis = _view.isVisible
_orig_view_fit = _view.fit
_orig_view_setvis = _view.setVisible
_vis_calls = []
_fit_n = {"n": 0}


def _spy_setvis(v):
    _vis_calls.append(v)


def _spy_fit_count():
    _fit_n["n"] += 1


_view.setVisible = _spy_setvis
_view.fit = _spy_fit_count
win._thumb_pool = _NoOpPool()  # 不真正派发子线程，busy 保持 True
win.input_files = [os.path.join(tmpdir, "sample.jxl")]
win._refresh_preview_sources()

# 场景 A：已有预览图 + 视图可见 → 必须保持旧图，绝不隐藏、绝不切「加载中」
_view.isVisible = lambda: True
win.preview_msg.setText("旧提示")  # 哨兵文本，用于检测是否被改写
win._preview_processed_pixmap = _QPixmap(100, 100)
_vis_calls.clear()
win._render_action_preview()
check("switching image keeps preview visible (no blank flash)",
      False not in _vis_calls)
check("switching image does not rewrite hint text (no text flash)",
      win.preview_msg.text() == "旧提示")
check("keeping old image => layout unchanged => fit stays synchronous",
      win._preview_fit_deferred is False)

# 场景 B：布局未变时回填，fit 必须同步发生（不产生「旧 transform」中间帧）
_fit_n["n"] = 0
win._preview_fit_deferred = False
win._apply_preview_pixmap(fit=True)
check("backfill fits synchronously (no intermediate frame)",
      _fit_n["n"] == 1)

# 场景 C：布局刚变化（隐藏→可见）时 fit 仍推迟一拍，避免拿到未落定 viewport
_fit_n["n"] = 0
win._preview_fit_deferred = True
win._apply_preview_pixmap(fit=True)
check("deferred fit is not called synchronously", _fit_n["n"] == 0)
QApplication.processEvents()
check("deferred fit runs on next event loop tick", _fit_n["n"] == 1)

# 场景 D：无图可留时立即给出「加载中」反馈（不能长时间空白）
_view.isVisible = lambda: False
win._preview_processed_pixmap = None
win._render_action_preview()
check("no current image => immediate 'loading' feedback",
      win.preview_msg.text() == "预览加载中…")
check("no current image => fit deferred until layout settles",
      win._preview_fit_deferred is True)

# 场景 E：解码超过阈值仍未完成 → 才切到加载态（此时跳变一次是合理反馈）
_view.isVisible = lambda: True
win.preview_msg.setText("旧提示2")
win._preview_processed_pixmap = _QPixmap(100, 100)
win._render_action_preview()
check("slow decode: keeps old image before threshold",
      win.preview_msg.text() == "旧提示2")
pump(mw._PREVIEW_LOADING_HINT_DELAY + 400)
check("slow decode: switches to 'loading' after threshold",
      win.preview_msg.text() == "预览加载中…")
win._cancel_preview_loading_hint()

win._thumb_pool = saved_pool
_view.isVisible = _orig_view_isvis
_view.setVisible = _orig_view_setvis
_view.fit = _orig_view_fit

print()
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
sys.exit(0 if not failures else 1)
