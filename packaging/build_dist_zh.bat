@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "PY="
if exist "E:\Python\Python312\python.exe" set "PY=E:\Python\Python312\python.exe"
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [FAIL] No usable Python interpreter found.
    echo        Expected E:\Python\Python312\python.exe, or py/python on PATH.
    pause
    exit /b 1
)
"%PY%" build_dist_zh.py
