# -*- coding: utf-8 -*-
"""Headless 单元测试：jxlforge.formats.pbm_to_ppm_bytes（PBM → PPM 灰度解码）。

PBM 是 PNM 系列的 1-bit 单色位图，分两类：
- ``P1`` ASCII：像素 0/1 以空白分隔、可跨行、可无空格连续写。
- ``P4`` 二进制：每字节 8 像素、高位优先，末行不足一字节补在低位。

极性按规范 ``1``=黑→0、``0``=白→255。本测试覆盖标准/注释/紧贴像素/连续写/
末行补齐/错误路径，确保与 cjxl 原生支持的 PBM 输入在 GUI 缩略图环节一致。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from jxlforge import formats

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


def _decode(name, data):
    """把字节写临时文件并调用 pbm_to_ppm_bytes，返回 (ok, pixels_or_None)。"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    try:
        out = formats.pbm_to_ppm_bytes(p)
    except Exception:
        return False, None
    if not out.startswith(b"P5\n"):
        return False, None
    # 头 "P5\n<w> <h>\n255\n" 固定 11 字节（宽高均为 1 位数的用例），取像素。
    return True, list(out[11:])


def _expect(name, data, expect_pixels):
    ok, px = _decode(name, data)
    check("PBM %s 解码成功" % name, ok)
    check("PBM %s 像素极性/尺寸正确" % name, px == expect_pixels)


# --- 标准 P1 / P4（2x2：黑 白 / 白 黑）---
_expect("P1 标准", b"P1\n2 2\n10\n01\n", [0, 255, 255, 0])
_expect("P4 标准", b"P4\n2 2\n" + bytes([0b10000000, 0b01000000]),
        [0, 255, 255, 0])

# --- P1 头部带 # 注释行（PNM 允许）---
_expect("P1 注释行", b"P1\n# a comment\n2 2\n1 0\n0 1\n", [0, 255, 255, 0])

# --- P4 宽高后无换行、像素紧贴（无头部尾部空白）---
_expect("P4 紧贴像素", b"P4\n3 2" + bytes([0b10100000, 0b00000000]),
        [0, 255, 0, 255, 255, 255])

# --- P1 像素连续写（无空格分隔）---
_expect("P1 连续写", b"P1\n2 2\n1001\n", [0, 255, 255, 0])

# --- 错误路径：文件过小 / magic 不符 / 头缺宽高 / 像素不完整 ---
def _raises(name, data):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    try:
        formats.pbm_to_ppm_bytes(p)
        check("PBM %s 应抛 ValueError" % name, False)
    except ValueError:
        check("PBM %s 应抛 ValueError" % name, True)
    except Exception:
        check("PBM %s 应抛 ValueError" % name, False)


_raises("过小", b"P1")
_raises("magic错", b"P9\n2 2\n10\n01\n")
_raises("缺宽高", b"P1\nxx\n")
_raises("像素不全", b"P4\n2 2\n" + bytes([0b10000000]))  # 缺第二行字节


if __name__ == "__main__":
    failed = [n for n, c in results if not c]
    if failed:
        print("\nFAILED %d / %d" % (len(failed), len(results)))
        sys.exit(1)
    print("\nALL PASS %d" % len(results))
