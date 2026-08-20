# -*- coding: utf-8 -*-
"""按本机 CPU 校准「大图像素地板」并写入 QSettings。

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

可随时重跑以获得适配新硬件/新 effort 的更精确地板值。
"""
import os
import sys
import time
import shutil
import tempfile
import argparse
import subprocess

# 不创建任何 GUI，仅用 QtCore 的 QSettings 写持久化；offscreen 避免显示依赖。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings  # noqa: E402

from libjxl_gui.main_window import (  # noqa: E402
    BIG_IMAGE_TARGET_SPEEDUP,
    BIG_IMAGE_FLOOR_KEY,
)

# 粗采样分辨率（兆像素）。如需更精细可追加中间档。
MP_LEVELS = [1, 2, 4, 8, 12, 16, 24, 32, 40, 48, 56, 64]


def cjxl_path():
    p = shutil.which("cjxl")
    if p:
        return p
    cand = r"C:\Program Files\libjxl\bin\cjxl.exe"
    return cand if os.path.isfile(cand) else None


def make_photo_mp(mp):
    """生成一张「照片感」合成 PNG 到临时文件，返回路径。

    用低频渐变 + 彩色形状 + 少量高频纹理，再 LANCZOS 放大到目标尺寸，
    使 cjxl 的多线程扩展性接近真实自然照片（纯随机噪声图上多线程几乎无收益，
    会骗过校准）。仅用于基准测量，不落盘进项目。
    """
    from PIL import Image, ImageDraw

    side = int(round((mp * 1_000_000) ** 0.5))
    # 小尺寸底图（含结构），再放大——放大后即得带纹理的大图。
    base = Image.new("RGB", (320, 320))
    d = ImageDraw.Draw(base)
    for i in range(40):
        x0, y0 = (i * 7) % 320, (i * 13) % 320
        d.rectangle([x0, y0, x0 + 60, y0 + 60],
                    fill=(i * 5 % 256, (i * 9) % 256, (i * 3) % 256))
    # 渐变
    px = base.load()
    for y in range(320):
        for x in range(320):
            r, g, b = px[x, y]
            px[x, y] = (
                (r + x) % 256, (g + y) % 256, (b + (x + y) // 2) % 256,
            )
    big = base.resize((side, side), Image.LANCZOS)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    big.save(path, "PNG")
    return path


def bench(png, nt, effort, runs):
    """测 cjxl 在 --num_threads=nt 下将 png 编码的墙钟耗时（秒，取平均）。"""
    cjxl = cjxl_path()
    best = None
    for _ in range(runs):
        fd, out = tempfile.mkstemp(suffix=".jxl")
        os.close(fd)
        t0 = time.time()
        try:
            subprocess.run(
                [cjxl, png, out, "--num_threads", str(nt), "-e", str(effort)],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError:
            return None
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
        dt = time.time() - t0
        best = dt if best is None else best  # 取最快一次，减小抖动
    return best


def main():
    ap = argparse.ArgumentParser(description="校准大图像素地板")
    ap.add_argument("--effort", type=int, default=7)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-mp", type=int, default=64)
    args = ap.parse_args()

    cjxl = cjxl_path()
    if not cjxl:
        print("SKIP: 未找到 cjxl，无法校准。")
        return 1
    cores = os.cpu_count() or 1
    print("本机逻辑核心数：%d，effort=%d，runs=%d" % (cores, args.effort, args.runs))

    levels = [m for m in MP_LEVELS if m <= args.max_mp]
    floor_mp = None
    rows = []
    for mp in levels:
        png = make_photo_mp(mp)
        t1 = bench(png, 1, args.effort, args.runs)
        tfull = bench(png, cores, args.effort, args.runs)
        try:
            os.remove(png)
        except OSError:
            pass
        if t1 is None or tfull is None or tfull <= 0:
            print("  %2dMP 测量失败，跳过" % mp)
            continue
        speedup = t1 / tfull
        rows.append((mp, t1, tfull, speedup))
        print("  %2dMP  T1=%.2fs  Tfull=%.2fs  speedup=%.2f" % (mp, t1, tfull, speedup))
        if speedup >= BIG_IMAGE_TARGET_SPEEDUP and floor_mp is None:
            floor_mp = mp

    if not rows:
        print("无有效测量，退出。")
        return 1

    if floor_mp is None:
        # 全程 speedup 未达目标：地板设为最大测量档（最激进但仍保守）。
        floor_mp = rows[-1][0]
        print("注意：speedup 始终未达 %.1f，地板取最大档 %dMP（保守）。"
              % (BIG_IMAGE_TARGET_SPEEDUP, floor_mp))

    floor_px = floor_mp * 1000 * 1000
    settings = QSettings(QSettings.IniFormat, QSettings.UserScope, "libjxl", "libjxl-gui")
    settings.beginGroup("conversion")
    settings.setValue(BIG_IMAGE_FLOOR_KEY, str(floor_px))
    settings.endGroup()
    print("")
    print("校准完成：big_image_floor_px = %d （约 %dMP，speedup>=%.1f 首达档）"
          % (floor_px, floor_mp, BIG_IMAGE_TARGET_SPEEDUP))
    print("已写入 QSettings（conversion 组）。下次转换将直接使用该校准值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
