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
