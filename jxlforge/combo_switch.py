# -*- coding: utf-8 -*-
r"""运行时可切换的 ``NoFlickerComboBox`` 代理 —— 解决「切主题后下拉实现不跟着换」。

背景（为什么需要代理）
----------------------
替换类只能在 ``MainWindow`` **构造之前**生效：控件一旦被 new 出来，它的类就固定了。
于是出现用户反馈的两个方向都错：

* 启动时是原生类主题 → 用了自绘原型；切到 Fusion 后**仍是自绘原型**（期望原生）。
* 启动时是 Fusion → 用了原生 QComboBox；切到原生类后**仍是原生**（期望自绘）。

而「切主题后重建控件」不可行：主项目在构建期就把信号连到了 combo 实例上，
重建会丢掉所有连接。

做法
----
代理对外仍是 ``NoFlickerComboBox``（``QWidget`` 子类，能被 ``findChildren`` 找到、
``isinstance`` 判断命中），内部**常驻两个实现**，按当前主题只显示其一：

* ``_native`` —— 主项目原有 ``NoFlickerComboBox``（**真 QComboBox**）
* ``_custom`` —— A 方案 drop-in（自绘 popup：圆角 / 系统阴影 / 120ms 淡入 / 不吞 release）

主题映射（用户定）：

======================  ==========================
主题                    下拉实现
======================  ==========================
``native_noflicker_proto``  **自绘 NoFlicker 原型**（默认主题的「原生（NoFlicker框）」）
``native_noflicker``        真原生 QComboBox（Fusion-popup，即「原生（Fusion框）」）
``native``                  真原生 QComboBox
``fusion``                  真原生 QComboBox
======================  ==========================

之所以只让「原生（NoFlicker框）」用自绘：另外几档若也用自绘，这几档在下拉观感上
就分不出来了。

切换只做「换内部控件 + 把 items 与 currentIndex 灌过去」，**不重建代理本身**，
所以主项目连在代理上的信号全程有效（代理自己转发信号）。

用法
----
    import combo_switch
    combo_switch.configure(
        native_cls=mw.NoFlickerComboBox,      # 替换前先存下来的原类
        custom_cls=A.NoFlickerComboBox,
        theme_getter=lambda: mw.app_theme(),
    )
    mw.NoFlickerComboBox = combo_switch.SwitchableComboBox
    # 并在 MainWindow._apply_theme 里（原实现**之前**）调
    # combo_switch.apply_mode_for_theme(theme)，让后续 _refresh_combo_styles
    # 作用到刚换上的新实现上。
"""

import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

# 所有活着的代理实例。
# ⚠️ **不能用 WeakSet**：PySide 的 Python wrapper 与 Qt 的 C++ 对象是两回事，
# C++ 对象由父 widget 持有，但 Python wrapper 没有别的强引用时会被 GC —— 于是
# WeakSet 静默清空，``apply_mode_for_theme`` 遍历到一个空集合，切换完全不生效
# （实测：10 个下拉一个都没切）。这里用强引用 list，并在每次遍历时剔除 C++
# 对象已被销毁的条目，避免长时间运行后无限累积。
_REGISTRY = []

_native_cls = None
_custom_cls = None
_theme_getter = None

#: 自绘实现的 popup 背景 palette 角色（``base`` / ``window``）。
#: 与主原型 ``probe_custom_combo_menu`` 读同一个环境变量，只影响自绘档
#: （原生档是 QComboBox 自己的画法，改不了也不需要改）。
#: 详见原型里 ``_POPUP_BG_ROLE_*`` 的注释 —— 这是真机 A/B 开关，默认 base。
_DEFAULT_BG_ROLE = (os.environ.get("COMBO_POPUP_BG_ROLE") or "base").strip().lower()
if _DEFAULT_BG_ROLE not in ("base", "window"):
    _DEFAULT_BG_ROLE = "base"


def configure(native_cls, custom_cls, theme_getter):
    """注入两个实现类与「当前主题」的读取函数。必须在替换类之前调用。"""
    global _native_cls, _custom_cls, _theme_getter
    _native_cls = native_cls
    _custom_cls = custom_cls
    _theme_getter = theme_getter


#: 唯一使用自绘 NoFlicker 原型的主题档。用户定：只有「原生（NoFlicker框）」
#: （native_noflicker_proto）这档用我们的自绘 popup（圆角 / 系统阴影 /
#: 120ms 淡入 / 不吞 release）；``native_noflicker``（原生 Fusion 框）/
#: ``native`` / ``fusion`` 都是**真原生 QComboBox（Fusion-popup）** —— 否则
#: 「原生（Fusion框）」和「原生（NoFlicker框）」两个选项在观感上就没区别了。
_CUSTOM_THEME = "native_noflicker_proto"


def use_native_for_theme(theme):
    """是否该用**原生 QComboBox**。除 ``native_noflicker_proto`` 外一律原生。"""
    return theme != _CUSTOM_THEME


def current_mode_is_native():
    """按当前主题判断该用哪个实现（构造新代理时用）。"""
    if _theme_getter is None:
        return False
    return use_native_for_theme(_theme_getter())


def _live_registry():
    """返回仍然有效的代理，顺手剔除 C++ 对象已被销毁的条目。"""
    try:
        from shiboken6 import isValid
    except Exception:  # noqa: BLE001
        return list(_REGISTRY)
    alive = [cb for cb in _REGISTRY if isValid(cb)]
    if len(alive) != len(_REGISTRY):
        _REGISTRY[:] = alive
    return alive


def apply_mode_for_theme(theme, root=None):
    """切主题后把所有下拉切到对应实现。幂等，可重复调用。

    ``root`` 给定时直接从该窗口 ``findChildren`` —— 最可靠、无泄漏，优先用这个；
    未给出时退回全局注册表。返回 ``(是否原生档, 切换到的下拉数量)``。
    """
    use_native = use_native_for_theme(theme)
    if root is not None:
        targets = list(root.findChildren(SwitchableComboBox))
    else:
        targets = _live_registry()
    for cb in targets:
        cb.set_mode(use_native)
    return use_native, len(targets)


def apply_popup_bg_role(role, root=None):
    """批量给所有代理的自绘实现设 popup 背景角色（``base`` / ``window``）。

    与 ``apply_mode_for_theme`` 同样的遍历策略：给了 ``root`` 就
    ``findChildren``（最可靠），否则退回全局注册表。返回刷新数量。
    """
    role = str(role).strip().lower()
    if role not in ("base", "window"):
        return 0
    targets = (list(root.findChildren(SwitchableComboBox)) if root is not None
               else _live_registry())
    for cb in targets:
        cb.set_popup_bg_role(role)
    return len(targets)


class SwitchableComboBox(QWidget):
    """对外保持 ``NoFlickerComboBox`` 身份、内部按主题切换实现的代理。"""

    # ⚠️ 信号必须定义在代理上：主项目在构建期连的是「代理」的信号。若让
    # __getattr__ 把 connect 引到内部控件上，一切换实现连接就断了。
    currentIndexChanged = Signal(int)
    currentTextChanged = Signal(str)
    activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []          # [(text, data)] —— 权威数据源，切换时用来重灌
        self._current = -1
        self._loading = False     # 重灌期间抑制信号，避免误触发主项目回调
        self._active = None

        if _native_cls is None or _custom_cls is None:
            raise RuntimeError("combo_switch.configure() 必须先调用")

        self._native = _native_cls()
        self._custom = _custom_cls()
        self._bg_role = _DEFAULT_BG_ROLE
        self._apply_bg_role(self._bg_role)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._lay = lay

        self.set_mode(current_mode_is_native())
        _REGISTRY.append(self)

    # --------------------------------------------------- popup 背景角色(A/B)
    def _apply_bg_role(self, role):
        """把背景角色下发给自绘实现（原生实现无此概念，跳过）。"""
        if self._custom is None:
            return
        setter = getattr(self._custom, "set_popup_bg_role", None)
        if setter is None:
            return
        try:
            setter(role)
        except Exception:  # noqa: BLE001 -- 旧版自绘实现没有这个方法，忽略即可
            pass

    def set_popup_bg_role(self, role):
        """切自绘档的 popup 背景 palette 角色：``base``（默认）/ ``window``。"""
        role = str(role).strip().lower()
        if role not in ("base", "window"):
            return
        self._bg_role = role
        self._apply_bg_role(role)

    def popup_bg_role(self):
        """当前自绘档用的背景角色（原生档返回 ``"base"`` 占位）。"""
        if self._custom is None:
            return "base"
        getter = getattr(self._custom, "popup_bg_role", None)
        return getter() if getter is not None else "base"

    # ------------------------------------------------------------ 模式切换
    def set_mode(self, use_native):
        """切到原生 / 自绘实现。已在目标模式则直接返回（幂等）。"""
        new = self._native if use_native else self._custom
        if new is self._active:
            return

        if self._active is not None:
            self._disconnect(self._active)
            self._lay.removeWidget(self._active)
            self._active.hide()

        self._active = new

        # 重灌 items + 恢复当前选中。整段抑制信号：clear()/addItem() 会让内部
        # 控件自己发出 currentIndexChanged，若不屏蔽，切一次主题就会把主项目
        # 的一堆回调全部触发一遍（可能引发「切主题就重置设置」之类的问题）。
        self._loading = True
        try:
            new.clear()
            for text, data in self._items:
                if data is None:
                    new.addItem(text)
                else:
                    new.addItem(text, data)
            if self._current >= 0:
                new.setCurrentIndex(self._current)
        finally:
            self._loading = False

        self._lay.addWidget(new)
        new.show()
        # Tab 焦点与键盘交互交给真正可见的那个内部控件。
        self.setFocusProxy(new)
        self._connect(new)
        self.updateGeometry()

    def _connect(self, w):
        w.currentIndexChanged.connect(self._on_index_changed)
        if hasattr(w, "activated"):
            w.activated.connect(self.activated)
        if hasattr(w, "currentTextChanged"):
            w.currentTextChanged.connect(self._on_text_changed)

    def _disconnect(self, w):
        for sig, slot in ((getattr(w, "currentIndexChanged", None), self._on_index_changed),
                          (getattr(w, "activated", None), self.activated),
                          (getattr(w, "currentTextChanged", None), self._on_text_changed)):
            if sig is None:
                continue
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _on_index_changed(self, index):
        if self._loading:
            return
        self._current = index
        self.currentIndexChanged.emit(index)

    def _on_text_changed(self, text):
        if self._loading:
            return
        self.currentTextChanged.emit(text)

    def is_native_mode(self):
        return self._active is self._native

    # ------------------------------------------------ QComboBox 接口转发
    def addItem(self, text, userData=None, **kwargs):
        """对齐 ``QComboBox.addItem(text, userData=None)``。

        主项目既用位置第二参（``addItem(t, v)``）也用 ``userData=`` 关键字
        （``addItem(t, userData=k)``，见 main_window 1629 行附近的 algo 下拉），
        两种都必须支持，否则 ``TypeError: ... unexpected keyword argument 'userData'``。
        自绘原型内部另支持 ``data=`` 关键字，这里也一并兼容。
        """
        data = userData
        if "data" in kwargs:
            if userData is not None:
                raise TypeError("addItem() 不能同时给 userData 和 data")
            data = kwargs["data"]
        self._items.append((text, data))
        if self._loading:
            return
        if data is None:
            self._active.addItem(text)
        else:
            self._active.addItem(text, data)
        # 首个 item 会让内部控件自动选中 0（QComboBox 行为），同步权威索引。
        if self._current < 0 and self._active.count() > 0:
            self._current = self._active.currentIndex()

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def count(self):
        return self._active.count()

    def itemText(self, index):
        return self._active.itemText(index)

    def itemData(self, index):
        return self._active.itemData(index)

    def currentIndex(self):
        return self._active.currentIndex()

    def currentText(self):
        return self._active.currentText()

    def currentData(self):
        return self._active.currentData()

    def setCurrentIndex(self, index):
        self._current = index
        self._active.setCurrentIndex(index)

    def setCurrentText(self, text):
        self._active.setCurrentText(text)
        self._current = self._active.currentIndex()

    def findText(self, text):
        return self._active.findText(text)

    def findData(self, data):
        return self._active.findData(data)

    def clear(self):
        self._items = []
        self._current = -1
        self._active.clear()

    def setSizeAdjustPolicy(self, policy):
        self._active.setSizeAdjustPolicy(policy)

    # ------------------------------------------------ 主项目主题刷新入口
    def _apply_fusion_style(self, *args, **kwargs):
        """主项目 ``_refresh_combo_styles()`` 对每个下拉调它 —— 转发给当前实现。

        ⚠️ 绝不能是 no-op：自绘 popup 的 palette 是快照，主题切换后必须重跑，
        否则「当前行小蓝条」会变黑。
        """
        fn = getattr(self._active, "_apply_fusion_style", None)
        if fn is not None:
            fn()

    # ------------------------------------------------ 尺寸（给 _set_combo_min_width）
    def sizeHint(self):
        return self._active.sizeHint()

    def minimumSizeHint(self):
        return self._active.minimumSizeHint()

    def setMinimumWidth(self, w):
        super().setMinimumWidth(w)
        self._active.setMinimumWidth(w)

    def setMaximumWidth(self, w):
        super().setMaximumWidth(w)
        self._active.setMaximumWidth(w)

    def updateGeometry(self):
        super().updateGeometry()
        self._active.updateGeometry()

    # ------------------------------------------------ 兜底：未列出的公开方法
    def __getattr__(self, name):
        """把没显式转发的**公开**方法交给当前实现。

        只转发非下划线开头的名字：私有一律不猜，避免在内部控件间串味，
        也避免 Qt/GC 在对象销毁期访问属性时产生诡异行为。
        """
        if name.startswith("_"):
            raise AttributeError(name)
        active = self.__dict__.get("_active")
        if active is not None:
            return getattr(active, name)
        raise AttributeError(name)
