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
# 3. 模糊参数框宽度
# ======================================================================
print("\n=== 3. 模糊参数框宽度 ===")
win2 = mw.MainWindow()


def param_widget(window, action_name, key):
    n = window.action_list.count()
    window._add_action_by_id(action_name)
    w = window.action_list.itemWidget(window.action_list.item(n))
    return w._param_widgets[key]


ref = param_widget(win2, "锐化", "factor")      # 0.0–5.0 的同类浮点框
rad = param_widget(win2, "模糊", "radius")
check("模糊半径框与同类参数框同宽",
      rad.sizeHint().width() == ref.sizeHint().width(),
      "radius=%d sharpen=%d" % (rad.sizeHint().width(), ref.sizeHint().width()))
check("半径上限收到 50（不再 250）", rad.maximum() == 50.0, str(rad.maximum()))
check("半径只显示 1 位小数", rad.decimals() == 1, str(rad.decimals()))
check("半径 50 仍能输入", rad.maximum() >= 50.0)

print()
print("通过 %d 项" % passed)
if failures:
    print("失败 %d 项:" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过")
