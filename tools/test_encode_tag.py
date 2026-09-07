# -*- coding: utf-8 -*-
"""回归测试：_encode_tag 依据输出格式返回正确标签。

Bug：输出格式为 JPEG / PNG（走 djxl 解码/重建，不经 cjxl 编码）时，
_record_result 回退到 _encode_tag，而旧实现完全忽略输出格式，按 JXL 有损
参数推导出错误的 [VarDCT, q90] 出现在日志。修复：非 JXL 输出返回重建标签，
且真实 cjxl 抓取优先（tag 非空时直接用它）。

本测试覆盖 _encode_tag 的规则分支，不启动任何真实 cjxl/djxl 二进制。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge.main_window import ConvertWorker

failures = []


def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


# 输出 JPEG：无论编码参数如何，都不应出现 JXL 标签，应返回重建标签。
w_jpg = ConvertWorker([], [], 7, None, 90, False, out_fmt="jpg")
check("JPEG 输出：返回 [JPEG 重建]（而非错误的 [VarDCT, q90]）",
      w_jpg._encode_tag() == "[JPEG 重建]")

# 输出 PNG：同理返回重建标签。
w_png = ConvertWorker([], [], 7, None, 90, False, out_fmt="png")
check("PNG 输出：返回 [PNG 重建]", w_png._encode_tag() == "[PNG 重建]")

# 输出 JXL 有损：兜底逻辑保持原 JXL 标签（真实抓取优先于它）。
w_jxl_lossy = ConvertWorker([], [], 7, None, 90, False, out_fmt="jxl")
check("JXL 有损：兜底仍返回 [VarDCT, q90]",
      w_jxl_lossy._encode_tag() == "[VarDCT, q90]")

# 输出 JXL 无损：保持 [Modular, lossless]。
w_jxl_lossless = ConvertWorker([], [], 7, 0, None, False, out_fmt="jxl")
check("JXL 无损：返回 [Modular, lossless]",
      w_jxl_lossless._encode_tag() == "[Modular, lossless]")

# 自定义命令优先级最高，即使输出格式为 JPEG 也优先返回 [自定义命令]。
w_custom = ConvertWorker([], [], 7, None, None, False, out_fmt="jpg",
                         custom_cmd="echo x")
check("自定义命令 + JPEG 输出：优先 [自定义命令]",
      w_custom._encode_tag() == "[自定义命令]")

# 未显式传 out_fmt 时默认 jxl，保持历史行为不被破坏。
w_default = ConvertWorker([], [], 7, None, 90, False)
check("未传 out_fmt：默认 jxl（兜底 [VarDCT, q90]）",
      w_default._encode_tag() == "[VarDCT, q90]")

print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
