# -*- coding: utf-8 -*-
"""回归测试：i18n 阶段 6 —— 内部 ID 与显示名分离。

阶段 6 的契约只有一条，但极易被后续改动破坏：

    **界面显示可以翻译，内部 ID 永远是中文。**

具体体现在四处：
  * 下拉框（动作类型 / 水印位置 / 冲突策略）：``itemText`` 是译文，
    ``itemData`` 必须是原始中文 ID；
  * 查看模式菜单：菜单项文字是译文，``triggered`` 回调传的必须是中文 ID；
  * 持久化：写进 ini 的必须是中文 ID（否则英文界面下重启后策略/模式丢失）；
  * ``_action_summary``：摘要里的类型名要翻译，但 ``action["type"]`` 不变。

为什么必须锁：这些中文 ID 在 processor.apply_actions 等处被字面量比较
40 多次（``if atype == "调整大小"``），而且已经写进了用户的 ini
（``input_view/mode=缩略图``、``jxl_output/on_exist=重命名``）。
一旦 ID 被译文顶替，功能会静默失效——不报错，只是什么都不对。

测试策略：切到 en_US 构造窗口，断言「显示是英文 / 取值是中文」；
再切回 zh_CN 断言一切复原（防止 i18n 状态泄漏到同进程的其他测试）。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)      # 导入 libjxl_gui
sys.path.insert(0, HERE)           # 导入 tools/ 下的分级脚本

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QSettings

QSettings.setDefaultFormat(QSettings.IniFormat)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui-test-i18n")
# 隔离 QSettings：测试全程写入临时目录，避免污染真实 ini。
_tmp_settings_dir = tempfile.mkdtemp(prefix="libjxl_test_i18n_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from libjxl_gui import i18n
from libjxl_gui import processor
from libjxl_gui.main_window import MainWindow, VIEW_MODES

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
# 1. 英文界面下构造窗口（阶段 6 的核心场景）
# ----------------------------------------------------------------------
print("\n=== 英文界面：显示译文，取值中文 ===")
w = fresh_window()

# --- 添加动作菜单（原「动作类型」下拉框已移除，改为「添加动作▶」弹出菜单） ---
# 菜单项与旧下拉同构：text = 译文，data = 原始中文 ID。
_acts = list(w.add_action_menu.actions())
check("添加动作菜单项数正确",
      len(_acts) == len(processor.ACTION_TYPES))
check("动作类型显示为英文",
      _acts[0].text() == "Resize",
      "got=%r" % _acts[0].text())
check("动作类型 userData 是中文 ID",
      _acts[0].data() == "调整大小",
      "got=%r" % _acts[0].data())
_ids = [a.data() for a in _acts]
check("全部动作类型 userData 与 ACTION_TYPES 一致",
      _ids == list(processor.ACTION_TYPES), str(_ids))

# --- 冲突策略下拉 ---
EXPECT_ON_EXIST = {"替换": "Overwrite", "询问": "Ask",
                   "跳过": "Skip", "重命名": "Rename"}
_got = {w.on_exist_combo.itemData(i): w.on_exist_combo.itemText(i)
        for i in range(w.on_exist_combo.count())}
check("冲突策略 显示/ID 全部配对正确",
      _got == EXPECT_ON_EXIST, str(_got))
check("冲突策略默认取到中文 ID「替换」",
      w.on_exist_combo.currentData() == "替换",
      "got=%r" % w.on_exist_combo.currentData())

# --- 查看模式菜单 ---
_menu_texts = [a.text() for a in w.view_menu.actions()]
check("查看模式菜单显示为英文",
      "Thumbnails" in _menu_texts and "Details" in _menu_texts,
      str(_menu_texts))
check("查看模式菜单不含中文 ID",
      not any(t in _menu_texts for t in ("缩略图", "详细信息")),
      str(_menu_texts))
check("查看按钮显示译文", w.view_button.text() == "Thumbnails",
      "got=%r" % w.view_button.text())

# --- _action_summary ---
_sum = MainWindow._action_summary(None, {"type": "调整大小",
                                         "params": {"width": 1920,
                                                    "height": 1080}})
check("摘要里的类型名已翻译", _sum.startswith("Resize"), "got=%r" % _sum)
_sum2 = MainWindow._action_summary(None, {"type": "旋转",
                                          "params": {"angle": 90}})
check("摘要（旋转）类型名已翻译", _sum2.startswith("Rotate"), "got=%r" % _sum2)

# ----------------------------------------------------------------------
# 2. 英文界面下添加动作：存进 action dict 的必须是中文 ID
# ----------------------------------------------------------------------
print("\n=== 英文界面下添加动作 ===")
_before = len(w._all_action_data())
# ⚠️ 用 QAction.trigger() 而非 add_action_button.click()：后者会 exec() 一个
# 模态菜单而阻塞。trigger() 同步走完「点菜单项 → 添加动作」的完整链路。
w.add_action_menu.actions()[0].trigger()      # 显示 "Resize"
QApplication.instance().processEvents()
_added = w._all_action_data()
check("添加后动作数 +1", len(_added) == _before + 1,
      "%d -> %d" % (_before, len(_added)))
check("刚添加的动作 type 是中文 ID",
      _added and _added[-1].get("type") == "调整大小",
      "got=%r" % (_added[-1] if _added else None))
check("刚添加的动作带默认参数",
      _added and "width" in _added[-1].get("params", {}),
      str(_added[-1] if _added else None))
# 摘要标签显示的是译文，但底层 ID 没变 —— 这正是阶段 6 要达到的效果
_item_widget = w.action_list.itemWidget(w.action_list.item(w.action_list.count() - 1))
check("列表项摘要显示译文而非中文 ID",
      _item_widget is not None
      and _item_widget.summary_label.text().startswith("Resize"),
      "got=%r" % (_item_widget.summary_label.text()
                  if _item_widget else None))

# ----------------------------------------------------------------------
# 3. 英文界面下持久化：写进 ini 的必须是中文 ID
# ----------------------------------------------------------------------
print("\n=== 英文界面下持久化 ===")
w.on_exist_combo.setCurrentIndex(
    [w.on_exist_combo.itemData(i) for i in range(w.on_exist_combo.count())]
    .index("重命名"))
w._save_jxl_output()
_s = QSettings()
check("ini 里的 on_exist 是中文 ID",
      _s.value("jxl_output/on_exist") == "重命名",
      "got=%r" % _s.value("jxl_output/on_exist"))

# 新建窗口（仍是英文）恢复策略：必须还能选中「重命名」
w2 = fresh_window()
check("英文重启后策略仍为「重命名」",
      w2.on_exist_combo.currentData() == "重命名",
      "got=%r" % w2.on_exist_combo.currentData())

# ----------------------------------------------------------------------
# 4. processor 侧：中文 ID 依然可用（英文不能污染处理链）
# ----------------------------------------------------------------------
print("\n=== 处理链不受影响 ===")
check("DEFAULT_PARAMS 仍以中文 ID 为键",
      "调整大小" in processor.DEFAULT_PARAMS)
check("ACTION_TYPES 仍是中文",
      processor.ACTION_TYPES[0] == "调整大小")
check("WATERMARK_POSITIONS 仍是中文",
      processor.WATERMARK_POSITIONS[-1] == "右下")

# ----------------------------------------------------------------------
# 6. 阶段 6 名单与真实常量对账（防止改漏/改错后测试仍绿）
# ----------------------------------------------------------------------
print("\n=== 名单对账 ===")
i18n.set_language("en_US")
import classify_i18n as cl  # noqa: E402

_real_ids = (set(processor.ACTION_TYPES)
             | set(processor.WATERMARK_POSITIONS)
             | set(VIEW_MODES)
             | {"替换", "询问", "跳过", "重命名"})
check("名单与真实常量完全一致",
      cl.STAGE6_DISPLAY_IDS == _real_ids,
      "多=%s 少=%s" % (sorted(cl.STAGE6_DISPLAY_IDS - _real_ids),
                       sorted(_real_ids - cl.STAGE6_DISPLAY_IDS)))

# 阶段 2 最容易踩的坑：看到 verdict=不翻 就跳过，导致这 27 条没有译文，
# 英文界面上动作类型/水印位置/查看模式/冲突策略全退回中文。
_missing = sorted(t for t in cl.STAGE6_DISPLAY_IDS
                  if not i18n.has_translation(t))
check("阶段 6 标识在字典里都有译文", not _missing, str(_missing))

# 反向：字典里不该出现 bench 诊断输出（它只打印到控制台，用户看不到）
check("调试输出没有混入字典",
      not i18n.has_translation("全延迟(旧V1)"))

# ----------------------------------------------------------------------
# 5. 切回中文：一切复原（防止 i18n 状态泄漏）
# ----------------------------------------------------------------------
print("\n=== 切回中文 ===")
i18n.set_language("zh_CN")
w3 = fresh_window()
check("中文界面动作类型显示中文",
      w3.add_action_menu.actions()[0].text() == "调整大小",
      "got=%r" % w3.add_action_menu.actions()[0].text())
check("中文界面冲突策略显示中文",
      w3.on_exist_combo.itemText(0) == "替换",
      "got=%r" % w3.on_exist_combo.itemText(0))
check("中文界面查看按钮显示「缩略图」",
      w3.view_button.text() == "缩略图",
      "got=%r" % w3.view_button.text())
check("中文界面摘要不翻译",
      MainWindow._action_summary(
          None, {"type": "旋转", "params": {"angle": 90}}).startswith("旋转"))
check("中文重启后策略仍为「重命名」",
      w3.on_exist_combo.currentData() == "重命名",
      "got=%r" % w3.on_exist_combo.currentData())

print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
if failures:
    for f in failures:
        print("  - %s" % f)
    sys.exit(1)
print("ALL_OK")
