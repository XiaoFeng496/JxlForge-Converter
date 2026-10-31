# -*- coding: utf-8 -*-
"""回归：输出冲突策略（overwrite / ask / skip / rename）的存取往返。

事故背景
--------
某版本在 userData 缺失时把 ``currentText()``（**当前语言的显示名**）写进了
ini。之后的加载逻辑 ``findData`` + ``findText`` 双双落空，又没有 else 分支，
于是组合框停在第一项「替换」——用户看到的是「重命名悄悄变成替换」，
全程没有任何提示。

这里锁四件事：
  1. 写进 ini 的永远是 ASCII 英文 ID（绝不写译文）；
  2. 三种语言下「保存 → 重启」往返一致；
  3. v1 中文旧值、以及历史脏值（各语言显示名）都能还原成 ID；
  4. 真正无法识别的值必须抛 ``RuntimeWarning``，不能静默改用户设置。
"""
import os
import sys
import tempfile
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter-test-on-exist")
# 隔离 QSettings：绝不污染真实 ini。
_tmp_dir = tempfile.mkdtemp(prefix="jxlforge_test_on_exist_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_dir)

from jxlforge import i18n                     # noqa: E402
from jxlforge.main_window import (            # noqa: E402
    MainWindow, ON_EXIST_IDS, ON_EXIST_LABELS, DEFAULT_ON_EXIST,
    LEGACY_ON_EXIST_MAP,
)

_app = QApplication.instance() or QApplication(sys.argv)

total = 0
failures = []


def check(name, cond, detail=""):
    global total
    total += 1
    if cond:
        print("PASS  %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s %s" % (name, detail))


def fresh_window():
    """只构造不 show：加载在 __init__ 里已完成，够验证持久化往返。"""
    return MainWindow()


def write_on_exist(value):
    s = QSettings()
    s.beginGroup("jxl_output")
    s.setValue("on_exist", value)
    s.endGroup()
    s.sync()


def read_on_exist():
    s = QSettings()
    s.beginGroup("jxl_output")
    v = s.value("on_exist")
    s.endGroup()
    return v


print("=== 1. 三种语言下 保存 → 重启 往返一致 ===")
for code in ("zh_CN", "en_US", "zh_TW"):
    i18n.set_language(code)
    w = fresh_window()
    ok_all = True
    written = []
    for eid in ON_EXIST_IDS:
        idx = w.on_exist_combo.findData(eid)
        if idx < 0:
            ok_all = False
            written.append("%s: combo 里没有该项目" % eid)
            continue
        w.on_exist_combo.setCurrentIndex(idx)
        w._save_jxl_output()
        stored = read_on_exist()
        written.append(stored)
        if stored != eid:
            ok_all = False
    check("[%s] 保存后 ini 里是英文 ID" % code, ok_all, str(written))
    check("[%s] 写出的值全部是 ASCII" % code,
          all(isinstance(v, str) and v.isascii() for v in written),
          str(written))

    # 最后一项是 rename，重启必须还是它
    w2 = fresh_window()
    check("[%s] 重启后仍是 rename" % code,
          w2.on_exist_combo.currentData() == "rename",
          "got=%r" % w2.on_exist_combo.currentData())
    check("[%s] 重启后显示的是当前语言译文" % code,
          w2.on_exist_combo.currentText()
          == i18n.t(ON_EXIST_LABELS["rename"]),
          "got=%r" % w2.on_exist_combo.currentText())

print("\n=== 2. v1 中文旧值仍能还原 ===")
for label, eid in LEGACY_ON_EXIST_MAP.items():
    write_on_exist(label)
    w = fresh_window()
    check("中文旧值 %r → %s" % (label, eid),
          w.on_exist_combo.currentData() == eid,
          "got=%r" % w.on_exist_combo.currentData())

print("\n=== 3. 历史脏值（各语言显示名）也能还原 ===")
for code in ("en_US", "zh_TW"):
    shown = i18n.translations(code).get(ON_EXIST_LABELS["rename"])
    if not shown:
        check("[%s] 有 rename 译文可测" % code, False, "字典缺该键")
        continue
    write_on_exist(shown)
    w = fresh_window()
    check("%s 显示名 %r → rename" % (code, shown),
          w.on_exist_combo.currentData() == "rename",
          "got=%r" % w.on_exist_combo.currentData())

print("\n=== 4. 无法识别的值必须告警并回退默认 ===")
write_on_exist("这不是任何策略")
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    w = fresh_window()
check("未知值回退到默认策略",
      w.on_exist_combo.currentData() == DEFAULT_ON_EXIST,
      "got=%r" % w.on_exist_combo.currentData())
check("未知值触发了 RuntimeWarning",
      any(issubclass(x.category, RuntimeWarning) for x in caught),
      str([str(x.category.__name__) for x in caught]))
check("告警里带上了原始值",
      any("这不是任何策略" in str(x.message) for x in caught),
      str([str(x.message) for x in caught]))

print("\n=== 5. 控件取不到 ID 时不得写译文 ===")
i18n.set_language("en_US")
w = fresh_window()
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    # 模拟异常：项被清空，currentData() 为 None（clear 自身会触发一次保存）
    w.on_exist_combo.clear()
    w._save_jxl_output()
stored = read_on_exist()
check("取不到 ID 时写默认值而非译文",
      stored == DEFAULT_ON_EXIST, "got=%r" % stored)
check("取不到 ID 时发出告警",
      any(issubclass(x.category, RuntimeWarning) for x in caught),
      str([str(x.category.__name__) for x in caught]))
check("ini 里没有非 ASCII 值",
      isinstance(stored, str) and stored.isascii(), repr(stored))

i18n.set_language("zh_CN")
print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
if failures:
    for f in failures:
        print("  - %s" % f)
    sys.exit(1)
print("ALL_OK")
