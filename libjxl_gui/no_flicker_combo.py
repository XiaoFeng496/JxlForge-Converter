# -*- coding: utf-8 -*-
"""CustomComboBox 原型：QToolButton + QWidget(Popup) 自绘下拉 + QListView。

## 为什么做这个

原生 ``QComboBox`` 的 popup 由 ``QComboBoxPrivateContainer`` 承载，它有三桩
甩不掉的包袱：

1. **吞 release**：按「位置 + 约 500ms」判定，吞掉「打开 popup 那一击」的
   mouse release，导致**闪电点第 1 项没反应**（已在
   ``probe_native_combo_first_row.py`` 里用零项目代码复现，属 Qt 原生行为）。
2. **僵尸容器**：每次 ``hidePopup()`` 都被 ``deleteLater``，无法在其上维护
   QSS / palette 状态。
3. **DWM 入场闪烁**：靠 frameless + 实色底 QSS 缓解，但容器每次重建。

本脚本用 **``QToolButton``（显示当前值 + 箭头）+ ``QWidget``（``Qt.Popup``，
常驻实例，frameless）+ 内嵌 ``QListView``** 绕开原生 popup，验证两件事：

⚠️ **为什么不用 QMenu**：``QMenu::popup()``（由 ``QToolButton`` 触发）每次弹出都
销毁重建 HWND，任何挂在 HWND 上的 DWM 属性（圆角/阴影）随之清零 → 表现为「首开
对、次开乱」。``QWidget.show()/hide()`` 复用同一 HWND，是持久 DWM 属性的唯一正解
（见 skill ``qt-combo-popup-flags`` 的 G-10）。

- **观感**：边框、箭头、悬停高亮、宽度、键盘导航能不能做到与原生下拉一致。
- **交互**：闪电点第 1 项是否还会被吞。

## 跑法

双击本脚本，或 ``python tools\\probe_custom_combo_menu.py``。

左侧「原生 QComboBox」是对照组，右侧是本原型，两边内容完全一致。
**请对两侧都做同一组操作，直接对比**：

| 组 | 操作 | 原生侧预期 | 原型侧待验证 |
|---|---|---|---|
| A | 点开下拉 → 鼠标几乎不动 → **闪电点第 1 项** | ❌ 频繁没反应 | 是否仍被吞？ |
| B | 点开下拉 → 闪电点**第 3 项** | ✅ | ✅ |
| C | 点开下拉 → 停 1 秒 → 点第 1 项 | ✅ | ✅ |
| D | 点开下拉 → **按住** 第 1 项 1 秒再松开 | ✅ | ✅ |

底部标签实时打印每次点击的「**按住时长 → 目标第几项 → 是否生效**」，
并标明是哪一侧的控件。

顶部可切「应用样式」（原生 / Fusion）与「深色配色」，用于评估两种渲染路径下
原型的观感。
"""

import os
import sys
import time

from PySide6.QtCore import (QEvent, QObject, QPoint, QPropertyAnimation,
                            QRect, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QPalette, QStandardItem,
                           QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFrame, QGraphicsDropShadowEffect,
                               QGroupBox, QHBoxLayout, QLabel, QListView,
                               QPushButton, QSizePolicy, QSpinBox,
                               QStyle, QStyleFactory, QStyleOptionComboBox,
                               QStylePainter, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget)

#: ⚠️ Windows 11 的圆角是「双轨制」：
#:   - 有 caption/边框的窗口 → DWM 默认给圆角（``DWMWCP_DEFAULT`` 解释为 ROUND）
#:   - **frameless / popup** 窗口 → DWM 默认**不**给圆角（``DWMWCP_DONOTROUND``）
#: 所以光靠「保留 FramelessWindowHint 去黑边」会把圆角一起丢掉；想 frameless 又
#: 要圆角，必须显式调 ``DwmSetWindowAttribute`` 设 ``DWMWCP_ROUND``。
#:
#: 这也是为什么「保留 Frameless + 拿 DWM 圆角」的标准做法需要走 ctypes：
#: PySide6 没暴露 ``DWMWA_WINDOW_CORNER_PREFERENCE``（33）和 ``DWMWCP_ROUND``（2）
#: 这两个常量。下面的辅助函数把它们凑出来，在 Windows 上跑、在别的平台静默跳过。
#:
#: 参考：https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_window_corner_preference
try:
    import ctypes
    from ctypes import wintypes

    _DWMAPI = ctypes.WinDLL("dwmapi", use_last_error=True)
    _DWMAPI.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND,        # hwnd
        wintypes.DWORD,       # attribute (DWMWA_*)
        ctypes.c_void_p,      # pvAttribute
        wintypes.DWORD,       # cbAttribute
    ]
    _DWMAPI.DwmSetWindowAttribute.restype = ctypes.HRESULT
    _DWMAPI.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,        # hwnd
        wintypes.DWORD,       # attribute (DWMWA_*)
        ctypes.c_void_p,      # pvAttribute
        wintypes.DWORD,       # cbAttribute
    ]
    _DWMAPI.DwmGetWindowAttribute.restype = ctypes.HRESULT
    _DWMWA_WINDOW_CORNER_PREFERENCE = 33
    _DWMWCP_ROUND = 2
    # 关掉 DWM 的入场/出场过渡动画（值 3）：让阴影出现瞬间即满，不被系统
    # 过渡干扰。仅对 DWM 自带过渡生效，与我们的 Qt 淡入动画无关。
    _DWMWA_TRANSITIONS_FORCEDISMISS = 3
    # 跟随系统/应用深色模式。⚠️ 别写死 1 —— 见 ``_apply_dwm_immersive_dark``。
    _DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    _HAS_DWM = True
except (OSError, AttributeError):
    # 非 Windows 或 dwmapi 缺失 —— 静默跳过圆角 API，菜单保持 frameless 方角
    _HAS_DWM = False

# ---------------------------------------------------------------------------
# 入场动画：复刻 QMenu 的渐显
#
# Qt 的 ``QMenu`` 在 Windows 上的渐显是调 user32 的 ``AnimateWindow``
# （``AW_BLEND`` 淡入 / ``AW_SLIDE`` 滑出）。本控件弃用 QMenu 后自己实现：
#
# - **淡入（fade）**：``windowOpacity`` 0 → 1 的 ``QPropertyAnimation``。观感与
#   ``AnimateWindow(AW_BLEND)`` 一致；弹窗是分层窗口、DWM 圆角属性照旧保留
#   （QMenu 的 popup 正是「分层 + 不透明 + 圆角」组合），所以既淡入也有圆角。
# - **下滑（slide）**：只改**高度**的几何展开，不碰透明度，圆角/阴影照旧。
#
# ⚠️ **为何不用 ``AnimateWindow(AW_BLEND)``**：它硬性要求窗口带 ``WS_EX_LAYERED``，
#   frameless 弹窗默认不带；靠 ``SetWindowLongPtrW`` 补样式后还需
#   ``SetWindowPos(SWP_FRAMECHANGED)`` 才真正生效，否则 ``AnimateWindow`` **静默
#   失败、落到下滑兜底** —— 正是「淡入看着像下滑、没淡入」的真因（用户 2026-09-06
#   真机复现两次）。``windowOpacity`` 由 Qt 自己管理分层与 alpha，从根上绕开这个坑，
#   且在 offscreen 也能驱动断言（``AnimateWindow`` 依赖真 HWND，offscreen 测不出）。
# ---------------------------------------------------------------------------
# （Win32 ``AnimateWindow`` / 分层窗口相关 ctypes 声明已移除：淡入改为 Qt 的
# ``windowOpacity`` 动画，从根上绕开 ``AW_BLEND`` 需要 ``WS_EX_LAYERED`` 的坑。）

#: 入场动画模式：``off`` 无 / ``fade`` 淡入 / ``slide`` 下滑展开
_ANIM_MODE_OFF = "off"
_ANIM_MODE_FADE = "fade"
_ANIM_MODE_SLIDE = "slide"
_ANIM_MODES = (_ANIM_MODE_OFF, _ANIM_MODE_FADE, _ANIM_MODE_SLIDE)

#: 默认动画时长（毫秒）。Win11 系统菜单动画约 100~150ms，取 120。
_DEFAULT_ANIM_MS = 120


# 入场动画的淡入改用 ``QPropertyAnimation(windowOpacity)``（见
# ``CustomComboBox._start_fade_animation``），不再依赖 Win32 ``AnimateWindow`` ——
# 后者要求 ``WS_EX_LAYERED`` 且 frameless 弹窗默认不带，补样式不生效时会静默失败、
# 落到下滑兜底（见文件顶部说明）。


def _apply_dwm_corner_round(widget):
    """让一个 Win11 frameless 窗口也获得 DWM 原生圆角（无黑边）。

    ⚠️ 必须窗口已创建（``winId()`` 非 0）才调；offscreen 测试中 ``_HAS_DWM``
    为 ``False``，直接返回不报错。即使 ``_HAS_DWM`` 为 ``True``，offscreen 下
    ``winId()`` 返回的也不是有效 OS 句柄（DWM 调过去会 ``OSError: 句柄无效``），
    所以 DWM 调用本身也要兜底异常 —— 测试在 offscreen 跑通，靠的就是这里吞掉。

    为什么 frameless 默认方角：见文件顶端的注释。``DWMWCP_ROUND`` 是告诉 DWM
    「无视 caption 缺失，按 ROUND 给我画圆角」—— 这是 Win11 上拿到「无黑边 +
    圆角」组合的唯一受支持做法。
    """
    if not _HAS_DWM:
        return
    try:
        hwnd = int(widget.winId())
        if hwnd == 0:
            return
        pref = ctypes.c_int(_DWMWCP_ROUND)
        _DWMAPI.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref),
            ctypes.sizeof(pref),
        )
        # 关掉 DWM 入场/出场过渡：避免阴影出现瞬间被系统过渡动画干扰
        # （原生 popup 同样无 DWM 过渡）。仅作用于 DWM 自带过渡。
        dis = ctypes.c_int(1)
        _DWMAPI.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_TRANSITIONS_FORCEDISMISS,
            ctypes.byref(dis),
            ctypes.sizeof(dis),
        )
    except OSError:
        # offscreen 平台 + 其它窗口系统会丢 ``句柄无效``；真机 Win11 上这里
        # 不会触发。静默跳过，圆角可能不生效但不抛错。
        pass


def _dwm_get_immersive_dark(hwnd):
    """读某个已有窗口的 ``DWMWA_USE_IMMERSIVE_DARK_MODE`` 值（0/1），读不到返回 ``None``。

    用来「照抄宿主窗口的明暗状态」，而不是自己猜。宿主窗口（主窗口）的属性
    是 Qt 按当前主题设好的，抄它最可靠。
    """
    if not _HAS_DWM or not hwnd:
        return None
    try:
        buf = ctypes.c_int(0)
        hr = _DWMAPI.DwmGetWindowAttribute(
            hwnd,
            _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(buf),
            ctypes.sizeof(buf),
        )
        return buf.value if hr == 0 else None
    except OSError:
        return None


def _apply_dwm_immersive_dark(widget, reference=None):
    """让弹出窗口的 ``DWMWA_USE_IMMERSIVE_DARK_MODE`` 与**宿主顶层窗口**一致。

    ⚠️ **千万别写死 1**（2026-09-07 真机教训）：原生下拉的阴影浓淡**随明暗主题
    变化**——深色更浓、浅色更淡。写死 1 等于在浅色主题下也按深色画阴影 →
    阴影比原生浓（用户真机反馈「我们对齐的是深色」）。

    正确做法：**读宿主窗口的实际值抄给 popup**。宿主（主窗口）这一属性由 Qt 按
    当前主题设好，抄它既能跟系统主题、也能跟应用内主题切换。

    读不到时（offscreen / 句柄无效）回退：按 ``QApplication.palette()`` 的
    ``Window`` 亮度判断（<128 视为深色）。

    与 ``_apply_dwm_corner_round`` 一样必须窗口已创建才调，且静默兜底。
    """
    if not _HAS_DWM:
        return
    ref = reference if reference is not None else widget
    try:
        hwnd = int(widget.winId())
        if hwnd == 0:
            return
        value = None
        # ① 优先抄宿主顶层窗口（Qt 已按主题设好的值）
        top = ref.window()
        if top is not None and top is not widget:
            top_hwnd = int(top.winId()) if top.windowHandle() is not None else 0
            if top_hwnd and top_hwnd != hwnd:
                value = _dwm_get_immersive_dark(top_hwnd)
        # ② 回退：按应用 palette 的 Window 亮度判断
        if value is None:
            bg = QApplication.palette().color(QPalette.ColorRole.Window)
            value = 1 if bg.lightness() < 128 else 0
        val = ctypes.c_int(int(value))
        _DWMAPI.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(val),
            ctypes.sizeof(val),
        )
    except OSError:
        pass

#: 下拉最多显示几行，超出滚动
_MAX_VISIBLE_ROWS = 10

#: 列表文字左右各留多少像素（原生下拉的文字也不是贴着边的）
_LIST_HMARGIN = 8

#: ⚠️ 真机高度补偿（**逻辑**像素），**按样式名分别记**。
#: offscreen 下原型与原生 sizeHint 完全相等，量不出差异；但真机 150% 缩放下
#: 实测原生 36px、原型 33px —— 换算回逻辑像素是 **24 vs 22，差 2**。
#: 成因是 CT_ComboBox 的尺寸计算在真机（真实 DPI / 真实系统主题）下少算了
#: 2px，offscreen 复现不出来，只能做成可调常量。
#: ⚠️ key 是 ``style().objectName()``（**全小写**，如 ``"fusion"``）——拼错
#: 会**静默失效**：真机照样矮 2px 而 offscreen 测试全绿，查都查不出来。
#: 用底部「高度补偿」SpinBox 在真机上按样式微调；定下来后钉死默认值。
_HEIGHT_TWEAK_PX = {
    "windows11": 2,
    "fusion": 2,
}

#: 阴影模式下菜单四周留出的空白（列表外扩多少像素给阴影画）。
#: ⚠️ 顶部留 0：菜单顶边紧贴按钮底边，与原生下拉一致；顶部本来就被按钮
#: 挡住，画阴影反而让内容比原生低 m 像素。
_SHADOW_MARGIN = 6

#: popup 背景取哪个 palette 角色（**真机 A/B 开关，默认 base 不动观感**）。
#: * ``base``   —— 历史行为。浅色下 = 纯白 → 下拉像一张「独立卡片」浮在界面上。
#: * ``window`` —— 原生 QComboBox 下拉实测用的角色：内部色 = 宿主容器色，
#:   观感上「融入背景」。像素取样（见 ``probe_pixel_sample.py``）：
#:   浅色 原生(243)/自绘(255)、深色 原生(30)/自绘(45) —— 差的正是这个角色。
#:
#: ⚠️ **只在容器背景 = palette(window) 时才看得出差异**：若主窗口把下拉放在
#: 纯白容器里（base == window == 白），两种角色观感完全一样（用户真机反馈
#: 「主窗口里内部色和原生一致」即属此情形）。是否要全局改成 window，取决于
#: 下拉所在容器的配色 —— 故做成开关，两边真机各看一次再定。
#:
#: 切换方式二选一：
#:   * 环境变量 ``COMBO_POPUP_BG_ROLE=window``（跑脚本前设，全局默认）
#:   * 运行期逐实例 ``combo.set_popup_bg_role("window")``
_POPUP_BG_ROLE_BASE = "base"
_POPUP_BG_ROLE_WINDOW = "window"
_POPUP_BG_ROLES = (_POPUP_BG_ROLE_BASE, _POPUP_BG_ROLE_WINDOW)
_DEFAULT_POPUP_BG_ROLE = (
    os.environ.get("COMBO_POPUP_BG_ROLE") or _POPUP_BG_ROLE_BASE
).strip().lower()
if _DEFAULT_POPUP_BG_ROLE not in _POPUP_BG_ROLES:
    _DEFAULT_POPUP_BG_ROLE = _POPUP_BG_ROLE_BASE

#: ⚠️ **圆角旋钮已作废**（仅留档，当前无引用）：圆角现由 DWM 档位给
#: （``DWMWCP_ROUND``，见 ``_apply_dwm_corner_round``），半径由系统定、不可调。
#: 曾试着改用 QSS ``border-radius`` 自画圆角，但 dwm 是**不透明**窗口、圆角外
#: 三角区无法透明 → 圆角不生效 + 露黑角，并连带把阴影也弄没了（2026-09-07 实测）。
#: 结论：**不透明窗口不要用 QSS 圆角**，要么分层（自绘阴影那套），要么交给 DWM。
_ROUND_RADIUS = 8            # 已作废：仅记录曾用值，改动它无任何效果


def _shadow_margins(mode):
    """阴影模式下 popup 四周留白（左, 上, 右, 下）。

    仅 ``effect`` 模式用自绘 ``QGraphicsDropShadowEffect``，需要透明外扩容纳模糊；
    ``dwm`` 不再自绘阴影（走系统 CS_DROPSHADOW，与原生下拉同机制），不会再调到这里。
    """
    return (_SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN)

#: 下拉菜单的画法（真机 A/B 用，探针底部下拉切换）。
#: 差别全在 **FramelessWindowHint** / **NoDropShadowWindowHint** 两个 flag
#: + 是否调 DWM 圆角 API（见 ``_apply_dwm_corner_round``）。
#:
#: =========  ========  ======  =============================================
#: 模式        Frameless NDS     DWM   观感
#:                                  corner
#: =========  ========  ======  =============================================
#: ``off``      ✔        ✔      —    无阴影；QSS 自画 1px 边框（**默认**）。
#: ``effect``   ✔        ✔      —    半透明 + 自绘投影；Win11 下圆角被吃 → 直角。
#: ``dwm``      ✔        ✗      ✔    frameless + 去 NoDropShadow + **DWM 圆角 API**
#:                                     → 圆角 + DWM 阴影 + **无黑边**（推荐 A，默认观感）。
#:                                     ⚠️ 阴影是 DWM 的柔和阴影，比原生 `CS_DROPSHADOW`
#:                                     淡一点——已知且接受（换不透明 QSS 圆角会连圆角一起丢）。
#: ``style``    ✔        ✗      ✔    ⚠️ **现与 ``dwm`` 等价**（2026-09-07 核实）：
#:                                     两者 flag 相同、DWM 圆角 API 都调、QSS 同为
#:                                     ``_POPUP_QSS_BORDERLESS``（历史上 style 走
#:                                     「自画 1px 边框、颜色可控」，后来 dwm 也改用
#:                                     同一份 QSS，两档就此合并）。
#:                                     唯一差别是 ``_style_pad_extra``（**style 专属**
#:                                     的内边距微调，默认 **0** = 与原生一致）。
#:                                     → 所以「推荐 A / B」现在**没有观感区别**，
#:                                     选哪个都一样；定稿时可直接合并成一档。
#: ``legacy``   ✗        ✗      —    旧版「去 Frameless 换系统阴影」 → Win11
#:                                     原生样式下 DWM 补一圈**黑边**；**仅留
#:                                     作 A/B 对比用**，不要当默认。
#: =========  ========  ======  =============================================
#: （NDS = NoDropShadowWindowHint；✔ = 启用、✗ = 不启用）
#:
#: ⚠️ 关键工程取舍：Win11 对 frameless 窗口默认 ``DWMWCP_DONOTROUND``，DWM
#: 不会自动给圆角/阴影。``dwm`` / ``style`` 必须显式调
#: ``DwmSetWindowAttribute(DWMWA_WINDOW_CORNER_PREFERENCE=DWMWCP_ROUND)`` 才有圆角
#: —— 这是 Win11 上唯一受支持的「无黑边 + 圆角」组合，也是 2026-09-06 之前缺的那块。
#:
#: ⚠️ **别再试 QSS ``border-radius`` 替代 DWM 圆角**（2026-09-07 实测踩坑）：
#: 圆角外的三角区要真透明才成立，只能靠 ``WA_TranslucentBackground``（分层）。
#: dwm / style 是不透明窗口，硬上 QSS 圆角 → 圆角不生效 + 露黑角，且因为不再调
#: DWM 圆角、frameless 下系统阴影又极淡 → **圆角和阴影同时丢失**。已改回 DWM 方案。
#:
#: ⚠️ 历史教训（别再绕回去）：``legacy`` = 2026-09-02 之前那版「用户满意」的
#: 实现，它在 Fusion 下又圆又有阴影，但 Win11 原生样式下 DWM 补了系统边框（黑边）
#: 才被换掉。保留它仅用于「无黑边真的更好吗」的 A/B 对比。
_SHADOW_MODE_OFF = "off"
_SHADOW_MODE_EFFECT = "effect"
_SHADOW_MODE_DWM = "dwm"
_SHADOW_MODE_STYLE = "style"
_SHADOW_MODE_LEGACY = "legacy"
_SHADOW_MODES = (_SHADOW_MODE_OFF, _SHADOW_MODE_EFFECT,
                 _SHADOW_MODE_DWM, _SHADOW_MODE_STYLE, _SHADOW_MODE_LEGACY)

#: 探针里每种模式对应的「真机看什么」提示（切模式时显示在下拉下方）
_MODE_HINTS = {
    _SHADOW_MODE_OFF: "看：菜单是否有圆角（Win11 下 DWM 会给），无阴影。",
    _SHADOW_MODE_EFFECT: "看：阴影是否自然、**圆角有没有变成直角**（Win11 下会）。",
    _SHADOW_MODE_DWM: "看：**圆角 + 阴影 + 无黑边**三者是否齐全 —— 推荐 A。",
    _SHADOW_MODE_STYLE: "看：与「系统阴影」**完全等价**（窗口 flag 一模一样），"
                        "只多一个「样式边距」微调旋钮（当前 0 = 与原生一致）。"
                        "两档看着一样是**正常的**，定稿后应合并成一档。",
    _SHADOW_MODE_LEGACY: "看：**Win11 原生样式下那圈黑边是否还在**，用于和 "
                         "``dwm``/``style`` 对比（不要当默认）。",
}

#: 原生 QComboBox 的 popup 行高缓存，key 是样式名（切主题会重新量一次）
_NATIVE_ROW_HEIGHT = {}
#: 原生 popup 容器相对内部 view 多出来的边框厚度缓存
_NATIVE_POPUP_PAD = {}


def native_combo_row_height():
    """量出**原生** QComboBox 下拉的行高，按样式名缓存。

    原生下拉的行高由 QComboBox 私有的 ``QComboBoxDelegate`` 按 ``CT_MenuItem``
    量出，比 ``QListView`` 默认高一截（实测：Windows 11 **26px**、Fusion
    **20px**，而 QListView 默认只有字体高 **12px**）。不跟齐就会显得又挤又扁，
    这正是「下拉菜单和原生差别很大」的主因。

    自己造一个 ``QComboBox`` 问它的 view 就行——``sizeHintForRow()`` 走的正是
    delegate 的 ``sizeHint()``。
    """
    app = QApplication.instance()
    key = app.style().objectName()
    if key not in _NATIVE_ROW_HEIGHT:
        probe = QComboBox()
        probe.addItems(["Hg"])
        _NATIVE_ROW_HEIGHT[key] = probe.view().sizeHintForRow(0)
    return _NATIVE_ROW_HEIGHT[key]


def native_combo_popup_padding():
    """量出**原生** popup 容器比内部 view 四周各厚多少（按样式名缓存）。

    这个厚度是样式自己画的（边框 / 内边距），每个样式都不一样，实测：

    * ``windows11``   容器 82 = view 78 + **上下各 2**
    * ``windowsvista``容器 44 = view 42 + **上下各 1**
    * ``fusion``      容器 62 = view 60 + **上下各 1**

    数据来自 ``tools/probe_menu_geometry.py``。不能写死——新样式（或样式
    换皮）会变，问一次原生最稳。
    """
    app = QApplication.instance()
    key = app.style().objectName()
    if key not in _NATIVE_POPUP_PAD:
        probe = QComboBox()
        probe.addItems(["Hg", "Hg"])
        # ⚠️ 这个测量必须真的 show + showPopup 才量得准（容器高度要等 WM 布局完
        # 才有效），但直接 show 会在用户屏幕上闪一个「和下拉框差不多大的窗口」——
        # 真机反复复现的「启动闪 / 首次点开闪」，元凶正是这个临时 probe（它就是
        # 一个真正的 QComboBox，大小当然和下拉框一样），**不是 popup 自己**。
        # 三轮误修都栽在这里：只要 _sync_popup_size 在构造期被调（addItem 预钉），
        # 测量就在启动时发生 → 启动闪；不预钉则首帧几何错 → 首次闪。
        # 解法：把 probe 挪到屏幕外再 show，测量精度完全不变，但用户看不见。
        probe.move(-10000, -10000)
        probe.show()
        app.processEvents()
        probe.showPopup()
        app.processEvents()
        view = probe.view()
        container = view.window()
        pad = max(0, (container.height() - view.height()) // 2)
        _NATIVE_POPUP_PAD[key] = pad
        probe.hidePopup()
        probe.hide()
    return _NATIVE_POPUP_PAD[key]


class DropDownDelegate(QStyledItemDelegate):
    """把行高顶到原生下拉的高度。

    ⚠️ **重写 ``QListView.sizeHintForRow()`` 对布局无效**——``QListView`` 的
    真实行高取自 item delegate 的 ``sizeHint()``。实测：把 ``sizeHintForRow``
    覆盖成返回 20，``visualRect()`` 仍然是 12，且 ``doItemsLayout()`` 也刷不
    过来。行高只能在这里给。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row_height = 0         # 0 = 沿用基类高度

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if self.row_height > 0:
            size.setHeight(max(size.height(), self.row_height))
        return size

#: 演示用的三行内容（标题，选项）
ROWS = (
    ("控件样式", ["原生", "原生（无闪烁）", "Fusion"]),
    ("输出模式", ["有损", "无损", "JPG 重编码"]),
    ("线程数", ["自动（全部核心）", "1", "2", "4", "8"]),
)

#: 菜单会自己吃掉这些键（在 action 之间移动/触发），必须转发给列表
_NAV_KEYS = frozenset((
    Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
    Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Return, Qt.Key.Key_Enter,
    Qt.Key.Key_Space,
))

#: 原生 ``QComboBox`` 用来**展开**下拉的键（见 QComboBox::keyPressEvent）。
#: 弃用 QMenu 后没有菜单替我们接管这些键，必须在 CustomComboBox 自己接。
_OPEN_KEYS = frozenset((
    Qt.Key.Key_F4, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter,
))


class DropDownList(QListView):
    """内嵌在菜单里的列表，只负责显示与选中。

    选中语义严格对齐原生 ``QComboBox``。Qt 的做法在 ``qcombobox.cpp`` 的
    ``QComboBoxPrivateContainer::eventFilter`` 里：

        MouseMove  →  view->setCurrentIndex(indexAt(pos))   高亮跟随鼠标
        Release    →  选中 view->currentIndex()             按「松开位置」选

    所以「在 A 行按下、拖到 B 行再松开」选中的是 **B 行**。而 ``QListView``
    默认只在「按下行 == 松开行」时发 ``clicked``，按下后拖动再松开**什么都不
    发**——这就是「移动中按下，松开没反应」的根因，必须自己补这一段。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        # ⚠️ 原生 QComboBox 的 view 是 frameWidth=0（测量值），条目铺满整行。
        # QListView 默认带 1px QFrame，会让条目左右各窄 1px、高亮条铺不满。
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 悬停即高亮（原生下拉就是这个行为）
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._pressed_index = None
        #: 由 CustomComboBox 注入，用于补发「按下行 != 松开行」的提交
        self._combo = None
        # 行高只能由 delegate 决定，见 DropDownDelegate 的注释
        self._delegate = DropDownDelegate(self)
        self.setItemDelegate(self._delegate)

    def mousePressEvent(self, event):
        """记下按下在哪一行，供松开时比对。"""
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            self._pressed_index = idx if idx.isValid() else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """高亮跟随鼠标——原生「松开位置即选中项」的前提。

        ``QListView`` 默认只在悬停时更新用于绘制的 hover index，**不会**改
        动 ``currentIndex()``；而原生是靠 MouseMove 改 ``currentIndex`` 的。
        不补这一步，拖动时高亮不跟手，松开也就选不到别的行。
        """
        super().mouseMoveEvent(event)
        idx = self.indexAt(event.position().toPoint())
        if idx.isValid() and idx != self.currentIndex():
            self.setCurrentIndex(idx)

    def mouseReleaseEvent(self, event):
        """按**松开位置**选中，与原生一致。

        基类只在「按下行 == 松开行」时发 ``clicked``；其余情况由这里补一次
        提交。松开在列表外（index 无效）则只关菜单、不改值，同样与原生一致。
        """
        if event.button() != Qt.MouseButton.LeftButton:
            self._pressed_index = None
            super().mouseReleaseEvent(event)
            return

        release_index = self.indexAt(event.position().toPoint())
        same_row = (self._pressed_index is not None
                    and release_index.isValid()
                    and release_index == self._pressed_index)
        # 先让基类收尾（同行的 clicked 在这里发出，并清掉内部按下状态）
        super().mouseReleaseEvent(event)
        self._pressed_index = None
        if not same_row and release_index.isValid() and self._combo is not None:
            self._combo._on_row_activated(release_index)


class _Popup(QWidget):
    """下拉面板：用普通 ``QWidget`` + ``Qt::Popup`` 代替 ``QMenu``。

    ⚠️ **为什么不用 QMenu**：``QMenu`` 的下拉窗口在每次 ``popup()`` 时 HWND
    都会被 Qt 内部重建，并**重置窗口 flag 与 DWM 属性**——这正是之前「只有第一次
    打开圆角/阴影正确，点第二次就全丢」的根因。``QWidget`` 的窗口在 show/hide 之间
    **复用同一 HWND、flag 随成员保留**，不会在每次弹出时被搅乱。

    ``Qt::Popup`` 让面板获得两件事，且都稳定：
    1. 点击面板外自动关闭（和原生下拉一致）；
    2. **原生 DWM 阴影**（``NoDropShadowWindowHint`` 不置位时系统自动画）。

    圆角则由 ``_apply_dwm_attributes()`` 在 ``showEvent`` 里用
    ``DwmSetWindowAttribute(DWMWCP_ROUND)`` 强制——``frameless`` 窗口 DWM 默认不给
    圆角，必须显式要。DWM 属性不随 HWND 存活，所以每次 show 都重设（此刻 HWND 已就绪）。
    """

    def __init__(self, owner, list_widget):
        # ⚠️ 构造时一次性把窗口 flag 设好：Popup（自动关闭 + 原生阴影）+ Frameless
        # （去系统边框 = 去黑边）。这两个 flag 之后不再改，复用 HWND 时自然保留。
        super().__init__(owner,
                         Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._owner = owner
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(list_widget)
        self._list = list_widget
        self._list.setParent(self)   # addWidget 已重设父，这里再保险一次

    def showEvent(self, event):
        super().showEvent(event)
        # 1) 同步尺寸 + 高亮当前项 + 焦点给列表；2) 补 DWM 圆角
        # ⚠️ 入场动画**不在这里播**：AnimateWindow 只能在窗口隐藏时调，而
        # showEvent 里窗口已经可见 → 调了也是白调（「动画完全无效」的根因）。
        # 动画统一由 showMenu 负责。
        self._owner._on_about_to_show()
        self._owner._apply_dwm_attributes()
        self._owner.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._owner.update()


# ── 下拉「当前行小蓝条」的取色（绝不能盲从 Accent）────────────────
# 主项目在 **Fusion 主题**下会故意把 ``QPalette.Accent`` 设成黑色 —— 见
# ``main_window._apply_app_style``：Fusion 用 Accent 画勾选框/单选钮的指示器，
# 主项目要它是黑的。自绘 popup 若照抄整份 ``QApplication.palette()``，就把这颗
# 黑 Accent 一起同步进来，于是下拉里那条 selection indicator 变黑 —— 正是用户
# 反馈的「从 Fusion 切到原生类主题，小蓝条变黑」。所以这里不盲从 Accent：
# 优先取 ``Highlight``（原生高亮蓝，不受那次设黑影响），拿不到再回退 Win11 强调色。
_FALLBACK_ACCENT = "#0078D4"   # Windows 11 默认强调色
_NEAR_BLACK_SUM = 0x5A         # r+g+b 低于此值视为「黑」


def _popup_palette():
    r"""构造供自绘 popup 使用的 palette。

    原则：**不创造任何颜色** —— 只做两件与原生 ``QComboBox`` 对齐的事。

    1. **颜色组对齐**：把 ``Inactive`` 组全部拉成 ``Active`` 组。自绘 popup 是
       独立 top-level 窗口，Qt 会用 Inactive 组绘制它；原生 Windows 样式下若干
       角色的 Inactive 值会解析成灰/黑 —— 这正是「下拉里那条小蓝条变黑」的根因。
       范式照抄主项目 ``MainWindow._sync_inactive_palette``（遍历所有 role，
       Inactive <- Active），那条已经是项目里验证过的写法。
    2. **Accent 近黑兜底**：主项目在 **Fusion 主题**下会故意把
       ``QPalette.Accent`` 设成黑（``_apply_app_style``：Fusion 用 Accent 画
       勾选框/单选钮指示器，主项目要它是黑的）。若 popup 恰好用 Accent 画选中
       行就会变黑。所以**仅当** Active.Accent 真的近黑时才把它换成同族的
       Active.Highlight（Highlight 不受那次设黑影响）；不黑则一个字节都不动。

    ⚠️ **``Highlight`` / ``HighlightedText`` 绝对不要改写**。它们就是原生下拉
    用的高亮色，改写后必然与原生 QComboBox 不一致 —— 用户反馈「小蓝条颜色不
    对，不是默认的那个蓝色」正是踩了这个坑（上一版无条件把 Highlight 也钉成
    自算的 accent，等于覆盖了系统真实高亮色）。
    """
    app_pal = QApplication.palette()
    pal = QPalette(app_pal)
    active = QPalette.ColorGroup.Active
    inactive = QPalette.ColorGroup.Inactive
    # ① 组对齐：Inactive <- Active（与 _sync_inactive_palette 同范式）
    for role_int in range(QPalette.ColorRole.Window.value,
                          QPalette.ColorRole.PlaceholderText.value + 1):
        role = QPalette.ColorRole(role_int)
        pal.setColor(inactive, role, app_pal.color(active, role))
    # ② Accent 近黑兜底（正常只会在 Fusion 主题命中）
    accent = pal.color(active, QPalette.ColorRole.Accent)
    if accent.isValid() and _is_near_black(accent):
        fallback = pal.color(active, QPalette.ColorRole.Highlight)
        if not fallback.isValid() or _is_near_black(fallback):
            fallback = QColor(_FALLBACK_ACCENT)
        pal.setColor(active, QPalette.ColorRole.Accent, fallback)
        pal.setColor(inactive, QPalette.ColorRole.Accent, fallback)
    return pal


def _is_near_black(color):
    """r+g+b 低于阈值即视为「黑」（用于识别被故意设黑的 Accent）。"""
    return not color.isValid() or (color.red() + color.green() + color.blue()) <= _NEAR_BLACK_SUM


class CustomComboBox(QToolButton):
    """用 QMenu + QListView 自绘的下拉框，实现 QComboBox 的 API 子集。

    与原生 popup 的关键差别：

    * **菜单实例常驻**（不会被 ``deleteLater``）→ QSS / palette 想什么时候设
      都行，不存在「僵尸容器上打补丁」这类问题。
    * 不经过 ``QComboBoxPrivateContainer``，因此**可能**绕开它那套吞 release
      的保护——这正是本脚本要实测的（见模块 docstring 的 A 组）。
    """

    currentIndexChanged = Signal(int)
    activated = Signal(int)

    #: 给 ``_Popup`` 设的 objectName，QSS 用它精确命中 popup 窗口本身（而不是
    #: 误伤到里面的列表）。不用 ``QWidget`` 裸选择器 —— 那样会同时命中列表
    #: （QListView 是 QWidget 子类），把边框也画到列表上。
    _POPUP_OBJ = "CustomComboPopup"

    #: ⚠️ **边框画在 popup 窗口自身，不画在列表上** —— 这是「hover 首/末项时选项
    #: 上下抖」的修法。列表一旦带 1px 边框，viewport 就比内容矮 2px → 溢出 →
    #: ``scrollTo(EnsureVisible)`` 把内容 nudges 1~2px。popup 带边框、列表
    #: borderless → 列表 viewport 恰好 = 行高总和，零溢出，不抖。
    #:
    #: off / dwm / style 同一画法（边框都在 popup 上，列表无边框）。
    _POPUP_QSS_FRAMELESS = (
        "#%s { background: palette(base);"
        " border: 1px solid palette(mid); margin: 0px; }"
        "QListView { background: palette(base); border: 0px;"
        " margin: 0px; padding: 0px; }"
    ) % _POPUP_OBJ
    _POPUP_QSS_BORDERLESS = _POPUP_QSS_FRAMELESS   # dwm/style 与 off 同一画法
    #: 带阴影的菜单（effect）：popup 透明，阴影由列表的 QGraphicsDropShadowEffect
    #: 画，边框下放到列表（popup 透明时画在 popup 上的边框没东西可贴）。
    _POPUP_QSS_SHADOW = (
        "#%s { background: transparent; border: 0px; margin: 0px; }"
        "QListView { background: palette(base);"
        " border: 1px solid palette(mid);"
        " margin: 0px; padding: 0px; }"
    ) % _POPUP_OBJ
    #: ⚠️ 已废弃：曾用 QSS ``border-radius`` 给 dwm 画圆角，实测在**不透明**
    #: （非分层）窗口上三角区无法透明 → 圆角不生效、还露黑角，并连带丢阴影。
    #: 圆角改由 DWM API 给（见 ``_apply_dwm_attributes``），dwm 现与 style / off
    #: 共用 ``_POPUP_QSS_BORDERLESS``。保留此注释以警示：**别再走 QSS 圆角**。
    #: 交还样式自画（``legacy`` 模式）：popup 不画背景/边框，让 DWM 自己画
    #: （Win11 原生 = 圆角 + 阴影，但会补一圈系统窗口边框 = 黑边，仅留作对比）。
    _POPUP_QSS_NATIVE = (
        "#%s { margin: 0px; }"
        "QListView { background: palette(base); border: 0px;"
        " margin: 0px; padding: 0px; }"
    ) % _POPUP_OBJ

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index = -1

        # ⚠️ 下拉用**普通 QWidget + Qt::Popup**，不是 QMenu。QMenu 的 HWND 在
        # 每次 ``popup()`` 都会被 Qt 内部重建并重置 flag / DWM 属性，正是
        # 「只有第一次打开圆角/阴影正确、点第二次全丢」的根因。QWidget 的窗口
        # 在 show/hide 之间复用同一 HWND，flag 随成员保留，不会每次弹出被搅乱。
        self._list = DropDownList(self)
        self._list._combo = self
        self._list.setObjectName("CustomComboList")
        self._popup = _Popup(self, self._list)
        self._popup.setObjectName(self._POPUP_OBJ)
        # ⚠️ popup 是顶层窗口，**不继承**主窗的 palette → 它的列表默认白底。
        # 必须把 palette 显式同步到应用（见 sync_popup_palette），且切样式/
        # 配色后还要再调一次。
        self._menu_mode = _SHADOW_MODE_OFF
        #: popup 背景的 palette 角色（``base`` / ``window``），默认取模块级
        #: ``_DEFAULT_POPUP_BG_ROLE``。见 ``set_popup_bg_role`` 文档。
        self._popup_bg_role = _DEFAULT_POPUP_BG_ROLE
        # ⚠️ 兼容字段：``True`` 当且仅当 mode == effect（自绘投影）。内部逻辑
        # 一律看 ``_menu_mode``，这个布尔只留给外部读和老代码用。
        self._menu_shadow = False
        self._shadow_effect = None
        # 事件转发重入守卫（见 eventFilter：防「列表忽略→向上传播→再转发」递归）
        self._in_key_forward = False
        # 样式自画（style）模式面板内边距的现场微调量（**逻辑**像素，可负）。
        # ⚠️ 用户 2026-09-06 真机实测：**0 与原生一致**（之前记的 -2 是旧版
        # 边框画法下的结论，边框改画在 popup 上之后已不成立）。
        self._style_pad_extra = 0
        # 入场动画（复刻 QMenu 的渐显）：模式 + 时长（毫秒）
        self._anim_mode = _ANIM_MODE_FADE
        self._anim_ms = _DEFAULT_ANIM_MS
        #: 最近一次入场动画的真实结果（真机诊断用，见探针底部「动画」标签）
        self._anim_diag = "未播放"
        #: 几何展开动画对象，**必须持有引用**，否则被 GC 后动画停摆
        self._slide_anim = None
        #: 淡入动画对象（windowOpacity），**必须持有引用**，否则被 GC 后动画停摆
        self._fade_anim = None
        #: 下滑动画的目标全高（由 _prepare_slide_start 在 show 前量好填入）
        self._slide_full_h = 0

        # ⚠️ 必须在 _list 建好之后再调：set_menu_shadow() 会去动列表的
        # graphics effect 和 QSS。
        self.set_menu_shadow(False)
        self.sync_popup_palette()
        self._model = QStandardItemModel(self)
        self._list.setModel(self._model)

        # 键盘事件转发给列表（在 popup 上装 eventFilter）。
        self._popup.installEventFilter(self)

        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        # ⚠️ QToolButton 默认 focusPolicy 是 TabFocus，而 QComboBox 是
        # WheelFocus（15）。不设成一样会连出两个问题：① 点击拿不到焦点，
        # 样式画不出焦点框 → 观感上「比原生矮/平一点点」；② 闭合态的键盘
        # 切换（↑↓）在真实点击流程里根本收不到。
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        # ⚠️ 必须开 mouse tracking：否则鼠标在按钮内部（文字区 ↔ 箭头区）
        # 移动时收不到 mouseMoveEvent，箭头的 hover 高亮不会跟手。
        self.setMouseTracking(True)
        # 真正的宽度由 sizeHint() 按 CT_ComboBox 算，这里只给一个很松的下限
        self.setMinimumWidth(60)

        self._list.clicked.connect(self._on_row_activated)
        self._list.activated.connect(self._on_row_activated)

    # -------------------------------------------------------------- 绘制

    def _style_option(self, with_arrow=True):
        """构造 ``QStyleOptionComboBox``——这是「长得像原生下拉」的关键。

        原生 ``QComboBox::initStyleOption()`` 就是这么填的；照抄一份，样式
        就会走**同一条绘制代码**：Windows 原生样式画出真正的原生组合框，
        Fusion 画出真正的 Fusion 组合框。之前用 QToolButton 的默认绘制
        （按钮面板）正是观感不对的根因。
        """
        opt = QStyleOptionComboBox()
        opt.initFrom(self)          # palette / font / enabled / hover / focus
        opt.editable = False
        opt.frame = True
        opt.currentText = self.text()
        # ⚠️ 必须显式填：QStyleOptionComboBox 的 iconSize 默认是 QSize(-1,-1)，
        # 而 QComboBox::initStyleOption() 会填成 PM_SmallIconSize（16x16）。
        # 某些样式（含 Windows 11）的 CT_ComboBox 尺寸计算会用到它。
        opt.iconSize = self.iconSize()
        opt.subControls = QStyle.SubControl.SC_All
        opt.activeSubControls = QStyle.SubControl.SC_None
        # ⚠️ 严格照抄 QComboBox::initStyleOption() 的状态位。原生**不会**因为
        # 拿到焦点就置 State_Selected，也**不会**在展开时置 State_On：
        #   state &= ~(State_Sunken | State_On)
        #   箭头按下 → state |= State_Sunken 且 activeSubControls |= Arrow
        # 多置一个状态位就会让 Windows 11 样式画出另一套边框（表现为「看起来
        # 矮/窄了一点点」）。已用 tools/probe_combo_pixel_diff.py 验证：
        # 照抄之后闭合态与原生**逐像素完全一致**。
        opened = self._popup.isVisible()
        if opened:
            # 菜单展开 = 箭头被按下，与原生展开态一致
            opt.state |= QStyle.StateFlag.State_Sunken
            opt.activeSubControls = QStyle.SubControl.SC_ComboBoxArrow
        elif with_arrow:
            # 只有鼠标真的落在箭头上才点亮箭头，与原生一致
            arrow = self.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, opt,
                QStyle.SubControl.SC_ComboBoxArrow, self)
            if arrow.contains(self.mapFromGlobal(QCursor.pos())):
                opt.activeSubControls = QStyle.SubControl.SC_ComboBoxArrow
        return opt

    def paintEvent(self, event):
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        opt = self._style_option()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)

    def sizeHint(self):
        opt = self._style_option(with_arrow=False)
        fm = self.fontMetrics()
        # 按所有项里最宽的文字算，避免切到长文本时按钮宽度跳动
        text_w = max((fm.horizontalAdvance(self.itemText(row))
                      for row in range(self.count())), default=0)
        # ⚠️ 内容宽度就是纯文字宽，不能再加 padding：多给 4px 会让按钮比原生
        # 每边宽 4px（实测 windows11 135 vs 131、Fusion 118 vs 114）。边距由
        # sizeFromContents(CT_ComboBox) 负责，不要自己再叠一层。
        content = QSize(
            text_w,
            # ⚠️ 原生 QComboBox 用 max(文字高, iconSize 高) 算内容高度；只按
            # 文字算会矮 2px（实测 offscreen 下 20 vs 22），行高对不齐。
            # iconSize 兜底到 PM_SmallIconSize：QComboBox 私有的 iconSize()
            # 在没显式设置时就是取这个度量，QToolButton 的默认值虽然实测也
            # 是 16x16，但真机主题下未必一致。
            max(fm.height(), self.iconSize().height(),
                self.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize,
                                         None, self)))
        size = self.style().sizeFromContents(
            QStyle.ContentsType.CT_ComboBox, opt, content, self)
        # ⚠️ 真机补偿：windows11 样式在真机（真实 DPI / 真实系统主题）下算出
        # 的高度比原生 QComboBox 少 2 个逻辑像素（150% 缩放下实测 33px vs
        # 36px）。offscreen 复现不出来，做成**按样式**可调的常量，由 SpinBox
        # 现场微调。Fusion/vista 实测相等，不在表里就不补。
        tweak = _HEIGHT_TWEAK_PX.get(self.style().objectName(), 0)
        if tweak:
            size.setHeight(size.height() + tweak)
        return size

    def minimumSizeHint(self):
        """与 sizeHint 同宽，避免布局把按钮压窄到文字被截断。"""
        return self.sizeHint()

    # -------------------------------------------------------------- 事件

    def eventFilter(self, obj, event):
        """把方向键 / 回车转发给列表；并作为 DWM 圆角的「二次兜底」。

        主修复在 ``_Popup.showEvent``（每次 show 都重设，HWND 已就绪且不复用
        重建窗口 —— 因为 _Popup 是普通 QWidget，show/hide 之间复用同一 HWND）。
        这里在 ``Show`` 后再补一刀，是针对极端时序的保险：万一 showEvent 那一帧
        HWND 尚未完全就绪，事件层还能把圆角属性补回。两处都只调
        ``_apply_dwm_attributes``（幂等、不重建窗口、offscreen 下被 OSError 兜底）。

        ⚠️ **重入守卫**：``QWidget`` 弹窗下，列表（popup 的子控件）忽略一个
        KeyPress 后，Qt 会**把同一事件向上传播给 popup**；若守卫不到位，popup 的
        过滤器又会把它转发回列表 → 无限递归（实测直接 stack overflow）。用
        ``_in_key_forward`` 拦掉「传播回来」的那一次转发即可（那次交给
        ``super()`` 正常忽略，不再循环）。
        """
        if obj is self._popup:
            et = event.type()
            if et == QEvent.Type.KeyPress and event.key() in _NAV_KEYS:
                if self._in_key_forward:
                    # 这是「列表忽略后向上传播回来」的事件，别再转发 → 交给
                    # 下面的 super() 正常忽略，打破递归。
                    return super().eventFilter(obj, event)
                self._in_key_forward = True
                try:
                    QApplication.sendEvent(self._list, event)
                finally:
                    self._in_key_forward = False
                return True
            if et == QEvent.Type.Show:
                # show 当帧 HWND 可能还没最终就绪，下一轮事件循环再补一次
                QTimer.singleShot(0, self._apply_dwm_attributes)
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        """滚轮切换选项——与原生 QComboBox 一致。

        ``QToolButton`` 压根不处理滚轮，事件会冒泡到父控件（在滚动区域里
        就表现为「滚轮没反应」或「页面跟着滚」）。原生 QComboBox 是自己吃掉
        并切上一项/下一项的，这里照做。

        菜单已展开时不抢事件，交给里面的列表滚动。
        """
        if self._popup.isVisible() or self.count() == 0:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        row = min(self.count() - 1,
                  max(0, self._index + (-1 if delta > 0 else 1)))
        if row != self._index:
            # 与原生一致：滚轮只发 currentIndexChanged，不发 activated
            self.setCurrentIndex(row)
        event.accept()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def mouseMoveEvent(self, event):
        """鼠标在按钮上移动时重绘，让箭头 hover 高亮跟手。"""
        super().mouseMoveEvent(event)
        self.update()

    def mousePressEvent(self, event):
        """左键按下即展开下拉——与原生 ``QComboBox`` 同一时机。

        ⚠️ **为什么必须自己接管**：``QToolButton`` 的 ``InstantPopup`` 只有在
        ``d->menu`` **非空**时才会去调 ``showMenu()``（见 qtoolbutton.cpp 的
        mousePress/mouseRelease）。本控件弃用 ``QMenu`` 改用 ``QWidget`` 弹窗后
        ``menu()`` 恒为 ``None``，基类那条路径根本不会被触发 → **点击按钮完全
        没反应**（本次 bug 的根因）。所以这里自己接左键，绕过 ``d->menu``。

        展开放在 **press** 而不是 release：原生非 editable ``QComboBox`` 的
        ``mousePressEvent`` 就是直接 ``showPopup()``，Qt 对「press + ``Qt::Popup``
        grab」这条路径处理得很成熟，照抄即可。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.showMenu()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """左键只做收尾：展开已在 press 完成，这里不再触发 ``clicked``。

        不交给 ``QAbstractButton`` 是为了避免它发出 ``clicked`` —— 本控件的
        「提交」语义是「选中某一项」（``activated``），按钮自身被点不算一次
        提交，与原生一致。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """闭合状态下也支持上下键切换，与原生一致。

        另外补齐原生 ``QComboBox`` 的**展开键**：Space / Enter / F4 / Alt+↓
        （``QComboBox::keyPressEvent`` 里就有这几个）。弃用 ``QMenu`` 之后不会
        再有菜单自动接管这些键，必须自己接，否则键盘打不开下拉。
        """
        key = event.key()
        if key in _OPEN_KEYS:
            self.showMenu()
            event.accept()
            return
        if (key == Qt.Key.Key_Down
                and event.modifiers() & Qt.KeyboardModifier.AltModifier):
            self.showMenu()
            event.accept()
            return
        if not self._popup.isVisible() and key in (
                Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if event.key() == Qt.Key.Key_Up else 1
            row = min(self.count() - 1, max(0, self._index + step))
            if row != self._index:
                self.setCurrentIndex(row)
            event.accept()
            return
        super().keyPressEvent(event)

    # -------------------------------------------------------------- 内部

    def sync_popup_palette(self):
        """把菜单与列表的 palette 同步到应用当前 palette。

        ⚠️ ``QMenu`` 是**顶层 popup**，不从主窗继承 palette：它的 QListView
        背景恒为白色，QSS 里的 ``palette(base)`` 也是从控件自己的 palette
        取色，跟着一起变白 → Fusion + 深色下拉就是一块白底。

        ⚠️ 只同步 menu 不够，列表要单独再来一份：``QPalette::Highlight``
        等角色在列表上生效，menu 的 palette 不会自动流下去。

        ⚠️ 初始化时同步一次**不够**——``QApplication.setStyle()`` /
        ``setPalette()`` 之后必须再调一次，否则用户切一次样式/配色就白回来。

        ⚠️ **``viewport()`` 必须单独再同步一份**（这是「下拉里当前行左侧小蓝条
        变黑」的直接原因）：viewport 才是真正的绘制表面，列表 widget 自己的
        palette 不会自动流到它身上。自绘 popup 是**独立顶层窗口**，Qt 喂它
        Inactive 调色板组，而原生 Windows 样式下 ``Inactive.Accent`` 解析为黑色
        → 本该是系统高亮蓝的那条 selection indicator 画成了黑色（见 skill
        ``qt-focus-loss-accent-blacken`` 的 Variant B）。这里统一拉回应用
        **Active** palette；切主题/配色后必须重跑（由 ``_apply_fusion_style`` 触发）。
        """
        pal = _popup_palette()
        self._popup.setPalette(pal)
        self._list.setPalette(pal)
        vp = self._list.viewport()
        if vp is not None:
            vp.setPalette(pal)
        # Re-polish：让 hover / selected item 这类缓存样式色从刚装上的 palette
        # 重新取色；否则只改了数据、画面还是旧色。
        try:
            self._list.style().polish(self._list)
            self._popup.style().polish(self._popup)
        except Exception:
            pass
        self._list.update()
        self._popup.update()
        if vp is not None:
            vp.update()
            # 若此刻弹窗正开着（独立窗口、有自己的绘制周期），排队的 update()
            # 可能赶不上这一帧，直接 repaint 强制同步重绘选中行。
            if self._popup.isVisible():
                vp.repaint()

    def set_menu_shadow(self, enabled):
        """布尔版接口：``True`` = 自绘投影（``effect``），``False`` = 无阴影。

        只在这两个模式间切，兼容老代码/老测试；要试另外两种（``dwm`` /
        ``style``）请用 :meth:`set_menu_mode`。
        """
        self.set_menu_mode(_SHADOW_MODE_EFFECT if enabled
                           else _SHADOW_MODE_OFF)

    def _compute_menu_flags(self):
        """按当前 ``_menu_mode`` 计算菜单窗口 flag（无副作用，只算不落）。

        * ``legacy`` 交还样式自画 → **去掉** Frameless（DWM 会补一圈系统边框
          = 黑边，仅留作对比用）
        * 其余模式全部 **保留** Frameless（去黑边）
        * dwm / style / legacy 去掉 ``NoDropShadow``（要 DWM 原生阴影）
        * off / effect 加上 ``NoDropShadow``（彻底关掉 DWM 入场动画 = 闪烁源）
        """
        mode = self._menu_mode
        flags = self._popup.windowFlags()
        if mode == _SHADOW_MODE_LEGACY:
            flags &= ~Qt.WindowType.FramelessWindowHint
        else:
            flags |= Qt.WindowType.FramelessWindowHint
        if mode in (_SHADOW_MODE_DWM, _SHADOW_MODE_STYLE, _SHADOW_MODE_LEGACY):
            flags &= ~Qt.WindowType.NoDropShadowWindowHint
        else:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        return flags

    def _apply_menu_flags(self):
        """把当前模式的窗口 flag 落到菜单上。

        ⚠️ **只应在隐藏态调用**（构造与 ``set_menu_mode`` 切模式时）。``setWindowFlags``
        在窗口可见/已存在时会销毁并重建 HWND，丢 mouse grab 与 DWM 圆角属性；隐藏态调
        只是更新「待创建」的 flag，不重建。

        ⚠️ 这是**唯一**一处设 flag 的地方。千万不要在 ``_on_about_to_show`` 之类每次
        弹出的路径上重设 —— 在已存在的窗口上 ``setWindowFlags`` 会触发 HWND 重建，
        把 frameless / 无 NoDropShadow 搅乱、DWM 圆角清零，表现就是「只有第一次打开
        正确，后面全乱了」（踩过两次的坑）。
        """
        self._popup.setWindowFlags(self._compute_menu_flags())

    def _apply_dwm_attributes(self):
        """把 DWM 原生圆角属性落到当前 popup HWND（仅 dwm / style 模式需要）。

        ⚠️ 这个属性**不随 HWND 存活**：每次窗口被（重）创建都会丢，所以要在窗口
        生命周期的关键点反复重设 —— ``set_menu_mode``（模式切换）、``Show`` 之后
        （``singleShot(0)`` 再补一刀，见 ``eventFilter`` / ``_Popup.showEvent``）。
        单靠 ``set_menu_mode`` 一处下发，正是之前「圆角时有时无」的根因。

        ⚠️ **绝不在 HWND 尚未创建时调 ``winId()``**：``_apply_dwm_corner_round``
        内部会调 ``widget.winId()``，而 ``winId()`` 会**强制创建** WinId。若构造期
        （``set_menu_mode`` → 这里）就把 popup 的 WinId 创建出来，真实 Windows 上
        那帧窗口用的是「创建时的默认小几何」，随后 ``setFixedSize`` 的尺寸要等 WM
        下一拍才应用 → 第一帧闪一个「和下拉框差不多大的小窗」（构造期 = 启动闪、
        show 时 = 首次点开闪）。用 ``windowHandle() is None`` 判断（**不会**触发
        WinId 创建）跳过，把圆角下发推迟到 ``_Popup.showEvent``（HWND 已就绪时）。
        """
        # ⚠️ 关键：HWND 还没建就不碰 winId()，避免提前创建 WinId 锁死默认几何。
        if self._popup.windowHandle() is None:
            return
        # 明暗跟随：**照抄宿主窗口**（传 ``self`` 让函数去读主窗口的实际值），
        # 不写死 1 —— 原生下拉阴影深色更浓、浅色更淡，写死会让浅色下偏浓。
        _apply_dwm_immersive_dark(self._popup, self)
        # ⚠️ dwm / style 的圆角**只能**由 DWM API 给：这两个模式是不透明（非分层）
        # 窗口，QSS ``border-radius`` 的三角区无法透明 → 圆角失效甚至露黑角
        # （2026-09-07 实测：改成 QSS 圆角后圆角 + 阴影双双丢失）。
        # 代价：DWM 接管合成后阴影偏柔和（比原生 CS_DROPSHADOW 淡一点），
        # 但「有圆角 + 有阴影」远好过「都没」，故两害相权取圆角。
        if self._menu_mode in (_SHADOW_MODE_DWM, _SHADOW_MODE_STYLE):
            _apply_dwm_corner_round(self._popup)

    def set_popup_animation(self, mode, duration_ms=None):
        """设置入场动画：``off`` / ``fade``（淡入）/ ``slide``（下滑展开）。

        用来复刻 ``QMenu`` 的渐显（Qt 在 Windows 上就是调 user32 的
        ``AnimateWindow``）。默认 ``fade`` + 120ms，就是原生菜单那一档观感。

        ⚠️ 时长别贪：``AnimateWindow`` 是**同步阻塞**的（Qt 的 QMenu 也一样），
        设成几百毫秒会让「点击 → 展开」明显发钝。
        """
        if mode not in _ANIM_MODES:
            raise ValueError("未知的入场动画模式: %r（可选 %s）"
                             % (mode, ", ".join(_ANIM_MODES)))
        self._anim_mode = mode
        if duration_ms is not None:
            self._anim_ms = max(0, int(duration_ms))

    def _play_open_animation(self):
        """播入场动画（由 ``showMenu`` 在 **show 之前**调）。

        返回 ``True`` 表示「窗口已经显示出来了，调用方不要再 ``show()``」。

        两种模式都用**纯 Qt 动画**，不依赖任何 Win32 怪癖：
        - 淡入（fade）：``windowOpacity`` 0 → 1 的 ``QPropertyAnimation``。观感与
          QMenu 的 ``AnimateWindow(AW_BLEND)`` 一致；弹窗是分层窗口、DWM 圆角属性
          照旧保留（QMenu 的 popup 正是「分层 + 不透明 + 圆角」组合），既淡入也有
          圆角。⚠️ **曾用 ``AnimateWindow(AW_BLEND)`` 复刻，但它要求 ``WS_EX_LAYERED``
          且 frameless 弹窗默认不带，补样式不生效时静默失败、落到下滑兜底 —— 正是
          「淡入看着像下滑」的真因。用 ``windowOpacity`` 从根上绕开，offscreen 也能测。
        - 下滑（slide）：只改**高度**的几何展开，不碰透明度，圆角/阴影照旧。
        """
        self._anim_diag = "关"
        # 先清空上一轮残留的动画引用（否则「淡入→下滑」切换后旧 _fade_anim 仍在，
        # 会被误判成「两种模式都建了淡入」）。本轮用到哪个再在哪个分支里重建。
        self._fade_anim = None
        self._slide_anim = None
        if self._anim_mode == _ANIM_MODE_OFF or self._anim_ms <= 0:
            self._popup.setWindowOpacity(1.0)   # 复位：上一次淡入若中途关闭，opacity 可能残留 0
            return False
        # 圆角先落定（此刻窗口仍隐藏，DWM 属性设在隐藏 HWND 上，动画一开始就是圆角）。
        self._apply_dwm_attributes()
        if self._anim_mode == _ANIM_MODE_FADE:
            # 真·淡入：先置 0（隐藏态 show 出来是透明的），再 Qt 动画拉到 1。
            self._popup.setWindowOpacity(0.0)
            self._popup.show()
            self._start_fade_animation()
            self._anim_diag = "Qt windowOpacity 淡入（%d ms）" % self._anim_ms
            return True
        # 下滑：先复位透明度（避免继承上一次淡入的透明残留），再 show 量全高、压起点高度。
        self._popup.setWindowOpacity(1.0)
        self._popup.show()
        self._prepare_slide_start()
        self._start_slide_animation()
        self._anim_diag = "Qt 高度下滑（%d ms）" % self._anim_ms
        return True

    def _start_fade_animation(self):
        """真·淡入：``windowOpacity`` 0 → 1 的 Qt 动画。

        不用 ``AnimateWindow(AW_BLEND)``：它要求窗口带 ``WS_EX_LAYERED``，frameless
        弹窗默认不带，补样式需 ``SetWindowLongPtrW`` + ``SetWindowPos(SWP_FRAMECHANGED)``
        才生效，否则静默失败。``windowOpacity`` 由 Qt 自己管理分层与 alpha，DWM 圆角
        在分层窗口上照旧保留，观感与原生菜单一致，且 offscreen 也能跑（可断言）。
        """
        if self._anim_ms <= 0:
            self._popup.setWindowOpacity(1.0)
            return
        anim = QPropertyAnimation(self._popup, b"windowOpacity", self)
        anim.setDuration(self._anim_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.finished.connect(lambda: self._popup.setWindowOpacity(1.0))
        self._fade_anim = anim          # 必须持有引用，否则被 GC 后动画停摆
        anim.start()

    def _prepare_slide_start(self):
        """下滑动画前，把弹窗高度压到起点（约 35% 全高）。

        ⚠️ 由 ``_play_open_animation`` 在 ``show()`` **之后**调用：顶层窗口尺寸
        由窗口系统在 show 时才落定，show 之前 height() 还是 0，量不到真实全高。
        """
        full = self._popup.height()      # show 之后已是 _sync_popup_size 定的全高
        if full <= 0:
            return
        start = max(1, int(full * 0.35))
        self._slide_full_h = full
        # ⚠️ 必须先解掉固定尺寸：``setFixedSize`` 把 min=max 钉死后，
        # QPropertyAnimation 改 "size" 会被夹住 → 动画一步不动。
        self._popup.setMinimumHeight(0)
        self._popup.setMaximumHeight(16777215)
        self._popup.resize(self._popup.width(), start)

    def _start_slide_animation(self):
        """纯 Qt 的「从上往下展开」动画：只改**高度**，不碰透明度。

        与淡入（``windowOpacity``）是两条独立的路径。下滑刻意不用 opacity：
        改高度只是把 popup 裁剪掉一部分，DWM 属性不受影响，圆角/阴影照旧；
        而且「整块从上往下滑出」的观感本就是改几何，不是改透明度。
        """
        full = getattr(self, "_slide_full_h", 0)
        if full <= 0 or self._anim_ms <= 0:
            return
        start = self._popup.height()     # 已是 _prepare_slide_start 压到的起点
        if start >= full:
            return
        anim = QPropertyAnimation(self._popup, b"size", self)
        anim.setDuration(self._anim_ms)
        anim.setStartValue(QSize(self._popup.width(), start))
        anim.setEndValue(QSize(self._popup.width(), full))

        def _pin_back():
            self._popup.setFixedSize(self._popup.width(), full)

        anim.finished.connect(_pin_back)
        self._slide_anim = anim          # 必须持有引用，否则被 GC 后动画停摆
        anim.start()

    def set_menu_mode(self, mode):
        """切换下拉菜单的画法，用于真机 A/B 对比观感与闪烁。

        五种模式的差别全在 ``FramelessWindowHint`` / ``NoDropShadowWindowHint``
        两个 flag + 是否调 DWM 圆角 API，见模块常量 ``_SHADOW_MODE_*`` 那张表。

        ⚠️ **阴影与圆角的正确组合（2026-09-06 修复版）**：
        ``dwm`` / ``style`` 都**保留 Frameless**（不引入黑边）+ **不**加
        ``NoDropShadowWindowHint``（让 DWM 画原生阴影）+ **调** DWM 圆角 API
        （让 frameless 窗口也能获得 DWM 圆角）。这是 Win11 上「无黑边 + 圆角
        + 原生阴影」的唯一受支持组合。

        ⚠️ ``legacy`` 是 2026-09-02 之前那版的「去 Frameless 换系统阴影」
        实现 —— 在 Win11 原生样式下 DWM 会补一圈系统窗口边框（黑边）。
        仅留作对比，**不要当默认**。

        ⚠️ ``effect`` 模式必须开 ``WA_TranslucentBackground``：菜单不透明
        的话阴影区域会被填成实色，看起来还是一圈黑边。代价是窗口变分层窗口，
        DWM 不再给圆角（Win11 下变直角）。

        ⚠️ 用 ``overrideWindowFlags`` 而不是 ``setWindowFlags``：后者会销毁
        并重建底层窗口、丢 mouse grab（即使菜单隐藏也丢）。且必须在菜单隐藏
        时调。
        """
        if mode not in _SHADOW_MODES:
            raise ValueError("未知的下拉菜单模式: %r（可选 %s）"
                             % (mode, ", ".join(_SHADOW_MODES)))
        self._menu_mode = mode
        # ⚠️ 自绘阴影（QGraphicsDropShadowEffect）**只用在 effect 模式**（方角 +
        # 半透明 + 自绘投影）。``dwm``（原生无闪烁）**不**自绘阴影：它走系统
        # ``CS_DROPSHADOW``（非分层窗口自动获得），与原生 QComboBox 下拉同机制、
        # 同浓淡——这是探针实测后定下的根因级修复（之前误用分层+自绘阴影导致
        # WS_EX_LAYERED=True、阴影被系统吞掉、观感发虚）。style / off / legacy
        # 也不自绘阴影。
        self._menu_shadow = (mode == _SHADOW_MODE_EFFECT)

        # ⚠️ 一次性把窗口 flag 设好。**只在这里（隐藏态、构造/切模式时）调用**，
        # 绝不在每次弹出时重设 —— 已存在（隐藏）窗口上调 ``overrideWindowFlags``
        # 会触发 HWND 重建，把刚设好的 frameless / 无 NoDropShadow 搅乱，DWM 圆角
        # 属性也一并清零 → 这正是之前「只有第一次打开正确、后面全乱了」的根因。
        # 圆角/阴影「时有时无」的反复重设改由 ``_Popup.showEvent`` 承担
        # （弹出时 HWND 已就绪、且不重建窗口）。
        self._apply_menu_flags()

        # 只有 effect 模式需要半透明：菜单不透明的话自绘阴影区域会被填成实色，
        # 看起来还是一圈黑边。dwm / off / style 都是**不透明**非分层窗口，阴影由
        # 系统 CS_DROPSHADOW 提供（dwm 靠这个拿到与原生一致的轻阴影）。
        self._popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,
                                self._menu_shadow)
        # ⚠️ popup 的内容边距 / 阴影外扩在 ``_sync_popup_size`` 里按模式算
        # （effect 留 _SHADOW_MARGIN 给自绘投影，其余留原生 pad）。

        # ⚠️ DWM 圆角属性：frameless 窗口 DWM 默认不圆（``DWMWCP_DONOTROUND``），
        # 只有显式调 ``DWMWCP_ROUND`` 才会按 ROUND 处理。此属性不随 HWND 存活，
        # 这里只是「切模式时」一处下发点；真正保证每次显示都生效的是
        # ``_Popup.showEvent``（每次 show 都会重设，见其类docstring）。
        self._apply_dwm_attributes()

        self._apply_popup_qss()

        if self._menu_shadow:   # 仅 effect 模式：半透明 + 自绘投影（方角）
            # ⚠️ 每次都**新建**：``setGraphicsEffect(None)`` 会让 Qt 把旧 effect
            # 删掉，缓存起来再挂回去会踩 ``already deleted``（实测）。开关不是
            # 热路径，重建的开销可以忽略。
            self._shadow_effect = QGraphicsDropShadowEffect(self._list)
            self._shadow_effect.setOffset(0, 1)
            self._shadow_effect.setBlurRadius(_SHADOW_MARGIN * 2)
            self._shadow_effect.setColor(QColor(0, 0, 0, 90))
            self._list.setGraphicsEffect(self._shadow_effect)
        else:
            # ⚠️ 必须显式摘掉：effect 会把列表渲染到离屏缓冲，留着既费性能
            # 又会让「关阴影」这一侧看起来仍然发虚。dwm 走系统 CS_DROPSHADOW，
            # 同样不需要这个 effect。
            self._list.setGraphicsEffect(None)
            self._popup.setGraphicsEffect(None)
            self._shadow_effect = None   # 已被 Qt 删除，别留悬挂引用

    def _apply_popup_qss(self):
        """按当前模式重新下发弹窗 QSS。

        QSS 是**与样式无关**的类常量（不像高度补偿要按样式查表），所以只需
        在切模式时下发；切样式/配色处调一次是为了让 palette 同步生效后重画。

        ⚠️ **边框画在 popup 窗口自身**（``#CustomComboPopup``），不画在列表上——
        这是修「hover 首/末项时选项上下抖」的关键：列表 borderless → 其 viewport
        恰好 = 行高总和，零溢出，``scrollTo`` 不 nudges。
        四份的取舍：
        * ``effect``  → popup 透明（给自绘阴影留画布），边框下放到列表（方角）。
        * ``dwm`` / ``style`` / ``off`` → popup 画 1px 边框 + 不透明背景；
          ``dwm`` / ``style`` 的圆角**由 DWM API 给**（见
          ``_apply_dwm_attributes``），**不能**用 QSS ``border-radius``。
        * ``legacy``  → popup 什么都不画，交还 DWM 画面板（**会引入黑边**）。

        ⚠️ **为什么 dwm 不能用 QSS 圆角**：``border-radius`` 只在圆角外的三角区
        能真正透明时才成立，而这需要 ``WA_TranslucentBackground``（分层窗口）。
        dwm / style 都是**不透明**窗口，硬加 QSS 圆角 → 三角区填不出透明、圆角
        不生效甚至露黑角（2026-09-07 实测：改走 QSS 圆角后「圆角和阴影都没了」）。
        所以圆角只能交给 DWM API。
        """
        if self._menu_mode == _SHADOW_MODE_EFFECT:
            qss = self._POPUP_QSS_SHADOW
        elif self._menu_mode in (_SHADOW_MODE_DWM, _SHADOW_MODE_STYLE,
                                 _SHADOW_MODE_OFF):
            qss = self._POPUP_QSS_BORDERLESS
        elif self._menu_mode == _SHADOW_MODE_LEGACY:
            qss = self._POPUP_QSS_NATIVE
        else:
            qss = self._POPUP_QSS_FRAMELESS
        self._popup.setStyleSheet(self._resolved_popup_qss(qss))

    def _resolved_popup_qss(self, qss):
        """把 QSS 模板里的背景角色替换成当前 ``_popup_bg_role``。

        三份模板都写 ``palette(base)``，这里做一次字符串替换即可，**不复制
        三份 QSS** —— 避免以后改边框时漏改其中一份。
        """
        if self._popup_bg_role == _POPUP_BG_ROLE_WINDOW:
            return qss.replace("palette(base)", "palette(window)")
        return qss

    def set_popup_bg_role(self, role):
        """切换 popup 背景的 palette 角色（``base`` / ``window``），立即重刷 QSS。

        原生 QComboBox 下拉实测内部色 = ``palette(window)``（= 宿主容器色 →
        「融入背景」）；我们历史上用 ``palette(base)``（浅色下纯白 → 「独立
        卡片」）。哪個更好看取决于下拉所在容器的配色，所以做成开关而不是写死。

        非法值静默忽略（避免外部传入空串把 QSS 搞坏）。
        """
        role = str(role).strip().lower()
        if role not in _POPUP_BG_ROLES or role == self._popup_bg_role:
            return
        self._popup_bg_role = role
        self._apply_popup_qss()

    def popup_bg_role(self):
        """当前 popup 背景用的 palette 角色。"""
        return self._popup_bg_role

    def _on_about_to_show(self):
        # ⚠️ 不在这里重设 flag / DWM：flag 在构造与 ``set_menu_mode`` 时设一次
        # （窗口复用 HWND，flag 随成员保留）；DWM 圆角在 ``_Popup.showEvent``
        # 每次显示都补回（属性不随 HWND 存活）。重复设 flag 会在已存在的窗口上
        # 触发重建，把刚设好的状态搅乱 —— 正是「首次对、后续乱」的根因。
        self._sync_popup_size()
        if self._index >= 0:
            index = self._model.index(self._index, 0)
            self._list.setCurrentIndex(index)
            self._list.scrollTo(index)
        # 菜单弹出后焦点要落在列表上，否则键盘导航收不到事件
        QTimer.singleShot(0, self._list.setFocus)

    def _sync_popup_size(self):
        count = self._model.rowCount()
        if count <= 0:
            # ⚠️ **空下拉也要把尺寸缩到最小**：``clear()`` 之后 ``_model`` 空了，
            # 但上一回有内容时 ``setFixedSize`` 钉下的大尺寸不会被自动清掉。若这里
            # 直接 ``return``，下次 ``showMenu`` 打开的就是一个又高又空的「大白框」，
            # 看着像控件坏了、用不了（用户在 combo_dropin_a.py 真机复现过）。列表
            # 无内容、popup 只留内边距/边框 → 一条很薄的空框，符合空下拉的观感。
            ml, mt, mr, mb = _shadow_margins(self._menu_mode) if self._menu_shadow \
                else (0, 0, 0, 0)
            pad = native_combo_popup_padding()
            w = max(self.width(), 40)
            self._list.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._list.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._list.setFixedSize(w, 0)
            self._list.updateGeometries()
            self._list.executeDelayedItemsLayout()
            if self._menu_shadow:
                lay = (ml, mt, mr, mb)      # 自绘阴影：popup 透明，留外扩给阴影
            else:
                lay = (pad, pad, pad, pad)  # 其余：popup 画边框，留原生 pad
            self._popup.layout().setContentsMargins(*lay)
            self._popup.setFixedSize(w + ml + mr, mt + mb)
            return
        # 行高与原生下拉对齐（Windows 11 26px / Fusion 20px，QListView 默认
        # 只有 12px）。切主题后要重量一次，所以每次弹出都设。
        # ⚠️ 改完 delegate 尺寸**必须强制重排**：否则 visualRect 里各行的 y 偏移
        # 仍是旧行高（实测：高度变了但位置没变，行会叠在一起）。
        row_h = native_combo_row_height()
        if self._list._delegate.row_height != row_h:
            self._list._delegate.row_height = row_h
            self._list.scheduleDelayedItemsLayout()
        self._list.executeDelayedItemsLayout()
        fm = self._list.fontMetrics()
        rows = min(count, _MAX_VISIBLE_ROWS)
        # 文字宽度用字体度量算，不用 sizeHintForColumn —— 后者依赖列表当前
        # 的布局状态，首次弹出时会拿到离谱的值（实测 offscreen 下给到 640）。
        text_w = max(fm.horizontalAdvance(self.itemText(row))
                     for row in range(count))
        # 上下左右留白要跟原生一致（windows11 各 2px，vista/fusion 各 1px）。
        # 只加列表自己的 frameWidth（恒 1）会让 windows11 比原生矮 2px。
        pad = native_combo_popup_padding()
        # 样式自画（style）模式下面板是样式自己画的，与原生下拉的面板内边距
        # 不一定严丝合缝；真机上用户对「边距/间隙」最敏感，给一个现场微调量
        # （逻辑像素，可负）。其余模式保持原生 pad 不动，以免破坏和原生对齐。
        if self._menu_mode == _SHADOW_MODE_STYLE:
            pad = max(0, pad + self._style_pad_extra)
        # 宽度至少与按钮同宽；内容更宽时按内容撑开。
        # ⚠️ 原生 QComboBox 的 popup 容器就是这么算的（实测容器宽 == 按钮宽
        # 200），不要在这里再加 padding —— 多加会让菜单比按钮宽出一截。
        menu_w = max(self.width(), text_w + 2 * _LIST_HMARGIN)
        list_h = row_h * rows

        # ⚠️ **自绘阴影模式（effect / dwm）：边框画在列表上、popup 透明留外扩给阴影**
        # —— 列表若在 QSS 里带 1px 边框，它的 viewport 会比内容矮 2px → 溢出 2px →
        # ``scrollTo(EnsureVisible)`` 把内容 nudges 1~2px；所以列表边框要配合把
        # 垂直滚动范围锁死（见下方 ``setRange(0,0)``），条目 ≤ 上限时彻底禁掉位移。
        # 其余模式（off / style）：边框画在 popup 窗口**内部**（QSS 的 border 不撑大
        # 控件），列表 borderless、viewport 恰好 = 行高总和，零溢出，不抖。
        border_on_popup = (self._menu_mode == _SHADOW_MODE_OFF
                           or self._menu_mode == _SHADOW_MODE_STYLE)
        border_on_list = self._menu_shadow   # effect / dwm 都用自绘阴影（边框在列表）
        ml, mt, mr, mb = _shadow_margins(self._menu_mode) if self._menu_shadow \
            else (0, 0, 0, 0)                # 自绘阴影外扩（dwm 更大以容纳模糊）

        if border_on_list:
            list_w = menu_w - 2 * pad + 2      # +2 给列表自己的 1px 边框
            list_h_full = list_h + 2
            lay = (ml, mt, mr, mb)            # 四边留外扩给自绘阴影
            popup_w = list_w + ml + mr
            popup_h = list_h_full + mt + mb
        else:
            list_w = menu_w - 2 * pad          # 列表无边框，viewport 恰好 = 内容
            list_h_full = list_h
            lay = (pad, pad, pad, pad)         # popup 上的留白（= 原生面板内边距）
            # ⚠️ 边框画在 popup 窗口**内部**（QSS 的 border 不撑大控件），所以
            # 容器尺寸 = 列表 + 2*pad，与原生下拉一致——**不要再 +2*bp**，否则
            # 容器会比原生大 2px（实测：下方和右方多出一圈边，和原生对不齐）。
            popup_w = list_w + 2 * pad
            popup_h = list_h_full + 2 * pad

        # ⚠️ 滚动条策略必须**在 setFixedSize 之前**定死，不能交给
        # ScrollBarAsNeeded 自己判：QAbstractScrollArea 的布局是两遍的——
        # 第一遍按**旧视口**判定「可能需要滚条」先占掉 14px，要等第二遍
        # updateGeometries() 才会把 14px 还回来。只调一次 updateGeometries()
        # 的话视口就永远窄 14px（实测列表 198、视口 184），条目跟着被裁，
        # 高亮条铺不满整行。先定策略就能单遍算对。
        #   - 条目数没超上限 → 高度恒等于 row_h * rows，不可能需要滚条
        #   - 超了 → 只留竖向，横向一律关（文字再长也不该横向滚）
        self._list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if count > _MAX_VISIBLE_ROWS else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # ⚠️ **防抖动（二次保险）**：列表即使还有 1px 边框的残留溢出，也把垂直
        # 滚动范围锁死在 0，彻底禁掉 ``scrollTo`` 的 1~2px 位移。条目 ≤ 上限时
        # 本就不需要滚；超上限时保留滚动（否则长列表点不到），所以只在不超时锁。
        if count <= _MAX_VISIBLE_ROWS:
            self._list.verticalScrollBar().setRange(0, 0)
        self._list.setFixedSize(list_w, list_h_full)
        # ⚠️ 改完尺寸仍要显式同步视口几何：QAbstractScrollArea 的 viewport
        # 尺寸是**滞后**的，setFixedSize 之后 viewport 还停在旧尺寸（实测
        # 列表已改成 196x78、viewport 仍是 254x190），条目会按旧宽度绘制而
        # 被裁掉。updateGeometries() 立即把视口对齐到新的列表尺寸。
        self._list.updateGeometries()
        self._list.executeDelayedItemsLayout()
        # popup 窗口：布局留白 + 自身 1px 边框（borderless 列表已无边框），
        # 尺寸钉死成「列表 + 留白 + 边框」，不再有 QMenu 那种 action 布局机械
        # 撑出额外空白。
        self._popup.layout().setContentsMargins(*lay)
        self._popup.setFixedSize(popup_w, popup_h)

    def _on_row_activated(self, index):
        row = index.row()
        self.setCurrentIndex(row)
        self.activated.emit(row)
        self._popup.close()

    # ------------------------------------------------- QComboBox 的 API 子集

    def addItem(self, text, data=None):
        item = QStandardItem(text)
        if data is not None:
            item.setData(data, Qt.ItemDataRole.UserRole)
        self._model.appendRow(item)
        if self._index < 0:
            self.setCurrentIndex(0)
        # ⚠️ 预钉 popup 尺寸：在「第一次 show（WinId 真正创建）之前」就把列表
        # 尺寸算好并 setFixedSize。frameless 顶层窗口只有在 WinId 创建那一刻用的
        # 几何才会成为首帧几何；若等到 showMenu 里再 setFixedSize（此时若 WinId
        # 已因历史原因存在），真实 Windows 上首帧会用默认小几何闪一下「和下拉框
        # 差不多大的小窗」。这里趁 WinId 尚未创建（构造期 hidden 状态）钉死，
        # 首帧尺寸即正确。空模型分支（count<=0）会缩到最小，无副作用。
        self._sync_popup_size()

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def count(self):
        return self._model.rowCount()

    def itemText(self, row):
        item = self._model.item(row)
        return item.text() if item is not None else ""

    def itemData(self, row):
        item = self._model.item(row)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def currentIndex(self):
        return self._index

    def currentText(self):
        return self.itemText(self._index)

    def currentData(self):
        return self.itemData(self._index)

    def setCurrentIndex(self, row):
        if row == self._index or not 0 <= row < self.count():
            return
        self._index = row
        self.setText(self.itemText(row))
        self.currentIndexChanged.emit(row)

    def findData(self, data):
        for row in range(self.count()):
            if self.itemData(row) == data:
                return row
        return -1

    def findText(self, text):
        for row in range(self.count()):
            if self.itemText(row) == text:
                return row
        return -1

    # -------------------------------------------------------- 探针用的接口

    def popup_open(self):
        return self._popup.isVisible()

    def showMenu(self):
        """点击按钮时弹出下拉（替代 QMenu 的默认 showMenu）。

        QToolButton 原生的 ``showMenu()`` 是给 ``QMenu`` 用的；本控件改用
        ``QWidget`` 弹窗，所以重写这一下：定位到按钮正下方并 ``show``。
        ``Qt::Popup`` 自带「点外面自动关闭」，无需自己管关闭。
        """
        if self._popup.isVisible():
            self._popup.close()
            return
        # 定位：popup 左上角对齐按钮左下角（与原生下拉一致）
        self._popup.move(self.mapToGlobal(QPoint(0, self.height())))
        # ⚠️ 尺寸/高亮必须在**显示之前**定好：AnimateWindow 是拿真实 HWND 播
        # 动画的，此刻窗口尺寸若还是上一次的旧值，动画就是错的。
        self._on_about_to_show()
        # ⚠️ 动画在 **show 之前**播（AnimateWindow 自己负责显示窗口），返回
        # True 表示它已经把窗口显示出来了，别再 show 一次。
        if not self._play_open_animation():
            # OFF 模式：动画函数没显示也没动 opacity。先置 0 再 show，保证
            # 第一帧不可见；下一拍尺寸已落定再显形，彻底规避「首帧小窗闪现」
            # （真实 Windows 上 frameless 弹窗偶发的尺寸未同步帧）。
            self._popup.setWindowOpacity(0.0)
            self._popup.show()
            QTimer.singleShot(0, lambda: self._popup.setWindowOpacity(1.0))
        self.update()

    def row_at(self, global_pos):
        """全局坐标落在列表第几行；不在列表内返回 ``None``。"""
        viewport = self._list.viewport()
        local = viewport.mapFromGlobal(global_pos)
        if not viewport.rect().contains(local):
            return None
        index = self._list.indexAt(local)
        return index.row() if index.isValid() else None


class NativeCombo:
    """把原生 QComboBox 包一层，暴露与 CustomComboBox 相同的探针接口。"""

    def __init__(self, combo):
        self._combo = combo

    def currentIndex(self):
        return self._combo.currentIndex()

    def popup_open(self):
        return self._combo.view().isVisible()

    def row_at(self, global_pos):
        view = self._combo.view()
        viewport = view.viewport()
        local = viewport.mapFromGlobal(global_pos)
        if not viewport.rect().contains(local):
            return None
        index = view.indexAt(local)
        return index.row() if index.isValid() else None


class ClickProbe(QObject):
    """全局鼠标探针：报告每次点击的「按住时长 → 目标项 → 是否生效」。

    装成全局 filter 而不是逐个装到控件上，是因为 popup / 菜单都是独立的
    顶层窗口，逐个安装太脆（原生侧每次 hidePopup 还会 deleteLater 容器）。
    """

    def __init__(self, combos, label, parent=None):
        super().__init__(parent)
        self._combos = combos          # {显示名: 探针适配器}
        self._label = label
        self._t0 = None
        self._snapshot = {}

    def eventFilter(self, _obj, event):
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress:
            self._t0 = time.perf_counter()
            self._snapshot = {name: w.currentIndex()
                              for name, w in self._combos.items()}
            return False

        if etype == QEvent.Type.MouseButtonRelease and self._t0 is not None:
            hold_ms = (time.perf_counter() - self._t0) * 1000.0
            self._t0 = None
            target = self._locate(event.globalPosition().toPoint())
            if target is None:
                self._label.setText(
                    "按住 %d ms → 松手时不在任何列表内"
                    "（打开/关闭下拉的那一击，不计）" % hold_ms)
                return False
            name, row = target
            before = self._snapshot.get(name, -1)
            # 值的提交发生在 release 之后，稍等一拍再判定
            QTimer.singleShot(
                200,
                lambda h=hold_ms, n=name, r=row, b=before: self._report(
                    h, n, r, b))
        return False

    def _locate(self, global_pos):
        for name, widget in self._combos.items():
            if not widget.popup_open():
                continue
            row = widget.row_at(global_pos)
            if row is not None:
                return name, row
        return None

    def _report(self, hold_ms, name, row, before):
        after = self._combos[name].currentIndex()
        if before == row:
            verdict = "・点的就是当前项，换另一项再测"
        elif after == row:
            verdict = "✅ 生效"
        else:
            verdict = "❌ 没反应"
        self._label.setText(
            "按住 %d ms → <b>%s</b> 第 %d 项 → %s（%d → %d）"
            % (hold_ms, name, row, verdict, before, after))


def _build_column(title, factory):
    """一列下拉（同一 factory 造三个），返回 (groupbox, [控件...])。"""
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    combos = []
    for caption, items in ROWS:
        row = QHBoxLayout()
        row.addWidget(QLabel(caption))
        combo = factory()
        combo.addItems(items)
        combo.setCurrentIndex(0)
        row.addWidget(combo, 1)
        layout.addLayout(row)
        combos.append(combo)
    layout.addStretch(1)
    return box, combos


def _dark_palette():
    """一份够用的深色配色，只为看观感，不追求完整。"""
    palette = QPalette()
    pairs = (
        (QPalette.ColorRole.Window, "#2b2b2b"),
        (QPalette.ColorRole.WindowText, "#e8e8e8"),
        (QPalette.ColorRole.Base, "#1f1f1f"),
        (QPalette.ColorRole.AlternateBase, "#2b2b2b"),
        (QPalette.ColorRole.Text, "#e8e8e8"),
        (QPalette.ColorRole.Button, "#333333"),
        (QPalette.ColorRole.ButtonText, "#e8e8e8"),
        (QPalette.ColorRole.Highlight, "#2a6fbf"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    )
    for role, color in pairs:
        palette.setColor(role, QColor(color))
    return palette


def build_window():
    """构建演示窗口，返回 ``(window, combos)``。

    ``combos`` 是 ``{显示名: 探针适配器}``，供 ``ClickProbe`` 使用
    （也让本模块可被 offscreen 冒烟测试直接构造）。
    """
    native_style_name = QApplication.style().objectName()

    window = QWidget()
    root = QVBoxLayout(window)

    intro = QLabel(
        "<b>左：原生 QComboBox（对照）　右：CustomComboBox 原型</b><br>"
        "两边内容完全一样。请对两侧做同一组操作，重点是 <b>A 组</b>：<br>"
        "A 闪电点<b>第 1 项</b>　B 闪电点第 3 项　"
        "C 停 1 秒点第 1 项　D 按住第 1 项 1 秒"
    )
    intro.setWordWrap(True)
    root.addWidget(intro)

    bar = QHBoxLayout()
    style_box = QComboBox()
    style_box.addItems(["原生", "Fusion"])
    dark_box = QCheckBox("深色配色")
    mode_box = QComboBox()
    mode_box.addItem("无阴影（frameless 自画边框）", _SHADOW_MODE_OFF)
    mode_box.addItem("自绘投影（半透明，直角）", _SHADOW_MODE_EFFECT)
    mode_box.addItem("系统阴影（frameless + DWM 阴影）", _SHADOW_MODE_DWM)
    mode_box.addItem("样式自画（交还样式，圆角+阴影）", _SHADOW_MODE_STYLE)
    mode_box.setToolTip(
        "下拉菜单的画法（**真机逐个切、逐个看**）：\n"
        "① 无阴影（off）：frameless + QSS 自画 1px 边框，最稳（默认）\n"
        "② 自绘投影（effect）：frameless + 半透明 + QGraphicsDropShadowEffect。\n"
        "   阴影最可控，但**开了半透明 DWM 就不再给圆角** → Win11 下变直角\n"
        "③ DWM 圆角（dwm）：frameless + DWM 原生阴影 + 调 DWM API 强制圆角\n"
        "   → **无黑边 + 圆角 + 阴影**（推荐 A）\n"
        "④ 自画边框（style）：同 dwm，但 QSS 自己画 1px 边框替代 DWM 系统边框\n"
        "   → 边框颜色可控，最接近原生观感（推荐 B）\n"
        "⑤ legacy（旧版）：去 Frameless 换系统阴影，**Win11 原生下有黑边**\n"
        "   → 仅留作 A/B 对比，不要当默认")
    # 真机高度微调：offscreen 量不出这个差异，只能现场调
    height_box = QSpinBox()
    height_box.setRange(-4, 8)
    height_box.setToolTip(
        "原型按钮高度的补偿值（**逻辑**像素，会被 DPI 缩放乘一遍）\n"
        "按样式分别记：切样式时这个框会跟着显示该样式的补偿值\n"
        "真机 150% 缩放下实测：原生 36px、原型 33px → 逻辑上差 2px\n"
        "调到两侧按钮像素高度一致为止，然后把值告诉我钉死成默认值")
    # 样式自画（style）模式的面板内边距现场微调：圆角+阴影观感最敏感，
    # 真机调到「完美」后把值告诉我钉成默认。逻辑像素，可负（往里收）。
    style_pad_box = QSpinBox()
    style_pad_box.setRange(-4, 8)
    style_pad_box.setSuffix(" px")
    # ⚠️ 默认 0：用户 2026-09-06 真机实测「0 与原生一致」（旧注释里的 -2 是
    # 边框还画在列表上那版的结论，已过期）。
    style_pad_box.setValue(0)
    style_pad_box.setToolTip(
        "**样式自画（style）模式**下拉菜单的面板内边距微调（逻辑像素，可负）\n"
        "正值=面板与条目之间留白更大（更松）；负值=更紧、条目更贴边\n"
        "只对 style 模式生效，其它模式保持与原生对齐不动\n"
        "调到你觉得「边距/间隙」舒服为止，然后把值告诉我钉死")
    # 入场动画：淡入 = windowOpacity 0→1（与 QMenu 的 AW_BLEND 观感一致）
    anim_box = QComboBox()
    anim_box.addItem("关（无动画）", _ANIM_MODE_OFF)
    anim_box.addItem("淡入（QMenu 观感）", _ANIM_MODE_FADE)
    anim_box.addItem("下滑展开", _ANIM_MODE_SLIDE)
    anim_box.setCurrentIndex(1)          # 默认淡入
    anim_box.setToolTip(
        "下拉展开时的入场动画（**复刻 QMenu 的渐显**）\n"
        "淡入 = windowOpacity 0→1 的 Qt 动画，与 Win11 原生菜单那一档观感一致\n"
        "下滑 = 只改高度的几何展开，整块从按钮下方滑出\n"
        "两种都是纯 Qt 动画，不依赖 Win32 AnimateWindow（后者 frameless 下会静默失败）")
    anim_ms_box = QSpinBox()
    anim_ms_box.setRange(0, 500)
    anim_ms_box.setSingleStep(10)
    anim_ms_box.setSuffix(" ms")
    anim_ms_box.setValue(_DEFAULT_ANIM_MS)
    anim_ms_box.setToolTip("入场动画时长（毫秒），0 = 关。Win11 系统菜单约 100~150ms")
    # 阴影模式的圆角半径不再可调（自绘圆角方案已撤，见 _POPUP_QSS_SHADOW）
    bar.addWidget(QLabel("应用样式："))
    bar.addWidget(style_box)
    bar.addWidget(dark_box)
    bar.addWidget(QLabel("　菜单画法："))
    bar.addWidget(mode_box)
    bar.addWidget(QLabel("　高度补偿："))
    bar.addWidget(height_box)
    bar.addWidget(QLabel("　样式边距："))
    bar.addWidget(style_pad_box)
    bar.addWidget(QLabel("　入场动画："))
    bar.addWidget(anim_box)
    bar.addWidget(anim_ms_box)
    bar.addStretch(1)
    root.addLayout(bar)

    # 当前模式的「真机该看什么」提示，跟着上面的下拉切换
    mode_hint = QLabel()
    mode_hint.setWordWrap(True)
    root.addWidget(mode_hint)

    # 入场动画的真机诊断：实际播的是哪种 Qt 动画（仅诊断用，不影响行为）。
    anim_diag = QLabel("入场动画：未播放")
    anim_diag.setWordWrap(True)
    anim_diag.setToolTip(
        "显示最近一次入场动画的真实结果\n"
        "「Qt windowOpacity 淡入」= 用 windowOpacity 0→1 的 Qt 动画（与 QMenu 观感一致）\n"
        "「Qt 高度下滑」= 只改高度的几何展开\n"
        "「关」= 无动画（模式=off 或时长=0）\n"
        "若想看这个数变化，展开一次下拉即可")
    root.addWidget(anim_diag)

    def _refresh_anim_diag():
        texts = sorted({c._anim_diag for c in custom_combos})
        anim_diag.setText("入场动画：" + " / ".join(texts))

    diag_timer = QTimer(window)
    diag_timer.timeout.connect(_refresh_anim_diag)
    diag_timer.start(300)

    # 实测读数：不用手数像素，直接看物理像素高度差
    measure = QLabel("测量中…")
    measure.setToolTip(
        "物理像素高度 = 逻辑高度 × devicePixelRatio\n"
        "150% 缩放下 DPR=1.5：原生 24 逻辑 → 36px，原型 22 逻辑 → 33px\n"
        "所以「差 3px」换算回逻辑像素是**差 2**，补偿值填 2 而不是 3")
    refresh_btn = QPushButton("重新测量")
    mrow = QHBoxLayout()
    mrow.addWidget(measure)
    mrow.addWidget(refresh_btn)
    mrow.addStretch(1)
    root.addLayout(mrow)

    columns = QHBoxLayout()
    native_box, native_combos = _build_column("① 原生 QComboBox（对照）",
                                              QComboBox)
    custom_box, custom_combos = _build_column("② CustomComboBox（原型）",
                                              CustomComboBox)
    columns.addWidget(native_box)
    columns.addWidget(custom_box)
    root.addLayout(columns)

    log = QLabel("等待点击…")
    log.setWordWrap(True)
    root.addWidget(log)

    combos = {}
    for (caption, _), native, custom in zip(ROWS, native_combos,
                                            custom_combos):
        combos["原生·%s" % caption] = NativeCombo(native)
        combos["原型·%s" % caption] = custom

    probe = ClickProbe(combos, log, window)
    QApplication.instance().installEventFilter(probe)

    def apply_style():
        name = "Fusion" if style_box.currentText() == "Fusion" \
            else native_style_name
        QApplication.setStyle(QStyleFactory.create(name))
        # 换样式会重置配色，深色开关要跟着补一次
        if dark_box.isChecked():
            QApplication.setPalette(_dark_palette())
        # ⚠️ 换样式后菜单/列表的 palette 会回到新样式的默认（浅色）→ 下拉
        # 又变成白底。必须重新同步一次。
        _sync_all_popup_palettes()
        # 高度补偿是按样式分别记的，SpinBox 要跟着显示新样式的值
        sync_height_box()
        QTimer.singleShot(0, refresh_measure)

    def _sync_all_popup_palettes():
        # QSS 与样式无关（是类常量），只同步 palette 即可，不必重下 QSS
        for combo in custom_combos:
            combo.sync_popup_palette()

    def on_style_changed(_index):
        # defer：在 popup 打开期间 setStyle 会销毁重建可见 popup（历史教训）
        QTimer.singleShot(0, apply_style)

    def on_dark_changed(checked):
        QApplication.setPalette(_dark_palette() if checked
                                else QApplication.style().standardPalette())
        _sync_all_popup_palettes()

    def on_mode_changed(_index):
        # 菜单常驻不销毁，隐藏状态下改 flags 安全
        mode = mode_box.currentData()
        for combo in custom_combos:
            combo.set_menu_mode(mode)
        mode_hint.setText(_MODE_HINTS.get(mode, ""))

    def on_style_pad_changed(value):
        # 只影响 style 模式：改完重排弹窗几何，下次弹出即生效
        for combo in custom_combos:
            combo._style_pad_extra = value

    def _apply_animation():
        mode = anim_box.currentData()
        for combo in custom_combos:
            combo.set_popup_animation(mode, anim_ms_box.value())
        QTimer.singleShot(0, refresh_measure)

    def refresh_measure():
        """把「逻辑高度 × DPR」换算成屏幕物理像素，直接读出差值。

        用户手数的是物理像素（36 vs 33），但补偿常量 ``_HEIGHT_TWEAK_PX``
        是**逻辑**像素（会被 DPR 乘一遍）。不换算就会按 3 去补 2 的缺口。
        """
        dpr = QApplication.primaryScreen().devicePixelRatio()
        nh = native_combos[0].height() * dpr
        ch = custom_combos[0].height() * dpr
        measure.setText(
            "物理像素高度（DPR=%.2f）：原生 %.1fpx　原型 %.1fpx　"
            "差 %+.1fpx　→ 补偿值应为 %+.0f"
            % (dpr, nh, ch, ch - nh, (nh - ch) / dpr if dpr else 0))

    def sync_height_box():
        """切样式后把 SpinBox 刷成**该样式**的值。

        ⚠️ 补偿表是按样式分别记的（windows11 +2、Fusion +2），不跟着刷新的
        话用户切样式看到的还是上一个样式的值，会误判成「这个样式也矮」。
        """
        name = QApplication.style().objectName()
        height_box.blockSignals(True)
        height_box.setValue(_HEIGHT_TWEAK_PX.get(name, 0))
        height_box.blockSignals(False)

    def on_height_changed(value):
        # sizeHint 依赖模块级常量表，改完必须 updateGeometry 通知布局重排，
        # 否则窗口里的按钮高度不会变（布局缓存的是旧 sizeHint）。
        name = QApplication.style().objectName()
        if value:
            _HEIGHT_TWEAK_PX[name] = value
        else:
            _HEIGHT_TWEAK_PX.pop(name, None)   # 0 就别留条目，保持表干净
        for combo in custom_combos:
            combo.updateGeometry()
        # 布局是异步重排的，等一轮事件循环再量
        QTimer.singleShot(0, refresh_measure)

    sync_height_box()
    on_mode_changed(mode_box.currentIndex())   # 初始化一次模式与提示

    style_box.currentIndexChanged.connect(on_style_changed)
    dark_box.toggled.connect(on_dark_changed)
    mode_box.currentIndexChanged.connect(on_mode_changed)
    height_box.valueChanged.connect(on_height_changed)
    style_pad_box.valueChanged.connect(on_style_pad_changed)
    anim_box.currentIndexChanged.connect(lambda _i: _apply_animation())
    anim_ms_box.valueChanged.connect(lambda _v: _apply_animation())
    refresh_btn.clicked.connect(refresh_measure)
    # 首次要在布局真正跑完之后才量得到（构造期 height() 还是 0）
    QTimer.singleShot(0, refresh_measure)

    window.setWindowTitle("CustomComboBox 原型对比")
    window.resize(760, 460)
    return window, combos


def main():
    app = QApplication(sys.argv)
    window, _combos = build_window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


# =====================================================================
# NoFlickerPrototypeComboBox：主项目 NoFlickerComboBox 的可替换自绘实现
# ---------------------------------------------------------------------
# 继承 ``CustomComboBox`` 原型（已实测：不吞 release / DWM 圆角 + 系统阴影 /
# 120ms 淡入），并补齐主项目 ``NoFlickerComboBox`` 实际用到的 4 个 QComboBox
# 接口：setCurrentText / currentTextChanged / clear / setSizeAdjustPolicy。
# 主项目无 isinstance(..., QComboBox) 检查，故非 QComboBox 子类亦可 drop-in。
# =====================================================================
class NoFlickerPrototypeComboBox(CustomComboBox):
    # 补齐主项目用到的信号：文本变化时发（对齐 QComboBox.currentTextChanged）
    currentTextChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._size_adjust_policy = None
        # 原型默认是 off（无圆角、关系统阴影）；drop-in 要复刻「做好的样式」，
        # 必须在构造期（隐藏态）切到 dwm：frameless + 去 NoDropShadow（系统阴影）
        # + DWM 强制圆角。set_menu_mode 内部只在隐藏态落 flag，安全、不重建 HWND。
        self.set_menu_mode(_SHADOW_MODE_DWM)

    # ----------------------------------------------------- 补齐的 QComboBox API
    def setCurrentText(self, text):
        """按文本选中项；非 editable 下找不到则保持原样（对齐 QComboBox 行为）。"""
        idx = self.findText(text)
        if idx >= 0:
            self.setCurrentIndex(idx)
        # 找不到：非 editable 时 QComboBox 保持当前项，这里同样不动。

    def clear(self):
        """清空所有项并重置当前索引。"""
        self._model.clear()
        self._index = -1
        self.setText("")

    def setSizeAdjustPolicy(self, policy):
        """接受参数并存档（原 QComboBox 尺寸策略提示）。

        本控件宽度自算，策略仅存档不崩；保留接口以兼容调用点。
        """
        self._size_adjust_policy = policy

    def addItem(self, text, *args, **kwargs):
        """兼容主项目的 ``userData=`` 关键字调用。

        原生 ``QComboBox.addItem(text, userData=...)`` 用 ``userData`` 关键字传数据；
        而原型 ``CustomComboBox.addItem(text, data=None)`` 用 ``data``。主项目第
        1629 行正是 ``algo.addItem(i18n.t(lab), userData=key)``，若不加这层转发，
        用户打开「调整大小」动作行时会 ``TypeError``。位置参数（``addItem(t, v)``）
        与 ``data=`` 关键字均原样透传。
        """
        if "userData" in kwargs:
            if "data" in kwargs:
                raise TypeError("addItem() 不能同时给 userData 和 data")
            kwargs["data"] = kwargs.pop("userData")
        return super().addItem(text, *args, **kwargs)

    def _apply_fusion_style(self, *args, **kwargs):
        """主题/配色切换后重新同步 popup 调色板（原 ``NoFlickerComboBox`` 同名接口）。

        ⚠️ **绝不能留成 no-op**：主项目 ``_refresh_combo_styles()`` 在每次主题
        切换后对每个下拉调它。自绘 popup 是**独立顶层窗口**，Qt 喂它 Inactive
        调色板组；原生 Windows 样式下 ``Inactive.Accent`` 解析为黑色 → 下拉里
        「当前行左侧的小蓝条」（selection indicator）变黑。用户反馈的「从 Fusion
        切到原生类主题，小蓝条变黑」正是这里被抹掉的修复。
        走自绘路径时不分 Fusion/native，一律拉回应用 Active palette。
        """
        self.sync_popup_palette()

    # ------------------------------------------------ 让 currentTextChanged 随文本变化触发
    def setCurrentIndex(self, row):
        old_text = self.currentText()
        super().setCurrentIndex(row)
        new_text = self.currentText()
        if new_text != old_text:
            self.currentTextChanged.emit(new_text)

