@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul && python run_compare.py || (echo 请先安装 Python 3.10+ 并加入 PATH & pause & exit /b 1)
