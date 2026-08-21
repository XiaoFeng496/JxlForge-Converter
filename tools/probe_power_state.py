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


def snapshot():
    return (
        power.get_active_power_scheme(),
        power.get_ac_status(),
        power.get_power_mode(),
    )


def main():
    watch = "--watch" in sys.argv
    try:
        while True:
            scheme, ac, mode = snapshot()
            line = "scheme=%-40s ac=%-8s mode=%-16s" % (scheme, ac, mode)
            if watch:
                print("[%s] %s" % (time.strftime("%H:%M:%S"), line))
                time.sleep(2)
            else:
                print(line)
                break
    except KeyboardInterrupt:
        if watch:
            print("\n(exited)")


if __name__ == "__main__":
    main()
