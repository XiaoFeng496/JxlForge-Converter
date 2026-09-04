# -*- coding: utf-8 -*-
r"""阶段 0：抽取产品代码中的中文字符串字面量，产出清单供人工分级与后续回填。

只读、零副作用：不 import 也不修改任何产品代码，只在 ``tools/i18n_work/``
下写清单文件。

用法：
    python tools/extract_i18n.py                 # 扫描 libjxl_gui/ 并写清单
    python tools/extract_i18n.py --stats         # 只打印统计，不写文件
    python tools/extract_i18n.py --roots libjxl_gui libjxl_gui/main_window.py

产出字段（每条记录）：
    file/lineno/col_offset/end_lineno/end_col_offset  阶段 3 精确回填用
        —— 必须记列偏移，一行可能有多个字符串
    text       原始字面量（已还原为真实值，含换行与引号）
    context    所处调用/常量名，如 ``QLabel`` / ``setToolTip`` / ``addItems``
    hint       自动初判：ID_HINT / UI_HINT / UNKNOWN（阶段 1 人工复核）

注意：f-string 在 AST 里是 JoinedStr 而非 Constant，不会被抽到，脚本会单独
列进 ``fstring_warnings`` 供人工补录（本项目仅个位数）。
"""
import argparse
import ast
import json
import os
import sys

# ---- 不可翻译：中文既是显示名又是 QSettings 持久化值 / 内部 ID ----
ID_CONSTANTS = {
    "ACTION_TYPES",
    "WATERMARK_POSITIONS",
    "VIEW_MODES",
    "GRID_SIZES",
    "THUMB_SIZES",
}

# ---- 典型 UI 调用点：命中即初步判为「界面可见文本」 ----
UI_CALLS = {
    "QLabel", "QPushButton", "QGroupBox", "QCheckBox", "QRadioButton",
    "QToolButton", "QMenu", "QAction", "QTabWidget", "QMessageBox",
    "QInputDialog", "QFileDialog", "QToolTip", "QWhatsThis",
    "setText", "setToolTip", "setWindowTitle", "setTitle", "setPlaceholderText",
    "setStatusTip", "setTabText", "addTab", "addItem", "addItems", "insertItem",
    "setItemText", "setHeaderData", "setHorizontalHeaderLabels",
    "setSectionText", "appendPlainText", "appendHtml", "insertPlainText",
    "append", "insert", "setPlainText", "setHtml", "insertHtml",
    "information", "warning", "critical", "question", "about",
    "showMessage", "setLabelText", "exec", "addAction", "setWhatsThis",
    "getText", "getItem", "getInt", "getDouble", "getMultiLineText",
    "QTableWidgetItem", "QListWidgetItem",
}

CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK 统一表意文字
    (0x3000, 0x303F),    # CJK 标点
    (0xFF00, 0xFFEF),    # 全角形式
)


def has_cjk(text):
    for ch in text:
        cp = ord(ch)
        for lo, hi in CJK_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def dotted_name(node):
    """把 ``self.log.appendPlainText`` 这类节点还原成点分字符串。"""
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return "%s.%s" % (base, node.attr) if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return ""


def collect_docstring_lines(tree):
    """模块 / 类 / 函数的首个字符串字面量表达式是 docstring，不参与翻译。

    注意：i18n 回填会在文件顶部插入 ``from . import i18n``，把模块 docstring
    从 body[0] 挤到 body[1]。这里跳过前导的 import / __future__ / 注释，
    认「第一个字符串字面量表达式」为文档串，才能稳定排除它（文档串不是 UI 文本）。
    """
    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        for stmt in body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            if (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)):
                lines.add(stmt.value.lineno)
            break  # 遇到非 import 语句即停止（无论是否 docstring）
    return lines


def build_parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def enclosing_constant_name(node, parents):
    """向上找最近的一层 ``NAME = [...]`` / ``NAME = (...)`` 赋值，返回常量名。"""
    seen = 0
    cur = node
    while cur is not None and seen < 6:
        parent = parents.get(cur)
        if parent is None:
            return ""
        if isinstance(parent, ast.Assign):
            for target in parent.targets:
                if isinstance(target, ast.Name):
                    return target.id
            return ""
        if isinstance(parent, (ast.Call, ast.Dict, ast.keyword)):
            return ""
        cur = parent
        seen += 1
    return ""


#: 这些节点对「字符串用在哪」是透明的，上溯时直接穿过。
#: 缺了 BinOp 就会漏掉 ``log.append("完成 %d" % n)`` 这类格式化的日志文本
#: —— 实测会漏掉三分之二的状态页日志。
TRANSPARENT_NODES = (
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.IfExp, ast.Starred,
    ast.Attribute, ast.Subscript, ast.Await, ast.keyword,
    ast.List, ast.Tuple, ast.Set, ast.Dict,
)

MAX_HOPS = 10


def context_of(node, parents):
    """返回该字面量所处的调用名 / 所属常量名，用于自动分级。

    上溯时穿过格式化运算与容器字面量，直到落到调用、赋值或比较上：
        ``self.log.append("完成 %d" % n)``  → ``append``
        ``addItems(["调整大小", "旋转"])``   → ``addItems``
        ``ACTION_TYPES = ["调整大小", ...]`` → ``ACTION_TYPES``
        ``if mode == "缩略图":``             → ``<compare>``
    """
    cur = node
    for _ in range(MAX_HOPS):
        parent = parents.get(cur)
        if parent is None:
            break
        if isinstance(parent, ast.Call):
            name = dotted_name(parent.func)
            return name.split(".")[-1] if name else "<literal>"
        if isinstance(parent, ast.Assign):
            for target in parent.targets:
                if isinstance(target, ast.Name):
                    return target.id
            return "<literal>"
        if isinstance(parent, ast.Compare):
            return "<compare>"
        if isinstance(parent, TRANSPARENT_NODES):
            cur = parent
            continue
        break
    # 兜底：模块级常量，如 ACTION_TYPES = ["调整大小", ...]
    const = enclosing_constant_name(node, parents)
    return const if const else "<literal>"


def is_dict_key(node, parents):
    parent = parents.get(node)
    return isinstance(parent, ast.Dict) and node in parent.keys


#: 赋值目标名带这些后缀的，基本都是界面文案（tooltip / 标签 / 提示语变量）。
#: 往 UI 方向误判代价低（最多多翻一条），往 ID 方向误判会破坏持久化，故放宽。
UI_NAME_SUFFIXES = (
    "tip", "label", "text", "msg", "title", "note", "desc",
    "hint", "caption", "labels", "tips", "names", "prompt",
)


def classify(text, context, parents, node):
    """自动初判。宁可判 UNKNOWN 交人工，也不要把内部 ID 误判成 UI 文本。"""
    if context in ID_CONSTANTS or is_dict_key(node, parents):
        return "ID_HINT"
    # 与变量比较的中文（if mode == "缩略图"）几乎都是内部标识，不是给看的字符串
    if context == "<compare>":
        return "ID_HINT"
    if context in UI_CALLS or context.split(".")[-1] in UI_CALLS:
        return "UI_HINT"
    low = context.lower()
    if "_" in low or low.islower():
        if low.endswith(UI_NAME_SUFFIXES):
            return "UI_HINT"
    return "UNKNOWN"


def scan_file(path, rel):
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    doc_lines = collect_docstring_lines(tree)
    parents = build_parent_map(tree)

    source_lines = source.split("\n")
    span_errors = []
    # f-string 内部的 Constant 是 JoinedStr 的片段，不是独立字符串字面量，
    # 已单独收进 fstring_warnings，这里必须跳过（否则坐标无从扫起）。
    joined_parts = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            for sub in ast.walk(n):
                if isinstance(sub, ast.Constant):
                    joined_parts.add(id(sub))

    records = []
    for node in ast.walk(tree):
        if id(node) in joined_parts:
            continue
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        if node.lineno in doc_lines:
            continue
        if not has_cjk(node.value):
            continue
        context = context_of(node, parents)
        char_col = byte_col_to_char_col(source_lines[node.lineno - 1],
                                        node.col_offset)
        end_lineno, end_col = node.lineno, char_col
        try:
            end_lineno, end_col = scan_literal_span(source_lines, node.lineno,
                                                    char_col)
        except ValueError as exc:                 # noqa: BLE001 - 仅报告
            span_errors.append((rel, node.lineno, str(exc)))
        records.append({
            "file": rel,
            "lineno": node.lineno,
            "col_offset": char_col,
            # 以下坐标均为**字符**偏移（已换算），可直接用于源码切片
            "end_lineno": end_lineno,
            "end_col_offset": end_col,
            "text": node.value,
            "context": context,
            "hint": classify(node.value, context, parents, node),
            # 相邻字面量（"a" "b"）在 AST 里会被合并成一个 Constant，坐标横跨
            # 首段起点到末段终点。阶段 3 回填按坐标整块替换即可，但要留意
            # 替换后会丢掉原有的换行排版。
            "multiline": end_lineno != node.lineno,
        })

    # f-string 里的中文：AST 中是 JoinedStr，单独提示人工处理
    fstring_warnings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and has_cjk(sub.value)):
                fstring_warnings.append({
                    "file": rel,
                    "lineno": sub.lineno,
                    "text": sub.value,
                    "context": "f-string",
                })
    records.sort(key=lambda r: (r["lineno"], r["col_offset"]))
    return records, fstring_warnings, span_errors


# CPython 3.12 的 ast 对「调用实参中的常量」会把 end_col_offset 算到整个
# Call 的末尾（实测 ``foo("中文", 1)`` 的 end_col 会切出 ``, 1)``），
# 直接拿它切片会静默写坏代码。故自行扫描字面量边界，ast 只用来找起点。
def byte_col_to_char_col(line, byte_col):
    r"""把 ast 的列号换算成字符列号。

    ⚠️ 中文项目的头号陷阱：ast 的 ``col_offset`` / ``end_col_offset`` 是
    **UTF-8 字节偏移**，不是字符偏移。源码里只要出现中文，后面的列号就会
    整体偏大（每个汉字多 2），直接拿去切 Python 字符串必然错位。
    """
    if byte_col <= 0:
        return 0
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="replace"))


_PREFIX_CHARS = set("rRbBuUfF")
_QUOTES = "'\""
_WS = " \t\r\f\v"


def _backslash_parity(line, end):
    """返回 end 之前连续反斜杠个数的奇偶（偶数=该引号未被转义）。"""
    n, p = 0, end - 1
    while p >= 0 and line[p] == "\\":
        n += 1
        p -= 1
    return n % 2


def _scan_one(lines, i, j):
    """扫描一个字符串片段，返回其结束位置之后一位的 (行索引, 列索引)。"""
    line = lines[i]
    while j < len(line) and line[j] in _PREFIX_CHARS:
        j += 1
    if j >= len(line) or line[j] not in _QUOTES:
        raise ValueError("第 %d 行第 %d 列不是字符串字面量" % (i + 1, j))
    quote = line[j]
    if line[j:j + 3] == quote * 3:
        closing, k = quote * 3, j + 3
        while True:
            while True:
                end = lines[i].find(closing, k)
                if end != -1:
                    break
                i += 1
                if i >= len(lines):
                    raise ValueError("三引号字符串未闭合")
                k = 0
            if _backslash_parity(lines[i], end) == 0:
                return i, end + 3
            k = end + 1
    k = j + 1
    while True:
        end = line.find(quote, k)
        if end == -1:
            raise ValueError("字符串未闭合，行 %d" % (i + 1))
        if _backslash_parity(line, end) == 0:
            return i, end + 1
        k = end + 1


def _skip_gaps(lines, i, j):
    """跳过空白与换行，返回下一个非空白位置的 (行索引, 列索引)。"""
    while True:
        if i >= len(lines):
            return None
        line = lines[i]
        if j >= len(line):
            i, j = i + 1, 0
            continue
        if line[j] in _WS:
            j += 1
            continue
        return i, j


def _is_string_start(lines, i, j):
    line = lines[i]
    p = j
    while p < len(line) and line[p] in _PREFIX_CHARS:
        p += 1
    return p < len(line) and line[p] in _QUOTES


def scan_literal_span(lines, lineno, col_offset):
    """扫出完整字符串字面量（含隐式拼接的所有片段）的结束位置。

    返回 (end_lineno, end_col_offset)，1 基行号 / 0 基列号，与 ast 一致。
    """
    i, j = lineno - 1, col_offset
    while True:
        i, j = _scan_one(lines, i, j)
        nxt = _skip_gaps(lines, i, j)
        if nxt is None:
            break
        ni, nj = nxt
        if not _is_string_start(lines, ni, nj):
            break
        i, j = ni, nj      # 还有一段相邻字面量，继续拼
    return i + 1, j


def slice_source(source, rec):
    """按记录坐标从源码切出原文片段（含引号）。"""
    lines = source.split("\n")
    start, end = rec["lineno"] - 1, rec["end_lineno"] - 1
    end_col = rec["end_col_offset"]
    if start == end:
        return lines[start][rec["col_offset"]:end_col]
    parts = [lines[start][rec["col_offset"]:]]
    parts.extend(lines[start + 1:end])
    parts.append(lines[end][:end_col])
    return "\n".join(parts)


def verify_coordinates(records, repo_root):
    """自检：每条记录的坐标能否从源码切回原始字面量。

    这是阶段 3「脚本回填」的地基——坐标错一位，回填就是静默写坏代码，
    所以宁可现在多跑一遍。
    """
    cache = {}
    bad = []
    for rec in records:
        path = os.path.join(repo_root, rec["file"])
        if path not in cache:
            with open(path, "r", encoding="utf-8") as fh:
                cache[path] = fh.read()
        seg = slice_source(cache[path], rec)
        try:
            # 隐式拼接的切片带换行缩进，裸 literal_eval 会报 unexpected indent，
            # 套一层括号即为合法表达式。
            value = ast.literal_eval("(" + seg + ")")
        except Exception as exc:            # noqa: BLE001 - 仅用于报告
            bad.append((rec, "无法 eval: %s" % exc, seg[:50]))
            continue
        if value != rec["text"]:
            bad.append((rec, "内容不一致: %r" % (value[:50],), seg[:50]))
    return bad


def main(argv=None):
    parser = argparse.ArgumentParser(description="抽取中文字符串清单（只读）")
    parser.add_argument("--roots", nargs="*", default=["libjxl_gui"],
                        help="要扫描的文件或目录，默认 libjxl_gui")
    parser.add_argument("--out", default=None,
                        help="输出 JSON 路径，默认 tools/i18n_work/strings_raw.json")
    parser.add_argument("--stats", action="store_true",
                        help="只打印统计，不写文件")
    args = parser.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)

    targets = []
    for root in args.roots:
        abs_root = root if os.path.isabs(root) else os.path.join(repo_root, root)
        if os.path.isfile(abs_root):
            targets.append(abs_root)
        else:
            for dirpath, dirnames, filenames in os.walk(abs_root):
                dirnames[:] = [d for d in dirnames
                               if d not in ("__pycache__", ".workbuddy")]
                for name in sorted(filenames):
                    if name.endswith(".py"):
                        targets.append(os.path.join(dirpath, name))

    all_records = []
    all_fstrings = []
    all_span_errors = []
    for path in sorted(targets):
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        records, warns, span_errs = scan_file(path, rel)
        all_records.extend(records)
        all_fstrings.extend(warns)
        all_span_errors.extend(span_errs)

    # ---- 自检：坐标必须能原样切回字面量，否则阶段 3 回填不可信 ----
    bad = verify_coordinates(all_records, repo_root)
    multiline = sum(1 for r in all_records if r["multiline"])
    print("坐标自检              : %d/%d 通过%s"
          % (len(all_records) - len(bad), len(all_records),
             "" if not bad else "  <-- 有 %d 条异常！" % len(bad)))
    print("跨行隐式拼接          : %d 条（回填后换行排版会丢）" % multiline)
    if bad:
        for rec, why, seg in bad[:10]:
            print("  ! %s:%d %s | 切片=%r" % (rec["file"], rec["lineno"], why, seg))
    if all_span_errors:
        print("边界扫描异常          : %d 条" % len(all_span_errors))
        for f, ln, why in all_span_errors[:10]:
            print("  ! %s:%d %s" % (f, ln, why))

    out_path = args.out or os.path.join(here, "i18n_work", "strings_raw.json")
    if not args.stats:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({
                "records": all_records,
                "fstring_warnings": all_fstrings,
            }, fh, ensure_ascii=False, indent=2)

    # ---- 统计 ----
    total = len(all_records)
    unique = len({r["text"] for r in all_records})
    by_hint = {}
    for r in all_records:
        by_hint[r["hint"]] = by_hint.get(r["hint"], 0) + 1
    by_file = {}
    for r in all_records:
        by_file[r["file"]] = by_file.get(r["file"], 0) + 1

    print("扫描文件数           : %d" % len(targets))
    print("中文字符串出现次数   : %d" % total)
    print("去重后条数           : %d" % unique)
    print("提示需人工补录(f串)  : %d" % len(all_fstrings))
    print("--- 自动初判分级 ---")
    for key in ("ID_HINT", "UI_HINT", "UNKNOWN"):
        if key in by_hint:
            print("  %-9s %d" % (key, by_hint[key]))
    print("--- 分文件 ---")
    for name, count in sorted(by_file.items(), key=lambda kv: -kv[1]):
        print("  %-40s %d" % (name, count))
    if not args.stats:
        print("清单已写入: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
