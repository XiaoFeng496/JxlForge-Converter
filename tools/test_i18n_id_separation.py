# -*- coding: utf-8 -*-
"""回归测试：内部 ID 与显示名分离（schema v2）。

契约只有两条，但极易被后续改动破坏：

  1. **内部 ID 永远是英文稳定 ID**（``resize`` / ``thumb`` / ``overwrite``），
     只用于存盘、比较、查表；一旦发布不可再改。
  2. **界面显示一律走 ``i18n.t(中文标签)``**，中文标签只作字典 key，
     优化文案时只改标签、补译文，不动任何已存数据。

具体体现在五处：
  * 菜单 / 下拉：``text`` 是译文，``data`` 必须是英文 ID；
  * 动作 dict：``type`` 必须是英文 ID，``params.position`` 同理；
  * 持久化：写进 ini 的必须是英文 ID + ``actions/version=2``；
  * 迁移：旧 ini 的中文 ID（v1）读取时自动升级，且幂等；
  * ``_action_summary``：摘要显示译文，但 ``action["type"]`` 不变。

为什么必须锁：ID 漂移的失败模式是**静默**的 —— apply_actions 找不到分支
就跳过，用户只会拿到一张"没处理"的原图，不报错。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)      # 导入 jxlforge
sys.path.insert(0, HERE)           # 导入 tools/ 下的分级脚本

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("JxlForge")
QCoreApplication.setApplicationName("JxlForge-Converter-test-i18n")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini。
_tmp_settings_dir = tempfile.mkdtemp(prefix="jxlforge_test_i18n_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from jxlforge import i18n
from jxlforge import processor
from jxlforge.main_window import (
    MainWindow,
    VIEW_MODES, VIEW_MODE_IDS, VIEW_MODE_LABELS, VIEW_MODE_ID_BY_LABEL,
    ON_EXIST_IDS, ON_EXIST_LABELS, ON_EXIST_ID_BY_LABEL,
    LEGACY_VIEW_MODE_MAP, LEGACY_ON_EXIST_MAP,
)

_app = QApplication.instance() or QApplication(sys.argv)

total = 0
failures = []


def check(name, cond, detail=""):
    global total
    total += 1
    if not cond:
        failures.append(name)
        print("FAIL  %s %s" % (name, detail))
    else:
        print("PASS  %s" % name)


def fresh_window():
    """构造 MainWindow 并触发首帧，使断言环境与真实启动一致。"""
    w = MainWindow()
    w._env_refreshed = True
    w.show()
    for _ in range(3):
        QApplication.instance().processEvents()
    return w


def clear_settings():
    s = QSettings()
    for group in ("actions", "input_view", "jxl_output"):
        s.remove(group)
    s.sync()


# ----------------------------------------------------------------------
# 0. i18n 模块本身
# ----------------------------------------------------------------------
print("=== i18n 模块 ===")
i18n.set_language("zh_CN")
check("中文模式字典为空（源码即译文）", i18n.translation_count() == 0,
      "count=%d" % i18n.translation_count())
check("中文模式 t() 原样返回", i18n.t("调整大小") == "调整大小")

i18n.set_language("en_US")
check("英文模式加载出译文", i18n.translation_count() > 0,
      "count=%d" % i18n.translation_count())
check("英文模式 t() 返回译文", i18n.t("调整大小") == "Resize",
      "got=%r" % i18n.t("调整大小"))
check("未收录条目回退原文", i18n.t("这个字符串没有译文") == "这个字符串没有译文")

# 元信息键（_meta / _actions ...）是给人看的分组注释，绝不能混进字典，
# 否则覆盖率统计虚高、且 t("_meta") 会返回一个 dict。
check("元信息键已被过滤",
      not any(k.startswith("_") for k in i18n._dict),
      str([k for k in i18n._dict if k.startswith("_")]))

# 未知语言必须回退，不能因为缺文件就让界面崩。
i18n.set_language("xx_YY")
check("未知语言安全回退（不抛异常）", i18n.t("调整大小") == "调整大小")
i18n.set_language("en_US")

# ----------------------------------------------------------------------
# 1. 英文界面下构造窗口：显示译文，取值英文 ID
# ----------------------------------------------------------------------
print("\n=== 英文界面：显示译文，取值英文 ID ===")
clear_settings()
w = fresh_window()

_acts = [a for a in w.add_action_menu.actions() if not a.isSeparator()]
check("添加动作菜单项数正确",
      len(_acts) == len(processor.ACTION_IDS))
check("菜单有分组标题（组数 == ACTION_GROUPS 数）",
      len([a for a in w.add_action_menu.actions() if a.isSeparator()])
      == len(processor.ACTION_GROUPS))
check("动作类型显示为英文",
      _acts[0].text() == "Resize",
      "got=%r" % _acts[0].text())
check("动作类型 userData 是英文稳定 ID",
      _acts[0].data() == "resize",
      "got=%r" % _acts[0].data())
_ids = [a.data() for a in _acts]
check("全部动作 userData 与 ACTION_IDS 一致",
      _ids == list(processor.ACTION_IDS), str(_ids))

# --- 冲突策略下拉 ---
EXPECT_ON_EXIST = {"overwrite": "Overwrite", "ask": "Ask",
                   "skip": "Skip", "rename": "Rename"}
_got = {w.on_exist_combo.itemData(i): w.on_exist_combo.itemText(i)
        for i in range(w.on_exist_combo.count())}
check("冲突策略 显示/ID 全部配对正确",
      _got == EXPECT_ON_EXIST, str(_got))
check("冲突策略默认取到英文 ID「overwrite」",
      w.on_exist_combo.currentData() == "overwrite",
      "got=%r" % w.on_exist_combo.currentData())

# --- 查看模式菜单 ---
_menu_texts = [a.text() for a in w.view_menu.actions()]
check("查看模式菜单显示为英文",
      "Thumbnails" in _menu_texts and "Details" in _menu_texts,
      str(_menu_texts))
check("查看模式菜单不含中文显示名",
      not any(t in _menu_texts for t in ("缩略图", "详细信息")),
      str(_menu_texts))
check("查看模式菜单 data 是英文 ID",
      [a.data() for a in w.view_menu.actions()] == VIEW_MODE_IDS,
      str([a.data() for a in w.view_menu.actions()]))
check("查看按钮显示译文", w.view_button.text() == "Thumbnails",
      "got=%r" % w.view_button.text())

# --- _action_summary ---
_sum = MainWindow._action_summary(None, {"type": "resize",
                                         "params": {"width": 1920,
                                                    "height": 1080}})
check("摘要里的类型名已翻译", _sum.startswith("Resize"), "got=%r" % _sum)
_sum2 = MainWindow._action_summary(None, {"type": "rotate",
                                          "params": {"angle": 90}})
check("摘要（旋转）类型名已翻译", _sum2.startswith("Rotate"), "got=%r" % _sum2)

# ----------------------------------------------------------------------
# 2. 英文界面下添加动作：存进 action dict 的必须是英文 ID
# ----------------------------------------------------------------------
print("\n=== 英文界面下添加动作 ===")
_before = len(w._all_action_data())
_first = [a for a in w.add_action_menu.actions() if not a.isSeparator()][0]
_first.trigger()                                 # 显示 "Resize"
QApplication.instance().processEvents()
_added = w._all_action_data()
check("添加后动作数 +1", len(_added) == _before + 1,
      "%d -> %d" % (_before, len(_added)))
check("刚添加的动作 type 是英文 ID",
      _added and _added[-1].get("type") == "resize",
      "got=%r" % (_added[-1] if _added else None))
check("刚添加的动作带默认参数",
      _added and "width" in _added[-1].get("params", {}),
      str(_added[-1] if _added else None))
_item_widget = w.action_list.itemWidget(
    w.action_list.item(w.action_list.count() - 1))
check("列表项摘要显示译文而非英文 ID",
      _item_widget is not None
      and _item_widget.summary_label.text().startswith("Resize"),
      "got=%r" % (_item_widget.summary_label.text()
                  if _item_widget else None))

# ----------------------------------------------------------------------
# 3. 英文界面下持久化：写进 ini 的必须是英文 ID + version=2
# ----------------------------------------------------------------------
print("\n=== 英文界面下持久化 ===")
_rename_idx = [w.on_exist_combo.itemData(i)
               for i in range(w.on_exist_combo.count())].index("rename")
w.on_exist_combo.setCurrentIndex(_rename_idx)
w._save_jxl_output()
w.save_actions_on_exit_check.setChecked(True)
w._save_actions_setting()
_s = QSettings()
check("ini 里的 on_exist 是英文 ID",
      _s.value("jxl_output/on_exist") == "rename",
      "got=%r" % _s.value("jxl_output/on_exist"))
check("ini 里写入 actions/version=2",
      _s.value("actions/version", 0, type=int) == processor.ACTIONS_SCHEMA_VERSION,
      "got=%r" % _s.value("actions/version"))

_raw = _s.value("actions/list", "", type=str)
_stored = json.loads(_raw) if _raw else []
check("ini 动作列表非空", bool(_stored))
check("ini 动作 type 全部是 ASCII 英文 ID",
      all(a.get("type", "").isascii() for a in _stored),
      str([a.get("type") for a in _stored]))
check("ini 动作 type 都在 ACTION_IDS 里",
      all(a.get("type") in processor.ACTION_IDS for a in _stored),
      str([a.get("type") for a in _stored]))

# 新建窗口（仍是英文）恢复策略：必须还能选中 rename
w2 = fresh_window()
check("英文重启后策略仍为 rename",
      w2.on_exist_combo.currentData() == "rename",
      "got=%r" % w2.on_exist_combo.currentData())

# ----------------------------------------------------------------------
# 4. 迁移：旧 ini（中文 ID）读取时升级为英文，且幂等
# ----------------------------------------------------------------------
print("\n=== v1 旧配置迁移 ===")
clear_settings()
_s = QSettings()
_s.setValue("actions/save_on_exit", True)
_s.setValue("actions/list", json.dumps([
    {"type": "旋转", "params": {"angle": 90}, "enabled": True},
    {"type": "水印", "params": {"text": "demo", "position": "右下"},
     "enabled": True},
    {"type": "亮度/对比度", "params": {"brightness": 1.2, "contrast": 0.8},
     "enabled": True},
], ensure_ascii=False))
_s.setValue("jxl_output/on_exist", "重命名")
_s.setValue("input_view/mode", "详细信息")
_s.sync()

w3 = fresh_window()
_migrated = w3._all_action_data()
check("v1 动作已迁移为英文 ID",
      [a["type"] for a in _migrated]
      == ["rotate", "watermark", "brightness", "contrast"],
      str([a["type"] for a in _migrated]))
check("水印位置已迁移为英文 ID",
      _migrated[1]["params"]["position"] == "bottom_right",
      str(_migrated[1]["params"]))
check("v1 冲突策略已迁移",
      w3.on_exist_combo.currentData() == "rename",
      "got=%r" % w3.on_exist_combo.currentData())
check("v1 视图模式已迁移",
      w3._last_view == "details",
      "got=%r" % w3._last_view)
check("迁移后界面显示仍是译文",
      w3.view_button.text() == "Details",
      "got=%r" % w3.view_button.text())

# 幂等：迁移两次结果一致
check("迁移函数幂等",
      processor.migrate_legacy_actions(_migrated) == _migrated)

# ----------------------------------------------------------------------
# 4b. 迁移表必须是「冻结快照」，不能跟着显示名派生
# ----------------------------------------------------------------------
# 为什么单独锁：迁移表如果由当前中文标签反查派生（dict(ACTION_ID_BY_LABEL)），
# 那么改一次文案（「调整大小」→「缩放」）就会让所有老用户的 ini 失配——
# apply_actions 只 warn 一句然后跳过，表现为「动作没了」，静默失效。
print("\n=== 迁移表冻结 ===")
check("动作迁移表不是派生别名（独立对象）",
      processor.LEGACY_ACTION_TYPE_MAP is not processor.ACTION_ID_BY_LABEL)
check("水印位置迁移表不是派生别名（独立对象）",
      processor.LEGACY_WATERMARK_POSITION_MAP
      is not processor.WATERMARK_POSITION_ID_BY_LABEL)
check("视图模式迁移表不是派生别名（独立对象）",
      LEGACY_VIEW_MODE_MAP is not VIEW_MODE_ID_BY_LABEL)
check("冲突策略迁移表不是派生别名（独立对象）",
      LEGACY_ON_EXIST_MAP is not ON_EXIST_ID_BY_LABEL)

# 条目数写死 = 冻结证明：新增动作**不允许**往迁移表追加（新数据一律写英文 ID）。
check("动作迁移表条目数已冻结（13 动作 + 亮度/对比度）",
      len(processor.LEGACY_ACTION_TYPE_MAP) == 14,
      "len=%d" % len(processor.LEGACY_ACTION_TYPE_MAP))
check("动作迁移表的取值都是合法 ID",
      all(v in processor.ACTION_IDS or v == "brightness_contrast"
          for v in processor.LEGACY_ACTION_TYPE_MAP.values()),
      str(sorted(set(processor.LEGACY_ACTION_TYPE_MAP.values()))))
check("水印位置迁移表条目数已冻结（九宫格）",
      len(processor.LEGACY_WATERMARK_POSITION_MAP) == 9,
      "len=%d" % len(processor.LEGACY_WATERMARK_POSITION_MAP))
check("视图模式迁移表条目数已冻结（5 种）",
      len(LEGACY_VIEW_MODE_MAP) == 5, "len=%d" % len(LEGACY_VIEW_MODE_MAP))
check("冲突策略迁移表条目数已冻结（4 种）",
      len(LEGACY_ON_EXIST_MAP) == 4, "len=%d" % len(LEGACY_ON_EXIST_MAP))

# 行为证明：把显示名改掉，旧中文串仍必须能迁移。
_saved_label = processor.ACTION_LABELS["resize"]
_saved_wm_label = processor.WATERMARK_POSITION_LABELS["bottom_right"]
try:
    processor.ACTION_LABELS["resize"] = "缩放"
    processor.WATERMARK_POSITION_LABELS["bottom_right"] = "右下角"
    _renamed = processor.migrate_legacy_actions([
        {"type": "调整大小", "params": {"width": 800}},
        {"type": "水印", "params": {"position": "右下"}},
    ])
finally:
    processor.ACTION_LABELS["resize"] = _saved_label
    processor.WATERMARK_POSITION_LABELS["bottom_right"] = _saved_wm_label
check("显示名改文案后，旧中文动作仍能迁移",
      _renamed[0]["type"] == "resize", str(_renamed[0]["type"]))
check("显示名改文案后，旧中文水印位置仍能迁移",
      _renamed[1]["params"]["position"] == "bottom_right",
      str(_renamed[1]["params"]))

# ----------------------------------------------------------------------
# 5. processor 侧：英文 ID 是唯一可执行形式
# ----------------------------------------------------------------------
print("\n=== 处理链 ===")
check("DEFAULT_PARAMS 以英文 ID 为键",
      "resize" in processor.DEFAULT_PARAMS
      and "调整大小" not in processor.DEFAULT_PARAMS)
check("ACTION_IDS 是英文",
      processor.ACTION_IDS[0] == "resize", str(processor.ACTION_IDS[0]))
check("WATERMARK_POSITION_IDS 是英文",
      processor.WATERMARK_POSITION_IDS[-1] == "bottom_right",
      str(processor.WATERMARK_POSITION_IDS[-1]))
check("ACTION_TYPES 仍是中文显示名（i18n key）",
      processor.ACTION_TYPES[0] == "调整大小")

# ----------------------------------------------------------------------
# 6. 显示名名单与真实常量对账（防止改漏/改错后测试仍绿）
# ----------------------------------------------------------------------
print("\n=== 名单对账 ===")
import classify_i18n as cl  # noqa: E402

_real_labels = (
    set(processor.ACTION_LABELS.values())
    | set(processor.WATERMARK_POSITION_LABELS.values())
    | set(VIEW_MODE_LABELS.values())
    | set(ON_EXIST_LABELS.values())
)
check("阶段 6 名单（显示名）与真实常量完全一致",
      cl.STAGE6_DISPLAY_IDS == _real_labels,
      "多=%s 少=%s" % (sorted(cl.STAGE6_DISPLAY_IDS - _real_labels),
                       sorted(_real_labels - cl.STAGE6_DISPLAY_IDS)))

_missing = sorted(t for t in cl.STAGE6_DISPLAY_IDS
                  if not i18n.has_translation(t))
check("显示名在字典里都有译文", not _missing, str(_missing))

# 反向：字典里不该出现 bench 诊断输出（它只打印到控制台，用户看不到）
check("调试输出没有混入字典",
      not i18n.has_translation("全延迟(旧V1)"))

# ----------------------------------------------------------------------
# 7. 切回中文：一切复原（防止 i18n 状态泄漏）
# ----------------------------------------------------------------------
print("\n=== 切回中文 ===")
i18n.set_language("zh_CN")
clear_settings()
w4 = fresh_window()
_zh_first = [a for a in w4.add_action_menu.actions()
             if not a.isSeparator()][0]
check("中文界面动作类型显示中文",
      _zh_first.text() == "调整大小", "got=%r" % _zh_first.text())
check("中文界面动作 userData 仍是英文 ID",
      _zh_first.data() == "resize", "got=%r" % _zh_first.data())
check("中文界面冲突策略显示中文",
      w4.on_exist_combo.itemText(0) == "替换",
      "got=%r" % w4.on_exist_combo.itemText(0))
check("中文界面查看按钮显示「缩略图」",
      w4.view_button.text() == "缩略图", "got=%r" % w4.view_button.text())
check("中文界面摘要不翻译",
      MainWindow._action_summary(
          None, {"type": "rotate", "params": {"angle": 90}}).startswith("旋转"))

print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
if failures:
    for f in failures:
        print("  - %s" % f)
    sys.exit(1)
print("ALL_OK")
