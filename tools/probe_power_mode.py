# -*- coding: utf-8 -*-
"""深度探测 Windows 电源模式（最佳能效/平衡/最佳性能）的真实存储位置。

本机（开发沙箱）无此机制，仅供在 Win11 真机上运行，把输出贴回，用于修正
power.get_power_mode() 的读取逻辑。

用法：
    python tools/probe_power_mode.py
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


def _line(s=""):
    print(s)


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
    except Exception as e:
        _line("  [ERR read active scheme] %r" % e)
    return None


def _setting_friendly_name(guid):
    """从 PowerSettings 定义里读子项 FriendlyName。"""
    if winreg is None:
        return None
    try:
        k = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Power\PowerSettings\%s" % guid)
        v = winreg.QueryValueEx(k, "FriendlyName")[0]
        winreg.CloseKey(k)
        return v
    except Exception:
        return None


def _read_indices(path):
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        ac = dc = None
        try:
            ac = winreg.QueryValueEx(k, "ACSettingIndex")[0]
        except Exception:
            pass
        try:
            dc = winreg.QueryValueEx(k, "DCSettingIndex")[0]
        except Exception:
            pass
        winreg.CloseKey(k)
        return ac, dc
    except Exception:
        return None, None


def main():
    _line("=" * 70)
    _line("1) PowerGetEffectivePowerMode —— 原始返回值（不映射）")
    _line("=" * 70)
    if ctypes is None:
        _line("  ctypes 不可用")
    else:
        try:
            fn = ctypes.windll.powrprof.PowerGetEffectivePowerMode
            fn.restype = ctypes.c_uint
            fn.argtypes = []
            v = fn()
            _line("  raw = %r  (%s)" % (v, int(v)))
        except Exception as e:
            _line("  EXCEPTION: %r" % e)
            _line("  (说明该符号在本机 powrprof.dll 未导出 -> 走注册表兜底)")

    scheme = _active_scheme()
    _line("")
    _line("=" * 70)
    _line("2) 当前活动电源计划: %s" % scheme)
    _line("=" * 70)

    if scheme is None or winreg is None:
        _line("  无法继续（无 scheme 或无 winreg）")
        return

    # --- 3. 54533251 组（处理器电源管理 / 可能的电源模式 overlay）---
    _line("")
    _line("=" * 70)
    _line("3) 54533251 组（活动方案下）每个子项 GUID + AC/DC 索引 + 名称")
    _line("=" * 70)
    base = (r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
            r"\%s\54533251-82be-4824-96c1-47b60b740d00" % scheme)
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        n = winreg.QueryInfoKey(k)[0]
        _line("  子项数: %d" % n)
        for i in range(n):
            guid = winreg.EnumKey(k, i)
            ac, dc = _read_indices(r"%s\%s" % (base, guid))
            name = _setting_friendly_name(guid)
            _line("  %s  AC=%s DC=%s  name=%s" % (guid, ac, dc, name))
        winreg.CloseKey(k)
    except Exception as e:
        _line("  OPEN FAILED: %r" % e)

    # --- 4. OverlaySchemes 键是否存在 ---
    _line("")
    _line("=" * 70)
    _line("4) OverlaySchemes 键（电源模式 overlay 候选位置）")
    _line("=" * 70)
    ov = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\OverlaySchemes"
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ov)
        n = winreg.QueryInfoKey(k)[0]
        _line("  存在，子项数: %d" % n)
        for i in range(n):
            guid = winreg.EnumKey(k, i)
            _line("  %s" % guid)
        winreg.CloseKey(k)
    except Exception as e:
        _line("  不存在或不可读: %r" % e)

    # --- 5. 标准电源模式 GUID 是否存在于 PowerSettings 定义 ---
    _line("")
    _line("=" * 70)
    _line("5) 标准电源模式 GUID 是否在 PowerSettings 定义中存在")
    _line("=" * 70)
    mode_guids = {
        "a1841308-3541-4fab-bc81-f71556f20b4a": "best_efficiency(最佳能效)",
        "ded574b5-45c0-4f42-8737-46345c09c238": "best_performance(最佳性能)",
        "34c7b99f-9a6d-4b3c-8dc7-b6693b78cef4": "balanced(平衡)",
    }
    for g, label in mode_guids.items():
        nm = _setting_friendly_name(g)
        _line("  %s -> %s  (注册表名称: %s)" % (g, label, nm))

    # --- 6. 在活动方案下递归搜这三个 GUID（深度 <=3）---
    _line("")
    _line("=" * 70)
    _line("6) 在活动方案下递归查找上述三个模式 GUID（深度<=4）")
    _line("=" * 70)
    found = {}

    def _walk(path, depth):
        if depth > 4 or len(found) >= 3:
            return
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except Exception:
            return
        try:
            sn = winreg.QueryInfoKey(k)[0]
            for i in range(sn):
                sub = winreg.EnumKey(k, i)
                full = r"%s\%s" % (path, sub)
                if sub in mode_guids and sub not in found:
                    ac, dc = _read_indices(full)
                    found[sub] = (full, ac, dc)
                _walk(full, depth + 1)
        finally:
            winreg.CloseKey(k)

    root = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\%s" % scheme
    _walk(root, 0)
    if found:
        for g, (p, ac, dc) in found.items():
            _line("  命中 %s @ %s  AC=%s DC=%s" % (g, p, ac, dc))
    else:
        _line("  未在任何子项下找到这三个模式 GUID")

    _line("")
    _line("DONE. 把以上输出贴回即可。")


if __name__ == "__main__":
    main()
