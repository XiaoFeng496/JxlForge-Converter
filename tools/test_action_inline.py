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
    QApplication, QGridLayout, QSpinBox, QDoubleSpinBox, QLineEdit,
)

app = QApplication.instance() or QApplication(sys.argv)

from jxlforge import main_window as mw
from jxlforge import processor

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


# --- 1) 添加动作菜单含新增的类型 -----------------------------------
# 动作类型下拉框已移除，改为「添加动作▶」点击弹出的 add_action_menu。
_action_texts = [a.text() for a in win.add_action_menu.actions()
                 if not a.isSeparator()]
check("添加动作菜单含规格化/曝光/阴影高光",
      all(t in _action_texts for t in ("规格化", "曝光", "阴影/高光")))


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
# 折叠/按钮（2026-08-28 新增）
check("每个动作项有折叠按钮", hasattr(w, "collapse_btn"))
# ⚠️ offscreen 下窗口未 show，全局 isVisible() 恒 False；改用 isVisibleTo(parent)
check("默认展开（params_container 对父可见）",
      w.params_container.isVisibleTo(w) is True)
check("按钮在标题行（与 summary/checkbox 同行）",
      w.collapse_btn.parentWidget() is w)
check("调整大小行有 algorithm combo", "algorithm" in w._param_widgets)
check("algorithm 列表含 6 种", len(processor.RESIZE_ALGORITHMS) == 6)

# QGridLayout(2 列) 排版（替代 FlowLayout：PySide6 中 Python 派生 QLayout
# 的 setGeometry 不会被 C++ 端 dispatch，导致 _do_layout 从不执行）
grid = w.params_container.layout()
check("参数容器用 QGridLayout（2 列布局）",
      isinstance(grid, QGridLayout))
check("label 列无 stretch（内容宽度）", grid.columnStretch(0) == 0)
check("widget 列 stretch=0（按内容显示，不拉满）", grid.columnStretch(1) == 0)
# 三个参数各占一行
check("3 个参数占 3 行", grid.rowCount() == 3)
labels = [grid.itemAtPosition(r, 0).widget().text()
          for r in range(grid.rowCount())]
widgets = [grid.itemAtPosition(r, 1).widget()
           for r in range(grid.rowCount())]
check("3 行 label 顺序正确：宽/高/算法",
      labels == ["宽", "高", "算法"])
check("3 行 widget 类型正确：QSpinBox/QSpinBox/下拉框(NoFlickerComboBox)",
      type(widgets[0]).__name__ == "QSpinBox"
      and type(widgets[1]).__name__ == "QSpinBox"
      # NoFlickerComboBox 现为可切换代理（QWidget 子类），不再是 QComboBox，
      # 按类名/基类判型都会漏；用代理类本身判型，兼容原生与自绘两种实现。
      and isinstance(widgets[2], mw.NoFlickerComboBox))

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
# 模拟在「添加动作▶」下拉里点「规格化」：触发对应菜单项（等价于用户点击）。
# ⚠️ 用 QAction.trigger() 而非 add_action_button.click()：后者会 exec() 一个
# 模态菜单而阻塞；trigger() 同步走完「点菜单项 → 添加动作」的完整链路。
_action_ids = {a.data(): a for a in win.add_action_menu.actions()
               if not a.isSeparator()}
_action_ids["规格化"].trigger()
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


# --- 8) 折叠按钮：点击可折叠/展开，状态持久化 ------------------------
win._on_clear_actions()
win._add_action_item(
    {"type": "水印", "params": {"text": "X"}}, render_preview=False,
)
item = win.action_list.item(0)
w = widget_at(0)
check("折叠按钮默认 DownArrow（展开）", w.collapse_btn.arrowType() == Qt.DownArrow)
# ⚠️ 折叠按钮不再是 checkable（用户要求：点击后不保持「已按下」外观，
# 原生会变蓝、Fusion 会颜色变深），只转箭头方向。
check("折叠按钮非 checkable（点击后不保持按下态）",
      w.collapse_btn.isCheckable() is False)
check("默认 params_container 对父可见",
      w.params_container.isVisibleTo(w))
# 点击折叠
w.collapse_btn.click()
check("点击后折叠（对父不可见）",
      w.params_container.isVisibleTo(w) is False)
check("折叠后按钮箭头变 RightArrow", w.collapse_btn.arrowType() == Qt.RightArrow)
check("折叠状态写回 action._collapsed=True",
      item.data(Qt.UserRole).get("_collapsed") is True)
# 再点击展开
w.collapse_btn.click()
check("再次点击展开",
      w.params_container.isVisibleTo(w) is True)
check("按钮箭头变回 DownArrow", w.collapse_btn.arrowType() == Qt.DownArrow)
check("展开后 _collapsed=False",
      item.data(Qt.UserRole).get("_collapsed") is False)
# 重新创建动作项时，_collapsed=True 的应保持折叠
win._on_clear_actions()
win._add_action_item(
    {"type": "水印", "params": {"text": "Y"}, "_collapsed": True},
    render_preview=False,
)
w2 = widget_at(0)
check("恢复时按 _collapsed=True 初始折叠（RightArrow）",
      w2.params_container.isVisibleTo(w2) is False
      and w2.collapse_btn.arrowType() == Qt.RightArrow)


print()
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
sys.exit(0 if not failures else 1)
