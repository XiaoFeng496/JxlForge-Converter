# -*- coding: utf-8 -*-
"""「关于 / 系统信息」标签页的回归测试。

锁住三件事：

1. tab 确实注册了、且排在最后（无配置、不持久化，不该插在中间）；
2. 页面两栏内容不为空、分组框齐全（版本快照是排障关键信息，不能静默缺失）；
3. 词典里有对应译文——切语言按项目约定需重启，这里直接验证 ``t()`` 的
   取词结果，避免英文界面上残留「关于」这类中文。

用 offscreen 平台跑（见 ``tools/run_tests.py``）。
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

# ⚠️ QSettings 必须先重定向到临时目录：MainWindow 会读写真实 ini，不隔离
# 会清掉受保护的校准值 big_image_floor_px=2000000（见项目 MEMORY.md）。
_tmp_settings_dir = tempfile.mkdtemp(prefix="jxlforge_about_tab_")
from PySide6.QtCore import QSettings  # noqa: E402

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel  # noqa: E402
from jxlforge import converter, i18n, main_window as mw  # noqa: E402

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


app = QApplication([])
window = mw.MainWindow()
tabs = window.tabs

# --- 1. tab 注册 --------------------------------------------------------
check("about tab widget built", window.about_tab is not None)
check("about tab is the last one",
      tabs.tabText(tabs.count() - 1) == i18n.t("关于"))
check("about tab index matches widget",
      tabs.indexOf(window.about_tab) == tabs.count() - 1)

# --- 2. 页面内容 --------------------------------------------------------
groups = window.about_tab.findChildren(QGroupBox)
group_titles = [g.title() for g in groups]
check("two group boxes on about tab", len(groups) == 2)
check("系统信息 group present", i18n.t("系统信息") in group_titles)
check("第三方依赖与许可证 group present",
      i18n.t("第三方依赖与许可证") in group_titles)

# 分组框里的标签逐行成对出现（key + value），任何一行为空都说明探测失败。
empty_labels = [lab.text() for lab in groups[0].findChildren(QLabel)
                if not lab.text().strip()]
check("no empty row in 系统信息", not empty_labels)

# --- 3. djxl 版本探测（与 get_cjxl_version 对称）------------------------
# 本机装了 libjxl 时必然返回 banner；没装时返回 None，两种情况都只校验格式，
# 让这套测试在没有引擎的机器上也能跑。
djxl_banner = converter.get_djxl_version()
check("djxl banner normalized",
      djxl_banner is None or (djxl_banner.startswith("JPEG XL decoder")
                              and " v" in djxl_banner))
cjxl_banner = converter.get_cjxl_version()
check("cjxl banner normalized",
      cjxl_banner is None or (cjxl_banner.startswith("JPEG XL encoder")
                              and " v" in cjxl_banner))

# --- 4. 词典译文（切语言重启生效，这里直接验 t()）----------------------
i18n.set_language("en_US")
check("en: 关于", i18n.t("关于") == "About")
check("en: 系统信息", i18n.t("系统信息") == "System information")
check("en: 第三方依赖与许可证",
      i18n.t("第三方依赖与许可证") == "Third-party dependencies & licenses")
i18n.set_language("zh_TW")
check("tw: 关于", i18n.t("关于") == "關於")
check("tw: 系统信息", i18n.t("系统信息") == "系統資訊")
check("tw: 未检测到", i18n.t("未检测到") == "未檢測到")
i18n.set_language(i18n.DEFAULT_LANGUAGE)

# --- 5. 诊断信息--------------------------------------------------------
lines = window._diagnostic_lines
check("diagnostic lines non-empty", len(lines) >= 8)
# 诊断文本刻意保持英文键名：不同语言界面导出的快照必须可逐行对照。
check("diagnostic keeps stable english keys",
      all(ln.startswith(("JxlForge Converter", "Python", "PySide6",
                         "Pillow", "cjxl", "djxl", "jxlinfo", "OS", "UI"))
          for ln in lines))

window.statusBar().clearMessage()
window._copy_diagnostic_info()
clip = QApplication.clipboard().text()
check("clipboard got diagnostic", clip.count("\n") >= len(lines) - 1)
check("clipboard mentions app version", "JxlForge Converter" in clip)
check("status bar confirms copy", window.statusBar().currentMessage() != "")

# --- 6. 结构同构回归（真 bug：本页容器与其余各页不同构）--
# 底色异常的源头不是颜色值，而是「只有关于页额外套了一层 QScrollArea 并反复
# 开关 autoFillBackground」。输入 / 操作 / 状态页都是裸 QWidget + QVBoxLayout，
# 关于页照用同构写法后，透明链自然透出 QTabWidget 面板色，浅 / 深色都跟随。
# 锁三条，防止滚动容器与 autoFill 补丁再长回来（颜色值随样式变，故不锁颜色）。
from PySide6.QtWidgets import QScrollArea  # noqa: E402

input_tab = None
for _i in range(tabs.count()):
    if tabs.tabText(_i) in ("输入", "Input"):
        input_tab = tabs.widget(_i)
        break
check("about tab has no scroll area",
      window.about_tab.findChild(QScrollArea) is None)
check("about tab keeps a plain layout", window.about_tab.layout() is not None)
check("about tab autofill matches plain tabs",
      input_tab is not None
      and window.about_tab.autoFillBackground() == input_tab.autoFillBackground())

# --- 7. 作者 / 项目主页行 ------------------------------------------------
# 这些行走两列网格：第 0 列标签、第 1 列内容，作者名与网址天然左对齐。
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402

about_labels = [lab for lab in window.about_tab.findChildren(QLabel)
                if lab.text().strip()]
texts = [lab.text() for lab in about_labels]
check("author row present", "Xiaofeng496" in texts)
check("project url shown host only",
      any("github.com/XiaoFeng496/JxlForge-Converter" in t for t in texts))
# 不列 jxlinfo 版本：实测 jxlinfo 没有任何自报版本的入口（--version 被当输入
# 文件名，处理真实 JXL 时的输出首行同样不带 banner），界面上的版本号只能从
# version.h / cjxl banner 借来；且未安装时也会显示版本号，属误导。
check("no jxlinfo row on about tab", "jxlinfo" not in texts)
# 同理，值列不许再出现「借来的」libjxl 版本号（形如 v0.12.0）——cjxl / djxl
# 两行显示的是本体自报的构建指纹，本页其它行不应混进这类推测值。
import re as _re  # noqa: E402

borrowed = [t for t in texts if _re.match(r"^v\d+\.\d+\.\d+$", t)]
check("no borrowed libjxl version on about tab", not borrowed)
# 系统信息区的键标签顺序：Python / Qt / Pillow / cjxl / djxl / 操作系统 /
# 界面语言，djxl 之后不得再插其它键。
sys_labels = [lab.text() for lab in about_labels
              if lab.text() in ("Python", "Qt", "Pillow", "cjxl", "djxl",
                                "操作系统", "界面语言")]
key_index = {lab: i for i, lab in enumerate(sys_labels)}
check("system info key order",
      list(key_index) == ["Python", "Qt", "Pillow", "cjxl", "djxl",
                          "操作系统", "界面语言"])
between = [lab.text() for lab in about_labels[
    key_index["djxl"] + 1:key_index["操作系统"]]]
check("nothing sits between djxl and OS row",
      all("\t" not in lab and lab not in ("操作系统", "界面语言")
          for lab in between))
# 配色：这两行的文字必须跟随主题（默认黑 / 白），此前标签写死 #c8c8c8、
# 链接写死纯白，浅色主题下整行几乎不可见。
# 只查这两行本身（标签以「：」收尾、值为富文本链接），别把头部简介小字
# （#888）与许可证脚注也算进来。
contact_labels = [lab for lab in about_labels
                  if "：" in lab.text() or "href" in lab.text()]
check("contact rows found", len(contact_labels) >= 3)
check("contact labels carry no hardcoded colour",
      len(contact_labels) >= 3
      and all("color:" not in lab.styleSheet() for lab in contact_labels))

# 网址必须是可点击的富文本链接，且允许左键拖选文本。
home_label = None
for lab in about_labels:
    if "href" in lab.text() and "github.com" in lab.text():
        home_label = lab
        break
check("project url is a rich-text link", home_label is not None)
if home_label is not None:
    url = "https://github.com/XiaoFeng496/JxlForge-Converter"
    check("link href carries full url", url in home_label.text())
    check("link text is host only",
          "github.com/XiaoFeng496/JxlForge-Converter" in home_label.text())
    check("label selects text by left drag",
          bool(home_label.textInteractionFlags() & Qt.TextSelectableByMouse))
    check("label keeps clickable links",
          bool(home_label.textInteractionFlags() & Qt.LinksAccessibleByMouse))
    # 右键动作走 i18n，且复制出来的必须是完整 URL（含协议头）。
    acts = home_label.actions()
    check("one context action on url label", len(acts) == 1)
    # 动作文本走 i18n（构造期定，切语言需重启才刷新 —— 项目既有约定），
    # 所以这里验的是「取到的就是当前语言的译文」，再单独验词典里有对得上
    # 的译文，避免英文界面上漏出中文、或反向漏出英语。
    check("copy url action uses current language",
          acts[0].text() == i18n.t("复制网址"))
    # 只锁词典译文：QAction 的文本在构造期就定了（切语言按项目约定要重启
    # 才刷新），拿同一个实例验证「切语言后文本跟着变」没有意义。
    i18n.set_language("en_US")
    check("en dictionary has copy-url entry", i18n.t("复制网址") == "Copy URL")
    i18n.set_language("zh_TW")
    check("tw dictionary has copy-url entry",
          i18n.t("复制网址") == "複製網址")
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
    window.statusBar().clearMessage()
    acts[0].trigger()
    check("copy url action puts full url on clipboard",
          QApplication.clipboard().text() == url)
    check("status bar confirms url copy",
          window.statusBar().currentMessage() != "")

print("ABOUT_TAB_OK" if not failures else "FAILURES: %s" % failures)
print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
if failures:
    sys.exit(1)
