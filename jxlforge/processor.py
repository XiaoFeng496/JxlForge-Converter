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
        ImageMath, ImageOps,
    )
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


# ── 动作规格：唯一事实源 ─────────────────────────────────────────────
# 每个动作 = (稳定英文 ID, 中文显示名, 分组 ID)。
#
# ⚠️ 铁律：
#   * 英文 ID **只**用于存盘 / 比较 / 查表，绝不用于显示；
#   * 显示一律 ``i18n.t(ACTION_LABELS[id])``（字典 key 仍是中文显示名，
#     所以改文案只需要改这里 + 补译文，不必动任何存储数据）；
#   * ID 一旦发布**不可再改**——改了旧 ini 里的动作会被静默跳过
#     （apply_actions 没有 else 兜底）。要改文案请只改第 2 列。
#
# 顺序 = 菜单里的展示顺序，按类别排（与 ACTION_GROUPS 一致），不是添加先后。
ACTION_SPECS = [
    # 几何与尺寸
    ("resize", "调整大小", "geometry"),
    ("crop", "裁剪", "geometry"),
    ("rotate", "旋转", "geometry"),
    # 明暗与影调
    ("brightness", "亮度", "tone"),
    ("contrast", "对比度", "tone"),
    ("exposure", "曝光", "tone"),
    ("shadow_highlight", "阴影/高光", "tone"),
    ("normalize", "规格化", "tone"),
    # 色彩
    ("saturation", "饱和度", "color"),
    ("vibrance", "自然饱和度", "color"),
    # 清晰度
    ("sharpen", "锐化", "clarity"),
    ("blur", "模糊", "clarity"),
    # 叠加
    ("watermark", "水印", "overlay"),
]

ACTION_IDS = [spec[0] for spec in ACTION_SPECS]
ACTION_LABELS = {spec[0]: spec[1] for spec in ACTION_SPECS}
ACTION_ID_BY_LABEL = {spec[1]: spec[0] for spec in ACTION_SPECS}

# 历史名字：这里仍是**中文显示名**列表（抽取器 / 旧测试依赖它）。
# 需要内部 ID 请用 ``ACTION_IDS``；需要某语言的显示名用
# ``i18n.t(ACTION_LABELS[id])``。
ACTION_TYPES = [spec[1] for spec in ACTION_SPECS]

# 「添加动作 ▶」菜单的分组：组名是中文显示名，组内**存英文稳定 ID**。
# 组内不再抄一遍中文动作名——那份重复曾导致「改了 SPECS 忘了改这里 →
# 菜单构建时 ACTION_ID_BY_LABEL 查不到 → KeyError，窗口直接建不出来」。
# 现在改显示名只动 ACTION_SPECS 第 2 列 + 译文，本表不受影响。
# ⚠️ 组内动作必须覆盖 ACTION_IDS 且一一对应、不重不漏：菜单按顺序渲染，测试对账。
ACTION_GROUPS = [
    ("几何与尺寸", ["resize", "crop", "rotate"]),
    ("明暗与影调", ["brightness", "contrast", "exposure", "shadow_highlight",
                    "normalize"]),
    ("色彩", ["saturation", "vibrance"]),
    ("清晰度", ["sharpen", "blur"]),
    ("叠加", ["watermark"]),
]

#: 动作列表持久化格式版本。1 = 中文 ID（旧），2 = 英文稳定 ID。
ACTIONS_SCHEMA_VERSION = 2

# Sensible defaults per action type (also used by the param widget builder).
# 键是英文稳定 ID（与 ACTION_IDS 一致），不是显示名。
DEFAULT_PARAMS = {
    "resize": {"width": 0, "height": 0, "algorithm": "LANCZOS"},
    "rotate": {"angle": 90, "expand": True},
    "watermark": {
        "text": "Sample", "font_size": 32, "opacity": 128,
        "position": "bottom_right", "color": "white",
    },
    # 旧版「亮度/对比度」已拆成「亮度」「对比度」两个独立动作（2026-09-09）。
    # 这个键只用于兼容已存进 ini 的老数据（见 migrate_legacy_actions），
    # 菜单里不再出现，新代码不要再用它。
    "brightness_contrast": {"brightness": 1.0, "contrast": 1.0},
    "brightness": {"factor": 1.0},
    "contrast": {"factor": 1.0},
    "sharpen": {"factor": 1.5},
    "crop": {"left": 0, "top": 0, "width": 0, "height": 0},
    # 规格化：cutoff=0 等价于纯拉满直方图，cutoff=N 截掉各端 N‰ 的极值再拉满。
    # 0 是默认（与「规格化」字面意义最贴），>0 用于避开噪点/过曝像素。
    "normalize": {"cutoff": 0},
    # 曝光：以 EV（曝光档位）为单位。0=不变，+1=亮一倍（2.0x），-1=暗一半（0.5x）。
    # 范围 [-3, +3] 步进 0.1，便于细调。
    "exposure": {"ev": 0.0},
    # 阴影/高光：分别调整暗部（阴影）与亮部（高光）的亮度系数。
    # 1.0=不变；>1.0 提亮阴影 / 压低高光，<1.0 反之。范围 [0.0, 2.0]。
    "shadow_highlight": {"shadow": 1.0, "highlight": 1.0},
    # 饱和度：全局均匀调整，等价于「与灰度图按 factor 混合」。
    # 1.0=不变，0.0=完全去色（灰度），>1.0 更浓。范围 [0.0, 3.0]。
    "saturation": {"factor": 1.0},
    # 自然饱和度（vibrance）：只强化低饱和像素，已经够艳的像素几乎不动，
    # 因此不会像全局饱和度那样把肤色/天空一次性推到溢色。
    # 同样以 1.0=不变、0.0=完全去色，范围 [0.0, 2.0]（内部增量夹到 ±1）。
    "vibrance": {"factor": 1.0},
    # 模糊：radius 为半径（像素），0=不处理；method 见 BLUR_METHODS。
    "blur": {"radius": 2.0, "method": "GAUSSIAN"},
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
# 显示标签只留算法名（描述见 toolTip / 下拉项自然顺序），避免长标签把
# 下拉框撑到 200+px 宽（见 main_window._build_param_widgets 的下拉宽度约束）。
RESIZE_ALGORITHMS = [
    ("LANCZOS", "Lanczos"),
    ("BICUBIC", "双三次"),
    ("BILINEAR", "双线性"),
    ("BOX", "方框"),
    ("HAMMING", "汉明"),
    ("NEAREST", "最近邻"),
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

# 水印九宫格位置：稳定英文 ID + 中文显示名，顺序 = 下拉里的展示顺序。
# 与动作类型同一条铁律：存盘/查表用 ID，显示用 t(label)。
WATERMARK_POSITION_SPECS = [
    ("top_left", "左上"), ("top_center", "中上"), ("top_right", "右上"),
    ("middle_left", "左中"), ("center", "居中"), ("middle_right", "右中"),
    ("bottom_left", "左下"), ("bottom_center", "中下"), ("bottom_right", "右下"),
]
WATERMARK_POSITION_IDS = [p[0] for p in WATERMARK_POSITION_SPECS]
WATERMARK_POSITION_LABELS = {p[0]: p[1] for p in WATERMARK_POSITION_SPECS}
WATERMARK_POSITION_ID_BY_LABEL = {p[1]: p[0] for p in WATERMARK_POSITION_SPECS}
# 兼容旧引用：中文显示名列表（旧测试 / 抽取器依赖）。
WATERMARK_POSITIONS = [p[1] for p in WATERMARK_POSITION_SPECS]


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
    # 兼容兜底：旧配置可能还带中文位置（正常路径下 migrate 已转换）。
    # 用冻结的旧映射，不用当前标签反查——见 LEGACY_WATERMARK_POSITION_MAP 的说明。
    position = LEGACY_WATERMARK_POSITION_MAP.get(position, position)
    hmap = {"left": margin, "center": (img_w - tw) // 2,
            "right": img_w - tw - margin}
    vmap = {"top": margin, "middle": (img_h - th) // 2,
            "bottom": img_h - th - margin}
    grid = {
        "top_left": ("left", "top"), "top_center": ("center", "top"),
        "top_right": ("right", "top"),
        "middle_left": ("left", "middle"), "center": ("center", "middle"),
        "middle_right": ("right", "middle"),
        "bottom_left": ("left", "bottom"), "bottom_center": ("center", "bottom"),
        "bottom_right": ("right", "bottom"),
    }
    hpos, vpos = grid.get(position, ("right", "bottom"))
    return _clamp(hmap[hpos], 0, img_w), _clamp(vmap[vpos], 0, img_h)


#: 旧中文动作类型 → 英文稳定 ID（读取 v1 配置时用）。
#:
#: ⚠️ 这是**冻结的历史快照**，不是 ``ACTION_ID_BY_LABEL`` 的别名，两者必须分开维护。
#: 原因：显示名会随文案优化而变（例如「调整大小」改成「缩放」），但旧 ini 里已经
#: 落盘的中文串不会跟着变。如果迁移表继续由当前标签反查派生，改名当天所有老用户
#: 的动作就会匹配不上——``apply_actions`` 只会 warn 一句然后跳过，用户拿到的是
#: 一张没处理的图，属于**静默失效**。
#:
#: 维护规则：
#:   * 只收 schema v1（中文 ID）时代真实落过盘的中文串，**永不追加新动作**；
#:   * 显示名改名时，改 ``ACTION_SPECS`` 第 2 列，**不动这里**；
#:   * 新增动作只进 ``ACTION_SPECS``，不会出现在本表（新数据一律写英文 ID）。
#:
#: ``tools/test_i18n_id_separation.py`` 会在显示名与下表不一致时报警，
#: 提醒你确认「新文案 + 旧映射」是否都照顾到了。
LEGACY_ACTION_TYPE_MAP = {
    # v1 的 13 个动作（当时的显示名）
    "调整大小": "resize",
    "裁剪": "crop",
    "旋转": "rotate",
    "亮度": "brightness",
    "对比度": "contrast",
    "曝光": "exposure",
    "阴影/高光": "shadow_highlight",
    "规格化": "normalize",
    "饱和度": "saturation",
    "自然饱和度": "vibrance",
    "锐化": "sharpen",
    "模糊": "blur",
    "水印": "watermark",
    # 更早的合并动作：2026-09 拆成 brightness + contrast，仍是 v1 合法值
    "亮度/对比度": "brightness_contrast",
}

#: 旧中文水印位置 → 英文稳定 ID（冻结值，理由同上）。
LEGACY_WATERMARK_POSITION_MAP = {
    "左上": "top_left",
    "中上": "top_center",
    "右上": "top_right",
    "左中": "middle_left",
    "居中": "center",
    "右中": "middle_right",
    "左下": "bottom_left",
    "中下": "bottom_center",
    "右下": "bottom_right",
}


def migrate_legacy_actions(actions):
    """把旧版动作列表升级到当前 schema（读 QSettings 时调用一次即可，幂等）。

    两类规则：
      1. 旧中文 ID → 英文稳定 ID（schema v2，2026-10）；
      2. 旧版「亮度/对比度」拆成「亮度」+「对比度」两个动作。

    ⚠️ 必须幂等：已经是英文 ID 的动作原样通过，不能重复拆分。
    """
    out = []
    for a in actions or []:
        if not isinstance(a, dict):
            out.append(a)
            continue
        atype = LEGACY_ACTION_TYPE_MAP.get(a.get("type"), a.get("type"))
        if atype == "brightness_contrast":
            p = a.get("params", {}) or {}
            for new_type, key in (("brightness", "brightness"),
                                  ("contrast", "contrast")):
                item = dict(a)
                item["type"] = new_type
                params = dict(p)
                params.pop("brightness", None)
                params.pop("contrast", None)
                params["factor"] = _num(p, key, 1.0)
                item["params"] = params
                out.append(item)
            continue
        item = dict(a)
        item["type"] = atype
        params = dict(a.get("params", {}) or {})
        # 水印位置：旧中文 → 英文 ID（英文值原样保留）。
        pos = params.get("position")
        if atype == "watermark" and pos in LEGACY_WATERMARK_POSITION_MAP:
            params["position"] = LEGACY_WATERMARK_POSITION_MAP[pos]
        item["params"] = params
        out.append(item)
    return out


def _normalize_to_8bit(img):
    """把非 8bit 输入**按比例**归一化到 0..255；8bit 输入原样返回。

    ⚠️ 不能直接 ``convert("RGBA")``：Pillow 对 ``I;16``/``I``/``F`` 转 8-bit 是
    **硬裁剪**到 0..255，不是按比例缩放。实测 16-bit 值 [0,1000,8000,32768,65535]
    直接转会得到 [0,255,255,255,255]——除 0 外全部纯白，图像被彻底毁掉。

    做法：
      1. 先无损提升到 ``I``（32bit）——实测 ``I;16 → I`` 值完全不变。
      2. 再用**线性** ``point()`` 按比例缩放到 0..255。线性 lambda 会被 Pillow 的
         ``_getscaleoffset`` 识别成 scale/offset 而走 **C 快路径**（实测
         2560×1440 仅 0.004s）。⚠️ 一旦写成非线性（带 round/min/max 或查表），
         就会退化成逐像素 Python 回调，慢两个数量级——正是本项目此前优化掉的坑。

    16-bit 按**标称**值域 0..65535 缩放（不做极值拉伸，保持原有明暗观感）；
    ``I``/``F`` 值域未知（HDR 常见 0..1 或任意范围），按实际极值线性归一化。
    """
    mode = img.mode
    if mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        return img.convert("I").point(lambda v: v * 255 / 65535)
    if mode in ("I", "F"):
        lo, hi = img.getextrema()
        if not hi or (lo >= 0 and hi <= 255):
            return img
        span = float(hi - lo) or 1.0
        return img.point(lambda v: (v - lo) * 255 / span)
    return img


def is_high_bit_depth(img):
    """返回 ``img`` 的高位深标签（``'16-bit'`` / ``'32-bit'`` / ``'浮点'``），非高位深返回 None。

    与 :func:`_normalize_to_8bit` 覆盖的模式一致（``I;16*`` / ``I`` / ``F``）。这些
    模式经 :func:`apply_actions` 会按比例降采样到 8-bit；经 Pillow 中转保存
    （如 16-bit TIFF → 临时 PNG）也会落到 8-bit——都是**不可逆**的。状态页据此提醒用户。
    """
    mode = getattr(img, "mode", None)
    if mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        return "16-bit"
    if mode == "I":
        return "32-bit"
    if mode == "F":
        return "浮点"
    return None


def apply_actions(image, actions):
    """Apply each action in ``actions`` (in order) to ``image`` and return the
    resulting ``PIL.Image`` (mode RGBA)."""
    if not AVAILABLE:
        raise RuntimeError(i18n.t("Pillow 未安装，无法执行图像处理动作。"))
    # ⚠️ 必须先做位深归一化再转 RGBA，否则非 8bit 输入会被硬裁剪成一片纯白。
    img = _normalize_to_8bit(image)
    img = img.convert("RGBA")
    for action in actions:
        atype = action.get("type")
        params = action.get("params", {}) or {}
        # ⚠️ 这里比较的是**英文稳定 ID**（schema v2）。旧数据在
        # migrate_legacy_actions 里已升级；若仍落到 else，说明 ID 漂移，
        # 必须显式告警——静默跳过会让用户拿到一张"没处理"的原图。
        if atype == "resize":
            img = _resize(img, params)
        elif atype == "rotate":
            img = _rotate(img, params)
        elif atype == "watermark":
            img = _watermark(img, params)
        elif atype == "brightness_contrast":
            # 旧数据兼容（见 DEFAULT_PARAMS 的注释）；菜单已不再提供。
            img = _brightness_contrast(img, params)
        elif atype == "brightness":
            img = _brightness(img, params)
        elif atype == "contrast":
            img = _contrast(img, params)
        elif atype == "sharpen":
            img = _sharpen(img, params)
        elif atype == "crop":
            img = _crop(img, params)
        elif atype == "normalize":
            img = _normalize(img, params)
        elif atype == "exposure":
            img = _exposure(img, params)
        elif atype == "shadow_highlight":
            img = _shadow_highlight(img, params)
        elif atype == "saturation":
            img = _saturation(img, params)
        elif atype == "vibrance":
            img = _vibrance(img, params)
        elif atype == "blur":
            img = _blur(img, params)
        else:
            import warnings
            warnings.warn(
                "unknown action type %r skipped (stale config? run migration)"
                % (atype,), RuntimeWarning, stacklevel=2)
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


def _blur_rgba_premultiplied(img, flt, blur_alpha=True):
    """RGBA 模糊：走**预乘**流程，避免透明区里"未定义"的垃圾 RGB 渗入可见区。

    ⚠️ 根因：Pillow 的滤镜对 RGBA **不做预乘**，透明像素里那些未定义的垃圾 RGB
    会被当成真实颜色参与卷积。实测不透明白 (255,255,255,255) 紧邻全透明
    (255,0,0,0) 时，高斯模糊 r=2 会把白区染成粉色 (255,244,244,244)、(255,196,…)。

    正确做法（预乘 → 模糊 → 反预乘）：
      1. ``prem = rgb * a / 255``    —— ImageChops.multiply（C），垃圾色被 alpha 归零
      2. ``P    = blur(prem)``       —— 在预乘空间模糊
      3. ``A    = blur(a)`` 或 ``a`` —— alpha 单独模糊（中值模糊时保持原 alpha）
      4. ``out  = P * 255 / A``      —— 反预乘，逐像素除法

    第 4 步 Pillow 的 ImageChops **没有除法**，但 ``ImageMath.unsafe_eval`` 可以：
      * Pillow 12 把旧的 ``ImageMath.eval`` 改名成 ``unsafe_eval``（``eval`` 已移除），
        故两个名字都兼容取一下，取不到就退回不预乘的原行为。
      * 只支持单通道（传 'RGB' 会报 unsupported mode），故逐通道算。
      * 溢出是**钳位**不是回绕（实测 200*255/50 → 255），除以 0 得 0
        （透明处 RGB 本来就无意义，不会崩）。
      * 实测单通道 2560×1440 约 0.047s，三通道 ~0.14s，可接受。
    """
    eval_fn = getattr(ImageMath, "unsafe_eval", None) or getattr(ImageMath, "eval", None)
    if eval_fn is None:
        return img.filter(flt)

    r, g, b, a = img.split()
    if a.getextrema()[0] >= 255:
        # 全不透明：预乘是恒等变换，无需做（快路径，覆盖绝大多数图）
        return img.filter(flt)

    rgb = Image.merge("RGB", (r, g, b))
    alpha3 = Image.merge("RGB", (a, a, a))
    prem = ImageChops.multiply(rgb, alpha3)
    P = prem.filter(flt)
    A = a.filter(flt) if blur_alpha else a
    out_bands = [eval_fn("convert(a * 255 / b, 'L')", a=band, b=A)
                 for band in P.split()]
    return Image.merge("RGBA", (*out_bands, A))


def _blur(img, p):
    """模糊：半径（像素）+ 滤波器类型。radius<=0 视为不处理。

    三种滤波器：GAUSSIAN（最自然，默认）、BOX（矩形核，最快，大半径时
    会出现方块感）、MEDIAN（中值，保边沿，适合去噪点/去摩尔纹）。

    带透明区的图走 :func:`_blur_rgba_premultiplied`（预乘 → 模糊 → 反预乘），
    否则透明区的垃圾 RGB 会被卷进可见区产生偏色晕边。MEDIAN 不支持 RGBA，
    故 alpha 单独处理（保持原 alpha，不参与模糊）。
    """
    radius = _num(p, "radius", 2.0)
    if radius <= 0:
        return img
    radius = _clamp(radius, 0.0, 250.0)
    method = str(p.get("method") or "GAUSSIAN").upper()
    if method == "BOX":
        flt = ImageFilter.BoxBlur(radius)
    elif method == "MEDIAN":
        # ⚠️ Pillow 的 MedianFilter 的 size 是**核边长**且必须是奇数（传偶数
        # 直接抛 "bad filter size"），开销随 size² 增长 → 夹到 [1, 9] 的奇数。
        size = int(_clamp(round(radius), 1.0, 9.0))
        if size % 2 == 0:
            size += 1
        flt = ImageFilter.MedianFilter(size)
        # 中值模糊保持原 alpha（与既有行为一致），但仍需预乘防垃圾色渗入。
        if img.mode == "RGBA":
            return _blur_rgba_premultiplied(img, flt, blur_alpha=False)
        return img.filter(flt)
    else:
        flt = ImageFilter.GaussianBlur(radius)
    if img.mode == "RGBA":
        return _blur_rgba_premultiplied(img, flt, blur_alpha=True)
    return img.filter(flt)


def _watermark(img, p):
    text = (p.get("text", "") or "").strip()
    if not text:
        return img
    size = int(p.get("font_size", 32) or 32)
    opacity = _clamp(int(p.get("opacity", 128) or 128), 0, 255)
    position = p.get("position", "bottom_right") or "bottom_right"
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
