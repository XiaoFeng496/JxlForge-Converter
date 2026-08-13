# -*- coding: utf-8 -*-
"""Headless tests for the Pillow action processor (no Qt / no rendering)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "libjxl_gui"))
import processor
from PIL import Image

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


def base():
    img = Image.new("RGBA", (200, 100), (10, 20, 30, 255))
    return img


check("Pillow available", processor.AVAILABLE is True)
check("six action types", len(processor.ACTION_TYPES) == 6)
check("watermark positions = 9", len(processor.WATERMARK_POSITIONS) == 9)

# resize: width only -> keep ratio
r = processor.apply_actions(base(), [{"type": "调整大小", "params": {"width": 100, "height": 0}}])
check("resize width=100 -> (100,50)", r.size == (100, 50))

# resize: height only
r = processor.apply_actions(base(), [{"type": "调整大小", "params": {"width": 0, "height": 80}}])
check("resize height=80 -> (160,80)", r.size == (160, 80))

# resize: both -> contain
r = processor.apply_actions(base(), [{"type": "调整大小", "params": {"width": 50, "height": 50}}])
check("resize both=50 contain -> (50,25)", r.size == (50, 25))

# resize: both 0 -> unchanged
r = processor.apply_actions(base(), [{"type": "调整大小", "params": {"width": 0, "height": 0}}])
check("resize both 0 unchanged", r.size == (200, 100))

# rotate 90 -> swapped
r = processor.apply_actions(base(), [{"type": "旋转", "params": {"angle": 90}}])
check("rotate 90 -> (100,200)", r.size == (100, 200))

# rotate 0 -> unchanged
r = processor.apply_actions(base(), [{"type": "旋转", "params": {"angle": 0}}])
check("rotate 0 unchanged", r.size == (200, 100))

# watermark
r = processor.apply_actions(base(), [{"type": "水印", "params": {
    "text": "Hi", "font_size": 32, "opacity": 128, "position": "右下", "color": "white"}}])
check("watermark keeps RGBA", r.mode == "RGBA")
check("watermark keeps size", r.size == (200, 100))

# watermark empty text -> no crash, unchanged size
r = processor.apply_actions(base(), [{"type": "水印", "params": {"text": "  ", "font_size": 32}}])
check("watermark empty text keeps size", r.size == (200, 100))

# brightness / contrast / sharpen keep size
for atype, p in (("亮度/对比度", {"brightness": 2.0, "contrast": 0.5}),
                 ("锐化", {"factor": 2.0})):
    r = processor.apply_actions(base(), [{"type": atype, "params": p}])
    check("%s keeps size" % atype, r.size == (200, 100))

# crop
r = processor.apply_actions(base(), [{"type": "裁剪", "params": {
    "left": 0, "top": 0, "width": 50, "height": 50}}])
check("crop 50x50 -> (50,50)", r.size == (50, 50))

# crop width=0 -> full width, height 50
r = processor.apply_actions(base(), [{"type": "裁剪", "params": {
    "left": 0, "top": 0, "width": 0, "height": 50}}])
check("crop width=0 -> (200,50)", r.size == (200, 50))

# crop both 0 -> unchanged
r = processor.apply_actions(base(), [{"type": "裁剪", "params": {
    "left": 0, "top": 0, "width": 0, "height": 0}}])
check("crop both 0 unchanged", r.size == (200, 100))

# combination: resize then crop
r = processor.apply_actions(base(), [
    {"type": "调整大小", "params": {"width": 100, "height": 0}},
    {"type": "裁剪", "params": {"left": 0, "top": 0, "width": 40, "height": 20}},
])
check("resize+crop -> (40,20)", r.size == (40, 20))

# empty actions -> unchanged
r = processor.apply_actions(base(), [])
check("empty actions unchanged", r.size == (200, 100))
check("empty actions mode RGBA", r.mode == "RGBA")

print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
