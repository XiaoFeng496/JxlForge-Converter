# -*- coding: utf-8 -*-
"""i18n 阶段 3：把「翻」文案的字面量机械回填为 i18n.t(...)。

设计要点
--------
- 字典（en_US.json）已 100% 译完，但源码里只有阶段 6 的 11 处循环式
  ``t()`` 调用。要让英文界面真正渲染，必须把其余「翻」文案在源码里的
  字面量包上 ``i18n.t()``。
- 判定「该不该包」只看**字典里的 verdict**：verdict=="翻" 的文案在分类阶段
  已被确认是「纯显示文本、绝不当标识符」(否则会被判 不翻)。所以只要源码里
  出现该字面量，就可安全包 ``t()``——天然避开阶段 6 的 ID 陷阱。
- 用 AST 枚举源码里所有字符串字面量，按**文本内容**匹配 verdict=="翻" 集合，
  不依赖行号（行号平移会静默失准）。
- 区分节点类型，安全分级：
    * 普通 Constant(str)（非 f-string/拼接/.format 的一部分）→ 包 t()
    * f-string (JoinedStr) 内的字面量 → 跳过（须模板化，单独处理）
    * ``.format()`` / ``%`` 格式化的字符串 → 跳过（须模板化，单独处理）
    * 字符串拼接 ``+`` → 跳过（须整体处理，单独处理）
    * docstring → 跳过（不是 UI 文本）
    * 已被 ``i18n.t(`` 包住的 → 跳过（幂等）

用法
----
    python tools/i18n_wrap_source.py            # 默认 dry-run，打印统计与改动预览
    python tools/i18n_wrap_source.py --apply    # 真正改写源文件
    python tools/i18n_wrap_source.py --apply --files jxlforge/main_window.py
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "jxlforge")

SKIP_FORMAT_ATTRS = {"format", "format_map"}


def load_translate_set():
    path = os.path.join(HERE, "i18n_work", "strings_classified.json")
    data = json.load(open(path, encoding="utf-8"))
    # 兼容 list / dict 两种结构
    entries = data if isinstance(data, list) else data.get("records", data.get("items", []))
    out = set()
    for e in entries:
        if e.get("verdict") == "翻":
            out.add(e["text"])
    return out


def char_abs_index(lines, lineno, char_col):
    """把 (行号, 字符列) 转成全文绝对字符下标。lines 不含换行符。"""
    idx = 0
    for i in range(lineno - 1):
        idx += len(lines[i]) + 1  # +1 为被 split 掉的换行
    return idx + char_col


def byte_to_char_col(line_bytes, byte_col):
    """行内字节偏移 -> 字符偏移（行字节不含换行）。"""
    return len(line_bytes[:byte_col].decode("utf-8"))


def classify_node(node, parent):
    """返回 ('plain' | 'fstring' | 'format' | 'concat' | 'docstring' | 'already' | 'other', extra)"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
        # docstring
        if isinstance(parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            # 仅当是首个语句且为 expr 时算 docstring
            if isinstance(parent, ast.Lambda):
                return "docstring", None
            body = parent.body if hasattr(parent, "body") else None
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return "docstring", None
        # f-string 内部字面量
        if isinstance(parent, (ast.JoinedStr, ast.FormattedValue)):
            return "fstring", None
        # .format() / .format_map()
        if isinstance(parent, ast.Attribute) and parent.attr in SKIP_FORMAT_ATTRS:
            return "format", None
        # 拼接 + （任一侧是字符串）
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add):
            # 仅当它真正参与字符串拼接时跳过（可能只是数字相加，但字符串 + 误判无害）
            return "concat", None
        return "plain", text
    if isinstance(node, ast.JoinedStr):
        return "fstring", None
    return "other", None


# 显示名同时被当字典 key / 标识符比较用的字符串（阶段 6 同款陷阱）。
# 这类不能机械包 t()：包了会在「构建时按当前语言求值」与「模块级冻结」之间
# 产生不一致（KeyError 崩窗）。改成「源码保持纯中文，显示点单独 t()」。
EXCLUDE_IDS = {
    "质量精细", "编码策略", "保真合成", "容器输出",
}


def analyze_file(path, translate_set, edits, skipped):
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    line_bytes = [ln.encode("utf-8") for ln in lines]
    tree = ast.parse(src, filename=path)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []
            self.fn_depth = 0

        def generic_visit(self, node):
            self.stack.append(node)
            is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_fn:
                self.fn_depth += 1
            # 处理当前节点上的字符串
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                parent = self.stack[-2] if len(self.stack) >= 2 else None
                kind, _ = classify_node(node, parent)
                text = node.value
                if kind in ("fstring", "format", "concat", "docstring"):
                    if kind != "docstring":
                        skipped.setdefault(kind, {}).setdefault(text, 0)
                        skipped[kind][text] += 1
                elif kind == "plain":
                    inside_fn = self.fn_depth > 0
                    is_dictkey = (isinstance(parent, ast.Dict)
                                  and any(k is node for k in parent.keys))
                    already = False
                    if inside_fn and text in translate_set and not is_dictkey \
                            and text not in EXCLUDE_IDS:
                        start = char_abs_index(
                            lines, node.lineno,
                            byte_to_char_col(line_bytes[node.lineno - 1], node.col_offset))
                        end = char_abs_index(
                            lines, node.end_lineno,
                            byte_to_char_col(line_bytes[node.end_lineno - 1], node.end_col_offset))
                        prefix = src[max(0, start - 9):start]
                        if prefix.rstrip().endswith("i18n.t"):
                            already = True
                        else:
                            edits.append((path, start, end, src[start:end], text))
                    if already:
                        skipped.setdefault("already", {}).setdefault(text, 0)
                        skipped["already"][text] += 1
                    elif not inside_fn and text in translate_set:
                        # 模块级/类级：import 时按默认 zh_CN 冻结，包 t() 会在
                        # 英文模式下仍显示中文（且若当 key 用会崩）。安全跳过。
                        skipped.setdefault("modulelevel", {}).setdefault(text, 0)
                        skipped["modulelevel"][text] += 1
                    elif is_dictkey and text in translate_set:
                        skipped.setdefault("dictkey", {}).setdefault(text, 0)
                        skipped["dictkey"][text] += 1
                    elif text in EXCLUDE_IDS:
                        skipped.setdefault("displaykey", {}).setdefault(text, 0)
                        skipped["displaykey"][text] += 1
            elif isinstance(node, ast.JoinedStr):
                start = char_abs_index(
                    lines, node.lineno,
                    byte_to_char_col(line_bytes[node.lineno - 1], node.col_offset))
                end = char_abs_index(
                    lines, node.end_lineno,
                    byte_to_char_col(line_bytes[node.end_lineno - 1], node.end_col_offset))
                skipped.setdefault("fstring", {}).setdefault(src[start:end], 0)
                skipped["fstring"][src[start:end]] += 1
            super().generic_visit(node)
            if is_fn:
                self.fn_depth -= 1
            self.stack.pop()

    Visitor().visit(tree)


def ensure_import(src, lines):
    """确保存在 from . import i18n（插到模块顶层顶部，绝不可插进 try/函数体内）。"""
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("from . import i18n") or s.startswith("from .i18n import"):
            return src, False
    # 插入点：文件顶部。跳过 coding 声明（允许落在第二行），
    # 再跳过后置的 from __future__ import（future 必须最先）。
    insert_at = 0
    if lines and lines[0].lstrip().startswith("#"):
        insert_at = 1
    while insert_at < len(lines) and lines[insert_at].lstrip().startswith("from __future__ import"):
        insert_at += 1
    new_lines = lines[:insert_at] + ["from . import i18n", ""] + lines[insert_at:]
    return "\n".join(new_lines), True


def apply_edits(files, edits):
    by_file = {}
    for path, start, end, token, text in edits:
        by_file.setdefault(path, []).append((start, end, token, text))
    changed = []
    for path, fl in by_file.items():
        src0 = open(path, encoding="utf-8").read()
        # 1) 先在「未插 import 的原文」上按绝对字符下标（倒序）应用编辑
        fl_sorted = sorted(fl, key=lambda e: e[0], reverse=True)
        for start, end, token, text in fl_sorted:
            replacement = "i18n.t(" + token + ")"
            src0 = src0[:start] + replacement + src0[end:]
        # 2) 再补 import（在顶部插入，不影响上面的下标）
        lines0 = src0.split("\n")
        src0, added = ensure_import(src0, lines0)
        open(path, "w", encoding="utf-8").write(src0)
        changed.append((path, len(fl), added))
    return changed


def main():
    apply = "--apply" in sys.argv
    restrict = []
    if "--files" in sys.argv:
        i = sys.argv.index("--files")
        restrict = sys.argv[i + 1:]
    files = []
    for fn in os.listdir(PKG):
        if fn.endswith(".py"):
            p = os.path.join(PKG, fn)
            if restrict and p not in restrict and fn not in restrict:
                continue
            files.append(p)
    files.sort()

    translate_set = load_translate_set()
    edits = []
    skipped = {}
    for p in files:
        analyze_file(p, translate_set, edits, skipped)

    # 统计
    from collections import Counter
    by_file = Counter(os.path.basename(p) for p, *_ in edits)
    print("verdict=='翻' 文案种类数：", len(translate_set))
    print("将包 t() 的字面量出现次数：", len(edits))
    for fn, n in by_file.most_common():
        print("   %-22s %d" % (fn, n))
    print("\n跳过（需模板化/人工/安全）：")
    for kind in ("modulelevel", "dictkey", "displaykey", "fstring", "format", "concat", "already"):
        d = skipped.get(kind, {})
        if d:
            print("  [%s] %d 种 / %d 处" % (kind, len(d), sum(d.values())))
            for t, c in sorted(d.items(), key=lambda x: -x[1])[:10]:
                print("      %3d  %r" % (c, (t or "")[:40]))

    if not apply:
        print("\n== DRY-RUN：未改动任何文件。加 --apply 执行。==")
        # 预览前若干个改动
        print("\n预览（前 12 处）：")
        for path, start, end, token, text in edits[:12]:
            print("  %s : %s -> i18n.t(%s)" % (os.path.basename(path), token[:30], token[:30]))
        return

    changed = apply_edits(files, edits)
    print("\n已改写文件：")
    for path, n, added in changed:
        print("  %-22s 包 %d 处  import+%s" % (os.path.basename(path), n, added))


if __name__ == "__main__":
    main()
