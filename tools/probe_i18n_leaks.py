# -*- coding: utf-8 -*-
"""英文界面中文泄漏扫描：en_US 模式下遍历控件树，报告所有仍含汉字的文本。

与 probe_i18n_en_ui.py（只截图不断言）互补——这里做程序化断言，给「阶段 3
回填到底翻干净没有」一个确定答案。

初版有两个盲区，扫不干净会给出「已无泄漏」的假阳性结论：

1. **非默认视图的控件**。窗口默认停在「缩略图」视图，详细信息视图的表头、
   列表视图的行都不会进入扫描范围 → 这里遍历 VIEW_MODES 全部切一遍再扫。
2. **动态触发的文案**。切换语言才出现的状态栏消息、右键表头才动态构建的
   QMenu，不触发交互就根本不存在 → 这里主动触发后再扫。

用法::

    python tools/probe_i18n_leaks.py

判定：
- 含汉字的文本 → 候选泄漏
- 命中允许名单（语言自称名、COMMAND_TEMPLATE 占位符等刻意保留中文的）→ 排除
- 其余 → 真泄漏，列出来人工修
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import (
    QApplication, QLabel, QAbstractButton, QGroupBox,
    QTabWidget, QComboBox, QLineEdit, QTableWidget,
    QMenu, QMenuBar, QStatusBar, QTextEdit, QPlainTextEdit,
)
from PySide6.QtGui import QFontDatabase, QFont

QSettings.setDefaultFormat(QSettings.IniFormat)
_tmp = tempfile.mkdtemp(prefix="libjxl_probe_leak_")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp)
QCoreApplication.setOrganizationName("libjxl")
QCoreApplication.setApplicationName("libjxl-gui")

from libjxl_gui import i18n
from libjxl_gui.__main__ import _apply_persisted_language
from libjxl_gui.main_window import MainWindow, VIEW_MODES

# ---- 字体（offscreen 默认 0 字体，载入系统字体只为控件能正常建文本） ----
app = QApplication.instance() or QApplication(["-platform", "offscreen"])
for fp in (r"C:\Windows\Fonts\msyh.ttc",
           r"C:\Windows\Fonts\segoeui.ttf",
           r"C:\Windows\Fonts\arial.ttf"):
    if os.path.isfile(fp):
        QFontDatabase.addApplicationFont(fp)
app.setFont(QFont("Microsoft YaHei UI", 10))

# ---- 模拟用户在设置页选 English 并重启 ----
s = QSettings()
s.beginGroup("appearance")
s.setValue("language", "en_US")
s.endGroup()
s.sync()
code = _apply_persisted_language()
print("applied language =", code)

w = MainWindow()
w._env_refreshed = True
w.resize(1180, 820)
w.show()
for _ in range(40):
    app.processEvents()

# ---- 允许名单 ----
# 语言下拉里显示的是「自称名」（endonym），刻意保留中文，不算泄漏
ALLOW_EXACT = {"简体中文", "English"}
# COMMAND_TEMPLATE 占位符必须保持字面（用户要照着复制到命令行），不算泄漏
ALLOW_SUBSTR = ("<输入>", "<输出>")


def has_cjk(t):
    return any("\u4e00" <= ch <= "\u9fff" for ch in t)


def strip_allowed(t):
    """剔除刻意保留中文的片段后再判定。

    中英文混排是正常形态——比如「Language switched to 简体中文」里模板是
    英文、自称名是中文。只有剔除允许片段后仍含汉字，才是真泄漏。
    """
    r = t
    for p in list(ALLOW_EXACT) + list(ALLOW_SUBSTR):
        r = r.replace(p, "")
    return r


def is_leak(t):
    return has_cjk(strip_allowed(t))


def pump(n=12):
    for _ in range(n):
        app.processEvents()


def add(out, kind, cls, text):
    if text and has_cjk(text):
        out.append((kind, cls, text))


def collect(widget, out):
    add(out, "windowTitle", widget.__class__.__name__, widget.windowTitle())
    for meth in ("toolTip", "statusTip", "whatsThis"):
        try:
            add(out, meth, widget.__class__.__name__, getattr(widget, meth)())
        except Exception:
            pass
    if isinstance(widget, QLabel):
        add(out, "text", "QLabel", widget.text())
    if isinstance(widget, QAbstractButton):
        add(out, "text", widget.__class__.__name__, widget.text())
    if isinstance(widget, QGroupBox):
        add(out, "title", "QGroupBox", widget.title())
    if isinstance(widget, QTabWidget):
        for i in range(widget.count()):
            add(out, "tabText[%d]" % i, "QTabWidget", widget.tabText(i))
    if isinstance(widget, QComboBox):
        for i in range(widget.count()):
            add(out, "itemText[%d]" % i, "QComboBox", widget.itemText(i))
        add(out, "placeholderText", "QComboBox", widget.placeholderText())
    if isinstance(widget, QLineEdit):
        add(out, "placeholderText", "QLineEdit", widget.placeholderText())
        add(out, "text", "QLineEdit", widget.text())
    if isinstance(widget, (QTextEdit, QPlainTextEdit)):
        add(out, "plainText", widget.__class__.__name__, widget.toPlainText())
    if isinstance(widget, QTableWidget):
        for c in range(widget.columnCount()):
            hh = widget.horizontalHeaderItem(c)
            if hh is not None:
                add(out, "hHeader[%d]" % c, "QTableWidget", hh.text())
    if isinstance(widget, QStatusBar):
        add(out, "currentMessage", "QStatusBar", widget.currentMessage())


def walk_menus(widget, out):
    for m in widget.findChildren(QMenu):
        for act in m.actions():
            add(out, "menuAction", m.objectName() or "QMenu", act.text())
    mb = widget.menuBar()
    if mb:
        for act in mb.actions():
            add(out, "menuBar", "QMenuBar", act.text())


def walk_actions(widget, out):
    """扫所有 QAction，含未挂到菜单上的（工具栏按钮等）。"""
    from PySide6.QtGui import QAction
    for act in widget.findChildren(QAction):
        add(out, "QAction", "QAction", act.text())
        add(out, "QAction.toolTip", "QAction", act.toolTip())


def scan_tree(out, note=""):
    """扫当前整棵控件树（含隐藏控件），note 仅用于出错时定位。"""
    collect(w, out)
    for ww in QApplication.allWidgets():
        try:
            collect(ww, out)
        except Exception:
            pass
    walk_menus(w, out)
    walk_actions(w, out)


def collect_dynamic_menus(w, out):
    """主动构建动态菜单：这类 QMenu 平时不存在，不触发就扫不到。

    ⚠️ 只调用**构建器**（返回 QMenu 的方法），绝不能调用"打开器"。
    靠 ``_menu`` 后缀猜语义很脆弱：``_open_add_action_menu`` 这类方法名字
    也以 _menu 结尾，但它是「弹出菜单」的动作方法，一旦被执行就会进入
    模态事件循环把扫描器挂死（本项目既有 ``_open_folder_menu`` 用的是非
    阻塞的 popup() 才侥幸没事）。故显式排除 ``_open`` 前缀。
    """
    for name in sorted(dir(w.__class__)):
        if not name.endswith("_menu"):
            continue
        if name.startswith("_open"):     # 打开器（会弹菜单），不是构建器
            continue
        fn = getattr(w, name, None)
        if not callable(fn):
            continue
        try:
            m = fn()
        except TypeError:
            continue          # 需要参数，跳过
        except Exception as e:
            print("[warn] %s() 调用失败: %s" % (name, e))
            continue
        if not isinstance(m, QMenu):
            continue
        for act in m.actions():
            add(out, "dynMenu:" + name, "QMenu", act.text())


found = []

# --- 1) 默认状态（缩略图视图） ---
scan_tree(found, "default")

# --- 2) 逐个标签页 ---
tabs = w.findChildren(QTabWidget)
for tw in tabs:
    for i in range(tw.count()):
        try:
            tw.setCurrentIndex(i)
            pump()
            scan_tree(found, "tab[%d]" % i)
        except Exception as e:
            print("[warn] 切标签页 %d 失败: %s" % (i, e))

# --- 3) 逐个输入页视图模式（详细信息视图的表头只在切过去后才重建） ---
for mode in VIEW_MODES:
    try:
        w._on_view_changed(mode)
        pump()
        scan_tree(found, "view=%s" % mode)
    except Exception as e:
        print("[warn] 切视图 %s 失败: %s" % (mode, e))

# --- 4) 动态菜单（表头右键菜单等） ---
collect_dynamic_menus(w, found)

# --- 5) 动态触发的文案：语言切换的状态栏消息 ---
combo = getattr(w, "language_combo", None)
if combo is not None:
    try:
        # 先拨到其它语言再拨回来，确保 currentIndexChanged 真的触发
        other = combo.findData("zh_CN")
        back = combo.findData("en_US")
        if other >= 0:
            combo.setCurrentIndex(other)
            pump()
            scan_tree(found, "lang=zh_CN")
        if back >= 0:
            combo.setCurrentIndex(back)
            pump()
            scan_tree(found, "lang=en_US")
    except Exception as e:
        print("[warn] 触发语言切换失败: %s" % e)

# --- 6) 主题 / 颜色方案切换的状态栏消息 ---
for attr, values in (("theme_combo", None), ("color_scheme_combo", None)):
    cb = getattr(w, attr, None)
    if cb is None:
        continue
    try:
        orig = cb.currentIndex()
        for i in range(cb.count()):
            cb.setCurrentIndex(i)
            pump()
            scan_tree(found, "%s[%d]" % (attr, i))
        cb.setCurrentIndex(orig)
        pump()
    except Exception as e:
        print("[warn] 触发 %s 失败: %s" % (attr, e))

# 去重 + 过滤允许名单
seen = set()
real = []
for kind, cls, txt in found:
    key = (kind, cls, txt)
    if key in seen:
        continue
    seen.add(key)
    if not is_leak(txt):
        continue
    real.append((kind, cls, txt))

print("\n=== 含汉字文本候选数：%d（去重 %d，排除允许名单后 %d） ==="
      % (len(found), len(seen), len(real)))
if not real:
    print("OK 未发现中文泄漏（已遍历全部标签页/视图模式/动态菜单/状态栏消息）")
else:
    for kind, cls, txt in real:
        print("  [%-14s %-24s] %s" % (cls, kind, txt[:90]))

sys.exit(1 if real else 0)
