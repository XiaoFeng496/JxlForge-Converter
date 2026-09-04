# -*- coding: utf-8 -*-
"""跑 tools/ 下所有 test_*.py，汇总结果。

存在的理由
----------
60 多个测试文件，靠人记「改完该跑哪几个」迟早会漏。改成一条命令全跑，
失败了它会把**每个失败文件的最后几行**贴出来，不用再去翻日志。

用法::

    python tools/run_tests.py                 # 全跑
    python tools/run_tests.py i18n            # 只跑名字含 i18n 的
    python tools/run_tests.py --timeout 300   # 放宽单文件超时

退出码：全部通过为 0，有失败为 1。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
PY = sys.executable


def discover(pattern=None):
    files = []
    for name in sorted(os.listdir(HERE)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if pattern and pattern not in name:
            continue
        files.append(name)
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量跑 tools/ 下的测试")
    ap.add_argument("pattern", nargs="?", default=None,
                    help="只跑文件名含此子串的测试")
    ap.add_argument("--timeout", type=int, default=180,
                    help="单个测试文件的超时秒数（默认 180）")
    ap.add_argument("--tail", type=int, default=6,
                    help="失败时贴出最后几行输出（默认 6）")
    args = ap.parse_args(argv)

    files = discover(args.pattern)
    if not files:
        print("没有匹配的测试文件（pattern=%r）" % args.pattern)
        return 1

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    ok, bad, slow = [], [], []
    t0 = time.time()
    for name in files:
        path = os.path.join(HERE, name)
        try:
            r = subprocess.run([PY, path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               env=env, timeout=args.timeout, cwd=REPO_ROOT)
        except subprocess.TimeoutExpired:
            slow.append(name)
            print("  ⏱  %s（超时 %ds）" % (name, args.timeout))
            continue
        if r.returncode == 0:
            ok.append(name)
        else:
            bad.append((name, r.returncode, (r.stdout or "") + (r.stderr or "")))

    dt = time.time() - t0
    print()
    print("=" * 60)
    print("通过 %d / 失败 %d / 超时 %d   用时 %.1fs"
          % (len(ok), len(bad), len(slow), dt))
    print("=" * 60)

    for name, rc, out in bad:
        print()
        print("❌ %s  (退出码 %d)" % (name, rc))
        lines = [ln for ln in out.strip().splitlines() if ln.strip()]
        fail_lines = [ln for ln in lines if "FAIL" in ln or "Error" in ln
                      or "Traceback" in ln]
        show = (fail_lines[-args.tail:] if fail_lines
                else lines[-args.tail:])
        for ln in show:
            print("     %s" % ln)

    for name in slow:
        print("  ⏱  超时：%s" % name)

    return 1 if (bad or slow) else 0


if __name__ == "__main__":
    sys.exit(main())
