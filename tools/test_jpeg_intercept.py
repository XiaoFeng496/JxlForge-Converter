# -*- coding: utf-8 -*-
"""回归测试：JPEG 输出拦截（jxlinfo 检测 + 重建可行性 + A/B 开关）。

覆盖：
1. converter.find_jxlinfo 环境检测（返回路径或 None）。
2. converter.is_lossless_jpeg_jxl：JPEG 重编码 JXL→True，PNG 源 JXL→False；
   jxlinfo 缺失时回退 djxl 仍能正确判定（清空缓存后强制重探测）。
3. _jpeg_recon_action：kept / skip / confirm 三态
   （A=直接跳过，B=弹确认；无法判定时放行 kept）。
4. 设置页开关 jpeg_hard_skip 经 QSettings 持久化往返。

不启动真实转换；检测用真实 cjxl/djxl/jxlinfo（本机已装），GUI 拦截动作以
monkeypatch converter 的探测函数做纯逻辑验证。
"""

import os
import sys
import shutil
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication

# 用独立的应用命名空间隔离 QSettings，避免污染真实用户配置。
QCoreApplication.setOrganizationName("jxlforge_test")
QCoreApplication.setApplicationName("jxlforge_test")
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge import converter as conv
from jxlforge.main_window import MainWindow, _jpeg_recon_action
from PIL import Image

failures = []


def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ---- 准备测试素材 ----
tmp = tempfile.mkdtemp()
try:
    src_jpg = os.path.join(tmp, "src.jpg")
    src_png = os.path.join(tmp, "src.png")
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = ((x * 4) & 255, (y * 4) & 255, (x + y) & 255)
    img.save(src_jpg, "JPEG", quality=90)
    img.save(src_png, "PNG")
    jxl_from_jpg = os.path.join(tmp, "a.jxl")
    jxl_from_png = os.path.join(tmp, "b.jxl")
    subprocess.run(["cjxl", src_jpg, jxl_from_jpg],
                   creationflags=_NO_WINDOW, check=True)
    subprocess.run(["cjxl", src_png, jxl_from_png],
                   creationflags=_NO_WINDOW, check=True)

    # 1) jxlinfo 环境检测：返回路径(存在)或 None，二者皆合法。
    fj = conv.find_jxlinfo()
    check("find_jxlinfo 返回路径(存在)或 None",
          fj is None or (isinstance(fj, str) and os.path.exists(fj)))

    # 2) is_lossless_jpeg_jxl 正确性（默认优先 jxlinfo）。
    check("JPEG 重编码 JXL → True",
          conv.is_lossless_jpeg_jxl(jxl_from_jpg) is True)
    check("PNG 源 JXL → False",
          conv.is_lossless_jpeg_jxl(jxl_from_png) is False)

    # 3) jxlinfo 缺失时回退 djxl 仍正确（清空缓存强制重探测）。
    _orig_find = conv.find_jxlinfo
    conv.find_jxlinfo = lambda: None
    conv._jpeg_recon_cache.clear()
    try:
        check("回退 djxl：JPEG 重编码 JXL → True",
              conv.is_lossless_jpeg_jxl(jxl_from_jpg) is True)
        check("回退 djxl：PNG 源 JXL → False",
              conv.is_lossless_jpeg_jxl(jxl_from_png) is False)
    finally:
        conv.find_jxlinfo = _orig_find
        conv._jpeg_recon_cache.clear()

    # 4) _jpeg_recon_action 三态（monkeypatch 探测结果）。
    _orig_recon = conv.is_lossless_jpeg_jxl

    def _fake(val):
        def f(_p):
            return val
        return f

    conv.is_lossless_jpeg_jxl = _fake(True)
    check("recon=True → kept (A 模式)", _jpeg_recon_action("x.jxl", True) == "kept")
    check("recon=True → kept (B 模式)", _jpeg_recon_action("x.jxl", False) == "kept")
    conv.is_lossless_jpeg_jxl = _fake(False)
    check("recon=False, 硬跳过开 → skip (A)",
          _jpeg_recon_action("x.jxl", True) == "skip")
    check("recon=False, 硬跳过关 → confirm (B)",
          _jpeg_recon_action("x.jxl", False) == "confirm")
    conv.is_lossless_jpeg_jxl = _fake(None)
    check("recon=None → kept (无法判定放行)",
          _jpeg_recon_action("x.jxl", False) == "kept")
    conv.is_lossless_jpeg_jxl = _orig_recon

    # 5) 设置页开关 jpeg_hard_skip 持久化往返。
    # 注意：开关的 toggled 信号会即时触发 _save_conversion_settings（真实用户
    # 每次拨动即保存，属正确行为）。测试模拟“重启”时，用 blockSignals 包住复选框
    # 重置内存态，避免再次保存把已持久化的值覆盖掉。
    w = MainWindow()
    w.jpeg_hard_skip_check.setChecked(True)        # toggled -> 保存 True
    w.jpeg_hard_skip_check.blockSignals(True)
    w.jpeg_hard_skip_check.setChecked(False)       # 不触发保存
    w.jpeg_hard_skip_check.blockSignals(False)
    w._load_conversion_settings()                  # 应从持久化恢复 True
    check("jpeg_hard_skip 往返=True", w.jpeg_hard_skip_check.isChecked() is True)
    w.jpeg_hard_skip_check.setChecked(False)       # toggled -> 保存 False
    w.jpeg_hard_skip_check.blockSignals(True)
    w.jpeg_hard_skip_check.setChecked(True)        # 不触发保存
    w.jpeg_hard_skip_check.blockSignals(False)
    w._load_conversion_settings()                  # 应从持久化恢复 False
    check("jpeg_hard_skip 往返=False", w.jpeg_hard_skip_check.isChecked() is False)

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
