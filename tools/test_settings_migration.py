# -*- coding: utf-8 -*-
"""更名后的 QSettings 标识迁移回归测试。

项目从 libjxl_GUI 改名为 JxlForge Converter，QSettings 的 org/app 随之改变，
ini 路径从 ``%APPDATA%\\libjxl\\libjxl-gui.ini`` 变成
``%APPDATA%\\JxlForge\\JxlForge-Converter.ini``。

不做迁移的话，老用户升级后所有设置会静默归零——最痛的是校准出来的
``big_image_floor_px``（跑一次基准要几分钟）。本测试锁住三件事：

1. 旧标识下的键值确实搬到新标识下；
2. 迁移只做一次（哨兵生效，重复调用不报错也不重复写）；
3. 旧文件保留不删（迁移是只读复制，出错不至于两头都没有）。

运行::

    python tools/test_settings_migration.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

LEGACY_ORG = "libjxl"
LEGACY_APP = "libjxl-gui"
NEW_ORG = "JxlForge"
NEW_APP = "JxlForge-Converter"

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


def main():
    tmp = tempfile.mkdtemp(prefix="jxlforge_migrate_")
    try:
        # 隔离到临时目录：真实 %APPDATA% 下的配置一个字节都不碰。
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp)
        QSettings.setDefaultFormat(QSettings.IniFormat)

        # --- 1. 造一份「旧标识」下的配置 ---
        legacy = QSettings(QSettings.IniFormat, QSettings.UserScope,
                           LEGACY_ORG, LEGACY_APP)
        legacy.setValue("conversion/big_image_floor_px", 2000000)
        legacy.setValue("appearance/theme", "native_noflicker_proto")
        legacy.sync()
        legacy_path = legacy.fileName()
        check("旧 ini 已造好", os.path.exists(legacy_path), legacy_path)
        del legacy

        app = QApplication([])
        app.setOrganizationName(NEW_ORG)
        app.setApplicationName(NEW_APP)

        from jxlforge.__main__ import _migrate_legacy_settings

        _migrate_legacy_settings()

        cur = QSettings()
        check("校准值已迁移",
              str(cur.value("conversion/big_image_floor_px")) == "2000000",
              repr(cur.value("conversion/big_image_floor_px")))
        check("主题已迁移",
              cur.value("appearance/theme") == "native_noflicker_proto",
              repr(cur.value("appearance/theme")))
        check("哨兵已写入",
              str(cur.value("_meta/migrated_from_libjxl")).lower() in ("true", "1"),
              repr(cur.value("_meta/migrated_from_libjxl")))
        check("旧文件保留未删", os.path.exists(legacy_path))

        # --- 2. 重复调用：不该报错，也不该重复搬运 ---
        cur.setValue("conversion/big_image_floor_px", 1234567)
        cur.sync()
        _migrate_legacy_settings()
        check("二次调用不回写旧值",
              str(cur.value("conversion/big_image_floor_px")) == "1234567",
              repr(cur.value("conversion/big_image_floor_px")))

        # --- 3. 新装用户：旧 ini 不存在时也要写下哨兵，且不吃异常 ---
        tmp2 = tempfile.mkdtemp(prefix="jxlforge_fresh_")
        try:
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tmp2)
            _migrate_legacy_settings()
            fresh = QSettings()
            check("无旧配置时仍写哨兵",
                  str(fresh.value("_meta/migrated_from_libjxl")).lower() in ("true", "1"),
                  repr(fresh.value("_meta/migrated_from_libjxl")))
            check("无旧配置时不产生垃圾键",
                  fresh.value("conversion/big_image_floor_px") is None)
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d 项通过，%d 项失败" % (_passed, len(_failures)))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
