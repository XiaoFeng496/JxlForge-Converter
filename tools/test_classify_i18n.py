# -*- coding: utf-8 -*-
"""i18n 阶段 1 分级脚本的回归测试。

锁住的是**判定结论**，不是数量——源码之后还会改，写死「458 条」这种
数字只会让测试变成负担。所以断言全部走相对口径：
指定一批有代表性的字符串，断言它们落在哪一档。

最要紧的一条：**持久化标识绝不能被判成「翻」**。
那会让老用户的配置文件失配，属于损坏用户数据，比漏翻危险得多。

运行::

    python tools/test_classify_i18n.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import classify_i18n as cl  # noqa: E402

TRANSLATE, SKIP, MANUAL = cl.TRANSLATE, cl.SKIP, cl.MANUAL

# 已知「ID 兼显示名」：中文既是界面文字又被存进 QSettings，不能翻。
MUST_SKIP = [
    "调整大小",   # ACTION_TYPES
    "右下",      # WATERMARK_POSITIONS
    "缩略图",    # VIEW_MODES / GRID_SIZES
]

# 已知「纯显示名」：程序内部用的是配对的英文 ID，翻译安全。
MUST_TRANSLATE = [
    "空闲",        # CPU 优先级 addItem(label, "idle")
    "低于正常",     # CPU 优先级 addItem(label, "below_normal")
    "自动",        # CPU 核心数 addItem("自动", "auto")
    "文件名",       # TABLE_COLUMNS 五元组的显示名位
    "格式",        # TABLE_COLUMNS
    "原生",        # _THEME_LABELS 的 value（key 是 native）
    "跟随系统",      # _COLOR_SCHEME_LABELS 的 value
    # 注意：「简体中文」不在此列 —— 它是 i18n 基础设施里的语言自称名，
    # 已被 I18N_INFRA_FILES 排除（永远不该翻，否则覆盖率卡在 99.x%）。
    "有损",        # QRadioButton 文字，状态靠对象引用判断
    "原文件夹",      # QRadioButton 文字，同上
    "输入",        # addTab 标签页名
    "设置",        # addTab 标签页名
    "选择文件夹",     # getExistingDirectory 标题
    # _ADVANCED_SCHEMA 里的 group/label 是显示名，key 才是内部 ID
    "质量精细",   # "group": "质量精细"
    "编码策略",   # "group": "编码策略"
    "保真合成",   # "group": "保真合成"
    "容器输出",   # "group": "容器输出"
]

# 报错文案：raise 出来后会被 ``except Exception as exc`` 用 str(exc) 拼进
# 状态页日志或提示框 —— 用户看得到，必须翻。
# （曾误判为「内部文本不翻」，是错的。）
MUST_TRANSLATE_ERRORS = [
    "未找到 send2trash 库，无法将文件移入回收站；请先安装：pip install send2trash",
    "未安装 pywin32，无法保持创建时间；请先安装：pip install pywin32",
    "Pillow 未安装，无法执行图像处理动作。",
    "magic 不符，不是 PFM",
    "EXR 头部缺少有效的 dataWindow",
]

# [bench] 诊断输出：只进 stdout，不进界面。
MUST_SKIP_BENCH = ["全延迟(旧V1)", "同步关键设置(默认)"]

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


def index_by_text(entries):
    return {e["text"]: e for e in entries}


def main():
    entries = cl.build()
    by_text = index_by_text(entries)

    # --- 1. 结构完整性 ---
    check("产出非空", len(entries) > 100, "(共 %d 条)" % len(entries))
    check("每条都有置信度",
          all(e.get("confidence") in ("高", "中", "低") for e in entries))
    check("置信度取值合法",
          all(e.get("confidence") in ("高", "中", "低") for e in entries))

    # --- 2. 持久化标识必须被拦住（数据安全红线）---
    missing = [t for t in MUST_SKIP if t not in by_text]
    check("ID 样本都还在清单里", not missing, str(missing))
    wrong = [t for t in MUST_SKIP
             if t in by_text and by_text[t]["verdict"] != SKIP]
    check("ID 兼显示名一律不翻", not wrong, str(wrong))

    # --- 3. 纯显示名必须放行（否则界面翻不干净）---
    missing = [t for t in MUST_TRANSLATE if t not in by_text]
    check("显示名样本都还在清单里", not missing, str(missing))
    wrong = [t for t in MUST_TRANSLATE
             if t in by_text and by_text[t]["verdict"] != TRANSLATE]
    check("配对的显示名一律可翻", not wrong, str(wrong))

    # --- 3b. 报错文案必须翻（会被 str(exc) 显示给用户）---
    # 回填阶段后这些文案已被 i18n.t(...) 包住，classify 会判成「已翻译」。
    # 真实不变量是「用户能在英文界面看到译文」，即字典里确有译文。
    import json as _json  # noqa: E402
    import os as _os  # noqa: E402
    _en_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                             "libjxl_gui", "i18n", "en_US.json")
    _en = _json.load(open(_en_path, encoding="utf-8"))
    missing = [t for t in MUST_TRANSLATE_ERRORS if t not in by_text]
    check("报错文案样本都还在清单里", not missing, str(missing))
    wrong = [t for t in MUST_TRANSLATE_ERRORS if t in by_text and t not in _en]
    check("raise 的报错文案一律有译文", not wrong, str(wrong))

    # --- 4. bench 诊断输出不翻 ---
    missing = [t for t in MUST_SKIP_BENCH if t not in by_text]
    check("bench 样本都还在清单里", not missing, str(missing))
    wrong = [t for t in MUST_SKIP_BENCH
             if t in by_text and by_text[t]["verdict"] != SKIP]
    check("[bench] 诊断输出不翻", not wrong, str(wrong))

    # --- 4b. i18n 基础设施自身的文本必须排除 ---
    # 「简体中文」是语言自称名，永远不该翻。留在清单里会让覆盖率永久卡在 99.x%。
    infra = [e for e in entries
             if any(o["file"] == "libjxl_gui/i18n.py" for o in e["occurrences"])]
    check("i18n 基础设施的文本已被排除", not infra,
          str([e["text"] for e in infra][:3]))

    # --- 4c. 纯标点（无汉字）不是文案 ---
    # ex.has_cjk 会把「、」这类中文标点算作中文抽进来，但它只是 join 的分隔符。
    import re as _re
    punct = [e for e in entries
             if not _re.search(r"[\u4e00-\u9fff]", e["text"])]
    wrong_punct = [e["text"] for e in punct if e["verdict"] != SKIP]
    check("纯标点一律不翻", not wrong_punct, str(wrong_punct))

    # --- 5. 规则覆盖率：不该留下大量待定 ---
    manual_n = sum(1 for e in entries if e["verdict"] == MANUAL)
    check("待定归零（规则已覆盖）", manual_n == 0, "(待定 %d 条)" % manual_n)
    low_n = sum(1 for e in entries if e.get("confidence") == "低")
    check("低置信控制在小比例", low_n <= len(entries) * 0.05,
          "(低置信 %d / %d)" % (low_n, len(entries)))

    # --- 6. 合并规则：一处不翻，整条不翻 ---
    mixed = [e for e in entries
             if len({o["verdict"] for o in e["occurrences"]}) > 1]
    bad = [e["text"] for e in mixed
           if SKIP in {o["verdict"] for o in e["occurrences"]}
           and e["verdict"] != SKIP]
    check("同一文本一处不翻则整条不翻", not bad, str(bad))

    # --- 7. 理由字段非空（用户看不懂代码，全靠这句话判断）---
    check("每条都有人话理由", all(e.get("reason") for e in entries))

    # --- 8. HTML 复核页可生成且含筛选入口 ---
    path = cl.render_html(entries)
    with open(path, encoding="utf-8") as fh:
        doc = fh.read()
    check("复核页已生成", os.path.exists(path))
    check("复核页含置信度列", "把握" in doc)
    check("复核页含低置信筛选", "仅看低置信" in doc)

    print("\n%d 项通过，%d 项失败" % (_passed, len(_failures)))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
