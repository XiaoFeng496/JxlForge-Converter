# -*- coding: utf-8 -*-
"""Wrapper around the libjxl command-line tools cjxl and djxl.

All code identifiers (variables, functions, classes) and subprocess command
parameters are in English. User-facing messages are in Chinese.
"""

import os
import platform
import shutil
import subprocess


# Module-level handle to the process currently spawned by _run(). Lets a
# caller (e.g. the conversion worker) terminate an in-flight cjxl/djxl run
# when the user presses 停止. Only one conversion runs at a time, so a single
# handle is sufficient.
_current_process = None

# On Windows, console subprocesses (cjxl/djxl) open a visible black console
# window by default. CREATE_NO_WINDOW spawns them without one. The constant is
# only defined on Windows; on other platforms the attribute is absent and we
# fall back to 0 (a no-op flag).
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def terminate_current():
    """Terminate the child process started by the most recent _run() call.

    Safe to call from a different thread than the one running _run(). If no
    process is running (or it already exited) this is a no-op.
    """
    global _current_process
    proc = _current_process
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass


def detect_os():
    """Return a normalized OS id: windows / macos / linux / unknown."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "unknown"


def find_tool(tool_name):
    """Locate an executable in PATH, also trying Windows extensions."""
    found = shutil.which(tool_name)
    if found:
        return found
    if detect_os() == "windows":
        for ext in (".exe", ".cmd", ".bat"):
            candidate = shutil.which(tool_name + ext)
            if candidate:
                return candidate
    return None


def check_tools():
    """Return a dict mapping cjxl / djxl to their paths or None."""
    return {
        "cjxl": find_tool("cjxl"),
        "djxl": find_tool("djxl"),
    }


def encode(
    input_path,
    output_path,
    effort=7,
    distance=None,
    quality=None,
    lossless_jpeg=False,
):
    """Run cjxl to encode ``input_path`` into a JPEG XL file at ``output_path``.

    ``effort`` maps to cjxl's ``-e`` (1..9, default 7). The output fidelity is
    controlled by one of two mutually-exclusive knobs:

    * ``quality`` (0..100, cjxl ``--quality``) — used for lossy / visually
      lossless output; cjxl derives the Butteraugli distance from it.
    * ``distance`` (cjxl ``-d``) — an explicit Butteraugli distance. ``0`` means
      mathematically lossless. ``None`` means "don't pass ``-d`` at all" so cjxl
      applies its own default (which depends on the input type).

    ``lossless_jpeg`` adds ``--lossless_jpeg=1`` so JPEG inputs are re-encoded
    losslessly (bit-identical decode) instead of being re-quantised.
    """
    args = ["cjxl", input_path, output_path, "-e", str(effort)]
    if distance is not None:
        args += ["-d", str(distance)]
    if quality is not None:
        args += ["--quality", str(quality)]
    if lossless_jpeg:
        args += ["--lossless_jpeg=1"]
    return _run(args)


def decode(input_path, output_path):
    """Run djxl to decode a JPEG XL file into output_path."""
    args = ["djxl", input_path, output_path]
    return _run(args)


def _run(args):
    """Execute a command and return (success: bool, message: str).

    Uses :class:`subprocess.Popen` so the child process is spawned explicitly;
    ``communicate()`` blocks until it finishes and captures stdout/stderr
    (functionally equivalent to the old ``subprocess.run`` call, but built on
    Popen as the underlying primitive). The running process is registered in
    ``_current_process`` so it can be interrupted via :func:`terminate_current`.
    """
    global _current_process
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        _current_process = proc
    except FileNotFoundError:
        _current_process = None
        return False, "未找到可执行文件：%s（请确认其已加入系统 PATH）" % args[0]
    except OSError as exc:
        _current_process = None
        return False, "执行命令失败：%s" % exc
    try:
        stdout, stderr = proc.communicate()
    finally:
        _current_process = None
    if proc.returncode != 0:
        detail = (stderr or "").strip() or "未知错误"
        return False, "命令返回错误（退出码 %d）：%s" % (proc.returncode, detail)
    return True, (stdout or "").strip() or "操作成功完成。"
