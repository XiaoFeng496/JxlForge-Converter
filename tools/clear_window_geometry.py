# -*- coding: utf-8 -*-
"""清除 libjxl GUI 持久化在 .ini 文件里的窗口几何，用于测试首启 6x3 排版。

程序在关闭时会把窗口大小/位置存进 QSettings 的 `geometry` 键
（见 libjxl_gui/main_window.py 的 _save_geometry / _load_geometry）。
只要该键存在，下次启动就恢复旧尺寸、跳过 6x3 自适应；
删掉它之后，程序会重新按 6x3 网格计算并居中窗口。

本脚本只删除 geometry（及可选的 windowState）键，不碰输出位置、
文件夹历史、编码参数等其它设置。运行前会自动备份原 ini。

用法（Windows）：
    python clear_window_geometry.py
然后双击 run.bat 观察首启窗口是否直接是 6x3 居中。
"""

import os
import sys
import shutil
from datetime import datetime

ORG = "libjxl"
APP = "libjxl-gui"

# Qt IniFormat 默认路径：%APPDATA%/<org>/<app>.ini（Roaming）
APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
INI = os.path.join(APPDATA, ORG, APP + ".ini")

# 仅清与“窗口大小/位置”直接相关的键（小写匹配）
TARGET_KEYS = ("geometry", "windowstate")


def _read_text(path):
    """按 UTF-8 读取；若含非 UTF-8 字节（如 @ByteArray 的原始数据），
    退回到 latin-1（一字节一字符，绝不抛错，且写回字节与原文一致）。
    返回 (文本, 使用的编码)。"""
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    # 极端兜底：直接替换不可解码字符
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(), "utf-8"


def main():
    if not os.path.exists(INI):
        print("未找到 ini 文件：%s" % INI)
        print("=> 程序尚未保存过设置，直接双击 run.bat 即为首次启动（6x3）。无需本脚本。")
        return

    # 备份原文件，便于反悔
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-%s" % (INI, ts)
    shutil.copy2(INI, bak)
    print("已备份原 ini -> %s" % bak)

    data, enc = _read_text(INI)
    lines = data.splitlines()

    out = []
    cur_sec = None
    removed = []
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur_sec = s[1:-1]
            out.append(line)
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key.lower() in TARGET_KEYS:
                removed.append((cur_sec, key))
                continue  # 丢弃该窗口几何键
        out.append(line)

    if not removed:
        print("ini 中未发现 geometry / windowState 键，无需清除（已是首启状态）。")
        # 还原备份，保持 ini 原样
        shutil.copy2(bak, INI)
        return

    with open(INI, "w", encoding=enc) as f:
        f.write("\n".join(out) + "\n")

    print("已删除以下窗口几何键（程序下次启动将重新 fit 到 6x3）：")
    for sec, key in removed:
        print("  [%s] %s" % (sec if sec else "<无组>", key))
    print("\n现在双击 run.bat 测试首启窗口大小。")


if __name__ == "__main__":
    main()
