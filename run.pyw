#! E:/Python/Python312/pythonw.exe
# -*- coding: utf-8 -*-
"""JxlForge Converter 无控制台启动入口（.pyw 由 Python Launcher 的 pyw.exe 无框加载）。

双击本文件即启动 GUI，不会弹出命令行窗口。
shebang 锁定到 E:/Python/Python312/pythonw.exe，避免 Launcher 版本选择歧义。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from jxlforge.__main__ import run as _main

if __name__ == "__main__":
    _main()
