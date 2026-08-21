# -*- coding: utf-8 -*-
"""只读诊断：打印当前「电源状态三元组」（电源计划 / 插拔电 / 电源模式）。

在 Windows 11 笔记本上运行，验证 libjxl_gui.power 能否正确读取这三维，
并观察切换「电源模式」（最佳能效 / 平衡 / 最佳性能）、插拔电源时数值是否
随之变化——这是正式接入校准阈值记忆前的"读数正确性"验收。

用法：
  python tools/probe_power_state.py            # 单次快照
  python tools/probe_power_state.py --watch    # 每 2 秒刷新（Ctrl+C 退出）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import libjxl_gui.power as power  # noqa: E402

try:
    import winreg
except ImportError:
    winreg = None


def _raw_overlay_guids():
    if winreg is None:
        return "n/a", "n/a"
    base = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
    out = {}
    for name in ("ActiveOverlayAcPowerScheme", "ActiveOverlayDcPowerScheme"):
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
            try:
                out[name] = winreg.QueryValueEx(k, name)[0]
            finally:
                winreg.CloseKey(k)
        except Exception:
            out[name] = "ERR"
    return out.get("ActiveOverlayAcPowerScheme", "?"), out.get(
        "ActiveOverlayDcPowerScheme", "?")


def snapshot():
    scheme = power.get_active_power_scheme()
    ac = power.get_ac_status()
    mode = power.get_power_mode()
    ac_guid, dc_guid = _raw_overlay_guids()
    # 当前生效的 overlay：插电看 AC，电池看 DC
    effective = ac_guid if ac == "ac" else dc_guid
    return scheme, ac, mode, ac_guid, dc_guid, effective


def main():
    watch = "--watch" in sys.argv
    try:
        while True:
            scheme, ac, mode, ac_guid, dc_guid, effective = snapshot()
            print("[%s]"
                  % (time.strftime("%H:%M:%S") if watch else ""))
            print("  电源计划 scheme : %s" % scheme)
            print("  供电状态 ac     : %s" % ac)
            print("  电源模式 mode   : %s" % mode)
            print("  overlay AC GUID : %s" % ac_guid)
            print("  overlay DC GUID : %s" % dc_guid)
            print("  -> 生效 overlay : %s" % effective)
            if watch:
                print("-" * 60)
                time.sleep(2)
            else:
                break
    except KeyboardInterrupt:
        if watch:
            print("\n(exited)")


if __name__ == "__main__":
    main()
