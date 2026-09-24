# -*- coding: utf-8 -*-
"""回归测试：非 8bit 位深归一化 + RGBA 模糊的透明区防渗色。

覆盖：
  * 16-bit（I;16）输入经 apply_actions 后**按比例**缩放到 8-bit，
    不再被硬裁剪成一片纯白（旧 bug：除 0 外全 255）；
  * 32bit 整型 / 浮点（I / F）按实际极值线性归一化；
  * 8-bit 输入走原路径，结果不变（无回归）；
  * RGBA 模糊走预乘流程，透明区里"未定义"的垃圾 RGB 不再渗入可见区
    （旧 bug：不透明白紧邻 (255,0,0,0) 时白区被染成粉色）；
  * 全不透明图走快路径，结果与直接 filter 完全一致（无回归）；
  * 增强类动作（亮度/对比度/锐化/饱和度）不得改动 alpha。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402  （保证 offscreen 下可构造动作控件）

from PIL import Image  # noqa: E402
from jxlforge import processor as P  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

total = 0
failures = []


def check(name, cond, detail=""):
    global total
    total += 1
    if cond:
        print("PASS  %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s   %s" % (name, detail))


# ---------------------------------------------------------------- 位深
_VALS = [0, 1000, 8000, 32768, 65535]
_EXPECT = [round(v * 255 / 65535) for v in _VALS]   # [0, 4, 31, 128, 255]

g16 = Image.new("I;16", (len(_VALS), 1))
g16.putdata(_VALS)
out16 = P.apply_actions(g16, [{"type": "亮度", "params": {"factor": 1.0}}])
got16 = [out16.getpixel((i, 0))[0] for i in range(len(_VALS))]

check("16-bit 输入最终 mode 为 RGBA", out16.mode == "RGBA", out16.mode)
check("16-bit 不再被硬裁剪成一片纯白",
      not all(v == 255 for v in got16[1:]),
      "got=%s" % got16)
check("16-bit 按比例缩放到 0..255（允许 ±1 取整误差）",
      all(abs(a - b) <= 1 for a, b in zip(got16, _EXPECT)),
      "got=%s expect=%s" % (got16, _EXPECT))
# 单调性：原值递增，结果也必须递增（硬裁剪时会全部撞到 255）
check("16-bit 结果保持单调递增（旧 bug 会从第 2 个值起全平）",
      all(got16[i] <= got16[i + 1] for i in range(len(got16) - 1))
      and got16[1] < got16[-1],
      "got=%s" % got16)

# 32bit 整型：值域未知 → 按实际极值归一化
i32 = Image.new("I", (3, 1))
i32.putdata([0, 500, 1000])
out_i = P.apply_actions(i32, [{"type": "亮度", "params": {"factor": 1.0}}])
got_i = [out_i.getpixel((i, 0))[0] for i in range(3)]
check("32bit 整型按极值归一化到 0..255",
      abs(got_i[0]) <= 1 and abs(got_i[2] - 255) <= 1,
      "got=%s" % got_i)

# 8-bit 输入必须原样（无回归）
rgb8 = Image.new("RGB", (3, 1))
rgb8.putdata([(10, 20, 30), (100, 110, 120), (250, 240, 230)])
out8 = P.apply_actions(rgb8, [{"type": "亮度", "params": {"factor": 1.0}}])
check("8-bit 输入走原路径、像素值不变",
      [out8.getpixel((i, 0))[:3] for i in range(3)]
      == [(10, 20, 30), (100, 110, 120), (250, 240, 230)],
      str([out8.getpixel((i, 0))[:3] for i in range(3)]))
check("8-bit 输入补出不透明 alpha",
      all(out8.getpixel((i, 0))[3] == 255 for i in range(3)))

# ---------------------------------------------------------------- 模糊 / alpha
# 不透明白 紧邻 全透明且 RGB 是垃圾红 (255,0,0,0)
tr = Image.new("RGBA", (8, 1))
tr.putdata([(255, 255, 255, 255)] * 4 + [(255, 0, 0, 0)] * 4)
bl = P._blur(tr, {"radius": 2, "method": "GAUSSIAN"})
px = [bl.getpixel((x, 0)) for x in range(8)]
# 渗色判据：alpha>0 的像素里出现 R 明显高于 G/B（被红色污染）
bleed = any(p[3] > 0 and p[0] > p[2] + 30 for p in px)
check("透明区垃圾 RGB 不再渗入可见区（无偏色晕边）", not bleed, "px=%s" % px)
check("模糊结果仍是白灰阶（R=G=B）",
      all(p[0] == p[1] == p[2] for p in px), "px=%s" % px)
check("透明区 alpha 被模糊成渐变（模糊观感保留）",
      px[0][3] > px[-1][3], "alpha=%s" % [p[3] for p in px])

# 全不透明图必须走快路径、结果与直接 filter 完全一致
op = Image.new("RGBA", (8, 1))
op.putdata([(255, 255, 255, 255)] * 4 + [(0, 0, 0, 255)] * 4)
bo = P._blur(op, {"radius": 2, "method": "GAUSSIAN"})
direct = op.filter(__import__("PIL.ImageFilter", fromlist=["x"]).GaussianBlur(2))
check("全不透明图：预乘是恒等变换，结果与直接模糊一致",
      [bo.getpixel((x, 0)) for x in range(8)]
      == [direct.getpixel((x, 0)) for x in range(8)],
      "got=%s direct=%s" % ([bo.getpixel((x, 0)) for x in range(8)],
                            [direct.getpixel((x, 0)) for x in range(8)]))
check("全不透明图模糊后 alpha 仍全 255",
      all(bo.getpixel((x, 0))[3] == 255 for x in range(8)))

# BOX 与 MEDIAN 同样不得渗色
bl_box = P._blur(tr, {"radius": 2, "method": "BOX"})
check("BOX 模糊也不渗色",
      not any(p[3] > 0 and p[0] > p[2] + 30
              for p in [bl_box.getpixel((x, 0)) for x in range(8)]))
bl_med = P._blur(tr, {"radius": 3, "method": "MEDIAN"})
check("MEDIAN 模糊不渗色且保持原 alpha",
      not any(p[3] > 0 and p[0] > p[2] + 30
              for p in [bl_med.getpixel((x, 0)) for x in range(8)])
      and [bl_med.getpixel((x, 0))[3] for x in range(8)]
      == [tr.getpixel((x, 0))[3] for x in range(8)])

# 增强类动作不得改动 alpha（半透明 100 必须原样保留）
semi = Image.new("RGBA", (2, 1))
semi.putdata([(200, 100, 50, 100)] * 2)
for name, fn, params in (("亮度", P._brightness, {"factor": 0.5}),
                         ("对比度", P._contrast, {"factor": 0.5}),
                         ("锐化", P._sharpen, {"factor": 2.0}),
                         ("饱和度", P._saturation, {"factor": 0.5}),
                         ("自然饱和度", P._vibrance, {"factor": 0.5}),
                         ("曝光", P._exposure, {"ev": 1.0})):
    r = fn(semi, params)
    check("%s：alpha 保持 100 不变" % name,
          r.getpixel((0, 0))[3] == 100,
          "alpha=%s" % r.getpixel((0, 0))[3])

print("\nTOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
