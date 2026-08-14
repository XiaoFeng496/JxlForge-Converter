# -*- coding: utf-8 -*-
"""Headless regression test for the conversion-log ordering fix.

Bug: 在文件级并行池下，_process_job 在「开始时」打印 >>> [N] 头，而大小行在
「完成时」由 _record_result 打印，两路 emit 并发竞争，导致头与大小行串位、大小行
脱钩成堆。修复：头与大小行都在 _record_result（编排线程、任务完成时）连续 emit，
保证成对相邻、不串位。

本测试验证：
  1. _record_result 的两次 emit 顺序固定为 [头, 大小行]（单元级、确定性）。
  2. 真实并行 run() 下，日志中每个 >>> [N] 头都紧接其大小/失败行（集成级）。
"""

import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import libjxl_gui.converter as conv_mod
from libjxl_gui.main_window import ConvertWorker

failures = []


def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        failures.append(name)


# 1. 单元级：_record_result 固定先头后大小行 --------------------------------
class _Collect:
    def __init__(self):
        self.lines = []

    def emit(self, s):
        self.lines.append(s)


worker = ConvertWorker([], [], 7, None, None, False)
col = _Collect()
worker.log_signal = col
worker._total = 5
worker._stat_processed = 0  # run() 中初始化，单元直调需补
worker._stat_in_bytes = 0
worker._stat_out_bytes = 0
worker._stat_ok = 0
worker._stat_err = 0

worker._record_result(3, "/tmp/a.png", True, "ok", 1000, 500, "[VarDCT, q90]")
check("成功：头先于大小行",
      col.lines[0].startswith(">>> [3/5]") and col.lines[1].startswith("\t"))

col.lines.clear()
worker._record_result(1, "/tmp/b.png", False, "boom", 0, 0, "")
check("失败：头先于「处理失败」行",
      col.lines[0].startswith(">>> [1/5]") and col.lines[1].startswith("处理失败"))
check("失败：行尾含错误信息", "boom" in col.lines[1])


# 2. 集成级：真实并行 run() 下无串位 ----------------------------------------
def fake_encode(src, out_path, **kwargs):
    # 随机小延时模拟真实耗时，制造并发完成乱序
    time.sleep(0.01 + (hash(src) % 5) * 0.01)
    try:
        with open(out_path, "wb") as f:
            f.write(b"fake")
    except OSError:
        pass
    return True, "ok"


_real = conv_mod.encode
conv_mod.decode = conv_mod.encode = fake_encode

tmp = tempfile.mkdtemp()
srcs = [os.path.join(tmp, "f%d.png" % i) for i in range(12)]
for p in srcs:
    open(p, "w").close()
jobs = [(p, p + ".jxl", False) for p in srcs]

w2 = ConvertWorker(jobs, [], 7, None, None, False,
                   cpu_cores=4, adv_threads_enabled=False)
logs2 = []
w2.log_signal.connect(lambda s: logs2.append(s))
w2.start()
w2.wait(30000)  # 原生阻塞等待会释放 GIL，让工作线程推进；勿用 processEvents 轮询
for _ in range(200):
    _app.processEvents()

conv_mod.encode = _real
conv_mod.decode = _real

header_idx = [i for i, s in enumerate(logs2) if s.startswith(">>> [")]
orphan = False
for i in header_idx:
    nxt = logs2[i + 1] if i + 1 < len(logs2) else ""
    if not (nxt.startswith("\t") or nxt.startswith("处理失败")):
        orphan = True
        break
check("并行 run()：每个 >>> [N] 头紧接其大小/失败行", not orphan)
check("并行 run()：12 个文件均产出头", len(header_idx) == 12)

shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL_OK")
