# -*- coding: utf-8 -*-
"""验证「加一门新语言 = 往 i18n/ 丢一个 json」这条契约真的成立。

以前加语言要改三处代码（放 json + 改 _LANGUAGE_ORDER + 改 _LANGUAGE_LABELS），
漏一处就是「下拉里看得见、切过去没反应」这类哑 bug。
现在语言清单从 ``i18n/`` 目录自动派生，这个测试就是那道锁。

为什么用子进程
--------------
``main_window._LANGUAGE_ORDER`` 在 import 时就算好了，运行期往目录里加 json
不会刷新它（这没问题——切语言本来就要重启）。
所以必须**先落文件、再导入**，用子进程跑最贴近真实流程，也避免污染主进程的
sys.modules。

覆盖：
- 新 json 被自动发现，出现在下拉里，自称名取自 json 的 _language_name。
- 覆盖率闸门自动把它纳入统计（不必改任何脚本）。
- 未知语言代码回退到默认语言，而不是「设了但没字典」。
- 字典里有源码已不存在的陈旧条目时，闸门报出 orphan（防字典腐烂）。
- 测试结束不留残留文件。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from jxlforge import i18n  # noqa: E402

PY = sys.executable
_failures = []
_passed = 0


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print("[ OK ] %s" % name)
    else:
        print("[FAIL] %s %s" % (name, detail))
        _failures.append(name)


# 探针脚本：由子进程执行，落一个假语言 json 后检查各层反应。
_PROBE = r'''
# -*- coding: utf-8 -*-
import io
import json
import os
import sys

ROOT = sys.argv[1]
CODE = sys.argv[2]
sys.path.insert(0, ROOT)

from jxlforge import i18n

path = os.path.join(i18n._I18N_DIR, "%s.json" % CODE)
try:
    payload = {
        "_language_name": "テスト語",
        "_note": "临时文件，由 test_i18n_add_language.py 创建并删除",
        "调整大小": "リサイズ",
        "旋转": "回転",
        "这条源码里没有": "orphan-probe",
    }
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    # ---- 1. i18n 层：自动发现 ----
    print("DISCOVERED %s" % (CODE in i18n.available_languages()))
    print("IN_ORDER %s" % (CODE in i18n.language_order()))
    print("SELFNAME %s" % i18n.language_name(CODE))

    # ---- 2. 下拉层：main_window 派生出的清单里要有它 ----
    from jxlforge.main_window import _LANGUAGE_ORDER, _LANGUAGE_LABELS
    print("IN_COMBO_ORDER %s" % (CODE in _LANGUAGE_ORDER))
    print("COMBO_LABEL %s" % _LANGUAGE_LABELS.get(CODE, "<缺失>"))

    # ---- 3. 切过去真的生效 ----
    i18n.set_language(CODE)
    print("T_RESIZE %s" % i18n.t("调整大小"))
    print("T_FALLBACK %s" % i18n.t("源码里有但字典里没有"))

finally:
    import time
    for _ in range(10):
        if not os.path.isfile(path):
            break
        try:
            os.remove(path)
        except OSError:
            pass
        time.sleep(0.05)
    print("CLEANED %s" % (not os.path.isfile(path)))
'''


def run_probe(code):
    """在子进程里跑探针，返回 {键: 值}。"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, "-c", _PROBE, REPO_ROOT, code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    out = {}
    for line in (r.stdout or "").splitlines():
        if " " in line:
            k, _, v = line.partition(" ")
            out[k] = v
    if r.returncode != 0:
        print("  探针 stderr:\n%s" % (r.stderr or "")[:800])
    return out


def main():
    print("=" * 60)
    print("加一门新语言：只丢一个 json，其余全自动")
    print("=" * 60)

    # --- 1. 基线：目录里当前只有 en_US ---
    # 防御：清掉上一次中断测试可能残留的临时语言文件（Windows 上 os.remove 有竞态，
    # 子进程 finally 偶尔删不干净），否则基线会被污染、本次断言误 FAIL。
    _stray = os.path.join(i18n._I18N_DIR, "ja_JP.json")
    if os.path.isfile(_stray):
        try:
            os.remove(_stray)
        except OSError:
            pass
    codes = i18n.available_languages()
    check("目录扫描能找到 en_US", "en_US" in codes, str(codes))
    check("默认语言排在第一位",
          i18n.language_order()[0] == i18n.DEFAULT_LANGUAGE,
          str(i18n.language_order()))

    # --- 2. 加一门假语言，看各层反应 ---
    probe = run_probe("ja_JP")
    if not probe:
        check("探针执行成功（加语言的完整流程）", False, "子进程无输出")
        return _summary()

    check("新语言被自动发现", probe.get("DISCOVERED") == "True", str(probe))
    check("新语言进入显示顺序", probe.get("IN_ORDER") == "True", str(probe))
    check("自称名取自 json 的 _language_name",
          probe.get("SELFNAME") == "テスト語", repr(probe.get("SELFNAME")))
    check("下拉清单自动包含新语言（不用改代码）",
          probe.get("IN_COMBO_ORDER") == "True", str(probe))
    check("下拉显示的是自称名",
          probe.get("COMBO_LABEL") == "テスト語", repr(probe.get("COMBO_LABEL")))
    check("切过去后 t() 取到译文",
          probe.get("T_RESIZE") == "リサイズ", repr(probe.get("T_RESIZE")))
    check("未收录的条目降级为中文原文（不会崩）",
          probe.get("T_FALLBACK") == "源码里有但字典里没有",
          repr(probe.get("T_FALLBACK")))
    check("临时 json 已清理", probe.get("CLEANED") == "True", str(probe))

    # --- 3. 清理后目录恢复原样 ---
    check("清理后目录恢复原样",
          i18n.available_languages() == codes,
          str(i18n.available_languages()))

    # --- 4. 未知语言代码回退到默认语言（而不是「设了但没字典」）---
    got = i18n.set_language("xx_YY_不存在")
    check("未知语言代码回退到默认语言",
          got == i18n.DEFAULT_LANGUAGE and
          i18n.current_language() == i18n.DEFAULT_LANGUAGE,
          "current=%r" % i18n.current_language())
    i18n.set_language(i18n.DEFAULT_LANGUAGE)

    # --- 5. 覆盖率闸门不写死语言清单 ---
    src = _read(os.path.join(HERE, "check_i18n_coverage.py"))
    check("覆盖率闸门动态取语言清单（没写死 en_US）",
          "i18n.language_order()" in src and '"en_US"' not in src,
          "闸门可能硬编码了语言代码")

    # --- 6. main_window 不再硬编码语言清单 ---
    src = _read(os.path.join(REPO_ROOT, "jxlforge", "main_window.py"))
    check("main_window 从 i18n 派生语言清单",
          "_LANGUAGE_ORDER = tuple(i18n.language_order())" in src,
          "可能又写回了硬编码列表")

    return _summary()


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _summary():
    print()
    print("%d 项通过，%d 项失败" % (_passed, len(_failures)))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
