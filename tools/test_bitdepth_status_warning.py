# -*- coding: utf-8 -*-
"""Headless test: 高位深输入降采样到 8-bit 时，状态页应给出提醒。

覆盖两条会丢失高位深的路径：
  * 动作路径（apply_actions 内部 _normalize_to_8bit 把 I;16/I/F 降到 8-bit）
  * Pillow 中转路径（16-bit TIFF -> 临时 PNG 落 8-bit）
8-bit 输入两条路径均不应产生提醒。同时验证提醒确实通过 log_signal 落到状态页。
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import jxlforge.converter as conv_mod
from jxlforge.main_window import ConvertWorker
from PIL import Image

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


tmpdir = tempfile.mkdtemp()

# 让转换引擎无关：stub converter.encode 一律成功。
_real = conv_mod.encode
conv_mod.encode = lambda input_path, output_path, **kw: (True, "操作成功完成。", "")

WARN_KEY = "输入为 %s 高位深图像，已降采样至 8-bit，高位深（HDR）数据不可逆丢失。"
WARN_PREFIX = WARN_KEY.split("%s")[0]


def make_hi16_png(path):
    """16-bit 灰度图存为 PNG，重开后仍为 I;16（高位深）。"""
    Image.new("I;16", (16, 16), 1000).save(path, "PNG")
    return Image.open(path).mode


def make_lo8_png(path):
    Image.new("L", (16, 16), 100).save(path, "PNG")


ACTION = [{"type": "亮度", "enabled": True, "params": {"factor": 1.2}}]


# --- 动作路径：16-bit 输入应提醒，8-bit 不应 ---
src_hi = os.path.join(tmpdir, "hi16.png")
mode = make_hi16_png(src_hi)
check("16-bit PNG 重开模式为 I;16", mode == "I;16")

worker = ConvertWorker([], ACTION)
res = worker._process_job(1, src_hi, os.path.join(tmpdir, "hi16.jxl"), True)
ok, _msg, _tag, _i, _o, _s, _d, warnings = res
check("动作路径 16-bit：转换成功", ok is True)
check("动作路径 16-bit：warnings 含高位深提醒",
      any(WARN_PREFIX in w for w in warnings))
check("动作路径 16-bit：提醒标注 16-bit", any("16-bit" in w for w in warnings))

src_lo = os.path.join(tmpdir, "lo8.png")
make_lo8_png(src_lo)
worker = ConvertWorker([], ACTION)
res = worker._process_job(2, src_lo, os.path.join(tmpdir, "lo8.jxl"), True)
ok, _msg, _tag, _i, _o, _s, _d, warnings = res
check("动作路径 8-bit：转换成功", ok is True)
check("动作路径 8-bit：warnings 为空", warnings == [])


# --- 中转路径：16-bit TIFF 应提醒，8-bit TIFF 不应 ---
src_tif_hi = os.path.join(tmpdir, "hi16.tif")
Image.new("I;16", (16, 16), 2000).save(src_tif_hi, "TIFF")
worker = ConvertWorker([], [])
warnings = []
ok, _m, _t = worker._encode_source(
    src_tif_hi, os.path.join(tmpdir, "h.tif.jxl"), [], warnings)
check("中转路径 16-bit TIFF：转换成功", ok is True)
check("中转路径 16-bit TIFF：warnings 含高位深提醒",
      any(WARN_PREFIX in w for w in warnings))

src_tif_lo = os.path.join(tmpdir, "lo8.tif")
Image.new("L", (16, 16), 100).save(src_tif_lo, "TIFF")
worker = ConvertWorker([], [])
warnings = []
ok, _m, _t = worker._encode_source(
    src_tif_lo, os.path.join(tmpdir, "l.tif.jxl"), [], warnings)
check("中转路径 8-bit TIFF：转换成功", ok is True)
check("中转路径 8-bit TIFF：warnings 为空", warnings == [])


# --- 状态页实际落字：log_signal 收到缩进提醒行 ---
logs = []
worker = ConvertWorker([], ACTION)
worker.log_signal.connect(logs.append)
# _record_result 直接调用，需先初始化 run() 里才设置的统计计数。
worker._stat_processed = 0
worker._stat_in_bytes = 0
worker._stat_out_bytes = 0
worker._stat_ok = 0
worker._stat_err = 0
worker._record_result(3, src_hi, True, "ok", 100, 50, "[tag]", False,
                      [WARN_KEY % "16-bit"])
check("状态页落字：含缩进提醒行",
      any("\t" + (WARN_KEY % "16-bit") == line for line in logs))


conv_mod.encode = _real
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
