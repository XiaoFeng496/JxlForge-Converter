# -*- coding: utf-8 -*-
"""offscreen 冒烟：构造窗口 + API + 键盘转发 + 尺寸同步。

迁自原型仓（XiaoFeng496/JxlForge-Prototypes）的 ``test_custom_combo_menu.py``，
被测对象改指主项目真源 ``jxlforge.no_flicker_combo``。原型仓已降为实验/存档区，
**改 combo 逻辑请改主项目，并在这里跑回归**，不要再改原型仓那份。
"""
import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import (QColor, QImage, QKeyEvent, QMouseEvent, QPalette,
                           QPixmap, QWheelEvent)
from PySide6.QtWidgets import (QApplication, QComboBox, QGraphicsDropShadowEffect,
                               QStyle, QStyleFactory)

from jxlforge import no_flicker_combo as m

app = QApplication([])

fails = []


def check(cond, msg):
    if cond:
        print("PASS", msg)
    else:
        print("FAIL", msg)
        fails.append(msg)


class no_height_tweak:
    """临时清空真机高度补偿，用于「与原生严格相等」这类基线断言。

    真机 150% 缩放下实测 windows11 原生 36px、原型 33px（逻辑上差 2px），
    offscreen 量不出来，只能做成模块级补偿表。但**几何基线断言必须拿 0 补偿
    来比**，否则补偿一调基线就跟着漂，等于没有守卫。
    """

    def __enter__(self):
        self._old = dict(m._HEIGHT_TWEAK_PX)
        m._HEIGHT_TWEAK_PX.clear()
        return self

    def __exit__(self, *_exc):
        m._HEIGHT_TWEAK_PX.clear()
        m._HEIGHT_TWEAK_PX.update(self._old)
        return False


window, combos = m.build_window()
# ⚠️ 必须先 show + processEvents：否则拿到的是布局生效前的尺寸（offscreen 下
# 会给到 640 这种离谱值），所有几何断言都要在这之后做。
window.show()
app.processEvents()
check(len(combos) == 6, "探针注册 6 个下拉（3 原生 + 3 原型）")

custom = combos["原型·控件样式"]
check(isinstance(custom, m.CustomComboBox), "原型侧拿到的是 CustomComboBox")

# --- API 子集 ---
check(custom.count() == 3, "count()==3，实际 %d" % custom.count())
check(custom.currentText() == "原生", "currentText()=='原生'，实际 %r" % custom.currentText())
check(custom.findText("Fusion") == 2, "findText('Fusion')==2")
check(custom.itemText(1) == "原生（无闪烁）", "itemText(1) 正确")

seen = []
custom.currentIndexChanged.connect(seen.append)
custom.setCurrentIndex(2)
check(custom.currentIndex() == 2, "setCurrentIndex(2) 生效")
check(custom.currentText() == "Fusion", "按钮文字跟着变：%r" % custom.currentText())
check(seen == [2], "currentIndexChanged 只发一次：%r" % seen)

custom.setCurrentIndex(2)     # 同值不应再发
check(seen == [2], "同值 setCurrentIndex 不重复发信号")

# --- 选中行 → 提交并关闭菜单 ---
custom.setCurrentIndex(0)
custom._popup.show()
custom._list.clicked.emit(custom._model.index(2, 0))
app.processEvents()
check(custom.currentIndex() == 2, "点第 3 行后 currentIndex==2")
check(not custom.popup_open(), "选中后菜单已关闭")

# --- 键盘转发：菜单的 Up/Down 必须进到列表 ---
custom._popup.show()
app.processEvents()
before = custom._list.currentIndex().row()
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(custom._popup, ev)
app.processEvents()
after = custom._list.currentIndex().row()
check(after == before + 1 or before == custom.count() - 1,
      "菜单的 Key_Down 被转发给列表（%d → %d）" % (before, after))
custom._popup.close()

# --- 尺寸同步 ---
custom._sync_popup_size()
check(custom._list.height() > 0, "列表高度已同步：%d" % custom._list.height())
_w = custom._list.minimumWidth()
# ⚠️ offscreen 下默认样式是 fusion，native_combo_popup_padding() 返回 1，
# 列表宽 = 按钮宽 − 2*pad。本沙箱里按钮约 405 → 列表约 403，比按钮还窄，
# 完全满足「不要比按钮宽太多」的本意。用相对按钮宽度的区间代替写死的 400
# 上限，避免不同 offscreen 字体度量下 ±几 px 的误报。
check(150 <= _w <= custom.width() + 8,
      "列表宽度落在合理区间（不要比按钮宽太多）：%d，按钮宽 %d"
      % (_w, custom.width()))

# --- 打开时高亮当前项 ---
custom.setCurrentIndex(1)
custom._on_about_to_show()
app.processEvents()
check(custom._list.currentIndex().row() == 1,
      "弹出时高亮当前项：%d" % custom._list.currentIndex().row())

# ---------------------------------------------------------------------------
# 绘制：必须走 CC_ComboBox 路径，否则观感与原生不一致（用户反馈的硬伤）
# ---------------------------------------------------------------------------
native_widget = combos["原生·控件样式"]._combo
with no_height_tweak():
    h_custom = custom.sizeHint().height()
    h_native = native_widget.sizeHint().height()
check(h_custom == h_native,
      "sizeHint 高度与原生 QComboBox 一致（%d vs %d）" % (h_custom, h_native))

opt = custom._style_option()
check(opt.currentText == custom.currentText(),
      "QStyleOptionComboBox.currentText 跟随当前值：%r" % opt.currentText)
check(opt.frame is True, "opt.frame=True（否则样式不画外框）")
check(opt.editable is False, "opt.editable=False")
check(bool(opt.subControls & QStyle.SubControl.SC_ComboBoxArrow),
      "opt.subControls 含 SC_ComboBoxArrow（否则样式不画箭头）")
check(opt.iconSize == custom.iconSize(),
      "opt.iconSize 已填（默认 QSize(-1,-1) 会让部分样式算错尺寸）：%s"
      % opt.iconSize)

# ⚠️ 状态位必须严格照抄 QComboBox::initStyleOption()。原生**不会**因为拿到
# 焦点就置 State_Selected，也**不会**在展开时置 State_On；多置一个就会让
# Windows 11 样式画出另一套边框（用户反馈的「比原生矮一点点」）。
closed_opt = custom._style_option()
check(not (closed_opt.state & QStyle.StateFlag.State_On),
      "闭合态不带 State_On（原生只在箭头按下时用 State_Sunken）")
check(not (closed_opt.state & QStyle.StateFlag.State_Selected),
      "拿到焦点也不置 State_Selected（原生没有这一位）")

# 绘制本身不能抛异常（offscreen 下不做像素断言）
try:
    custom.render(QPixmap(custom.size()))
    check(True, "paintEvent（CC_ComboBox + CE_ComboBoxLabel）不抛异常")
except Exception as exc:                                  # noqa: BLE001
    check(False, "paintEvent 抛异常：%r" % (exc,))

# 菜单展开时按钮应处于按下态
custom._popup.show()
app.processEvents()
opened_opt = custom._style_option()
check(bool(opened_opt.state & QStyle.StateFlag.State_Sunken),
      "菜单展开时 opt.state 带 State_Sunken（与原生箭头按下态一致）")
check(bool(opened_opt.activeSubControls & QStyle.SubControl.SC_ComboBoxArrow),
      "菜单展开时高亮的是箭头子控件")
custom._popup.close()
app.processEvents()

# --- palette 同步：换样式/换配色后下拉不能变白底 ---
# ⚠️ QMenu 是顶层 popup，**不继承**主窗 palette → 它的 QListView 默认白底。
# QSS 里的 palette(base) 也是从控件自身 palette 取色，跟着一起白。
# 初始化时同步一次**不够**——setStyle/setPalette 之后必须再调一次，
# 否则切一次样式/配色就白回来（已加 sync_popup_palette()）。
from PySide6.QtGui import QColor, QPalette
custom._popup.setPalette(QPalette())  # 全默认（接近白）
custom._list.setPalette(QPalette())
custom.sync_popup_palette()
check(custom._popup.palette().color(QPalette.ColorRole.Base)
      == QApplication.palette().color(QPalette.ColorRole.Base),
      "sync_popup_palette 把 menu.base 同步到应用 base（Fusion+深色下不白底）")
check(custom._list.palette().color(QPalette.ColorRole.Base)
      == QApplication.palette().color(QPalette.ColorRole.Base),
      "sync_popup_palette 把 list.base 同步到应用 base（焦点/选中态不白底）")

# ---------------------------------------------------------------------------
# 打开下拉：点击按钮必须能展开（回归：曾因弃用 QMenu 而彻底点不开）
#
# ⚠️ ``QToolButton`` 的 ``InstantPopup`` 只在 ``d->menu`` **非空**时才调
# ``showMenu()``。换成 ``QWidget`` 弹窗后 ``menu()`` 恒为 ``None`` → 基类那条
# 路径根本不触发 → **点击按钮毫无反应**。必须自己接管 ``mousePressEvent``，
# 且展开时机在 **press**（原生非 editable QComboBox 的 mousePressEvent 就是
# 直接 showPopup()）。
# ---------------------------------------------------------------------------
custom._popup.close()
app.processEvents()

from PySide6.QtGui import QKeyEvent


def _click(target, pos=None):
    """向 ``target`` 投递一次左键按下 + 松开（pos 为控件局部坐标）。"""
    p = pos if pos is not None else QPointF(target.rect().center())
    for kind, buttons in (
            (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        _ev = QMouseEvent(kind, p, p, Qt.MouseButton.LeftButton, buttons,
                          Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, _ev)
        app.processEvents()


check(custom.menu() is None,
      "前置条件：没有 QMenu（弹窗是 QWidget，基类不会自动触发 showMenu）")
_click(custom)
check(custom.popup_open(),
      "点击按钮：下拉已展开（press 那一刻就展开，与原生 QComboBox 一致）")
custom._popup.close()
app.processEvents()

# 键盘展开键：原生 QComboBox 的 Space / Enter / F4 / Alt+↓
for _key, _name in ((Qt.Key.Key_F4, "F4"), (Qt.Key.Key_Space, "Space"),
                    (Qt.Key.Key_Return, "Enter")):
    QApplication.sendEvent(
        custom,
        QKeyEvent(QEvent.Type.KeyPress, _key, Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    check(custom.popup_open(),
          "按 %s：下拉已展开（对齐原生 QComboBox 的展开键）" % _name)
    custom._popup.close()
    app.processEvents()

# ---------------------------------------------------------------------------
# 入场动画：淡入 = windowOpacity 0→1 的 Qt 动画；下滑 = 改高度的 Qt 几何展开
#
# ⚠️ 两种模式都是**纯 Qt 动画**，不依赖 Win32 ``AnimateWindow``（后者要求
# ``WS_EX_LAYERED``、frameless 弹窗默认不带，补样式不生效会静默失败、落到下滑
# 兜底——正是「淡入看着像下滑」真因，见 probe 文件顶部说明）。因为不依赖真 HWND，
# offscreen 下也能驱动断言（旧版靠 spy 替换 ``_animate_window`` 只能验 flag）。
# ---------------------------------------------------------------------------
check(custom._anim_mode == m._ANIM_MODE_FADE
      and custom._anim_ms == m._DEFAULT_ANIM_MS,
      "默认入场动画 = 淡入 120ms（对齐 QMenu 观感）")

# —— 淡入：windowOpacity 0 → 1 ——
custom._popup.close()
app.processEvents()
custom.set_popup_animation(m._ANIM_MODE_FADE, 150)
custom.showMenu()
app.processEvents()
_fa = custom._fade_anim
check(custom.popup_open() and _fa is not None,
      "淡入：下拉已打开，且建了 windowOpacity 动画对象")
check(custom._popup.windowOpacity() < 0.5,
      "淡入：首帧 opacity<0.5（正在淡入，不是瞬间不透明也不是下滑）")
check(_fa.startValue() == 0.0 and _fa.endValue() == 1.0,
      "淡入：动画 0.0 → 1.0")
check(custom._slide_anim is None,
      "淡入：没有建下滑动画（两种模式必须区分开，旧 bug 是它俩混了）")
# 把动画推进到结尾，确认 opacity 真的拉到 1.0（真·淡入）
_fa.setCurrentTime(150)
app.processEvents()
check(abs(custom._popup.windowOpacity() - 1.0) < 1e-6,
      "淡入：播完后 opacity→1.0（真·淡入，不是下滑）")
custom._popup.close()
app.processEvents()

# —— 下滑：只改高度，不动 opacity ——
custom._popup.close()
app.processEvents()
custom.set_popup_animation(m._ANIM_MODE_SLIDE, 100)
custom.showMenu()
app.processEvents()
_sa = custom._slide_anim
check(custom.popup_open() and _sa is not None,
      "下滑：下拉已打开，且建了高度动画对象")
check(abs(custom._popup.windowOpacity() - 1.0) < 1e-6,
      "下滑：opacity 保持 1.0（不碰透明度，圆角/阴影照旧）")
check(custom._fade_anim is None,
      "下滑：没有建淡入动画")
check(_sa.startValue().height() < _sa.endValue().height(),
      "下滑：高度从 %d 增长到 %d（不是原地不动）"
      % (_sa.startValue().height(), _sa.endValue().height()))
check(custom._popup.maximumHeight() > _sa.startValue().height(),
      "下滑：动画期间高度未被 setFixedSize 钉死（否则一步不动）")
custom._popup.close()
app.processEvents()

# —— 关（off）：不播动画，但下拉照常打开，opacity 复位 ——
custom._popup.close()
app.processEvents()
custom.set_popup_animation(m._ANIM_MODE_OFF)
custom.showMenu()
app.processEvents()
check(custom.popup_open() and custom._fade_anim is None
      and custom._slide_anim is None,
      "关（off）：不播任何动画，但下拉照样打开")
check(abs(custom._popup.windowOpacity() - 1.0) < 1e-6,
      "关（off）：opacity 复位为 1.0（避免继承上一次淡入的透明残留）")
custom._popup.close()
app.processEvents()

# —— 时长 0：等同 off，下拉照常打开 ——
custom._popup.close()
app.processEvents()
custom.set_popup_animation(m._ANIM_MODE_FADE, 0)
custom.showMenu()
app.processEvents()
check(custom.popup_open() and custom._fade_anim is None,
      "时长 0：不播动画，但下拉照样打开")
custom._popup.close()
app.processEvents()

# —— 未知模式：抛 ValueError，不静默降级 ——
try:
    custom.set_popup_animation("nope")
    check(False, "未知动画模式：抛 ValueError（不静默降级）")
except ValueError:
    check(True, "未知动画模式：抛 ValueError（不静默降级）")
custom._popup.close()
app.processEvents()

# —— 真实调用（offscreen 无真 HWND）不能抛异常、下拉必须能打开 ——
try:
    custom._popup.close()
    app.processEvents()
    custom.set_popup_animation(m._ANIM_MODE_FADE, 120)
    custom.showMenu()
    app.processEvents()
    check(custom.popup_open(),
          "动画在 offscreen 下真实调用不抛异常、下拉照样能打开——动画不能挡路")
    custom._popup.close()
    app.processEvents()
except Exception as _exc:   # noqa: BLE001
    check(False, "offscreen 下入场动画抛异常：%r" % (_exc,))

# 样式边距默认 0（用户 2026-09-06 真机实测：0 与原生一致）
check(custom._style_pad_extra == 0,
      "样式边距默认 0（真机实测与原生一致；旧值 -2 是边框画在列表上那版的结论）")

# ---------------------------------------------------------------------------
# 选中语义：高亮跟随鼠标 + 按「松开位置」选中（与原生 QComboBox 一致）
#
# Qt 的做法（qcombobox.cpp QComboBoxPrivateContainer::eventFilter）：
#     MouseMove → view->setCurrentIndex(indexAt(pos))
#     Release   → 选中 view->currentIndex()
# QListView 默认只在「按下行 == 松开行」时发 clicked，拖动松开会毫无反应。
# ---------------------------------------------------------------------------
lv = custom._list


def _pt(row):
    """取第 ``row`` 行中心的**视口坐标**（``indexAt`` 用的就是视口坐标）。"""
    rect = lv.visualRect(lv.model().index(row, 0))
    return QPointF(rect.center().x(), rect.center().y())


def _mouse(kind, row, target=None):
    """向列表投递一个鼠标事件。

    ⚠️ 必须发给 ``viewport()`` 而不是 ``QListView`` 本体：`QAbstractScrollArea`
    的鼠标事件是由**视口**接收、再经事件过滤器转发给视图的 ``mouseXxxEvent``。
    直接 ``sendEvent(lv, ev)`` 会被静默丢弃（实测 log 为空）。
    """
    pos = _pt(row) if target is None else target
    ev = QMouseEvent(kind, pos, pos,
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(lv.viewport(), ev)
    app.processEvents()


custom.setCurrentIndex(0)
custom._popup.show()
custom._on_about_to_show()
app.processEvents()
check(custom._list.currentIndex().isValid(), "前置条件：列表已完成布局")

_mouse(QEvent.Type.MouseMove, 2)
check(lv.currentIndex().row() == 2,
      "鼠标划到第 3 行：高亮跟随（currentIndex=%d）"
      % lv.currentIndex().row())

_mouse(QEvent.Type.MouseButtonPress, 0)
_mouse(QEvent.Type.MouseButtonRelease, 2)
check(custom.currentIndex() == 2,
      "第 1 行按下、拖到第 3 行松开 → 选中松开位置（实际 %d）"
      % custom.currentIndex())
check(not custom.popup_open(), "拖动松手后菜单已关闭")

# 同行按下松开：仍只提交一次（基类发 clicked，不得再补一次）
custom.setCurrentIndex(0)
custom._popup.show()
app.processEvents()
seen.clear()
_mouse(QEvent.Type.MouseButtonPress, 1)
_mouse(QEvent.Type.MouseButtonRelease, 1)
check(custom.currentIndex() == 1, "同行按下松开正常选中第 2 行")
check(seen == [1], "同行按下松开只提交一次：%r" % seen)
custom._popup.close()
app.processEvents()

# 按下后拖到列表外松开：只关菜单，不改值（与原生一致）
custom.setCurrentIndex(0)
custom._popup.show()
app.processEvents()
_mouse(QEvent.Type.MouseButtonPress, 1)
_mouse(QEvent.Type.MouseButtonRelease, 0,
       target=QPointF(lv.width() / 2, lv.height() + 50))
check(custom.currentIndex() == 0,
      "按下后拖到列表外松开：不改值（实际 %d）" % custom.currentIndex())
custom._popup.close()
app.processEvents()

# ---------------------------------------------------------------------------
# 滚轮切换（QToolButton 默认不处理滚轮，必须自己实现）
# ---------------------------------------------------------------------------
def wheel(dy):
    """向控件投递一个纵向滚轮事件，返回是否被 accept。"""
    ev = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5),
        QPoint(0, 0), QPoint(0, dy),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    ev.setAccepted(False)
    QApplication.sendEvent(custom, ev)
    return ev.isAccepted()


custom.setCurrentIndex(0)
seen.clear()
check(wheel(-120), "向下滚：事件被 accept（不再冒泡给父控件）")
check(custom.currentIndex() == 1, "向下滚切到下一项，实际 %d"
      % custom.currentIndex())
check(seen == [1], "滚轮只发 currentIndexChanged，不发 activated：%r" % seen)

check(wheel(120), "向上滚：事件被 accept")
check(custom.currentIndex() == 0, "向上滚切回上一项")

custom.setCurrentIndex(0)
wheel(120)                       # 已在首项，再往上应停住不越界
check(custom.currentIndex() == 0, "首项继续上滚不越界")
custom.setCurrentIndex(custom.count() - 1)
wheel(-120)
check(custom.currentIndex() == custom.count() - 1, "末项继续下滚不越界")

# 菜单展开时不抢滚轮，交给里面的列表
custom._popup.show()
app.processEvents()
idx_before = custom.currentIndex()
check(not wheel(-120), "菜单展开时滚轮被 ignore（留给列表滚动）")
check(custom.currentIndex() == idx_before, "菜单展开时滚轮不切换值")
custom._popup.close()
app.processEvents()

# ---------------------------------------------------------------------------
# 闭合状态下的上下键
# ---------------------------------------------------------------------------
custom.setCurrentIndex(1)
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up,
               Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(custom, ev)
check(custom.currentIndex() == 0, "闭合状态按 ↑ 切上一项")
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down,
               Qt.KeyboardModifier.NoModifier)
QApplication.sendEvent(custom, ev)
check(custom.currentIndex() == 1, "闭合状态按 ↓ 切下一项")

# ---------------------------------------------------------------------------
# 菜单阴影开关：必须能设也能还原（单向设置会残留）
# ---------------------------------------------------------------------------
custom.set_menu_shadow(False)
flags_frameless = custom._popup.windowFlags()
qss_frameless = custom._popup.styleSheet()
check(bool(flags_frameless & Qt.WindowType.FramelessWindowHint),
      "关阴影：带 FramelessWindowHint")
check(bool(flags_frameless & Qt.WindowType.NoDropShadowWindowHint),
      "关阴影：带 NoDropShadowWindowHint")
check("border" in qss_frameless, "关阴影：QSS 自带边框补上")
check(custom._list.graphicsEffect() is None, "关阴影：列表上没有 shadow effect")
check(not custom._popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
      "关阴影：菜单不透明（不做离屏合成）")

custom.set_menu_shadow(True)
custom._on_about_to_show()   # 同步尺寸/布局留白（自绘阴影的房间在布局留白里）
flags_shadow = custom._popup.windowFlags()
# ⚠️ 开阴影**绝不能**去掉 FramelessWindowHint：那会让 Windows 给这个顶层
# popup 补一圈系统窗口边框（真机实测：原生样式下一圈黑边）。阴影必须自己画。
check(bool(flags_shadow & Qt.WindowType.FramelessWindowHint),
      "开阴影：仍然 Frameless（去掉会有黑边）")
check(bool(flags_shadow & Qt.WindowType.NoDropShadowWindowHint),
      "开阴影：仍然 NoDropShadow（去掉会引入 DWM 入场闪烁）")
check(custom._popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
      "开阴影：菜单透明（给阴影留作画区域）")
check(isinstance(custom._list.graphicsEffect(), QGraphicsDropShadowEffect),
      "开阴影：列表挂上 QGraphicsDropShadowEffect")
check(custom._popup.layout().contentsMargins().left() == m._SHADOW_MARGIN
      and custom._popup.layout().contentsMargins().top() == m._SHADOW_MARGIN,
      "开阴影：布局四边留 %dpx 给自绘阴影（顶部不留会被裁）" % m._SHADOW_MARGIN)

custom.set_menu_shadow(False)   # 再切回来，验证可重复
check(custom._popup.windowFlags() == flags_frameless,
      "切回关阴影：flags 完全还原（幂等，无残留）")
check(custom._popup.styleSheet() == qss_frameless, "切回关阴影：QSS 还原")
check(custom._list.graphicsEffect() is None,
      "切回关阴影：shadow effect 被摘掉（留着会一直走离屏合成）")
check(custom._popup.contentsMargins().left() == 0,
      "切回关阴影：菜单边距归零（幂等）")

# ---------------------------------------------------------------------------
# 五种菜单画法（off / effect / dwm / style / legacy）
#
# 差别全在 Frameless / NoDropShadow 两个 flag + 是否调 DWM 圆角 API：
#   off     frameless ✔  nodropshadow ✔   无阴影，QSS 自画 1px 边框（默认）
#   effect  frameless ✔  nodropshadow ✔   半透明 + 自绘投影（**丢 DWM 圆角**）
#   dwm     frameless ✔  nodropshadow ✗ + 非分层(不透明) + **DWM 圆角 API**
#          → 圆角 + DWM 柔和阴影 + **无黑边**（推荐 A）
#          ⚠️ 圆角只能靠 DWM API：QSS border-radius 在不透明窗口上三角区不透明
#             → 圆角失效 + 露黑角，且会连带丢阴影（2026-09-07 实测，已改回）
#   style   frameless ✔  nodropshadow ✗ + DWM corner API + QSS 自画 1px 边框
#          → 边框颜色可控，最接近原生观感（推荐 B）
#   legacy  frameless ✗  nodropshadow ✗
#          → 旧版「去 Frameless 换系统阴影」，**Win11 原生下 DWM 补黑边**
#
# ⚠️ 2026-09-06 真机回归结论：``style`` 模式曾因「去 Frameless 换系统阴影」
# 让 DWM 在 Win11 原生样式下补了一圈黑边。现在 ``style`` 已**保留 Frameless**
# + 调 DWM 圆角 API，等价于「类原生自画 + 无黑边」。旧实现降级为 ``legacy``
# 模式仅用于 A/B 对比，**不要当默认**。
# ---------------------------------------------------------------------------
def _mode_state(combo):
    """（frameless, nodropshadow, 半透明, 有 effect）四元组。"""
    f = combo._popup.windowFlags()
    return (bool(f & Qt.WindowType.FramelessWindowHint),
            bool(f & Qt.WindowType.NoDropShadowWindowHint),
            combo._popup.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground),
            combo._list.graphicsEffect() is not None)


custom.set_menu_mode(m._SHADOW_MODE_OFF)
st_off = _mode_state(custom)
qss_off = custom._popup.styleSheet()
check(st_off == (True, True, False, False),
      "off 模式：frameless + nodropshadow + 不透明 + 无 effect（实际 %r）"
      % (st_off,))

custom.set_menu_mode(m._SHADOW_MODE_EFFECT)
check(_mode_state(custom) == (True, True, True, True),
      "effect 模式：frameless + nodropshadow + 半透明 + 挂 effect")
check(isinstance(custom._list.graphicsEffect(), QGraphicsDropShadowEffect),
      "effect 模式：effect 类型是 QGraphicsDropShadowEffect")

custom.set_menu_mode(m._SHADOW_MODE_DWM)
# dwm 走「非分层(不透明) + 去 NoDropShadow + DWM 圆角 API」：frameless 防黑边、
# DWM 圆角给圆角（QSS border-radius 在不透明窗口上无效，见上）。状态四元组 =
# (frameless, 无 nodropshadow, 不透明, 无自绘 effect)。阴影是 DWM 的柔和阴影，
# 比原生 CS_DROPSHADOW 淡一点——已知且接受。
check(_mode_state(custom) == (True, False, False, False),
      "dwm 模式：**保留 frameless**（防黑边）+ 去 nodropshadow + 非分层"
      "（圆角与阴影由 DWM 给，不透明窗口不能用 QSS 圆角）")

custom.set_menu_mode(m._SHADOW_MODE_STYLE)
st_style = _mode_state(custom)
# ⚠️ 2026-09-06 修复版：style 模式已**保留 frameless**（防黑边），
# 圆角由 DWM API 强加；QSS 自画 1px 边框替代 DWM 系统边框（颜色可控）。
check(st_style == (True, False, False, False),
      "style 模式：保留 frameless + 去掉 nodropshadow（实际 %r）"
      % (st_style,))
check("1px" in custom._popup.styleSheet(),
      "style 模式：QSS 自画 1px 边框（替代 DWM 系统边框，颜色可控）")
check(custom._popup.contentsMargins().left() == 0,
      "style 模式：菜单不留阴影外扩（阴影由系统画在窗口外）")

custom.set_menu_mode(m._SHADOW_MODE_LEGACY)
st_legacy = _mode_state(custom)
# legacy = 旧版「去 Frameless 换系统阴影」，Win11 原生下 DWM 补一圈黑边
check(st_legacy == (False, False, False, False),
      "legacy 模式：去 Frameless 换系统阴影 —— Win11 原生下有黑边（实际 %r）"
      % (st_legacy,))
check("border" not in custom._popup.styleSheet().split("QListView")[0],
      "legacy 模式：菜单 QSS **不画**边框（交给样式画，否则叠成双层框）")

# ⚠️ 幂等：五种模式来回切，回到 off 后必须**完全**还原（单向设置会残留）
custom.set_menu_mode(m._SHADOW_MODE_LEGACY)
custom.set_menu_mode(m._SHADOW_MODE_STYLE)
custom.set_menu_mode(m._SHADOW_MODE_DWM)
custom.set_menu_mode(m._SHADOW_MODE_EFFECT)
custom.set_menu_mode(m._SHADOW_MODE_OFF)
check(_mode_state(custom) == st_off and custom._popup.styleSheet() == qss_off,
      "五种模式来回切：回到 off 后 flags/QSS 完全还原（幂等）")

# 布尔接口必须等价于 off/effect 两档（老代码/老测试靠它）
custom.set_menu_shadow(True)
check(custom._menu_mode == m._SHADOW_MODE_EFFECT,
      "set_menu_shadow(True) 等价于 effect 模式")
custom.set_menu_shadow(False)
check(custom._menu_mode == m._SHADOW_MODE_OFF,
      "set_menu_shadow(False) 等价于 off 模式")

# 未知模式必须立刻炸，不能静默降级成默认档
try:
    custom.set_menu_mode("nope")
except ValueError:
    check(True, "未知模式：抛 ValueError（不静默降级）")
else:
    check(False, "未知模式：应当抛 ValueError，实际没抛")

# ⚠️ dwm / style 都不留阴影外扩、也不补边框厚度 → 菜单尺寸必须与 off 一致
# （仅 effect 模式才外扩阴影边距并给列表加 1px 边框；off / dwm / style 不留外扩）
custom.set_menu_mode(m._SHADOW_MODE_OFF)
custom._popup.show()
custom._on_about_to_show()
app.processEvents()
size_off = custom._popup.size()
custom._popup.close()
# 隔离「样式边距微调」变量：_style_pad_extra 只影响 style 模式高度，
# 这里先归零，单独验「模式」本身不误加外扩；微调的回归见下方单独用例。
_style_pad_saved = custom._style_pad_extra
custom._style_pad_extra = 0
for _mode in (m._SHADOW_MODE_DWM, m._SHADOW_MODE_STYLE):
    custom.set_menu_mode(_mode)
    custom._popup.show()
    custom._on_about_to_show()
    app.processEvents()
    # dwm / style 都走「系统 CS_DROPSHADOW + 非分层（不透明）」，无自绘阴影外扩
    # → 尺寸与 off 一致（圆角由 QSS 实现，不撑大控件）。
    check(custom._popup.size() == size_off,
          "%s 模式：菜单尺寸与 off 一致（%s vs %s）—— 不一致说明误加了外扩"
          % (_mode, custom._popup.size(), size_off))
    _st_expect = (True, False, False, False)
    # ⚠️ 这是「圆角/阴影/黑边 时有时无」的核心回归：菜单**真正显示**（HWND
    # 创建/重建）之后，frameless + 无 NoDropShadow + 非分层 必须仍然成立。显示
    # 路径上 ``_on_about_to_show`` 重钉 flag + WinIdChange / Show 事件补 DWM
    # 属性，漏掉任何一处都会在真机上表现为「弹出后黑边/阴影/圆角偶尔丢失」。
    _st = _mode_state(custom)
    check(_st == _st_expect,
          "%s 模式：显示后 flag 仍 frameless + 无 NoDropShadow + 非分层"
          "（实际 %r）" % (_mode, _st))

# _apply_dwm_attributes 对所有模式都不得抛异常；dwm / style 会去调 DWM 圆角 API
# + immersive dark，off / legacy / effect 不调 —— 防止「非目标模式被误加强制圆角
# / 或补属性时抛异常」（offscreen 下 DWM 调用应被 OSError 静默吞掉）。
for _mode in m._SHADOW_MODES:
    custom.set_menu_mode(_mode)
    try:
        custom._apply_dwm_attributes()
    except Exception as _e:   # noqa: BLE001
        check(False, "%s 模式调 _apply_dwm_attributes 不应抛异常：%r"
              % (_mode, _e))
    else:
        check(True, "%s 模式：_apply_dwm_attributes 安全（无抛）" % _mode)
    custom._popup.close()
custom.set_menu_mode(m._SHADOW_MODE_OFF)
custom._style_pad_extra = _style_pad_saved   # 还原默认微调值

# ---------------------------------------------------------------------------
# 样式边距微调（_style_pad_extra）：默认 -2，只对 style 模式生效；off/dwm 不动
# ---------------------------------------------------------------------------
# ⚠️ 默认值 2026-09-06 改为 **0**：用户真机实测「现在默认样式边距 0 和原生一致」
# （旧值 -2 是「边框还画在列表上」那版的结论，边框改画到 popup 之后已不成立）。
check(custom._style_pad_extra == 0,
      "样式边距微调默认值 = 0（真机实测 0 与原生一致；-2 是旧画法的过期结论）")
custom.set_menu_mode(m._SHADOW_MODE_OFF)
custom._on_about_to_show()
h_off_p0 = custom._popup.height()
custom.set_menu_mode(m._SHADOW_MODE_STYLE)
custom._style_pad_extra = 0
custom._on_about_to_show()
h_style_p0 = custom._popup.height()
custom._style_pad_extra = -2
custom._on_about_to_show()
h_style_pn2 = custom._popup.height()
# style 模式的菜单高度必须随 _style_pad_extra 变化（每 -1，上下各减 1 → 总高 -2）。
# 具体差值取决于当前样式的原生 pad：pad0 = native，pad-2 = max(0, native-2)，
# 总高差 = 2*(pad0 - pad-2)。offscreen 默认 fusion（pad=1 → 差 2），Win11（pad=2 → 差 4）。
_native_pad = m.native_combo_popup_padding()
_expected_delta = 2 * (_native_pad - max(0, _native_pad - 2))
check(h_style_pn2 == h_style_p0 - _expected_delta,
      "style 模式：_style_pad_extra 0→-2 菜单高度应减 %d（实际 %d vs %d）"
      % (_expected_delta, h_style_pn2, h_style_p0))
# off 模式完全不受 _style_pad_extra 影响（隔离性）
custom._style_pad_extra = -2
custom.set_menu_mode(m._SHADOW_MODE_OFF)
custom._on_about_to_show()
check(custom._popup.height() == h_off_p0,
      "off 模式：_style_pad_extra 不影响其菜单高度（隔离性）")
custom._style_pad_extra = _style_pad_saved
custom.set_menu_mode(m._SHADOW_MODE_OFF)

# ---------------------------------------------------------------------------
# 阴影模式下**内容几何必须与无阴影一致**
#
# 边框画在列表上会吃掉 viewport 2px → 内容装不下 → 冒出垂直滚动条 → 宽度被
# 滚条吃掉 16px（实测 Fusion 下 viewport 198→182）。这里盯死：开了阴影之后
# viewport 尺寸和条目宽度**一格都不能变**。
# ---------------------------------------------------------------------------
custom.set_menu_shadow(False)
# ⚠️ 必须先 show 再同步：QMenu 的 action 布局只在可见时按最终尺寸摆放，
# 关着菜单调 _sync_popup_size() 拿到的是上一次的旧宽度（实测 viewport
# 286 → 272，正好是没算进阴影外扩那 14px）。
custom._popup.show()
custom._on_about_to_show()
app.processEvents()
_vp_plain = custom._list.viewport().size()
_item_plain = custom._list.visualRect(custom._list.model().index(0, 0)).width()
custom._popup.close()

custom.set_menu_shadow(True)
custom._popup.show()
custom._on_about_to_show()
app.processEvents()
_vp_shadow = custom._list.viewport().size()
_item_shadow = custom._list.visualRect(custom._list.model().index(0, 0)).width()
check(_vp_shadow == _vp_plain,
      "开阴影：viewport 尺寸不变（%s → %s）—— 变了说明被滚条吃宽度"
      % (_vp_plain, _vp_shadow))
check(_item_shadow == _item_plain and _item_shadow == _vp_shadow.width(),
      "开阴影：条目仍铺满整行（%d，无阴影时 %d）" % (_item_shadow, _item_plain))
check(custom._popup.width() == custom._list.width() + 2 * m._SHADOW_MARGIN,
      "开阴影：菜单比列表外扩出阴影区（菜单 %d，列表 %d）"
      % (custom._popup.width(), custom._list.width()))
custom._popup.close()

# ---------------------------------------------------------------------------
# 阴影模式 QSS：菜单透明 + 列表自画边框，**两种模式都不带 border-radius**
#
# 曾试过按样式查表自绘圆角（开 WA_TranslucentBackground 后 Win11 的 DWM 不再
# 给顶层窗口套圆角，开阴影会突然变直角），但 QSS 的 border-radius 只裁剪
# 背景/边框、**裁不到 viewport**，首末行高亮条在四角仍是方角，观感比直角更
# 糟 → 整块撤掉。这里守住「不许再手补圆角」，防止以后又绕回来。
# ---------------------------------------------------------------------------
custom.set_menu_shadow(True)
_qss_shadow = custom._popup.styleSheet()
check("border-radius" not in _qss_shadow,
      "开阴影：QSS 不带圆角（自绘圆角裁不到 viewport，比直角更糟）")
check("#CustomComboPopup { background: transparent" in _qss_shadow
      and "border: 1px solid" in _qss_shadow,
      "开阴影：菜单透明 + 列表自己画边框（阴影区不被填实色 = 无黑边）")
check(bool(custom._popup.windowFlags() & Qt.WindowType.FramelessWindowHint),
      "开阴影：菜单仍是 frameless（去掉它会被 DWM 补一圈系统黑边）")
custom.set_menu_shadow(False)
check("border-radius" not in custom._popup.styleSheet(),
      "关阴影：QSS 也不画圆角（交给系统，自己再画会双重描边）")

# ---------------------------------------------------------------------------
# 真机高度补偿：必须是「基线相等 + 补偿可加」两层
#
# offscreen 下原型与原生 sizeHint 都是 24，量不出真机那 2px；所以基线断言先
# 把补偿归零，再单独验证补偿真的加得上去。
# ---------------------------------------------------------------------------
# ⚠️ 补偿表按**样式的 objectName()** 取键。拼写错一个字符（大小写、拼错）
# 补偿就是静默失效——真机上照样矮 2px，而 offscreen 测试全绿，查都查不出来。
# 这里显式验证默认表里的每一项都能命中当前样式。
_orig_tweak = dict(m._HEIGHT_TWEAK_PX)
app.setStyle(QStyleFactory.create("windows11"))
_name11 = custom.style().objectName()
m._HEIGHT_TWEAK_PX.clear()
_base11 = custom.sizeHint().height()
m._HEIGHT_TWEAK_PX.update(_orig_tweak)
_with11 = custom.sizeHint().height()
check(_orig_tweak.get(_name11) == 2 and _with11 == _base11 + 2,
      "windows11：默认补偿 +2 且 objectName 能命中（objectName=%r，%d → %d）"
      % (_name11, _base11, _with11))
check(custom.minimumSizeHint() == custom.sizeHint(),
      "高度补偿：minimumSizeHint 跟随 sizeHint（否则布局会把按钮压回去）")
# ⚠️ 补偿必须**按样式**分开记：真机实测 windows11 和 Fusion 都比原生矮 2 个
# 逻辑像素，windowsvista 不矮。表里**只允许有这两个非零项**——谁以后手滑加
# 了 {"windowsvista": 2}，或把 "fusion" 拼成大写 "Fusion"（objectName 是全
# 小写），这里立刻红（拼错会静默失效，offscreen 永远测不出来）。
_nonzero_keys = sorted(k for k, v in _orig_tweak.items() if v)
check(_nonzero_keys == ["fusion", "windows11"],
      "高度补偿按样式区分：非零项只有 fusion/windows11（实际 %r）"
      % _nonzero_keys)
check(all(v == 2 for v in _orig_tweak.values()),
      "高度补偿：两项都是 +2 逻辑像素（实际 %r）" % _orig_tweak)
# Fusion 也要补，且 key 必须能命中 objectName（Fusion 的 objectName 是小写）
app.setStyle(QStyleFactory.create("Fusion"))
_name_f = custom.style().objectName()
m._HEIGHT_TWEAK_PX.clear()
_base_f = custom.sizeHint().height()
m._HEIGHT_TWEAK_PX.update(_orig_tweak)
check(_orig_tweak.get(_name_f) == 2
      and custom.sizeHint().height() == _base_f + 2,
      "Fusion：默认补偿 +2 且 objectName 能命中（objectName=%r，%d → %d）"
      % (_name_f, _base_f, custom.sizeHint().height()))
m._HEIGHT_TWEAK_PX.clear()
m._HEIGHT_TWEAK_PX.update(_orig_tweak)

# ---------------------------------------------------------------------------
# 绘制必须与原生 QComboBox 逐像素一致
#
# 这是「按钮比原生矮一点点」这类 1px 差异的唯一可靠守卫：sizeHint 相等只说明
# 尺寸算对了，绘制（边框内缩、文字基线、箭头位置）仍然可能差 1px，而肉眼在
# 真机上分辨不出来。这里直接比像素。
# ---------------------------------------------------------------------------
def _render(widget, size):
    widget.setFixedSize(size)
    widget.show()
    app.processEvents()
    app.processEvents()
    img = QImage(size, QImage.Format.Format_ARGB32)
    widget.render(img)
    return img


def _pixel_diff(style_name):
    app.setStyle(QStyleFactory.create(style_name))
    ref = QComboBox()
    ref.addItems(["原生（无闪烁）", "原生", "Fusion"])
    mine = m.CustomComboBox()
    mine.addItems(["原生（无闪烁）", "原生", "Fusion"])
    mine.setFont(ref.font())
    mine.setPalette(ref.palette())
    with no_height_tweak():
        # 画布按「补偿归零」的尺寸取，否则多出来的几行只有原型有像素
        size = QSize(max(ref.sizeHint().width(), mine.sizeHint().width()),
                     max(ref.sizeHint().height(), mine.sizeHint().height()))
        a = _render(ref, size)
        b = _render(mine, size)
    diffs = {}
    for y in range(size.height()):
        n = sum(1 for x in range(size.width())
                if a.pixelColor(x, y) != b.pixelColor(x, y))
        if n:
            diffs[y] = n
    return size, sum(diffs.values()), diffs


_orig_style = app.style().objectName()
for _style in ("windows11", "windowsvista", "Fusion"):
    if _style not in QStyleFactory.keys():
        continue
    _size, _total, _rows = _pixel_diff(_style)
    check(_total == 0,
          "%s：闭合态与原生 QComboBox 逐像素一致（%dx%d，差异 %d 像素%s）"
          % (_style, _size.width(), _size.height(), _total,
             "" if not _total else " 分布在行 %s" % sorted(_rows)))
app.setStyle(QStyleFactory.create(_orig_style))

# ---------------------------------------------------------------------------
# 几何必须与原生 QComboBox 严格相等（尺寸对了观感才对）
#
# 用户反馈过两类「差一点点」：按钮比原生矮、Fusion 下拉菜单差别很大。逐像素
# 比对只能测闭合态绘制，测不到弹窗尺寸，所以这里逐项量：
#   * 焦点策略（点一下拿不到焦点 → 画不出焦点框 → 观感「矮一点点」）
#   * 按钮 sizeHint
#   * 弹窗容器 / 内部列表尺寸
#   * 条目是否铺满整行（高亮条宽度）
# ---------------------------------------------------------------------------
def _native_popup_size(style_name):
    """量原生 QComboBox 弹出时「容器」与「内部 view」的尺寸。"""
    app.setStyle(QStyleFactory.create(style_name))
    ref = QComboBox()
    ref.addItems(["原生（无闪烁）", "原生", "Fusion"])
    ref.resize(200, ref.sizeHint().height())
    ref.show()
    app.processEvents()
    ref.showPopup()
    app.processEvents()
    out = (ref.view().window().size(), ref.view().size(),
           ref.view().visualRect(ref.view().model().index(0, 0)).width())
    ref.hidePopup()
    ref.hide()
    return out


def _my_popup_size(style_name):
    app.setStyle(QStyleFactory.create(style_name))
    mine = m.CustomComboBox()
    mine.addItems(["原生（无闪烁）", "原生", "Fusion"])
    mine.resize(200, mine.sizeHint().height())
    # ⚠️ 一律关阴影再比：阴影是可选视觉增强，主路径（无阴影）才必须与原生
    # 严格相等；阴影模式单独有「内容几何不变」的断言。
    mine.set_menu_shadow(False)
    mine._popup.show()
    mine._on_about_to_show()
    app.processEvents()
    lv = mine._list
    out = (mine._popup.size(), lv.size(),
           lv.visualRect(lv.model().index(0, 0)).width())
    mine._popup.close()
    return out


for _style in ("windows11", "windowsvista", "Fusion"):
    if _style not in QStyleFactory.keys():
        continue
    app.setStyle(QStyleFactory.create(_style))
    _ref = QComboBox()
    _ref.addItems(["原生（无闪烁）", "原生", "Fusion"])
    _mine = m.CustomComboBox()
    _mine.addItems(["原生（无闪烁）", "原生", "Fusion"])

    check(int(_mine.focusPolicy()) == int(_ref.focusPolicy()),
          "%s：focusPolicy 与原生一致（%s vs %s）—— 不一致会导致点击拿不到"
          "焦点、画不出焦点框，观感上「比原生矮一点点」"
          % (_style, int(_mine.focusPolicy()), int(_ref.focusPolicy())))

    # ⚠️ 补偿归零后再比：这是几何基线，不能被真机补偿常量带偏
    with no_height_tweak():
        check(_mine.sizeHint() == _ref.sizeHint(),
              "%s：sizeHint 与原生相等（%s vs %s）"
              % (_style, _mine.sizeHint(), _ref.sizeHint()))

    _n_cont, _n_view, _n_item = _native_popup_size(_style)
    _m_cont, _m_view, _m_item = _my_popup_size(_style)
    check(_m_cont == _n_cont,
          "%s：弹窗容器尺寸与原生相等（%s vs %s）"
          % (_style, _m_cont, _n_cont))
    check(_m_view == _n_view,
          "%s：内部列表尺寸与原生相等（%s vs %s）"
          % (_style, _m_view, _n_view))
    # ⚠️ 条目宽度**不能**拿原生当基准：offscreen 下原生 popup 的布局没跑完，
    # 它会报出比自己 view 还宽的条目（实测 Fusion 下 view 198、条目 265）。
    # 这里只对自己的不变量下断言：条目宽度 == 列表宽度，即高亮条铺满整行。
    check(_m_item == _m_view.width(),
          "%s：条目宽度铺满整行（列表 %d，条目 %d；原生测量值 %d 在 "
          "offscreen 下不可信）" % (_style, _m_view.width(), _m_item, _n_item))

app.setStyle(QStyleFactory.create(_orig_style))

# 列表不能带 QFrame：条目会左右各窄 1px，高亮条铺不满
check(custom._list.frameWidth() == 0,
      "列表 frameWidth 为 0（QSS 的 padding 也会撑出 frame，实测 1px）")
check(custom._list.viewport().width() == custom._list.width(),
      "视口宽度与列表宽度一致（updateGeometries 同步生效）")

# --- 原生侧适配器不报错 ---
native = combos["原生·控件样式"]
check(native.currentIndex() == 0, "原生适配器 currentIndex 可读")
check(native.row_at(native._combo.mapToGlobal(native._combo.rect().center()))
      is None or True, "原生适配器 row_at 可调用")

# --- immersive dark 必须「跟随明暗」，**不能写死 1** -----------------------
# ⚠️ 真机教训（2026-09-07）：原生下拉阴影**深色更浓、浅色更淡**。曾经无条件写 1，
# 结果浅色主题下我们的阴影比原生浓（用户反馈「我们对齐的是深色」）。
# 这里打桩 DWM 调用，逼它走 palette 回退，验证浅→0 / 深→1 真的会变。
if m._HAS_DWM:
    _written = []
    _orig_set = m._DWMAPI.DwmSetWindowAttribute
    _orig_get = m._DWMAPI.DwmGetWindowAttribute

    def _fake_set(hwnd, attr, pv, cb):
        if attr == m._DWMWA_USE_IMMERSIVE_DARK_MODE:
            _written.append(
                ctypes.cast(pv, ctypes.POINTER(ctypes.c_int)).contents.value)
        return 0

    def _fake_get(hwnd, attr, pv, cb):
        return 0x80070006     # 句柄无效 → 逼它走 palette 回退分支

    m._DWMAPI.DwmSetWindowAttribute = _fake_set
    m._DWMAPI.DwmGetWindowAttribute = _fake_get
    try:
        _orig_pal = QApplication.palette()
        custom.set_menu_mode(m._SHADOW_MODE_DWM)
        for _bg, _expect in ((QColor(250, 250, 250), 0),    # 浅色 → 0
                             (QColor(28, 28, 28), 1)):       # 深色 → 1
            _p = QPalette(_orig_pal)
            _p.setColor(QPalette.ColorRole.Window, _bg)
            QApplication.setPalette(_p)
            _written.clear()
            custom._popup.show()
            custom._on_about_to_show()
            app.processEvents()
            custom._apply_dwm_attributes()
            # ⚠️ 一次 show 会触发多轮属性下发（showEvent / WinIdChange 等），
            # 所以断言「**所有**写入值都等于期望」而不是「恰好写一次」。
            check(bool(_written) and all(v == _expect for v in _written),
                  "immersive dark 跟随明暗：窗口底色 %s → 全写入 %d（实际 %r）——"
                  " 写死 1 会让浅色主题下阴影比原生浓"
                  % (_bg.name(), _expect, _written))
            custom._popup.close()
        QApplication.setPalette(_orig_pal)
    finally:
        m._DWMAPI.DwmSetWindowAttribute = _orig_set
        m._DWMAPI.DwmGetWindowAttribute = _orig_get

print()
print("FAILURES=%d" % len(fails))
sys.exit(1 if fails else 0)
