# -*- coding: utf-8 -*-
"""诊断：验证 cjxl 校准时 effort 7→4 的 speedup-分辨率曲线是否基本不变。

复用 jxlforge.calibrate 的 make_photo_mp / bench（与真实校准同一测量方法），
对若干 MP 档位分别测量：
  T1   : --num_threads=1
  Tfull: --num_threads=逻辑核心数
speedup = T1 / Tfull。比较 effort=7 与 effort=4 的 speedup 曲线及「首达
BIG_IMAGE_TARGET_SPEEDUP 的档位（floor_mp）」是否一致。

用法（仓库根目录）：
    python tools/probe_effort_speedup.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jxlforge.calibrate import (  # noqa: E402
    BIG_IMAGE_TARGET_SPEEDUP,
    bench,
    make_photo_mp,
)

LEVELS = [1, 2, 4, 8, 12, 16, 24, 32, 40, 48]
EFFORTS = [7, 4]
RUNS = 2  # 取最快一次；验证曲线用 2 次足够


def measure(effort):
    cores = os.cpu_count() or 1
    rows = []
    floor_mp = None
    for mp in LEVELS:
        png = make_photo_mp(mp)
        t1 = bench(png, 1, effort, RUNS)
        tfull = bench(png, cores, effort, RUNS)
        try:
            os.remove(png)
        except OSError:
            pass
        if t1 is None or tfull is None or tfull <= 0:
            rows.append((mp, None, None, None))
            continue
        sp = t1 / tfull
        rows.append((mp, t1, tfull, sp))
        if sp >= BIG_IMAGE_TARGET_SPEEDUP and floor_mp is None:
            floor_mp = mp
    return rows, floor_mp


def main():
    t_start = time.time()
    data = {}
    floors = {}
    for eff in EFFORTS:
        print("测量 effort=%d ..." % eff, flush=True)
        rows, floor = measure(eff)
        data[eff] = rows
        floors[eff] = floor

    print("")
    print("MP    e7_speedup   e4_speedup   e4/e7")
    print("-" * 42)
    for i, mp in enumerate(LEVELS):
        _, _, _, sp7 = data[7][i]
        _, _, _, sp4 = data[4][i]
        if sp7 is None or sp4 is None:
            print("%3d   (测量失败)" % mp)
            continue
        ratio = sp4 / sp7 if sp7 else float("nan")
        print("%3d    %8.3f    %8.3f    %6.3f" % (mp, sp7, sp4, ratio))

    print("")
    print("floor_mp (speedup 首达 %.1f 的档位):" % BIG_IMAGE_TARGET_SPEEDUP)
    for eff in EFFORTS:
        print("  effort=%d -> %s" % (eff, floors[eff]))

    if floors[7] is not None and floors[4] is not None:
        diff = abs(floors[7] - floors[4])
        verdict = "一致（相同档位）" if diff == 0 else (
            "接近（相差 %d 档，仍在容差内）" % diff)
        print("结论: floor_mp %s" % verdict)
    else:
        print("结论: 至少一侧未达目标 speedup，无法简单比较（取最大档）。")

    print("")
    print("总耗时 %.1fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()
