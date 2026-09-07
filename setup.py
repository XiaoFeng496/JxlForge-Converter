#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JxlForge Converter - 项目环境安装脚本

功能:
  1. 检测当前操作系统 (Windows / macOS / Linux / 其他)
  2. 安装 PySide6 (Qt for Python)
  3. 检查系统 PATH 中是否存在 libjxl 的命令行工具 cjxl 与 djxl

用法:
  python setup.py            # 默认: 仅检测系统与工具 (不安装)
  python setup.py --check    # 仅检测系统与 cjxl/djxl
  python setup.py --install  # 安装 PySide6
  python setup.py --all      # 检测 + 安装 + 检查 (完整流程)
  python setup.py --venv     # 先创建虚拟环境, 再安装到其中
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# 颜色输出 (Windows 旧版控制台也尽量兼容)
# ---------------------------------------------------------------------------
USE_COLOR = sys.stdout.isatty() or os.environ.get("FORCE_COLOR", "0") == "1"


def _c(code: str, text: str) -> str:
    if not USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def info(msg: str) -> None:
    print(f"{_c('36', '[INFO]')} {msg}")


def ok(msg: str) -> None:
    print(f"{_c('32', '[ OK ]')} {msg}")


def warn(msg: str) -> None:
    print(f"{_c('33', '[WARN]')} {msg}")


def err(msg: str) -> None:
    print(f"{_c('31', '[FAIL]')} {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 1. 操作系统检测
# ---------------------------------------------------------------------------
def detect_os() -> str:
    """返回归一化的操作系统名称: windows / macos / linux / unknown"""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "unknown"


def os_friendly(name: str) -> str:
    return {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
        "unknown": f"未知系统 ({platform.system()})",
    }.get(name, name)


def detect_os_full() -> dict:
    return {
        "id": detect_os(),
        "name": os_friendly(detect_os()),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "release": platform.release(),
    }


# ---------------------------------------------------------------------------
# 2. 检查 PATH 中的 cjxl / djxl
# ---------------------------------------------------------------------------
def _which(tool: str) -> str | None:
    """在 PATH 中查找可执行文件, Windows 下额外尝试 .exe/.cmd/.bat"""
    found = shutil.which(tool)
    if found:
        return found
    if detect_os() == "windows":
        for ext in (".exe", ".cmd", ".bat"):
            found = shutil.which(tool + ext)
            if found:
                return found
    return None


def check_tools() -> dict:
    """检查 cjxl 与 djxl 是否在 PATH 中, 返回 {工具名: 路径或 None}"""
    results = {}
    for tool in ("cjxl", "djxl"):
        path = _which(tool)
        results[tool] = path
    return results


# ---------------------------------------------------------------------------
# 3. 安装 PySide6
# ---------------------------------------------------------------------------
def pip_install(packages: list[str], python_exe: str | None = None) -> int:
    exe = python_exe or sys.executable
    cmd = [exe, "-m", "pip", "install", "--upgrade", *packages]
    info("执行: " + " ".join(cmd))
    try:
        return subprocess.call(cmd)
    except FileNotFoundError as e:
        err(f"无法运行 pip: {e}")
        return 1


def ensure_venv(python_exe: str | None = None) -> str | None:
    """若当前不在虚拟环境中, 则创建一个并激活, 返回虚拟环境的 python 路径"""
    exe = python_exe or sys.executable
    if sys.prefix != sys.base_prefix:
        info("已处于虚拟环境中: " + sys.prefix)
        return exe

    venv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv")
    if not os.path.isdir(venv_dir):
        info(f"创建虚拟环境: {venv_dir}")
        try:
            subprocess.check_call([exe, "-m", "venv", venv_dir])
        except subprocess.CalledProcessError as e:
            err(f"创建虚拟环境失败: {e}")
            return None
    else:
        info(f"复用已存在的虚拟环境: {venv_dir}")

    if detect_os() == "windows":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")
    if not os.path.isfile(venv_python):
        err(f"未找到虚拟环境解释器: {venv_python}")
        return None
    return venv_python


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def report_os() -> None:
    data = detect_os_full()
    ok(f"检测到操作系统: {data['name']}  ({data['id']})")
    info(f"架构: {data['arch']} | 系统版本: {data['release']} | Python: {data['python']}")


def report_tools(results: dict) -> bool:
    all_ok = True
    for tool, path in results.items():
        if path:
            ok(f"找到 {tool}: {path}")
        else:
            warn(f"未找到 {tool} (不在 PATH 中)")
            all_ok = False
    if not all_ok:
        _print_tools_hint()
    return all_ok


def _print_tools_hint() -> None:
    print()
    info("cjxl / djxl 是 libjxl 提供的 JPEG XL 编解码命令行工具, 安装方式:")
    hints = {
        "windows": "  • 下载 libjxl 官方 Windows 构建, 或将包含 cjxl.exe/djxl.exe 的目录加入 PATH\n"
                   "  • 或通过 vcpkg: vcpkg install libjxl",
        "macos": "  • Homebrew:   brew install jpeg-xl\n"
                 "  • MacPorts:   sudo port install libjxl",
        "linux": "  • Debian/Ubuntu:  sudo apt install libjxl-tools\n"
                 "  • Fedora:         sudo dnf install libjxl-utils\n"
                 "  • Arch:           sudo pacman -S libjxl",
    }
    print(hints.get(detect_os(), "  • 请从 https://github.com/libjxl/libjxl 获取对应平台的构建"))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="libjxl GUI 项目环境安装脚本 (OS 检测 / PySide6 安装 / cjxl·djxl 检查)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="仅检测系统与 cjxl/djxl (默认)")
    group.add_argument("--install", action="store_true", help="安装 PySide6")
    group.add_argument("--venv", action="store_true", help="创建虚拟环境并安装 PySide6")
    group.add_argument("--all", action="store_true", help="检测 + 安装 + 检查 (完整流程)")
    args = parser.parse_args()

    do_check = args.check or args.all or not (args.install or args.venv or args.all)
    do_install = args.install or args.venv or args.all

    print(_c("1;35", "=== libjxl GUI · 项目环境脚本 ==="))
    report_os()

    if do_install:
        target_python = None
        if args.venv:  # 仅 --venv 时创建虚拟环境
            target_python = ensure_venv()
            if target_python is None:
                return 1
        # 读取 requirements.txt (若存在)
        req = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
        packages = ["PySide6"]
        if os.path.isfile(req):
            with open(req, encoding="utf-8") as f:
                extra = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            if extra:
                packages = extra
        rc = pip_install(packages, target_python)
        if rc != 0:
            err("PySide6 安装失败, 请检查网络或 pip 配置")
            return rc
        ok("PySide6 安装完成")

    if do_check:
        print()
        report_tools(check_tools())

    print()
    ok("流程结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
