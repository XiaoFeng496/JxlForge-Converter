# -*- coding: utf-8 -*-
"""JxlForge Converter - 中文版一键打包脚本（one-folder, windowed）。

为什么中文放在 .py 而不是 .bat：Windows 的 cmd.exe 按系统 OEM 码页读取 .bat
文件本身，chcp 65001 只改“控制台输出”编码、改不了“文件解析”编码，所以 .bat
里写 UTF-8 中文会乱码（经典坑）。正确做法是 .bat 只做纯 ASCII 外壳（切 UTF-8
码页 + 调起本脚本），所有中文提示由 Python 用 UTF-8 输出，控制台已切 UTF-8 即
正常显示。这与项目里 tools/run_compare.bat + run_compare.py 的先例一致。
"""
import os
import sys
import subprocess
import datetime


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
BUILD = os.path.normpath(os.path.join(HERE, "..", "..", "JxlForge-Build"))
SPEC = os.path.join(HERE, "JxlForge Converter.spec")
DIST = os.path.join(BUILD, "dist", "JxlForge Converter")
EXE = os.path.join(DIST, "JxlForge Converter.exe")
OLD = os.path.join(BUILD, "_dist_old_bak")
LOG = os.path.join(HERE, "build_dist_zh.log")


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def resolve_python():
    """只用存在性/which 判断，绝不运行 python 去探测（坏 PATH python 会带崩控制台）。"""
    cand = [r"E:\Python\Python312\python.exe"]
    for c in cand:
        if os.path.exists(c):
            return c
    for cmd in ("py", "python"):
        try:
            subprocess.run([cmd, "--version"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10)
            return cmd
        except Exception:
            pass
    return None


def main():
    open(LOG, "w", encoding="utf-8").close()
    log("===== build_dist_zh 开始 =====")
    py = resolve_python()
    if not py:
        print("未找到可用的 Python 解释器（需 3.10+ 且已安装 PyInstaller）。")
        print("请安装 Python 3.10+ 并加入 PATH，或安装到 E:\\Python\\Python312。")
        return 1
    log("解释器 = %s" % py)
    print("[信息] 使用 Python：%s" % py)

    # 构建前把旧 dist 改名移开（不是删除，安全守卫安全）。
    if os.path.isdir(DIST):
        bak = OLD
        if os.path.isdir(bak):
            bak = "%s_%d" % (OLD, os.getpid())
        try:
            os.rename(DIST, bak)
            log("旧 dist 已移开 -> %s" % bak)
        except Exception as e:
            log("移开旧 dist 失败：%s" % e)

    os.chdir(REPO)
    log("开始 PyInstaller ...")
    print("[构建] 正在用 PyInstaller 打包（约 1 分钟）...")
    rc = subprocess.run([
        py, "-m", "PyInstaller", SPEC, "--noconfirm",
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "build"),
    ]).returncode
    log("PyInstaller rc=%d" % rc)
    if rc != 0:
        print("[失败] 打包失败，退出码 %d" % rc)
        print("       完整日志：%s" % LOG)
        return rc

    log("PyInstaller 完成")
    print()
    print("[自检] 正在运行打包 exe 完整性检查...")
    log("selftest 开始")
    if not os.path.exists(EXE):
        print("[失败] 未找到生成的 exe：%s" % EXE)
        return 1
    rc = subprocess.run([EXE, "--selftest"]).returncode
    log("selftest rc=%d" % rc)
    if rc == 0:
        print()
        print("[成功] 构建完成且自检通过")
        print("       分发目录：%s" % DIST)
        print()
        print("===== 自检报告 =====")
        rep = os.path.join(DIST, "selftest_report.txt")
        if os.path.exists(rep):
            with open(rep, encoding="utf-8", errors="replace") as f:
                print(f.read())
        else:
            print("（无报告文件）")
        print("====================")
        return 0
    else:
        print()
        print("[失败] 自检未通过！分发目录可能缺少运行时资源（如 i18n）。")
        print("       详见：%s" % os.path.join(DIST, "selftest_report.txt"))
        return rc


if __name__ == "__main__":
    code = main()
    try:
        input("构建脚本结束（rc=%s）。按回车键关闭。" % code)
    except EOFError:
        pass
    sys.exit(code)
