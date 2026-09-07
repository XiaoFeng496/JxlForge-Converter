# -*- coding: utf-8 -*-
r"""``combo_switch`` 代理的 headless 回归。

覆盖「切主题后下拉实现实时切换」这条新路径必须成立的不变量：
切换实现不丢 items / 不丢 currentIndex、不误触发主项目回调、信号仍可转发、
代理能被 ``findChildren`` 找到（主项目 ``_refresh_combo_styles`` 依赖它）。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, r"F:\Agent工作空间\libjxl_GUI"):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication, QComboBox, QWidget  # noqa: E402

from jxlforge import combo_switch  # noqa: E402
from jxlforge import no_flicker_combo as A  # noqa: E402

app = QApplication([])

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


# 用原生 QComboBox 充当「原生档」实现，A 方案 drop-in 充当「自绘档」实现
combo_switch.configure(native_cls=QComboBox,
                       custom_cls=A.NoFlickerPrototypeComboBox,
                       theme_getter=lambda: "native_noflicker_proto")
Proxy = combo_switch.SwitchableComboBox

print("— 基本构造与初始模式 —")
cb = Proxy()
cb.addItems(["有损", "无损", "JPG 重编码"])
cb.setCurrentIndex(1)
check("默认主题=native_noflicker_proto（原生 NoFlicker 框）→ 自绘档", not cb.is_native_mode())
check("count", cb.count() == 3, "count=%d" % cb.count())
check("currentText", cb.currentText() == "无损", cb.currentText())

print("— 信号转发 —")
got = []
cb.currentIndexChanged.connect(lambda i: got.append(i))
cb.setCurrentIndex(2)
check("setCurrentIndex 触发代理信号", got == [2], str(got))

print("— 切到原生实现 —")
cb.set_mode(True)
check("模式已切原生", cb.is_native_mode())
check("切换后 count 保留", cb.count() == 3, "count=%d" % cb.count())
check("切换后 currentIndex 保留", cb.currentIndex() == 2,
      "idx=%d" % cb.currentIndex())
check("切换后 currentText 保留", cb.currentText() == "JPG 重编码",
      cb.currentText())
check("切换本身不误触发回调", got == [2], str(got))

print("— 切回自绘实现 —")
cb.set_mode(False)
check("模式已切自绘", not cb.is_native_mode())
check("切回后 count", cb.count() == 3)
check("切回后 currentIndex", cb.currentIndex() == 2)
check("切回后 currentText", cb.currentText() == "JPG 重编码", cb.currentText())

print("— 带 userData 的项在切换后不丢 —")
cb2 = Proxy()
cb2.addItem("A", "keyA")
cb2.addItem("B", "keyB")
check("findData", cb2.findData("keyB") == 1)
check("itemData", cb2.itemData(1) == "keyB")
cb2.set_mode(True)
check("切原生后 findData", cb2.findData("keyB") == 1)
check("切原生后 itemData", cb2.itemData(1) == "keyB")
cb2.set_mode(False)
check("切回自绘后 itemData", cb2.itemData(1) == "keyB")

print("— 主题刷新入口 _apply_fusion_style 双向不崩 —")
try:
    cb2.set_mode(True)
    cb2._apply_fusion_style()
    cb2.set_mode(False)
    cb2._apply_fusion_style()
    check("_apply_fusion_style 双向不崩", True)
except Exception as exc:  # noqa: BLE001
    check("_apply_fusion_style 双向不崩", False, repr(exc))

print("— 主项目依赖：findChildren / clear / 尺寸接口 —")
host = QWidget()
p = Proxy(host)
p.addItems(["x", "y"])
check("findChildren(代理) 能找到", len(host.findChildren(Proxy)) == 1,
      "n=%d" % len(host.findChildren(Proxy)))
p.setMinimumWidth(120)
p.setMaximumWidth(300)
check("setMinimumWidth/setMaximumWidth 不崩", True)
check("sizeHint 有宽度", p.sizeHint().width() > 0,
      "w=%d" % p.sizeHint().width())
p.clear()
check("clear 后 count=0", p.count() == 0)
check("clear 后 currentText 为空", p.currentText() == "",
      repr(p.currentText()))

print("— 主题→实现映射 —")
# 用户定：只有「原生（NoFlicker框）」(native_noflicker_proto) 用自绘 NoFlicker 原型；
# native_noflicker（原生 Fusion 框）/ native / fusion 都是真原生 QComboBox，
# 否则这几档在下拉观感上就没有区别了。
check("native_noflicker_proto（原生 NoFlicker 框）→ 自绘原型",
      not combo_switch.use_native_for_theme("native_noflicker_proto"))
check("native_noflicker（原生 Fusion 框）→ 真原生 QComboBox",
      combo_switch.use_native_for_theme("native_noflicker"))
check("native → 真原生 QComboBox", combo_switch.use_native_for_theme("native"))
check("fusion → 真原生 QComboBox", combo_switch.use_native_for_theme("fusion"))

print("— popup 背景角色开关(base/window) —")
# 像素取样发现原生 QComboBox 下拉内部色取 palette(window)（融入容器），
# 我们历史取 palette(base)（浅色下纯白、像独立卡片）。做成运行时开关，
# 由真机 A/B 决定用哪个。这里锁住「开关能下发给自绘档 + 非法值被忽略」。
cb3 = Proxy()
cb3.addItems(["x", "y"])
cb3.set_popup_bg_role("window")
check("set_popup_bg_role(window) 生效", cb3.popup_bg_role() == "window",
      cb3.popup_bg_role())
cb3.set_popup_bg_role("base")
check("set_popup_bg_role(base) 生效", cb3.popup_bg_role() == "base",
      cb3.popup_bg_role())
cb3.set_popup_bg_role("garbage")  # 非法值静默忽略
check("非法角色被忽略（保持 base）", cb3.popup_bg_role() == "base",
      cb3.popup_bg_role())

# 批量入口：root=host 时只刷新该 host 的代理（与 apply_mode_for_theme 同策略）
host2 = QWidget()
p_a = Proxy(host2); p_a.addItems(["a"])
p_b = Proxy(host2); p_b.addItems(["b"])
n = combo_switch.apply_popup_bg_role("window", root=host2)
check("apply_popup_bg_role 刷新数量=2", n == 2, "n=%d" % n)
check("批量后 p_a 角色=window", p_a.popup_bg_role() == "window")
check("批量后 p_b 角色=window", p_b.popup_bg_role() == "window")

# 最底层：QSS 解析器真把 palette(base) 换成 palette(window)
inst = A.NoFlickerPrototypeComboBox()
inst.set_popup_bg_role("window")
resolved = inst._resolved_popup_qss(inst._POPUP_QSS_FRAMELESS)
check("window 角色把 palette(base) 替换为 palette(window)",
      "palette(base)" not in resolved and "palette(window)" in resolved)
inst.set_popup_bg_role("base")
resolved2 = inst._resolved_popup_qss(inst._POPUP_QSS_FRAMELESS)
check("base 角色保留 palette(base)", "palette(base)" in resolved2)

print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
