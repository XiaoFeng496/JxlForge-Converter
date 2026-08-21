# -*- coding: utf-8 -*-
"""深度探测 Windows 电源模式真实存储位置 + 环境指纹。

必须在「跑 GUI 的那台 Win11 笔记本」上、用本地终端（不要用 WorkBuddy 内置终端，
那是云沙箱）运行，并把完整输出贴回。

用法：
    python tools/probe_power_mode.py
"""
import os
import sys

try:
    import ctypes
except ImportError:
    ctypes = None
try:
    import winreg
except ImportError:
    winreg = None

MODE_GUIDS = {
    "a1841308-3541-4fab-bc81-f71556f20b4a": "best_efficiency(最佳能效)",
    "ded574b5-45c0-4f42-8737-46345c09c238": "best_performance(最佳性能)",
    "34c7b99f-9a6d-4b3c-8dc7-b6693b78cef4": "balanced(平衡)",
}


def _line(s=""):
    print(s)


def _env_fingerprint():
    _line("=" * 70)
    _line("0) 环境指纹（用于确认你跑的是不是真机，而非云沙箱）")
    _line("=" * 70)
    _line("  sys.executable   : %s" % sys.executable)
    _line("  bitness 64?      : %s" % (sys.maxsize > 2 ** 32))
    _line("  sys.version      : %s" % sys.version.replace("\n", " "))
    try:
        v = sys.getwindowsversion()
        _line("  winver build     : %d.%d.%d" % (v.major, v.minor, v.build))
    except Exception as e:
        _line("  winver ERR: %r" % e)
    _line("  COMPUTERNAME     : %s" % os.environ.get("COMPUTERNAME", "<none>"))
    _line("  USERPROFILE      : %s" % os.environ.get("USERPROFILE", "<none>"))
    try:
        if ctypes is not None:
            wow = ctypes.windll.kernel32.GetCurrentProcess()
            # IsWow64Process
            fn = ctypes.windll.kernel32.IsWow64Process
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            fn.restype = ctypes.c_int
            out = ctypes.c_int(0)
            fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(out))
            _line("  running under WOW64 (32bit py on 64bit OS): %s" % bool(out.value))
    except Exception as e:
        _line("  WOW64 check ERR: %r" % e)


def _api_raw():
    _line("")
    _line("=" * 70)
    _line("1) PowerGetEffectivePowerMode —— 原始返回值（两种加载方式）")
    _line("=" * 70)
    if ctypes is None:
        _line("  ctypes 不可用")
        return
    # 方式 A: ctypes.windll
    try:
        fn = ctypes.windll.powrprof.PowerGetEffectivePowerMode
        fn.restype = ctypes.c_uint
        fn.argtypes = []
        _line("  [windll] raw = %r" % int(fn()))
    except Exception as e:
        _line("  [windll] EXCEPTION: %r" % e)
    # 方式 B: 显式 WinDLL 加载
    try:
        dll = ctypes.WinDLL("powrprof.dll")
        fn2 = dll.PowerGetEffectivePowerMode
        fn2.restype = ctypes.c_uint
        fn2.argtypes = []
        _line("  [WinDLL] raw = %r" % int(fn2()))
    except Exception as e:
        _line("  [WinDLL] EXCEPTION: %r" % e)


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


def _walk_search(base, depth, cap_state, found):
    """递归搜索整棵 Power 树，找以模式 GUID 命名的键，或值数据含模式 GUID。"""
    if depth > 7 or cap_state[0] >= 4000 or len(found) >= 6:
        return
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except Exception:
        return
    try:
        # 子键（可能本身就是模式 GUID 命名）
        sk_n = winreg.QueryInfoKey(k)[0]
        for i in range(sk_n):
            sub = winreg.EnumKey(k, i)
            cap_state[0] += 1
            if sub in MODE_GUIDS and sub not in found:
                found[sub] = base + "\\" + sub
            _walk_search(base + "\\" + sub, depth + 1, cap_state, found)
        # 命名值（数据可能含模式 GUID）
        val_n = winreg.QueryInfoKey(k)[1]
        for i in range(val_n):
            try:
                name, data, _typ = winreg.EnumValue(k, i)
            except Exception:
                continue
            s = str(data)
            for g in MODE_GUIDS:
                if g in s and g not in found:
                    found[g] = "%s  (value '%s' data=%r)" % (base, name, data[:80])
    except Exception:
        pass
    finally:
        try:
            winreg.CloseKey(k)
        except Exception:
            pass


def _registry_deep():
    _line("")
    _line("=" * 70)
    _line("2) 全树搜索模式 GUID（作为键名或值数据）")
    _line("=" * 70)
    if winreg is None:
        _line("  winreg 不可用")
        return
    found = {}
    cap = [0]
    _walk_search(
        r"SYSTEM\CurrentControlSet\Control\Power", 0, cap, found)
    _line("  扫描节点数: %d" % cap[0])
    if found:
        for g, loc in found.items():
            _line("  命中 %s (%s) @ %s" % (g, MODE_GUIDS[g], loc))
    else:
        _line("  未在整个 Power 树中找到任何模式 GUID（键名或值数据）")


def _powercfg_alias():
    _line("")
    _line("=" * 70)
    _line("3) powercfg /aliases 中是否出现模式 GUID")
    _line("=" * 70)
    try:
        import subprocess
        proc = subprocess.run(
            ["powercfg", "/aliases"], capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = proc.stdout.decode("utf-8", "ignore")
        hit = False
        for line in out.splitlines():
            for g in MODE_GUIDS:
                if g.lower() in line.lower():
                    _line("  %s" % line.strip())
                    hit = True
        if not hit:
            _line("  /aliases 输出中未出现这三个模式 GUID")
    except Exception as e:
        _line("  powercfg /aliases ERR: %r" % e)


def _powercfg_query_effective(scheme):
    _line("")
    _line("=" * 70)
    _line("4) powercfg /query 当前方案 -> 54533251 子组有效设置")
    _line("=" * 70)
    if not scheme:
        _line("  无 scheme，跳过")
        return
    try:
        import subprocess
        proc = subprocess.run(
            ["powercfg", "/query", scheme,
             "54533251-82be-4824-96c1-47b60b740d00"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = proc.stdout.decode("utf-8", "ignore")
        # 只打印含子项 GUID 或 index 的行，避免刷屏
        for line in out.splitlines():
            ls = line.strip()
            if ("Index" in ls or "GUID" in ls or "Power" in ls
                    or "State" in ls or "Boost" in ls or "当前" in ls
                    or "索引" in ls):
                _line("  %s" % ls)
    except Exception as e:
        _line("  powercfg /query ERR: %r" % e)


def main():
    _env_fingerprint()
    _api_raw()
    scheme = _active_scheme()
    _line("")
    _line("活动电源计划: %s" % scheme)
    _registry_deep()
    _powercfg_alias()
    _powercfg_query_effective(scheme)
    _line("")
    _line("DONE. 把以上完整输出贴回即可。")


if __name__ == "__main__":
    main()
