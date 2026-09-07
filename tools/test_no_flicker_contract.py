# -*- coding: utf-8 -*-
"""代理 API 契约测试：SwitchableComboBox 必须完整转发主项目用到的 QComboBox API。

为什么需要这份测试
------------------
代理（``combo_switch.SwitchableComboBox``）的 API 转发是**手写**的，每加一个
方法就多一处缺口。真实事故：代理 ``addItem`` 的参数名写成 ``data``，而
``QComboBox`` 是 ``userData``，于是 ``addItem(text, userData=key)`` 抛
TypeError——位置调用全过、关键字调用全崩，只在跑全量回归时才炸。

⚠️ ``hasattr`` 靠不住：代理的 ``__getattr__`` 会把未知名字兜底转发给内部实现，
所以 ``hasattr(proxy, 'insertItem')`` 恒为 True，但 custom 模式下真调会
AttributeError（自绘原型没实现）。**必须真调用，不能只看 hasattr。**

两层断言
--------
1. **契约**：17 个 API，四种目标（原生实现 / 自绘实现 / 代理 native 模式 /
   代理 custom 模式）跑同一组调用，结果与原生 ``QComboBox`` **逐项相等**。
   刻意混用 ``addItem`` 的三种调用约定（单参 / 位置第二参 / ``userData=`` 关键字），
   第三种正是曾经炸掉的那种。
2. **无缺口**：自绘原型必须实现 REQUIRED 全部 API。代理会兜底转发，缺口只在
   真机点开时才炸，所以在这里提前卡死。

REQUIRED 的来源
---------------
前 14 个是 ``main_window.py`` 在下拉上**实际调用**过的（统计得出）；
后 3 个（``insertItem`` / ``removeItem`` / ``setItemData``）主项目暂未使用，
但代理会兜底转发给自绘原型，缺了就是潜在 AttributeError，已一并补齐并纳入契约。

迁移自原型仓的 ``test_combo_switch.py`` 测的是「切换行为」（切主题不丢 items /
信号转发 / findChildren），本文件测的是「调用面」，两者互补，不要合并。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

import libjxl_gui.main_window as mw  # noqa: E402
from libjxl_gui import combo_switch as cs  # noqa: E402
from libjxl_gui import no_flicker_combo as nfc  # noqa: E402

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


# main_window 在下拉上实际用到的 14 个 + 主动补齐的 3 个。
# 不含 setEnabled/setToolTip 等 QWidget 通用方法（Qt 父子传播天然生效）。
REQUIRED = (
    "addItem", "addItems", "count", "itemText", "itemData",
    "currentIndex", "currentText", "currentData", "setCurrentIndex",
    "setCurrentText", "findText", "findData", "clear",
    "setSizeAdjustPolicy",
    "insertItem", "removeItem", "setItemData",
)


def _snapshot(c):
    """对任一实现跑同一组调用，返回结果快照。"""
    c.clear()
    # 三种 addItem 调用约定混用：单参 / 位置第二参 / userData 关键字
    c.addItem("有损")
    c.addItem("无损", "lossless")
    c.addItem("JPG 重编码", userData="jpg")
    c.addItems(["A", "B"])
    n = c.count()
    snap = {
        "count": n,
        "texts": [c.itemText(i) for i in range(n)],
        "datas": [c.itemData(i) for i in range(n)],
    }
    c.setCurrentIndex(1)
    snap["at1"] = (c.currentIndex(), c.currentText(), c.currentData())
    c.setCurrentText("JPG 重编码")
    snap["after_set_text"] = (c.currentIndex(), c.currentText())
    snap["findText"] = c.findText("无损")
    snap["findData"] = c.findData("jpg")
    c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    snap["size_adjust_ok"] = True

    # 补齐项：插入 → 改 data → 删除，验证与原生一致的语义
    c.insertItem(1, "X", "dx")
    snap["after_insert"] = (c.count(),
                            [c.itemText(i) for i in range(c.count())])
    snap["insert_data"] = c.itemData(1)
    c.setItemData(0, "da")
    snap["set_item_data"] = c.itemData(0)
    c.removeItem(1)
    snap["after_remove"] = (c.count(),
                            [c.itemText(i) for i in range(c.count())])
    return snap


def _proxy(use_native):
    p = cs.SwitchableComboBox()
    p.set_mode(use_native)
    return p


print("— 第 1 层：契约（%d 个 API，与原生 QComboBox 行为逐项一致）—" % len(REQUIRED))
ref = _snapshot(QComboBox())
targets = [
    ("NativeNoFlickerComboBox", lambda: mw.NativeNoFlickerComboBox()),
    ("NoFlickerPrototypeComboBox", lambda: nfc.NoFlickerPrototypeComboBox()),
    ("代理 / native 模式", lambda: _proxy(True)),
    ("代理 / custom 模式", lambda: _proxy(False)),
]
for name, factory in targets:
    try:
        got = _snapshot(factory())
    except Exception as exc:  # noqa: BLE001 - 任何异常都要记成失败
        check(name, False, "抛 %s: %s" % (type(exc).__name__, exc))
        continue
    diff = {k: (ref[k], got[k]) for k in ref if ref[k] != got[k]}
    check(name, not diff, "" if not diff else "差异=%s" % diff)

print("— 第 2 层：无缺口（自绘原型必须实现 REQUIRED 全部 API）—")
proto = nfc.NoFlickerPrototypeComboBox()
missing_now = tuple(m for m in REQUIRED if not hasattr(proto, m))
check("自绘原型已实现全部 %d 个 API" % len(REQUIRED), not missing_now,
      "" if not missing_now else "缺失=%s" % sorted(missing_now))

# 代理兜底转发会把缺口藏起来，这里同样用真调用确认无缺口（而不只是 hasattr）
try:
    _snapshot(_proxy(False))
    check("代理 custom 模式真调用无缺口", True)
except Exception as exc:  # noqa: BLE001
    check("代理 custom 模式真调用无缺口", False,
          "抛 %s: %s" % (type(exc).__name__, exc))

print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
