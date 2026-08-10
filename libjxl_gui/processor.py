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
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


# The six action types shown in the 动作 tab's combo box.
ACTION_TYPES = ["调整大小", "旋转", "水印", "亮度/对比度", "锐化", "裁剪"]

# Sensible defaults per action type (also used by the param dialog).
DEFAULT_PARAMS = {
    "调整大小": {"width": 0, "height": 0},
    "旋转": {"angle": 90, "expand": True},
    "水印": {
        "text": "Sample", "font_size": 32, "opacity": 128,
        "position": "右下", "color": "white",
    },
    "亮度/对比度": {"brightness": 1.0, "contrast": 1.0},
    "锐化": {"factor": 1.5},
    "裁剪": {"left": 0, "top": 0, "width": 0, "height": 0},
}

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
    return img.resize((tw, th), Image.LANCZOS)


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
