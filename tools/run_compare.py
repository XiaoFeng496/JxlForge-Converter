# -*- coding: utf-8 -*-
"""白屏 A/B 对比启动器（Python 交互版，避免 cmd 中文/回车坑）。

依次启动两个 GUI 进程（A = 延迟加载默认版；B = 旧版同步加载），
每个窗口关闭后再启动下一个，便于肉眼对比白屏。所有中文提示都在 Python
层输出（cmd 的 chcp 65001 控制台下稳定），按键等待用 msvcrt.getch()（读
原始按键，按任意键即继续，避开 input()/pause 在共用控制台下的回车失效问题）。

关键健壮性：每个 GUI 用 subprocess.Popen 启动并 poll 等待其退出；若关闭窗口后
进程在 SHUTDOWN_TIMEOUT 秒内仍未退出，判定为"进程未正常退出"并强制 TerminateProcess，
保证对比流程一定能继续（不会因为某个 GUI 进程卡住而永远停在 A）。

用法：双击 run_compare.bat，或直接 `python run_compare.py`。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # 本文件所在目录（tools/）
REPO = os.path.dirname(HERE)                          # 仓库根（main.py 同级）
MAIN = os.path.join(REPO, "main.py")
PY = sys.executable

# 关闭窗口后，最多等待这么久让 GUI 进程自行退出；超时则强制结束，保证对比继续。
SHUTDOWN_TIMEOUT = 5


def launch(label, env_extra):
    """启动一次 GUI，阻塞直到窗口关闭（进程退出或被强制结束）。"""
    env = os.environ.copy()
    env["LIBJXL_BENCH"] = "1"          # 打开首帧计时打印
    env.update(env_extra)              # 覆盖模式开关
    print("\n" + "=" * 60, flush=True)
    print(">>> 正在启动：" + label, flush=True)
    print("    请观察白屏，关闭窗口后自动继续（控制台会打印 [bench] 计时）", flush=True)
    print("=" * 60, flush=True)

    p = subprocess.Popen([PY, MAIN], env=env, cwd=REPO)
    t0 = time.perf_counter()
    try:
        rc = p.wait(timeout=SHUTDOWN_TIMEOUT)
        print("[ok] %s 已退出（退出码=%s，耗时 %.1fs）" % (label, rc, time.perf_counter() - t0), flush=True)
    except subprocess.TimeoutExpired:
        # 关闭窗口后仍不退出：说明该 GUI 进程未正常结束（可能是常驻线程/子窗口）。
        # 强制结束，保证对比流程继续，并打印诊断信息供后续排查。
        print("[诊断] 关闭窗口 %ds 后 %s 仍未退出，强制结束以保证对比继续。" % (SHUTDOWN_TIMEOUT, label), flush=True)
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        print("[诊断] %s 已被强制结束。" % label, flush=True)


def wait_any_key(prompt):
    """等任意键继续；优先 msvcrt.getch（原始按键，可靠），否则退回 input()。"""
    print(prompt, end="", flush=True)
    try:
        if sys.stdin.isatty():
            import msvcrt
            msvcrt.getch()             # 读一个原始按键（含回车），不回显
            return
    except Exception:
        pass
    try:
        input()                        # 兜底（非控制台 / 导入失败时）
    except Exception:
        pass


def main():
    # A：默认优化版（延迟加载）
    launch("A = 延迟加载（默认优化版）", {"LIBJXL_NO_DEFER": "0"})
    wait_any_key("\nA 窗口已关闭。按任意键启动 B（旧版同步加载）...")
    # B：旧版同步加载（三个设置恢复挪回 __init__）
    launch("B = 旧版同步加载（LIBJXL_NO_DEFER=1）", {"LIBJXL_NO_DEFER": "1"})
    wait_any_key("\n对比结束。按任意键关闭窗口。")


if __name__ == "__main__":
    main()
