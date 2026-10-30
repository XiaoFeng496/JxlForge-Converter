# -*- coding: utf-8 -*-
"""Qt 版本一致性检查（构建门禁，中文输出）。

为什么要有这道门禁：v0.3.0 的发行包是在 PySide6 6.11.2 环境上打的，但包里的
``plugins/styles/qmodernwindowsstyle.dll`` 是残留的 **6.11.1**（pip 升级 PySide6 时
不清理旧插件，PyInstaller 就原样收了进去）。结果那版所有按钮都比正常的小一圈、
按钮间隙也更大，用户拿新旧两版截图一眼看出不一样，排查成本很高。

用法（两个构建脚本都调它，构建前查环境、构建后查产物）::

    python check_qt_versions.py --env            # 查当前解释器装好的 PySide6/shiboken6
    python check_qt_versions.py <目录> [--label 名称]

退出码：0 = 一致（或没扫到 Qt 文件，视为跳过）；1 = 混装（≥2 个 Qt 版本）。
"""
import ctypes
import os
import subprocess
import sys

EXIT_OK = 0
EXIT_MIXED = 1

# Qt 家族的文件名前缀。只有这些文件参与版本比对：同目录下的 libjpeg、webp、
# MSVC 运行库（14.x）等第三方 DLL 版本天然不同，混进来会误报。
_QT_NAME_PREFIXES = ("qt", "shiboken", "pyside")


class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
    ]


_VER_DLL = None


def _ver_api():
    """惰性准备 version.dll 的签名（argtypes 必须显式声明，否则传 str 会报错）。"""
    global _VER_DLL
    if _VER_DLL is not None:
        return _VER_DLL
    try:
        ver = ctypes.WinDLL("version")
        ver.GetFileVersionInfoSizeW.argtypes = [
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        ver.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
        ver.GetFileVersionInfoW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = ctypes.c_int
        ver.VerQueryValueW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        ver.VerQueryValueW.restype = ctypes.c_int
        _VER_DLL = ver
    except Exception:
        _VER_DLL = False
    return _VER_DLL


def file_version(path):
    """返回 PE 文件的 (major, minor, patch)；读不到返回 None。

    用 ctypes 直调 version.dll：构建脚本只保证有 PyInstaller，不该再依赖 pywin32。
    """
    ver = _ver_api()
    if not ver:
        return None
    try:
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint32()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        if not ptr.value or length.value < ctypes.sizeof(_VS_FIXEDFILEINFO):
            return None
        info = _VS_FIXEDFILEINFO.from_address(ptr.value)
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return (ms >> 16, ms & 0xFFFF, ls >> 16)
    except Exception:
        return None


def is_qt_family(rel):
    """判断某个相对路径是否属于「Qt 家族」（只给这些文件做版本比对）。

    Qt 家族 = Qt6*.dll / shiboken6* / pyside6* / 任何 .pyd 扩展 / plugins 下的 DLL。
    """
    name = os.path.basename(rel).lower()
    if not name.endswith((".dll", ".pyd")):
        return False
    if name.startswith(_QT_NAME_PREFIXES) or name.endswith(".pyd"):
        return True
    return "plugins" in rel.replace("\\", "/").lower()


def scan_qt_versions(root):
    """扫描 root 下 Qt 家族文件的版本分布，返回 {版本号: [相对路径, ...]}。"""
    found = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root)
            if not is_qt_family(rel):
                continue
            v = file_version(path)
            if not v or v[0] not in (5, 6, 7):
                continue
            found.setdefault("%d.%d.%d" % v, []).append(rel)
    return found


def check_dir(root, label, out=print):
    """检查一个目录；返回 True = 一致。混装时打印报告并返回 False。"""
    if not os.path.isdir(root):
        out("[跳过] %s：目录不存在（%s）" % (label, root))
        return True
    found = scan_qt_versions(root)
    if not found:
        out("[跳过] %s：没有带版本号的 Qt 文件（无可比对项）" % label)
        return True
    versions = sorted(found, key=lambda s: [int(x) for x in s.split(".")])
    out("[预检] %s：%d 个 Qt 版本 / %d 个文件（%s）" % (
        label, len(versions), sum(len(v) for v in found.values()),
        ", ".join("%s×%d" % (v, len(found[v])) for v in versions)))
    if len(versions) == 1:
        return True

    out("")
    out("[失败] %s 里发现 %d 个不同的 Qt 版本 —— 这是混合安装，" % (label, len(versions)))
    out("       打出来的包会出现「按钮/控件样式与预期不符」这类很难查的问题：")
    for v in versions:
        files = found[v]
        out("         %-10s %d 个文件，例如：%s" % (
            v, len(files), ", ".join(files[:3])))
    out("       修法：整套强制重装，让四个包版本完全一致，然后重新打包：")
    out("         python -m pip install --force-reinstall --no-cache-dir ^")
    out("             PySide6==6.11.3 PySide6-Essentials==6.11.3 ^")
    out("             PySide6-Addons==6.11.3 shiboken6==6.11.3")
    return False


def installed_pyside_dir(python_exe=None):
    """返回解释器实际加载的 PySide6 包目录（拿不到返回 None）。

    不给 ``python_exe`` 时用当前进程的解释器（省一次子进程）；给了就去问那个
    解释器——构建脚本用的解释器未必是本脚本自己跑的那个。
    """
    if python_exe is None:
        try:
            import PySide6
        except Exception:
            return None
        d = os.path.dirname(os.path.abspath(PySide6.__file__))
        return d if os.path.isdir(d) else None
    code = "import os, PySide6; print(os.path.dirname(PySide6.__file__))"
    try:
        out = subprocess.run([python_exe, "-c", code], capture_output=True,
                             text=True, timeout=180)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    for line in reversed(out.stdout.strip().splitlines()):
        d = line.strip()
        if d and os.path.isdir(d):
            return d
    return None


def check_environment(out=print, python_exe=None):
    """检查 PySide6 + shiboken6 的版本一致性；返回 True = 一致。"""
    site_dir = installed_pyside_dir(python_exe)
    if not site_dir:
        out("[跳过] 未能定位已安装的 PySide6 目录")
        return True
    out("[预检] PySide6 目录 = %s" % site_dir)
    if not check_dir(site_dir, "已安装的 PySide6", out):
        return False
    shiboken_dir = os.path.join(os.path.dirname(site_dir), "shiboken6")
    if os.path.isdir(shiboken_dir):
        if not check_dir(shiboken_dir, "已安装的 shiboken6", out):
            return False
    return True


def main(argv):
    args = [a for a in argv[1:]]
    label = None
    if "--label" in args:
        i = args.index("--label")
        if i + 1 < len(args):
            label = args[i + 1]
            del args[i:i + 2]

    def out(line=""):
        print(line, flush=True)

    if not args:
        out(__doc__ or "用法：check_qt_versions.py [--env | <目录>]")
        return 2
    if args[0] == "--env":
        return EXIT_OK if check_environment(out) else EXIT_MIXED
    target = args[0]
    if label is None:
        label = os.path.basename(os.path.normpath(target)) or target
    return EXIT_OK if check_dir(target, label, out) else EXIT_MIXED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
