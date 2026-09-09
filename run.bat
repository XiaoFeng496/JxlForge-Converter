@echo off
cd /d "%~dp0"
REM 优先 pythonw（无控制台），回退 python；不再硬编码作者机器 E: 盘解释器。
pythonw main.py 2>nul || python main.py
