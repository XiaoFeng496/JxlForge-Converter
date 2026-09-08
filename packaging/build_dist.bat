@echo off
REM ===========================================================================
REM JxlForge Converter - 一键打包（one-folder, windowed）
REM
REM 本脚本位于仓库 packaging/ 目录，是打包流程的 canonical 版本（已入库）。
REM 打包产物（dist/build）输出到仓库外的 JxlForge-Build，不进 git。
REM
REM 必须用本 spec 打包：spec 已把 jxlforge/i18n/*.json 作为数据文件带进包。
REM 若直接用 `pyinstaller _launch_app.py` 而不带 --add-data，语言下拉会扫不到
REM en_US / zh_TW，英文界面会整片失效（已知坑，见项目记忆）。
REM
REM 打包完成后会自动跑 `JxlForge Converter.exe --selftest` 做完整性自检：
REM 漏打 i18n 等资源会让自检 FAIL 并红字报警，防止「打包漏功能」漏到用户手上。
REM ===========================================================================

REM 仓库根目录（packaging/ 的上一级）
set REPO=%~dp0..
REM 外部构建目录（与仓库同级，不在 git 内；存放 dist/build 产物）
set BUILD=%~dp0..\..\JxlForge-Build
set SPEC=%~dp0JxlForge Converter.spec
set DIST=%BUILD%\dist\JxlForge Converter
set EXE=%DIST%\JxlForge Converter.exe
set OLD=%BUILD%\_dist_old_bak

REM 打包前把旧 dist 改名挪走，避免 PyInstaller COLLECT 阶段删旧目录触发
REM safe-delete 守卫（历史实测会卡住）。rename 不算删除，安全。旧备份推到
REM 随机名以免覆盖；长期可手动清理 _dist_old_bak* 占的空间。
if exist "%DIST%" (
    if exist "%OLD%" move "%OLD%" "%OLD%_%RANDOM%" >nul 2>&1
    move "%DIST%" "%OLD%"
)

E:\Python\Python312\python.exe -m PyInstaller "%SPEC%" --noconfirm --distpath "%BUILD%\dist" --workpath "%BUILD%\build"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [FAIL] 打包失败，退出码 %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

REM 打包后冒烟自检：验证 i18n 等资源没漏（刚那个 bug 的回归防线）。
echo.
echo [SELFTEST] 启动打包产物做完整性自检...
"%EXE%" --selftest
if %ERRORLEVEL%==0 (
    echo.
    echo [OK] 打包完成且自检通过
    echo      分发包：%DIST%
) else (
    echo.
    echo [FAIL] 自检未通过！分发包可能漏了运行时资源（如 i18n）。
    echo        详见：%DIST%\selftest_report.txt
)
echo.
echo ===== 自检报告 =====
if exist "%DIST%\selftest_report.txt" (type "%DIST%\selftest_report.txt") else (echo （无报告文件）)
echo ====================
pause
