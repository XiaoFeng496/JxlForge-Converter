@echo off
chcp 65001 >nul
REM Switch console to UTF-8 so non-ASCII output from the Python script renders.
setlocal EnableExtensions
REM ===========================================================================
REM JxlForge Converter - package release archives (ZIP + 7Z)
REM Pure ASCII + CRLF + no BOM. Messages live in the paired .py file.
REM Interpreter resolution uses existence/where checks only (never runs python),
REM so a broken python on PATH cannot take the console down.
REM Control flow is goto-style only: a multi-line if ( ... ) block whose body
REM contains parentheses makes cmd scan to EOF and close silently.
REM --no-pause: this launcher shows its own prompt, so the script must not
REM wait for another key press (otherwise two presses are needed to exit).
REM Requires build_dist.bat to have produced dist first.
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
"%PY%" make_release_zh.py --no-pause
goto :halt
:no_py
echo.
echo [FAIL] No usable Python interpreter found.
echo        Expected E://Python//Python312//python.exe, or py/python on PATH.
goto :halt
:halt
echo.
echo Release packaging finished. Press any key to close.
pause
