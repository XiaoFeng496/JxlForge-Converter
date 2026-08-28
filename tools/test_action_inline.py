# -*- coding: utf-8 -*-
"""回归：动作参数 inline 暴露 + 3 个新动作 (规格化/曝光/阴影高光) + 重采样算法。

覆盖：

1) 参数 inline 暴露
   - ActionItemWidget 接受 (action, on_change) 并按 type 生成对应控件。
   - 控件值变化通过 on_change 写回 action dict，触发防抖预览刷新。
   - 取消勾选、改变参数、移动、删除动作均能正确工作（不闪退）。
   - 默认参数来自 ``processor.DEFAULT_PARAMS``，无需弹窗。

2) 三个新动作
   - 规格化（autocontrast + cutoff）
   - 曝光（按 EV 档位，+1 EV = 亮度 ×2）
   - 阴影/高光（分别调整暗部/亮部系数）

3) 调整大小支持选择重采样算法
   - 9 个算法（LANCZOS/BICUBIC/BILINEAR/BOX/HAMMING/NEAREST）
   - algorithm 字符串映射到 Pillow 的 Image.Resampling 枚举
   - 旧数据无 algorithm 键回退 LANCZOS
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox,
)

app = QApplication.instance() or QApplication(sys.argv)

from libjxl_gui import main_window as mw
from libjxl_gui import processor

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


def get_action(row):
    return win.action_list.item(row).data(Qt.UserRole)


def widget_at(row):
    return win.action_list.itemWidget(win.action_list.item(row))


# --- 1) action_combo 含全部 9 个类型 -----------------------------------
check("动作下拉框含规格化/曝光/阴影高光",
      all(t in [win.action_combo.itemText(i)
                for i in range(win.action_combo.count())]
          for t in ("规格化", "曝光", "阴影/高光")))


# --- 2) 调整大小 inline：width/height/algorithm 三控件 -----------------
win._add_action_item(
    {"type": "调整大小", "params": {"width": 800, "height": 600,
                                "algorithm": "LANCZOS"}, "enabled": True},
    render_preview=False,
)
w = widget_at(0)
check("调整大小行有 width spin", "width" in w._param_widgets)
check("调整大小行有 height spin", "height" in w._param_widgets)
check("调整大小行有 algorithm combo", "algorithm" in w._param_widgets)
check("algorithm 列表含 6 种", len(processor.RESIZE_ALGORITHMS) == 6)

# 改宽 → 写回 dict
w._param_widgets["width"].setValue(1024)
check("改 width 后 action 数据已更新",
      get_action(0)["params"]["width"] == 1024)
# 改算法 → 写回 dict
algo_combo = w._param_widgets["algorithm"]
# 找 BICUBIC 索引
bicubic_idx = next(i for i in range(algo_combo.count())
                   if algo_combo.itemData(i) == "BICUBIC")
algo_combo.setCurrentIndex(bicubic_idx)
check("改 algorithm 后 action 数据已更新",
      get_action(0)["params"]["algorithm"] == "BICUBIC")
check("摘要包含算法名",
      "BICUBIC" in w.summary_label.text())


# --- 3) 新增 3 个动作能正确生成 inline 控件 ----------------------------
for new_type in ("规格化", "曝光", "阴影/高光"):
    win._add_action_item(
        {"type": new_type, "params": dict(processor.DEFAULT_PARAMS[new_type]),
         "enabled": True},
        render_preview=False,
    )
row = win.action_list.count() - 1
w = widget_at(row)
check(f"{new_type} 项至少有一个 inline 控件", len(w._param_widgets) > 0)

# 验证各类型有正确的参数键
expect_keys = {
    "规格化": ("cutoff",),
    "曝光": ("ev",),
    "阴影/高光": ("shadow", "highlight"),
}
for new_type, keys in expect_keys.items():
    idx = next(i for i in range(win.action_list.count())
                if get_action(i)["type"] == new_type)
    w = widget_at(idx)
    for k in keys:
        check(f"{new_type} 含 {k} 控件", k in w._param_widgets)

# 改曝光 EV → 写回
ev_widget = widget_at(next(i for i in range(win.action_list.count())
                           if get_action(i)["type"] == "曝光"))\
    ._param_widgets["ev"]
ev_widget.setValue(1.5)
idx = next(i for i in range(win.action_list.count())
           if get_action(i)["type"] == "曝光")
check("改 EV 后 action 数据已更新", get_action(idx)["params"]["ev"] == 1.5)


# --- 4) 默认参数来自 processor.DEFAULT_PARAMS（添加时不弹窗） ---------
# 模拟选「规格化」点「添加」
win.action_combo.setCurrentText("规格化")
win._on_add_action()
last = get_action(win.action_list.count() - 1)
check("添加规格化后默认 cutoff=0", last["params"]["cutoff"] == 0)
check("添加规格化后 enabled=True", last["enabled"] is True)


# --- 5) processor 层：3 个新动作 + 算法选择 ----------------------------
from PIL import Image
test_img = Image.new("RGB", (4, 4), (128, 128, 128))

# 规格化
out = processor.apply_actions(test_img, [{"type": "规格化", "params": {"cutoff": 0}}])
check("规格化可调用", out.size == (4, 4))

# 曝光（用 100 输入：×2 = 200，不踩 255 clamp）
src = Image.new("RGB", (2, 2), (100, 100, 100))
out = processor.apply_actions(src, [{"type": "曝光", "params": {"ev": 1.0}}])
px = list(out.getdata())[0]
check("曝光 +1 EV 后 100→200", px[0] == 200)

# 阴影/高光
dark_img = Image.new("RGB", (2, 2), (50, 50, 50))
out = processor.apply_actions(
    dark_img, [{"type": "阴影/高光", "params": {"shadow": 2.0, "highlight": 1.0}}]
)
check("阴影 2.0 提亮暗部", list(out.getdata())[0][0] > 50)

bright_img = Image.new("RGB", (2, 2), (200, 200, 200))
out = processor.apply_actions(
    bright_img, [{"type": "阴影/高光", "params": {"shadow": 1.0, "highlight": 0.5}}]
)
check("高光 0.5 压低亮部", list(out.getdata())[0][0] < 200)

# 算法选择
out = processor.apply_actions(
    test_img, [{"type": "调整大小", "params": {"width": 2, "height": 2,
                                            "algorithm": "BICUBIC"}}]
)
check("BICUBIC 调整大小可调用", out.size == (2, 2))

# 默认无 algorithm 键 → LANCZOS
out = processor.apply_actions(
    test_img, [{"type": "调整大小", "params": {"width": 2, "height": 2}}]
)
check("无 algorithm 键回退到 LANCZOS", out.size == (2, 2))

# 错误 algorithm 字符串回退 LANCZOS
out = processor.apply_actions(
    test_img, [{"type": "调整大小", "params": {"width": 2, "height": 2,
                                            "algorithm": "BOGUS"}}]
)
check("错误 algorithm 字符串回退 LANCZOS", out.size == (2, 2))


# --- 6) inline 编辑防抖：连续改值只触发一次预览刷新 -------------------
from PySide6.QtCore import QElapsedTimer
win._on_clear_actions()
win._add_action_item(
    {"type": "旋转", "params": {"angle": 90}, "enabled": True},
    render_preview=False,
)
w = widget_at(0)
render_count = {"n": 0}
_orig_render = win._render_action_preview


def _spy_render(*args, **kwargs):
    render_count["n"] += 1
    _orig_render(*args, **kwargs)


win._render_action_preview = _spy_render
# 拖动 spinbox 模拟连续变化
t = QElapsedTimer(); t.start()
for v in (10, 20, 30, 40, 50, 60, 70, 80, 90):
    w._param_widgets["angle"].setValue(v)
# 不 pump 事件循环：不应立即触发预览
check("spinbox 连续变化期间不立即触发预览（防抖）",
      render_count["n"] == 0)
# 等待防抖触发
import time
for _ in range(30):
    QApplication.processEvents()
    time.sleep(0.05)
    if render_count["n"] > 0:
        break
check("防抖窗口结束（~250ms）后触发预览",
      render_count["n"] >= 1 and t.elapsed() < 2000)

# 验证：参数变化时 fit=False（不强制适应窗口），保留用户缩放位置
# —— 用 spy 包 _apply_preview_pixmap，fit 参数会被传进去；
# 但更直接的是看 win._render_action_preview 调用时传了 fit=False。
fit_args = []
_orig_render2 = win._render_action_preview
def _spy_render2(*args, **kwargs):
    fit_args.append(kwargs.get("fit", True))
    _orig_render2(*args, **kwargs)
win._render_action_preview = _spy_render2
# 触发一次参数变化
w._param_widgets["angle"].setValue(120)
# 等防抖
import time as _t
for _ in range(20):
    QApplication.processEvents(); _t.sleep(0.05)
    if fit_args:
        break
check("参数变化时 _render_action_preview 传 fit=False（保留缩放）",
      bool(fit_args) and fit_args[0] is False)

win._render_action_preview = _orig_render

# --- 7) 切换预览源：向上/向下都必须按复选框决定 fit -------------------
# Bug：currentIndexChanged 会带 index(int)，_render_action_preview 的 fit
# 默认参数被它顶掉 —— 切到 index=0（向上）时 fit=0→False 不 fit，
# 切到 index>0（向下）才 fit。表现为「向上切不自动适应窗口、向下切会」。
import tempfile as _tf
_tmpdir = _tf.mkdtemp()
win.input_files = [
    os.path.join(_tmpdir, "a.png"),
    os.path.join(_tmpdir, "b.png"),
    os.path.join(_tmpdir, "c.png"),
]
win._refresh_preview_sources()
check("预览源下拉有 3 项", win.preview_source_combo.count() == 3)

# 捕获切源时 _render_action_preview 收到的 fit 实参
src_fit_args = []
_orig_render3 = win._render_action_preview


def _spy_render3(*args, **kwargs):
    src_fit_args.append((args, kwargs))


win._render_action_preview = _spy_render3
# 向下切（0 → 2）
win.preview_source_combo.setCurrentIndex(2)
# 向上切（2 → 0）—— 旧 bug 在这里不 fit
win.preview_source_combo.setCurrentIndex(0)
# 再切一次
win.preview_source_combo.setCurrentIndex(1)
win._render_action_preview = _orig_render3

# 复选框默认启用 → 所有切源都应 fit=True（含向上切到 index=0）
check("切源触发了预览刷新", len(src_fit_args) >= 3)
check("向下切预览源 fit=True",
      any(kw.get("fit") is True for _a, kw in src_fit_args))
check("向上切预览源（切到 index=0）也 fit=True —— 旧 bug 此处为 False",
      len(src_fit_args) >= 2 and src_fit_args[1][1].get("fit") is True)

# 取消勾选 → 切源应 fit=False
win.fit_on_source_change_check.setChecked(False)
check("取消勾选后 _fit_on_source_change() 为 False",
      win._fit_on_source_change() is False)
src_fit_args.clear()
win._render_action_preview = _spy_render3
win.preview_source_combo.setCurrentIndex(2)
win._render_action_preview = _orig_render3
check("取消勾选后切源 fit=False（沿用当前缩放）",
      bool(src_fit_args) and src_fit_args[-1][1].get("fit") is False)
win.fit_on_source_change_check.setChecked(True)


print()
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
sys.exit(0 if not failures else 1)
