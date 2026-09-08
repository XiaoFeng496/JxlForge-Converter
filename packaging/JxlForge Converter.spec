# -*- mode: python ; coding: utf-8 -*-
import os

# 仓库根目录：spec 位于 <repo>/packaging/，上一级即项目根 JxlForge-Converter/
REPO = os.path.normpath(os.path.join(SPECPATH, '..'))
# 运行时数据目录（与代码同目录，i18n.py 通过 __file__ 定位）：
# jxlforge/i18n/*.json 必须进包，否则语言下拉扫不到 en_US/zh_TW，英文界面失效。
I18N_DIR = os.path.join(REPO, 'jxlforge', 'i18n')
# 打包入口：与 spec 同目录的 _launch_app.py
ENTRY = os.path.join(SPECPATH, '_launch_app.py')


a = Analysis(
    [ENTRY],
    pathex=[REPO],
    binaries=[],
    datas=[
        (I18N_DIR, 'jxlforge/i18n'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JxlForge Converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JxlForge Converter',
)
