# -*- coding: utf-8 -*-
"""Windows 电源计划与 CPU 指纹的轻量探测（供大图阈值按计划记忆）。

- get_active_power_scheme(): 返回当前活动电源方案 GUID（小写、无花括号）；
  非 Windows 或查询失败时返回 None。
- cpu_signature(): 返回可标识 CPU 型号的字符串（注册表 ProcessorNameString +
  逻辑核心数等）；用于检测硬件是否更换。
- PowerPlanMonitor: 隐藏的顶层 QWidget，注册 WM_POWERBROADCAST /
  GUID_POWERSCHEME_PERSONALITY 通知；活动电源方案「性格」变化（高性能 / 平衡 /
  节能等任意方案的切换）时发出 plan_changed 信号（携带当前活动方案 GUID）。
  本模块只负责「侦测变化」，不自动做任何校准——是否重校准由主窗口决定。
"""
import os
import re
import subprocess
import sys

try:
    import winreg
except ImportError:
    winreg = None

try:
    import ctypes
    from ctypes import wintypes
except ImportError:
    ctypes = None

# 注册此 GUID 后，活动电源方案「性格」变化会收到 PBT_POWERSETTINGCHANGE。
# 所有方案都映射到三种性格之一，故切换任意方案都会触发；我们用
# get_active_power_scheme() 再取精确的活动方案 GUID 作为记忆键。
GUID_POWERSCHEME_PERSONALITY = "245D8541-3943-4422-B025-13A784F679B7"

WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013


def get_active_power_scheme():
    """返回当前活动电源方案 GUID（小写、无花括号）；失败返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        proc = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, timeout=5,
        )
        # 中文 Windows 下 powercfg 输出为 GBK 编码，用 errors 容错避免解码崩溃；
        # 我们只提取 ASCII 的 GUID，忽略其余字节即可。
        out = proc.stdout.decode("utf-8", "ignore")
        m = re.search(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", out)
        if m:
            return m.group(0).lower()
    except Exception:
        pass
    return None


def cpu_signature():
    """返回可标识 CPU 型号的字符串；非 Windows 降级为 machine + 核心数。"""
    parts = []
    if winreg is not None:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            winreg.CloseKey(key)
            if name:
                parts.append(str(name).strip())
        except Exception:
            pass
    parts.append(getattr(sys, "platform", ""))
    parts.append(str(os.cpu_count() or 1))
    return "|".join(p for p in parts if p)


if ctypes is not None:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtWidgets import QWidget

    class PowerPlanMonitor(QWidget):
        """隐藏顶层窗口，监听电源方案切换并广播 plan_changed 信号。"""

        plan_changed = Signal(str)  # 当前活动方案 GUID（可能为空串）

        def __init__(self, parent=None):
            super().__init__(parent)
            self._handle = None
            # 无父对象的 QWidget 在 Win32 层即为顶层窗口（能收到 WM_POWERBROADCAST），
            # 但刻意不设为 Qt.Window、且永不 show()，故不会出现在任务栏或抢焦点。
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self._register()

        def _register(self):
            if sys.platform != "win32":
                return
            try:
                import uuid
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                guid = ctypes.create_string_buffer(
                    uuid.UUID(GUID_POWERSCHEME_PERSONALITY).bytes)
                self._handle = user32.RegisterPowerSettingNotification(
                    ctypes.c_void_p(hwnd), ctypes.byref(guid), 0)
            except Exception:
                self._handle = None

        def nativeEvent(self, eventType, message):
            if eventType == b"windows_generic_MSG":
                try:
                    msg = ctypes.cast(
                        int(message), ctypes.POINTER(wintypes.MSG))[0]
                    if (msg.message == WM_POWERBROADCAST
                            and msg.wParam == PBT_POWERSETTINGCHANGE):
                        self.plan_changed.emit(get_active_power_scheme() or "")
                except Exception:
                    pass
            return super().nativeEvent(eventType, message)

else:
    # 非 Windows：提供空实现，避免导入报错；plan_changed 永不触发。
    class PowerPlanMonitor:
        def __init__(self, parent=None):
            self.plan_changed = _NoopSignal()

        def nativeEvent(self, eventType, message):
            return (False, 0)


    class _NoopSignal:
        def connect(self, *args, **kwargs):
            return None
