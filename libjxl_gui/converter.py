# -*- coding: utf-8 -*-
"""Wrapper around the libjxl command-line tools cjxl and djxl.

All code identifiers (variables, functions, classes) and subprocess command
parameters are in English. User-facing messages are in Chinese.
"""

import os
import platform
import shutil
import subprocess
import re

# cjxl 编码时向 stderr 打印 "Encoding [<codec>, <mode>, effort: N]"，
# 抓取方括号内的完整原文作为状态页每文件的编码模式标签。
_ENCODING_TAG_RE = re.compile(r"Encoding\s*\[([^\]]+)\]")


# Module-level handle to the process currently spawned by _run(). Lets a
# Handle(s) to the child process started by the most recent _run() call, so the
# conversion worker can terminate an in-flight cjxl/djxl run when the user
# presses 停止. The parallel pool can run several conversions at once, so every
# live process is tracked in _active_processes and terminate_current() kills all
# of them; _current_process keeps pointing at the most-recently started one.
_current_process = None
_active_processes = set()

# On Windows, console subprocesses (cjxl/djxl) open a visible black console
# window by default. CREATE_NO_WINDOW spawns them without one. The constant is
# only defined on Windows; on other platforms the attribute is absent and we
# fall back to 0 (a no-op flag).
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Windows process-priority classes, used to throttle the cjxl/djxl child
# processes so a large batch does not hog the CPU. The constants only exist
# on Windows; elsewhere they fall back to 0 (a no-op) and the priority is
# simply ignored. Keyed by a stable string so the value can be persisted in
# QSettings and stay readable across platforms.
_PRIORITY_FLAGS = {
    "idle": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0),
    "below_normal": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
    "normal": getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0),
    "above_normal": getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0),
    "high": getattr(subprocess, "HIGH_PRIORITY_CLASS", 0),
}
DEFAULT_PRIORITY = "below_normal"


def terminate_current():
    """Terminate every child process currently spawned by this module.

    The parallel pool may run several cjxl/djxl processes at once, so this kills
    all of them (not just the most recent). Safe to call from a different thread
    than the ones running _run(). Processes that already exited are skipped.
    """
    global _current_process
    for proc in list(_active_processes):
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
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


def get_cjxl_version():
    """Return the cjxl version banner, e.g.

    ``JPEG XL encoder v0.12.0 4128790 [_AVX2_,SSE4,SSE2] {Clang 22.1.3}``

    ``--version`` prints ``cjxl v0.12.0 ...``; we normalize the leading
    ``cjxl `` to ``JPEG XL encoder `` so the banner matches the first line
    emitted by running cjxl with no arguments. Returns ``None`` when cjxl is
    missing or its version cannot be read.
    """
    path = find_tool("cjxl")
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, ValueError):
        return None
    line = (result.stdout or result.stderr or "").strip().splitlines()
    if not line:
        return None
    banner = line[0].strip()
    if banner.startswith("cjxl "):
        banner = "JPEG XL encoder " + banner[len("cjxl "):]
    return banner


def build_args(
    input_path,
    output_path,
    effort=7,
    distance=None,
    quality=None,
    lossless_jpeg=None,
    progressive=False,
    modular=None,
    num_threads=None,
    brotli_effort=None,
    epf=None,
    noise=None,
    resampling=None,
    container=None,
    codestream_level=None,
    faster_decoding=None,
):
    """Assemble the cjxl argument list for an encode, WITHOUT running it.

    This is the single source of truth for how UI/worker settings map to cjxl
    flags. ``encode`` delegates to it, and the GUI's command-preview bar reuses
    it with placeholder paths so the user sees exactly what will run.

    ``lossless_jpeg`` here is the *resolved* flag value (``None`` / ``0`` / ``1``)
    — ``None`` means "don't pass the flag". ``encode`` computes that value from
    its boolean ``lossless_jpeg`` argument plus the JPEG auto-detection rule.

    Every advanced knob defaults to "don't pass" so callers that omit it get the
    same command as before:
      * ``progressive`` — pure switch: ``--progressive`` only when truthy.
      * ``modular`` / ``container`` — value-bool switches: pass e.g.
        ``--modular=1``; never pass ``=0`` (cjxl rejects bare ``--modular`` and
        ``=0`` is meaningless noise). ``None`` = omit.
      * ``faster_decoding`` — multi-level int flag ``--faster_decoding=0..4``
        (verified on cjxl v0.12.0): higher values improve decode speed at the
        expense of quality or density, default 0. Any in-range value is valid,
        including ``=0`` (it is cjxl's default, not noise); ``None`` = omit.
      * ``num_threads`` / ``brotli_effort`` / ``epf`` / ``noise`` /
        ``resampling`` / ``codestream_level`` — plain value flags, only appended
        when not ``None``.
    """
    args = ["cjxl", input_path, output_path, "-e", str(effort)]
    if distance is not None:
        args += ["-d", str(distance)]
    if quality is not None:
        args += ["--quality", str(quality)]
    if lossless_jpeg is not None:
        args.append("--lossless_jpeg=%s" % lossless_jpeg)
    if progressive:
        args.append("--progressive")
    if modular is not None:
        args.append("--modular=%s" % modular)
    if faster_decoding is not None:
        args.append("--faster_decoding=%s" % faster_decoding)
    if num_threads is not None:
        args += ["--num_threads", str(num_threads)]
    if brotli_effort is not None:
        args.append("--brotli_effort=%s" % brotli_effort)
    if epf is not None:
        args.append("--epf=%s" % epf)
    if noise is not None:
        args.append("--noise=%s" % noise)
    if resampling is not None:
        args.append("--resampling=%s" % resampling)
    if container is not None:
        args.append("--container=%s" % container)
    if codestream_level is not None:
        args.append("--codestream_level=%s" % codestream_level)
    return args


def encode(
    input_path,
    output_path,
    effort=7,
    distance=None,
    quality=None,
    lossless_jpeg=False,
    priority=DEFAULT_PRIORITY,
    progressive=False,
    modular=None,
    num_threads=None,
    brotli_effort=None,
    epf=None,
    noise=None,
    resampling=None,
    container=None,
    codestream_level=None,
    faster_decoding=None,
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

    When the input is a JPEG *and* we are targeting lossy output (``quality`` is
    set but ``lossless_jpeg`` is not), we explicitly pass ``--lossless_jpeg=0``.
    Newer cjxl (>= 0.12) flips the default of that flag to 1 and additionally
    forbids ``quality < 100`` while it is 1, so a JPEG fed to lossy mode with the
    default would crash with zero output. Forcing 0 tells cjxl to decode the JPEG
    to pixels first and run a real VarDCT encode. Older cjxl defaulted to 0
    anyway, so the explicit flag is redundant but harmless there — making the
    behaviour identical across versions.

    The remaining keyword args (``progressive``, ``modular``, ``num_threads``,
    ``brotli_effort``, ``epf``, ``noise``, ``resampling``, ``container``,
    ``codestream_level``, ``faster_decoding``) are optional cjxl tuning knobs;
    see :func:`build_args` for how each maps to a flag. All default to "don't
    pass", so omitting them reproduces the previous behaviour exactly.

    ``priority`` (one of the ``_PRIORITY_FLAGS`` keys) sets the Windows CPU
    priority class of the spawned cjxl process. Default: ``below_normal``.
    """
    # Resolve the --lossless_jpeg flag value (None / 0 / 1) from the boolean
    # argument plus the JPEG auto-detection rule described above.
    lj_flag = None
    if lossless_jpeg:
        lj_flag = 1
    elif _is_jpeg(input_path) and quality is not None:
        # 有损模式遇到 JPG 输入显式传 0，规避新版默认 1 的崩溃（详见上方说明）。
        lj_flag = 0
    args = build_args(
        input_path,
        output_path,
        effort=effort,
        distance=distance,
        quality=quality,
        lossless_jpeg=lj_flag,
        progressive=progressive,
        modular=modular,
        num_threads=num_threads,
        brotli_effort=brotli_effort,
        epf=epf,
        noise=noise,
        resampling=resampling,
        container=container,
        codestream_level=codestream_level,
        faster_decoding=faster_decoding,
    )
    ok, msg, err = _run(args, priority=priority)
    # 抓取 cjxl 真实输出的编码标签（如 [Modular, lossless, effort: 7]）；
    # 解析失败（异常/自定义命令无 Encoding 行）时返回空串，由调用方兜底。
    tag = parse_encoding_tag(err)
    return ok, msg, tag


def _is_jpeg(path):
    """Return True if ``path`` looks like a JPEG file by extension."""
    return str(path).lower().endswith((".jpg", ".jpeg"))


def decode(input_path, output_path, priority=DEFAULT_PRIORITY):
    """Run djxl to decode a JPEG XL file into output_path.

    ``priority`` sets the Windows CPU priority class of the spawned djxl
    process. Default: ``below_normal``.
    """
    args = ["djxl", input_path, output_path]
    ok, msg, _ = _run(args, priority=priority)
    return ok, msg


def parse_encoding_tag(text):
    """从 cjxl 真实 stderr 输出里提取编码模式标签（含方括号）。

    cjxl 编码时打印一行 ``Encoding [<codec>, <mode>, effort: N]``（例如
    ``[Modular, lossless, effort: 7]`` / ``[VarDCT, d1.000, effort: 7]``），
    抓取方括号完整原文作为状态页每文件的编码描述；无匹配返回空串。
    """
    if not text:
        return ""
    m = _ENCODING_TAG_RE.search(text)
    if not m:
        return ""
    return "[" + m.group(1).strip() + "]"


def _run(args, priority=DEFAULT_PRIORITY):
    """Execute a command and return (success: bool, message: str).

    Uses :class:`subprocess.Popen` so the child process is spawned explicitly;
    ``communicate()`` blocks until it finishes and captures stdout/stderr
    (functionally equivalent to the old ``subprocess.run`` call, but built on
    Popen as the underlying primitive). The running process is registered in
    ``_current_process`` (most-recent) and ``_active_processes`` (all live ones)
    so the parallel pool can interrupt them via :func:`terminate_current`.
    """
    global _current_process
    flag = _PRIORITY_FLAGS.get(priority, _PRIORITY_FLAGS[DEFAULT_PRIORITY])
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_CREATE_NO_WINDOW | flag,
        )
    except FileNotFoundError:
        _current_process = None
        return False, "未找到可执行文件：%s（请确认其已加入系统 PATH）" % args[0]
    except OSError as exc:
        _current_process = None
        return False, "执行命令失败：%s" % exc
    _current_process = proc
    _active_processes.add(proc)
    try:
        stdout, stderr = proc.communicate()
    finally:
        _active_processes.discard(proc)
        if _current_process is proc:
            _current_process = None
    if proc.returncode != 0:
        detail = (stderr or "").strip() or "未知错误"
        return False, "命令返回错误（退出码 %d）：%s" % (proc.returncode, detail), stderr
    return True, (stdout or "").strip() or "操作成功完成。", stderr
