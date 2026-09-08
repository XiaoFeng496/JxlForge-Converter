# -*- coding: utf-8 -*-
"""回归：新增的 3 个动作「饱和度 / 自然饱和度 / 模糊」。

覆盖两层面：

A) processor（纯 Pillow，不依赖 Qt）
   1. 饱和度：factor=1 恒等、0=灰度、>1 更浓；RGBA 保留 alpha。
   2. 自然饱和度：灰色像素不动（与全局饱和度的关键区别）、低饱和像素
      增益大于高饱和像素、factor=1 恒等、alpha 保留。
   3. 模糊：radius<=0 恒等；高斯/方框/中值三种滤波器都能跑；中值核边长
      被夹成奇数且 <=9（Pillow 传偶数会抛 "bad filter size"）；
      RGBA + 中值不崩（Pillow 的 MedianFilter 不支持 RGBA）。

B) UI（offscreen）
   4. 三个动作都在「添加动作 ▶」菜单里，添加后参数 inline 暴露在列表项上。
   5. 调参写回 action dict（含 **factor=0 这种 0 值不能被 ``or`` 吞掉**）。
   6. 模糊的方法下拉：显示译文、userData 存英文 ID；切到中值时半径上限
      自动收到 9（否则界面显示 50、实际按 9 处理，数字与结果对不上）。
   7. 摘要文本随参数变化。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

app = QApplication.instance() or QApplication(sys.argv)

from jxlforge import main_window as mw
from jxlforge import processor

passed = 0
failures = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("PASS:", name)
    else:
        failures.append("%s %s" % (name, detail))
        print("FAIL:", name, detail)


# ======================================================================
# A) processor
# ======================================================================
print("=== A. processor ===")

check("ACTION_TYPES 含 3 个新动作",
      all(t in processor.ACTION_TYPES
          for t in ("饱和度", "自然饱和度", "模糊")),
      str(processor.ACTION_TYPES))
for t in ("饱和度", "自然饱和度", "模糊"):
    check("DEFAULT_PARAMS 有 %s" % t, t in processor.DEFAULT_PARAMS)
check("模糊默认用高斯",
      processor.DEFAULT_PARAMS["模糊"]["method"] == "GAUSSIAN")
check("BLUR_METHODS 的 ID 是英文（要进配置）",
      all(mid.isascii() for mid, _ in processor.BLUR_METHODS))

# --- 饱和度 ---
src = Image.new("RGB", (4, 4), (200, 100, 100))
check("饱和度 factor=1 恒等",
      processor._saturation(src, {"factor": 1.0}).getpixel((0, 0))
      == (200, 100, 100))
gray = processor._saturation(src, {"factor": 0.0}).getpixel((0, 0))
check("饱和度 factor=0 变灰度",
      gray[0] == gray[1] == gray[2], str(gray))
sat2 = processor._saturation(src, {"factor": 2.0}).getpixel((0, 0))
check("饱和度 factor=2 更浓（通道差变大）",
      (sat2[0] - sat2[1]) > (200 - 100), str(sat2))
rgba = Image.new("RGBA", (4, 4), (200, 100, 100, 77))
check("饱和度保留 alpha",
      processor._saturation(rgba, {"factor": 0.0}).getpixel((0, 0))[3] == 77)
check("apply_actions 能派发饱和度",
      processor.apply_actions(src, [{"type": "饱和度",
                                     "params": {"factor": 0.5}}]) is not None)

# --- 自然饱和度 ---
g = Image.new("RGB", (4, 4), (128, 128, 128))
check("自然饱和度：灰色像素不动（关键特性）",
      processor._vibrance(g, {"factor": 3.0}).getpixel((0, 0)) == (128, 128, 128))
check("自然饱和度 factor=1 恒等",
      processor._vibrance(src, {"factor": 1.0}).getpixel((0, 0))
      == (200, 100, 100))
vib = processor._vibrance(src, {"factor": 2.0}).getpixel((0, 0))
check("自然饱和度 factor=2 提饱和（通道差变大）",
      (vib[0] - vib[1]) > (200 - 100), str(vib))
vib0 = processor._vibrance(src, {"factor": 0.0}).getpixel((0, 0))
check("自然饱和度 factor=0 降饱和（通道差变小）",
      (vib0[0] - vib0[1]) < (200 - 100), str(vib0))

# 低饱和像素增益 > 高饱和像素增益（这才是 vibrance，不是全局饱和度）
dull = Image.new("RGB", (4, 4), (140, 120, 100))     # 低饱和
rich = Image.new("RGB", (4, 4), (255, 40, 0))        # 高饱和


def _spread(px):
    return max(px) - min(px)


gain_dull = (_spread(processor._vibrance(dull, {"factor": 2.0}).getpixel((0, 0)))
             - _spread(dull.getpixel((0, 0))))
gain_rich = (_spread(processor._vibrance(rich, {"factor": 2.0}).getpixel((0, 0)))
             - _spread(rich.getpixel((0, 0))))
check("自然饱和度：低饱和增益 > 高饱和增益",
      gain_dull > gain_rich, "dull=%d rich=%d" % (gain_dull, gain_rich))
check("自然饱和度保留 alpha",
      processor._vibrance(rgba, {"factor": 2.0}).getpixel((0, 0))[3] == 77)

# --- 模糊 ---
check("模糊 radius=0 恒等",
      processor._blur(src, {"radius": 0}).getpixel((0, 0)) == (200, 100, 100))
check("模糊 radius=0.0 不被 ``or`` 吞掉（仍恒等）",
      processor._blur(src, {"radius": 0.0}).getpixel((0, 0)) == (200, 100, 100))
edge = Image.new("RGB", (16, 16), (0, 0, 0))
for x in range(8, 16):
    for y in range(16):
        edge.putpixel((x, y), (255, 255, 255))
blurred = processor._blur(edge, {"radius": 3})
mid_px = blurred.getpixel((8, 8))
check("高斯模糊真的糊了（边界不再是纯 0/255）",
      0 < mid_px[0] < 255, str(mid_px))
for method in ("GAUSSIAN", "BOX", "MEDIAN"):
    out = processor._blur(edge, {"radius": 3, "method": method})
    check("模糊 %s 能跑且尺寸不变" % method,
          out.size == edge.size and out.mode == "RGB")
check("中值偶数半径被夹成奇数（Pillow 不接受偶数）",
      processor._blur(edge, {"radius": 8, "method": "MEDIAN"}) is not None)
check("中值大半径被夹到 <=9",
      processor._blur(edge, {"radius": 200, "method": "MEDIAN"}) is not None)
check("中值 + RGBA 不崩（MedianFilter 不支持 RGBA）",
      processor._blur(rgba.convert("RGBA"), {"radius": 3,
                                             "method": "MEDIAN"}).mode == "RGBA")
check("未知 method 回退高斯",
      processor._blur(edge, {"radius": 3, "method": "NOPE"}) is not None)


# ======================================================================
# B) UI
# ======================================================================
print("\n=== B. UI ===")
win = mw.MainWindow()


def add_action(name):
    n = win.action_list.count()
    win._add_action_by_id(name)
    return win.action_list.item(n)


def widget_of(item):
    return win.action_list.itemWidget(item)


# ⚠️ 菜单已按类别分组，组标题是 addSection 的 separator action，要过滤掉
menu_ids = [a.data() for a in win.add_action_menu.actions()
            if not a.isSeparator()]
check("菜单里有 3 个新动作",
      all(t in menu_ids for t in ("饱和度", "自然饱和度", "模糊")),
      str(menu_ids))
check("菜单项数 = ACTION_TYPES 数",
      len(menu_ids) == len(processor.ACTION_TYPES))

# --- 饱和度 ---
it = add_action("饱和度")
w = widget_of(it)
check("饱和度有 factor 控件", "factor" in w._param_widgets)
spin = w._param_widgets["factor"]
check("factor 初值 1.0", spin.value() == 1.0, str(spin.value()))
spin.setValue(0.0)
check("factor=0 能写回 action dict（不被 or 吞掉）",
      it.data(Qt.UserRole)["params"]["factor"] == 0.0,
      str(it.data(Qt.UserRole)["params"]))
check("摘要跟着变（0.00）",
      "0.00" in w.summary_label.text(), w.summary_label.text())
spin.setValue(1.5)
check("factor=1.5 写回", it.data(Qt.UserRole)["params"]["factor"] == 1.5)

# --- 自然饱和度 ---
it2 = add_action("自然饱和度")
w2 = widget_of(it2)
check("自然饱和度有 factor 控件", "factor" in w2._param_widgets)
w2._param_widgets["factor"].setValue(1.6)
check("自然饱和度 factor 写回",
      it2.data(Qt.UserRole)["params"]["factor"] == 1.6)
check("自然饱和度上限 2.0（内部增量夹 ±1）",
      w2._param_widgets["factor"].maximum() == 2.0)

# --- 模糊 ---
it3 = add_action("模糊")
w3 = widget_of(it3)
check("模糊有 radius 与 method 两个控件",
      "radius" in w3._param_widgets and "method" in w3._param_widgets)
rad = w3._param_widgets["radius"]
mcombo = w3._param_widgets["method"]
check("radius 初值 2.0", rad.value() == 2.0, str(rad.value()))
check("method 默认 GAUSSIAN", mcombo.currentData() == "GAUSSIAN")
check("method 下拉显示译文且 userData 是英文 ID",
      [mcombo.itemData(i) for i in range(mcombo.count())]
      == [mid for mid, _ in processor.BLUR_METHODS],
      str([mcombo.itemData(i) for i in range(mcombo.count())]))

rad.setValue(50.0)
mid_idx = next(i for i in range(mcombo.count())
               if mcombo.itemData(i) == "MEDIAN")
mcombo.setCurrentIndex(mid_idx)
check("切到中值后 method 写回 action dict",
      it3.data(Qt.UserRole)["params"]["method"] == "MEDIAN")
check("切到中值后半径上限收到 9", rad.maximum() == 9.0, str(rad.maximum()))
check("切到中值后当前值被夹到 9（界面与实际一致）",
      rad.value() == 9.0, str(rad.value()))
check("夹过的半径也写回了 action dict",
      it3.data(Qt.UserRole)["params"]["radius"] == 9.0)
g_idx = next(i for i in range(mcombo.count())
             if mcombo.itemData(i) == "GAUSSIAN")
mcombo.setCurrentIndex(g_idx)
check("切回高斯后上限恢复", rad.maximum() == mw._BLUR_MAX_RADIUS)
check("摘要含半径与滤波器名",
      "9.0" in w3.summary_label.text() and "高斯" in w3.summary_label.text(),
      w3.summary_label.text())

# 从配置载入「中值 + 半径 40」这种越界数据：控件与摘要都要按夹过的 9 显示
# （PySide6 的 item.setData 是拷贝，测试里不能原地改 item 的 dict，
#  必须走 _add_action_item 重新构造，与启动时读配置的路径一致）。
it4 = win._add_action_item(
    {"type": "模糊", "params": {"method": "MEDIAN", "radius": 40.0},
     "enabled": True})
w4 = widget_of(it4)
rad4 = w4._param_widgets["radius"]
check("载入越界配置：半径上限收到 9", rad4.maximum() == 9.0, str(rad4.maximum()))
check("载入越界配置：值被夹到 9", rad4.value() == 9.0, str(rad4.value()))
check("载入越界配置：摘要也按 9 显示（不出现 r=40）",
      "9.0" in w4.summary_label.text() and "40" not in w4.summary_label.text(),
      w4.summary_label.text())

# --- 启用的动作能被 collect 到 ---
enabled = [a["type"] for a in win._collect_actions()]
check("3 个新动作都进 _collect_actions",
      all(t in enabled for t in ("饱和度", "自然饱和度", "模糊")),
      str(enabled))

# --- 端到端：真的按参数处理出图 ---
acts = [{"type": "饱和度", "params": {"factor": 0.0}},
        {"type": "自然饱和度", "params": {"factor": 1.5}},
        {"type": "模糊", "params": {"radius": 2.0, "method": "GAUSSIAN"}}]
try:
    out = processor.apply_actions(Image.new("RGB", (8, 8), (180, 90, 40)), acts)
    check("三动作链式执行不抛异常", out.size == (8, 8))
except Exception as exc:                                   # pragma: no cover
    check("三动作链式执行不抛异常", False, repr(exc))

print()
print("通过 %d 项" % passed)
if failures:
    print("失败 %d 项:" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过")
