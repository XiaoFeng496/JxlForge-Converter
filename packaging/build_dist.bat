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

REM 仓库根目录（packaging/ 的上一级）—— 全程基于 bat 自身位置 %~dp0 推算，
REM 不硬编码任何盘符；换台机器克隆到任意盘，构建产物会自动落在「仓库同级」目录。
set REPO=%~dp0..
REM 外部构建目录（与仓库同级，不在 git 内；存放 dist/build 产物）。
REM 例：仓库若在 D:\x\JxlForge-Converter，则产物落到 D:\x\JxlForge-Build。
set BUILD=%~dp0..\..\JxlForge-Build
set SPEC=%~dp0JxlForge Converter.spec
set DIST=%BUILD%\dist\JxlForge Converter
set EXE=%DIST%\JxlForge Converter.exe
set OLD=%BUILD%\_dist_old_bak

REM 解析 Python 解释器（跨机器可移植，不再绑定作者机器 E: 盘）：
REM   逐个候选探测「能否真正调起 PyInstaller」——优先 PATH 中的 python、
REM   Windows Python Launcher `py -3`、回退作者固定安装 E:\Python\Python312；
REM   探测失败（如 PATH 上那个 python 损坏）会自动跳过下一个候选。都不可用则报错退出。
set PY=
python -m PyInstaller --version >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 -m PyInstaller --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    if exist "E:\Python\Python312\python.exe" (
        E:\Python\Python312\python.exe -m PyInstaller --version >nul 2>&1 && set "PY=E:\Python\Python312\python.exe"
    )
)
if not defined PY (
    echo.
    echo [FAIL] 找不到可用的 Python 解释器（需能 import PyInstaller）。
    echo        请安装 Python 3.10+ 并加入 PATH，或安装到 E:\Python\Python312。
    echo        也可手动编辑本脚本 PY 变量指向你的 python.exe。
    pause
    exit /b 1
)
echo [INFO] 使用 Python：%PY%

REM 打包前把旧 dist 改名挪走，避免 PyInstaller COLLECT 阶段删旧目录触发
REM safe-delete 守卫（历史实测会卡住）。rename 不算删除，安全。旧备份推到
REM 随机名以免覆盖；长期可手动清理 _dist_old_bak* 占的空间。
if exist "%DIST%" (
    if exist "%OLD%" move "%OLD%" "%OLD%_%RANDOM%" >nul 2>&1
    move "%DIST%" "%OLD%"
)

cd /d "%REPO%"
"%PY%" -m PyInstaller "%SPEC%" --noconfirm --distpath "%BUILD%\dist" --workpath "%BUILD%\build"
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
