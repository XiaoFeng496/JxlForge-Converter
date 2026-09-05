# -*- coding: utf-8 -*-
"""极简 i18n：自建 JSON 字典 + ``t()`` 查询。

为什么不用 Qt 的 ``tr()``：本项目没有 .ui 文件（界面全部用代码搭），
``retranslateUi`` 那套机制用不上，pylupdate 也扫不出东西。

设计取舍
--------
- **切语言重启生效**：省掉「实时刷新所有控件」和「已输出日志重渲染」两套机制，
  这两套都要给每个控件留句柄、还要缓存原始中文，复杂度和出错面都大得多。
- **中文不建字典**：源码里写的就是中文，查不到时 ``t()`` 直接返回原文，
  所以 zh_CN 不需要维护一份「自己翻自己」的字典，也就不会和源码漂移。
- **内部 ID 也能翻**：``ACTION_TYPES`` 这类中文既是显示名又是内部标识，
  显示在界面上时走 ``t()`` 取译文，存进配置 / 参与判断时仍用原始中文。
  ID 与显示名由此分离，两边互不干扰（详见下方 ``t()`` 的用法说明）。

用法::

    from libjxl_gui.i18n import t

    QLabel(t("设置"))
    combo.addItem(t(action_id), action_id)   # 显示译文，userData 存原 ID
"""
from __future__ import annotations

import json
import os

# 默认语言与回退语言。加新语言**不需要改这里**——
# 往 i18n/ 丢一个 <code>.json 就会被 language_order() 自动发现。
DEFAULT_LANGUAGE = "zh_CN"
FALLBACK_LANGUAGE = "zh_CN"
# 默认语言没有 json（源码即译文），它的自称名只能写在这里。
_DEFAULT_LANGUAGE_NAME = "简体中文"

_DIR = os.path.dirname(os.path.abspath(__file__))
_I18N_DIR = os.path.join(_DIR, "i18n")

_current = DEFAULT_LANGUAGE
_dict = {}
_loaded_for = None


def available_languages():
    """返回已有的语言代码列表（按扫描 i18n/ 目录得到）。"""
    codes = []
    if os.path.isdir(_I18N_DIR):
        for name in sorted(os.listdir(_I18N_DIR)):
            if name.endswith(".json"):
                codes.append(name[:-5])
    return codes


def language_order():
    """下拉里语言的显示顺序：默认语言排第一，其余按代码排序。

    顺序从目录推导，加语言时**不必再改代码**——丢一个 json 进 i18n/ 即可。
    """
    rest = [c for c in available_languages() if c != DEFAULT_LANGUAGE]
    return [DEFAULT_LANGUAGE] + rest


def language_name(code):
    """该语言**用自己写的名字**（自称名 / endonym），如 English、日本語。

    存在各语言 json 的 ``_language_name`` 元信息键里。理由：语言名不该被
    当前界面语言翻译——英文界面上「日本語」仍应显示「日本語」，译成
    "Japanese" 反而让人认不出。查不到就退回语言代码。
    """
    if code == DEFAULT_LANGUAGE:
        return _DEFAULT_LANGUAGE_NAME
    path = os.path.join(_I18N_DIR, "%s.json" % code)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return code
    if isinstance(data, dict):
        name = data.get("_language_name")
        if isinstance(name, str) and name:
            return name
    return code


def current_language():
    return _current


def set_language(code):
    """设置当前语言并立即加载字典。

    未知代码（i18n/ 下没有对应 json）回退到默认语言，而不是「设了但字典是空的」
    —— 后者会让 ``current_language()`` 报一个界面上根本不支持的代码。
    """
    global _current
    if code != DEFAULT_LANGUAGE and code not in available_languages():
        code = FALLBACK_LANGUAGE
    _current = code or DEFAULT_LANGUAGE
    load(_current)
    return _current


def load(code):
    """加载指定语言的字典；查不到就空字典（``t()`` 会原样返回中文）。"""
    global _dict, _loaded_for
    if _loaded_for == code:
        return _dict
    _dict = {}
    path = os.path.join(_I18N_DIR, "%s.json" % code)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                # 下划线开头的键是分组注释 / 元信息，不是译文，
                # 混进来会让覆盖率统计虚高。
                _dict = {k: v for k, v in data.items()
                         if not k.startswith("_")}
        except (ValueError, OSError):
            _dict = {}
    _loaded_for = code
    return _dict


def t(text):
    """把中文原文译成的当前语言；没有译文就返回原文。

    传入的 ``text`` 应当是**源码里写的中文原文**（也就是内部 ID 或待译文本），
    不要传已经翻译过的字符串 —— 否则二次查询必然落空。
    """
    if not _dict:
        return text
    return _dict.get(text, text)


def resolve_language(pref):
    """把语言「偏好」（可能含哨兵）解析成可加载的有效语言代码。

    下拉里除了真实收录的语言（zh_CN / en_US / 以后丢进 i18n/ 的 json），
    还有两类哨兵项：

    - ``"follow_system"``（跟随系统，默认选中）：启动 / 下次启动时按系统 UI
      语言选；系统语言命中已有语言就用它，否则回落 ``en_US``。
    - ``"zh_TW"``（繁體中文，仅占位）：翻译尚未实现，选中后回落简体中文
      （源码即译文，界面仍完整可读）。

    真实代码（默认 zh_CN 或在 available_languages() 里）原样返回。
    """
    if pref == DEFAULT_LANGUAGE or pref in available_languages():
        return pref
    if pref == "follow_system":
        return _detect_system_language()
    # 占位 / 未知 → 简体中文（源码原文），避免空字典让界面半中半英
    return DEFAULT_LANGUAGE


def _detect_system_language():
    """探测系统 UI 语言，映射到本项目已有语言；找不到则回落 English。"""
    try:
        from PySide6.QtCore import QLocale
        name = QLocale.system().name()   # e.g. "en_US" / "zh_CN" / "zh_TW"
    except Exception:
        name = ""
    candidates = [name]
    if "_" in name:
        candidates.append(name.split("_", 1)[0])
    for c in candidates:
        if c in available_languages() or c == DEFAULT_LANGUAGE:
            return c
    return "en_US"


def has_translation(text):
    """当前语言的字典里有没有这条译文（用于自检 / 覆盖率统计）。"""
    return text in _dict


def translation_count():
    return len(_dict)


# 启动时按默认语言初始化一次（中文模式字典为空，t() 原样返回）。
set_language(DEFAULT_LANGUAGE)
