# -*- coding: utf-8 -*-
"""大图像素地板的按-CPU 校准逻辑（与应用、命令行脚本共用）。

双队列调度器用「相对中位数 + 绝对地板」两道闸门判定大图。绝对地板依赖 CPU——
核越多，满核 speedup 越大，地板可以更低、更激进；低核机器（≤4）则直接禁用大图
专属判定。校准一次即可把该临界点固化到本机，避免每次转换都跑基准。

本模块同时服务于：
  * 应用内「一键校准」按钮（CalibrateWorker 在后台线程调用 run_calibration）；
  * 命令行工具 tools/calibrate_floor.py（直接调用 run_calibration 并写 QSettings）。
"""
import os
import shutil
import tempfile
import subprocess
import time

# 大图判定的绝对地板目标加速比：仅当「独占满核」相对「单线程」快至少这么多时，
# 才值得为某文件暂停小图池去独占核心。校准据此寻找 speedup 首次达到该值的
# 分辨率作为像素地板。
BIG_IMAGE_TARGET_SPEEDUP = 2.0
# QSettings 键（conversion 组）：已校准的大图像素地板（整数像素字符串）；缺省时
# read_big_image_floor_px 回退到 estimate_floor_px 的启发式。
BIG_IMAGE_FLOOR_KEY = "big_image_floor_px"

# 按电源计划分别记忆阈值：每个方案存一份 big_image_floor_px__<scheme_guid>。
CALIB_SCHEME_KEY = "calib_scheme"          # 最近一次校准时的活动方案 GUID
CALIB_SCHEMES_KEY = "calib_schemes"        # 已校准方案 GUID 列表（逗号分隔）
CALIB_CPU_KEY = "calib_cpu_signature"      # 校准时的 CPU 指纹，用于检测换硬件

# 粗采样分辨率（兆像素）。如需更精细可追加中间档。
MP_LEVELS = [1, 2, 4, 8, 12, 16, 24, 32, 40, 48, 56, 64]


def cjxl_path():
    """返回 cjxl 可执行文件路径（PATH 优先，否则回退到 Windows 默认安装位）。"""
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
    """测 cjxl 在 --num_threads=nt 下将 png 编码的墙钟耗时（秒，取最快一次）。"""
    cjxl = cjxl_path()
    if not cjxl:
        return None
    best = None
    for _ in range(runs):
        fd, out = tempfile.mkstemp(suffix=".jxl")
        os.close(fd)
        t0 = time.time()
        try:
            subprocess.run(
                [cjxl, png, out, "--num_threads", str(nt), "-e", str(effort)],
                check=True, capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.CalledProcessError:
            return None
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
        dt = time.time() - t0
        best = dt if best is None else min(best, dt)  # 取最快一次，减小抖动
    return best


def run_calibration(progress_cb=None, log_cb=None, effort=7, runs=3, max_mp=64):
    """按本机 CPU 校准大图像素地板。

    progress_cb(msg): 状态栏短消息，如「校准中：测量 8MP（3/12）」。
    log_cb(line):     状态标签页日志行。
    返回 int 像素地板；缺失 cjxl 或测量失败返回 None。
    """
    cjxl = cjxl_path()
    if not cjxl:
        msg = "校准跳过：未找到 cjxl，无法测量。"
        if log_cb:
            log_cb(msg)
        if progress_cb:
            progress_cb("校准跳过：未找到 cjxl")
        return None

    cores = os.cpu_count() or 1
    if log_cb:
        log_cb("开始校准大图像素地板：本机逻辑核心数 %d，effort=%d，runs=%d"
               % (cores, effort, runs))

    levels = [m for m in MP_LEVELS if m <= max_mp]
    floor_mp = None
    rows = []
    for i, mp in enumerate(levels):
        if progress_cb:
            progress_cb("校准中：测量 %dMP（%d/%d）" % (mp, i + 1, len(levels)))
        if log_cb:
            log_cb("  — 测量 %dMP…" % mp)
        png = make_photo_mp(mp)
        t1 = bench(png, 1, effort, runs)
        tfull = bench(png, cores, effort, runs)
        try:
            os.remove(png)
        except OSError:
            pass
        if t1 is None or tfull is None or tfull <= 0:
            if log_cb:
                log_cb("    %2dMP 测量失败，跳过" % mp)
            continue
        speedup = t1 / tfull
        rows.append((mp, t1, tfull, speedup))
        if log_cb:
            log_cb("    %2dMP  T1=%.2fs  Tfull=%.2fs  speedup=%.2f"
                   % (mp, t1, tfull, speedup))
        if speedup >= BIG_IMAGE_TARGET_SPEEDUP and floor_mp is None:
            floor_mp = mp
            if log_cb:
                log_cb("    %2dMP 已首达目标加速比 %.1f，提前结束剩余档位测量"
                       % (mp, BIG_IMAGE_TARGET_SPEEDUP))
            break

    if not rows:
        if log_cb:
            log_cb("校准失败：无有效测量数据。")
        if progress_cb:
            progress_cb("校准失败：无有效测量数据")
        return None

    if floor_mp is None:
        # 全程 speedup 未达目标：地板设为最大测量档（最激进但仍保守）。
        floor_mp = rows[-1][0]
        if log_cb:
            log_cb("注意：speedup 始终未达 %.1f，地板取最大档 %dMP（保守）。"
                   % (BIG_IMAGE_TARGET_SPEEDUP, floor_mp))

    floor_px = floor_mp * 1000 * 1000
    if log_cb:
        log_cb("校准完成：big_image_floor_px = %d（约 %dMP，speedup≥%.1f 首达档）"
               % (floor_px, floor_mp, BIG_IMAGE_TARGET_SPEEDUP))
        log_cb("下次转换将直接使用该校准值。")
    return floor_px


def _settings():
    """统一的 QSettings 句柄（与应用内 QSettings() 指向同一 ini 文件）。"""
    from PySide6.QtCore import QSettings
    return QSettings(QSettings.IniFormat, QSettings.UserScope, "libjxl", "libjxl-gui")


def floor_key_for_scheme(scheme):
    """某电源方案专属的阈值键名。"""
    return "big_image_floor_px__" + scheme


def write_floor_px(floor_px, scheme=None):
    """把已校准的像素地板写入 QSettings 的 conversion 组（应用重启后仍生效）。

    scheme 为当前活动电源方案 GUID（来自 power.get_active_power_scheme）：
    提供时，除写入通用键 big_image_floor_px 外，还会写一份该方案专属键，并登记到
    已校准方案列表，使切换电源计划时可自动套用。
    """
    settings = _settings()
    settings.beginGroup("conversion")
    settings.setValue(BIG_IMAGE_FLOOR_KEY, str(floor_px))
    if scheme:
        settings.setValue(floor_key_for_scheme(scheme), str(floor_px))
        raw = settings.value(CALIB_SCHEMES_KEY, "")
        schemes = [x for x in str(raw).split(",") if x] if raw else []
        if scheme not in schemes:
            schemes.append(scheme)
        settings.setValue(CALIB_SCHEMES_KEY, ",".join(schemes))
        settings.setValue(CALIB_SCHEME_KEY, scheme)
    settings.endGroup()


def read_stored_floor_px(scheme=None):
    """返回已存储的像素地板（int）或 None。

    scheme 给定时优先返回该方案专属键；否则（或专属键缺失）回退通用键；都没有返
    回 None。调用方据此决定是否回退到 estimate_floor_px 启发式。
    """
    settings = _settings()
    settings.beginGroup("conversion")
    try:
        if scheme:
            raw = settings.value(floor_key_for_scheme(scheme), None)
            if raw is not None:
                try:
                    iv = int(raw)
                    if 0 <= iv < 10 ** 18:
                        return iv
                except (ValueError, TypeError):
                    pass
        raw = settings.value(BIG_IMAGE_FLOOR_KEY, None)
        if raw is not None:
            try:
                iv = int(raw)
                if 0 <= iv < 10 ** 18:
                    return iv
            except (ValueError, TypeError):
                pass
        return None
    finally:
        settings.endGroup()


def read_per_scheme_floor_px(scheme):
    """仅返回某方案专属键的值（int 或 None），用于判断是否已为该方案记录过。"""
    settings = _settings()
    settings.beginGroup("conversion")
    try:
        raw = settings.value(floor_key_for_scheme(scheme), None)
    finally:
        settings.endGroup()
    if raw is not None:
        try:
            iv = int(raw)
            if 0 <= iv < 10 ** 18:
                return iv
        except (ValueError, TypeError):
            pass
    return None


def has_calibration():
    """ini 中是否已存在校准结果（conversion/big_image_floor_px）。"""
    return read_stored_floor_px() is not None


def write_cpu_signature(sig):
    """记录校准时的 CPU 指纹（用于检测是否更换了硬件）。"""
    settings = _settings()
    settings.beginGroup("conversion")
    settings.setValue(CALIB_CPU_KEY, sig)
    settings.endGroup()


def read_cpu_signature():
    """读取已记录的 CPU 指纹；无则返回 None。"""
    settings = _settings()
    settings.beginGroup("conversion")
    try:
        return settings.value(CALIB_CPU_KEY, None)
    finally:
        settings.endGroup()


def clear_all_calibration():
    """清空全部校准数据（通用键、各方案专属键、方案列表、CPU 指纹），用于换硬件后
    丢弃旧阈值、当作首次启动重新校准。"""
    settings = _settings()
    settings.beginGroup("conversion")
    try:
        settings.remove(BIG_IMAGE_FLOOR_KEY)
        raw = settings.value(CALIB_SCHEMES_KEY, "")
        schemes = [x for x in str(raw).split(",") if x] if raw else []
        for sch in schemes:
            settings.remove(floor_key_for_scheme(sch))
        settings.remove(CALIB_SCHEMES_KEY)
        settings.remove(CALIB_SCHEME_KEY)
        settings.remove(CALIB_CPU_KEY)
    finally:
        settings.endGroup()
