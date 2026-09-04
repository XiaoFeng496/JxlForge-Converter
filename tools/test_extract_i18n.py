# -*- coding: utf-8 -*-
r"""回归测试：tools/extract_i18n.py（i18n 阶段 0 的抽取脚本）。

锁住三个踩过的坑，任何一个复发都会让阶段 3 的脚本回填静默写坏代码：

1. ast 的 ``col_offset`` 是 **UTF-8 字节偏移**，中文项目里必须换算成字符偏移
   —— 不换算则坐标整体错位（每个汉字偏 2）。
2. Python 3.12 的 ast 对「调用实参中的常量」会把 ``end_col_offset`` 算到整个
   Call 末尾（``foo("中文", 1)`` 会切出 ``, 1)``），故边界必须自己扫描。
3. f-string 内部的 Constant 是 JoinedStr 片段，不是独立字面量，必须排除。

不需要 Qt，不需要 QSettings 隔离（本脚本不读写任何配置）。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract_i18n as ex  # noqa: E402

REPO_ROOT = os.path.dirname(HERE)


# --------------------------------------------------------------------------
# 用例 1：全量坐标自检
# --------------------------------------------------------------------------
def test_all_coordinates_roundtrip():
    """清单里每条记录的坐标都必须能从源码原样切回字面量。

    这是阶段 3「按坐标整块替换成 t(...)」的地基：坐标错一位就是静默写坏代码。
    """
    targets = []
    pkg = os.path.join(REPO_ROOT, "libjxl_gui")
    for name in sorted(os.listdir(pkg)):
        if name.endswith(".py"):
            targets.append(os.path.join(pkg, name))

    records = []
    for path in targets:
        rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
        recs, _warns, span_errs = ex.scan_file(path, rel)
        assert not span_errs, "边界扫描异常：%r" % (span_errs[:3],)
        records.extend(recs)

    assert records, "没抽到任何中文字符串，抽取逻辑已失效"
    bad = ex.verify_coordinates(records, REPO_ROOT)
    assert not bad, "有 %d 条坐标切不回原文，首条=%r" % (len(bad), bad[0])
    print("PASS all_coordinates_roundtrip (%d 条)" % len(records))


# --------------------------------------------------------------------------
# 用例 2：字节偏移 → 字符偏移的换算（中文项目头号陷阱）
# --------------------------------------------------------------------------
_SAMPLE = (
    "# -*- coding: utf-8 -*-\n"
    "def f():\n"
    "    log(\"转换完成：%d 个文件\" % n)\n"
    "    VIEW = [\"小缩略图\", \"缩略图\", \"列表\"]\n"
)


def _sample_records(sample=None):
    """在临时文件上跑一次抽取，返回 (源码, 记录, warnings, errors, 临时目录)。"""
    import tempfile
    text = sample if sample is not None else _SAMPLE
    tmpdir = tempfile.mkdtemp(prefix="i18n_extract_")
    path = os.path.join(tmpdir, "sample.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    recs, warns, errs = ex.scan_file(path, "sample.py")
    return text, recs, warns, errs, tmpdir


def test_byte_offset_converted_to_char_offset():
    """同一行里出现中文后，后续字面量的列号必须仍是字符偏移。

    构造 ``["小缩略图", "缩略图", "列表"]``：三个字面量的字节列号会因前面的
    汉字而偏大，换算后必须落回字符列号，且能切出正确文本。
    """
    text, recs, _warns, _errs, _tmp = _sample_records()
    got = [r["text"] for r in recs]
    assert "转换完成：%d 个文件" in got, "漏抽格式化日志文本：%r" % (got,)
    for want in ("小缩略图", "缩略图", "列表"):
        assert want in got, "漏抽 %r：%r" % (want, got)

    lines = text.split("\n")
    for rec in recs:
        seg = ex.slice_source(text, rec)
        assert ast.literal_eval("(" + seg + ")") == rec["text"], \
            "第 %d 行切片错位：%r != %r" % (rec["lineno"], seg, rec["text"])
        # 额外确认坐标落在真实字符边界上（不是半个汉字）
        assert rec["col_offset"] <= len(lines[rec["lineno"] - 1]), \
            "列号越界：%r" % (rec,)
    print("PASS byte_offset_converted_to_char_offset")


def test_call_argument_end_offset_not_call_end():
    """调用实参里的字符串，结束坐标不能延伸到整个 Call 末尾。

    Python 3.12 的 ast 会返回 Call 的 end_col_offset（实测会切出 ``, 1)``），
    本脚本必须自己扫描边界。
    """
    sample = 'foo("中文", 1)\n'
    text, recs, _warns, _errs, _tmp = _sample_records(sample)
    assert len(recs) == 1, "应只抽到 1 条，实际 %d 条：%r" % (
        len(recs), [r["text"] for r in recs])
    rec = recs[0]
    seg = ex.slice_source(text, rec)
    assert seg == '"中文"', '切片必须正好是 "中文"，实际=%r' % (seg,)
    print("PASS call_argument_end_offset_not_call_end")


# --------------------------------------------------------------------------
# 用例 3：f-string / docstring 必须排除
# --------------------------------------------------------------------------
def test_fstring_parts_excluded_but_warned():
    """f-string 内部的中文进 warnings，不进 records（否则坐标无从扫起）。"""
    sample = (
        "# -*- coding: utf-8 -*-\n"
        "def f():\n"
        "    print(f\"[bench] 模式={mode} | 耗时={sec}s\")\n"
    )
    _text, recs, warns, errs, _tmp = _sample_records(sample)
    assert not errs, "f-string 不应触发边界扫描异常：%r" % (errs,)
    assert not recs, "f-string 片段不该进 records：%r" % (
        [r["text"] for r in recs],)
    assert any("模式=" in w["text"] for w in warns), \
        "f-string 中文必须进 warnings 供人工补录：%r" % (warns,)
    print("PASS fstring_parts_excluded_but_warned")


def test_docstring_excluded():
    """模块 / 类 / 函数的 docstring 不参与翻译。"""
    sample = (
        "# -*- coding: utf-8 -*-\n"
        '"""模块说明：这是中文 docstring。"""\n'
        "def f():\n"
        '    """函数说明：也是中文。"""\n'
        '    return "真正的界面文本"\n'
    )
    _text, recs, _warns, _errs, _tmp = _sample_records(sample)
    got = [r["text"] for r in recs]
    assert got == ["真正的界面文本"], "docstring 必须被排除，实际=%r" % (got,)
    print("PASS docstring_excluded")


# --------------------------------------------------------------------------
# 用例 4：自检本身非空转
# --------------------------------------------------------------------------
def test_verify_detects_bad_offset():
    """把某条记录的列号挪一位，自检必须报错——证明它不是摆设。"""
    _text, recs, _warns, _errs, _tmp = _sample_records()
    assert not ex.verify_coordinates(recs, _tmp), "原始坐标应当全部通过自检"

    recs[0]["col_offset"] += 1          # 故意错位一位
    bad = ex.verify_coordinates(recs, _tmp)
    assert bad, "列号错位后自检仍判定通过，说明自检无效"
    print("PASS verify_detects_bad_offset")


def main():
    tests = [
        test_all_coordinates_roundtrip,
        test_byte_offset_converted_to_char_offset,
        test_call_argument_end_offset_not_call_end,
        test_fstring_parts_excluded_but_warned,
        test_docstring_excluded,
        test_verify_detects_bad_offset,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        except Exception as exc:          # noqa: BLE001
            failed += 1
            print("ERROR %s: %r" % (fn.__name__, exc))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
