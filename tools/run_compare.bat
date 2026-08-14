@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul && python run_compare.py || "E:\Python\Python312\python.exe" run_compare.py
