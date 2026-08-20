# -*- coding: utf-8 -*-
"""Windows 电源计划与 CPU 指纹的轻量探测（供大图阈值按计划记忆）。

- get_active_power_scheme(): 返回当前活动电源方案 GUID（小写、无花括号）；
  非 Windows 或查询失败时返回 None。
- cpu_signature(): 返回可标识 CPU 型号的字符串（注册表 ProcessorNameString +
  逻辑核心数等）；用于检测硬件是否更换。
- register_power_notification(hwnd) / unregister_power_notification(handle):
  用主窗口自身的 HWND 注册 / 注销 GUID_POWERSCHEME_PERSONALITY 通知。活动电源
  方案「性格」变化（高性能 / 平衡 / 节能等任意方案切换）时，主窗口 nativeEvent
  会收到 WM_POWERBROADCAST。本模块只做 ctypes 封装，是否重校准由主窗口决定。
- is_power_setting_change(msg_ptr): 判断一条原生 MSG 是否为电源设置变更广播，
  供主窗口 nativeEvent 调用（也便于单元测试，无需真实窗口）。
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
# 所有方案都映射到三种性格之一，故切换任意方案都会触发；主窗口再用
# get_active_power_scheme() 取精确的活动方案 GUID 作为记忆键。
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
    def register_power_notification(hwnd):
        """用主窗口 HWND 注册电源方案性格变更通知。返回句柄（成功）或 None。"""
        if sys.platform != "win32":
            return None
        try:
            import uuid
            user32 = ctypes.windll.user32
            guid = ctypes.create_string_buffer(
                uuid.UUID(GUID_POWERSCHEME_PERSONALITY).bytes)
            handle = user32.RegisterPowerSettingNotification(
                ctypes.c_void_p(int(hwnd)), ctypes.byref(guid), 0)
            return handle
        except Exception:
            return None

    def unregister_power_notification(handle):
        """注销先前注册的通知句柄（关闭窗口时调用）。"""
        if sys.platform != "win32" or not handle:
            return
        try:
            user32 = ctypes.windll.user32
            user32.UnregisterPowerSettingNotification(
                ctypes.c_void_p(int(handle)))
        except Exception:
            pass

    def is_power_setting_change(msg_ptr):
        """msg_ptr 是原生 MSG 的指针（ctypes.c_void_p / Qt sip.voidptr）。是电源设置变更广播返回 True。"""
        if msg_ptr is None:
            return False
        try:
            msg = ctypes.cast(msg_ptr, ctypes.POINTER(wintypes.MSG))[0]
        except Exception:
            # 个别 ctypes 构建里 c_void_p 的 int() 异常，退回用地址再 cast。
            try:
                msg = ctypes.cast(int(msg_ptr), ctypes.POINTER(wintypes.MSG))[0]
            except Exception:
                return False
        return (msg.message == WM_POWERBROADCAST
                and msg.wParam == PBT_POWERSETTINGCHANGE)

else:
    # 非 Windows：提供空实现，避免导入报错；is_power_setting_change 永不触发。
    def register_power_notification(hwnd):
        return None

    def unregister_power_notification(handle):
        pass

    def is_power_setting_change(msg_ptr):
        return False
