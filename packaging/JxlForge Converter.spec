# -*- mode: python ; coding: utf-8 -*-
# JxlForge Converter 默认打包 spec（one-folder, windowed）。
# 已做精简：剔除运行期用不到的二进制 + PySide6 子模块（纯 Widgets 程序）。
# 本文件是发布默认；构建请用仓库 packaging/build_dist.bat（已入库、带 --selftest 门）。
import os

# 仓库根目录：spec 位于 <repo>/packaging/，上一级即项目根 JxlForge-Converter/
REPO = os.path.normpath(os.path.join(SPECPATH, '..'))
# 运行时数据目录（与代码同目录，i18n.py 通过 __file__ 定位）：
# jxlforge/i18n/*.json 必须进包，否则语言下拉扫不到 en_US/zh_TW，英文界面失效。
I18N_DIR = os.path.join(REPO, 'jxlforge', 'i18n')
# 打包入口：与 spec 同目录的 _launch_app.py（真实常驻启动序列，run() 支持 --selftest）
ENTRY = os.path.join(SPECPATH, '_launch_app.py')


# 可省运行期二进制（实测可删，不影响功能）：
#   opengl32sw.dll         软件 GL 渲染器，本程序用系统 OpenGL，用不到（~20.6MB）
#   libcrypto-3*.dll       OpenSSL，程序不走网络/SSL（~11.0MB）
#   mfc140u.dll            VC++ MFC，被 pywin32(send2trash)间接拉入（~5.7MB）；
#                          删除后已实测「删除原图到回收站」路径不崩
#   qt6qml*.dll / qt6quick*.dll / qt6qmlmodels*.dll / qt6qmlmeta*.dll /
#   qt6qmlworkerscript*.dll / qt6pdf*.dll / qt6network*.dll / qt6virtualkeyboard*.dll
#                          PySide6 的 QML/Quick/网络/PDF/虚拟键盘模块，纯 Widgets 程序不用
#                          （PyInstaller 的 Qt hook 无视模块 excludes 仍会收这些 DLL，
#                           故必须在 a.binaries 层按 basename 过滤，共省 ~20MB）
# 注意：PIL._avif 必须保留——AVIF 是程序的输入格式（main_window._DECODE_TO_TEMP_EXTS），
#       经 Pillow 解码，依赖该插件；若 exclude 会导致 AVIF 输入崩溃。
# 注意：Qt6Svg（SVG 图标）/ Qt6OpenGL（Widgets 用系统 GL）必须保留。
# 注意：libheif-*.dll / libde265-*.dll 必须保留——pi_heif 的 libheif 是*解码专用*
#       构建，不链 libx265（实测导入表无 x265），故 HEIC/HEIF 解码无需带 HEVC 编码器，
#       包体比 pillow-heif（其 libheif 硬链 x265）精简约 22MB。
_DROP_BIN = {
    'opengl32sw.dll',
    'libcrypto-3-x64.dll',
    'libcrypto-3.dll',
    'mfc140u.dll',
    'qt6qml.dll',
    'qt6quick.dll',
    'qt6qmlmodels.dll',
    'qt6qmlmeta.dll',
    'qt6qmlworkerscript.dll',
    'qt6pdf.dll',
    'qt6network.dll',
    'qt6virtualkeyboard.dll',
}

# 不 import 的 PySide6 子模块（纯 Widgets 程序无需 QML/网络/PDF/虚拟键盘）。
# 仅删 .pyd，底层 Qt6*.dll 已在上面 _DROP_BIN 按 basename 一并过滤。
_EXCLUDE_MODULES = [
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtNetwork',
    'PySide6.QtPdf',
    'PySide6.QtVirtualKeyboard',
]

a = Analysis(
    [ENTRY],
    pathex=[REPO],
    binaries=[],
    datas=[
        (I18N_DIR, 'jxlforge/i18n'),
        # HEIC 解码能力自检资源：随包落入 <bundle>/jxlforge/test_assets/，
        # 让冻结版 --selftest 能验证 HEIC 解码链（libheif+libde265）确实可用。
        (os.path.join(REPO, 'tools', 'test_assets'), 'jxlforge/test_assets'),
    ],
    hiddenimports=['pi_heif'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDE_MODULES,
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
