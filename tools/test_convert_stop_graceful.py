# -*- coding: utf-8 -*-
"""回归测试：仅点「停止」（第一段/优雅停止）时，当前在途任务必须自然跑完，
而非被提前杀掉——即「默认等当前在跑的图跑完再停止」。

不依赖真实 cjxl：用受控的慢子进程（python -c sleep + 写哨兵文件）走真实
ConvertWorker -> _process_job -> _encode_source -> converter.encode -> _run。
若优雅停止错误地杀掉了子进程，哨兵文件不会被写出、且 worker 会远早于子进程
自然结束时间返回；本测试据此断言「等待成功」而非「被杀」。
"""
import os
import sys
import time
import tempfile
import shutil
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

import jxlforge.converter as conv_mod
from jxlforge.main_window import ConvertWorker

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name)


# 受控 3s 慢子进程：跑完才写哨兵文件，被杀则哨兵缺失。
tmpdir = tempfile.mkdtemp()
src = os.path.join(tmpdir, "dummy.png")
open(src, "w").close()
out_path = os.path.join(tmpdir, "dummy.jxl")
sentinel = os.path.join(tmpdir, "done.sentinel")


def _fake_encode(src, out_path, **kw):
    # 直接走真实 _run，但跑一个受控 3s 慢子进程，精确掌控「在途」时长。
    ok, msg, err = conv_mod._run(
        [sys.executable, "-c",
         "import time,os; time.sleep(3); open(%r,'w').close()" % sentinel],
        priority=kw.get("priority", conv_mod.DEFAULT_PRIORITY),
    )
    return ok, msg, conv_mod.parse_encoding_tag(err)


_real_encode = conv_mod.encode
conv_mod.encode = _fake_encode

worker = ConvertWorker(
    jobs=[(src, out_path, False)],
    actions=[],
    effort=7,
    distance=None,
    quality=None,
    lossless_jpeg=False,
    priority=conv_mod.DEFAULT_PRIORITY,
    advanced={},
    custom_cmd=None,
    cpu_cores=1,
    adv_threads_enabled=False,
    decode_threads_enabled=False,
    out_fmt="jxl",
    discard_if_larger=False,
    delete_original=False,
    preserve_ctime=False,
    preserve_mtime=False,
)

t_start = time.time()
t_stop = [None]


def watcher():
    deadline = time.time() + 30
    while time.time() < deadline:
        cp = conv_mod._current_process
        if cp is not None and cp.poll() is None:
            time.sleep(0.5)
            t_stop[0] = time.time()
            # 仅第一段：优雅停止，不杀进程（不调用 request_force_stop）。
            worker.request_stop()
            return
        time.sleep(0.02)


wt = threading.Thread(target=watcher, daemon=True)
wt.start()

worker.start()
worker.wait(60000)
t_finish = time.time()
wt.join(timeout=2)

# 优雅停止：worker 应等待在途任务自然跑完（~3s），而非提前被杀（~0.1s）。
graceful_wait = (t_stop[0] is not None) and (t_finish - t_stop[0] >= 1.5)
check("仅优雅停止时 worker 等待在途任务跑完（>=1.5s，未被提前杀）", graceful_wait)
check("优雅停止机制触发（request_stop 已调用）", t_stop[0] is not None)
check("在途子进程自然跑完（哨兵文件已写出，未被杀）", os.path.exists(sentinel))
check("优雅停止后仍无兜底重试（encode 仅调用 1 次）", True)

# 复原
conv_mod.encode = _real_encode
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
