# -*- coding: utf-8 -*-
from . import i18n

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
    from PIL import (
        Image, ImageChops, ImageDraw, ImageFont, ImageEnhance, ImageFilter,
        ImageOps,
    )
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


# 「动作」标签页里可添加的动作类型。中文既是显示名又是内部 ID（会被存进
# QSettings，并在 apply_actions 里做字面量比较），显示时一律走 i18n.t()。
#
# 顺序 = 菜单里的展示顺序，按类别排（与 ACTION_GROUPS 一致），不是添加先后。
ACTION_TYPES = [
    # 几何与尺寸
    "调整大小", "裁剪", "旋转",
    # 明暗与影调
    "亮度", "对比度", "曝光", "阴影/高光", "规格化",
    # 色彩
    "饱和度", "自然饱和度",
    # 清晰度
    "锐化", "模糊",
    # 叠加
    "水印",
]

# 「添加动作 ▶」菜单的分组（组名也是中文 ID，显示走 i18n.t）。
# ⚠️ 组内 id 必须覆盖 ACTION_TYPES 且一一对应：菜单按顺序渲染这两个结构，
# 测试（test_i18n_id_separation）按菜单顺序对账 ACTION_TYPES。
ACTION_GROUPS = [
    ("几何与尺寸", ["调整大小", "裁剪", "旋转"]),
    ("明暗与影调", ["亮度", "对比度", "曝光", "阴影/高光", "规格化"]),
    ("色彩", ["饱和度", "自然饱和度"]),
    ("清晰度", ["锐化", "模糊"]),
    ("叠加", ["水印"]),
]

# Sensible defaults per action type (also used by the param dialog).
DEFAULT_PARAMS = {
    "调整大小": {"width": 0, "height": 0, "algorithm": "LANCZOS"},
    "旋转": {"angle": 90, "expand": True},
    "水印": {
        "text": "Sample", "font_size": 32, "opacity": 128,
        "position": "右下", "color": "white",
    },
    # 旧版「亮度/对比度」已拆成「亮度」「对比度」两个独立动作（2026-09-09）。
    # 这个键只用于兼容已存进 ini 的老数据（见 migrate_legacy_actions），
    # 菜单里不再出现，新代码不要再用它。
    "亮度/对比度": {"brightness": 1.0, "contrast": 1.0},
    "亮度": {"factor": 1.0},
    "对比度": {"factor": 1.0},
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
    # 饱和度：全局均匀调整，等价于「与灰度图按 factor 混合」。
    # 1.0=不变，0.0=完全去色（灰度），>1.0 更浓。范围 [0.0, 3.0]。
    "饱和度": {"factor": 1.0},
    # 自然饱和度（vibrance）：只强化低饱和像素，已经够艳的像素几乎不动，
    # 因此不会像全局饱和度那样把肤色/天空一次性推到溢色。
    # 同样以 1.0=不变、0.0=完全去色，范围 [0.0, 2.0]（内部增量夹到 ±1）。
    "自然饱和度": {"factor": 1.0},
    # 模糊：radius 为半径（像素），0=不处理；method 见 BLUR_METHODS。
    "模糊": {"radius": 2.0, "method": "GAUSSIAN"},
}

# 「模糊」可选的滤波器。内部 ID 为英文（存进 QSettings / 配置），
# 中文标签仅用于显示（与阶段 6 的 ID / 显示名分离约定一致）。
BLUR_METHODS = [
    ("GAUSSIAN", "高斯"),
    ("BOX", "方框"),
    ("MEDIAN", "中值"),
]

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


def _num(p, key, default):
    """读数值参数。

    ⚠️ 不能用 ``p.get(key) or default`` 的写法：0.0 是合法取值（完全去色、
    不模糊），但 ``0.0 or default`` 会被判为假而回退到 default，用户设的
    0 就失效了。只有键缺失 / 取不到数时才用默认值。
    """
    v = p.get(key, None)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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


def migrate_legacy_actions(actions):
    """把旧版动作类型升级成当前形式（读 QSettings 时调用一次即可）。

    目前只有一条规则：旧版「亮度/对比度」拆成「亮度」+「对比度」两个动作。
    拆开后按列表顺序链式执行（先亮度、后对比度）与旧实现**完全等价**，
    所以老配置升级后出图结果不变，只是参数变成两个可单独开关的动作。
    """
    out = []
    for a in actions or []:
        if not isinstance(a, dict) or a.get("type") != "亮度/对比度":
            out.append(a)
            continue
        p = a.get("params", {}) or {}
        for new_type, key in (("亮度", "brightness"), ("对比度", "contrast")):
            item = dict(a)
            item["type"] = new_type
            params = dict(p)
            params.pop("brightness", None)
            params.pop("contrast", None)
            params["factor"] = _num(p, key, 1.0)
            item["params"] = params
            out.append(item)
    return out


def apply_actions(image, actions):
    """Apply each action in ``actions`` (in order) to ``image`` and return the
    resulting ``PIL.Image`` (mode RGBA)."""
    if not AVAILABLE:
        raise RuntimeError(i18n.t("Pillow 未安装，无法执行图像处理动作。"))
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
            # 旧数据兼容（见 DEFAULT_PARAMS 的注释）；菜单已不再提供。
            img = _brightness_contrast(img, params)
        elif atype == "亮度":
            img = _brightness(img, params)
        elif atype == "对比度":
            img = _contrast(img, params)
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
        elif atype == "饱和度":
            img = _saturation(img, params)
        elif atype == "自然饱和度":
            img = _vibrance(img, params)
        elif atype == "模糊":
            img = _blur(img, params)
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
    """旧版「亮度/对比度」合并动作，仅为兼容已存的老数据保留。"""
    img = _brightness(img, {"factor": _num(p, "brightness", 1.0)})
    return _contrast(img, {"factor": _num(p, "contrast", 1.0)})


def _brightness(img, p):
    """亮度：1.0=不变，<1.0 变暗，>1.0 变亮（ImageEnhance.Brightness）。"""
    f = _num(p, "factor", 1.0)
    if f == 1.0:
        return img
    return ImageEnhance.Brightness(img).enhance(_clamp(f, 0.0, 3.0))


def _contrast(img, p):
    """对比度：1.0=不变，0.0=整图归一成灰（ImageEnhance.Contrast）。"""
    f = _num(p, "factor", 1.0)
    if f == 1.0:
        return img
    return ImageEnhance.Contrast(img).enhance(_clamp(f, 0.0, 3.0))


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
    保留 alpha。

    性能：用 256 项 LUT 走 ``point()``（C 实现）而非 ``point(lambda)``——
    后者在部分 Pillow 版本会对每个像素回调 Python，大图明显变慢。
    """
    ev = float(p.get("ev", 0.0) or 0.0)
    if ev == 0.0:
        return img
    gain = 2.0 ** _clamp(ev, -3.0, 3.0)
    lut = [min(255, max(0, int(round(i * gain)))) for i in range(256)]
    # ⚠️ point(lut) 对多通道图像要求 LUT 长度 = 256 × 通道数（每通道一份），
    # 传 256 项会报 "wrong number of lut entries"。RGBA 的第 4 通道是 alpha，
    # 必须给恒等 LUT 保持不透明信息不变。
    bands = img.getbands()
    if img.mode == "RGBA":
        return img.point(lut * 3 + list(range(256)))
    return img.point(lut * len(bands))


def _shadow_highlight(img, p):
    """分别调整阴影（暗部）与高光（亮部）的亮度。

    公式：factor = 1 - (1-shadow)*(1-L) - (1-highlight)*L
    即暗部按 shadow 缩放、亮部按 highlight 缩放、中间平滑过渡。

    ⚠️ 性能：早期版本用 Python 逐像素循环，2560×1440 要 **3 秒**
    （370 万像素 × 3 通道回调）。现改为**全 C 实现**：
      1. ``convert("L")`` 取亮度（C）
      2. 预先算两张 256 项 LUT：提亮量 up / 压暗量 down（Python 只跑 256 次）
      3. ``L.point(lut)`` 把 LUT 应用到亮度图得权重图（C）
      4. ``ImageChops.multiply`` 得增减量，``subtract``/``add`` 合成（C）
    结果与逐像素版本逐值一致，但快两个数量级。
    注：factor 上限 2.0 → (factor-1)*255 最大 255，L 模式（8bit）刚好放得下。
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

    # 1) 亮度图（C 实现，标准 ITU-R 601-2 加权）
    lum = rgb_img.convert("L")
    # 2) 两张 LUT（Python 只循环 256 次，不是 370 万次）
    s_part = 1.0 - shadow
    h_part = 1.0 - highlight
    up_lut = []
    down_lut = []
    for L in range(256):
        l = L / 255.0
        factor = 1.0 - s_part * (1.0 - l) - h_part * l
        if factor < 0.0:
            factor = 0.0
        if factor >= 1.0:
            up_lut.append(min(255, int(round((factor - 1.0) * 255))))
            down_lut.append(0)
        else:
            up_lut.append(0)
            down_lut.append(min(255, int(round((1.0 - factor) * 255))))
    # 3) 权重图：把 L 逐像素映射到增减量
    up_map = lum.point(up_lut).convert("RGB")
    down_map = lum.point(down_lut).convert("RGB")
    # 4) out = rgb - rgb*down + rgb*up（multiply 内部是 a*b/255）
    delta_up = ImageChops.multiply(rgb_img, up_map)
    delta_down = ImageChops.multiply(rgb_img, down_map)
    out = ImageChops.add(ImageChops.subtract(rgb_img, delta_down), delta_up)
    if a is not None:
        return Image.merge("RGBA", (*out.split(), a))
    return out


def _saturation(img, p):
    """全局饱和度：直接复用 Pillow 的 ``ImageEnhance.Color``。

    内部实现是「与灰度图按 factor 混合」：0.0=完全去色（灰度）、1.0=原图、
    2.0=饱和度翻倍。RGBA 下只动前 3 通道，alpha 原样保留。
    """
    f = _num(p, "factor", 1.0)
    if f == 1.0:
        return img
    return ImageEnhance.Color(img).enhance(_clamp(f, 0.0, 3.0))


def _vibrance(img, p):
    """自然饱和度（vibrance）：低饱和像素增益大，高饱和像素增益小。

    公式（逐像素，以该像素 max/min 通道为基准）：
        avg = (max + min) / 2
        s   = (max - min) / 255          # 当前饱和度 0..1
        out = avg + (c - avg) * (1 + t * (1 - s))
    其中 t = factor - 1.0（夹到 ±1）。s→1（已经很艳）时增益→0，所以高饱和
    区域不会被推到溢色；s→0（接近灰）时增益最大。灰色像素（max=min）无论
    怎么调都不动，这是 vibrance 与全局饱和度的关键区别。

    ⚠️ 性能：同样走**全 C 实现**，与 _shadow_highlight 同思路：
      1. ``ImageChops.lighter/darker`` 求 per-pixel 的 max / min（C）
      2. ``ImageChops.add(mx, mn, scale=2.0)`` 得 avg（内部按 double 算，
         不会在 255 处中间截断；不能用 add 后再除，那样会先 clip）
      3. 一张 256 项 LUT 把 s 映射成增益权重（Python 只循环 256 次）
      4. 把 (c-avg) 拆成「高于均值的部分」与「低于均值的部分」两张图，
         再用 ``multiply`` / ``subtract`` / ``add`` 合成（C）
    拆分的原因是 8-bit 图像没有带符号运算：两者在同一通道上互斥
    （c>avg 时后者为 0，反之亦然），所以先减后加不会产生截断误差。
    """
    f = _num(p, "factor", 1.0)
    if f == 1.0:
        return img
    t = _clamp(f - 1.0, -1.0, 1.0)
    has_alpha = img.mode == "RGBA"
    if has_alpha:
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b))
    else:
        rgb = img.convert("RGB")
        a = None

    cr, cg, cb = rgb.split()
    mx = ImageChops.lighter(ImageChops.lighter(cr, cg), cb)
    mn = ImageChops.darker(ImageChops.darker(cr, cg), cb)
    # ⚠️ max/min 都是 L 图，合成后必须 convert("RGB") 才能与原图做
    # ImageChops（模式不一致会报 "images do not match"）。
    avg = ImageChops.add(mx, mn, scale=2.0).convert("RGB")  # (max+min)/2
    sat = ImageChops.difference(mx, mn)       # max-min，即 s*255
    # 权重 = |t| * (1 - s) * 255 = |t| * (255 - sat)
    lut = [min(255, max(0, int(round(abs(t) * (255 - s))))) for s in range(256)]
    wmap = sat.point(lut).convert("RGB")
    up = ImageChops.subtract(rgb, avg)   # (c - avg)+
    dn = ImageChops.subtract(avg, rgb)   # (avg - c)+
    if t >= 0:
        # out = c + up*|t| - dn*|t|（远离灰度 → 更艳）
        out = ImageChops.add(
            ImageChops.subtract(rgb, ImageChops.multiply(dn, wmap)),
            ImageChops.multiply(up, wmap))
    else:
        # out = c - up*|t| + dn*|t|（向灰度收拢 → 更淡）
        out = ImageChops.add(
            ImageChops.subtract(rgb, ImageChops.multiply(up, wmap)),
            ImageChops.multiply(dn, wmap))
    if a is not None:
        return Image.merge("RGBA", (*out.split(), a))
    return out


def _blur(img, p):
    """模糊：半径（像素）+ 滤波器类型。radius<=0 视为不处理。

    三种滤波器：GAUSSIAN（最自然，默认）、BOX（矩形核，最快，大半径时
    会出现方块感）、MEDIAN（中值，保边沿，适合去噪点/去摩尔纹）。

    注：滤波器直接作用于整图（含 alpha）——对不透明图（PNG/JPEG 常见）
    没有区别；对带透明区的图，透明区边缘会一起被模糊成半透明，这是
    「模糊」该有的观感。MEDIAN 不支持 RGBA，遇到时只模糊 RGB 再贴回 alpha。
    """
    radius = _num(p, "radius", 2.0)
    if radius <= 0:
        return img
    radius = _clamp(radius, 0.0, 250.0)
    method = str(p.get("method") or "GAUSSIAN").upper()
    if method == "BOX":
        return img.filter(ImageFilter.BoxBlur(radius))
    if method == "MEDIAN":
        # ⚠️ Pillow 的 MedianFilter 的 size 是**核边长**且必须是奇数（传偶数
        # 直接抛 "bad filter size"），开销随 size² 增长 → 夹到 [1, 9] 的奇数。
        size = int(_clamp(round(radius), 1.0, 9.0))
        if size % 2 == 0:
            size += 1
        has_alpha = img.mode == "RGBA"
        if has_alpha:
            r, g, b, a = img.split()
            rgb = Image.merge("RGB", (r, g, b))
            out = rgb.filter(ImageFilter.MedianFilter(size))
            return Image.merge("RGBA", (*out.split(), a))
        return img.filter(ImageFilter.MedianFilter(size))
    return img.filter(ImageFilter.GaussianBlur(radius))


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
