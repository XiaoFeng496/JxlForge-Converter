# -*- coding: utf-8 -*-
r"""Windows 电源计划与 CPU 指纹的轻量探测（供大图阈值按计划记忆）。

- get_active_power_scheme(): 返回当前活动电源方案 GUID（小写、无花括号）。
  优先读注册表 HKLM\...\Power\User\PowerSchemes\ActivePowerScheme（零进程、无闪窗、
  可安全轮询，且值随方案切换由系统同步更新）；读不到时回退到 powercfg（带
  CREATE_NO_WINDOW，避免开 GUI 时闪黑框）。非 Windows 返回 None。
- cpu_signature(): 返回可标识 CPU 型号的字符串（注册表 ProcessorNameString +
  逻辑核心数等）；用于检测硬件是否更换。
"""
import os
import re
import subprocess
import sys

try:
    import ctypes
except ImportError:
    ctypes = None

try:
    import winreg
except ImportError:
    winreg = None

# 活动电源方案在注册表中的规范位置（切换方案时由系统同步更新，值即方案 GUID）。
_POWER_SCHEMES_KEY = (
    winreg.HKEY_LOCAL_MACHINE,
    r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes",
) if winreg is not None else None
_ACTIVE_SCHEME_VALUE = "ActivePowerScheme"


def _read_active_scheme_registry():
    """从注册表读取当前活动电源方案 GUID（小写）或 None。"""
    if winreg is None:
        return None
    try:
        hk, path = _POWER_SCHEMES_KEY
        key = winreg.OpenKey(hk, path)
        try:
            val, _typ = winreg.QueryValueEx(key, _ACTIVE_SCHEME_VALUE)
        finally:
            winreg.CloseKey(key)
        if val:
            return str(val).strip().lower()
    except Exception:
        pass
    return None


def get_active_power_scheme():
    """返回当前活动电源方案 GUID（小写、无花括号）；失败返回 None。"""
    if sys.platform != "win32":
        return None
    # 首选注册表：零进程、可轮询、不会闪出控制台窗口。
    reg = _read_active_scheme_registry()
    if reg:
        return reg
    # 回退：powercfg（隐藏控制台窗口，避免开 GUI 时闪黑框）。
    try:
        proc = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
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


# ---------------------------------------------------------------------------
# 插拔电状态（AC / DC）与电源模式（最佳能效 / 平衡 / 最佳性能）
# 这两个信号独立影响 CPU 频率策略（从而改变大图并行的 speedup 曲线），
# 与「电源计划」共同构成完整的「电源状态」三元组。
# ---------------------------------------------------------------------------

def get_ac_status():
    """返回当前供电状态：``'ac'``（接通电源）/ ``'dc'``（电池）/ ``'unknown'``。

    基于 Win32 ``GetSystemPowerStatus`` 的 ``ACLineStatus`` 字段（零进程、无闪窗，
    可安全轮询）。非 Windows 返回 ``'unknown'``。
    """
    if sys.platform != "win32" or ctypes is None:
        return "unknown"
    try:
        class _SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]
        st = _SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            if st.ACLineStatus == 0:
                return "dc"
            if st.ACLineStatus == 1:
                return "ac"
    except Exception:
        pass
    return "unknown"


# Win11 电源模式（overlay scheme）注册表位置：
#   HKLM\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes
#     ActiveOverlayAcPowerScheme  (插电时当前模式 GUID)
#     ActiveOverlayDcPowerScheme  (电池时当前模式 GUID)
# 与 ActivePowerScheme（电源计划）独立：切换「最佳能效/平衡/最佳性能」只改 overlay，
# 不改计划 GUID。由系统或第三方切换工具（如 G-Helper）改写，可靠、零进程、可轮询。
# 实测映射（G-Helper 日志 + 用户三模式验证）：
_OVERLAY_MODE_GUIDS = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "best_efficiency",  # 最佳能效
    "00000000-0000-0000-0000-000000000000": "balanced",         # 无 overlay，跟随计划/平衡
    "ded574b5-45a0-4f42-8737-46345c09c238": "best_performance",  # 最佳性能
}


def get_power_mode():
    """返回当前电源模式规范名：``best_efficiency`` / ``balanced`` /
    ``best_performance`` / ``unknown``。

    读取注册表 ``ActiveOverlayAcPowerScheme``（插电时）/
    ``ActiveOverlayDcPowerScheme``（电池时）当前 overlay GUID 并映射。该键由系统
    或电源模式切换工具（G-Helper 等）在切换「最佳能效/平衡/最佳性能」时改写，是
    可靠的、零进程、可轮询的信号。

    注：Win32 ``PowerGetEffectivePowerMode`` 在本机 powrprof.dll 未导出，故不使用。
    非 Windows 或读不到时返回 ``'unknown'``（调用方据此不误触发）。
    """
    if sys.platform != "win32" or winreg is None:
        return "unknown"
    ac = (get_ac_status() == "ac")
    value_name = "ActiveOverlayAcPowerScheme" if ac else "ActiveOverlayDcPowerScheme"
    try:
        hk, path = _POWER_SCHEMES_KEY
        key = winreg.OpenKey(hk, path)
        try:
            guid, _typ = winreg.QueryValueEx(key, value_name)
        finally:
            winreg.CloseKey(key)
        guid = str(guid).strip().lower()
        return _OVERLAY_MODE_GUIDS.get(guid, "unknown")
    except Exception:
        return "unknown"
