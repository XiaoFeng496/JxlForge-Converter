# -*- coding: utf-8 -*-
"""Application entry point: python -m jxlforge"""

import os
import sys
import tempfile
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
    - 端到端（当 cjxl/djxl 可发现时自动追加）：直接调用 ``converter.encode``
      / ``converter.decode`` 跑一遍真实转换管线（PNG→JXL→PNG 往返、无损
      JPEG 重编码、有损 quality 编码、缺失输入优雅报错），抓「打包后
      cjxl/djxl 调用链路断掉 / 参数拼错」这类只有真正跑过才暴露的回归。
      引擎本就不打包，机器上没装 libjxl 时这部分标记 SKIP（不阻断整体
      PASS），但在装了 libjxl 的机器（含打包机）上会真正跑、坏了当场 FAIL。
    - ``--deep``：额外实例化 MainWindow，抓「模块未被收集」类 ImportError。

    返回退出码 0=PASS / 1=FAIL，并把报告写到 exe 同目录下的
    ``selftest_report.txt``（windowed 打包态无控制台，靠文件 + 退出码观测）。
    """
    report = []
    checks = []  # (label, status, detail)  status in PASS/FAIL/SKIP

    def add(label, ok, detail="", skip=False):
        status = "SKIP" if skip else ("PASS" if ok else "FAIL")
        checks.append((label, status, detail))

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

    # 4) 端到端转换管线：cjxl 与 djxl 都在才跑；否则整体标记 SKIP。
    if found["cjxl"] and found["djxl"]:
        _run_e2e_checks(add)
    else:
        for label in (
            "e2e: encode PNG->JXL",
            "e2e: decode JXL->PNG round-trip",
            "e2e: lossless JPEG re-encode",
            "e2e: lossy quality=70 encode",
            "e2e: Pillow-transit BMP->JXL (via _encode_source)",
            "e2e: HEIC decode-capable (pi_heif bundled)",
            "e2e: missing-input handled gracefully (no crash)",
        ):
            add(label, True, "SKIP: cjxl/djxl not found (engine not bundled by default)",
                skip=True)

    # 5) 可选 deep：实例化主窗口，抓「模块未被收集」类缺失。
    if deep:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            from .main_window import MainWindow
            MainWindow()
            add("MainWindow instantiates", True, "")
        except Exception as e:  # noqa: BLE001
            add("MainWindow instantiates", False, "%s: %s" % (type(e).__name__, e))

    failed = [c for c in checks if c[1] == "FAIL"]
    passed = not failed
    report.append("JxlForge Converter --selftest @ %s"
                  % time.strftime("%Y-%m-%d %H:%M:%S"))
    for label, status, detail in checks:
        report.append("[%s] %s%s" % (status, label, ("  -- " + detail) if detail else ""))
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


def _run_e2e_checks(add):
    """端到端转换管线真实跑一遍（cjxl/djxl 已确认可发现时调用）。

    每个用例直接用程序自己的 ``converter.encode`` / ``converter.decode`` /
    ``converter.is_lossless_jpeg_jxl``，与 GUI 走的是同一套执行路径，因此
    能抓到「打包后 cjxl/djxl 子进程调用链路断掉」或「参数拼错导致 cjxl
    退出非零」这类只有真正跑过转换才暴露的回归。临时文件用 mkdtemp 统一
    清理，不会污染用户目录。
    """
    import shutil as _shutil
    from PIL import Image
    from . import converter

    tmp = tempfile.mkdtemp(prefix="jxlforge_e2e_")
    try:
        # 造一张小 PNG（纯色，便于快速编码）。
        src_png = os.path.join(tmp, "src.png")
        Image.new("RGB", (64, 64), (123, 200, 77)).save(src_png, "PNG")

        # 1) 编码 PNG -> JXL（最基本、最该有的能力）。
        out_jxl = os.path.join(tmp, "out.jxl")
        ok, msg, tag = converter.encode(src_png, out_jxl, effort=4)
        good = ok and os.path.isfile(out_jxl) and os.path.getsize(out_jxl) > 0
        add("e2e: encode PNG->JXL", good,
            "" if good else "msg=%s tag=%s" % (msg, tag))

        if good:
            # 2) 解码 JXL -> PNG 往返，并校验产物是合法 PNG（防「文件生成了但
            #    解码损坏」这类静默失败）。
            dec_png = os.path.join(tmp, "dec.png")
            dok, dmsg = converter.decode(out_jxl, dec_png)
            valid = dok and os.path.isfile(dec_png) and os.path.getsize(dec_png) > 0
            if valid:
                try:
                    Image.open(dec_png).verify()
                except Exception:
                    valid = False
            add("e2e: decode JXL->PNG round-trip", valid,
                "" if valid else "decode ok=%s msg=%s" % (dok, dmsg))

            # 3) 无损 JPEG 重编码：编码时传 lossless_jpeg=True，再用
            #    is_lossless_jpeg_jxl 验证产物确实是可比特还原的 JPEG 重编码。
            src_jpg = os.path.join(tmp, "src.jpg")
            Image.new("RGB", (48, 48), (10, 20, 30)).save(src_jpg, "JPEG", quality=90)
            jxl_jpg = os.path.join(tmp, "out_j.jpg")
            ok2, msg2, _ = converter.encode(src_jpg, jxl_jpg, effort=4,
                                           lossless_jpeg=True)
            okj = ok2 and os.path.isfile(jxl_jpg) and os.path.getsize(jxl_jpg) > 0
            if okj:
                recon = converter.is_lossless_jpeg_jxl(jxl_jpg)
                add("e2e: lossless JPEG re-encode", recon is True,
                    "" if recon is True else "is_lossless_jpeg_jxl=%r" % recon)
            else:
                add("e2e: lossless JPEG re-encode", False, msg2)

            # 4) 有损 quality 编码（经 --quality 路径，验证参数拼装不崩）。
            out_q = os.path.join(tmp, "out_q.jxl")
            okq, msgq, _ = converter.encode(src_png, out_q, effort=4, quality=70)
            add("e2e: lossy quality=70 encode",
                okq and os.path.isfile(out_q) and os.path.getsize(out_q) > 0,
                "" if okq else msgq)

        # 5) Pillow 中转格式（BMP）：cjxl 原生读不了，_encode_source 必须路由到
        #    Pillow。直接走应用真实路径（_encode_source）覆盖 converter.encode-only
        #    用例没碰到的中转逻辑；本段仅在 cjxl 可发现时运行（上层已判断）。
        try:
            from PySide6.QtWidgets import QApplication as _QA
            _qa = _QA.instance() or _QA([])
            from .main_window import ConvertWorker as _CW
            src_bmp = os.path.join(tmp, "src.bmp")
            Image.new("RGB", (32, 32), (200, 100, 50)).save(src_bmp, "BMP")
            out_bmp = os.path.join(tmp, "out_bmp.jxl")
            _cw = _CW([], [])
            ok_b, _mb, _tb = _cw._encode_source(src_bmp, out_bmp, [])
            good_b = ok_b and os.path.isfile(out_bmp) and os.path.getsize(out_bmp) > 0
            add("e2e: Pillow-transit BMP->JXL (via _encode_source)", good_b,
                "" if good_b else "msg=%s" % _mb)
        except Exception as exc:  # noqa: BLE001
            add("e2e: Pillow-transit BMP->JXL (via _encode_source)", False,
                "%s: %s" % (type(exc).__name__, exc))

        # 6) HEIC 解码能力（pi_heif）：用随包测试资源验证「冻结包内能解码
        #    HEIC」——这是 HEIC 支持的关键门槛。要求 pi_heif 已随包收集且
        #    libheif/libde265 解码链可用（pi_heif 的 libheif 为解码专用构建，
        #    不链 libx265，无需带编码器）。资源经源码态 pi_heif 预生成并提交（tools/test_assets）。
        try:
            from .main_window import _ensure_heif_opener, _decode_to_temp_file
            # 资源在源码态位于 <repo>/tools/test_assets/；打包后随 datas 落到
            # <bundle>/jxlforge/test_assets/，两条路径都试。
            _here = os.path.dirname(os.path.abspath(__file__))
            asset_candidates = [
                os.path.normpath(os.path.join(_here, "test_assets", "sample.heic")),
                os.path.normpath(os.path.join(_here, "..", "tools", "test_assets", "sample.heic")),
            ]
            asset = next((p for p in asset_candidates if os.path.isfile(p)), None)
            if asset is None:
                add("e2e: HEIC decode-capable (pi_heif bundled)", True,
                    "SKIP: sample.heic asset missing", skip=True)
            elif not _ensure_heif_opener():
                add("e2e: HEIC decode-capable (pi_heif bundled)", False,
                    "pi_heif not bundled in this package")
            else:
                dec = _decode_to_temp_file(asset)
                ok_h = isinstance(dec, str) and dec.lower().endswith(".png") \
                    and os.path.isfile(dec) and os.path.getsize(dec) > 0
                add("e2e: HEIC decode-capable (pi_heif bundled)", ok_h,
                    "" if ok_h else "decode returned %r" % dec)
        except Exception as exc:  # noqa: BLE001
            add("e2e: HEIC decode-capable (pi_heif bundled)", False,
                "%s: %s" % (type(exc).__name__, exc))

        # 7) 错误路径：输入文件不存在 -> 应优雅返回 False（不抛异常、不卡死）。
        miss = os.path.join(tmp, "nope.png")
        eok, _emsg, _ = converter.encode(miss, os.path.join(tmp, "x.jxl"))
        add("e2e: missing-input handled gracefully (no crash)", eok is False,
            "" if eok is False else "expected failure but got ok=True")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


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
