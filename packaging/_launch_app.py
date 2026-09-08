# -*- coding: utf-8 -*-
"""真实常驻启动入口（位于仓库 packaging/，仅用于 PyInstaller 打包）。

与 _bench_app.py 的区别：直接调用 jxlforge.__main__.run() 的完整启动序列
（旧设置迁移 / 语言 / 自动校准 / app.exec 常驻），**不自动 quit**——
双击打包出的 exe 能正常打开、一直运行，用于真机验白屏与日常使用。

打包时 PyInstaller 通过 spec 的 pathex（项目根）找到 jxlforge 包；打进包后
jxlforge 为顶层模块，from jxlforge.__main__ import run 直接可用。
"""

from jxlforge.__main__ import run

if __name__ == "__main__":
    run()
