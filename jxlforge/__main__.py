# -*- coding: utf-8 -*-
"""Application entry point: python -m jxlforge"""

import os
import sys
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import i18n
from .main_window import MainWindow, _LANGUAGE_DEFAULT, _LANGUAGE_COMBO_ORDER

# 更名前的 QSettings 标识。项目从 libjxl_GUI 改名为 JxlForge Converter 后
# org/app 变了，ini 路径随之改变；老用户的配置若不做迁移就会静默丢失。
_LEGACY_SETTINGS_ORG = "libjxl"
_LEGACY_SETTINGS_APP = "libjxl-gui"
# 哨兵键：迁移只做一次。放在独立的 _meta 组，避免和真正的设置混在一起。
_SETTINGS_MIGRATED_KEY = "_meta/migrated_from_libjxl"


def _apply_persisted_language():
    """Load the persisted language and install it **before** the UI is built.

    必须在构造 MainWindow 之前：界面文本是在构建时从字典取定的，之后再改
    字典只会让新旧语言混在一起（新弹出的控件是英文、已存在的是中文）。
    这也是本项目选择「切语言重启生效」的原因——实时刷新所有控件既容易漏，
    又要额外缓存原文。

    偏好可能含哨兵（"follow_system" / "zh_TW" 占位），先用 i18n.resolve_language()
    解析成可加载的有效代码（随系统选 / 非已有语言回落 English / 占位回落中文），
    再 set_language。返回解析后的有效代码。
    """
    settings = QSettings()
    settings.beginGroup("appearance")
    code = settings.value("language", _LANGUAGE_DEFAULT)
    settings.endGroup()
    if code not in _LANGUAGE_COMBO_ORDER:
        code = _LANGUAGE_DEFAULT
    effective = i18n.resolve_language(code)
    i18n.set_language(effective)
    return effective


def _migrate_legacy_settings():
    """把旧标识下的配置一次性搬到新标识，老用户的设置不因改名而丢失。

    要保的不只是主题和窗口几何，还有校准出来的 ``big_image_floor_px``
    （跑一次基准要几分钟，丢了只能重跑）。

    只在旧 ini 确实存在时才搬；搬完写哨兵，之后每次启动直接返回。
    旧文件保留不删 —— 迁移是只读复制，出错也不至于两头都没有。
    """
    current = QSettings()
    already = str(current.value(_SETTINGS_MIGRATED_KEY, "")).lower()
    if already in ("true", "1"):
        return

    legacy = QSettings(QSettings.IniFormat, QSettings.UserScope,
                       _LEGACY_SETTINGS_ORG, _LEGACY_SETTINGS_APP)
    legacy_path = legacy.fileName()
    if not legacy_path or not os.path.exists(legacy_path):
        # 新装用户，或测试环境（UserScope 已被指向临时目录）。
        current.setValue(_SETTINGS_MIGRATED_KEY, True)
        current.sync()
        return

    for key in legacy.allKeys():
        current.setValue(key, legacy.value(key))
    current.setValue(_SETTINGS_MIGRATED_KEY, True)
    current.sync()


def _selftest(deep=False):
    """打包后完整性自检（不建窗口、不进 GUI）。

    通过程序自己的 ``i18n.available_languages()`` 等运行时接口做检查，
    与正式运行走同一套代码，因此能抓到「数据没打进包 / 路径没对齐到
    _MEIPASS / JSON 损坏」这类打包态才暴露的缺失——这正是源码态回归
    测试覆盖不到、却最容易漏掉的维度。

    - 默认（资源层）：校验 i18n 字典已打包且可加载、libjxl 引擎查找结果。
      仅启动 exe 本身就会先验证整条 import 链（模块缺失则 exe 起不来，
      selftest 根本跑不到，退出码非 0）。
    - ``--deep``：额外实例化 MainWindow，抓「模块未被收集」类 ImportError。

    返回退出码 0=PASS / 1=FAIL，并把报告写到 exe 同目录下的
    ``selftest_report.txt``（windowed 打包态无控制台，靠文件 + 退出码观测）。
    """
    report = []
    checks = []  # (label, ok, detail)

    def add(label, ok, detail=""):
        checks.append((label, ok, detail))

    # 1) i18n 语言清单：打包必须把 en_US / zh_TW 的 json 收进 datas。
    langs = i18n.available_languages()
    for code in ("en_US", "zh_TW"):
        ok = code in langs
        add("i18n language present: %s" % code, ok,
            "" if ok else "available=%s（打包漏了 i18n/*.json）" % langs)

    # 2) 英文字典真能加载且有内容（抓「文件在但空 / 损坏」）。
    if "en_US" in langs:
        i18n.set_language("en_US")
        n = i18n.translation_count()
        ok = n > 400
        add("en_US dictionary loads with content", ok,
            "translation_count=%d（期望 >400）" % n)
    else:
        add("en_US dictionary loads with content", False, "skipped: en_US missing")

    # 3) libjxl 引擎可发现：打包本就不带引擎，仅报告、不阻断。
    from . import converter
    found = {t: converter.find_tool(t) is not None for t in ("cjxl", "djxl", "jxlinfo")}
    add("libjxl engines discoverable (engine not bundled by default)", True,
        "cjxl=%s djxl=%s jxlinfo=%s" % (found["cjxl"], found["djxl"], found["jxlinfo"]))

    # 4) 可选 deep：实例化主窗口，抓「模块未被收集」类缺失。
    if deep:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            from .main_window import MainWindow
            MainWindow()
            add("MainWindow instantiates", True, "")
        except Exception as e:  # noqa: BLE001
            add("MainWindow instantiates", False, "%s: %s" % (type(e).__name__, e))

    passed = all(ok for _, ok, _ in checks)
    report.append("JxlForge Converter --selftest @ %s"
                  % time.strftime("%Y-%m-%d %H:%M:%S"))
    for label, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        report.append("[%s] %s%s" % (mark, label, ("  -- " + detail) if detail else ""))
    report.append("")
    report.append("RESULT: %s" % ("PASS" if passed else "FAIL"))

    text = "\n".join(report) + "\n"
    try:
        out = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                           "selftest_report.txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        out = None
    # 源码态（有控制台）也打到 stdout，便于即时查看。
    print(text)
    return 0 if passed else 1


def run():
    """Create the QApplication and show the main window."""
    # 打包后自检入口：--selftest / --verify 不建窗口，只做资源完整性检查。
    # 必须放在最前，避免任何 QSettings / QApplication 副作用。
    if "--selftest" in sys.argv or "--verify" in sys.argv:
        sys.exit(_selftest(deep="--deep" in sys.argv))

    # Persist settings to a portable .ini file instead of the Windows registry.
    # Must be set before any QSettings object is constructed.
    QSettings.setDefaultFormat(QSettings.IniFormat)

    app_start = time.perf_counter()  # 进程启动时刻（白屏基准用）
    app = QApplication([])
    # Stable identity for QSettings so persisted data survives restarts.
    app.setOrganizationName("JxlForge")
    app.setApplicationName("JxlForge-Converter")
    # 必须在读任何设置之前：语言偏好、主题、校准值都在旧 ini 里。
    _migrate_legacy_settings()
    _apply_persisted_language()
    window = MainWindow()
    window._app_start = app_start
    # 真实启动：允许「首次启动自动校准大图阈值」（headless 测试不会置此标志，
    # 避免测试期间触发耗时的 cjxl 基准测量）。
    window._auto_calibrate_enabled = True
    window.show()
    return app.exec()


if __name__ == "__main__":
    run()
