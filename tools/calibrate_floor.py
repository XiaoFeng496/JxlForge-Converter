# -*- coding: utf-8 -*-
"""按本机 CPU 校准「大图像素地板」并写入 QSettings（命令行入口）。

用法（在仓库根目录运行）：
    python tools/calibrate_floor.py [--effort 7] [--runs 3] [--max-mp 64]

做什么：
    扫描 1–64MP（粗采样，先粗后细）的照片感合成图，对每档分别测量
      * T1   : cjxl 单线程（--num_threads=1）耗时
      * Tfull: cjxl 满核（--num_threads=核心数）耗时
    计算 speedup = T1 / Tfull，找到 speedup 首次 >= BIG_IMAGE_TARGET_SPEEDUP
    的分辨率档，把该档的像素数写入 QSettings 的 conversion/big_image_floor_px。

为何需要：双队列调度器用「相对中位数 + 绝对地板」两道闸门判定大图。绝对地板
依赖 CPU——核越多，满核 speedup 越大，地板可以更低、更激进；低核机器（≤4）则
直接禁用大图专属判定。校准一次即可把该临界点固化到本机，避免每次转换都跑基准。

可随时重跑以获得适配新硬件/新 effort 的更精确值（注：此处「值」指地板像素数）。

本脚本复用 libjxl_gui.calibrate 的核心逻辑，与应用内「一键校准」按钮同一真源。
"""
import os
import sys
import argparse

# 不创建任何 GUI，仅用 QtCore 的 QSettings 写持久化；offscreen 避免显示依赖。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libjxl_gui.calibrate import (  # noqa: E402
    BIG_IMAGE_TARGET_SPEEDUP,
    run_calibration,
    write_floor_px,
    write_cpu_signature,
)
from libjxl_gui.power import (  # noqa: E402
    cpu_signature,
    get_active_power_scheme,
)


def main():
    ap = argparse.ArgumentParser(description="校准大图像素地板")
    ap.add_argument("--effort", type=int, default=7)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-mp", type=int, default=64)
    args = ap.parse_args()

    floor_px = run_calibration(
        progress_cb=None,
        log_cb=print,
        effort=args.effort,
        runs=args.runs,
        max_mp=args.max_mp,
    )
    if floor_px is None:
        print("")
        print("校准未完成（未找到 cjxl 或测量失败）。")
        return 1

    # 按当前电源计划分别记忆阈值，并记下 CPU 指纹（与应用内按钮同一真源）。
    try:
        scheme = get_active_power_scheme()
        write_cpu_signature(cpu_signature())
    except Exception:
        scheme = None
    write_floor_px(floor_px, scheme=scheme)
    mp = floor_px / 1_000_000.0
    print("")
    print("已写入 QSettings（conversion 组）：big_image_floor_px = %d（约 %.1fMP）。"
          % (floor_px, mp))
    if scheme:
        print("已按当前电源计划（%s）记录该阈值，切换计划后将自动套用或需重新校准。"
              % scheme)
    print("下次转换将直接使用该校准值。目标加速比 = %.1f。"
          % BIG_IMAGE_TARGET_SPEEDUP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
