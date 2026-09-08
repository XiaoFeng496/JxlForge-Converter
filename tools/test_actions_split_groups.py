# -*- coding: utf-8 -*-
"""回归：亮度/对比度拆分 + 添加动作菜单分组 + 模糊参数框宽度。

1) 亮度/对比度拆成两个独立动作
   - 菜单里只有「亮度」「对比度」，不再有合并项；各自一个 factor 参数。
   - 拆分后**结果等价**：旧「亮度/对比度」= 先调亮度再调对比度，
     与迁移后的两个动作链式执行逐像素一致（migrate_legacy_actions 的正确性）。
   - 旧数据（已存进 ini 的老配置）仍能处理，载入时自动升级成两个动作。

2) 「添加动作 ▶」菜单按类别分组
   - ACTION_GROUPS 覆盖且仅覆盖 ACTION_TYPES（对账，防止漏/重复）。
   - 菜单顺序 == ACTION_TYPES 顺序（组标题是 separator，不算动作项）。

3) 模糊半径框不再比同类参数框宽
   - QDoubleSpinBox 的 sizeHint 按「能显示的最大值」算，范围 0–250 会撑到
     108px，而同类 0.0–3.0 的框只有 84px。上限收到 50 + 1 位小数后应对齐。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSlider, QSpinBox

app = QApplication.instance() or QApplication(sys.argv)

from jxlforge import main_window as mw
from jxlforge import main_window
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
# 1. 亮度 / 对比度 拆分
# ======================================================================
print("=== 1. 亮度/对比度拆分 ===")
check("菜单里不再有合并的「亮度/对比度」",
      "亮度/对比度" not in processor.ACTION_TYPES, str(processor.ACTION_TYPES))
check("新增独立的「亮度」「对比度」",
      "亮度" in processor.ACTION_TYPES and "对比度" in processor.ACTION_TYPES)
check("两者各有 factor 默认参数",
      processor.DEFAULT_PARAMS.get("亮度", {}).get("factor") == 1.0
      and processor.DEFAULT_PARAMS.get("对比度", {}).get("factor") == 1.0)

src = Image.new("RGB", (8, 8), (60, 120, 200))
br = processor.apply_actions(src, [{"type": "亮度", "params": {"factor": 1.4}}])
check("亮度 1.4 真的变亮",
      br.getpixel((0, 0))[1] > src.getpixel((0, 0))[1],
      str(br.getpixel((0, 0))))
co = processor.apply_actions(src, [{"type": "对比度", "params": {"factor": 2.0}}])
check("对比度 2.0 真的拉开明暗",
      (max(co.getpixel((0, 0))) - min(co.getpixel((0, 0))))
      > (max(src.getpixel((0, 0))) - min(src.getpixel((0, 0)))),
      str(co.getpixel((0, 0))))
# apply_actions 统一按 RGBA 处理，比较时只看前 3 通道。
check("亮度 factor=1 恒等",
      processor.apply_actions(src, [{"type": "亮度", "params": {"factor": 1.0}}])
      .getpixel((0, 0))[:3] == (60, 120, 200))
check("对比度 factor=1 恒等",
      processor.apply_actions(src, [{"type": "对比度", "params": {"factor": 1.0}}])
      .getpixel((0, 0))[:3] == (60, 120, 200))
check("亮度 factor=0 不被 ``or`` 吞掉（能压到全黑）",
      processor.apply_actions(src, [{"type": "亮度", "params": {"factor": 0.0}}])
      .getpixel((0, 0))[:3] == (0, 0, 0))

# --- 老数据兼容 + 拆分等价性 ---
old = {"type": "亮度/对比度", "params": {"brightness": 1.3, "contrast": 0.8},
       "enabled": True}
legacy = processor.apply_actions(src, [old])
migrated = processor.migrate_legacy_actions([old])
check("迁移产出两个动作（亮度 + 对比度）",
      [a["type"] for a in migrated] == ["亮度", "对比度"],
      str([a["type"] for a in migrated]))
check("迁移带上原 factor 值",
      [a["params"]["factor"] for a in migrated] == [1.3, 0.8],
      str([a["params"] for a in migrated]))
check("迁移保留 enabled / 不残留旧键",
      all(a.get("enabled") is True for a in migrated)
      and all("brightness" not in a["params"] and "contrast" not in a["params"]
              for a in migrated))
check("迁移后出图与旧动作**逐像素等价**",
      processor.apply_actions(src, migrated).tobytes() == legacy.tobytes())
check("旧类型仍能被处理（兼容手改 ini）", legacy.size == src.size)
check("无关动作不被迁移函数改动",
      processor.migrate_legacy_actions([{"type": "锐化", "params": {}}])
      == [{"type": "锐化", "params": {}}])

# --- UI：两个动作各有自己的参数控件 ---
win = mw.MainWindow()


def add_action(name):
    n = win.action_list.count()
    win._add_action_by_id(name)
    return win.action_list.item(n)


it_b = add_action("亮度")
w_b = win.action_list.itemWidget(it_b)
check("亮度项有 factor 控件", "factor" in w_b._param_widgets)
w_b._param_widgets["factor"].setValue(1.25)
check("亮度 factor 写回 action dict",
      it_b.data(Qt.UserRole)["params"]["factor"] == 1.25)
check("亮度摘要含数值", "1.25" in w_b.summary_label.text(),
      w_b.summary_label.text())
it_c = add_action("对比度")
w_c = win.action_list.itemWidget(it_c)
w_c._param_widgets["factor"].setValue(0.75)
check("对比度 factor 写回 action dict",
      it_c.data(Qt.UserRole)["params"]["factor"] == 0.75)
check("两个动作互不干扰",
      it_b.data(Qt.UserRole)["params"]["factor"] == 1.25)

# ======================================================================
# 2. 菜单分组
# ======================================================================
print("\n=== 2. 添加动作菜单分组 ===")
grouped = [aid for _, ids in processor.ACTION_GROUPS for aid in ids]
check("ACTION_GROUPS 与 ACTION_TYPES 一一对应",
      grouped == list(processor.ACTION_TYPES),
      "groups=%s types=%s" % (grouped, list(processor.ACTION_TYPES)))
acts = [a for a in win.add_action_menu.actions() if not a.isSeparator()]
check("菜单动作项数 == ACTION_TYPES 数",
      len(acts) == len(processor.ACTION_TYPES))
check("菜单顺序 == ACTION_TYPES 顺序",
      [a.data() for a in acts] == list(processor.ACTION_TYPES),
      str([a.data() for a in acts]))
seps = [a for a in win.add_action_menu.actions() if a.isSeparator()]
check("分组标题数 == ACTION_GROUPS 数",
      len(seps) == len(processor.ACTION_GROUPS), str(len(seps)))
# 组标题也是「中文即 ID」，字典里必须有译文（否则英文界面退回中文）。
from jxlforge import i18n

i18n.set_language("en_US")
check("每个分组标题在字典里都有译文",
      all(i18n.has_translation(g) for g, _ in processor.ACTION_GROUPS),
      str([g for g, _ in processor.ACTION_GROUPS
           if not i18n.has_translation(g)]))
i18n.set_language("zh_CN")
# 分类合理性的粗校验：几何组在前、叠加（水印）在最后
check("分组顺序：几何在前、水印在最后",
      processor.ACTION_GROUPS[0][1][0] == "调整大小"
      and processor.ACTION_GROUPS[-1][1] == ["水印"],
      str([g[0] for g in processor.ACTION_GROUPS]))

# ======================================================================
# 3. 下拉框宽度：_build_param_widgets 末尾集中 cap，对齐同动作 SpinBox
#    根因：调整大小·算法原标签 "LANCZOS (高质量, 默认)" 20+ 字符 → sizeHint 234，
#    把整列撑爆；模糊/水印下拉只有 54–90，本身没超，但要跟同动作 SpinBox 等宽。
# ======================================================================
print("\n=== 3. 下拉框宽度（集中 cap 对齐同动作 SpinBox） ===")
win2 = mw.MainWindow()


def _spin_max_of(window, action_name, spin_keys):
    """加一个动作，返回其 item widget 与「最宽 SpinBox 的 sizeHint 宽度」。"""
    n = window.action_list.count()
    window._add_action_by_id(action_name)
    w = window.action_list.itemWidget(window.action_list.item(n))
    sm = 0
    for k in spin_keys:
        sm = max(sm, w._param_widgets[k].sizeHint().width())
    return w, sm


# --- 模糊：SpinBox(半径)+下拉(算法) ---
b_w, _ = _spin_max_of(win2, "模糊", ["radius"])
rad = b_w._param_widgets["radius"]
method_w = b_w._param_widgets["method"]
# 锐化作为同类浮点框参照（不 cap，验证自然等宽）
s_w, _ = _spin_max_of(win2, "锐化", ["factor"])
ref = s_w._param_widgets["factor"]
check("模糊半径框与锐化强度框自然等宽（sizeHint 一致）",
      rad.sizeHint().width() == ref.sizeHint().width(),
      "radius=%d sharpen=%d" % (rad.sizeHint().width(), ref.sizeHint().width()))
check("半径上限收到 50（不再 250）", rad.maximum() == 50.0, str(rad.maximum()))
check("半径只显示 1 位小数", rad.decimals() == 1, str(rad.decimals()))
# SpinBox 不被 cap：Qt 默认 minW==0（未设任何宽度约束）
check("模糊半径 SpinBox 未被 cap（minW==0）", rad.minimumWidth() == 0,
      "minW=%d" % rad.minimumWidth())
# 下拉被 cap 到 _PARAM_COMBO_REF_WIDTH（模糊动作 spin_max=半径 sizeHint≈84）
check("模糊算法下拉被 cap 到 _PARAM_COMBO_REF_WIDTH",
      method_w.maximumWidth() == main_window._PARAM_COMBO_REF_WIDTH,
      "maxW=%d ref=%d" % (method_w.maximumWidth(),
                          main_window._PARAM_COMBO_REF_WIDTH))
check("模糊算法下拉不是 setMinimumWidth 撑大（minW==0）",
      method_w.minimumWidth() == 0, "minW=%d" % method_w.minimumWidth())

# --- 调整大小：算法下拉原标签 20+ 字符，sizeHint 234，是「撑宽」真凶 ---
r_w, r_spin = _spin_max_of(win2, "调整大小", ["width", "height"])
algo_w = r_w._param_widgets["algorithm"]
check("调整大小算法下拉被 cap（不再 234 撑爆整列）",
      algo_w.maximumWidth() == r_spin and algo_w.maximumWidth() < 234,
      "maxW=%d spin_max=%d" % (algo_w.maximumWidth(), r_spin))
check("调整大小算法下拉与同动作 SpinBox 等宽",
      algo_w.maximumWidth() == r_spin,
      "maxW=%d spin_max=%d" % (algo_w.maximumWidth(), r_spin))

# --- 水印：位置/颜色下拉，cap 后应与同动作 SpinBox 等宽（且 >= 基准 84）---
wm_w, wm_spin = _spin_max_of(win2, "水印", ["font_size", "opacity"])
pos_w = wm_w._param_widgets["position"]
col_w = wm_w._param_widgets["color"]
_wm_ref = max(wm_spin, main_window._PARAM_COMBO_REF_WIDTH)
check("水印位置下拉被 cap（与同动作 SpinBox 等宽，>=基准84）",
      pos_w.maximumWidth() == _wm_ref, "maxW=%d ref=%d" % (pos_w.maximumWidth(), _wm_ref))
check("水印颜色下拉同样被 cap",
      col_w.maximumWidth() == _wm_ref, "maxW=%d" % col_w.maximumWidth())

# ======================================================================
# 4. 数值参数拖拽条：滑块放参数名与数字框中间，数字框在条右边，双向同步
# =====================================================================
print("\n=== 4. 数值参数拖拽条（滑块放中间，数字框在右）===")


def _slider_of(w, key):
    """取某数值参数对应的拖拽条（_param_sliders 以数值框为键）。"""
    return w._param_sliders.get(w._param_widgets[key])


# --- 锐化：浮点框 0..5 step0.1，应映射成 0..50 的整数滑块 ---
fac_sb = s_w._param_widgets["factor"]
fac_sl = _slider_of(s_w, "factor")
check("锐化 factor 带拖拽条（QSlider）",
      isinstance(fac_sl, QSlider), type(fac_sl).__name__)
# 数字框在滑块右边：控件列是一个 HBox，item0=滑块 item1=数字框
fac_ctrl = s_w.params_container.layout().itemAtPosition(0, 1).widget()
check("数字框在拖拽条右边（HBox: [滑块, 数字框]）",
      fac_ctrl.layout().itemAt(0).widget() is fac_sl
      and fac_ctrl.layout().itemAt(1).widget() is fac_sb)
check("浮点滑块量程 = round((5-0)/0.1) = 50",
      fac_sl.maximum() == 50, str(fac_sl.maximum()))
# 拖滑块 → 数字框同步
fac_sl.setValue(fac_sl.maximum())
check("拖滑块到最大 → 数字框同步到 5.0",
      abs(fac_sb.value() - 5.0) < 1e-6, str(fac_sb.value()))
# 改数字框 → 滑块同步
fac_sb.setValue(2.0)
check("改数字框 → 滑块同步（位置 > 0）",
      fac_sl.value() > 0, str(fac_sl.value()))

# --- 调整大小：宽/高是像素参数 → 不加拖拽条（验证 no_slider），但可正常设值 ---
wid_sb = r_w._param_widgets["width"]
wid_sl = _slider_of(r_w, "width")
check("调整大小 width 是像素参数 → 不带拖拽条",
      wid_sl is None, type(wid_sl).__name__)
wid_sb.setValue(1024)
check("设 width 数字框 = 1024 生效", wid_sb.value() == 1024, str(wid_sb.value()))
hei_sb = r_w._param_widgets["height"]
hei_sl = _slider_of(r_w, "height")
check("调整大小 height 是像素参数 → 不带拖拽条",
      hei_sl is None, type(hei_sl).__name__)

# --- 模糊 radius 是像素参数 → 不带拖拽条；切 MEDIAN 仅收半径上限到 9 ---
b_sl = _slider_of(b_w, "radius")
check("模糊 radius 是像素参数 → 不带拖拽条",
      b_sl is None, type(b_sl).__name__)
rad.setValue(8.0)
method_w.setCurrentIndex(2)   # MEDIAN
check("切 MEDIAN 后 radius 上限收到 9（功能不受影响）",
      rad.maximum() == main_window._BLUR_MEDIAN_MAX_RADIUS,
      "max=%s" % rad.maximum())

# --- 拖拽条拖动要触发参数回写（实时预览依赖此链路，issue #3）---
# 锐化 factor 带滑块：拖到最大应让 _emit('factor', ...) 被调用 → action 数据更新。
_emit_calls = []
_orig_emit = s_w._emit
def _spy_emit(key, value):
    _emit_calls.append((key, value))
    _orig_emit(key, value)
s_w._emit = _spy_emit
fac_sl.setValue(fac_sl.maximum())
s_w._emit = _orig_emit
check("拖滑块触发参数回写（_emit 被调用，实时预览链路打通）",
      len(_emit_calls) >= 1, str(_emit_calls))

# --- 回归：调 MEDIAN 仍把半径上限收 9（功能不受影响）---
n_blur = win2.action_list.count()
win2._add_action_by_id("模糊")
w_blur = win2.action_list.itemWidget(win2.action_list.item(n_blur))
rad = w_blur._param_widgets["radius"]
method_w = w_blur._param_widgets["method"]
gl_blur = w_blur.params_container.layout()
check("模糊两个参数各占一行（共 2 行）",
      gl_blur.rowCount() == 2, "rowCount=%d" % gl_blur.rowCount())
check("rad 初始值 = 2.0", rad.value() == 2.0)
check("method 初始 = GAUSSIAN",
      method_w.itemData(method_w.currentIndex()) == "GAUSSIAN")
rad.setValue(8.0); method_w.setCurrentIndex(2)   # MEDIAN
check("切到 MEDIAN 后 rad 上限收到 9",
      rad.maximum() == main_window._BLUR_MEDIAN_MAX_RADIUS,
      "max=%s" % rad.maximum())

print()
print("通过 %d 项" % passed)
if failures:
    print("失败 %d 项:" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过")
