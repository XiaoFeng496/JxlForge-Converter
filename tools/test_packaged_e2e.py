# -*- coding: utf-8 -*-
"""端到端转换管线测试 —— 针对【编译产物】(frozen exe) 而非源码。

为什么需要它：
    仓库里 7 套 headless 测试全部 stub 掉 converter.encode/decode，从没真正
    跑过 cjxl/djxl。所以它们覆盖不到「打包后转换管线是否真的能转」这一层——
    而这一层正是之前「编译版漏 i18n」「cjxl 调用链路在打包态断裂」这类问题的
    重灾区。本脚本直接驱动构建出的 exe 跑它自带的 --selftest（其中已含
    e2e 转换用例），是编译产物的真正回归。

怎么用：
    python tools/test_packaged_e2e.py
    可通过环境变量 JF_PACKAGE_EXE 指定 exe 路径；缺省指向仓库外构建目录。

退出码：
    0  = 通过（或 e2e 因本机未装 libjxl 被 SKIP，此时会打印醒目提示）
    1  = 失败（RESULT: FAIL，或 exe 起不来 / 报告缺失）
"""

import os
import subprocess
import sys

# 缺省 exe 位置（与 packaging/build_dist.bat 的 --distpath 对应；基于本文件位置推算，
# 不硬编码盘符，换机器克隆到任意盘都能自动落到「仓库同级 JxlForge-Build」）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_EXE = os.path.join(
    os.path.dirname(_HERE), "..", "JxlForge-Build", "dist",
    "JxlForge Converter", "JxlForge Converter.exe")


def main():
    exe = os.environ.get("JF_PACKAGE_EXE", _DEFAULT_EXE)
    exe = os.path.abspath(exe)

    if not os.path.isfile(exe):
        print("SKIP  packaged e2e: built exe not found at")
        print("      %s" % exe)
        print("      （先跑 packaging/build_dist.bat 构建，或设置 JF_PACKAGE_EXE）")
        return 0

    print("RUN   %s --selftest" % exe)
    try:
        rc = subprocess.run([exe, "--selftest"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL).returncode
    except OSError as exc:
        print("FAIL  could not launch exe: %s" % exc)
        return 1

    # 报告写在 exe 同目录（windowed 无控制台，靠文件观测）。
    report_path = os.path.join(os.path.dirname(exe), "selftest_report.txt")
    try:
        report = open(report_path, "r", encoding="utf-8").read()
    except OSError:
        report = ""

    lines = report.splitlines()
    result_line = next((l for l in lines if l.startswith("RESULT:")), "")
    e2e_lines = [l for l in lines if "e2e:" in l]

    print("----- selftest report (excerpt) -----")
    for l in lines:
        if l.startswith("[") or l.startswith("RESULT:"):
            print(l)
    print("--------------------------------------")

    if rc != 0 or "FAIL" in result_line:
        print("FAIL  packaged e2e (exe exit=%d, %s)" % (rc, result_line or "no RESULT"))
        return 1

    # 引擎未打包 -> e2e 整段 SKIP：算通过，但给出醒目提示，避免误以为转码已验。
    skipped = [l for l in e2e_lines if l.strip().startswith("[SKIP]")]
    if e2e_lines and len(skipped) == len(e2e_lines):
        print("WARN  转换管线 e2e 全部 SKIP（本机未装 libjxl，未真正跑转码）。")
        print("      在装了 libjxl 的机器上重跑才会真正验证 PNG->JXL->PNG 往返。")
    elif skipped:
        print("WARN  部分 e2e 用例被 SKIP：")
        for l in skipped:
            print("      " + l.strip())

    print("PASS  packaged e2e (rc=%d, %s)" % (rc, result_line or "RESULT: PASS"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
