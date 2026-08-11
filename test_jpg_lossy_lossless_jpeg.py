# -*- coding: utf-8 -*-
"""Regression test: 有损模式对 JPG 输入必须显式传 --lossless_jpeg=0。

新版 cjxl (>=0.12) 把 --lossless_jpeg 默认值改成 1，且禁止在 lossless_jpeg=1
时指定 quality<100，导致 JPG 走有损模式会直接报错退出、零产物。converter.encode
现在对有损 JPG 显式加 --lossless_jpeg=0（解码成像素后做 VarDCT 真有损），老版本
默认本就是 0，显式传 0 冗余但无害，跨版本行为一致。

本测试直接验证 encode 组装出的 cjxl 命令行参数，不真正调用 cjxl 可执行文件。
"""

import sys

from libjxl_gui import converter as conv_mod

# Capture the argv that _run() would hand to subprocess.Popen, instead of
# actually spawning cjxl.
_captured = []
_real_run = conv_mod._run


def _fake_run(args, priority=None):
    _captured.append(list(args))
    return True, "fake cjxl ok"


conv_mod._run = _fake_run

failures = []
total = 0


def check(name, ok):
    global total
    total += 1
    if ok:
        print("PASS  " + name)
    else:
        print("FAIL  " + name)
        failures.append(name)


def build(input_path, **kwargs):
    _captured.clear()
    conv_mod.encode(input_path, "out.jxl", **kwargs)
    return _captured[-1]


# 1) JPG 有损 -> 必须含 --lossless_jpeg=0，且不能出现 =1（否则新版 cjxl 崩溃）。
args = build("photo.jpg", effort=7, quality=90)
check("JPG 有损含 --lossless_jpeg=0", "--lossless_jpeg=0" in args)
check("JPG 有损不含 --lossless_jpeg=1", "--lossless_jpeg=1" not in args)
check("JPG 有损含 --quality 90", "--quality" in args and "90" in args)

# 2) JPG 无损 (-d 0) -> 不传 lossless_jpeg，依赖默认=1 转码（行为正确，不动）。
args = build("photo.jpg", effort=7, distance=0)
check("JPG 无损不含 --lossless_jpeg=0", "--lossless_jpeg=0" not in args)
check("JPG 无损不含 --lossless_jpeg=1", "--lossless_jpeg=1" not in args)

# 3) JPG 无损重编码 -> 显式 --lossless_jpeg=1，且无 quality。
args = build("photo.jpg", effort=7, lossless_jpeg=True)
check("JPG 重编码含 --lossless_jpeg=1", "--lossless_jpeg=1" in args)
check("JPG 重编码不含 --quality", "--quality" not in args)

# 4) 非 JPG 有损 -> 不应出现任何 --lossless_jpeg（无需特殊处理）。
args = build("pic.png", effort=7, quality=90)
check("PNG 有损不含 --lossless_jpeg",
      "--lossless_jpeg=0" not in args and "--lossless_jpeg=1" not in args)

# 5) 大写扩展名 .JPEG 也应识别为 JPG 并加 =0。
args = build("photo.JPEG", effort=7, quality=90)
check("JPG 大写扩展名有损含 --lossless_jpeg=0", "--lossless_jpeg=0" in args)

# 6) .jpeg 扩展名（小写）同样识别。
args = build("photo.jpeg", effort=7, quality=90)
check(".jpeg 扩展名有损含 --lossless_jpeg=0", "--lossless_jpeg=0" in args)

# Restore the real runner so the module is left untouched for other imports.
conv_mod._run = _real_run

print()
print("TOTAL %d, FAIL %d" % (total, len(failures)))
sys.exit(1 if failures else 0)
