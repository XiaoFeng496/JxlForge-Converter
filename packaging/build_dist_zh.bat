@echo off
chcp 65001 >nul
REM Switch console to UTF-8 so the Python script Chinese output renders.
setlocal EnableExtensions
REM ===========================================================================
REM JxlForge Converter - one-click build (Chinese output)
REM Pure ASCII + CRLF + no BOM. Chinese messages live in build_dist_zh.py.
REM Interpreter resolution uses existence/where checks only (never runs python).
REM Control flow is goto-style only: a multi-line if ( ... ) block whose body
REM contains parentheses makes cmd scan to EOF and close silently.
REM --no-pause: this launcher shows its own "press any key" prompt, so the
REM script must not wait for another key press (two presses otherwise).
REM ===========================================================================
cd /d "%~dp0"
set "PY="
if exist "E://Python//Python312//python.exe" set "PY=E://Python//Python312//python.exe"
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY goto :no_py
echo [INFO] Using Python: %PY%
"%PY%" build_dist_zh.py --no-pause
goto :halt
:no_py
echo.
echo [FAIL] No usable Python interpreter found.
echo        Expected E://Python//Python312//python.exe, or py/python on PATH.
goto :halt
:halt
echo.
echo Build finished. Press any key to close.
pause
