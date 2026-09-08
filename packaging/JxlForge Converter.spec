# -*- mode: python ; coding: utf-8 -*-
import os

# 仓库根目录：spec 位于 <repo>/packaging/，上一级即项目根 JxlForge-Converter/
REPO = os.path.normpath(os.path.join(SPECPATH, '..'))
# 运行时数据目录（与代码同目录，i18n.py 通过 __file__ 定位）：
# jxlforge/i18n/*.json 必须进包，否则语言下拉扫不到 en_US/zh_TW，英文界面失效。
I18N_DIR = os.path.join(REPO, 'jxlforge', 'i18n')
# 打包入口：与 spec 同目录的 _launch_app.py
ENTRY = os.path.join(SPECPATH, '_launch_app.py')


# 可省运行期二进制（按 Tier 1 瘦身，详见下方 a.binaries 过滤）：
#   opengl32sw.dll         软件 GL 渲染器，本程序用系统 OpenGL，用不到（~20.6MB）
#   libcrypto-3*.dll       OpenSSL，程序不走网络/SSL（~11.0MB）
#   mfc140u.dll            VC++ MFC，被 pywin32(send2trash)间接拉入（~5.7MB）；
#                          删除后必须实测「删除原图到回收站」路径不崩
# 注意：PIL._avif 必须保留——AVIF 是程序的输入格式（main_window._DECODE_TO_TEMP_EXTS），
#       经 Pillow 解码，依赖该插件；若 exclude 会导致 AVIF 输入崩溃。
_DROP_BIN = {
    'opengl32sw.dll',
    'libcrypto-3-x64.dll',
    'libcrypto-3.dll',
    'mfc140u.dll',
}

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
# 剔除非必需二进制：a.binaries 为 TOC 列表，元素为 [目标名, 源路径, 类型]
a.binaries = [b for b in a.binaries
              if os.path.basename(b[0]).lower() not in _DROP_BIN]
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
