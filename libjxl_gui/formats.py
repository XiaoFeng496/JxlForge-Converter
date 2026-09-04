# -*- coding: utf-8 -*-
from . import i18n

"""纯 Python 实现的非主流图像格式支持（0 外部依赖，仅用标准库）。

覆盖两类能力：

1. **像素解码**（PFM / PAM / PGX）→ 输出二进制 PPM 字节，交给 Qt 原生读取。
   cjxl 命令行原生支持这几种输入，但 Qt 的 ``QImageReader`` 读不了，GUI 需要
   一个能显示缩略图 / 预览的位图。我们解码成 PPM（Qt 内置支持）而非 PNG，
   省去 Pillow——保持工具轻量。

2. **头部元数据解析**（EXR）→ 只读文件头，不解码任何像素。EXR 是浮点 HDR 格式，
   Qt / Pillow 都不原生支持，且本机未安装 OpenEXR；我们仅用 ``struct`` 解析头部
   属性块，向用户展示尺寸 / 通道 / 压缩等元数据，不做缩略图像素渲染。

所有函数失败时抛 ``Exception``（调用方统一 ``try/except`` 降级），不静默返回错误结果。
"""

import os
import struct


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def _fmt_size(num_bytes):
    """把字节数格式化为带 B/KB/MB 的可读串。"""
    try:
        size = int(num_bytes)
    except (TypeError, ValueError):
        return "-"
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / (1024.0 * 1024.0))
    if size >= 1024:
        return "%.1f KB" % (size / 1024.0)
    return "%d B" % size


# ---------------------------------------------------------------------------
# PFM（Portable Float Map）→ PPM
# ---------------------------------------------------------------------------
def pfm_to_ppm_bytes(path):
    """把 PFM 文件解码为二进制 PPM（P5 灰度 / P6 RGB）字节。

    PFM 头三行：magic(``Pf``/``PF``)、``width height``、scale（负=小端）。
    之后是 ``width*height*channels`` 个 32-bit 浮点，行序与 height 符号相关
    （height>0 表示自底向上，需翻转）。HDR 浮点经全局归一化映射到 0..255。
    """
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 8:
        raise ValueError(i18n.t("文件过小，不是 PFM"))
    nl1 = data.index(b"\n")
    magic = data[:nl1].strip()
    if magic not in (b"PF", b"Pf"):
        raise ValueError(i18n.t("magic 不符，不是 PFM"))
    channels = 3 if magic == b"PF" else 1
    nl2 = data.index(b"\n", nl1 + 1)
    wh = data[nl1 + 1:nl2].split()
    if len(wh) < 2:
        raise ValueError(i18n.t("PFM 头缺少宽高"))
    width = int(wh[0])
    height_raw = int(wh[1])
    nl3 = data.index(b"\n", nl2 + 1)
    scale = float(data[nl2 + 1:nl3])
    little_endian = scale < 0
    height = abs(height_raw)
    if width <= 0 or height <= 0:
        raise ValueError(i18n.t("PFM 宽高非法"))

    count = width * height * channels
    data_start = nl3 + 1
    raw = data[data_start:data_start + count * 4]
    if len(raw) < count * 4:
        raise ValueError(i18n.t("PFM 像素数据不完整"))
    floats = struct.unpack(("<" if little_endian else ">") + ("%df" % count), raw)

    # height>0 时 PFM 自底向上存储，翻转为自上向下以匹配 PPM。
    if height_raw > 0:
        row = width * channels
        flipped = []
        for y in range(height - 1, -1, -1):
            flipped.extend(floats[y * row:(y + 1) * row])
        floats = flipped

    # 全局归一化到 0..255（HDR 浮点范围未知，保守映射）。
    mn = min(floats)
    mx = max(floats)
    if mx <= 1.0 and mn >= 0.0:
        factor = 255.0
    elif mx > mn:
        factor = 255.0 / mx
    else:
        factor = 0.0
    out = bytearray()
    for v in floats:
        c = int(v * factor + 0.5)
        if c < 0:
            c = 0
        elif c > 255:
            c = 255
        out.append(c)

    if channels == 3:
        return b"P6\n%d %d\n255\n" % (width, height) + bytes(out)
    return b"P5\n%d %d\n255\n" % (width, height) + bytes(out)


# ---------------------------------------------------------------------------
# PAM（Portable Arbitrary Map）→ PPM
# ---------------------------------------------------------------------------
def _pam_colortype(tupltype, depth):
    """根据 TUPLTYPE / DEPTH 判定输出为 RGB(3) 还是灰度(1)。"""
    t = (tupltype or "").upper()
    if t in ("RGB", "RGB_ALPHA"):
        return 3
    # GRAYSCALE / GRAYSCALE_ALPHA / BLACKANDWHITE / BLACKANDWHITE_ALPHA 均归灰度
    return 1


def pam_to_ppm_bytes(path):
    """把 PAM 文件解码为二进制 PPM（P5 灰度 / P6 RGB）字节。

    PAM 头以 ``ENDHDR`` 结束，含 WIDTH/HEIGHT/DEPTH/MAXVAL/TUPLTYPE。之后是
    逐像素 ``DEPTH`` 个样本（每样本 1 或 2 字节，大端）。alpha 通道被丢弃，
    16-bit 样本右移 8 位降到 8-bit。
    """
    with open(path, "rb") as f:
        full = f.read()
    if len(full) < 8 or not full.startswith(b"P7"):
        raise ValueError(i18n.t("magic 不符，不是 PAM"))

    # 头部文本以 "ENDHDR\n" 行终结，其后紧跟二进制像素数据。搜索带换行的
    # "ENDHDR\n" 既能精确定位头部边界，也避免把像素二进制里恰好出现的
    # 裸 "ENDHDR" 误判为头部结束。
    marker = full.find(b"ENDHDR\n")
    if marker < 0:
        raise ValueError(i18n.t("PAM 缺少 ENDHDR"))
    header = full[:marker].decode("ascii", "ignore")
    fields = {}
    for line in header.split("\n"):
        parts = line.split(None, 1)
        if len(parts) == 2:
            fields[parts[0].upper()] = parts[1].strip()
    try:
        width = int(fields["WIDTH"])
        height = int(fields["HEIGHT"])
        depth = int(fields["DEPTH"])
        maxval = int(fields["MAXVAL"])
    except (KeyError, ValueError):
        raise ValueError(i18n.t("PAM 头字段缺失或非法"))
    if width <= 0 or height <= 0 or depth <= 0:
        raise ValueError(i18n.t("PAM 尺寸非法"))
    tupltype = fields.get("TUPLTYPE", "RGB")
    if maxval <= 0:
        raise ValueError(i18n.t("PAM MAXVAL 非法"))

    sb = 1 if maxval <= 255 else (2 if maxval <= 65535 else None)
    if sb is None:
        raise ValueError(i18n.t("不支持的 PAM MAXVAL（>65535）"))

    # 数据即 "ENDHDR\n" 之后的全部内容（含像素二进制，不再跳过任何字节）。
    i = marker + len(b"ENDHDR\n")
    data = full[i:]
    expected = width * height * depth * sb
    if len(data) < expected:
        raise ValueError(i18n.t("PAM 像素数据不完整"))

    target = _pam_colortype(tupltype, depth)
    out = bytearray()
    n_pixels = width * height
    for p in range(n_pixels):
        base = p * depth * sb
        samples = []
        for c in range(depth):
            off = base + c * sb
            if sb == 1:
                val = data[off]
            else:
                val = struct.unpack(">H", data[off:off + 2])[0]
            samples.append(val)
        if target == 3:
            for c in range(3):
                v = samples[c]
                if sb == 2:
                    v >>= 8
                out.append(v & 0xFF)
        else:
            v = samples[0]
            if sb == 2:
                v >>= 8
            out.append(v & 0xFF)

    if target == 3:
        return b"P6\n%d %d\n255\n" % (width, height) + bytes(out)
    return b"P5\n%d %d\n255\n" % (width, height) + bytes(out)


# ---------------------------------------------------------------------------
# PGX（JPEG2000 参考格式）→ PPM（灰度）
# ---------------------------------------------------------------------------
def pgx_to_ppm_bytes(path):
    """把 PGX 文件解码为二进制灰度 PPM（P5）字节。

    PGX 单组件灰度，头一行：``PG <ML|LM> <S|U> <bits> <width> <height>``，
    之后是 ``width*height`` 个 ``bits`` 位样本。经全局 min/max 归一化到 0..255。
    """
    with open(path, "rb") as f:
        full = f.read()
    nl = full.index(b"\n")
    tokens = full[:nl].decode("ascii", "ignore").split()
    if len(tokens) < 6 or tokens[0] != "PG":
        raise ValueError(i18n.t("magic 不符，不是 PGX"))
    endian = tokens[1].upper()
    sign = tokens[2].upper()
    prec = int(tokens[3])
    width = int(tokens[4])
    height = int(tokens[5])
    if width <= 0 or height <= 0 or prec <= 0:
        raise ValueError(i18n.t("PGX 头参数非法"))
    sb = (prec + 7) // 8
    if sb not in (1, 2):
        raise ValueError(i18n.t("不支持的 PGX 精度（>16 位）"))

    ec = "<" if endian == "LM" else ">"
    if sb == 1:
        fmt = ec + ("b" if sign == "S" else "B")
    else:
        fmt = ec + ("h" if sign == "S" else "H")
    n = width * height
    raw = full[nl + 1:nl + 1 + n * sb]
    if len(raw) < n * sb:
        raise ValueError(i18n.t("PGX 像素数据不完整"))
    vals = struct.unpack(ec + fmt[1:] * n, raw)

    mn = min(vals)
    mx = max(vals)
    span = mx - mn
    out = bytearray()
    if span > 0:
        inv = 255.0 / span
        for v in vals:
            out.append(int((v - mn) * inv + 0.5))
    else:
        out.extend(b"\x00" * n)

    return b"P5\n%d %d\n255\n" % (width, height) + bytes(out)


# ---------------------------------------------------------------------------
# EXR（OpenEXR）头部元数据解析 —— 只读头，不解码像素
# ---------------------------------------------------------------------------
_EXR_MAGIC = b"\x76\x2f\x31\x01"
_COMPRESSION_NAMES = {
    0: "none（无压缩）", 1: "RLE", 2: "ZIPS", 3: "ZIP", 4: "PIZ",
    5: "PXR24", 6: "B44", 7: "B44A", 8: "DWAA", 9: "DWAB",
}
_PIXEL_TYPE_NAMES = {0: "UINT", 1: "HALF", 2: "FLOAT"}
_LINE_ORDER_NAMES = {0: "INCREASING_Y", 1: "DECREASING_Y", 2: "RANDOM_Y"}


def _parse_exr_chlist(val):
    """解析 EXR channels 属性（chlist）中的通道列表。"""
    channels = []
    i = 0
    n = len(val)
    while i < n:
        end = val.find(b"\x00", i)
        if end < 0:
            break
        name = val[i:end].decode("ascii", "ignore")
        i = end + 1
        if name == "":
            break  # 通道表以空名结尾
        if i + 16 <= n:
            ptype, _plinear, xsamp, ysamp = struct.unpack("<iB3xii", val[i:i + 16])
            channels.append({
                "name": name,
                "type": _PIXEL_TYPE_NAMES.get(ptype, str(ptype)),
                "xSampling": xsamp,
                "ySampling": ysamp,
            })
            i += 16
        else:
            break
    return channels


def parse_exr_header(path):
    """解析 EXR 文件头部，返回元数据字典（不解码任何像素数据）。

    字典键：``width`` / ``height`` / ``channels``(list) / ``compression`` /
    ``pixelAspectRatio`` / ``lineOrder`` / ``tiles``(dict|None) /
    ``multipart`` / ``filename`` / ``filesize``。解析失败抛 ``Exception``。
    """
    with open(path, "rb") as f:
        head = f.read(8)
    if len(head) < 8 or head[:4] != _EXR_MAGIC:
        raise ValueError(i18n.t("magic 不符，不是 EXR"))

    with open(path, "rb") as f:
        data = f.read()
    # 版本号低 8 位为版本，高位为标志位（0x100000=多部件，0x200000=非图像）。
    version = struct.unpack("<I", data[4:8])[0]
    flags = version & 0xFFFFFF00

    meta = {
        "width": 0,
        "height": 0,
        "channels": [],
        "compression": "",
        "pixelAspectRatio": 1.0,
        "lineOrder": "",
        "tiles": None,
        "multipart": bool(flags & 0x100000),
        "filename": os.path.basename(path),
        "filesize": len(data),
    }

    i = 8
    n = len(data)
    while i < n:
        end = data.find(b"\x00", i)
        if end < 0:
            break
        name = data[i:end].decode("ascii", "ignore")
        i = end + 1
        if name == "":
            break  # 头部结束
        tend = data.find(b"\x00", i)
        if tend < 0:
            break
        typ = data[i:tend].decode("ascii", "ignore")
        i = tend + 1
        if i + 4 > n:
            break
        (size,) = struct.unpack("<I", data[i:i + 4])
        i += 4
        val = data[i:i + size]
        i += size
        if len(val) < size:
            break

        if name == "dataWindow" and typ == "box2i" and size >= 16:
            xmin, ymin, xmax, ymax = struct.unpack("<iiii", val[:16])
            meta["width"] = xmax - xmin + 1
            meta["height"] = ymax - ymin + 1
        elif name == "displayWindow" and typ == "box2i" and size >= 16 and meta["width"] == 0:
            xmin, ymin, xmax, ymax = struct.unpack("<iiii", val[:16])
            meta["width"] = xmax - xmin + 1
            meta["height"] = ymax - ymin + 1
        elif name == "channels" and typ == "chlist":
            meta["channels"] = _parse_exr_chlist(val)
        elif name == "compression" and size >= 1:
            meta["compression"] = _COMPRESSION_NAMES.get(val[0], i18n.t("未知(%d)") % val[0])
        elif name == "pixelAspectRatio" and typ == "float" and size >= 4:
            meta["pixelAspectRatio"] = struct.unpack("<f", val[:4])[0]
        elif name == "lineOrder" and size >= 1:
            meta["lineOrder"] = _LINE_ORDER_NAMES.get(val[0], str(val[0]))
        elif name == "tiles" and typ == "tiledesc" and size >= 9:
            xt, yt, mode = struct.unpack("<iiB", val[:9])
            meta["tiles"] = {"x": xt, "y": yt, "mode": mode}

    if meta["width"] <= 0 or meta["height"] <= 0:
        raise ValueError(i18n.t("EXR 头部缺少有效的 dataWindow"))
    return meta


def exr_metadata_text(meta):
    """把 EXR 元数据字典渲染为可展示的多行中文文本。"""
    lines = []
    lines.append(i18n.t("格式：OpenEXR（浮点 HDR）"))
    lines.append(i18n.t("文件名：%s") % (meta.get("filename") or ""))
    w = meta.get("width") or 0
    h = meta.get("height") or 0
    lines.append(i18n.t("尺寸：%d x %d") % (w, h) if w and h else i18n.t("尺寸：未知"))
    ch = meta.get("channels") or []
    ch_str = "、".join("%s(%s)" % (c["name"], c["type"]) for c in ch) or i18n.t("未知")
    lines.append(i18n.t("通道：%s") % ch_str)
    lines.append(i18n.t("压缩：%s") % (meta.get("compression") or i18n.t("未知")))
    lines.append(i18n.t("像素宽高比：%.4f") % (meta.get("pixelAspectRatio") or 1.0))
    lo = meta.get("lineOrder") or ""
    if lo:
        lines.append(i18n.t("行序：%s") % lo)
    t = meta.get("tiles")
    if t:
        lines.append(i18n.t("分块：%d x %d") % (t["x"], t["y"]))
    if meta.get("multipart"):
        lines.append(i18n.t("多部件：是"))
    lines.append(i18n.t("文件大小：%s") % _fmt_size(meta.get("filesize") or 0))
    lines.append("")
    lines.append(i18n.t("说明：EXR 为浮点 HDR 格式，本工具仅解析头部元数据，不渲染像素缩略图。"))
    return "\n".join(lines)
