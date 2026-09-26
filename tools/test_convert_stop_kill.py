# -*- coding: utf-8 -*-
"""回归测试：转换途中点击「强制停止」，必须即时杀掉在跑的子进程，且不得因原生
编码失败（被杀）而回退到 Pillow 中转重试（否则会再 spawn 一个子进程跑到自然结束，
使「停止」形同虚设）。同时验证被杀后清理半成品输出。

不依赖真实 cjxl：用受控的慢子进程（python -c sleep）走真实
ConvertWorker -> _process_job -> _encode_source -> converter.encode -> _run，
精确复现「被杀 -> 原生 encode 失败 -> 兜底重试会再起进程」的回归路径。

两段式停止：watcher 先 request_stop()（优雅，置位 _stopped 使调度停止并触发
清理分支）再 request_force_stop()（真正杀进程），与真实 UI 的两次点击一致。
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


# 受控慢子进程 + 计数：记录 encode（-> _run）被调用几次，用于断言没有兜底重试。
encode_calls = []


def _fake_encode(src, out_path, **kw):
    encode_calls.append(1)
    # 走真实 _run，但跑一个受控 60s 慢子进程，精确掌控「在途」时长。
    ok, msg, err = conv_mod._run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        priority=kw.get("priority", conv_mod.DEFAULT_PRIORITY),
    )
    return ok, msg, conv_mod.parse_encoding_tag(err)


_real_encode = conv_mod.encode
conv_mod.encode = _fake_encode

tmpdir = tempfile.mkdtemp()
src = os.path.join(tmpdir, "dummy.png")
open(src, "w").close()
out_path = os.path.join(tmpdir, "dummy.jxl")

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
            time.sleep(1.0)
            t_stop[0] = time.time()
            # 两段式：先优雅停止（置位 _stopped，调度停、触发清理分支），
            # 再强制停止（真正杀掉在途子进程）。
            worker.request_stop()
            worker.request_force_stop()
            return
        time.sleep(0.02)


wt = threading.Thread(target=watcher, daemon=True)
wt.start()

worker.start()
worker.wait(90000)
t_finish = time.time()
wt.join(timeout=2)

check("强制停止后 worker 快速结束（< 10s，证明无兜底重试跑到自然结束）",
      t_stop[0] is not None and (t_finish - t_stop[0]) < 10)
check("强制停止机制触发（request_force_stop 已调用）", t_stop[0] is not None)
check("未发生兜底重试（encode 仅被调用 1 次）", len(encode_calls) == 1)
check("半成品输出已清理（停止后无残留 .jxl）",
      not os.path.exists(out_path))

# 复原
conv_mod.encode = _real_encode
shutil.rmtree(tmpdir, ignore_errors=True)

failed = [n for n, ok in results if not ok]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
print("ALL_OK" if not failed else "FAILED: " + ", ".join(failed))
