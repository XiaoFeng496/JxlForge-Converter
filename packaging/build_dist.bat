@echo off
chcp 65001 >nul
REM Switch console to UTF-8 so the Python selftest's Chinese output renders correctly.
setlocal EnableExtensions
REM ===========================================================================
REM JxlForge Converter - one-click build (one-folder, windowed)
REM Pure ASCII + CRLF. Logs every step to packaging\build_dist.log so a
REM double-click failure is never silent.
REM Resolution uses where/existence only (never runs python) to avoid any
REM probe crash; the real build step below is the actual test.
REM NOTE: goto-style control flow only (no multi-line if-blocks). A multi-line
REM if (...) ( ... ) block whose body contains a line with parentheses (e.g.
REM echo ... (%VAR%) ...) confuses cmd's parenthesis nesting when the condition
REM is false, making it scan to EOF, error out, and close the window silently.
REM PyInstaller output is streamed LIVE to the console (no redirect) so the
REM ~1 minute build is visible and never looks frozen.
REM ===========================================================================
set "REPO=%~dp0.."
set "BUILD=%~dp0..\..\JxlForge-Build"
set "SPEC=%~dp0JxlForge Converter.spec"
set "DIST=%BUILD%\dist\JxlForge Converter"
set "EXE=%DIST%\JxlForge Converter.exe"
set "OLD=%BUILD%\_dist_old_bak"
set "LOG=%~dp0build_dist.log"
echo [%date% %time%] ===== build_dist start ===== > "%LOG%"
echo [%date% %time%] REPO=%REPO% >> "%LOG%"

REM Resolve interpreter: prefer explicit known-good path, then py launcher,
REM then bare python on PATH. We only TEST existence/where, never run python
REM during resolution (a bad PATH python can crash the probe console).
set "PY="
if exist "E:/Python/Python312/python.exe" set "PY=E:/Python/Python312/python.exe"
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
echo [%date% %time%] interpreter=%PY% >> "%LOG%"
if not defined PY goto :no_py

echo [INFO] Using Python: %PY%
echo [%date% %time%] Using Python: %PY% >> "%LOG%"

REM Rename old dist aside before build (not a delete; safe-delete guard safe).
if exist "%DIST%" (
    if exist "%OLD%" move "%OLD%" "%OLD%_%RANDOM%" >nul 2>&1
    move "%DIST%" "%OLD%"
)

cd /d "%REPO%"
echo.
echo [BUILD] Running PyInstaller ... this takes about 1 minute.
echo         The lines below are LIVE build output -- a blank pause is normal, not stuck.
echo [%date% %time%] starting PyInstaller ... >> "%LOG%"
"%PY%" -m PyInstaller "%SPEC%" --noconfirm --distpath "%BUILD%\dist" --workpath "%BUILD%\build"
set "RC=%ERRORLEVEL%"
echo [%date% %time%] PyInstaller rc=%RC% >> "%LOG%"
if %RC% NEQ 0 goto :build_fail

echo [%date% %time%] PyInstaller done >> "%LOG%"

echo.
echo [SELFTEST] Running packaged exe integrity check...
echo [%date% %time%] selftest start >> "%LOG%"
if not exist "%EXE%" goto :exe_missing
"%EXE%" --selftest
set "RC=%ERRORLEVEL%"
echo [%date% %time%] selftest rc=%RC% >> "%LOG%"
if %RC%==0 goto :selftest_ok

echo.
echo [FAIL] Self-test FAILED! Dist may miss runtime resources (e.g. i18n).
echo        See: %DIST%\selftest_report.txt
goto :halt

:selftest_ok
echo.
echo [OK] Build complete and self-test passed
echo       Dist: %DIST%
echo.
echo ===== Self-test report =====
if exist "%DIST%\selftest_report.txt" (type "%DIST%\selftest_report.txt") else echo (no report file)
echo ====================
goto :halt

:exe_missing
echo.
echo [FAIL] Built exe not found: %EXE%
echo        PyInstaller may have used an unexpected output name.
goto :halt

:build_fail
echo.
echo [FAIL] Build failed, exit code %RC%
echo        The PyInstaller output above shows the cause. Step log: %LOG%
goto :halt

:no_py
echo.
echo [FAIL] No usable Python interpreter found.
echo        Expected E:/Python/Python312/python.exe, or py/python on PATH.
echo        Install Python 3.10+ with PyInstaller, then retry.
echo [%date% %time%] [FAIL] no interpreter resolved >> "%LOG%"
set "RC=1"
goto :halt

:halt
if not defined RC set "RC=0"
echo.
echo Build script finished (rc=%RC%). Press any key to close.
echo Log: %LOG%
pause
exit /b %RC%
