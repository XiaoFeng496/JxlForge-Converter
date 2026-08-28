# -*- coding: utf-8 -*-
"""Pillow-based image processing actions applied before libjxl encode/decode.

All code identifiers (variables, functions, classes) are in English. The
module raises Chinese messages only where it is surfaced to the user (the
main window checks ``AVAILABLE`` beforehand and reports the missing
dependency itself).

Each action is a dict of the form ``{"type": <str>, "params": <dict>}``. The
ordered list of such dicts is passed to :func:`apply_actions`, which runs them
in sequence on an in-memory ``PIL.Image`` and returns the processed image.
"""

try:
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


# The action types shown in the 动作 tab's combo box.
ACTION_TYPES = [
    "调整大小", "旋转", "水印", "亮度/对比度", "锐化", "裁剪",
    "规格化", "曝光", "阴影/高光",
]

# Sensible defaults per action type (also used by the param dialog).
DEFAULT_PARAMS = {
    "调整大小": {"width": 0, "height": 0, "algorithm": "LANCZOS"},
    "旋转": {"angle": 90, "expand": True},
    "水印": {
        "text": "Sample", "font_size": 32, "opacity": 128,
        "position": "右下", "color": "white",
    },
    "亮度/对比度": {"brightness": 1.0, "contrast": 1.0},
    "锐化": {"factor": 1.5},
    "裁剪": {"left": 0, "top": 0, "width": 0, "height": 0},
    # 规格化：cutoff=0 等价于纯拉满直方图，cutoff=N 截掉各端 N‰ 的极值再拉满。
    # 0 是默认（与「规格化」字面意义最贴），>0 用于避开噪点/过曝像素。
    "规格化": {"cutoff": 0},
    # 曝光：以 EV（曝光档位）为单位。0=不变，+1=亮一倍（2.0x），-1=暗一半（0.5x）。
    # 范围 [-3, +3] 步进 0.1，便于细调。
    "曝光": {"ev": 0.0},
    # 阴影/高光：分别调整暗部（阴影）与亮部（高光）的亮度系数。
    # 1.0=不变；>1.0 提亮阴影 / 压低高光，<1.0 反之。范围 [0.0, 2.0]。
    "阴影/高光": {"shadow": 1.0, "highlight": 1.0},
}

# 「调整大小」可选的重采样算法。中文标签面向用户；内部值是 Pillow 的
# ``Image.Resampling`` 枚举名，未识别时回退到 LANCZOS（最稳）。BICUBIC
# 默认替代 LANCZOS 的选项，二者都是高质量插值；BOX 适合缩小整数倍，
# BILINEAR 速度更快但质量稍差，NEAREST 适合像素画（保边沿锐利）。
RESIZE_ALGORITHMS = [
    ("LANCZOS", "LANCZOS (高质量, 默认)"),
    ("BICUBIC", "BICUBIC (高质量, 较快)"),
    ("BILINEAR", "BILINEAR (较快)"),
    ("BOX", "BOX (缩小整数倍)"),
    ("HAMMING", "HAMMING (缩小)"),
    ("NEAREST", "NEAREST (像素画, 最快)"),
]


def _resolve_resample(name):
    """把字符串算法名映射到 Pillow 的 ``Image.Resampling`` 枚举。

    旧数据没有 ``algorithm`` 键或值不在白名单里都回退到 LANCZOS。
    """
    if not name:
        return Image.Resampling.LANCZOS
    try:
        return getattr(Image.Resampling, str(name).upper())
    except AttributeError:
        return Image.Resampling.LANCZOS

# Nine-grid positions for the watermark, in the same order shown in the combo.
WATERMARK_POSITIONS = [
    "左上", "中上", "右上",
    "左中", "居中", "右中",
    "左下", "中下", "右下",
]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _load_font(size):
    """Load a font that can render CJK text on Windows; fall back to the PIL
    default (ASCII only) elsewhere or when no system font is found."""
    import os
    candidates = []
    if os.name == "nt":
        base = r"C:\Windows\Fonts"
        candidates = [
            os.path.join(base, "msyh.ttc"),
            os.path.join(base, "msyhbd.ttc"),
            os.path.join(base, "simhei.ttf"),
            os.path.join(base, "simsun.ttc"),
        ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _watermark_offset(position, img_w, img_h, tw, th, margin):
    hmap = {"左": margin, "中": (img_w - tw) // 2, "右": img_w - tw - margin}
    vmap = {"上": margin, "中": (img_h - th) // 2, "下": img_h - th - margin}
    grid = {
        "左上": ("左", "上"), "中上": ("中", "上"), "右上": ("右", "上"),
        "左中": ("左", "中"), "居中": ("中", "中"), "右中": ("右", "中"),
        "左下": ("左", "下"), "中下": ("中", "下"), "右下": ("右", "下"),
    }
    hpos, vpos = grid.get(position, ("右", "下"))
    return _clamp(hmap[hpos], 0, img_w), _clamp(vmap[vpos], 0, img_h)


def apply_actions(image, actions):
    """Apply each action in ``actions`` (in order) to ``image`` and return the
    resulting ``PIL.Image`` (mode RGBA)."""
    if not AVAILABLE:
        raise RuntimeError("Pillow 未安装，无法执行图像处理动作。")
    img = image.convert("RGBA")
    for action in actions:
        atype = action.get("type")
        params = action.get("params", {}) or {}
        if atype == "调整大小":
            img = _resize(img, params)
        elif atype == "旋转":
            img = _rotate(img, params)
        elif atype == "水印":
            img = _watermark(img, params)
        elif atype == "亮度/对比度":
            img = _brightness_contrast(img, params)
        elif atype == "锐化":
            img = _sharpen(img, params)
        elif atype == "裁剪":
            img = _crop(img, params)
        elif atype == "规格化":
            img = _normalize(img, params)
        elif atype == "曝光":
            img = _exposure(img, params)
        elif atype == "阴影/高光":
            img = _shadow_highlight(img, params)
    return img


def _resize(img, p):
    tw = int(p.get("width", 0) or 0)
    th = int(p.get("height", 0) or 0)
    iw, ih = img.size
    if tw <= 0 and th <= 0:
        return img
    if tw <= 0:
        # Only height given: derive width from the aspect ratio.
        tw = max(1, int(round(iw * (th / ih))))
    elif th <= 0:
        # Only width given: derive height from the aspect ratio.
        th = max(1, int(round(ih * (tw / iw))))
    else:
        # Contain within tw x th, keep aspect ratio.
        ratio = min(tw / iw, th / ih)
        tw, th = max(1, int(round(iw * ratio))), max(1, int(round(ih * ratio)))
    return img.resize((tw, th), _resolve_resample(p.get("algorithm")))


def _rotate(img, p):
    angle = float(p.get("angle", 0) or 0)
    # The user-facing angle is clockwise; PIL rotates counter-clockwise.
    return img.rotate(-angle, expand=True, fillcolor=(0, 0, 0, 0))


def _brightness_contrast(img, p):
    b = float(p.get("brightness", 1.0) or 1.0)
    c = float(p.get("contrast", 1.0) or 1.0)
    if b != 1.0:
        img = ImageEnhance.Brightness(img).enhance(b)
    if c != 1.0:
        img = ImageEnhance.Contrast(img).enhance(c)
    return img


def _sharpen(img, p):
    f = float(p.get("factor", 1.0) or 1.0)
    if f != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(f)
    return img


def _crop(img, p):
    iw, ih = img.size
    left = int(p.get("left", 0) or 0)
    top = int(p.get("top", 0) or 0)
    width = int(p.get("width", 0) or 0)
    height = int(p.get("height", 0) or 0)
    right = left + width if width > 0 else iw
    bottom = top + height if height > 0 else ih
    left = _clamp(left, 0, iw)
    right = _clamp(right, 0, iw)
    top = _clamp(top, 0, ih)
    bottom = _clamp(bottom, 0, ih)
    if right <= left or bottom <= top:
        return img
    return img.crop((left, top, right, bottom))


def _normalize(img, p):
    """自动拉伸直方图到 0-255，让图像对比度最大化。

    Pillow 的 ``ImageOps.autocontrast(cutoff)`` 会在直方图两端各丢弃
    ``cutoff``‰ 的极值像素再拉满；``cutoff=0`` 即纯规格化（端点像素
    强制到 0/255）。该动作在 RGBA 上是按全通道做的，但完全透明像素
    会让对比度失真，因此先合成到白底再规格化、再恢复透明通道。
    """
    cutoff = int(p.get("cutoff", 0) or 0)
    cutoff = _clamp(cutoff, 0, 50)  # 上限防误输入把整图都截掉
    if cutoff == 0 and img.mode == "RGBA":
        # 透明像素的 0 会污染直方图。先合成到白底。
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        out = ImageOps.autocontrast(bg, cutoff=0)
        # 把原 alpha 贴回来
        r, g, b = out.split()
        return Image.merge("RGBA", (r, g, b, img.split()[3]))
    out = ImageOps.autocontrast(img.convert("RGB"), cutoff=cutoff)
    return out


def _exposure(img, p):
    """按 EV 档位调整曝光（+1 EV = 亮度翻倍，-1 EV = 减半）。

    公式：``v' = v * 2**EV``，clamp 到 [0, 255]。对 RGBA 只动前 3 通道，
    保留 alpha。这是比 ``ImageEnhance.Brightness`` 更符合摄影直觉的
    调整方式（后者是线性乘，与 EV 不严格等价，但视觉差异较小）。
    """
    ev = float(p.get("ev", 0.0) or 0.0)
    if ev == 0.0:
        return img
    gain = 2.0 ** ev
    if img.mode == "RGBA":
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b))
    else:
        rgb = img.convert("RGB")
    out = rgb.point(lambda v: 255 if v * gain >= 255 else max(0, int(v * gain)))
    if img.mode == "RGBA":
        r2, g2, b2 = out.split()
        return Image.merge("RGBA", (r2, g2, b2, a))
    return out


def _shadow_highlight(img, p):
    """分别调整阴影（暗部）与高光（亮部）的亮度。

    实现：用绿色通道近似亮度（暗/亮权重），按权重把 shadow/highlight
    系数融合成逐像素 factor，再乘到原图。轻量近似（不是真正的
    Shadow/Highlight 滤镜，但能给出方向性的调整且性能好）。
    公式：factor = 1 - (1-shadow)*(1-L) - (1-highlight)*L
    即暗部按 shadow 缩放、亮部按 highlight 缩放、中间平滑过渡。
    """
    shadow = float(p.get("shadow", 1.0) or 1.0)
    highlight = float(p.get("highlight", 1.0) or 1.0)
    if shadow == 1.0 and highlight == 1.0:
        return img
    shadow = _clamp(shadow, 0.0, 2.0)
    highlight = _clamp(highlight, 0.0, 2.0)
    has_alpha = img.mode == "RGBA"
    if has_alpha:
        r, g, b, a = img.split()
        rgb_img = Image.merge("RGB", (r, g, b))
    else:
        rgb_img = img.convert("RGB")
        a = None
    w, h = rgb_img.size
    s_part = 1.0 - shadow
    h_part = 1.0 - highlight
    # 逐像素计算：每像素用绿色通道当亮度（暗/亮权重的近似）。
    out = []
    for px in rgb_img.getdata():
        lum = px[1] / 255.0
        m_s = 1.0 - lum
        m_h = lum
        factor = 1.0 - s_part * m_s - h_part * m_h
        if factor < 0.0:
            factor = 0.0
        out.append(tuple(
            255 if c * factor >= 255 else int(c * factor) for c in px
        ))
    out_img = Image.new("RGB", (w, h))
    out_img.putdata(out)
    if a is not None:
        return Image.merge("RGBA", (*out_img.split(), a))
    return out_img


def _watermark(img, p):
    text = (p.get("text", "") or "").strip()
    if not text:
        return img
    size = int(p.get("font_size", 32) or 32)
    opacity = _clamp(int(p.get("opacity", 128) or 128), 0, 255)
    position = p.get("position", "右下") or "右下"
    color_name = p.get("color") or "white"
    rgb = (0, 0, 0) if color_name == "black" else (255, 255, 255)
    font = _load_font(size)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
    except Exception:
        bbox = (0, 0, size * max(1, len(text)), int(size * 1.2))
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    margin = max(8, size // 4)
    x, y = _watermark_offset(position, img.size[0], img.size[1], tw, th, margin)
    draw.text((x - bbox[0], y - bbox[1]), text, font=font,
              fill=rgb + (opacity,))
    return Image.alpha_composite(img, layer)
