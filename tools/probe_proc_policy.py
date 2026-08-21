# -*- coding: utf-8 -*-
"""轻量探针：读取当前「生效的处理器频率策略」（电源模式/插拔电落地的实质）。

请在 Win11 笔记本上、分别在各电源模式下各跑一次，把输出贴回对比：
    （设置 -> 系统 -> 电源和电池 -> 电源模式：最佳能效 / 平衡 / 最佳性能）

用法：
    python tools/probe_proc_policy.py
"""
import sys

try:
    import ctypes
except ImportError:
    ctypes = None
try:
    import winreg
except ImportError:
    winreg = None

# 我们关心的三个设置（处理器电源管理 54533251 组下）
_KNOWN = {
    "893dee8e-2bef-41e0-89c6-b55d0929964c": "最小处理器状态",
    "bc5038f7-23e0-4960-96c1-47b60b740d00": "最大处理器状态",
    "be337238-0d82-4146-a960-4f3749d470c7": "处理器性能提升模式",
}


def _ac_status():
    if ctypes is None:
        return "unknown"
    try:
        class _SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_byte),
                        ("BatteryFlag", ctypes.c_byte),
                        ("BatteryLifePercent", ctypes.c_byte),
                        ("Reserved1", ctypes.c_byte),
                        ("BatteryLifeTime", ctypes.c_ulong),
                        ("BatteryFullLifeTime", ctypes.c_ulong)]
        st = _SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return {0: "dc(电池)", 1: "ac(插电)"}.get(st.ACLineStatus, "unknown")
    except Exception:
        pass
    return "unknown"


def _active_scheme():
    if winreg is None:
        return None
    try:
        k = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes")
        v = winreg.QueryValueEx(k, "ActivePowerScheme")[0]
        winreg.CloseKey(k)
        return str(v).strip().lower()
    except Exception:
        return None


def main():
    scheme = _active_scheme()
    ac = _ac_status()
    print("活动电源计划 : %s" % scheme)
    print("供电状态     : %s" % ac)
    print("-" * 64)
    print("处理器频率策略 (54533251 组，当前方案下，全部子项):")
    if scheme and winreg is not None:
        base = (r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
                r"\%s\54533251-82be-4824-96c1-47b60b740d00" % scheme)
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except Exception as e:
            print("  打开 54533251 失败: %r" % e)
            k = None
        if k is not None:
            try:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    guid = winreg.EnumKey(k, i)
                    label = _KNOWN.get(guid, guid)
                    try:
                        sk = winreg.OpenKey(k, guid)
                        ac_i = dc_i = None
                        try:
                            ac_i = winreg.QueryValueEx(sk, "ACSettingIndex")[0]
                        except Exception:
                            pass
                        try:
                            dc_i = winreg.QueryValueEx(sk, "DCSettingIndex")[0]
                        except Exception:
                            pass
                        winreg.CloseKey(sk)
                        print("  %-22s %s" % (label, guid[:8]))
                        print("      AC=%s  DC=%s" % (ac_i, dc_i))
                    except Exception as e:
                        print("  %s 读取失败: %r" % (guid[:8], e))
            finally:
                winreg.CloseKey(k)
    else:
        print("  (无 scheme 或无 winreg)")
    print("-" * 64)
    print("把以上输出在『最佳能效/平衡/最佳性能』三种模式下各贴一次即可对比。")


if __name__ == "__main__":
    main()
