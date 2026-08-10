#!/usr/bin/env bash
#
# libjxl GUI - 依赖安装脚本 (Bash 版, 适用于 macOS / Linux / WSL)
#
# 功能:
#   1. 检测操作系统 (Windows / macOS / Linux)
#   2. 安装 PySide6
#   3. 检查 PATH 中是否存在 cjxl 与 djxl
#
# 用法:
#   ./install_deps.sh          # 检测 + 安装 PySide6 + 检查工具
#   ./install_deps.sh --check  # 仅检测系统与工具
#
# 说明: Windows 原生 cmd/PowerShell 不支持本脚本, 请在 Git Bash / WSL 中运行,
#       或直接用 setup.py (跨平台)。

set -euo pipefail

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; BOLD=$'\033[1;35m'; RESET=$'\033[0m'
info() { printf '%s[INFO]%s %s\n' "$CYAN" "$RESET" "$1"; }
ok()   { printf '%s[ OK ]%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$1"; }
err()  { printf '%s[FAIL]%s %s\n' "$RED" "$RESET" "$1" >&2; }

# ---------------------------------------------------------------------------
# 1. 操作系统检测
# ---------------------------------------------------------------------------
detect_os() {
  local raw
  raw="$(uname -s)"
  case "$raw" in
    Linux*)  echo "linux" ;;
    Darwin*) echo "macos" ;;
    CYGWIN*|MINGW*|MSYS*|Windows_NT) echo "windows" ;;
    *)       echo "unknown" ;;
  esac
}

OS="$(detect_os)"
case "$OS" in
  windows) OS_NAME="Windows (MSYS/Cygwin/WSL)" ;;
  macos)   OS_NAME="macOS" ;;
  linux)   OS_NAME="Linux" ;;
  *)       OS_NAME="未知系统 ($raw)" ;;
esac

# ---------------------------------------------------------------------------
# 2. 检查 cjxl / djxl
# ---------------------------------------------------------------------------
check_tools() {
  local all_ok=1
  for tool in cjxl djxl; do
    if command -v "$tool" >/dev/null 2>&1; then
      ok "找到 $tool: $(command -v "$tool")"
    else
      warn "未找到 $tool (不在 PATH 中)"
      all_ok=0
    fi
  done
  if [ "$all_ok" -eq 0 ]; then
    echo
    info "cjxl / djxl 安装方式:"
    case "$OS" in
      macos)   echo "  • Homebrew: brew install jpeg-xl" ;;
      linux)   echo "  • Debian/Ubuntu: sudo apt install libjxl-tools" ;;
      windows) echo "  • vcpkg: vcpkg install libjxl  或下载官方 Windows 构建并加入 PATH" ;;
      *)       echo "  • 见 https://github.com/libjxl/libjxl" ;;
    esac
  fi
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

printf '%s=== libjxl GUI · 依赖安装脚本 ===%s\n' "$BOLD" "$RESET"
ok "检测到操作系统: $OS_NAME ($OS)"
info "架构: $(uname -m) | Shell: $BASH_VERSION"

if [ "$CHECK_ONLY" -eq 0 ]; then
  info "使用 pip 安装 PySide6 ..."
  python3 -m pip install --upgrade -r "$(dirname "$0")/requirements.txt" || {
    err "PySide6 安装失败, 请检查网络或 pip 配置"
    exit 1
  }
  ok "PySide6 安装完成"
fi

echo
check_tools

echo
ok "流程结束"
