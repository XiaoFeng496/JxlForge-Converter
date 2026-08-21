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


# EffectivePowerMode 枚举 -> 规范名（Windows 11 设置里的「电源模式」三选项）。
_POWER_MODE_NAMES = {
    0: "unknown",
    1: "battery_saver",     # 电池省电模式（Win11 独立开关，偏最节能）
    2: "best_efficiency",   # 最佳能效
    3: "balanced",          # 平衡
    4: "best_performance",  # 最佳性能（部分硬件也用 5）
    5: "best_performance",
}
# Win11 标准电源模式 overlay 子项 GUID -> 规范名（注册表兜底用）。
_POWER_MODE_GUIDS = {
    "a1841308-3541-4fab-bc81-f71556f20b4a": "best_efficiency",
    "ded574b5-45c0-4f42-8737-46345c09c238": "best_performance",
}


def get_power_mode():
    """返回当前电源模式规范名：``best_efficiency`` / ``balanced`` /
    ``best_performance`` / ``battery_saver`` / ``unknown``。

    优先用 Win32 ``PowerGetEffectivePowerMode``（Windows 10 1809+ 导出，直接给出
    生效的电源模式）；不可用时回退到注册表 overlay 兜底（遍历活动方案下
    ``54533251-...`` 组里各模式子项的选中索引）。非 Windows 或本机无该机制时
    返回 ``'unknown'``（调用方据此不误触发）。
    """
    if sys.platform != "win32" or ctypes is None:
        return "unknown"
    # 1) Win32 API（最可靠，Windows 11 桌面版应可用）。
    try:
        fn = ctypes.windll.powrprof.PowerGetEffectivePowerMode
        fn.restype = ctypes.c_uint
        fn.argtypes = []
        name = _POWER_MODE_NAMES.get(int(fn()), "unknown")
        if name != "unknown":
            return name
    except Exception:
        pass
    # 2) 注册表 overlay 兜底：遍历活动方案下 54533251 组，找选中(index=1)的模式。
    if winreg is not None:
        try:
            scheme = get_active_power_scheme()
            if scheme:
                ac = (get_ac_status() == "ac")
                base = (r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
                        r"\%s\54533251-82be-4824-96c1-47b60b740d00" % scheme)
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
                n = winreg.QueryInfoKey(key)[0]
                for i in range(n):
                    guid = winreg.EnumKey(key, i).lower()
                    mapped = _POWER_MODE_GUIDS.get(guid)
                    if mapped is None:
                        continue
                    idx_name = "ACSettingIndex" if ac else "DCSettingIndex"
                    idx = winreg.QueryValueEx(
                        winreg.OpenKey(key, guid), idx_name)[0]
                    if idx == 1:
                        return mapped
        except Exception:
            pass
    return "unknown"
