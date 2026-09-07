# -*- coding: utf-8 -*-
"""i18n 覆盖率闸门：一条命令跑完「抽取 → 分级 → 各语言覆盖率」。

这是让 i18n **可持续**的那一环。没有它，工作流是这样的：

    加功能 → 顺手写中文 → 字典悄悄落后 → 攒够一堆 → 再来一轮大批量翻译

有了它，工作流变成：

    加功能 → 顺手写中文 → 跑这条命令（或 CI 跑）→ 红色 → 顺手补 2 条

成本从「一次性 422 条」摊薄成「每次 1-2 条」，这才是低维护成本的关键。

设计要点
--------
- **默认自动重跑抽取与分级**：不做这步，清单会因为源码行号变动而悄悄失准
  （阶段 1 踩过：行号错位 → 结构分析静默失效，只报「判错」不报「缺失」）。
  闸门必须自己保证输入是新鲜的。加 ``--no-refresh`` 可跳过（纯查字典时用）。
- **应翻集合 = 分级为「翻」的 + 阶段 6 标记过的内部 ID**。
  后者在分级表里是「不翻」（源码不动），但界面显示走 ``t()``，
  **字典里必须有译文**，漏了界面就会退回中文。
- **缺啥按出现次数排序**：先翻出现 10 次的「添加文件」，
  再翻只出现 1 次的冷门提示，单位精力收益最高。
- **未收录的不会让界面崩**：``t()`` 查不到就返回原文，所以覆盖率 30%
  也能正常用英文界面，只是残留中文。这是刻意的渐进式降级。

用法::

    python tools/check_i18n_coverage.py              # 报告
    python tools/check_i18n_coverage.py --min 80     # 低于 80% 退出码 1（CI 用）
    python tools/check_i18n_coverage.py --top 30     # 列出最该先翻的 30 条
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import classify_i18n as cl  # noqa: E402
import extract_i18n as ex  # noqa: E402

sys.path.insert(0, REPO_ROOT)
from jxlforge import i18n  # noqa: E402

TRANSLATE, SKIP = cl.TRANSLATE, cl.SKIP
REPORT_JSON = os.path.join(HERE, "i18n_work", "coverage.json")


def target_strings(entries):
    """应翻集合：分级为「翻」的，或阶段 6 标记过（显示名已分离）的 ID。"""
    return [e for e in entries if e["verdict"] == TRANSLATE or e.get("stage6")]


def load_language(code):
    """读某语言的译文字典（不含下划线开头的元信息键）。"""
    path = os.path.join(i18n._I18N_DIR, "%s.json" % code)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if not k.startswith("_")}


def report_for(code, targets):
    """返回该语言的覆盖率报告。"""
    d = load_language(code)
    if d is None:
        return None
    done, missing = [], []
    for e in targets:
        text = e["text"]
        if text in d:
            done.append(e)
        else:
            missing.append(e)
    # 按出现次数降序：先翻最常出现的，单位精力收益最高
    missing.sort(key=lambda e: (-e["count"], e["text"]))
    total = len(targets)
    return {
        "code": code,
        "name": i18n.language_name(code),
        "translated": len(done),
        "total": total,
        "missing": len(missing),
        "percent": (100.0 * len(done) / total) if total else 100.0,
        # 字典里可能有源码已删掉的陈旧条目，一并报出来
        "orphans": sorted(set(d) - {e["text"] for e in targets}),
        "top_missing": missing,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="i18n 覆盖率闸门")
    ap.add_argument("--min", type=float, default=None,
                    help="覆盖率低于此百分比则退出码 1（CI 用）")
    ap.add_argument("--top", type=int, default=20,
                    help="列出最该优先翻译的前 N 条（按出现次数）")
    ap.add_argument("--lang", default=None, help="只看某一种语言")
    ap.add_argument("--no-refresh", action="store_true",
                    help="跳过重新抽取/分级，直接用上次的清单（快，但可能过期）")
    args = ap.parse_args(argv)

    if not args.no_refresh:
        ex.main(["--out", cl.RAW_JSON])
        entries = cl.build()
    else:
        entries = cl.build()

    counts = {k: sum(1 for e in entries if e["verdict"] == k)
              for k in (TRANSLATE, SKIP)}
    targets = target_strings(entries)
    s6 = sum(1 for e in entries if e.get("stage6"))

    print("=" * 68)
    print("i18n 覆盖率%s" % ("（已刷新清单）" if not args.no_refresh
                          else "（用上次清单，可能过期）"))
    print("=" * 68)
    print("源码中文字符串：出现 %d 次 / 去重 %d 条" % (
        sum(e["count"] for e in entries), len(entries)))
    print("  分级：翻 %d / 不翻 %d" % (counts[TRANSLATE], counts[SKIP]))
    print("  阶段 6 已分离（源码不改但仍需译文）：%d 条" % s6)
    print("  → 应翻目标集合：%d 条" % len(targets))
    print()

    codes = [args.lang] if args.lang else i18n.language_order()
    reports = []
    for code in codes:
        if code == i18n.DEFAULT_LANGUAGE:
            continue  # 默认语言不建字典，源码即译文
        r = report_for(code, targets)
        if r is None:
            print("⚠️  语言 %s 没有字典文件（i18n/%s.json）" % (code, code))
            continue
        reports.append(r)
        print("【%s / %s】%d / %d 条  = %.1f%%   缺 %d 条"
              % (r["code"], r["name"], r["translated"], r["total"],
                 r["percent"], r["missing"]))
        if r["orphans"]:
            print("     字典里有 %d 条源码已不存在的陈旧译文（可清理）："
                  % len(r["orphans"]))
            for t in r["orphans"][:5]:
                print("       - %s" % t)
    print()

    # 优先翻译建议
    if reports and args.top:
        primary = reports[0]
        print("=" * 68)
        print("【%s】最该先翻的 %d 条（按出现次数降序，先翻高收益的）"
              % (primary["code"], min(args.top, len(primary["top_missing"]))))
        print("=" * 68)
        for e in primary["top_missing"][:args.top]:
            where = ", ".join("%s:%d" % (o["file"].split("/")[-1], o["lineno"])
                              for o in e["occurrences"][:2])
            flag = " [ID分离]" if e.get("stage6") else ""
            print("  ×%-3d %s%s" % (e["count"], e["text"][:44], flag))
            print("        %s" % where)
        print()

    # 落一份机器可读的报告，便于跨时间对比进度
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with open(REPORT_JSON, "w", encoding="utf-8") as fh:
        json.dump({
            "target_total": len(targets),
            "stage6_count": s6,
            "languages": [{k: v for k, v in r.items() if k != "top_missing"}
                          for r in reports],
        }, fh, ensure_ascii=False, indent=2)
    print("报告已写入：%s" % os.path.relpath(REPORT_JSON, REPO_ROOT))

    if args.min is not None:
        bad = [r for r in reports if r["percent"] < args.min]
        if bad:
            print("\n❌ 未达标（门槛 %.1f%%）：%s"
                  % (args.min, ", ".join("%s %.1f%%" % (r["code"], r["percent"])
                                         for r in bad)))
            return 1
        print("\n✅ 所有语言均达标（门槛 %.1f%%）" % args.min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
