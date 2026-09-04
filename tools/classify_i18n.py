# -*- coding: utf-8 -*-
"""i18n 阶段 1：对阶段 0 抽出的中文字符串做自动分级。

阶段 0 的 ``extract_i18n.py`` 只做粗判（UI_HINT / UNKNOWN / ID_HINT），
本项目有 205 条落进 UNKNOWN。本脚本用更精确的结构特征把它们归类，
目标是**把需要人工逐条判断的数量压到最小**。

三档结论（用中文标签，便于非程序员复核）：

``翻``   界面文本 / 日志 / 提示语，直接翻译，无副作用
``不翻`` 内部标识：存进 QSettings 的 key、程序内部用的 ID。
         翻译会导致老用户配置文件失配，属于会损坏用户数据的改动
``待定`` 规则判不出来，需要人工拍板

判断依据不是语法，而是**数据流向**：这个字符串最终是「显示给人看」
还是「被程序拿来当标识符用」。比如 ``("LANCZOS", "LANCZOS (高质量, 默认)")``
这种 (英文ID, 中文显示名) 配对，程序内部只用前面的英文 ID，
后面的中文纯显示 → 翻译它完全安全。

用法::

    python tools/classify_i18n.py               # 生成分级结果 + 复核页
    python tools/classify_i18n.py --stats       # 只看统计
"""
from __future__ import annotations

import argparse
import ast
import collections
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import extract_i18n as ex  # noqa: E402

RAW_JSON = os.path.join(HERE, "i18n_work", "strings_raw.json")
OUT_JSON = os.path.join(HERE, "i18n_work", "strings_classified.json")
OUT_HTML = os.path.join(HERE, "i18n_work", "review.html")

TRANSLATE = "翻"
SKIP = "不翻"
MANUAL = "待定"

# ---------------------------------------------------------------- 规则表

# (1) 已知「ID 兼显示名」的常量表：成员一律不翻。
#     这些中文同时是 QSettings 的持久化值，翻译 = 老配置失配。
ID_CONSTANT_MEMBERS = {
    "processor.py": {"ACTION_TYPES", "WATERMARK_POSITIONS"},
    "main_window.py": {"VIEW_MODES"},
}

# (2) 已知「中文当 dict key」的字典：key 一律不翻。
ID_DICT_KEYS = {
    "GRID_SIZES": "main_window.py",
}

# (2b) 纯运行时的局部布局表：可翻。
#
# 判据不是「中文当没当 key」，而是 **key 和显示值是否同源、是否持久化**。
# ``sub_order`` / ``sub_pos`` 里的字符串同时承担两个角色：
#     sub_pos[sg]  →  查 2×2 布局坐标的 key
#     QGroupBox(sg) →  分组框标题（用户看到的文字）
# 但两者用的是**同一个变量**，翻译成中文改英文时它们会一起变，查表不会断；
# 而且这是函数内的局部变量，不写进 QSettings、不跨会话。
# 对比 ACTION_TYPES：那个会把中文存进配置文件，才是真不能翻的。
RUNTIME_LAYOUT_TABLES = {"sub_order", "sub_pos"}

# 自定义命令模板：整条字符串必须原样保留。
# ``<输入>``/``<输出>`` 是**硬编码字面量**——校验用 ``if "<输入>" not in raw``、
# 替换用 ``.replace("<输入>", src)``，都不走 ``t()``。
# 因此示例文本翻成英文后，用户照抄示例反而过不了校验。
# 与占位符本身（已在 SKIP 名单）同理，整条命令模板一并排除。
COMMAND_TEMPLATES = {
    "cjxl <输入> <输出> -e 7 ...",
    "djxl <输入> <输出>",
}

# (3) 日志类 context：用户已确认状态页日志纳入翻译范围。
LOG_CONTEXTS = {
    "log", "log_cb", "progress_cb", "emit", "appendPlainText",
    "appendHtml", "insertPlainText",
}

# (4) 明显的界面调用。
UI_CONTEXTS = {
    "_add_param", "addRow", "_label_row", "_section", "addButton",
    "_show_warning_centered", "setValue", "setRange", "setText",
    "setToolTip", "setPlaceholderText", "addItems", "addItem",
    "setWindowTitle", "information", "warning", "critical", "question",
}

# (5c) i18n 基础设施模块本身不产生界面文本。
#
# 它里面的中文是语言自称名（「简体中文」）和说明文档。语言名用**自称名**
# 是有意为之——英文界面上仍应显示「简体中文」，译成 "Simplified Chinese"
# 反而让中文用户认不出来。不排除的话它会永远躺在「未翻译」里，
# 把覆盖率永久卡在 99.x%。
I18N_INFRA_FILES = {"libjxl_gui/i18n.py"}

# (5) 异常类名：raise 出来的消息是给开发者看的，不进 UI。
EXCEPTION_CONTEXTS = {"ValueError", "RuntimeError", "TypeError", "KeyError",
                      "IOError", "OSError", "Exception"}

# (5b) 阶段 6 已做「ID 与显示名分离」的标识。
#
# 这些中文在源码里**不翻**（它们要存进 QSettings、还要参与几十处字面量
# 比较），但界面上显示的是 ``t(该中文)``，所以**字典里必须有它们的译文**。
#
# ⚠️ 这个区分很重要：如果阶段 2 看到「不翻」就跳过，英文界面上动作类型、
# 水印位置、查看模式、冲突策略会全部退回中文，阶段 6 等于白做。
# 这些条目在输出里会带 ``stage6 = True``，阶段 2 据此照样要翻。
#
# 名单刻意写死而不是 AST 扫描：这几张表是设计决策的产物，不是代码里
# 偶然出现的同名变量。``tools/test_i18n_id_separation.py`` 会拿它和
# processor / main_window 里的真实常量对账，改漏了会红。
STAGE6_DISPLAY_IDS = {
    # processor.ACTION_TYPES —— 动作类型（apply_actions 里 9 处字面量比较）
    "调整大小", "旋转", "水印", "亮度/对比度", "锐化", "裁剪",
    "规格化", "曝光", "阴影/高光",
    # processor.WATERMARK_POSITIONS —— 水印九宫格位置（_watermark_offset 查表）
    "左上", "中上", "右上", "左中", "居中", "右中", "左下", "中下", "右下",
    # main_window.VIEW_MODES —— 输入标签页查看模式（GRID_SIZES / THUMB_SIZES 查表）
    "小缩略图", "缩略图", "大缩略图", "列表", "详细信息",
    # 输出文件已存在时的冲突策略（_resolve_existing_outputs 分支判断）
    "替换", "询问", "跳过", "重命名",
}

# (6) 文件对话框：标题是给用户看的。
DIALOG_CONTEXTS = {
    "getExistingDirectory", "getSaveFileName", "getOpenFileNames",
    "getOpenFileName",
}

# (7) 状态栏 / 标签页 / 菜单项。
EXTRA_UI_CONTEXTS = {
    "setStatusTip", "setWhatsThis", "setTabText", "setItemText",
    "setTitle", "setHeaderData", "setHorizontalHeaderLabels",
}

# (8) ``str.join`` 的分隔符：拼接出来是给人看的一句话。
JOIN_CONTEXTS = {"join"}

# (9) 命名约定：``_XXX_LABELS = {英文ID: 中文显示名}``。
#     持久化存的是英文 ID，中文只是显示名，翻译安全。
#     已核实：_THEME_LABELS / _COLOR_SCHEME_LABELS / _LANGUAGE_LABELS 均属此类。
LABEL_DICT_RE = r"^[A-Za-z_]*LABELS?$"


# ---------------------------------------------------------------- 结构分析

class StructureAnalyzer:
    """扫描源码，找出「配对结构」与「ID 常量成员」的精确位置。

    阶段 0 的记录里只有 context（调用名），看不出一个字符串是不是
    ``(英文ID, 中文显示名)`` 里的显示名。这里补上这一层。
    """

    def __init__(self, rel_path):
        self.rel = rel_path
        abs_path = os.path.join(REPO_ROOT, rel_path)
        with open(abs_path, encoding="utf-8") as fh:
            self.source = fh.read()
        self.tree = ast.parse(self.source)
        self.parents = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self.lines = self.source.split("\n")

        # (lineno, col_offset) -> 角色标签
        self.roles = {}
        self._scan_id_constants()
        self._scan_pairs()
        self._scan_dict_keys()
        self._scan_runtime_layout()
        self._scan_label_dicts()
        self._scan_schema_fields()
        self._scan_bench()
        self._scan_exceptions()

    def _key(self, node):
        """ast 的 col_offset 是 UTF-8 字节偏移，必须换算成字符偏移才能和
        阶段 0 记录里的坐标对上（同一个坑，见 extract_i18n.byte_col_to_char_col）。"""
        line = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
        return (node.lineno, ex.byte_col_to_char_col(line, node.col_offset))

    def _scan_id_constants(self):
        """标记 ID 常量表的成员，如 ACTION_TYPES = ["调整大小", ...]。"""
        names = ID_CONSTANT_MEMBERS.get(self.rel)
        if not names:
            return
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not targets or node.value is None:
                continue
            tgt = targets[0]
            if not isinstance(tgt, ast.Name) or tgt.id not in names:
                continue
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    self.roles[self._key(sub)] = "ID_MEMBER"

    def _scan_pairs(self):
        """标记 (英文ID, 中文显示名) 里的显示名。

        命中形如 ``("LANCZOS", "LANCZOS (高质量, 默认)")`` 的二元组：
        第 0 项是纯 ASCII（ID），第 1 项含中文（显示名）。
        程序内部按 ID 取值并持久化，显示名可安全翻译。
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.Tuple, ast.List)):
                continue
            elts = node.elts
            if len(elts) < 2:
                continue
            head, second = elts[0], elts[1]
            if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
                continue
            if not (isinstance(second, ast.Constant) and isinstance(second.value, str)):
                continue
            if not ex.has_cjk(head.value) and ex.has_cjk(second.value):
                # 二元组：显示名在第 1 位
                self.roles[self._key(second)] = "PAIR_LABEL"
            elif (ex.has_cjk(head.value) and isinstance(second.value, str)
                    and second.value and not ex.has_cjk(second.value)):
                # 反向配对：(中文显示名, 英文key)，如 ("左", "left")。
                # 前半是界面标签，后半是内部参数名 → 前半可翻。
                # （曾漏掉这个方向，把「宽」「高」误判成不翻。）
                self.roles[self._key(head)] = "PAIR_LABEL"
            elif len(elts) >= 2 and ex.has_cjk(head.value):
                # 三元组以上且首项含中文（如 ("name","文件名",True,200,...)）
                # 首项是内部 ID，第二项才是显示名
                if len(elts) > 2:
                    self.roles[self._key(head)] = "ID_MEMBER"

    def _scan_dict_keys(self):
        """标记中文当 dict key 的位置，如 GRID_SIZES = {"缩略图": QSize(...)}。"""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Dict):
                continue
            owner = ex.enclosing_constant_name(node, self.parents)
            if owner not in ID_DICT_KEYS:
                continue
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    self.roles[self._key(k)] = "ID_MEMBER"

    def _scan_label_dicts(self):
        """标记 ``_XXX_LABELS = {"英文ID": "中文显示名"}`` 里的显示名（dict 的 value）。

        和 ``_scan_pairs`` 的差别：配对结构是二元组，这里是字典。
        两者持久化都只存英文 ID，中文改名不影响老配置文件。
        """
        import re
        rx = re.compile(LABEL_DICT_RE)
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            if node.value is None or not isinstance(node.value, ast.Dict):
                continue
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            if not targets or not isinstance(targets[0], ast.Name):
                continue
            if not rx.match(targets[0].id):
                continue
            for k, v in zip(node.value.keys, node.value.values):
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    continue
                if not ex.has_cjk(k.value):
                    continue  # key 是英文 ID → value 是显示名
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    self.roles[self._key(v)] = "PAIR_LABEL"

    def _scan_bench(self):
        """标记 ``[bench]`` 诊断输出用到的文本。

        这是启动耗时测量的调试 print，写到 stdout 而非界面，
        用户看不到，翻了只会白白增加维护负担。

        定位方式：找到含 "[bench]" 的字面量，向上追溯到它所在的语句块。
        注意要取**复合语句**（if/for/try…）而不是最内层的那一条语句——
        本例里 ``mode`` 的赋值在 print 之外、但在同一个 if 块之内，
        只取 print 那条语句会漏掉它。
        """
        compound_types = (ast.If, ast.For, ast.While, ast.Try, ast.With,
                          ast.AsyncFor)
        spans = []
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str) and "[bench]" in node.value):
                continue
            chain = []
            cur = self.parents.get(node)
            while cur is not None:
                if isinstance(cur, ast.stmt):
                    chain.append(cur)
                cur = self.parents.get(cur)
            if not chain:
                continue
            block = next((s for s in chain if isinstance(s, compound_types)),
                         chain[0])
            end = getattr(block, "end_lineno", None) or block.lineno
            spans.append((block.lineno, end))
        if not spans:
            return
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if not ex.has_cjk(node.value):
                continue
            if any(lo <= node.lineno <= hi for lo, hi in spans):
                self.roles[self._key(node)] = "INTERNAL"

    def _scan_exceptions(self):
        """标记 raise / 异常构造里的消息。

        ⚠️ 这些**要翻**。一开始误当成「内部文本」判了不翻，是错的：
        本项目的调用方普遍用 ``except Exception as exc`` 捕获后把
        ``str(exc)`` 拼进日志或标签，例如

        - ``log("删除原文件失败（已保留）：%s —— %s" % (src, exc))``  → 状态页
        - ``QLabel("无法解析 EXR 头部：%s" % exc)``                   → 预览区
        - ``"处理出错：%s" % exc``                                    → 状态页

        而且这些消息本身就是写给用户看的（带「请先安装：pip install xxx」指引）。
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Raise):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if ex.has_cjk(sub.value):
                        self.roles[self._key(sub)] = "ERROR_SHOWN"

    def _scan_runtime_layout(self):
        """标记纯运行时布局表里的字符串（key 与显示同源，可翻）。"""
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            if node.value is None:
                continue
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            if not targets or not isinstance(targets[0], ast.Name):
                continue
            if targets[0].id not in RUNTIME_LAYOUT_TABLES:
                continue
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and ex.has_cjk(sub.value):
                    self.roles[self._key(sub)] = "PAIR_LABEL"

    def _scan_schema_fields(self):
        """标记数据驱动模式里的显示字段。

        形如 ``{"key": "distance", "label": "…", "group": "质量精细"}``：
        ``key`` 是内部 ID，``label`` / ``group`` / ``tip`` 是给用户看的。
        靠「短且无标点」猜 ID 会把「质量精细」「容器输出」这类分组标题误伤，
        所以改成直接看键名。
        """
        field_names = {"label", "group", "tip", "title", "text",
                       "placeholder", "desc", "description"}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values):
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    continue
                if k.value not in field_names:
                    continue
                if isinstance(v, ast.Constant) and isinstance(v.value, str) \
                        and ex.has_cjk(v.value):
                    self.roles[self._key(v)] = "PAIR_LABEL"

    def role_of(self, lineno, col_offset):
        return self.roles.get((lineno, col_offset))


# ---------------------------------------------------------------- 分级

def classify_record(rec, analyzer):
    """返回 (结论, 理由, 置信度)。

    置信度（写给用户看的）：
      高 = 结构或调用名能确定，直接采信
      中 = 按字面量 / 上下文推断，建议扫一眼
    复核时只需看「中」那一档。
    """
    text = rec["text"]
    ctx = rec["context"]
    role = analyzer.role_of(rec["lineno"], rec["col_offset"]) if analyzer else None

    # --- 纯标点 / 格式化字符：不是文案 ---
    # ex.has_cjk 把中文标点（、。，）也算「中文」，会被抽进来。
    # 但它们只是拼接列表用的分隔符，翻成 ", " 反而要改调用点，不值当。
    if not _has_han(text):
        return SKIP, "纯标点或格式化字符，不是文案", "高"

    # --- 自定义命令模板：整条原样保留，否则用户照抄示例会过不了校验 ---
    if text in COMMAND_TEMPLATES:
        return SKIP, "自定义命令模板；<输入>/<输出> 是硬编码字面量，翻译会让示例失效", "高"

    # --- 硬规则：结构角色优先于 context ---
    if role == "ID_MEMBER":
        return SKIP, "这是程序内部的标识（会存进配置文件），翻译会让老配置失效", "高"
    if role == "INTERNAL":
        return SKIP, "程序内部 / 调试输出，不会显示在界面上", "高"
    if role == "PAIR_LABEL":
        return TRANSLATE, "显示名；程序内部用的是配对的英文标识，翻译安全", "高"
    if role == "ERROR_SHOWN":
        return TRANSLATE, ("报错文案：会被界面捕获后用 str(exc) 拼进状态页日志或"
                           "提示框，用户看得到"), "高"

    # --- ID_HINT 直接继承阶段 0 的判断 ---
    if rec["hint"] == "ID_HINT":
        return SKIP, "阶段 0 判定为内部标识", "高"

    # --- 常量表：整表成员是 ID，但表里的 label/group 字段是显示名 ---
    if ctx in ("_ADVANCED_SCHEMA", "TABLE_COLUMNS", "DEFAULT_PARAMS",
               "RESIZE_ALGORITHMS"):
        if _looks_like_id(text):
            return SKIP, "疑似内部标识，暂不翻（阶段 1 复核）", "中"
        return TRANSLATE, "界面上显示的分组名 / 列名 / 参数名", "高"

    # --- 异常 ---
    if ctx in EXCEPTION_CONTEXTS:
        return SKIP, "程序内部异常消息，不显示在界面上", "高"

    # --- 日志（用户已确认纳入） ---
    if ctx in LOG_CONTEXTS:
        return TRANSLATE, "状态页日志（已确认纳入翻译范围）", "高"

    # --- 文件对话框标题 ---
    if ctx in DIALOG_CONTEXTS:
        return TRANSLATE, "文件选择对话框的标题", "高"

    # --- 界面调用 ---
    if ctx in UI_CONTEXTS or ctx in EXTRA_UI_CONTEXTS \
            or ctx.split(".")[-1] in UI_CONTEXTS:
        return TRANSLATE, "界面文本", "高"

    # --- join 的分隔符 ---
    if ctx in JOIN_CONTEXTS:
        return TRANSLATE, "拼接成句的分隔符（如顿号、“与”）", "中"

    # --- 变量名带界面语义 ---
    if rec["hint"] == "UI_HINT":
        return TRANSLATE, "阶段 0 按变量名判定为界面文本", "中"

    # --- 兜底：字面量（日志模板 / 提示语 / 计算结果的显示部分）---
    if ctx == "<literal>":
        return TRANSLATE, "字面量文本：日志模板、提示语或结果显示", "中"

    return TRANSLATE, "调用名未识别，按显示文本处理", "低"


_IDISH_MAXLEN = 6


def _has_han(text):
    """是否含汉字（U+4E00–U+9FFF）。

    注意与 ``extract_i18n.has_cjk`` 区分：那个把中文标点也算进去，
    所以「、」会被当成中文抽进来。这里只认真正的汉字。
    """
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _looks_like_id(text):
    """短且无标点、无空格的中文字串更像内部 ID（“右下”“缩略图”）。"""
    if len(text) > _IDISH_MAXLEN:
        return False
    if any(ch in text for ch in " ，。、；：（）()%/：:"):
        return False
    return not any(ord(ch) > 0x20000 for ch in text)


# ---------------------------------------------------------------- 输出

def build(source=RAW_JSON):
    with open(source, encoding="utf-8") as fh:
        data = json.load(fh)
    records = data["records"]

    # 排除 i18n 基础设施自身的文本（如语言自称名「简体中文」）。
    # 它们不是待翻内容，留着会永久占据「未翻译」，把覆盖率卡死在 99.x%。
    records = [r for r in records if r["file"] not in I18N_INFRA_FILES]

    analyzers = {}
    by_text = collections.OrderedDict()
    for rec in records:
        rel = rec["file"]
        if rel not in analyzers:
            try:
                analyzers[rel] = StructureAnalyzer(rel)
            except SyntaxError:
                analyzers[rel] = None
        verdict, reason, conf = classify_record(rec, analyzers[rel])
        rec = dict(rec)
        rec["verdict"] = verdict
        rec["reason"] = reason
        rec["confidence"] = conf
        by_text.setdefault(rec["text"], []).append(rec)

    # 同一文本多条出现：只要有任意一处「不翻」，整条就不翻（保守优先）。
    # 置信度取同档里最低的一档，避免某一处的高置信度掩盖另一处的存疑。
    _CONF_RANK = {"低": 0, "中": 1, "高": 2}
    entries = []
    for text, group in by_text.items():
        verdicts = {g["verdict"] for g in group}
        if SKIP in verdicts:
            final = SKIP
        elif MANUAL in verdicts:
            final = MANUAL
        else:
            final = TRANSLATE
        pool = [g for g in group if g["verdict"] == final]
        why = pool[0]["reason"]
        conf = min((g.get("confidence", "中") for g in pool),
                   key=lambda c: _CONF_RANK.get(c, 1))
        # 阶段 6 已把显示名与 ID 分离：源码不动它，但界面上显示 t(它)，
        # 所以字典里照样要有译文。标出来，避免阶段 2 看到「不翻」就跳过。
        separated = text in STAGE6_DISPLAY_IDS
        if separated:
            why = ("源码不改（要存配置、参与判断），但界面显示已走 t() 分离 "
                   "—— 字典里仍需译文")
        entries.append({
            "text": text,
            "verdict": final,
            "reason": why,
            "confidence": conf,
            "stage6": separated,
            "count": len(group),
            "occurrences": group,
        })
    entries.sort(key=lambda e: (e["verdict"] != MANUAL,
                                e["verdict"] != SKIP,
                                _CONF_RANK.get(e["confidence"], 1),
                                -e["count"]))
    return entries


def render_html(entries, path=OUT_HTML):
    counts = collections.Counter(e["verdict"] for e in entries)
    rows = []
    for i, e in enumerate(entries, 1):
        locs = "；".join(
            "%s:%d" % (o["file"].split("/")[-1], o["lineno"])
            for o in e["occurrences"][:4]
        )
        if e["count"] > 4:
            locs += " …共 %d 处" % e["count"]
        conf = e.get("confidence", "中")
        rows.append(
            '<tr class="v-%s cf-%s" data-v="%s" data-cf="%s" data-s6="%s">'
            '<td class="idx">%d</td>'
            '<td class="txt">%s%s</td>'
            '<td class="verdict">%s</td>'
            '<td class="conf">%s</td>'
            '<td class="why">%s</td>'
            '<td class="loc">%s</td>'
            '<td class="pick"><button onclick="setV(%d,\'%s\')">翻</button>'
            '<button onclick="setV(%d,\'%s\')">不翻</button></td>'
            "</tr>" % (
                {"翻": "t", "不翻": "s", "待定": "m"}[e["verdict"]],
                {"高": "h", "中": "m", "低": "l"}.get(conf, "m"),
                e["verdict"], conf,
                "1" if e.get("stage6") else "0", i, html.escape(e["text"]),
                ' <span class="s6" title="阶段 6 已做显示名分离：源码不改，'
                '但字典里需要译文">分</span>' if e.get("stage6") else "",
                e["verdict"], conf,
                html.escape(e["reason"]),
                html.escape(locs),
                i, TRANSLATE, i, SKIP,
            )
        )
    css = """
    body{font-family:'Microsoft YaHei',system-ui,sans-serif;background:#1b1d21;
         color:#e3e5e8;margin:0;padding:24px}
    h1{font-size:19px;margin:0 0 6px}
    .sub{color:#9aa0a6;font-size:13px;margin-bottom:16px;line-height:1.7}
    .bar{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
    .chip{background:#282c33;border:1px solid #3a4048;border-radius:14px;
          padding:5px 14px;font-size:13px;cursor:pointer}
    .chip.on{background:#2d6cdf;border-color:#2d6cdf;color:#fff}
    table{border-collapse:collapse;width:100%;font-size:13px}
    th{background:#23272e;text-align:left;padding:8px 10px;position:sticky;top:0;
       border-bottom:1px solid #3a4048;font-weight:600}
    td{padding:7px 10px;border-bottom:1px solid #2a2e35;vertical-align:top}
    tr.v-t .verdict{color:#7ee08a} tr.v-s .verdict{color:#ff8f6b}
    .s6{display:inline-block;padding:0 4px;margin-left:4px;border-radius:3px;
        background:#3a5a8a;color:#cfe4ff;font-size:11px;cursor:help;
        vertical-align:1px}
    tr.v-m .verdict{color:#ffd166}
    .conf{width:44px;font-size:12px}
    .cf-h .conf{color:#6f7681} .cf-m .conf{color:#c9b478} .cf-l .conf{color:#ffd166}
    .txt{max-width:320px;word-break:break-all}
    .why{color:#9aa0a6;font-size:12px;max-width:260px}
    .loc{color:#6f7681;font-size:11px;font-family:Consolas,monospace;max-width:200px}
    .idx{color:#6f7681;width:38px}
    .pick button{background:#333944;border:1px solid #464d58;color:#ddd;
                 border-radius:4px;padding:3px 9px;cursor:pointer;font-size:12px;
                 margin-right:4px}
    .pick button:hover{background:#3f4753}
    tr.hide{display:none}
    .n{color:#e3e5e8}
    """
    js = """
    var overridden = {};
    function setV(i, v){
      overridden[i] = v;
      var tr = document.querySelectorAll('tbody tr')[i-1];
      tr.className = 'v-' + (v==='翻'?'t':'s');
      tr.dataset.v = v;
      tr.querySelector('.verdict').textContent = v;
      var n = document.getElementById('ovr');
      n.textContent = Object.keys(overridden).length;
    }
    function filter(v){
      document.querySelectorAll('.chip').forEach(function(c){
        c.classList.toggle('on', c.dataset.f === v);
      });
      document.querySelectorAll('tbody tr').forEach(function(tr){
        var show;
        if(v === 'all'){ show = true; }
        else if(v === 'low'){ show = (tr.dataset.cf === '低'); }
        else if(v === 's6'){ show = (tr.dataset.s6 === '1'); }
        else { show = (tr.dataset.v === v); }
        tr.classList.toggle('hide', !show);
      });
    }
    """
    conf_counts = collections.Counter(e.get("confidence", "中") for e in entries)
    stats = "　".join(
        '<span class="n"><b>%d</b> %s</span>' % (counts[k], k)
        for k in (TRANSLATE, SKIP, MANUAL) if counts[k]
    )
    cstats = "　".join(
        '<span class="n"><b>%d</b> %s置信</span>' % (conf_counts[k], k)
        for k in ("高", "中", "低") if conf_counts[k]
    )
    doc = (
        "<!doctype html><meta charset='utf-8'><title>i18n 分级复核</title>"
        "<style>%s</style>"
        "<h1>i18n 阶段 1 · 分级复核表</h1>"
        "<div class='sub'>共 <b>%d</b> 条去重字符串。%s　｜　%s<br>"
        "「翻」= 界面/日志文本，直接翻译；「不翻」= 内部标识，翻译会让老配置失效。"
        "右侧按钮可改判，已改判 <b id='ovr'>0</b> 条。<br>"
        "原文后带 <span class='s6'>分</span> 的 %d 条是<b>阶段 6 已做显示名分离</b>的"
        "标识：源码里不动它，但界面显示的是它的译文，"
        "<b>所以字典里仍然需要译文</b>（阶段 2 别跳过）。<br>"
        "<b>你只需复核「低置信」那一档</b>（点下方标签筛选）——"
        "高/中置信由代码结构判定，已排除持久化标识。</div>"
        "<div class='bar'>"
        "<span class='chip on' data-f='all' onclick=\"filter('all')\">全部</span>"
        "<span class='chip' data-f='%s' onclick=\"filter('%s')\">翻</span>"
        "<span class='chip' data-f='%s' onclick=\"filter('%s')\">不翻</span>"
        "<span class='chip' data-f='%s' onclick=\"filter('%s')\">待定</span>"
        "<span class='chip' data-f='low' onclick=\"filter('low')\">仅看低置信</span>"
        "<span class='chip' data-f='s6' onclick=\"filter('s6')\">仅看显示名分离</span>"
        "</div>"
        "<table><thead><tr><th>#</th><th>中文原文</th><th>结论</th>"
        "<th>把握</th><th>理由</th><th>代码位置</th><th>改判</th></tr></thead>"
        "<tbody>%s</tbody></table><script>%s</script>"
    ) % (css, len(entries), stats, cstats,
         sum(1 for e in entries if e.get("stage6")),
         TRANSLATE, TRANSLATE, SKIP, SKIP, MANUAL, MANUAL,
         "\n".join(rows), js)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="i18n 阶段 1：字符串分级")
    ap.add_argument("--stats", action="store_true", help="只打印统计")
    ap.add_argument("--out-json", default=OUT_JSON)
    args = ap.parse_args(argv)

    entries = build()
    counts = collections.Counter(e["verdict"] for e in entries)

    print("去重后总条数：%d" % len(entries))
    for k in (TRANSLATE, SKIP, MANUAL):
        n = counts[k]
        print("  %-3s %3d  (%.0f%%)" % (k, n, 100.0 * n / max(1, len(entries))))

    conf = collections.Counter(e.get("confidence", "中") for e in entries)
    print("  把握：" + "  ".join("%s %d" % (k, conf[k]) for k in ("高", "中", "低")
                                if conf[k]))
    low = [e for e in entries if e.get("confidence") == "低"]
    if low:
        print("\n=== 需你复核（低置信 %d 条）===" % len(low))
        for e in low[:40]:
            locs = ",".join("%s:%d" % (o["file"].split("/")[-1], o["lineno"])
                            for o in e["occurrences"][:2])
            print("  %-34s %s" % (e["text"][:34], locs))

    if not args.stats:
        os.makedirs(os.path.dirname(args.out_json), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
        print("\n分级结果 -> %s" % args.out_json)
        print("复核页   -> %s" % render_html(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
