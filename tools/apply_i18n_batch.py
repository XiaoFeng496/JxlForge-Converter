# -*- coding: utf-8 -*-
"""把 tools/i18n_work/batch_NN.json 合并进 libjxl_gui/i18n/<lang>.json。

为什么要分批：翻译 421 条是 token 消耗大头，中途被限流中断很正常。
每批一个独立文件，合并是幂等的——重跑只会覆盖同名键，不会丢进度。

合并前会做三项校验，防止手工搬运把字典写坏：
- **占位符守恒**：译文里 %s/%d/%.1f 等格式化占位符必须与原文**完全一致**
  （漏一个就是运行时 "not enough arguments for format string"）。
- **换行守恒**：`\n` 的个数必须一致（否则多行提示会挤成一行）。
- **键必须真的存在于源码**：拼错的键会永远匹配不上，静默成为孤儿。

用法::

    python tools/apply_i18n_batch.py                 # 合并所有批次
    python tools/apply_i18n_batch.py 01 02           # 只合并指定批次
    python tools/apply_i18n_batch.py --lang en_US --dry-run
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, "i18n_work")
I18N_DIR = os.path.join(REPO_ROOT, "libjxl_gui", "i18n")

sys.path.insert(0, HERE)
sys.path.insert(0, REPO_ROOT)

# %s / %d / %.1f / %(name)s —— 顺序与个数都要一致
_PLACEHOLDER = re.compile(r"%(?:\([^)]*\))?[-+# 0]*[\d.]*[sdfgexXocrua%]")
_FMT = re.compile(r"\{[^}]*\}")


def placeholders(text):
    """返回 (printf 占位符序列, .format 占位符集合)。"""
    return tuple(_PLACEHOLDER.findall(text)), set(_FMT.findall(text))


def check_pair(src, dst):
    """返回问题列表；空列表表示这一对没问题。"""
    problems = []
    if src == dst:
        return problems  # 与原文相同不算错（有些词本来就一样）
    sp, sf = placeholders(src)
    dp, df = placeholders(dst)
    if sp != dp:
        problems.append("占位符不一致：原文 %s / 译文 %s" % (list(sp), list(dp)))
    if sf != df:
        problems.append(".format 占位符不一致：%s / %s" % (sorted(sf), sorted(df)))
    if src.count("\n") != dst.count("\n"):
        problems.append("换行数不一致：%d / %d"
                        % (src.count("\n"), dst.count("\n")))
    # 冒号/括号等结尾标点提示（不是硬错误，仅提醒保持一致）
    if src.endswith("：") and not dst.rstrip().endswith(":"):
        problems.append("注意：原文以全角冒号结尾，译文建议以 : 结尾")
    return problems


def source_keys():
    """源码里真实存在的键集合（分级结果 + 阶段 6 的 ID）。"""
    import classify_i18n as cl
    entries = cl.build()
    return {e["text"] for e in entries}


def main(argv=None):
    ap = argparse.ArgumentParser(description="合并 i18n 翻译批次")
    ap.add_argument("batches", nargs="*", help="批次号，如 01 02；省略则全部")
    ap.add_argument("--lang", default="en_US")
    ap.add_argument("--dry-run", action="store_true", help="只校验不写入")
    args = ap.parse_args(argv)

    if args.batches:
        paths = [os.path.join(WORK, "batch_%s.json" % b) for b in args.batches]
    else:
        paths = sorted(glob.glob(os.path.join(WORK, "batch_*.json")))
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print("没有找到批次文件")
        return 1

    merged = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            print("❌ %s 不是 JSON 对象" % os.path.basename(p))
            return 1
        for k, v in data.items():
            if k.startswith("_"):
                continue
            merged[k] = v
        print("  载入 %-18s %3d 条" % (os.path.basename(p), len(data)))
    print()

    known = source_keys()
    errors, warns, orphans = [], [], []
    for src, dst in merged.items():
        if not isinstance(dst, str):
            errors.append("译文不是字符串：%r -> %r" % (src, dst))
            continue
        if src not in known:
            orphans.append(src)
        for msg in check_pair(src, dst):
            (errors if msg.startswith(("占位符", ".format", "换行"))
             else warns).append("%s\n      原文：%r\n      译文：%r" % (msg, src, dst))

    print("合计 %d 条待合并" % len(merged))
    if orphans:
        print("\n⚠️ 源码里查不到的键 %d 条（拼错或已被删除）：" % len(orphans))
        for k in orphans[:20]:
            print("   - %r" % k)
    if errors:
        print("\n❌ 硬错误 %d 处（占位符/换行不匹配，会导致运行时崩溃）：" % len(errors))
        for e in errors[:20]:
            print("   %s" % e)
        return 1
    if warns:
        print("\n⚠️ 风格提醒 %d 处（不阻断）：" % len(warns))
        for w in warns[:15]:
            print("   %s" % w)

    target = os.path.join(I18N_DIR, "%s.json" % args.lang)
    with open(target, encoding="utf-8") as fh:
        current = json.load(fh)

    added = sum(1 for k in merged if k not in current)
    changed = sum(1 for k in merged
                  if k in current and current[k] != merged[k])
    current.update(merged)

    # 元信息键始终排在前面，译文按原文排序，便于人工 diff
    meta = {k: v for k, v in current.items() if k.startswith("_")}
    body = {k: current[k] for k in sorted(current) if not k.startswith("_")}
    final = dict(meta)
    final.update(body)

    print("\n%s -> 新增 %d 条，修改 %d 条，合计 %d 条"
          % (os.path.basename(target), added, changed, len(body)))
    if args.dry_run:
        print("（--dry-run，未写入）")
        return 0

    with open(target, "w", encoding="utf-8") as fh:
        json.dump(final, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("已写入 %s" % os.path.relpath(target, REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
