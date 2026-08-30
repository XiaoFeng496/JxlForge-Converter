# -*- coding: utf-8 -*-
"""下拉弹窗选中行竖线颜色 —— 修复前后对比探针（真机用，勿加 QT_QPA_PLATFORM=offscreen）。

背景
----
Bug：控件样式 = 「原生」（非「原生（无闪烁）」）+ 系统深色模式时，打开任意
NoFlickerComboBox 弹窗，被选中项左侧的竖线渲染为黑色，正常应与系统高亮同色（蓝）。

根因：QComboBox 的 popup 是独立 top-level window，Qt 给它喂 QPalette.Inactive
调色板组；Windows native style 在 Inactive 组下把 QPalette.Accent 解析为黑。

修复（commit 94b56f3，父提交 e48a1f1）：在 NoFlickerComboBox._apply_fusion_style
的 native 分支主动 view.setPalette(QApplication.palette())，把 popup 拉回 Active 组。

这个 bug 的**视觉差异在 offscreen 下看不到**（offscreen 强制 Fusion，且没有
Windows native 的 dark-mode Accent 行为），只能在真机验证。本脚本就是为真机
对比而生：左下拉 = e48a1f1 行为（竖线黑），右下拉 = 94b56f3 行为（竖线蓝）。

用法（系统终端，双击或命令行，不要设 QT_QPA_PLATFORM）
------------------------------------------------------
    E:\\Python\\Python312\\python.exe tools\\probe_popup_palette.py

操作步骤
--------
1. 顶部「控件样式」下拉切到 **原生**（必须！另外两种是 Fusion 弹窗，看不到症状）。
2. 系统设为**深色模式**（浅色模式下 Inactive 的暗化不明显，容易看漏）。
3. 分别点开左/右两个下拉，对比被选中那一行最左侧的竖线颜色。
   预期：左 = 黑色（Bug 复现），右 = 系统高亮蓝（已修复）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget,
)


def _build_app():
    """Build the QApplication in *real* (non-offscreen) mode.

    The whole point of this probe is to exercise the Windows native style, so
    we deliberately do NOT force QT_QPA_PLATFORM=offscreen — if the caller set
    it, drop it for this process so the probe can actually show the bug.

    Escape hatch: set ``LIBJXL_PROBE_OFFSCREEN_OK=1`` to keep offscreen (used
    only by automated smoke checks that must not open a real window).
    """
    if os.environ.get("LIBJXL_PROBE_OFFSCREEN_OK") != "1":
        os.environ.pop("QT_QPA_PLATFORM", None)
    return QApplication(sys.argv)


def main():
    app = _build_app()
    # 必须在创建任何控件前把应用级 QSettings 指向隔离目录，避免探针读写本机 ini。
    from PySide6.QtCore import QCoreApplication, QSettings
    import tempfile
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(
        QSettings.IniFormat, QSettings.UserScope,
        tempfile.mkdtemp(prefix="libjxl_popup_probe_"),
    )
    QCoreApplication.setOrganizationName("libjxl")
    QCoreApplication.setApplicationName("libjxl-gui")

    from libjxl_gui.main_window import (
        NoFlickerComboBox, _THEME_ORDER, _THEME_LABELS,
        set_app_theme, app_theme,
    )
    # 捕获初始（原生）样式名，供切换到「原生」时还原——与 MainWindow.__init__ 里
    # 保存 _native_style_name 的做法一致。必须在任何 setStyle 之前取。
    from PySide6.QtWidgets import QStyleFactory
    native_style_name = QApplication.style().objectName()

    class LegacyNoFlickerComboBox(NoFlickerComboBox):
        """复刻 e48a1f1（修复前）的 _apply_fusion_style：native 分支不 setPalette。

        与当前实现的唯一差异就是那一行 view.setPalette(QApplication.palette())。
        去掉它，popup view 就留在 Qt 默认的 Inactive palette 上，Windows native
        style 把 Accent 画成黑 → 竖线变黑。
        """

        def _apply_fusion_style(self):
            from libjxl_gui.main_window import _fusion_style, dropdowns_use_fusion
            fusion = _fusion_style()
            if fusion is not None and dropdowns_use_fusion():
                self.setStyle(fusion)
            else:
                self.setStyle(QApplication.style())
                # e48a1f1：这里没有 view.setPalette(QApplication.palette())

    class ProbeWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(
                "下拉弹窗竖线对比探针 — 左 e48a1f1（旧） / 右 94b56f3（新）"
            )
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(14, 12, 14, 12)
            root.setSpacing(10)

            # ---- 控件样式选择 ----
            theme_row = QHBoxLayout()
            theme_row.setSpacing(8)
            theme_row.addWidget(QLabel("控件样式："))
            self.theme_combo = NoFlickerComboBox()
            for key in _THEME_ORDER:
                self.theme_combo.addItem(_THEME_LABELS[key], key)
            self.theme_combo.setCurrentIndex(
                self.theme_combo.findData(app_theme())
            )
            self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
            theme_row.addWidget(self.theme_combo)
            theme_row.addStretch(1)
            root.addLayout(theme_row)

            tip = QLabel(
                "① 把控件样式切到「原生」  ② 系统设为深色模式  "
                "③ 分别点开下面两个下拉，对比被选中项左侧竖线颜色"
            )
            tip.setWordWrap(True)
            tip.setStyleSheet("color: #888; font-size: 11px;")
            root.addWidget(tip)

            # ---- 左右两个对比下拉 ----
            cmp_row = QHBoxLayout()
            cmp_row.setSpacing(16)

            left_col = QVBoxLayout()
            left_col.setSpacing(4)
            left_col.addWidget(QLabel("旧 e48a1f1（竖线应为黑）"))
            self.legacy_combo = LegacyNoFlickerComboBox()
            self.legacy_combo.addItems(["原生（无闪烁）", "原生", "Fusion"])
            self.legacy_combo.setCurrentIndex(1)
            left_col.addWidget(self.legacy_combo)
            cmp_row.addLayout(left_col)

            right_col = QVBoxLayout()
            right_col.setSpacing(4)
            right_col.addWidget(QLabel("新 94b56f3（竖线应为蓝）"))
            self.fixed_combo = NoFlickerComboBox()
            self.fixed_combo.addItems(["原生（无闪烁）", "原生", "Fusion"])
            self.fixed_combo.setCurrentIndex(1)
            right_col.addWidget(self.fixed_combo)
            cmp_row.addLayout(right_col)

            root.addLayout(cmp_row)

            note = QLabel(
                "说明：另外两种控件样式（原生（无闪烁）/ Fusion）的弹窗由 Fusion 自绘，"
                "走它自己的 Active 派生调色板，所以看不到这个症状——这也是为什么必须"
                "切到「原生」才能复现。"
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: #888; font-size: 11px;")
            root.addWidget(note)
            root.addStretch(1)
            self.resize(560, 260)

        def _on_theme_changed(self, _index):
            """Switch the control style and re-apply it to both probe combos.

            Mirrors MainWindow._apply_app_style: Fusion for the 'fusion' theme,
            the captured native style otherwise. A fresh QStyle is created per
            call so re-applying never collides on ownership.
            """
            theme = self.theme_combo.currentData()
            set_app_theme(theme)
            name = "Fusion" if theme == "fusion" else native_style_name
            style = QStyleFactory.create(name)
            if style is not None:
                QApplication.setStyle(style)
            # 让两个对比下拉按新主题重新走一遍 _apply_fusion_style。
            self.legacy_combo._apply_fusion_style()
            self.fixed_combo._apply_fusion_style()
            # 顶部那个下拉自身也要跟着切（它不是被测对象，但外观需一致）。
            self.theme_combo._apply_fusion_style()

    win = ProbeWindow()
    win.show()
    # Escape hatch for automated smoke checks: build everything but skip the
    # blocking event loop, so the probe can be exercised headlessly.
    if os.environ.get("LIBJXL_PROBE_NO_EXEC") == "1":
        _self_check(win)
        return 0
    return app.exec()


def _self_check(win):
    """Headless sanity check: switch to the native control style and report
    whether each probe combo's popup view palette matches the application's
    Active palette.

    NOTE: this can only prove the *code path* ran (setPalette called or not).
    The visible black-vs-blue bar difference depends on the Windows native
    style resolving Inactive.Accent to black, which offscreen never does —
    that part still requires a real machine run.
    """
    from PySide6.QtGui import QPalette
    idx = win.theme_combo.findData("native")
    win.theme_combo.setCurrentIndex(idx)
    app_pal = QApplication.palette()
    roles = range(QPalette.ColorRole.WindowText.value,
                  QPalette.ColorRole.PlaceholderText.value + 1)

    def _matches_app(combo):
        v = combo.view().palette()
        return all(
            v.color(QPalette.ColorGroup.Active, QPalette.ColorRole(r))
            == app_pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole(r))
            for r in roles
        )

    print("控件样式 = 原生（native）时的 popup view palette 自检：")
    print("  左 legacy(e48a1f1) 与 app.palette(Active) 全角色一致 = "
          f"{_matches_app(win.legacy_combo)}")
    print("  右 fixed (94b56f3) 与 app.palette(Active) 全角色一致 = "
          f"{_matches_app(win.fixed_combo)}  <- 期望 True（修复已生效）")
    print("注意：offscreen 上两者可能都显示 True，因为 offscreen 下 view 的默认"
          "palette 本就等于 app.palette。此自检只证明代码分支被走到，"
          "**视觉上的黑/蓝竖线差异必须在真机（不设 QT_QPA_PLATFORM）验证**。")


if __name__ == "__main__":
    sys.exit(main())
