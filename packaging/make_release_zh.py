# -*- coding: utf-8 -*-
"""JxlForge Converter 发布包打包脚本（ZIP + 7Z）。[中文版]

用法：
    双击 packaging/make_release_zh.bat（推荐）
    命令行：python packaging/make_release_zh.py
    自定义：python packaging/make_release_zh.py --src <目录> --out <目录>

职责：
    1. 从 jxlforge/__init__.py 读取 __version__，版本号无需手写；
    2. 把构建产物 dist/JxlForge Converter/ 打包成
       JxlForge-Converter_v<version>_win64.zip 与 .7z；
    3. 输出到仓库同级的 JxlForge-Build/release/（不进 git）。

后端策略：
    ZIP —— 优先 7-Zip（-tzip -mx=9，deflate 实现更好、更快）；
           无 7-Zip 时回退标准库 zipfile（compresslevel=9，零依赖）。
           注意：ZIP 格式本身无固实压缩，对满是相似 DLL 的 dist 天然比 7z 大，
           这是格式限制，不是 bug。
    7Z  —— 依次探测 7-Zip(7z.exe) → py7zr；
           两者皆无则跳过 7Z 并给出安装指引（不影响 ZIP 产出）。

注意：本脚本只打包，不构建。请先运行 build_dist.bat 生成 dist。
"""

import os
import re
import sys
import shutil
import zipfile
import subprocess
import argparse

# 让中文提示在各种控制台下都不乱码（Python 3.7+ 支持 reconfigure）。
try:
    if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # packaging/
REPO = os.path.normpath(os.path.join(_HERE, ".."))           # 仓库根 JxlForge-Converter/
# 与 build_dist.bat 同源约定：构建产物在仓库**同级**的 JxlForge-Build/
BUILD = os.path.normpath(os.path.join(REPO, "..", "JxlForge-Build"))

DEFAULT_SRC = os.path.join(BUILD, "dist", "JxlForge Converter")
DEFAULT_OUT = os.path.join(BUILD, "release")

EXE_NAME = "JxlForge Converter.exe"

# 构建期产物，不随包分发给用户。
# selftest_report.txt 记录的是「构建机」的自检结果（含该机 PATH 上是否有 libjxl），
# 与用户机器无关，放在 exe 旁边只会引起困惑；用户随时可用
# `JxlForge Converter.exe --selftest` 自己重新生成。
# 注意：dist 里仍然保留它——打包门禁要读它，只是不进压缩包。
EXCLUDE_FROM_PACKAGE = ("selftest_report.txt",)

SRC_DIR = DEFAULT_SRC
OUT_DIR = DEFAULT_OUT
NO_PAUSE = False


def read_version():
    """从 jxlforge/__init__.py 解析 __version__，避免版本号写死在多处。"""
    init_py = os.path.join(REPO, "jxlforge", "__init__.py")
    with open(init_py, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"""__version__\s*=\s*["']([^"']+)["']""", line)
            if m:
                return m.group(1)
    raise RuntimeError("未能在 %s 中找到 __version__" % init_py)


def check_dist():
    """打包前校验：dist 存在、exe 在、自检报告为 PASS（否则仅警告）。"""
    if not os.path.isdir(SRC_DIR):
        raise RuntimeError(
            "找不到构建产物：%s\n请先运行 packaging\\build_dist.bat 完成构建。" % SRC_DIR
        )
    if not os.path.isfile(os.path.join(SRC_DIR, EXE_NAME)):
        raise RuntimeError("构建产物中缺少 %s，打包中止。" % EXE_NAME)

    report = os.path.join(SRC_DIR, "selftest_report.txt")
    if os.path.isfile(report):
        text = open(report, "r", encoding="utf-8", errors="replace").read()
        if "RESULT: PASS" in text:
            print("[OK ] 自检报告为 PASS")
        else:
            print("[WARN] 自检报告非 PASS，打包继续，但请先确认构建健康：")
            print("       %s" % report)
    else:
        print("[WARN] 未找到 selftest_report.txt（构建产物可能未经自检）")


def source_size():
    total = 0
    for root, _dirs, files in os.walk(SRC_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _find_exe(names, extra_paths):
    """先查 PATH（换机器也能用），再查已知安装路径。"""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for p in extra_paths:
        if p and os.path.isfile(p):
            return p
    return None


def find_7z_exe():
    """查找 7-Zip：PATH 优先，其次常见安装路径。

    注意：不接 Bandizip——它的 Bandizip.exe 是 GUI 主程序，命令行参数
    格式不同（CLI 版为 bz.exe），传 7z 风格参数会弹「参数无效」对话框。
    """
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "E:\\7-Zip",
    ]
    seen = set()
    extra = []
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        extra.append(os.path.join(root, "7-Zip", "7z.exe"))
    return _find_exe(("7z", "7za"), extra)


def make_zip(out_path):
    """打 ZIP：优先 7-Zip（-tzip -mx=9），否则回退标准库 zipfile（级别 9）。

    返回文件数；若由 7-Zip 生成则返回 None。
    """
    sevenz = find_7z_exe()
    if sevenz:
        print("[ZIP] 使用 7-Zip：%s" % sevenz)
        cmd = [sevenz, "a", "-tzip", out_path, os.path.basename(SRC_DIR),
               "-mx=9", "-mmt=on", "-y"]
        for name in EXCLUDE_FROM_PACKAGE:
            cmd.append("-xr!" + name)
        subprocess.run(cmd, cwd=os.path.dirname(SRC_DIR), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None

    print("[ZIP] 使用标准库 zipfile（DEFLATE 级别 9），约需数十秒...")
    base = os.path.dirname(SRC_DIR)
    n = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, files in os.walk(SRC_DIR):
            for name in files:
                if name in EXCLUDE_FROM_PACKAGE:
                    continue
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, base))
                n += 1
    return n


def make_7z_with_7zip(sevenz, out_path):
    """用 7-Zip 官方 CLI 打 7Z（最快、压缩率最高）。"""
    print("[7Z ] 使用 7-Zip：%s" % sevenz)
    cmd = [sevenz, "a", "-t7z", out_path, os.path.basename(SRC_DIR),
           "-mx=9", "-mmt=on", "-y"]
    for name in EXCLUDE_FROM_PACKAGE:
        cmd.append("-xr!" + name)
    subprocess.run(cmd, cwd=os.path.dirname(SRC_DIR), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_7z_with_py7zr(out_path):
    """回退方案：py7zr 纯 Python 打包（较慢，但无需外部 exe）。"""
    import py7zr
    print("[7Z ] 使用 py7zr（纯 Python，较慢，请耐心）...")
    filters = [{"id": py7zr.FILTER_LZMA2, "preset": 7}]
    # 逐文件写入（writeall 不支持排除），以便跳过构建期产物
    base = os.path.dirname(SRC_DIR)
    with py7zr.SevenZipFile(out_path, "w", filters=filters) as z:
        for root, _dirs, files in os.walk(SRC_DIR):
            for name in files:
                if name in EXCLUDE_FROM_PACKAGE:
                    continue
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, base))


def make_7z(out_path):
    """按 7-Zip → py7zr 探测后端；都无则返回 False（不阻断 ZIP）。"""
    sevenz = find_7z_exe()
    if sevenz:
        make_7z_with_7zip(sevenz, out_path)
        return True
    try:
        import py7zr  # noqa: F401
    except ImportError:
        return False
    make_7z_with_py7zr(out_path)
    return True


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.2f %s" % (size, unit)
        size /= 1024.0


def main():
    global SRC_DIR, OUT_DIR, NO_PAUSE

    ap = argparse.ArgumentParser(description="打包 JxlForge Converter 发布包（ZIP + 7Z）")
    ap.add_argument("--src", default=DEFAULT_SRC, help="源目录（默认 dist/JxlForge Converter）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录（默认 JxlForge-Build/release）")
    ap.add_argument("--no-pause", action="store_true",
                    help="结束后不等待按键（供 .bat 启动器调用）")
    args = ap.parse_args()
    SRC_DIR = os.path.abspath(args.src)
    OUT_DIR = os.path.abspath(args.out)
    NO_PAUSE = args.no_pause

    print("=" * 60)
    print("JxlForge Converter 发布包打包工具")
    print("=" * 60)
    version = read_version()
    print("[INFO] 版本号：%s（读自 jxlforge/__init__.py）" % version)

    check_dist()
    print("[INFO] 源目录：%s（%s）" % (SRC_DIR, human(source_size())))

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = "JxlForge-Converter_v%s_win64" % version
    zip_path = os.path.join(OUT_DIR, stem + ".zip")
    sz_path = os.path.join(OUT_DIR, stem + ".7z")

    made = []
    try:
        n = make_zip(zip_path)
        if n is None:
            print("[OK ] ZIP 完成 → %s（%s）" % (zip_path, human(os.path.getsize(zip_path))))
        else:
            print("[OK ] ZIP 完成：%d 个文件 → %s（%s）"
                  % (n, zip_path, human(os.path.getsize(zip_path))))
        made.append(zip_path)
    except Exception as e:
        print("[FAIL] ZIP 打包失败：%r" % (e,))

    try:
        if make_7z(sz_path):
            print("[OK ] 7Z  完成 → %s（%s）" % (sz_path, human(os.path.getsize(sz_path))))
            made.append(sz_path)
        else:
            print("[SKIP] 未生成 7Z：未找到 7-Zip，且未安装 py7zr。")
            print("       任选其一即可启用：")
            print("       - 装 7-Zip ：winget install 7zip.7zip")
            print("       - 或装库   ：pip install py7zr")
    except Exception as e:
        print("[FAIL] 7Z 打包失败：%r" % (e,))

    print("-" * 60)
    if made:
        print("完成，共生成 %d 个发布包：" % len(made))
        for p in made:
            print("  " + p)
    else:
        print("未能生成任何发布包。")
    print("-" * 60)
    return 0 if made else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception as exc:
        print("[FAIL] %s" % exc)
        rc = 1
    # 仅直接运行 .py 时停留；.bat 启动器会传 --no-pause 并显示自己的提示，
    # 避免双击时「按两次才退出」。
    if not NO_PAUSE:
        try:
            input("\n按回车键关闭...")
        except Exception:
            pass
    sys.exit(rc)
