# -*- coding: utf-8 -*-
"""诊断输出页在 en_US 下的「向右挤压 / 右侧截断」。

注意：offscreen 下窗口宽度受平台屏幕尺寸限制（resize(1180) 实际可能只有
~800），所以「可视宽」不可信。可靠的对比指标是 **内容固有宽度需求**
inner.sizeHint().width() / minimumSizeHint().width()——它与窗口宽度无关。

输出页的 QScrollArea 曾设为 ScrollBarAlwaysOff，内容一旦超出可视宽度就只能
被截断（已改为 ScrollBarAsNeeded 兜底）。本脚本按「顶层行」分解宽度贡献，
定位真正的瓶颈行，并验证窄窗口下出现的是滚动条而非截断。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtWidgets import QApplication, QTabWidget, QScrollArea
from PySide6.QtGui import QFontDatabase, QFont

from libjxl_gui import i18n
from libjxl_gui.main_window import MainWindow


def make(lang):
    tmp = tempfile.mkdtemp(prefix="libjxl_probe_out_")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")
    s = QSettings()
    s.beginGroup("appearance")
    s.setValue("language", lang)
    s.endGroup()
    s.sync()
    i18n.set_language(lang)
    w = MainWindow()
    w._env_refreshed = True
    # offscreen 屏幕尺寸会把 resize() 后的窗口裁到屏幕大小（880 可能变成
    # ~800），这里用 setFixedSize 强制到默认窗口尺寸，测真实可视宽。
    w.setFixedSize(880, 640)
    w.show()
    for _ in range(40):
        app.processEvents()
    return w


app = QApplication.instance() or QApplication(["-platform", "offscreen"])
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\segoeui.ttf",
           r"C:\Windows\Fonts\arial.ttf"):
    if os.path.isfile(fp):
        QFontDatabase.addApplicationFont(fp)
app.setFont(QFont("Microsoft YaHei UI", 10))


def measure(lang):
    w = make(lang)
    tw = w.findChild(QTabWidget)
    for i in range(tw.count()):
        if tw.tabText(i) in ("Output", "输出"):
            tw.setCurrentIndex(i)
            break
    for _ in range(30):
        app.processEvents()

    scroll = w.output_scroll
    inner = scroll.widget()
    lay = inner.layout()

    hsb = scroll.horizontalScrollBar()
    vp_w = scroll.viewport().width()
    need = inner.sizeHint().width()
    # 横向滚动条是否真的需要出现 = 是否超宽（修复前 AlwaysOff 时就是被截断）
    overflow = hsb.maximum() > 0
    print("\n" + "=" * 74)
    print("语言 = %s   窗口 = %dx%d   viewport 可视宽 = %d"
          % (lang, w.width(), w.height(), vp_w))
    print("=" * 74)
    print("内容固有宽度需求 sizeHint=%d  minimumSizeHint=%d"
          % (need, inner.minimumSizeHint().width()))
    print("横向滚动条：%s   余量 = %d px"
          % ("【超宽·会被截断】" if overflow else "不需要（未超宽）",
             vp_w - need))

    print("\n按顶层行分解（该行单独所需的宽度）：")
    rows = []
    for i in range(lay.count()):
        item = lay.itemAt(i)
        if item is None:
            continue
        if item.widget() is not None:
            wd = item.widget()
            sh = wd.sizeHint().width()
            rows.append(("widget:" + (wd.title() if hasattr(wd, "title")
                                      else wd.__class__.__name__), sh))
        elif item.layout() is not None:
            lo = item.layout()
            sh = lo.sizeHint().width()
            # 行内逐个控件
            kids = []
            for j in range(lo.count()):
                it = lo.itemAt(j)
                if it is None:
                    continue
                if it.widget() is not None:
                    kw = it.widget()
                    kids.append((kw.title() if hasattr(kw, "title")
                                 else kw.__class__.__name__,
                                 kw.sizeHint().width()))
                elif it.layout() is not None:
                    kids.append(("<layout>", it.layout().sizeHint().width()))
            rows.append(("layout[%d]" % i, sh, kids))
        elif item.spacerItem() is not None:
            rows.append(("<stretch>", 0))

    for r in rows:
        if r[0] == "<stretch>":
            continue
        name, sh = r[0], r[1]
        print("  %-42s %5d" % (name, sh))
        if len(r) > 2 and r[2]:
            for kn, kw in r[2]:
                print("        └─ %-36s %5d" % (kn, kw))
    return inner.sizeHint().width(), inner.minimumSizeHint().width()


zh = measure("zh_CN")
en = measure("en_US")

print("\n" + "=" * 74)
print("结论：内容固有宽度需求  中文 %d  →  英文 %d   （+%d px, +%.0f%%）"
      % (zh[0], en[0], en[0] - zh[0],
         100.0 * (en[0] - zh[0]) / max(zh[0], 1)))
print("      minimumSizeHint   中文 %d  →  英文 %d   （+%d px）"
      % (zh[1], en[1], en[1] - zh[1]))
print("=" * 74)

# --- 兜底验证：窗口调窄时，出现的是横向滚动条（可滚动）而非截断 ---
w = make("en_US")
# ⚠️ 必须先切到输出页：未激活的标签页布局是陈旧的，量出来的 viewport 宽度
# 恒定不变（曾因此得到「可视宽 626」的假数据）。
_tw = w.findChild(QTabWidget)
for i in range(_tw.count()):
    if _tw.tabText(i) in ("Output", "输出"):
        _tw.setCurrentIndex(i)
        break
for _ in range(30):
    app.processEvents()
print("\n--- 不同窗口宽度下的兜底检查（en_US）---")
for width in (880, 820, 760, 700, 640, 560):
    w.setFixedSize(width, 640)
    for _ in range(25):
        app.processEvents()
    sc = w.output_scroll
    hsb = sc.horizontalScrollBar()
    vp = sc.viewport().width()
    need = sc.widget().sizeHint().width()
    print("  窗口 %4d → 可视宽 %4d / 内容需求 %4d → %s"
          % (width, vp, need,
             "横向滚动条出现（可滚动查看，不截断）" if hsb.maximum() > 0
             else "未超宽"))
