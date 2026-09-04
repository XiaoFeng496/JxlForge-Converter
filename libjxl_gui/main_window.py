# -*- coding: utf-8 -*-
"""PySide6 main window that mimics XnConvert's step-by-step workflow.

The UI is organized as five tabs (输入 / 动作 / 输出 / 状态 / 设置). The 状态 tab
hosts the run log and environment detection. A persistent bottom bar keeps the
「转换」,「停止」and 「关闭」buttons always visible, and the whole window accepts
file drops (switching to the 输入 tab and appending the dropped items on drop).

The 输入 tab adds a view switcher (small/normal/large thumbnails, list,
details) and a quick-filter bar (a drop-down button plus a text box) so the
user can preview the actual image content and filter the list.

Input ordering:
  * In the thumbnail (IconMode) and details (table) views, items are reordered
    by dragging them directly inside the widget (internal drag & drop). A blue
    insertion line is drawn in the *seam* between cells to preview where the
    item will land (the snap range is the whole viewport); the actual reorder
    is performed manually so no row/item is ever lost.
  * In the list (ListMode) view, dragging is NOT used for sorting; instead it
    rubber-band selects items (NoDragDrop). Other view modes keep drag-sort.

Every thumbnail sits inside a fixed-ratio box (a fixed grid cell) so the box
ratio never changes with the image aspect ratio or the file name length. The
thumbnail itself is always a square pixmap (letterboxed), which also keeps the
file name visible. Hovering an item shows image info; double-clicking opens a
centered preview dialog that supports wheel zoom and drag-to-pan.

All code identifiers (variables, functions, classes) and subprocess command
parameters are in English; all visible UI text (labels, buttons, status bar)
is in Chinese.
"""

import os
import math
import time
import json
import atexit
import shutil
import shlex
import tempfile
import threading
import queue

# 移到回收站依赖 send2trash（Windows 上走 IFileOperation）；缺失时不阻断整个
# 程序启动，_move_to_recycle_bin 会给出清晰报错，调用方据此保留原文件。
try:
    import send2trash
except ImportError:
    send2trash = None

# 大图并发校准：应用内「一键校准」按钮与命令行校准脚本共用同一份逻辑/常量真源。
# calibrate 不回注 main_window（无循环依赖），仅导出常量与 run_calibration 等。
from . import calibrate
from . import power
from . import i18n

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QSize,
    Qt,
    QUrl,
    QSettings,
    QTimer,
    QThread,
    QThreadPool,
    QRunnable,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QPalette,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPixmap,
    QWheelEvent,
    QCursor,
    QMouseEvent,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QSizePolicy,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleFactory,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QDoubleSpinBox,
    QFormLayout,
    QDialogButtonBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import converter, processor, formats


# ----------------------------------------------------------------------
# Preview / thumbnail support for formats Qt cannot load natively
#
# Qt's built-in image readers (QImageReader / QPixmap) do NOT understand some
# modern containers (JPEG XL, AVIF) nor a few scientific / HDR formats
# (PFM / PAM / PGX). Those yield a null pixmap — no thumbnail and no
# double-click preview. We decode them to a temporary viewable file and let Qt
# read that instead:
#   * JXL  → 临时 PNG（djxl，已可用）
#   * AVIF → 临时 PNG（Pillow，已可用）
#   * PFM / PAM / PGX → 临时 PPM（libjxl_gui.formats 纯 Python 解码，0 新依赖）
# EXR 是浮点 HDR 格式，Qt/Pillow 均不原生支持且本机无 OpenEXR；它只解析头部
# 元数据用于预览展示，不做像素渲染（见 libjxl_gui.formats.parse_exr_header）。
# 解码出的临时文件按源路径缓存，重复缩略图 / 预览不再重复解码。
# ----------------------------------------------------------------------
_DECODE_TO_TEMP_EXTS = {".jxl", ".avif", ".pfm", ".pam", ".pgx"}

_DECODE_TEMP_CACHE = {}       # src_path -> 解码出的临时可显示文件（PNG/PPM）路径
# 线程安全锁：异步预览（_PreviewLoader）与后续缩略图线程池都可能在子线程里
# 读/写该缓存，必须加锁，否则会出现竞态（同一文件被并发解码两次、缓存写入错位）。
_DECODE_TEMP_CACHE_LOCK = threading.Lock()

# 预览解码策略：默认按「原生分辨率」解码（不再为 fit 而缩小），fit 到窗口时
# 永远是「从大到小缩」，清晰无糊。用户反馈 2560×1440 截图也糊，根因是旧逻辑按
# 「窗口 × DPR × 1.5」钳制解码上限（如窗口 1190 宽则解码上限仅 1785，原图被压小后
# fit 放大糊掉）。放开后一般截图/照片均原生解码，fit 始终清晰。
# 仅保留一个极端巨图的 OOM 保护上限：单边超过此值的超巨图（如拼接图 / 8K+）才
# 等比缩到此值以内，避免一次性解码吃光内存。正常图片（≤此值）一律原生解码。
_PREVIEW_OOM_CAP = 8192
# 「预览加载中…」提示的延迟显示阈值（毫秒）。
# 切换图片/改动作时，若当前已有预览图，先保持旧图不动 —— 不隐藏视图、不切
# 文字，避免「图消失 → 加载中文字 → 图回来」的三步跳变造成闪烁（与图片大小
# 无关，小图解码只要几毫秒却仍会完整跳变一次，故大图小图都闪）。
# 只有解码耗时超过此阈值（真·大图）才切到加载态，此时跳变一次是合理反馈。
_PREVIEW_LOADING_HINT_DELAY = 400
_DECODE_TEMP_DIR = None

# 缩略图像素缓存（path, px) -> QImage 与悬停信息缓存 path -> str 都可能在
# 子线程（缩略图线程池 / 异步预览 worker）与 GUI 线程并发访问，各配一把锁。
_THUMB_IMG_LOCK = threading.Lock()      # 保护主窗口的 _thumb_cache（存 QImage）
_INFO_CACHE_LOCK = threading.Lock()     # 保护主窗口的 _info_cache

# 懒加载的像素尺寸缓存，供缩略图 / 悬停信息 / 转换调度分类复用。
# 模块级 + 锁：双队列调度器可能在子线程（ConvertWorker）内调用，需线程安全。
_DIMS_CACHE = {}             # path -> (width, height)，读取失败记为 (0, 0)
_DIMS_CACHE_LOCK = threading.Lock()


def _decode_temp_dir():
    global _DECODE_TEMP_DIR
    if _DECODE_TEMP_DIR is None:
        _DECODE_TEMP_DIR = tempfile.mkdtemp(prefix="libjxl_gui_decode_")
        atexit.register(_decode_cleanup_temp_dir)
    return _DECODE_TEMP_DIR


def _decode_cleanup_temp_dir():
    global _DECODE_TEMP_DIR
    if _DECODE_TEMP_DIR is not None:
        shutil.rmtree(_DECODE_TEMP_DIR, ignore_errors=True)
        _DECODE_TEMP_DIR = None


def _decode_to_temp_file(path):
    """Decode *path* to a temporary file Qt can load, returning that path.

    JXL 解码为临时 PNG（djxl）；AVIF 解码为临时 PNG（Pillow）；
    PFM / PAM / PGX 由 :mod:`libjxl_gui.formats` 纯 Python 解码为临时 PPM。
    EXR 不在此处处理（只解析头部元数据，见 :func:`formats.parse_exr_header`）。
    返回临时文件路径表示成功，``None`` 表示解码失败。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".jxl":
        fd, png = tempfile.mkstemp(suffix=".png", dir=_decode_temp_dir())
        os.close(fd)
        ok, _msg = converter.decode(path, png)
        if ok and os.path.isfile(png) and os.path.getsize(png) > 0:
            return png
        try:
            os.remove(png)
        except OSError:
            pass
        return None
    if ext == ".avif":
        if not processor.AVAILABLE:
            return None
        try:
            from PIL import Image
            fd, png = tempfile.mkstemp(suffix=".png", dir=_decode_temp_dir())
            os.close(fd)
            img = Image.open(path)
            icc = img.info.get("icc_profile")
            if icc:
                img.save(png, "PNG", icc_profile=icc)
            else:
                img.save(png, "PNG")
            if os.path.isfile(png) and os.path.getsize(png) > 0:
                return png
            try:
                os.remove(png)
            except OSError:
                pass
        except Exception:
            pass
        return None
    if ext in (".pfm", ".pam", ".pgx"):
        try:
            if ext == ".pfm":
                ppm = formats.pfm_to_ppm_bytes(path)
            elif ext == ".pam":
                ppm = formats.pam_to_ppm_bytes(path)
            else:
                ppm = formats.pgx_to_ppm_bytes(path)
            fd, tmp = tempfile.mkstemp(suffix=".ppm", dir=_decode_temp_dir())
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(ppm)
            if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                return tmp
            try:
                os.remove(tmp)
            except OSError:
                pass
        except Exception:
            pass
        return None
    return None


def _display_path(path):
    """Return a path Qt can actually load for ``path``.

    For natively-supported formats this is ``path`` itself. For JXL/AVIF/PFM/
    PAM/PGX it decodes to a cached temporary file (PNG / PPM) and returns that
    path, or ``None`` if decoding failed. EXR is intentionally NOT decoded to
    pixels — its header metadata is surfaced via :func:`formats.parse_exr_header`
    instead. Callers treat ``None`` as "cannot display".
    """
    if not path.lower().endswith(tuple(_DECODE_TO_TEMP_EXTS)):
        return path
    with _DECODE_TEMP_CACHE_LOCK:
        cached = _DECODE_TEMP_CACHE.get(path)
    if cached is not None and os.path.isfile(cached):
        return cached
    try:
        decoded = _decode_to_temp_file(path)
        if decoded is not None:
            with _DECODE_TEMP_CACHE_LOCK:
                _DECODE_TEMP_CACHE[path] = decoded
            return decoded
    except Exception:
        pass
    return None


# Backwards-compatible alias (older tests / callers).
_jxl_display_path = _display_path


def get_image_dims(path):
    """返回图像的 ``(width, height)`` 像素尺寸（**不阻塞** GUI 线程）。

    原生格式只读取文件头（O(1)，不解码像素数据）。JXL / AVIF / PFM / PAM / PGX
    等需要解码才能知道尺寸；本函数**不**在调用线程里同步跑解码器——若此前
    预览 / 缩略图已解码过（命中 ``_DECODE_TEMP_CACHE``），则直接读已解码文件
    头（仍是 O(1)）；否则返回 ``(0, 0)``，由上层（``MainWindow._ensure_dims``）
    异步解码后回填 ``_DIMS_CACHE`` 并刷新 UI。这样拖入大图时主线程绝不会被
    ``djxl`` 子进程阻塞。EXR 仅解析头部即可得尺寸，同样不解码像素。

    任何失败均返回 ``(0, 0)`` 而非抛异常，调用方把零值视为「未知 / 非图像」。
    """
    with _DIMS_CACHE_LOCK:
        cached = _DIMS_CACHE.get(path)
    if cached is not None:
        return cached
    w = h = 0
    # 1) 原生格式：只读文件头，不解码像素。
    reader = QImageReader(path)
    if reader.canRead():
        size = reader.size()
        if size.isValid() and not size.isNull():
            w, h = size.width(), size.height()
    # 2) 需解码格式：先看是否已有解码好的临时文件（预览 / 缩略图可能已解过），
    #    有则直接读其头，仍然是 O(1) 不阻塞。
    if not (w and h):
        with _DECODE_TEMP_CACHE_LOCK:
            disp = _DECODE_TEMP_CACHE.get(path)
        if disp is not None and os.path.isfile(disp):
            r2 = QImageReader(disp)
            if r2.canRead():
                s2 = r2.size()
                if s2.isValid() and not s2.isNull():
                    w, h = s2.width(), s2.height()
    # 3) EXR 等只解析头部即得尺寸信息。
    if not (w and h):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".exr":
            try:
                meta = formats.parse_exr_header(path)
                w = meta.get("width") or 0
                h = meta.get("height") or 0
            except Exception:
                pass
    result = (w, h)
    with _DIMS_CACHE_LOCK:
        _DIMS_CACHE[path] = result
    return result


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif",
    ".tiff", ".webp", ".ppm", ".pgm", ".jxl", ".avif",
    ".pfm", ".pam", ".pgx", ".exr",
}

# Extensions that denote a real JPEG bitstream. cjxl's --lossless_jpeg=1 can
# only re-encode an actual JPEG, so the JPG 无损重编码 mode must skip others.
JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".jfif"}


# ----------------------------------------------------------------------
# 双队列调度：按像素数把文件分类为大图 / 小图（见 docs/design-decisions.md）。
#
# 小图走并行池（每图均分一部分核心）；大图独占全部核心、逐个串行处理，
# 这样单张大图不会让其余 CPU 闲置、把整批拖慢。动态线程分配（按存活文件数
# 重分配核心）作为「搁置」方案记录在 docs/design-decisions.md。
# ----------------------------------------------------------------------
# 大图判定的相对阈值：文件像素数超过本批次中位数的该倍数即视为离群「大图」。
BIG_IMAGE_RATIO = 2.5


def estimate_floor_px(cores):
    """未校准时的保守启发式：speedup 达到 BIG_IMAGE_TARGET_SPEEDUP 的近似分辨率
    随核心数下降。低核（≤4）机器直接禁用大图专属判定（返回极大值），因为此时
    并行单线程本就是最优、独占满核收益不足。

    ``60.0 / cores`` 兆像素是经验线性近似（20 核→3MP、8 核→8MP、64 核→1MP，
    与实测 speedup 曲线同趋势）；校准脚本会写入更精确的值覆盖它。
    """
    if cores <= 4:
        return 10 ** 18
    mp = max(1, int(round(60.0 / cores)))
    return mp * 1000 * 1000


def read_big_image_floor_px(cores, scheme=None, ac=None, mode=None):
    """从 QSettings 读取已校准的大图像素地板；缺省时回退启发式估计值。

    scheme/ac/mode 给定时优先取该电源状态专属阈值（切换计划/插拔电/电源模式后
    自动套用已记录值）；任一为 None 时按当前实际电源状态自动补全。读取遵循
    「精确复合键 → 计划键 → 通用键」回退链（见 calibrate.read_stored_floor_px）。
    无记录则回退 estimate_floor_px(cores)。
    """
    cores = cores or (os.cpu_count() or 1)
    if scheme is None or ac is None or mode is None:
        try:
            cur_scheme, cur_ac, cur_mode = power.get_power_state()
        except Exception:
            cur_scheme = cur_ac = cur_mode = None
        if scheme is None:
            scheme = cur_scheme
        if ac is None:
            ac = cur_ac
        if mode is None:
            mode = cur_mode
    stored = calibrate.read_stored_floor_px(scheme=scheme, ac=ac, mode=mode)
    if stored is not None:
        return stored
    return estimate_floor_px(cores)


def _is_jpeg(path):
    """Whether *path* is a JPEG file, judged by its extension (case-insensitive)."""
    return os.path.splitext(path)[1].lower() in JPEG_EXTENSIONS


def _jpeg_recon_action(src, hard_skip):
    """对单个 JXL 输入，按 JPEG 输出可行性返回拦截动作（供 _on_convert 调用）。

    返回 ``'kept'``（可重建，正常转换）/ ``'skip'``（A 模式：直接跳过）/
    ``'confirm'``（B 模式：留入 jobs，待弹一次确认后以解码重编码方式输出）。
    调用方已先判定 ``out_fmt == "jpg"`` 且 ``not custom_cmd`` 且输入为 ``.jxl``，
    此处只负责单文件的可行性分支。
    """
    recon = converter.is_lossless_jpeg_jxl(src)
    if recon is not False:
        return "kept"
    return "skip" if hard_skip else "confirm"


def _move_to_recycle_bin(path):
    """将文件或目录移入系统回收站（**非永久删除**）。

    使用 ``send2trash`` 库（Windows 上走 ``IFileOperation``），是业界标准的跨平台
    「移到回收站」实现；其设计保证「无法移入回收站时抛异常，绝不回退为永久删除」，
    因此调用方可以安全地把异常当作「保留原文件」处理。本函数本身不捕获异常。
    """
    if send2trash is None:
        raise RuntimeError(
            i18n.t("未找到 send2trash 库，无法将文件移入回收站；请先安装：pip install send2trash")
        )
    send2trash.send2trash(path)


def _preserve_mtime(src, dst):
    """将 dst 的修改时间（及访问时间）设为与原文件 src 一致（跨平台 os.utime）。

    使用纳秒精度，避免大文件或接近的源/目标时间被截断为整数秒。
    失败抛异常，由调用方决定是否忽略（不计入转换失败）。
    """
    st = os.stat(src)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))


def _preserve_ctime(src, dst):
    """将 dst 的创建时间设为与原文件 src 一致（仅 Windows；需 pywin32）。

    非 Windows 平台没有「创建时间」这一独立属性，直接返回（此选项在该平台无意义）。
    os.utime 无法修改创建时间，故 Windows 下必须用 win32file.SetFileTime。
    缺少 pywin32 时抛 RuntimeError，由调用方 try/except 容错（转换结果不受影响）。
    """
    import sys
    if sys.platform != "win32":
        return
    st = os.stat(src)
    try:
        import pywintypes
        import win32file
        import win32con
    except ImportError:
        raise RuntimeError(
            i18n.t("未安装 pywin32，无法保持创建时间；请先安装：pip install pywin32")
        )
    # 部分 pywin32 构建未在 win32con/win32file 暴露 FILE_WRITE_ATTRIBUTES
    # （访问掩码 0x100，仅需写属性的最小权限，无需写文件数据）。缺失时回退字面量，
    # 避免 AttributeError 导致「保持时间戳失败」频繁误报。
    FILE_WRITE_ATTRIBUTES = getattr(win32con, "FILE_WRITE_ATTRIBUTES", 0x100)
    ctime = pywintypes.Time(st.st_ctime)
    handle = win32file.CreateFile(
        dst,
        FILE_WRITE_ATTRIBUTES,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE
        | win32file.FILE_SHARE_DELETE,
        None,
        win32file.OPEN_EXISTING,
        win32file.FILE_ATTRIBUTE_NORMAL,
        None,
    )
    try:
        win32file.SetFileTime(handle, ctime, None, None)
    finally:
        handle.Close()


# ----------------------------------------------------------------------
# Logging helpers for the 状态 (status) tab conversion report
# ----------------------------------------------------------------------
# 状态页日志分隔线：每次启动转换时输出在本批内容之前，用于区分不同批次。
_LOG_SEPARATOR = "─" * 72


def _format_datetime(timestamp):
    """Format a Unix timestamp as 'YYYY/M/D HH:MM' (no leading zeroes)."""
    lt = time.localtime(timestamp)
    return "%d/%d/%d %d:%02d" % (
        lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min
    )


def _format_bytes(num_bytes):
    """Human-readable byte size, e.g. '78.95 KB'. Always keeps two decimals."""
    n = float(num_bytes)
    if n < 1024.0:
        return "%.2f" % n + " B"
    if n < 1024.0 * 1024.0:
        return "%.2f" % (n / 1024.0) + " KB"
    if n < 1024.0 * 1024.0 * 1024.0:
        return "%.2f" % (n / (1024.0 * 1024.0)) + " MB"
    return "%.2f" % (n / (1024.0 * 1024.0 * 1024.0)) + " GB"


def _format_duration(seconds):
    """Format a duration in seconds as a short Chinese string.

    e.g. '12 秒' / '1 分 5 秒' / '2 时 3 分'. Invalid input returns '--'.
    """
    if seconds is None or seconds < 0:
        return "--"
    seconds = int(round(seconds))
    if seconds < 60:
        return i18n.t("%d 秒") % seconds
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return i18n.t("%d 分 %d 秒") % (minutes, secs)
    hours = minutes // 60
    mins = minutes % 60
    return i18n.t("%d 时 %d 分") % (hours, mins)


def _safe_getsize(path):
    """Return the file size in bytes, or 0 if it cannot be read."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _format_size_change(in_bytes, out_bytes, tag=""):
    """Indented 'in -> out (±pct%)' summary line for a single file.

    ``tag`` is an optional bracketed encoding description (e.g.
    '[Modular, lossless]') showing how the file was encoded. The leading tab
    keeps the line indented under its '>>> [n/m]' header; the tag is separated
    by a fixed run of spaces (not a tab) so the gap stays uniform instead of
    jumping to the next tab stop with varying byte-size widths.
    """
    if in_bytes > 0:
        pct = (out_bytes - in_bytes) / in_bytes * 100.0
        pct_str = "%+d%%" % round(pct)
    else:
        pct_str = "--"
    line = "\t%s -> %s (%s)" % (
        _format_bytes(in_bytes), _format_bytes(out_bytes), pct_str
    )
    if tag:
        line += "        " + tag
    return line


VIEW_MODES = ["小缩略图", "缩略图", "大缩略图", "列表", "详细信息"]

# Icon (thumbnail) size per view mode.
THUMB_SIZES = {
    "小缩略图": QSize(56, 56),
    "缩略图": QSize(96, 96),
    "大缩略图": QSize(160, 160),
}

# Fixed grid cell per view mode so every image sits in a constant-ratio box
# aligned to a grid; the text area below is fixed and the name is elided, so
# the box ratio never changes with the file name length.
GRID_SIZES = {
    "小缩略图": QSize(74, 94),
    "缩略图": QSize(122, 154),
    "大缩略图": QSize(196, 238),
}

# Details-view ("详细信息") columns, Explorer-style. Each entry is:
#   (key, label, default_visible, default_width_px, alignment)
# The order here is the DEFAULT display order. The user can hide columns,
# reorder them by dragging the header, and resize them; the live configuration
# is tracked on the MainWindow (self._table_hidden / _table_widths / _table_sort).
# The header is built ONCE; afterwards Qt owns the live visual column order, so
# it survives view switches and reorders without any logical-index renumbering.
TABLE_COLUMNS = [
    ("name", "文件名", True, 200, Qt.AlignLeft | Qt.AlignVCenter),
    ("format", "格式", True, 64, Qt.AlignLeft | Qt.AlignVCenter),
    ("size", "大小", True, 92, Qt.AlignRight | Qt.AlignVCenter),
    ("modified", "修改日期", True, 140, Qt.AlignLeft | Qt.AlignVCenter),
    ("created", "创建日期", True, 140, Qt.AlignLeft | Qt.AlignVCenter),
    ("operated", "操作日期", False, 140, Qt.AlignLeft | Qt.AlignVCenter),
    ("resolution", "分辨率", True, 110, Qt.AlignLeft | Qt.AlignVCenter),
    ("ratio", "比率", True, 70, Qt.AlignLeft | Qt.AlignVCenter),
    ("path", "路径", True, 240, Qt.AlignLeft | Qt.AlignVCenter),
]
TABLE_COL_DEFAULTS = {
    key: (label, visible, width, align)
    for (key, label, visible, width, align) in TABLE_COLUMNS
}


DROP_COLOR = QColor(60, 140, 255)        # blue insertion line
WHEEL_SPEED = 4                          # thumbnail-view wheel scroll multiplier
THUMB_PAD = 8                            # safe-area margin inside each cell so
                                         # thumbnails never touch their neighbors
FIT_EXTRA_W = 6                          # extra viewport width (px) added when
                                         # fitting the 6x3 grid, so the 6th column
                                         # is fully visible despite DPI/scrollbar
                                         # rounding (prevents "looks like 5 columns")
FIT_EXTRA_H = 21                         # extra viewport height (px) added when
                                         # fitting the 6x3 grid, so the output page
                                         # (the tallest tab) shows in full without
                                         # clipping; even 1px less and it clips.
                                         # Baseline measured under 原生（无闪烁） / Fusion.
FIT_EXTRA_H_NATIVE = 6                   # native-theme bonus for FIT_EXTRA_H (px). Under the
                                         # pure "native" theme (not 原生（无闪烁）) the output
                                         # page's GroupBox/checkbox controls render taller
                                         # (Windows native drawing), so 6 more px of viewport
                                         # height are needed to avoid a scrollbar on 一键 6×3.
ROOT_MARGIN_LTR = 9                      # root layout left / top / right contents
                                         # margin (px); identical across themes.
ROOT_MARGIN_BOTTOM_NATIVE = 2            # root layout bottom contents margin (px)
                                         # under the native / 原生（无闪烁） themes.
ROOT_MARGIN_BOTTOM_FUSION = 3            # root layout bottom contents margin (px)
                                         # under Fusion; 1px larger than native to
                                         # compensate for Fusion's tighter frame.


def _root_bottom_margin():
    """Root layout bottom contents margin (px).

    Fusion draws its frame ~1px tighter than the native themes, so under Fusion
    the bottom margin is 1px larger to keep the same visual gap above the bottom
    button bar. The native themes (原生 / 原生（无闪烁）) use the smaller value.
    """
    return ROOT_MARGIN_BOTTOM_FUSION if _APP_THEME == "fusion" else ROOT_MARGIN_BOTTOM_NATIVE


def _fit_extra_h():
    """Extra viewport height (px) for the 6x3 fit, theme-dependent.

    Baseline FIT_EXTRA_H (21) is the clearance measured under 原生（无闪烁） /
    Fusion. Under the pure "native" theme, Windows-native control drawing makes
    the output page (the tallest tab) render ~6px taller, so we add
    FIT_EXTRA_H_NATIVE (6) to keep 一键 6×3 from popping a scrollbar.
    """
    return FIT_EXTRA_H + FIT_EXTRA_H_NATIVE if _APP_THEME == "native" else FIT_EXTRA_H


THUMB_BATCH = 8                           # thumbnails decoded per timer tick when
                                         # (re)building the icon view, so the first
                                         # switch to a thumbnail mode stays responsive
                                         # instead of blocking on a synchronous
                                         # decode of every image
THUMB_PLACEHOLDER_BG = QColor(40, 40, 40) # thumbnail placeholder / letterbox fill
# Qt's built-in edge auto-scroll ("drag a selection past the page edge") is driven
# by an internal timer whose interval we could NOT change reliably across Qt builds
# (it is not QApplication.keyboardInputInterval() on Qt 6.11 -> the page just jumps
# ~6x/sec instead of gliding). So we disable Qt's auto-scroll and run our own
# high-frequency, interpolation-based auto-scroll that glides smoothly.
AUTO_SCROLL_INTERVAL = 16    # ms between auto-scroll ticks (~60 fps)
AUTO_SCROLL_MARGIN = 28      # px from top/bottom edge that starts auto-scrolling
AUTO_SCROLL_MAX_SPEED = 18   # px scrolled per tick at the very edge (smooth glide)


def _event_pos(event):
    """Return the event position as a QPoint (works for Qt6 position())."""
    pos = getattr(event, "position", None)
    if callable(pos):
        return pos().toPoint()
    return event.pos()


class InputListWidget(QListWidget):
    """Input list with thumbnail drag-sort (blue seam line) or list drag-select.

    In IconMode the widget reorders items by internal drag & drop. A blue
    insertion line is drawn in the seam to the left of the target cell. The
    reorder is computed manually from the selected rows and the insertion index
    so nothing is ever lost.

    In ListMode (set by the main window) drag & drop is disabled and dragging
    simply rubber-band selects items, so the names stay packed and selectable.

    External file drops (URLs) are always ignored here so the event propagates
    to the parent MainWindow, which appends the dropped files.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setDropIndicatorShown(False)   # we draw our own blue seam line
        # Snap lets the items be drag-reordered; Static would block the drag
        # from even starting in IconMode. Adjust makes the grid reflow when the
        # window is resized instead of staying fixed at N columns.
        self.setMovement(QListWidget.Snap)
        self.setResizeMode(QListWidget.Adjust)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSpacing(0)
        self.drop_index = -1   # insertion index (0..count); -1 = no indicator
        self._reordering = False  # True only while a real drag-reorder (QDrag)
                                  # is in progress; gates the blue seam line so
                                  # it never shows during rubber-band selection.
        # Let Qt bitblt the existing content when the view scrolls (rubber-band
        # auto-scroll, wheel, scrollbar) and only repaint the newly exposed
        # strip instead of clearing + repainting the whole viewport every frame.
        # This is the single biggest win for smooth edge auto-scroll with many
        # thumbnails; our delegate paints every cell opaque so there are no
        # leftover artifacts after the blit.
        vp = self.viewport()
        if vp is not None:
            vp.setAttribute(Qt.WA_StaticContents, True)
        # Replace Qt's built-in edge auto-scroll (which jumps ~6x/sec on this Qt
        # build) with our own smooth, interpolation-based scroller.
        self.setAutoScroll(False)
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(AUTO_SCROLL_INTERVAL)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)
        self._auto_scroll_speed = 0.0  # px per tick (signed); 0 == idle

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.selectedIndexes():
                self.main_window._on_remove_selected()
                event.accept()
                return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        # While a rubber-band selection or drag-reorder is in progress, watch the
        # cursor distance to the top/bottom edge and drive our smooth auto-scroll
        # (Qt's built-in one jumps ~6x/sec on this Qt build).
        self._update_auto_scroll_target(_event_pos(event))

    def _update_auto_scroll_target(self, pos):
        """Set the auto-scroll speed from how close the cursor is to a viewport
        edge, and start/stop the smooth auto-scroll timer accordingly.

        The cursor does NOT have to stay inside the viewport: while a drag /
        selection is in progress Qt keeps delivering mouse-move events even when
        the pointer leaves the widget, so we also drive full-speed scrolling
        when the cursor is *outside* the viewport (top or bottom). Otherwise the
        auto-scroll would die the instant the cursor pushed past the edge -- the
        exact moment the user wants it to keep going.
        """
        vp = self.viewport()
        h = vp.height()
        y = pos.y()
        speed = 0.0
        if y < 0:
            # pointer left the viewport above the top edge -> keep scrolling up
            speed = -AUTO_SCROLL_MAX_SPEED
        elif y < AUTO_SCROLL_MARGIN:
            t = (AUTO_SCROLL_MARGIN - y) / AUTO_SCROLL_MARGIN
            speed = -AUTO_SCROLL_MAX_SPEED * t
        elif y > h:
            # pointer left the viewport below the bottom edge -> keep scrolling
            # down (this is where selection-by-drag wants to keep extending)
            speed = AUTO_SCROLL_MAX_SPEED
        elif y > h - AUTO_SCROLL_MARGIN:
            t = (y - (h - AUTO_SCROLL_MARGIN)) / AUTO_SCROLL_MARGIN
            speed = AUTO_SCROLL_MAX_SPEED * t
        self._auto_scroll_speed = speed
        if speed != 0 and not self._auto_scroll_timer.isActive():
            self._auto_scroll_timer.start()
        elif speed == 0 and self._auto_scroll_timer.isActive():
            self._auto_scroll_timer.stop()

    def _auto_scroll_tick(self):
        if self._auto_scroll_speed == 0:
            self._auto_scroll_timer.stop()
            return
        bar = self.verticalScrollBar()
        new = bar.value() + self._auto_scroll_speed
        new = max(bar.minimum(), min(new, bar.maximum()))
        if new == bar.value():
            return  # already at the boundary
        bar.setValue(new)
        # Keep the selection / drop indicator in sync with the scrolled content.
        # Re-run Qt's rubber-band selection with the unchanged cursor so items
        # scrolled under the band get (de)selected smoothly.
        local = self.viewport().mapFromGlobal(QCursor.pos())
        ev = QMouseEvent(QEvent.Type.MouseMove, local,
                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.mouseMoveEvent(ev)
        # Drag-reorder only: refresh the blue seam line against the scrolled
        # grid. Gated by _reordering so it never fires during a rubber-band
        # selection (which would draw a spurious seam at the cursor).
        if self._reordering:
            new_idx = self._insertion_index(local)
            if new_idx != self.drop_index:
                old = self.drop_index
                self.drop_index = new_idx
                self._update_drop_indicator(old)

    def mousePressEvent(self, event):
        # Pause thumbnail decoding while the user interacts (rubber-band
        # selection or drag-reorder): decoding on the main thread would make
        # the auto-scroll stutter badly at the page edges. Resumed on release.
        self.main_window._pause_thumb_batch()
        self._auto_scroll_timer.stop()
        self._auto_scroll_speed = 0.0
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._auto_scroll_timer.stop()
        self._auto_scroll_speed = 0.0
        # Clear any stray blue reorder seam (it must never persist after the
        # button is released, regardless of which interaction just ended).
        if self.drop_index != -1:
            old = self.drop_index
            self.drop_index = -1
            self._update_drop_indicator(old)
        super().mouseReleaseEvent(event)
        self.main_window._resume_thumb_batch()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()          # external file -> let MainWindow handle it
        else:
            event.accept()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()
            return
        if self.dragDropMode() == QAbstractItemView.NoDragDrop:
            event.ignore()          # list mode: dragging selects, not reorders
            return
        event.setDropAction(Qt.MoveAction)
        event.accept()              # accept anywhere -> whole viewport is a drop zone
        self._reordering = True     # a real drag-reorder is in progress
        new = self._insertion_index(_event_pos(event))
        if new != self.drop_index:
            old = self.drop_index
            self.drop_index = new
            self._update_drop_indicator(old)

    def dragLeaveEvent(self, event):
        self._reordering = False
        self._update_drop_indicator(self.drop_index)
        self.drop_index = -1
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()          # external file -> let MainWindow handle it
            return
        if self.dragDropMode() == QAbstractItemView.NoDragDrop:
            event.ignore()
            return
        paths = list(self.main_window.input_files)
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        rows = [r for r in rows if 0 <= r < len(paths)]
        if rows:
            target = max(0, min(self.drop_index, len(paths)))
            new_order = self.main_window._reorder_paths(paths, rows, target)
            self.main_window.input_files = new_order
            self._update_drop_indicator(self.drop_index)
            self.drop_index = -1
            self._reordering = False
            self.main_window._refresh_list()
            self.main_window._refresh_table()
            self.main_window.statusBar().showMessage(
                i18n.t("已调整顺序，共 %d 个文件") % len(new_order)
            )
            # Safety net: a drag-reorder may swallow mouseReleaseEvent, so make
            # sure thumbnail decoding restarts (and auto-scroll is stopped) here
            # too.
            self.main_window._resume_thumb_batch()
            self._auto_scroll_timer.stop()
            self._auto_scroll_speed = 0.0
            event.accept()
            return
        event.ignore()

    def _insertion_index(self, pos):
        """Insertion seam index (0..count) for the thumbnail (IconMode) grid.

        The grid flows left-to-right and wraps by row, so "before/after" is
        decided by the cursor's horizontal position relative to the hovered
        cell's center -- NOT vertically (the old vertical rule drifted into the
        wrong row near row boundaries, making the seam at the *start* of a row
        unreachable). This lets you drop an item as the first cell of any row by
        hovering the left half of that row's first cell.
        """
        count = self.count()
        if count == 0:
            return 0
        idx = self.indexAt(pos)
        if not idx.isValid():
            # Off the grid: above the first row -> front; below/right -> end.
            first = self.visualRect(self.model().index(0, 0))
            return 0 if pos.y() < first.y() else count
        r = idx.row()
        rect = self.visualRect(idx)
        return r if pos.x() < rect.center().x() else r + 1

    def paintEvent(self, event):
        t0 = None
        if getattr(self.main_window, "_perf_enabled", False):
            t0 = time.perf_counter()
        super().paintEvent(event)
        self._draw_drop_indicator()
        if t0 is not None:
            self.main_window._perf_record_paint(
                (time.perf_counter() - t0) * 1000.0, self.state()
            )

    def _indicator_rect(self, index):
        """Bounding rect of the blue seam line for insertion `index`."""
        count = self.count()
        if index < 0 or count == 0:
            return None
        if self.viewMode() == QListWidget.IconMode:
            if index < count:
                rect = self.visualRect(self.model().index(index, 0))
                x = rect.x()
            else:
                rect = self.visualRect(self.model().index(count - 1, 0))
                x = rect.x() + rect.width()
            return QRect(x - 2, rect.y(), 4, rect.height())
        else:
            if index < count:
                rect = self.visualRect(self.model().index(index, 0))
                y = rect.y()
            else:
                rect = self.visualRect(self.model().index(count - 1, 0))
                y = rect.y() + rect.height()
            return QRect(rect.x(), y - 2, rect.width(), 4)

    def _update_drop_indicator(self, old_index):
        """Repaint only the indicator's seam (old + new) so dragging many items
        stays smooth instead of repainting the whole viewport every move."""
        for idx in (old_index, self.drop_index):
            r = self._indicator_rect(idx)
            if r is not None:
                self.viewport().update(r.adjusted(-2, -2, 2, 2))

    def _draw_drop_indicator(self):
        if self.drop_index < 0 or self.dragDropMode() == QAbstractItemView.NoDragDrop:
            return
        r = self._indicator_rect(self.drop_index)
        if r is None:
            return
        painter = QPainter(self.viewport())
        painter.fillRect(r, DROP_COLOR)
        painter.end()

    def wheelEvent(self, event):
        # Speed up vertical scrolling in the thumbnail (IconMode) view; the
        # default wheel step is too small. List mode keeps the native behavior.
        if self.viewMode() == QListWidget.IconMode and isinstance(event, QWheelEvent):
            delta = event.angleDelta().y()
            if delta != 0:
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta * WHEEL_SPEED
                )
                event.accept()
                return
        super().wheelEvent(event)


class InputTableHeader(QHeaderView):
    """Horizontal header whose right-click opens the column menu.

    Overriding contextMenuEvent (rather than relying on the
    customContextMenuRequested signal) is the canonical, reliable way to pop a
    context menu on a QHeaderView -- the signal approach intermittently fails to
    fire on some Qt builds, which is why right-clicking the header did nothing.
    """

    def __init__(self, table, main_window):
        super().__init__(Qt.Horizontal, table)
        self.main_window = main_window

    def contextMenuEvent(self, event):
        self.main_window._on_table_header_context_menu(event.globalPos())
        event.accept()


class InputTableWidget(QTableWidget):
    """Details table that reorders whole rows by internal drag & drop.

    Each cell stores the file path in its UserRole so the order survives the
    move. The reorder is computed manually from the selected rows and the
    insertion index, which avoids Qt's internal-move quirk of dropping a row
    (and therefore a file) when only part of the row is selected.

    A blue insertion line is drawn in the seam above the target row.
    """

    def __init__(self, main_window):
        super().__init__(0, 0)
        self.main_window = main_window
        # Custom header so right-click reliably opens the column menu.
        header = InputTableHeader(self, main_window)
        self.setHorizontalHeader(header)
        # Explorer-style header: resizable + movable columns, clickable to sort
        # (custom sort handled by MainWindow), and a right-click menu to toggle
        # which columns are visible.
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSortIndicatorShown(True)
        header.setHighlightSections(False)
        header.sectionClicked.connect(self.main_window._on_table_header_clicked)
        header.sectionResized.connect(self.main_window._on_table_section_resized)
        self.setTextElideMode(Qt.ElideRight)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDropIndicatorShown(False)
        self.drop_index = -1

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()
        else:
            event.accept()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()
            return
        event.setDropAction(Qt.MoveAction)
        event.accept()
        new = self._insertion_index(_event_pos(event))
        if new != self.drop_index:
            old = self.drop_index
            self.drop_index = new
            self._update_drop_indicator(old)

    def dragLeaveEvent(self, event):
        self._update_drop_indicator(self.drop_index)
        self.drop_index = -1
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()
            return
        # Reorder based on the CURRENT DISPLAYED order, not on input_files
        # directly. Each cell stores its path in UserRole, so reading the
        # visible rows gives exactly what the user sees and drags -- correct
        # even when a sort is active (the old code used input_files, which
        # diverged from the displayed order once sorted and mis-placed files).
        displayed = []
        for r in range(self.rowCount()):
            it = self.item(r, 0)
            displayed.append(it.data(Qt.UserRole) if it is not None else None)
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        rows = [r for r in rows if 0 <= r < len(displayed)]
        if rows:
            target = max(0, min(self.drop_index, len(displayed)))
            new_order = self.main_window._reorder_paths(displayed, rows, target)
            self.main_window.input_files = new_order
            # A manual row reorder cancels any active sort: the rearranged order
            # is now the explicit custom order, so the header arrow clears.
            self.main_window._table_sort = None
            self._update_drop_indicator(self.drop_index)
            self.drop_index = -1
            self.main_window._refresh_table()
            self.main_window._refresh_list()
            self.main_window._save_table_layout()
            self.main_window.statusBar().showMessage(
                i18n.t("已调整顺序，共 %d 个文件") % len(new_order)
            )
            event.accept()
            return
        event.ignore()

    def _insertion_index(self, pos):
        n = self.rowCount()
        if n == 0:
            return 0
        idx = self.indexAt(pos)
        if not idx.isValid():
            first = self.visualRect(self.model().index(0, 0))
            return 0 if pos.y() < first.y() else n
        r = idx.row()
        rect = self.visualRect(idx)
        # Single-column vertical list: insert before the hovered row when the
        # cursor is above its vertical midpoint, after it otherwise.
        return r if pos.y() < rect.center().y() else r + 1

    def paintEvent(self, event):
        super().paintEvent(event)
        self._draw_drop_indicator()

    def _indicator_rect(self, index):
        n = self.rowCount()
        if index < 0 or n == 0:
            return None
        if index < n:
            rect = self.visualRect(self.model().index(index, 0))
            y = rect.y()
        else:
            rect = self.visualRect(self.model().index(n - 1, 0))
            y = rect.y() + rect.height()
        return QRect(rect.x(), y - 2, rect.width(), 4)

    def _update_drop_indicator(self, old_index):
        for idx in (old_index, self.drop_index):
            r = self._indicator_rect(idx)
            if r is not None:
                self.viewport().update(r.adjusted(-2, -2, 2, 2))

    def _draw_drop_indicator(self):
        r = self._indicator_rect(self.drop_index)
        if r is None:
            return
        painter = QPainter(self.viewport())
        painter.fillRect(r, DROP_COLOR)
        painter.end()


class PreviewScroll(QGraphicsView):
    """Image preview viewport: wheel zooms and left-drag pans.

    Zoom is applied as a matrix transform on the view, NOT by re-rasterizing a
    scaled QPixmap. The previous QLabel + ``scaled()`` approach rebuilt a
    multi-thousand-pixel pixmap on every wheel tick, which stuttered on large
    images; a transform-based zoom keeps any zoom level smooth because the
    compositor handles the scaling. Left-drag panning is provided by Qt's
    built-in ScrollHandDrag mode.
    """

    def __init__(self, parent_dialog):
        super().__init__(parent_dialog)
        self.dialog = parent_dialog
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setCursor(Qt.OpenHandCursor)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = None
        self._fit_on_show = True

    def set_pixmap(self, pixmap):
        self._scene.clear()
        if pixmap.isNull():
            self._pixmap_item = None
            return
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self._pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._pixmap_item)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())

    def zoom(self, factor):
        cur = self.transform().m11()
        if cur * factor < 0.02 or cur * factor > 64:
            return
        self.scale(factor, factor)

    def zoom_to_actual(self):
        """Reset zoom to 100% (1 image pixel == 1 device pixel)."""
        cur = self.transform().m11()
        if cur > 0:
            self.zoom(1.0 / cur)

    def fit(self):
        if self._pixmap_item is None or not self.isVisible():
            return
        vp = self.viewport()
        if vp.width() <= 0 or vp.height() <= 0:
            # 几何尚未就绪（刚 setVisible / 兄弟控件显隐导致布局未落定），
            # 推迟到下一事件循环，拿到真实 viewport 尺寸再 fit，避免缩得过小。
            retries = getattr(self, "_fit_retries", 0)
            if retries < 8:
                self._fit_retries = retries + 1
                QTimer.singleShot(0, self.fit)
            return
        self._fit_retries = 0
        # 关键：临时关闭滚动条，使 viewport 尺寸不受「当前缩放态滚动条」干扰。
        # 否则放大时滚动条占宽/高，viewport 偏小，fit 基于偏小 viewport 缩得
        # 「比窗口小一点点」且需再点一次才对。关掉后 viewport 即最终（最大）尺寸，
        # 单次 resetTransform + scale 精确贴合，无需 processEvents（避免闪烁）。
        # setScrollBarPolicy 同步触发 updateGeometries 重算 viewport 几何，
        # 紧接其后的 viewport().rect() 已是「无滚动条」尺寸，不依赖事件循环，
        # 故不会在中途 paint 出中间态（无闪烁）。
        h_old = self.horizontalScrollBarPolicy()
        v_old = self.verticalScrollBarPolicy()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.resetTransform()
        rect = self.viewport().rect()
        if rect.width() > 0 and rect.height() > 0:
            pix = self._pixmap_item.pixmap()
            pw = pix.width()
            ph = pix.height()
            if pw > 0 and ph > 0:
                # 直接按 viewport rect 算缩放（KeepAspectRatio 取 min 维度），
                # 用 scale() 应用——比 fitInView 少一层隐式边距，图片精确贴合窗口。
                s = min(rect.width() / pw, rect.height() / ph)
                self.scale(s, s)
        # 恢复滚动条策略：fit 后图 <= 无滚动条 viewport，不会触发滚动条，无副作用。
        self.setHorizontalScrollBarPolicy(h_old)
        self.setVerticalScrollBarPolicy(v_old)
        self._fit_on_show = False

    def wheelEvent(self, event):
        # Zoom around the cursor; never scroll the view.
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom(factor)
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if self._fit_on_show:
            # 延迟到下一事件循环再 fit：setVisible 触发的布局重算（resize）是异步
            # 的，立即 fit 会拿到 0/旧的 viewport 尺寸，把图片缩得过小。
            QTimer.singleShot(0, self.fit)


class _PreviewLoader(QThread):
    """在 GUI 线程之外解码预览图，避免双击预览大图时主线程卡顿。

    之前 ``PreviewDialog.__init__`` 直接在 GUI 线程里 ``_display_path``（JXL 走
    djxl 子进程、AVIF 走 Pillow，皆为同步阻塞）+ ``QPixmap(path)``（整图全分辨率
    解码），大图会冻结事件循环，表现为「顿一下 / 短暂无响应」。

    本 worker 把解码放到后台线程，并向 GUI 线程回传 ``QImage``（**不是**
    ``QPixmap``——QPixmap 必须留在 GUI 线程，跨线程创建会崩溃）。解码分辨率按
    预览可视区（窗口尺寸 × DPR）上限钳制：预览本就 fit-to-window，解 8k 原图纯属
    浪费算力与内存，按显示尺寸解码即可。
    """

    loaded = Signal(QImage)
    failed = Signal(str)

    def __init__(self, path, max_w, max_h):
        super().__init__()
        self.path = path
        self.max_w = max(1, int(max_w))
        self.max_h = max(1, int(max_h))

    def run(self):
        try:
            display = _display_path(self.path)
            if display is None:
                self.failed.emit(self.path)
                return
            reader = QImageReader(display)
            size = reader.size()
            w = size.width() if size.isValid() else 0
            h = size.height() if size.isValid() else 0
            if w > 0 and h > 0 and (w > self.max_w or h > self.max_h):
                scale = min(self.max_w / w, self.max_h / h)
                reader.setScaledSize(
                    QSize(max(1, int(w * scale)), max(1, int(h * scale)))
                )
            img = reader.read()
            if img.isNull():
                self.failed.emit(self.path)
                return
            self.loaded.emit(img)
        except Exception:
            self.failed.emit(self.path)


class PreviewDialog(QDialog):
    """Centered image preview with wheel zoom and drag-to-pan."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self._closed = False
        self._loader = None      # 后台解码线程（异步预览时）
        self._center = None      # 当前中央控件（占位 / 预览 / 错误）
        self.scroll = None
        # EXR 是浮点 HDR，不渲染像素，只解析并展示头部元数据（纯头部读取，很快，
        # 保持同步即可）。其余格式改为异步加载：先放「加载中」占位，由 _PreviewLoader
        # 在子线程解码，完成后在 GUI 线程把 QImage 转 QPixmap 并替换占位。这样双击
        # 大图不再冻结事件循环（顿一下 / 短暂无响应）。
        is_exr = path.lower().endswith(".exr")

        name = os.path.basename(path)
        self.setWindowTitle(i18n.t("预览：%s") % name)

        # Do not grow larger than the parent (main) window.
        if parent is not None:
            pw, ph = parent.width(), parent.height()
            w = min(int(pw * 0.85), pw)
            h = min(int(ph * 0.85), ph)
            self.resize(w, h)
            self.move(
                parent.x() + (pw - w) // 2,
                parent.y() + (ph - h) // 2,
            )

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.zoom_in_button = QPushButton(i18n.t("放大 +"))
        self.zoom_out_button = QPushButton(i18n.t("缩小 -"))
        self.zoom_actual_button = QPushButton("1:1")
        self.fit_button = QPushButton(i18n.t("适应窗口"))
        self.close_button = QPushButton(i18n.t("关闭"))
        bar.addWidget(self.zoom_in_button)
        bar.addWidget(self.zoom_out_button)
        bar.addWidget(self.zoom_actual_button)
        bar.addWidget(self.fit_button)
        bar.addStretch(1)
        bar.addWidget(self.close_button)
        root.addLayout(bar)

        if is_exr:
            # EXR 是浮点 HDR 格式，不渲染像素；仅解析并展示头部元数据。
            try:
                meta = formats.parse_exr_header(path)
                msg = QLabel(formats.exr_metadata_text(meta))
            except Exception as exc:
                msg = QLabel(i18n.t("无法解析 EXR 头部：%s") % exc)
            msg.setAlignment(Qt.AlignTop)
            msg.setWordWrap(True)
            # 等宽字体更易读元数据
            font = msg.font()
            font.setFamily("Consolas, Menlo, monospace")
            msg.setFont(font)
            self._set_center(msg)
        else:
            loading = QLabel(i18n.t("正在加载预览…"))
            loading.setAlignment(Qt.AlignCenter)
            loading.setWordWrap(True)
            self._set_center(loading)
            self._start_loader(path)

        # 无渲染对象（EXR 元数据 / 加载失败）时隐藏缩放按钮。
        if self.scroll is None:
            self.zoom_in_button.hide()
            self.zoom_out_button.hide()
            self.zoom_actual_button.hide()
            self.fit_button.hide()

        self.zoom_in_button.clicked.connect(lambda: self._zoom(1.2))
        self.zoom_out_button.clicked.connect(lambda: self._zoom(1 / 1.2))
        self.zoom_actual_button.clicked.connect(self._zoom_actual)
        self.fit_button.clicked.connect(self._fit)
        self.close_button.clicked.connect(self.close)

    # ------------------------------------------------------------------
    # 异步预览加载
    # ------------------------------------------------------------------
    def _set_center(self, widget):
        """替换中央区域（工具栏之下）的控件，旧控件延迟销毁。"""
        if self._center is not None:
            self.layout().removeWidget(self._center)
            self._center.deleteLater()
        self._center = widget
        self.layout().addWidget(widget, stretch=1)

    def _start_loader(self, path):
        """启动后台解码线程；默认按原生分辨率解码，仅超大图做 OOM 保护。

        解码不再为 fit 而缩小：一般截图 / 照片（单边 ≤ ``_PREVIEW_OOM_CAP``）一律
        原生解码，fit 到窗口时永远是「从大到小缩」，清晰无糊。单边超过
        ``_PREVIEW_OOM_CAP`` 的超巨图（8K+ / 拼接图）才等比缩到此值以内，避免
        一次性解码吃光内存。
        """
        # 传入 OOM 保护上限作为唯一钳制：worker 仅在原图单边超过它时才缩放，
        # 否则按原生分辨率解码。
        max_w = max_h = _PREVIEW_OOM_CAP
        loader = _PreviewLoader(path, max_w, max_h)
        loader.loaded.connect(self._on_preview_loaded)
        loader.failed.connect(self._on_preview_failed)
        loader.finished.connect(loader.deleteLater)
        self._loader = loader
        loader.start()

    def _on_preview_loaded(self, img):
        if self._closed:
            return
        pix = QPixmap.fromImage(img)
        self.scroll = PreviewScroll(self)
        self.scroll.set_pixmap(pix)
        self._set_center(self.scroll)
        # 显式延迟一拍 fit：_set_center 把 scroll 加入布局，dialog 尺寸此时才最终
        # 落定；若立即 fit 会拿到 0 / 旧的 viewport 而缩得偏小（scroll.showEvent
        # 里的 singleShot 也可能在 dialog 布局完成前触发）。fit() 内部对
        # viewport<=0 有重试兜底，双保险确保用最终尺寸 fit。
        QTimer.singleShot(0, self.scroll.fit)
        self.zoom_in_button.show()
        self.zoom_out_button.show()
        self.zoom_actual_button.show()
        self.fit_button.show()

    def _on_preview_failed(self, path):
        if self._closed:
            return
        self.scroll = None
        msg = QLabel(self._preview_error_message(path))
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        self._set_center(msg)
        self.zoom_in_button.hide()
        self.zoom_out_button.hide()
        self.zoom_actual_button.hide()
        self.fit_button.hide()

    def closeEvent(self, event):
        # 关闭时若后台解码仍在跑，置标志并请求线程退出；线程结束后自清理。
        self._closed = True
        if self._loader is not None:
            self._loader.quit()
        super().closeEvent(event)

    @staticmethod
    def _preview_error_message(path):
        """Return a user-friendly failure message for the preview dialog.

        JXL files need djxl; when libjxl is not on PATH we surface a specific
        hint instead of the generic "cannot load" text.
        """
        name = os.path.basename(path)
        if path.lower().endswith(".jxl"):
            if converter.find_tool("djxl") is None:
                return (
                    i18n.t("无法预览 JXL 文件：未检测到 libjxl 的 djxl 工具。\n\n"
                    "请确认 libjxl 已正确安装，并将其所在目录加入系统的 PATH 环境变量。")
                )
            return (
                i18n.t("无法加载 JXL 图片：%s\n\n"
                "djxl 解码失败，文件可能损坏或不受支持。") % name
            )
        return i18n.t("无法加载图片：%s") % name

    def _zoom(self, factor):
        if self.scroll is not None:
            self.scroll.zoom(factor)

    def _fit(self):
        if self.scroll is not None:
            self.scroll.fit()

    def _zoom_actual(self):
        if self.scroll is not None:
            self.scroll.zoom_to_actual()


class ActionItemWidget(QWidget):
    """动作列表项的 widget：折叠按钮 + 勾选框 + 摘要 + inline 参数 + 紧凑按钮。

    布局（2 行）：
        行 1: [▾ 折叠] [☑ 启用] [摘要 stretch] [上移] [下移] [移除]
        行 2: QGridLayout(2 列: label, widget) 装 inline 参数（按 type 动态生成）
              —— 折叠时整行隐藏
    参数变化调 ``on_change(item, key, value)`` 通知 MainWindow，
    主线程用 250ms 防抖触发预览刷新。折叠状态存 ``action._collapsed`` 跨会话恢复。

    历史上试过 FlowLayout（按可用宽度自动换行），但 PySide6 中 Python 派生
    QLayout 的 setGeometry 不会被 C++ 端 dispatch，导致 _do_layout 从不执行。
    改为 QGridLayout(2 列) 后稳定可靠：label 列固定宽度、widget 列 stretch=1
    占满剩余；多参数时自动换行（每行一对 label + widget）。
    """

    def __init__(self, action=None, on_change=None, parent=None):
        super().__init__(parent)
        self.item = None
        self.action = dict(action) if isinstance(action, dict) else {}
        self.on_change = on_change
        self._block_change = False
        self._param_widgets = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(3)
        # 第 1 行：折叠 + 勾选 + 摘要(stretch) + 三个紧凑按钮
        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        # 折叠按钮：模仿 QComboBox 下拉按钮样式（正方形 + ▼ 向下箭头）。
        # ⚠️ 不要 setCheckable(True)：checkable 按钮点击后会保持「已按下」状态
        # （原生变蓝、Fusion 颜色变深），用户要求只转箭头方向、不呈现按下态。
        # 改为非 checkable + 自己用 clicked 维护 self._collapsed 状态。
        self.collapse_btn = QToolButton()
        self.collapse_btn.setArrowType(Qt.DownArrow)
        self.collapse_btn.setFixedSize(20, 20)
        self.collapse_btn.setToolTip(i18n.t("折叠/展开参数"))
        self._collapsed = False  # 折叠按钮为非 checkable，状态自己维护
        # ⚠️ 用 AlignVCenter 而非 AlignTop：AlignTop 会把 20px 的小控件贴到
        # 行顶，而旁边的文字/勾选框基线偏下 → 视觉上「上漂」（原生下折叠
        # 按钮上漂、Fusion 下勾选框上漂，都是同一根因）。垂直居中统一对齐。
        top_row.addWidget(self.collapse_btn, 0, Qt.AlignVCenter)
        self.enable_check = QCheckBox()
        self.enable_check.setChecked(bool(self.action.get("enabled", True)))
        self.enable_check.setToolTip(
            i18n.t("取消勾选可临时停用该动作（参数保留，不会被应用）"))
        top_row.addWidget(self.enable_check, 0, Qt.AlignVCenter)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        if self.action:
            self.summary_label.setText(self._summary_text())
        top_row.addWidget(self.summary_label, stretch=1)
        # 三个紧凑按钮（与标题同行，不另起一行）
        # 用户要求：高度用默认（QPushButton 标准高度），宽度刚好显示文字。
        # 设 setMaximumWidth 让内容决定宽度（不再设 fixed/minimum 强制拉宽）。
        self.up_button = QPushButton(i18n.t("上移"))
        self.down_button = QPushButton(i18n.t("下移"))
        self.remove_button = QPushButton(i18n.t("移除"))
        for b in (self.up_button, self.down_button, self.remove_button):
            b.setMaximumWidth(50)
            top_row.addWidget(b)
        root.addLayout(top_row)
        # 第 2 行：inline 参数（可折叠）
        # 用 QGridLayout(2 列: label, widget)：
        # PySide6 中 Python 派生的 QLayout 子类的 setGeometry 不会被 C++ 端
        # 调到（_do_layout 从不执行）。QGridLayout 是 Qt 原生实现，cascade 稳。
        # ⚠️ widget 列不要 stretch=1 —— 6×3 默认布局里 panel 只有 ~400px，
        # stretch=1 会把 QSpinBox/NoFlickerComboBox 拉成全宽（实测 640px），
        # 「算法」下拉「LANCZOS (高质量, 默认)」撑爆整行。改为 0：widget 按
        # 内容显示，右侧留白；窗口拉宽时各 widget 略宽但仍按内容。
        self.params_container = QWidget()
        if self.action:
            param_layout = QGridLayout(self.params_container)
            param_layout.setContentsMargins(0, 0, 0, 0)
            param_layout.setHorizontalSpacing(8)
            param_layout.setVerticalSpacing(4)
            param_layout.setColumnStretch(0, 0)  # label 列：内容宽度
            param_layout.setColumnStretch(1, 0)  # widget 列：按内容
            self._next_param_row = 0
            self._param_widgets = self._build_param_widgets(param_layout)
            if self._param_widgets:
                root.addWidget(self.params_container)
                self.collapse_btn.clicked.connect(self._on_collapse_clicked)
                # 同步初始折叠状态（默认展开；用户上次折叠过则仍折叠）
                if self.action.get("_collapsed", False):
                    self._set_collapsed(True)

    def _set_collapsed(self, collapsed):
        """切换折叠状态：只转箭头方向，按钮不保持「已按下」外观。"""
        self._collapsed = bool(collapsed)
        self.collapse_btn.setArrowType(
            Qt.RightArrow if self._collapsed else Qt.DownArrow)
        self.params_container.setVisible(not self._collapsed)
        if self.item is not None:
            self.item.setSizeHint(self.sizeHint())

    def _on_collapse_clicked(self):
        """折叠按钮点击：翻转折叠状态（非 checkable，按钮点击后自动弹起）。"""
        collapsed = not getattr(self, "_collapsed", False)
        self._set_collapsed(collapsed)
        # 持久化 UI 状态到 action dict（_collapsed=True 表示折叠）
        if self.item is not None:
            data = self.item.data(Qt.UserRole)
            if isinstance(data, dict):
                data["_collapsed"] = collapsed
                self.item.setData(Qt.UserRole, data)

    def _on_collapse_toggled(self, checked):
        """兼容旧接口（toggled 语义，按钮曾为 checkable）：
        checked=True=展开，=False=折叠。新流程走 _on_collapse_clicked。"""
        self._set_collapsed(not checked)

    def _summary_text(self):
        """用 MainWindow._action_summary 派生摘要文本（保留作为类型指示）。"""
        from libjxl_gui import main_window as _mw
        try:
            return _mw.MainWindow._action_summary(None, self.action)
        except Exception:
            # 兜底也走 t()：直接返回原始 ID 会让英文界面冒出中文。
            return i18n.t(self.action.get("type", ""))

    def _add_param(self, grid, label, widget):
        """Add 'label: widget' as a row in a QGridLayout (label=左, widget=右)。"""
        lbl = QLabel(label)
        lbl.setStyleSheet("color: gray;")
        row = self._next_param_row
        self._next_param_row += 1
        # ⚠️ 不用 AlignRight —— 它把 label 推到 cell 右端，widget 紧跟其右，
        # 整体看起来 label+widget 都偏右。改用默认左对齐，label 紧贴左边缘、
        # widget 紧跟其后，参数行整体靠左（与标题行左缩进对齐）。
        grid.addWidget(lbl, row, 0)
        grid.addWidget(widget, row, 1)

    def _emit(self, key, value):
        if self._block_change:
            return
        if self.on_change is not None and self.item is not None:
            self.on_change(self.item, key, value)

    def _build_param_widgets(self, layout):
        """按 action type 在 layout 上挂 inline 参数控件，返回 {key: widget}。"""
        p = self.action.get("params", {}) or {}
        atype = self.action.get("type", "")
        widgets = {}
        if atype == "调整大小":
            w = QSpinBox()
            w.setRange(0, 100000)
            w.setValue(int(p.get("width", 0) or 0))
            w.setSpecialValueText(i18n.t("自动(按比例)"))
            w.valueChanged.connect(lambda v, k="width": self._emit(k, v))
            self._add_param(layout, i18n.t("宽"), w)
            widgets["width"] = w
            h = QSpinBox()
            h.setRange(0, 100000)
            h.setValue(int(p.get("height", 0) or 0))
            h.setSpecialValueText(i18n.t("自动(按比例)"))
            h.valueChanged.connect(lambda v, k="height": self._emit(k, v))
            self._add_param(layout, i18n.t("高"), h)
            widgets["height"] = h
            algo = NoFlickerComboBox()
            for key, lab in processor.RESIZE_ALGORITHMS:
                algo.addItem(lab, userData=key)
            cur = p.get("algorithm", "LANCZOS")
            idx = next((i for i, (k, _) in enumerate(processor.RESIZE_ALGORITHMS)
                        if k == cur), 0)
            algo.setCurrentIndex(idx)
            algo.currentIndexChanged.connect(
                lambda i, c=algo: self._emit("algorithm", c.itemData(i)))
            self._add_param(layout, i18n.t("算法"), algo)
            widgets["algorithm"] = algo
        elif atype == "旋转":
            a = QSpinBox()
            a.setRange(-360, 360)
            a.setValue(int(p.get("angle", 90) or 90))
            a.setSuffix(" °")
            a.valueChanged.connect(lambda v, k="angle": self._emit(k, v))
            self._add_param(layout, i18n.t("角度"), a)
            widgets["angle"] = a
        elif atype == "水印":
            t = QLineEdit(p.get("text", "Sample") or "Sample")
            t.editingFinished.connect(lambda k="text", w=t: self._emit(k, w.text()))
            self._add_param(layout, i18n.t("文字"), t)
            widgets["text"] = t
            fs = QSpinBox()
            fs.setRange(8, 400)
            fs.setValue(int(p.get("font_size", 32) or 32))
            fs.valueChanged.connect(lambda v, k="font_size": self._emit(k, v))
            self._add_param(layout, i18n.t("字号"), fs)
            widgets["font_size"] = fs
            op = QSpinBox()
            op.setRange(0, 255)
            op.setValue(int(p.get("opacity", 128) or 128))
            op.valueChanged.connect(lambda v, k="opacity": self._emit(k, v))
            self._add_param(layout, i18n.t("透明度"), op)
            widgets["opacity"] = op
            pos = NoFlickerComboBox()
            # 显示名与内部 ID 分离：显示 t(位置)，userData 存原始中文位置，
            # 回写 action params 时用 currentData()，英文界面下也不会存成英文。
            for _pid in processor.WATERMARK_POSITIONS:
                pos.addItem(i18n.t(_pid), _pid)
            _pi = pos.findData(p.get("position", "右下"))
            if _pi >= 0:
                pos.setCurrentIndex(_pi)
            pos.currentIndexChanged.connect(
                lambda _idx, k="position": self._emit(k, pos.currentData()))
            self._add_param(layout, i18n.t("位置"), pos)
            widgets["position"] = pos
            col = NoFlickerComboBox()
            col.addItems(["white", "black"])
            col.setCurrentText(p.get("color", "white"))
            col.currentTextChanged.connect(
                lambda v, k="color": self._emit(k, v))
            self._add_param(layout, i18n.t("颜色"), col)
            widgets["color"] = col
        elif atype == "亮度/对比度":
            b = QDoubleSpinBox()
            b.setRange(0.0, 3.0)
            b.setSingleStep(0.1)
            b.setValue(float(p.get("brightness", 1.0) or 1.0))
            b.valueChanged.connect(lambda v, k="brightness": self._emit(k, v))
            self._add_param(layout, i18n.t("亮度"), b)
            widgets["brightness"] = b
            c = QDoubleSpinBox()
            c.setRange(0.0, 3.0)
            c.setSingleStep(0.1)
            c.setValue(float(p.get("contrast", 1.0) or 1.0))
            c.valueChanged.connect(lambda v, k="contrast": self._emit(k, v))
            self._add_param(layout, i18n.t("对比度"), c)
            widgets["contrast"] = c
        elif atype == "锐化":
            f = QDoubleSpinBox()
            f.setRange(0.0, 5.0)
            f.setSingleStep(0.1)
            f.setValue(float(p.get("factor", 1.5) or 1.5))
            f.valueChanged.connect(lambda v, k="factor": self._emit(k, v))
            self._add_param(layout, i18n.t("强度"), f)
            widgets["factor"] = f
        elif atype == "裁剪":
            for label, key in (
                ("左", "left"), ("上", "top"),
                (i18n.t("宽"), "width"), (i18n.t("高"), "height"),
            ):
                sp = QSpinBox()
                sp.setRange(0, 100000)
                sp.setValue(int(p.get(key, 0) or 0))
                sp.valueChanged.connect(lambda v, k=key: self._emit(k, v))
                self._add_param(layout, label, sp)
                widgets[key] = sp
        elif atype == "规格化":
            co = QSpinBox()
            co.setRange(0, 50)
            co.setValue(int(p.get("cutoff", 0) or 0))
            co.setToolTip(i18n.t("截掉直方图两端各 N‰ 的极值像素后再拉满（0=纯规格化）"))
            co.valueChanged.connect(lambda v, k="cutoff": self._emit(k, v))
            self._add_param(layout, i18n.t("截断"), co)
            widgets["cutoff"] = co
        elif atype == "曝光":
            ev = QDoubleSpinBox()
            ev.setRange(-3.0, 3.0)
            ev.setSingleStep(0.1)
            ev.setValue(float(p.get("ev", 0.0) or 0.0))
            ev.setSuffix(" EV")
            ev.setToolTip(i18n.t("+1 EV = 亮度翻倍，-1 EV = 减半；0=不变"))
            ev.valueChanged.connect(lambda v, k="ev": self._emit(k, v))
            self._add_param(layout, "曝光", ev)
            widgets["ev"] = ev
        elif atype == "阴影/高光":
            s = QDoubleSpinBox()
            s.setRange(0.0, 2.0)
            s.setSingleStep(0.05)
            s.setValue(float(p.get("shadow", 1.0) or 1.0))
            s.setToolTip(i18n.t(">1 提亮阴影，<1 压暗阴影；1.0=不变"))
            s.valueChanged.connect(lambda v, k="shadow": self._emit(k, v))
            self._add_param(layout, i18n.t("阴影"), s)
            widgets["shadow"] = s
            h = QDoubleSpinBox()
            h.setRange(0.0, 2.0)
            h.setSingleStep(0.05)
            h.setValue(float(p.get("highlight", 1.0) or 1.0))
            h.setToolTip(i18n.t(">1 提亮高光，<1 压暗高光；1.0=不变"))
            h.valueChanged.connect(lambda v, k="highlight": self._emit(k, v))
            self._add_param(layout, i18n.t("高光"), h)
            widgets["highlight"] = h
        return widgets

    def sync_from_action(self):
        """外部更新 action 数据后，同步各 inline 控件显示（避免循环回调）。"""
        p = self.action.get("params", {}) or {}
        self._block_change = True
        try:
            for key, widget in self._param_widgets.items():
                if key not in p:
                    continue
                v = p[key]
                if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(v)
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(v))
                elif isinstance(widget, NoFlickerComboBox):
                    if widget.itemData(widget.currentIndex()) is not None:
                        # resize algo combo: 用 userData 匹配
                        for i in range(widget.count()):
                            if widget.itemData(i) == v:
                                widget.setCurrentIndex(i)
                                break
                    else:
                        widget.setCurrentText(str(v))
            self.enable_check.setChecked(bool(self.action.get("enabled", True)))
            self.summary_label.setText(self._summary_text())
        finally:
            self._block_change = False


class ActionListWidget(QListWidget):
    """Action list; Delete/Backspace removes the selected action items."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.selectedIndexes():
                self.main_window._on_remove_selected_actions()
                event.accept()
                return
        super().keyPressEvent(event)


class ThumbnailDelegate(QStyledItemDelegate):
    """Draws each thumbnail item inside a fixed-ratio frame.

    The frame size is taken from the list widget's gridSize, so every item box
    is identical and grid-aligned regardless of the image aspect ratio or the
    file name length. The square (letterboxed) icon is centered in the top
    area and the file name is drawn in a fixed bottom band so it is always
    visible. This is what keeps the box ratio constant (the original bug where
    the ratio followed the last dropped image is gone because the icon is
    always square and the frame is always the grid size).
    """

    NAME_BAND = 22   # height reserved for the file name, in pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        # Cached font metrics + elided names so painting dozens of visible
        # items per frame stays cheap. The font never changes at runtime, and
        # the elided text only depends on the file name and the available width
        # (both constant within one view mode).
        self._fm = None
        self._elide_cache = {}

    def sizeHint(self, option, index):
        view = self.parent()
        if isinstance(view, QListWidget):
            gs = view.gridSize()
            if gs.isValid() and gs.width() > 0 and gs.height() > 0:
                return gs
        return super().sizeHint(option, index)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        icon = index.data(Qt.DecorationRole)
        name = index.data(Qt.DisplayRole)
        icon_box = QRect(
            rect.x(), rect.y(), rect.width(), rect.height() - self.NAME_BAND
        )
        view = self.parent()
        # Draw the icon at exactly its native resolution with a safe-area margin
        # (THUMB_PAD) on every side, so two neighboring thumbnails never touch.
        # The drawn size matches what _make_thumbnail rendered (same formula),
        # so it is never upscaled and never blurry.
        gs = view.gridSize()
        if gs.isValid() and gs.width() > 0 and gs.height() > 0:
            draw = min(gs.width(), gs.height() - self.NAME_BAND) - 2 * THUMB_PAD
        else:
            draw = view.iconSize().width() - 2 * THUMB_PAD
        if draw <= 0:
            draw = view.iconSize().width()
        # The decoration is a ready-made QPixmap (rendered at draw*dpr) stored
        # directly in DecorationRole -- draw it 1:1. Going through QIcon.pixmap()
        # would re-render the thumbnail whenever the requested size disagreed
        # with the cached one (a DPR mismatch), which made rubber-band auto-scroll
        # stutter. We still tolerate a QIcon fallback for safety.
        pix = None
        if isinstance(icon, QPixmap) and not icon.isNull():
            pix = icon
        elif icon is not None and not icon.isNull() and hasattr(icon, "pixmap"):
            dpr = max(1.0, float(view.devicePixelRatio()))
            tmp = icon.pixmap(int(round(draw * dpr)), int(round(draw * dpr)))
            if tmp.isNull():
                tmp = icon.pixmap(draw, draw)
            pix = tmp
        if pix is not None and not pix.isNull() and draw > 0:
            x = rect.x() + (rect.width() - draw) // 2
            y = rect.y() + (icon_box.height() - draw) // 2
            painter.drawPixmap(x, y, draw, draw, pix)
        name_rect = QRect(
            rect.x(), rect.y() + rect.height() - self.NAME_BAND,
            rect.width(), self.NAME_BAND,
        )
        if self._fm is None:
            self._fm = QFontMetrics(painter.font())
        avail = name_rect.width() - 6
        cache_key = (name, avail)
        elided = self._elide_cache.get(cache_key)
        if elided is None:
            elided = self._fm.elidedText(name or "", Qt.ElideRight, avail)
            self._elide_cache[cache_key] = elided
        painter.drawText(name_rect, Qt.AlignHCenter | Qt.AlignVCenter, elided)
        painter.restore()


# A single Fusion style instance, shared by every combobox / popup so they
# render with Qt's own (non-native, non-DWM-animated) engine. On Windows 10/11
# the native combobox popup gets a DWM slide/fade entrance animation that
# flickers; Fusion-drawn popups are painted immediately and do not animate.
_FUSION_STYLE = None  # 留作占位历史变量；_fusion_style() 现在每次创建新风格。


def _fusion_style():
    """返回一个 Fusion 风格的**新**实例。

    注意：曾经用作模块级单例 (``_FUSION_STYLE``)，但 ``QApplication.setStyle``
    会在被调用时接管新风格的 C++ 所有权并可能作废旧风格——而 NoFlickerComboBox
    仍持有旧风格的 Python 引用，导致后续 ``setStyle(dead_ref)`` 报
    ``Internal C++ object already deleted``。改为每次创建新风格（成本极低）：
    与 ``_apply_app_style`` 里「A fresh style is created each call so re-applying
    the same QStyle instance never collides on ownership」同款做法。
    """
    return QStyleFactory.create("Fusion")




# Application-wide theme. One of:
#   "native_noflicker" - mostly native; only the flickering dropdown popups
#                        (NoFlickerComboBox + folder-history menu) use the
#                        Fusion style to dodge the Windows DWM popup flicker.
#   "native"           - fully native; no Fusion anywhere (dropdowns may flicker).
#   "fusion"           - the entire application uses the Fusion style.
# Default is "native_noflicker" (the original behaviour of the app).
_APP_THEME = "native_noflicker"
_THEME_ORDER = ("native_noflicker", "native", "fusion")
_THEME_LABELS = {
    "native_noflicker": "原生（无闪烁）",
    "native": "原生",
    "fusion": "Fusion",
}


def app_theme():
    """Return the active theme key."""
    return _APP_THEME


def set_app_theme(theme):
    """Set the active theme key (ignored if not a known value)."""
    global _APP_THEME
    if theme in _THEME_ORDER:
        _APP_THEME = theme


def dropdowns_use_fusion():
    """Whether the flickering dropdown popups should be individually styled
    with Fusion. True for native_noflicker (default) and fusion; False for the
    pure-native 'native' theme."""
    return _APP_THEME in ("native_noflicker", "fusion")


# Application-wide color scheme (light/dark). One of:
#   "follow_system" - follows the OS / style hints color scheme (default).
#   "light"         - force a light palette.
#   "dark"          - force a dark palette.
# The change is applied via QStyleHints.setColorScheme(), the Qt 6.6+ API that
# notifies the active style to repaint with light/dark colours. The existing
# QEvent.PaletteChange handler already refreshes the custom-folder popup and
# re-syncs the Inactive palette group, so a switch propagates everywhere.
_APP_COLOR_SCHEME = "follow_system"
_COLOR_SCHEME_ORDER = ("follow_system", "light", "dark")
_COLOR_SCHEME_LABELS = {
    "follow_system": "跟随系统",
    "light": "亮色",
    "dark": "暗色",
}
_QT_COLOR_SCHEMES = {
    "follow_system": Qt.ColorScheme.Unknown,
    "light": Qt.ColorScheme.Light,
    "dark": Qt.ColorScheme.Dark,
}

# 界面语言。**加语言不需要改这里**：丢一个 <code>.json 进 libjxl_gui/i18n/
# 就会自动出现在下拉里，显示名取该文件里的 _language_name 元信息键。
# 语言名用自称名（English / 日本語）而非当前语言的译文，否则英文界面上
# 会显示成 "Japanese"，反而认不出来。
_LANGUAGE_DEFAULT = i18n.DEFAULT_LANGUAGE
_LANGUAGE_ORDER = tuple(i18n.language_order())
_LANGUAGE_LABELS = {code: i18n.language_name(code) for code in _LANGUAGE_ORDER}


def app_color_scheme():
    """Return the active color scheme key."""
    return _APP_COLOR_SCHEME


def set_app_color_scheme(scheme):
    """Set the active color scheme key (ignored if not a known value) and
    push it to ``QStyleHints`` so the style repaints immediately."""
    global _APP_COLOR_SCHEME
    if scheme in _COLOR_SCHEME_ORDER:
        _APP_COLOR_SCHEME = scheme
        QGuiApplication.styleHints().setColorScheme(_QT_COLOR_SCHEMES[scheme])


class NoFlickerComboBox(QComboBox):
    """QComboBox that does not flicker on Windows 10/11. The dropdown popup
    is shown frameless: a frameless (caption-less) top-level window is exempt
    from the DWM slide/fade entrance animation that otherwise flickers. A solid
    background is forced on the popup container so no black flash appears, and
    the Fusion style is applied for clean, native-free rendering."""

    #: Stylesheet applied to the popup container while it is rendered by Fusion
    #: (solid background so the area around the list never flashes black).
    _POPUP_QSS_FUSION = (
        "QFrame { background: palette(base); "
        "border: 1px solid palette(mid); }"
    )

    #: Stylesheet applied to the popup *view* when the dropdown is drawn by the
    #: Fusion style (themes "fusion" and "native_noflicker"). The 1px frame
    #: belongs here because the container is forced frameless, so nothing else
    #: draws an outline around the list.
    _VIEW_QSS_FUSION = (
        "QAbstractItemView { border: 1px solid palette(mid); "
        "background: palette(base); }"
    )
    #: Stylesheet applied to the popup view when the dropdown is left to the
    #: Windows native style (theme "native" — ``dropdowns_use_fusion()`` False).
    #:
    #: ⚠️ No border here: the native style keeps the container's own frame and
    #: draws a rounded one around the popup, so a 1px border on the view stacks
    #: with it and shows up as a visible double border (rounded outer frame +
    #: square inner frame). Only the solid background is kept, so the area still
    #: never flashes through to a default colour.
    #:
    #: Note "原生（无闪烁）" is *not* covered here despite its name: it keeps the
    #: native window chrome but draws its dropdowns with Fusion, so it uses
    #: ``_VIEW_QSS_FUSION``.
    _VIEW_QSS_NATIVE = "QAbstractItemView { background: palette(base); }"

    def __init__(self, parent=None):
        super().__init__(parent)
        # Popup-container bookkeeping. The container (a frameless-able top-level
        # window that owns the view) is only created the first time the popup is
        # shown, so its original window flags must be snapshotted at that moment
        # to be able to restore them later.
        self._popup_container = None
        self._popup_orig_flags = None
        self._popup_styled_for = None  # last applied dropdowns_use_fusion() value
        # ``_apply_fusion_style`` sets the view stylesheet, and it has to follow
        # the same Fusion/native split as the container (see the two constants
        # above). Setting it unconditionally with a border here is what produced
        # the double border under the native style.
        self._apply_fusion_style()

    def _popup_container_widget(self):
        """Return the popup's own top-level container, or ``None`` if the popup
        has never been shown (before that the view still lives inside the main
        window, so ``view.window()`` is the main window, not a popup).

        Caches the container and snapshots its original window flags on the
        first sighting.

        ⚠️ Also returns ``None`` when the container has just been torn down.
        Qt 6 ``deleteLater`` s the popup container in ``QComboBox.hidePopup()``,
        which leaves the view as an orphan top-level window — ``view.window()``
        then returns the *view itself*. Treating that QListView as the container
        and calling window-flag / stylesheet setters on it rebuilds the list's
        native window for no reason. Qt re-creates a fresh container on the next
        ``showPopup()``, so the cached state is dropped and re-snapshotted then.
        """
        view = self.view()
        if view is None:
            return None
        container = view.window()
        if container is None or container is self.window():
            # The popup has never been opened: the view still lives inside the
            # main window, so there is no popup container to style.
            return None
        if container is view:
            self._popup_container = None
            self._popup_orig_flags = None
            self._popup_styled_for = None
            return None
        if self._popup_container is not container:
            self._popup_container = container
            self._popup_orig_flags = container.windowFlags()
            self._popup_styled_for = None
        return container

    def _apply_popup_container_style(self):
        """Apply — or revert — the frameless + solid-background treatment that
        suppresses the Windows DWM popup animation.

        This is a *state machine*, not a one-shot: it is idempotent and can be
        re-run at any time (theme switch, palette change). Under the pure-native
        theme the container is restored to exactly the flags it was created with
        and its Fusion stylesheet is cleared, so a popup that was previously
        shown under a Fusion-ish theme is not left stuck frameless/borderless.

        The window flags are only touched when they actually differ, and always
        through ``overrideWindowFlags`` rather than ``setWindowFlags``:
        ``setWindowFlags`` destroys and recreates the underlying native window,
        which drops the mouse grab the combobox holds while its popup is open —
        and it does so even when the container is merely hidden, so "hidden" is
        not a safe excuse. ``overrideWindowFlags`` only mutates the widget's
        internal flag state, so the new flags take effect on the next native
        window show without disturbing any grab.

        Note that on Qt 6 the flags half is usually a no-op:
        ``QComboBoxPrivateContainer`` is *already* created with
        ``FramelessWindowHint | NoDropShadowWindowHint``, so the Fusion branch
        computes ``base_flags`` unchanged. What actually suppresses the black
        flash is the solid background stylesheet, not the flags.
        """
        container = self._popup_container_widget()
        if container is None:
            return
        use_fusion = dropdowns_use_fusion()
        if self._popup_styled_for != use_fusion:
            self._popup_styled_for = use_fusion
            base_flags = (
                self._popup_orig_flags
                if self._popup_orig_flags is not None
                else container.windowFlags()
            )
            if use_fusion:
                desired = base_flags | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
                qss = self._POPUP_QSS_FUSION
            else:
                desired = base_flags
                qss = ""
            if container.windowFlags() != desired:
                # overrideWindowFlags — no native window rebuild, so no grab is
                # lost and no geometry has to be saved and restored.
                container.overrideWindowFlags(desired)
            if container.styleSheet() != qss:
                container.setStyleSheet(qss)
        # Always resnap the palette: a freshly created container (first ever
        # ``showPopup()`` after process start, or the very first popup after a
        # ``_popup_container`` was reset) inherits a stale, style-default palette
        # that has nothing to do with the user's current colour scheme.
        self._resnap_popup_palette()

    def _apply_fusion_style(self):
        """Re-apply the active theme to this combobox *and* its popup.

        Safe to call at any time (construction, theme switch, palette change).

        Three pieces of state are refreshed:

        * The combobox's own style — Fusion for the no-flicker themes, the
          application-wide style otherwise.
        * The popup view's *stylesheet*, which follows the same Fusion/native
          split as the container: Fusion needs the 1px frame drawn on the view,
          the native style must not draw one (see ``_VIEW_QSS_NATIVE``).
        * The popup view's palette, pinned to the application-wide **Active**
          group. Qt treats the popup as a separate top-level window and feeds it
          the Inactive group, which under the native Windows style paints the
          current-row selection indicator (the thin vertical bar) black instead
          of the active blue highlight.

        Because that palette is a *snapshot*, every caller that changes the
        palette (colour-scheme switch, system light/dark switch) must re-run
        this method — see ``MainWindow._refresh_combo_styles``.
        """
        fusion = _fusion_style()
        use_fusion = dropdowns_use_fusion()
        if fusion is not None and use_fusion:
            self.setStyle(fusion)
        else:
            # Inherit the application-wide style so the widget reflects the
            # current theme (native, or global Fusion) instead of staying Fusion.
            self.setStyle(QApplication.style())
        # Refresh the view stylesheet from the *current* theme. It is set here
        # rather than once in ``__init__`` because it differs between the two
        # styles, and because ``setStyle`` above repolishes the view.
        view = self.view()
        if view is not None:
            view_qss = self._VIEW_QSS_FUSION if use_fusion else self._VIEW_QSS_NATIVE
            if view.styleSheet() != view_qss:
                view.setStyleSheet(view_qss)
        self._resnap_popup_palette()
        # Bring an already-created popup container in line with the new theme:
        # without this, a popup opened under one style keeps the old style's
        # flags/stylesheet until the app is restarted.
        self._apply_popup_container_style()

    def _resnap_popup_palette(self):
        """Refresh the cached application-wide palette on every popup piece
        (view + its viewport + the top-level container, when present) and
        force a repaint.

        Why this is needed beyond ``view.setPalette(QApplication.palette())``:
        Qt does not automatically notify a *detached* popup of palette
        changes. ``view.setPalette`` only updates the list widget itself;
        its ``viewport()`` (the actual paint surface) and the top-level
        popup container (a plain ``QFrame``) inherit the previously
        rendered colours until they are repolished and repainted. Without
        these extra ``setPalette + update`` calls, a colour-scheme switch
        updates the data (``QApplication.palette()``) but the already-built
        popup keeps painting with the stale theme — the user must toggle
        the control style and back to force a rebuild.
        """
        view = self.view()
        if view is not None:
            active = QApplication.palette()
            view.setPalette(active)
            vp = view.viewport()
            if vp is not None:
                vp.setPalette(active)
            # Re-polish so cached style colours (hover / selected item)
            # are re-read from the just-installed palette.
            try:
                view.style().polish(view)
            except Exception:
                pass
            view.update()
            if vp is not None:
                vp.update()
            # If the popup is open RIGHT NOW (detached top-level window), a
            # queued ``update()`` may not land before paint: the popup sits
            # on its own event-loop / paint cycle and the cached frame stays
            # stale. ``viewport().repaint()`` forces a synchronous redraw of
            # the item surface and is the cheapest way to make an open
            # dropdown follow the new colour scheme without rebuilding it.
            if view.isVisible() and vp is not None:
                vp.repaint()
        container = self._popup_container
        if container is not None:
            try:
                container.style().polish(container)
            except Exception:
                pass
            container.setPalette(QApplication.palette())
            container.update()
            if container.isVisible():
                container.repaint()


    def showPopup(self):
        super().showPopup()
        self._apply_popup_container_style()


class HistoryRowWidget(QWidget):
    """One row inside the custom-folder history menu: a clickable path label
    plus a per-row "✕" delete button. Clicking the label selects the folder;
    clicking "✕" deletes that history entry (the menu stays open)."""

    selected = Signal(str)
    deleteRequested = Signal(int)

    def __init__(self, path, row, parent=None):
        super().__init__(parent)
        # A plain QWidget does NOT paint its Stylesheet background unless this
        # attribute is set, so the hover highlight would never show. Enabling
        # it makes the hover feedback visible.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._row = row
        self._hover = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(8)
        label = QPushButton(path)
        label.setFlat(True)
        label.clicked.connect(lambda: self.selected.emit(path))
        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip(i18n.t("删除该历史记录"))
        del_btn.clicked.connect(lambda: self.deleteRequested.emit(self._row))
        layout.addWidget(label, stretch=1)
        layout.addWidget(del_btn)
        # Hover highlighting runs on THIS row's own Enter/Leave event filter
        # (installed on the row and both children). Enter/Leave fire reliably
        # on boundary crossings, unlike the old menu-level MouseMove filter,
        # which only updated while the cursor sat on a mouse-tracking widget
        # and therefore froze ("felt sticky") the instant it crossed onto a
        # child that did not track the mouse. As a belt-and-suspenders backup
        # the menu keeps a mouse-move filter too, and we enable mouse tracking
        # on the children so that backup keeps firing across the whole row.
        for w in (self, label, del_btn):
            w.installEventFilter(self)
            w.setMouseTracking(True)
        self._apply_style(False)

    def _apply_style(self, hovered):
        # Concrete colours from the live palette (never the QSS palette(...) role,
        # which here resolves inverted and does not re-resolve on theme switch).
        pal = QApplication.palette()
        if hovered:
            bg = pal.color(QPalette.Highlight).name()
            fg = pal.color(QPalette.HighlightedText).name()
        else:
            bg = "transparent"
            fg = pal.color(QPalette.Text).name()
        # Define every property on the row's own stylesheet so it cascades to
        # the child QPushButton / QToolButton (they have no own stylesheet).
        self.setStyleSheet(
            "HistoryRowWidget { background: %s; }"
            "QPushButton { text-align: left; border: none; padding: 2px 0; "
            "background: transparent; color: %s; }"
            "QToolButton { border: none; background: transparent; color: %s; }"
            % (bg, fg, fg)
        )

    def set_hover(self, on):
        """Highlight (or clear) this row."""
        if on == self._hover:
            return
        self._hover = on
        self._apply_style(on)

    def set_row(self, row):
        """Re-number this row's logical index after another row is deleted
        in place (so its delete signal keeps pointing at the right entry)."""
        self._row = row

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Enter:
            self.set_hover(True)
        elif etype == QEvent.Leave:
            # Moving from the row onto one of its own children fires Leave on
            # the row, but the cursor is still inside the row's bounds -- only
            # clear when the pointer truly left the row.
            if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
                self.set_hover(False)
        return super().eventFilter(obj, event)


class FolderMenu(QMenu):
    """QMenu that lists folder-history rows (HistoryRowWidget). It installs a
    menu-level event filter so hover highlighting works on the custom row
    widgets: QWidgetAction children inside a QMenu do not reliably get their
    own hover / mouse events, but events of ALL descendants propagate to a
    filter installed on the menu itself."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.installEventFilter(self)

    def _rows(self):
        rows = []
        for action in self.actions():
            w = action.defaultWidget()
            if isinstance(w, HistoryRowWidget):
                rows.append(w)
        return rows

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.MouseMove:
            self._update_hover()
        elif etype == QEvent.Leave:
            for r in self._rows():
                r.set_hover(False)
        return super().eventFilter(obj, event)

    def _update_hover(self):
        # Use real hit-testing instead of manual geometry math: find the actual
        # widget under the cursor and walk up to the HistoryRowWidget it
        # belongs to. This avoids the off-by-one row offset that resulted from
        # comparing each row's rect() against mapFromGlobal() (the row widgets'
        # effective positions inside the menu's QWidgetAction container did not
        # line up with their own rect()).
        w = QApplication.widgetAt(QCursor.pos())
        target = None
        while w is not None:
            if isinstance(w, HistoryRowWidget):
                target = w
                break
            w = w.parent()
        for r in self._rows():
            r.set_hover(r is target)


# ---------------------------------------------------------------------------
# 高级参数 schema（数据驱动构建输出标签的「高级参数」折叠分组）。
# 每项描述一个 cjxl 调优旋钮：
#   key     -> converter.encode / build_args 的参数名
#   flag    -> cjxl 命令行标志（仅用于展示）
#   label   -> 复选框文字
#   kind    -> double | int | choice | switch | bool_value
#              switch/bool_value 无独立值控件（勾选即生效）；
#              double/int/choice 需额外的值控件（未勾选则不传递该参数）。
#   default -> 值控件默认值（复选框一律默认不勾选，即不传该参数）
#   group   -> 所属子组（质量精细 / 编码策略 / 保真合成 / 容器输出）
#   modes   -> 该参数在哪些编码模式下可用（用于按模式置灰）
# 仅收录此前对 cjxl v0.12.0 实跑验证「接受」的参数；orientation 等被拒参数不暴露。
# ---------------------------------------------------------------------------

# 本机逻辑核心数（含超线程），用作 --num_threads 的上限。按机器读取而非写死，
# 免得在核心数更少的机器上暴露永远用不到的档位。
_LOGICAL_CORES = os.cpu_count() or 1

# --num_threads 的三个特殊档位说明（cjxl v0.12 与 djxl 语义一致，见
# ``cjxl -v -v --help`` / ``djxl -v -v --help``）。
_NUM_THREADS_TIP = (
    "每文件分配给 cjxl 的 worker 线程数。默认 -1，与 cjxl 不传该参数时的行为一致。\n"
    "  -1 = 交给 cjxl 按机器自动决定（等于吃满全部核心，故同时只跑 1 个进程）；\n"
    "   0 = 禁用多线程（单线程编码，此时会改为多进程并行）；\n"
    " 1..%d = 指定线程数，并行进程数按「CPU 核心使用数」预算自动收缩，避免超订。"
)

_ADVANCED_SCHEMA = [
    # 质量精细（仅「有损」模式有意义）
    {"key": "distance", "flag": "-d", "label": "Butteraugli 距离 (-d)",
     "kind": "double", "default": 1.0, "min": 0.0, "max": 25.0, "step": 0.1,
     "group": "质量精细", "modes": ("lossy",)},
    {"key": "progressive", "flag": "--progressive", "label": "渐进式解码 (--progressive)",
     "kind": "switch", "default": False,
     "group": "质量精细", "modes": ("lossy",)},
    {"key": "faster_decoding", "flag": "--faster_decoding", "label": "加速解码档位 (--faster_decoding, 0–4)",
     "kind": "int", "default": 0, "min": 0, "max": 4,
     "group": "质量精细", "modes": ("lossy",)},
    # 编码策略（全部模式可用）
    {"key": "modular", "flag": "--modular", "label": "Modular 模式 (--modular)",
     "kind": "bool_value", "default": False, "value": 1,
     "group": "编码策略", "modes": ("lossy", "lossless", "lossless_jpeg")},
    # 取值区间 -1..逻辑核心数：-1/0 是 libjxl 的特殊档位（见 _NUM_THREADS_TIP），
    # 上限按机器读取。default 取 -1（机器自动决定），与 cjxl 不传该参数时的默认
    # 行为保持一致；不写死正整数，免得在核心数更少/更多的机器上口径不一致。
    {"key": "num_threads", "flag": "--num_threads", "label": "线程数 (--num_threads)",
     "kind": "int", "default": -1,
     "min": -1, "max": _LOGICAL_CORES, "tip": _NUM_THREADS_TIP,
     "group": "编码策略", "modes": ("lossy", "lossless", "lossless_jpeg")},
    {"key": "brotli_effort", "flag": "--brotli_effort", "label": "Brotli 压缩强度 (--brotli_effort)",
     "kind": "int", "default": 9, "min": 0, "max": 11,
     "group": "编码策略", "modes": ("lossy", "lossless", "lossless_jpeg")},
    # 保真合成（有损 / 无损可用；JPG 无损重编码会绕过，故置灰）
    {"key": "epf", "flag": "--epf", "label": "边缘滤波强度 (--epf)",
     "kind": "int", "default": 3, "min": 0, "max": 3,
     "group": "保真合成", "modes": ("lossy", "lossless")},
    {"key": "noise", "flag": "--noise", "label": "噪声合成 (--noise)",
     "kind": "int", "default": 0, "min": 0, "max": 16,
     "group": "保真合成", "modes": ("lossy", "lossless")},
    {"key": "resampling", "flag": "--resampling", "label": "色度重采样 (--resampling)",
     "kind": "choice", "default": -1,
     "choices": [(-1, "-1 默认"), (1, "1 八倍"), (2, "2 四倍"), (4, "4 两倍"), (8, "8 无")],
     "group": "保真合成", "modes": ("lossy", "lossless")},
    # 容器输出（全部模式可用）
    {"key": "container", "flag": "--container", "label": "JXL 容器 (--container)",
     "kind": "bool_value", "default": False, "value": 1,
     "tip": "启用可保留元数据（如 Exif、XMP、ICC 颜色配置等）",
     "group": "容器输出", "modes": ("lossy", "lossless", "lossless_jpeg")},
    {"key": "codestream_level", "flag": "--codestream_level", "label": "码流等级 (--codestream_level)",
     "kind": "int", "default": 5, "min": 0, "max": 10,
     "group": "容器输出", "modes": ("lossy", "lossless", "lossless_jpeg")},
]


def _adv_num_threads(adv_threads_enabled, advanced):
    """Resolve the user's explicit ``--num_threads`` value, or ``None``.

    返回 ``-1`` / ``0`` / ``N>=1``；未启用高级参数、未设定或值非法时返回
    ``None``（调用方据此走自动调度）。语义与 libjxl 一致（cjxl v0.12 与 djxl
    的 ``-v -v --help`` 均确认）：

      * ``-1`` — 交给工具按机器自动决定（会吃满全部逻辑核心）
      * ``0``  — 禁用多线程（单线程）
      * ``N``  — 用 N 个 worker 线程

    其余负数（如 -5）无定义，按非法处理。``bool`` 需排除：它是 ``int`` 子类，
    ``isinstance(True, int)`` 为真，会让 ``True`` 被当成 1 个线程。
    """
    if not adv_threads_enabled:
        return None
    value = (advanced or {}).get("num_threads")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value in (-1, 0):
        return value
    return value if value >= 1 else None


class MainWindow(QMainWindow):
    """Main application window (XnConvert-style four tabs)."""

    # 首次启动、尚未校准时，等窗口完全展示后再自动跑一次校准的延迟（毫秒）。
    CALIBRATION_DELAY_MS = 2000

    def __init__(self):
        super().__init__()
        self.setWindowTitle(i18n.t("JXL 转换器"))
        self.resize(880, 640)

        # Windows animates menus into view (slide / fade). That entrance effect
        # is the same one the combo boxes go frameless to escape: it delays the
        # popup and can flicker. Turning it off app-wide makes every menu --
        # including the custom-folder history list -- appear instantly.
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateMenu, False)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_FadeMenu, False)

        # Capture the native style name BEFORE any setStyle() call so it can be
        # restored for the "native" / "native_noflicker" themes.
        self._native_style_name = QApplication.style().objectName()
        # Also capture the original system palette. The Fusion theme overrides
        # QPalette.Accent to black; when switching back to a native theme we
        # must restore this original palette instead of using standardPalette(),
        # which can return a generic/beige palette and break native colors.
        self._original_app_palette = QApplication.palette()

        self.input_files = []   # list of absolute file paths
        # 每个输入文件对应的「根目录」：用于「保留文件夹结构」时计算相对路径。
        # 值为该文件被添加时所属文件夹（添加文件夹则为其本身，添加单文件则为其父目录）；
        # 缺失时回退到 os.path.dirname(src)。
        self.input_roots = {}
        self._convert_worker = None  # background conversion thread (or None)
        self._stop_requested = False  # True while a user-initiated stop is pending
        self._thumb_cache = {}  # (path, px) -> QImage  (线程安全；主线程 fromImage 成 QPixmap)
        self._info_cache = {}   # path -> tooltip text  (avoid re-reading files)
        self._placeholder_cache = {}  # size -> placeholder QPixmap
        # 缩略图异步解码（Phase 2）：解码在子线程线程池内完成，GUI 线程只做回填。
        self._thumb_pool = None          # QThreadPool（懒加载）
        self._thumb_timer = None         # 投递节拍器：每 tick 向线程池投一批 worker
        self._thumb_queue = []           # 待投递任务：(path, box_square, px)
        self._thumb_items = {}           # 当前列表有效映射：path -> QListWidgetItem
        self._thumb_epoch = 0            # 每次重建列表自增，作废在途 worker 的过期结果
        self._thumb_paused = False       # 拖拽/框选进行中：暂停投递新 worker
        self._thumb_inflight = 0         # 已投递、尚未回填的 worker 数
        self._thumb_results = queue.Queue()  # 子线程 -> 主线程 的结果队列
        self._thumb_drain_timer = None   # 主线程抽取结果队列的节拍器
        # 尺寸异步预取（Phase 3）：get_image_dims 不再同步解码，需解码的大图
        # 由 _DimsWorker 在子线程取尺寸，回填 _DIMS_CACHE 后刷新可见 item 的
        # tooltip 与分辨率列，彻底消除拖入大图时的主线程阻塞。
        self._dims_inflight = set()
        self._dims_queue = queue.Queue()  # 子线程 -> 主线程：解码完成的 path
        self._dims_drain_timer = None
        self._sized = False     # resize-to-fit (6x3) once, on first show
        self._env_refreshed = False  # _refresh_environment done once, after show
        self._calib_worker = None    # 后台校准线程（或 None）
        self._auto_calibrate_enabled = False  # 由 __main__ 在真实启动时置 True
        self._calib_auto_checked = False      # 首次 show 已决策是否自动校准
        self._last_seen_state = None     # 上次见到的完整电源状态三元组（轮询比对用）
        self._power_poll_timer = None     # 电源方案轮询定时器（真实启动时创建）
        self._menu_warmed = False  # folder-history popup pre-warm DONE
        self._warm_fallback_scheduled = False  # idle fallback timer armed
        # Cached window "chrome" (title bar + borders + tab bar + status bar +
        # input-tab padding) measured once on first show when the input tab is
        # visible. Constant regardless of window size, so the 6x3 fit uses it
        # instead of measuring the (possibly hidden / zero-sized) viewport.
        self._fit_frame_w = None
        self._fit_frame_h = None

        # Details-view ("详细信息") column configuration (Explorer-style):
        #   _table_hidden  -> list of HIDDEN column keys
        #   _table_sort    -> (key, ascending: bool) or None (view-only sort)
        #   _table_widths  -> key -> column width in px (persisted across rebuilds)
        #   _table_added   -> path -> timestamp when first added (操作日期)
        #   _file_meta     -> path -> lazily computed metadata cache
        #   _table_header_built -> header is built exactly once; afterwards Qt
        #                         owns the visual column order / hidden / widths.
        self._table_hidden = [
            key for (key, _l, visible, _w, _a) in TABLE_COLUMNS if not visible
        ]
        self._table_sort = None
        self._table_widths = {
            key: width for (key, _l, _v, width, _a) in TABLE_COLUMNS
        }
        self._table_added = {}
        self._file_meta = {}
        self._table_header_built = False
        # Ordered list of column keys as the user last arranged them (visual
        # order). Empty until a saved layout is loaded or the user drags a
        # header. Consumed by _build_table_header to restore the column order.
        self._table_saved_order = []

        # Restore any previously persisted column layout (widths / visibility /
        # order / sort) so "what I set in the GUI" survives a restart.
        self._load_table_layout()
        # Restore the persisted window geometry (size + position). If a saved
        # geometry exists we skip the 6x3 fit on first show so the window comes
        # back exactly where the user left it; otherwise showEvent fits + centres
        # it as the default startup.
        if self._load_geometry():
            self._sized = True
        # Performance telemetry (toggled with F9). Off by default; when on, the
        # input-list paintEvent records per-frame paint time and the FPS while a
        # rubber-band / drag is active, surfaced in the status bar so we can see
        # on the real machine where the cost actually is (headless can't render
        # thumbnails, so this is our only way to measure paint cost).
        self._perf_enabled = False
        self._perf_paints = []
        self._perf_fps = 0.0
        self._perf_drag_last = 0.0
        self._perf_status_t = 0.0
        # Output-location / filename persistence state.
        self._folder_history = []      # historical custom output folders (most recent first)
        self._output_loading = False   # guard to suppress saves while restoring
        self._conversion_loading = False  # guard for CPU-priority restore
        self._view_loading = True      # suppress view-mode saves during build + restore
        self._theme_loading = False    # suppress theme saves during build / restore
        self._init_theme()             # apply persisted theme (default 原生（无闪烁）) before UI build
        self._build_ui()
        # 关键设置（输出标签 / 输出位置 / 转换优先级）必须在首帧前就绪，否则首帧
        # 画的是不完整的输出/设置标签。经验测：把它们延后到 show 之后（singleShot）
        # 只会把这段 QSettings 读取+控件填充的耗时暴露在「show→首帧」的白屏窗口里，
        # 反而加长白屏。故默认在 __init__ 同步加载——窗口 show 时即完整，白屏最短；
        # 而纯 I/O（检测 cjxl/djxl、窗口尺寸拟合、菜单预热）仍留在 showEvent 之后
        # 延迟，不阻塞首屏。LIBJXL_NO_DEFER=1 可恢复旧的全延迟行为用于量化对比。
        if os.environ.get("LIBJXL_NO_DEFER") == "1":
            # 对比用：旧全延迟行为，关键设置留到 showEvent 之后才加载
            self._settings_loaded = False
        else:
            # 默认：关键设置在 __init__ 同步加载，窗口 show 时即完整
            # 注意：先恢复转换设置（含「启用高级参数」），让输出页 effort 可选
            # 范围（1..9 / 1..10）就绪后，再恢复 JXL 输出设置里的 effort 值，
            # 否则启用高级参数时持久化的 effort=10 会因组合框尚无该项而被丢弃。
            self._load_conversion_settings()
            self._load_jxl_output()
            self._load_output_settings()
            self._load_actions_setting()
            self._settings_loaded = True
        self._bench_enabled = os.environ.get("LIBJXL_BENCH") == "1"
        self._bench_done = False
        self._bench_show_ts = None
        self._app_start = None  # 由 __main__ 在 show 前写入（进程启动时刻）
        # Restore the persisted Input-tab "查看" view mode (after the input
        # tab is built and the default view applied during build).
        self._load_view_mode()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            # 转换进行中：ESC 不做任何操作，避免误触退出或中断正在运行的任务。
            if self._convert_worker is not None and self._convert_worker.isRunning():
                event.accept()
                return
            self.close()
            event.accept()
            return
        if event.key() == Qt.Key_F9:
            self._perf_enabled = not self._perf_enabled
            if self._perf_enabled:
                self._perf_paints = []
                self._perf_fps = 0.0
                self._perf_drag_last = 0.0
                self._perf_status_t = 0.0
                self.statusBar().showMessage(
                    i18n.t("性能监测已开启（F9 关闭）：请在缩略图视图下框选并拖到边缘触发自动翻页")
                )
            else:
                self.statusBar().showMessage(i18n.t("性能监测已关闭"))
            event.accept()
            return
        super().keyPressEvent(event)

    def _perf_record_paint(self, dt_ms, state):
        # dt_ms: paintEvent duration in milliseconds. state: the list widget's
        # current interaction state (NoState when idle, Selecting/Dragging while
        # a rubber-band or reorder is in progress).
        self._perf_paints.append(dt_ms)
        if len(self._perf_paints) > 60:
            self._perf_paints.pop(0)
        now = time.perf_counter()
        if state != QAbstractItemView.State.NoState:
            if self._perf_drag_last > 0:
                interval = now - self._perf_drag_last
                if interval > 0:
                    self._perf_fps = 1.0 / interval
            self._perf_drag_last = now
        else:
            self._perf_drag_last = 0.0
        if now - self._perf_status_t > 0.25:
            self._perf_status_t = now
            if self._perf_paints:
                avg = sum(self._perf_paints) / len(self._perf_paints)
                peak = max(self._perf_paints)
            else:
                avg = peak = 0.0
            self.statusBar().showMessage(
                i18n.t("性能监测 绘制 %.2f ms/帧 (峰值 %.2f) | 拖拽中 FPS %.0f")
                % (avg, peak, self._perf_fps)
            )

    def showEvent(self, event):
        super().showEvent(event)
        # On first show, resize + center the window so its thumbnail viewport
        # holds exactly 6 columns x 3 rows of the default "缩略图" grid, and the
        # window sits in the middle of the screen.
        if not self._sized:
            self._sized = True
            # Defer until after the initial layout so the viewport/frame sizes
            # are final and the centering math is correct.
            QTimer.singleShot(0, self._fit_window_to_grid)
        else:
            # A saved geometry was restored, so we don't re-fit — but we still
            # cache the window chrome once while the input tab (the default
            # current tab on first show) is visible, so the "一键 6×3 排版"
            # button stays correct even when clicked from another tab.
            QTimer.singleShot(0, self._cache_window_frame)
        # Detect cjxl/djxl + write the env log/status line OFF the startup
        # critical path: it is pure I/O + log/status text, the window does not
        # need it to paint its first frame. Running it after show() (deferred
        # one event-loop turn) shaves its cost off the open-to-visible time.
        if not self._env_refreshed:
            self._env_refreshed = True
            QTimer.singleShot(0, self._refresh_environment)
        # 首次启动（且真实运行，非 headless 测试）：启动电源方案轮询、检查 CPU 是否
        # 更换（更换则清空旧阈值，当作首次启动重校准）、未校准则自动跑一次。
        if self._auto_calibrate_enabled and not self._calib_auto_checked:
            self._calib_auto_checked = True
            self._start_power_poll()
            self._check_cpu_signature()  # 换硬件会清空旧数据，使下方 has_calibration 为假
            if calibrate.has_calibration():
                self._refresh_calib_value_label()
            else:
                QTimer.singleShot(self.CALIBRATION_DELAY_MS, self._auto_calibrate)
        # Restore the output / output-location / conversion-priority settings
        # OFF the startup critical path. These only touch output/settings-tab
        # widgets, which are not visible on the first-painted input tab, so
        # deferring them past the first frame causes no visible flicker while
        # shaving their QSettings reads + widget fills off the open-to-visible
        # time.
        if not self._settings_loaded:
            self._settings_loaded = True
            if os.environ.get("LIBJXL_NO_DEFER") == "1":
                # 对比用：旧全延迟行为，首帧后才加载关键设置
                QTimer.singleShot(0, self._deferred_load_settings)
        if self._bench_enabled:
            self._bench_show_ts = time.perf_counter()
        # Warm the folder-history popup OFF the startup critical path. The
        # one-off native-popup creation (HWND + drop shadow + style polish) is
        # what used to make the first click stutter -- and what moving it to
        # "right after show" turned into a startup stutter. Instead we trigger
        # it on first hover of the dropdown arrow (idle, just before a likely
        # click, via the button's Enter event filter), with a delayed idle
        # fallback so it still happens even if the user never hovers. Startup
        # itself stays instant: the cost never lands on the open/show path.
        if not self._menu_warmed and not self._warm_fallback_scheduled:
            self._warm_fallback_scheduled = True
            # Short idle fallback for the no-hover case (e.g. keyboard open):
            # well after the window has painted, so it never reads as startup.
            QTimer.singleShot(600, self._maybe_prewarm)

    def paintEvent(self, event):
        """白屏基准测试：记录 show -> 首帧的时长并打印（仅 LIBJXL_BENCH=1）。

        这段时长即 DWM 本机擦除白帧持续的可观测代理：窗口可见到 Qt 画出
        第一帧之间，用户看到的就是白屏。
        """
        super().paintEvent(event)
        if self._bench_enabled and not self._bench_done:
            self._bench_done = True
            now = time.perf_counter()
            show_ts = self._bench_show_ts or now
            start = self._app_start or show_ts
            if os.environ.get("LIBJXL_NO_DEFER") == "1":
                mode = "全延迟(旧V1)"
            else:
                mode = "同步关键设置(默认)"
            print(
                f"[bench] 模式={mode} "
                f"| 启动→首帧={now - start:.3f}s | 白屏(show→首帧)={now - show_ts:.3f}s",
                flush=True,
            )

    def _deferred_load_settings(self):
        """首屏之后恢复输出标签 / 输出位置 / 转换优先级的持久化设置。

        这三者只写输出标签与设置标签的控件，首屏默认显示输入标签，用户
        看不到任何跳变，因此延后到窗口首帧之后执行不会造成可见闪烁，却能把
        这部分 QSettings 读取 + 控件填充从启动关键路径上移走，缩短白屏到
        首帧的时长。
        """
        self._load_conversion_settings()
        self._load_jxl_output()
        self._load_output_settings()
        self._load_actions_setting()
        # 「切换预览源后自动适应窗口」勾选状态（默认启用）。
        self._load_fit_on_source_change()
        # 恢复完动作列表后再统一刷一次标题计数：无动作 / 无输入时也要显示
        # 「输入 [0个]」「动作 [0/0]」，而不是光秃秃的「输入」「动作」。
        self._update_tab_titles()

    def _warm_now(self):
        """Warm the popup right now, exactly once.

        Builds the native popup off-screen synchronously. Called from the
        drop-down arrow's hover-Enter so the work lands on the hover moment
        (a barely-noticeable hitch while the cursor sits on the button) rather
        than on the click itself -- the click then opens an already-built
        popup and feels instant even if the user rushes the button straight
        after launch."""
        if self._menu_warmed:
            return
        self._menu_warmed = True
        self._prewarm_folder_menu()

    def _maybe_prewarm(self):
        """Idle-fallback gate for the one-off popup warm-up.

        Used only when the user never hovers the arrow (e.g. keyboard-driven
        open). Defers one event-loop turn so the triggering timer callback
        returns before we build the native popup window."""
        if self._menu_warmed:
            return
        QTimer.singleShot(0, self._warm_now)

    def eventFilter(self, obj, event):
        # Warm the folder-history popup the instant the cursor ENTERS the
        # dropdown arrow -- synchronously, before the click. This keeps the
        # first click fast even if the user rushes the button straight after
        # launch, without paying the cost at application startup.
        if obj is self.custom_folder_dropdown and event.type() == QEvent.Enter:
            self._warm_now()
        return super().eventFilter(obj, event)

    def _prewarm_folder_menu(self):
        """Make the FIRST click on the custom-folder arrow as fast as the rest.

        The first time a QMenu is shown, Qt/Windows has to style-polish the
        menu and every row widget, parse their style sheets, run the layout,
        create the native popup window (HWND + drop shadow) and allocate its
        backing store. All of that landed on the first click, which is why it
        felt sluggish once and was instant afterwards. We run the same work
        here -- triggered on first hover of the drop-down arrow (or, if the
        user never hovers, by a 1.5 s idle fallback) -- so the cost is paid
        during a natural pre-click idle moment and NOT at application startup.
        Nothing is ever put on screen: WA_DontShowOnScreen suppresses the
        mapping, so the show/hide pair only triggers the polish and layout,
        and winId() then creates the native popup up front.
        """
        menu = getattr(self, "folder_menu", None)
        if menu is None:
            return
        try:
            menu.setAttribute(Qt.WA_DontShowOnScreen, True)
            try:
                menu.show()
                menu.hide()
            finally:
                menu.setAttribute(Qt.WA_DontShowOnScreen, False)
            menu.ensurePolished()
            menu.adjustSize()
            menu.winId()
        except Exception:
            # A warm-up must never break the UI: worst case the first popup is
            # simply as slow as it used to be.
            pass

    def _cache_window_frame(self):
        """Measure and cache the window chrome (frame) once, using the input
        list's viewport while it is visible. The chrome is constant regardless
        of window size, so the 6x3 fit can rely on this cached value instead of
        re-measuring a hidden/zero-sized viewport later."""
        vp = self.input_list.viewport()
        if vp is not None and vp.width() > 0 and vp.height() > 0:
            self._fit_frame_w = self.width() - vp.width()
            self._fit_frame_h = self.height() - vp.height()

    def _fit_window_to_grid(self, center=True):
        """Resize the window so its thumbnail viewport holds exactly 6x3 of the
        default "缩略图" grid.

        ``center=True`` (default-startup behaviour, also used on first show)
        moves the window to the centre of the primary screen. ``center=False``
        keeps the current position and only changes the size — this is what the
        "一键 6×3 排版" button wants ("restore the default startup size" without
        relocating the window).
        """
        COLS, ROWS = 6, 3
        grid = GRID_SIZES.get("缩略图", QSize(122, 154))
        lst = self.input_list
        vp = lst.viewport()
        if vp is None:
            return
        # The window chrome INSIDE the client area (tab widget + bottom bar +
        # margins) is a CONSTANT that does NOT depend on the window size. It was
        # cached once at first show (input tab visible) via _cache_window_frame,
        # and the very first fit call below also seeds it. CRITICALLY: never
        # re-measure it here against the *current* (already-fitted) window — that
        # would create a feedback loop that grows w/h on every click.
        if self._fit_frame_w is None or self._fit_frame_w <= 0 \
                or self._fit_frame_h is None or self._fit_frame_h <= 0:
            # Seed just-in-time, but ONLY if the viewport is currently visible
            # (non-zero). If it's hidden (another tab active) we have no reliable
            # value, so bail and keep the existing geometry instead of guessing.
            if vp.width() > 0 and vp.height() > 0:
                self._fit_frame_w = self.width() - vp.width()
                self._fit_frame_h = self.height() - vp.height()
            else:
                # The view isn't laid out yet. This happens when run() calls
                # app.processEvents() right after show(): the QTimer.singleShot(0)
                # that triggers this fit fires INSIDE that processEvents(), BEFORE
                # the first real layout/paint, so the viewport is still 0x0. Don't
                # bail permanently (that would strand the window at the wrong
                # size) -- retry on the next event-loop turn, where the view is
                # sized, so we still land on the correct 6x3. Cap retries so a
                # never-sized view can't loop forever.
                self._fit_retry = getattr(self, "_fit_retry", 0) + 1
                if self._fit_retry <= 30:
                    QTimer.singleShot(0, lambda: self._fit_window_to_grid(center))
                return
        frame_w = self._fit_frame_w
        frame_h = self._fit_frame_h
        # Reserve the scrollbar thickness so the 6x3 grid stays visible even
        # after files are added and a scrollbar appears (otherwise the scrollbar
        # gutter would eat one column and we'd only fit 5).
        # clamp the reserved scrollbar thickness to a sane minimum in case
        # sizeHint() returns 0 (some styles) — otherwise the gutter would eat
        # the last column even after we add FIT_EXTRA_W.
        sbw = max(lst.verticalScrollBar().sizeHint().width(), 17)
        sbh = max(lst.horizontalScrollBar().sizeHint().height(), 17)
        vp_w = COLS * grid.width() + sbw + FIT_EXTRA_W
        vp_h = ROWS * grid.height() + sbh + _fit_extra_h()
        w = max(self.minimumWidth(), vp_w + frame_w)    # target CLIENT width
        h = max(self.minimumHeight(), vp_h + frame_h)   # target CLIENT height

        # COORDINATE-SPACE DISAMBIGUATION (this is what killed the "drift up"
        # bug): On Windows, x()/y()/frameGeometry()/move() work in the OUTER
        # frame space (including the title bar + borders), while geometry()/
        # width()/height()/resize()/setGeometry() work in the INNER client space.
        # Feeding frame coords into setGeometry() (which expects client coords)
        # made the window creep up and left by the decoration thickness on every
        # call. So we resize() (client) and move() (frame) SEPARATELY, and
        # convert sizes via the current frame surplus so centring/clamping is
        # pixel-accurate in the outer space.
        fg = self.frameGeometry()
        surplus_w = fg.width() - self.width()
        surplus_h = fg.height() - self.height()
        outer_w = w + surplus_w
        outer_h = h + surplus_h

        screen = QApplication.primaryScreen()
        if center and screen is not None:
            sg = screen.availableGeometry()
            x = sg.x() + max(0, (sg.width() - outer_w) // 2)
            y = sg.y() + max(0, (sg.height() - outer_h) // 2)
        else:
            # "一键 6×3 排版": keep the current FRAME position (move() uses frame
            # coords), only resize. If the 6x3 window would spill off the screen,
            # clamp it inside the usable area so the OS never re-floats it (which
            # would drift it upward on repeated clicks). Clamping a fixed target
            # is deterministic, so it converges instead of accumulating.
            x, y = self.x(), self.y()
            if screen is not None:
                sg = screen.availableGeometry()
                x = max(sg.x(), min(x, sg.x() + max(0, sg.width() - outer_w)))
                y = max(sg.y(), min(y, sg.y() + max(0, sg.height() - outer_h)))
        # resize() (client size) first, then move() (frame position) — never
        # setGeometry(), whose mixed coordinate spaces caused the drift.
        self.resize(w, h)
        self.move(x, y)

    # ------------------------------------------------------------------
    # UI construction (all visible strings are Chinese)
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        # 根布局左/上/右边距统一为 ROOT_MARGIN_LTR(9px)；底边距按主题区分：
        # 原生（含原生无闪烁）用 2px，Fusion 用 3px（Fusion 边框更紧，多 1px 校正）。
        root.setContentsMargins(
            ROOT_MARGIN_LTR, ROOT_MARGIN_LTR, ROOT_MARGIN_LTR,
            _root_bottom_margin())

        self.tabs = QTabWidget()
        self.input_tab = self._build_input_tab()
        # 记录索引，供 _update_tab_titles 按索引刷新标题上的数量统计。
        self._input_tab_index = self.tabs.addTab(self.input_tab, i18n.t("输入"))
        self._actions_tab_index = self.tabs.addTab(self._build_actions_tab(), i18n.t("动作"))
        self.tabs.addTab(self._build_output_tab(), i18n.t("输出"))
        self.status_tab = self._build_status_tab()
        self.tabs.addTab(self.status_tab, i18n.t("状态"))
        self.settings_tab = self._build_settings_tab()
        self.tabs.addTab(self.settings_tab, i18n.t("设置"))
        root.addWidget(self.tabs, stretch=1)

        # Persistent bottom bar: 转换 (left) + 停止 + 关闭 (right).
        bottom = QHBoxLayout()
        self.convert_button = QPushButton(i18n.t("转换"))
        self.convert_button.setMinimumHeight(34)
        self.convert_button.clicked.connect(self._on_convert)
        self.stop_button = QPushButton(i18n.t("停止"))
        self.stop_button.setMinimumHeight(34)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_convert_stop)
        self.close_button = QPushButton(i18n.t("关闭"))
        self.close_button.setMinimumHeight(34)
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.convert_button)
        bottom.addWidget(self.stop_button)
        bottom.addStretch(1)
        bottom.addWidget(self.close_button)
        root.addLayout(bottom, stretch=0)

        self.statusBar().showMessage(i18n.t("就绪"))
        self.setAcceptDrops(True)

        # Radio buttons and checkboxes keep their selected (blue) colour even
        # when the window loses focus (see _sync_inactive_palette).
        self._sync_inactive_palette()

    def _build_input_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        toolbar = QHBoxLayout()
        self.add_files_button = QPushButton(i18n.t("添加文件"))
        self.add_folder_button = QPushButton(i18n.t("添加文件夹"))
        self.remove_button = QPushButton(i18n.t("移除"))
        self.clear_button = QPushButton(i18n.t("清空"))
        for button in (
            self.add_files_button, self.add_folder_button,
            self.remove_button, self.clear_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)

        # Filter bar: drop-down button (left) + text box "快速过滤" (right).
        self.filter_button = QToolButton()
        self.filter_button.setText(i18n.t("过滤"))
        filter_menu = QMenu(self.filter_button)
        filter_menu.addAction(
            i18n.t("移除已过滤的"), lambda: self._on_remove_filtered("filtered")
        )
        filter_menu.addAction(
            i18n.t("移除未过滤的"), lambda: self._on_remove_filtered("unfiltered")
        )
        self.filter_button.setMenu(filter_menu)
        self.filter_button.setPopupMode(QToolButton.InstantPopup)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(i18n.t("快速过滤"))
        self.filter_edit.setFixedWidth(150)
        self.filter_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_button)
        toolbar.addWidget(self.filter_edit)

        # View switcher (rightmost of the toolbar).
        # Use a QToolButton + QMenu instead of QComboBox: QComboBox's popup has
        # an intermittent first-click race on Windows (the first pick is
        # sometimes swallowed until a second click), so the view appeared to
        # need two clicks to switch. QMenu.triggered fires reliably on every
        # menu-item click, eliminating that race entirely.
        self.view_button = QToolButton()
        toolbar.addWidget(QLabel(i18n.t("查看：")))
        self.view_button.setText(i18n.t("缩略图"))
        self.view_button.setPopupMode(QToolButton.InstantPopup)
        self.view_menu = QMenu(self.view_button)
        for mode in VIEW_MODES:
            # 菜单项显示译文，回调仍传原始中文 ID —— 持久化、GRID_SIZES 查表、
            # "列表"/"详细信息" 分支判断全部依赖 ID，不能被翻译影响。
            act = self.view_menu.addAction(i18n.t(mode))
            act.triggered.connect(
                lambda _checked=False, m=mode: self._on_view_changed(m)
            )
        self.view_button.setMenu(self.view_menu)
        toolbar.addWidget(self.view_button)

        layout.addLayout(toolbar)

        # Body: a stacked widget holding the details table and the list/thumb view.
        self.input_stack = QStackedWidget()
        self.input_table = InputTableWidget(self)
        self.input_list = InputListWidget(self)
        self.list_delegate = ThumbnailDelegate(self.input_list)
        self.input_list.setItemDelegate(self.list_delegate)
        self.input_stack.addWidget(self.input_table)
        self.input_stack.addWidget(self.input_list)
        layout.addWidget(self.input_stack, stretch=1)

        self.add_files_button.clicked.connect(self._on_add_files)
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.remove_button.clicked.connect(self._on_remove_selected)
        self.clear_button.clicked.connect(self._on_clear_inputs)

        # Double-click opens a preview.
        self.input_list.itemDoubleClicked.connect(
            lambda item: self._open_preview(item.data(Qt.UserRole))
        )
        self.input_table.itemDoubleClicked.connect(
            lambda item: self._open_preview(item.data(Qt.UserRole))
        )
        # Apply the default view (缩略图). setCurrentText() above fired its
        # signal before this connection existed, so call it explicitly now that
        # the list/stack widgets are built.
        self._on_view_changed("缩略图")
        return widget

    def _build_actions_tab(self):
        self.actions_tab = widget = QWidget()
        layout = QVBoxLayout(widget)
        splitter = QSplitter(Qt.Horizontal)

        # --- Left: action controls + ordered action list -----------------
        left = QWidget()
        left_layout = QVBoxLayout(left)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(i18n.t("动作类型：")))
        self.action_combo = NoFlickerComboBox()
        # 显示名走 t()，userData 存原始中文 ID —— ID 与显示名分离后，
        # 切英文界面时下拉显示 "Resize"，而 action dict 里仍是 "调整大小"，
        # processor.apply_actions 那 40 处字面量判断和已有配置都不受影响。
        for _aid in processor.ACTION_TYPES:
            self.action_combo.addItem(i18n.t(_aid), _aid)
        self.add_action_button = QPushButton(i18n.t("添加动作"))
        self.clear_action_button = QPushButton(i18n.t("清空"))
        toolbar.addWidget(self.action_combo)
        toolbar.addWidget(self.add_action_button)
        toolbar.addWidget(self.clear_action_button)
        toolbar.addStretch(1)
        left_layout.addLayout(toolbar)

        # Each action is a two-row item (summary + 上移/下移/移除 buttons),
        # built via setItemWidget so the buttons live on the item itself.
        # Delete/Backspace removes the selected action items.
        self.action_list = ActionListWidget(self)
        left_layout.addWidget(self.action_list, stretch=1)
        splitter.addWidget(left)

        # --- Right: live preview of the added actions --------------------
        right = QGroupBox(i18n.t("预览"))
        right_layout = QVBoxLayout(right)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel(i18n.t("预览源：")))
        self.preview_source_combo = NoFlickerComboBox()
        self.preview_source_combo.setMinimumWidth(160)
        src_row.addWidget(self.preview_source_combo, stretch=1)
        right_layout.addLayout(src_row)

        # Preview toolbar: zoom in / out, 1:1, fit-to-window, toggle original.
        # The buttons use an Ignored horizontal policy so they shrink (instead
        # of overflowing / clipping) when the panel is narrow (e.g. at the
        # default 6x3 window width).
        pbar = QHBoxLayout()
        self.zoom_in_button = QPushButton(i18n.t("放大"))
        self.zoom_out_button = QPushButton(i18n.t("缩小"))
        self.zoom_actual_button = QPushButton("1:1")
        self.zoom_fit_button = QPushButton(i18n.t("适应窗口"))
        self.show_original_button = QPushButton(i18n.t("显示原图"))
        for b in (
            self.zoom_in_button, self.zoom_out_button, self.zoom_actual_button,
            self.zoom_fit_button, self.show_original_button,
        ):
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            pbar.addWidget(b)
        right_layout.addLayout(pbar)

        self.preview_view = PreviewScroll(self)
        right_layout.addWidget(self.preview_view, stretch=1)

        self.preview_msg = QLabel(
            i18n.t("请先在「输入」标签添加图片，\n再在此处预览动作效果。")
        )
        self.preview_msg.setAlignment(Qt.AlignCenter)
        # 错误信息可能很长（含 PIL 抛出的完整文件路径），必须约束它的尺寸
        # 策略和最大高度，否则 QLabel 会强制把整个主窗体撑得过高/过宽。
        # Preferred × Maximum 让它可以水平伸缩但垂直方向不抢空间；
        # setMaximumHeight 给一个硬上限，超过该高度就触发竖向滚动条。
        self.preview_msg.setWordWrap(True)
        self.preview_msg.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.preview_msg.setMaximumHeight(120)
        # 把 preview_msg 包进一个带上下 stretch 的容器，让它垂直居中于预览区；
        # 否则在 QVBoxLayout 里它会和底部的 hint 一起被推到顶部（"往上飘"）。
        # 容器自身用 stretch=1 占满中间区域，并在下方 4 处显隐切换点手动同步
        # 可见性——它与 preview_view 互斥，避免抢 stretch 空间。
        self.preview_msg_container = QWidget()
        msg_container_layout = QVBoxLayout(self.preview_msg_container)
        msg_container_layout.setContentsMargins(0, 0, 0, 0)
        msg_container_layout.addStretch(1)
        msg_container_layout.addWidget(self.preview_msg)
        msg_container_layout.addStretch(1)
        right_layout.addWidget(self.preview_msg_container, stretch=1)

        # 「切换预览源后自动适应窗口」复选框：控制切换预览源时是否 fit 到窗口。
        # 取消勾选后，切源沿用当前缩放位置（便于对比多张图的同一局部）。
        # 默认启用（保留「切换预览源后自动适应窗口」的既有行为）。
        self.fit_on_source_change_check = QCheckBox(i18n.t("切换预览源后自动适应窗口"))
        self.fit_on_source_change_check.setChecked(True)
        # 不设样式：保留与默认 QCheckBox 一致的外观（颜色/字号同系统控件）
        self.fit_on_source_change_check.setToolTip(
            i18n.t("取消勾选后，切换预览源时沿用当前缩放位置，不被重置到适应窗口。")
        )
        # 勾选状态即时持久化（toggled 首参是 checked，无需 _checked 守卫）。
        self.fit_on_source_change_check.toggled.connect(
            lambda _checked: self._save_fit_on_source_change())
        right_layout.addWidget(self.fit_on_source_change_check)

        splitter.addWidget(right)
        # Default ratio: the preview area takes 4/7 of the width (left 3 /
        # right 4). QSplitter already allows *continuous* (stepless) dragging of
        # the divider to any proportion; setChildrenCollapsible(False) just
        # guarantees neither panel can be dragged all the way to zero.
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, stretch=1)

        # Preview state (toggled by the 显示原图 button) and initial visibility:
        # no input yet, so the message is shown instead of the (empty) canvas.
        self._preview_mode = "processed"
        self._preview_original_pixmap = None
        self._preview_processed_pixmap = None
        self.preview_view.setVisible(False)
        # 动作页预览异步状态：解码（djxl/Pillow）+ apply_actions 全部在子线程
        # 完成，主线程只做 QPixmap.fromImage 回填，杜绝大图切换标签/选源时 GUI
        # 冻结（与输入页缩略图/尺寸卡顿同源）。epoch 令牌丢弃过期结果。
        self._action_preview_epoch = 0
        self._action_preview_queue = queue.Queue()
        self._action_preview_drain = None
        self._action_preview_busy = False
        # 延迟显示「预览加载中…」的定时器（见 _PREVIEW_LOADING_HINT_DELAY）。
        self._preview_loading_timer = None
        # 布局刚发生变化（预览视图由隐藏转可见）时为 True：此时 viewport 尺寸
        # 待重算，fit 必须推迟一拍；布局未变时 viewport 稳定，可同步 fit 以
        # 避免「新图 + 上一张的 transform」中间帧被绘制出来（切图闪烁次因）。
        self._preview_fit_deferred = False
        # inline 参数控件变化时的防抖：拖动 spinbox 时每个值都重渲染会卡，
        # 250ms 内的连续变化合并为一次预览刷新。
        self._param_change_timer = None

        self.zoom_in_button.clicked.connect(
            lambda: self.preview_view.zoom(1.2))
        self.zoom_out_button.clicked.connect(
            lambda: self.preview_view.zoom(1 / 1.2))
        self.zoom_actual_button.clicked.connect(self._preview_actual)
        self.zoom_fit_button.clicked.connect(self.preview_view.fit)
        # Press-and-hold: press to peek the original (un-processed) image,
        # release to return to the processed (action-applied) result. This is
        # more intuitive than a click-toggle for a quick before/after glance.
        self.show_original_button.pressed.connect(self._preview_show_original)
        self.show_original_button.released.connect(self._preview_show_processed)

        self.add_action_button.clicked.connect(self._on_add_action)
        self.clear_action_button.clicked.connect(self._on_clear_actions)
        # ⚠️ currentIndexChanged 会带 index(int) 实参；_render_action_preview
        # 的 fit 参数会把它接住 —— 切到 index=0（向上切到第一项）时
        # fit=0 → bool(0)=False → 不 fit；切到 index>0 才 fit。这正是
        # 「向上切不自动适应窗口、向下切会」的根因。用 lambda 吞掉 index。
        # fit 值改由「切换预览源后自动适应窗口」复选框决定（默认启用）。
        self.preview_source_combo.currentIndexChanged.connect(
            lambda _index=0: self._render_action_preview(
                fit=self._fit_on_source_change())
        )
        # Refresh the preview whenever the user jumps back to this tab (so it
        # also picks up input files added while on another tab).
        self.tabs.currentChanged.connect(self._on_tab_changed)
        return widget

    def _build_output_tab(self):
        # 整页包进 QScrollArea：高级参数展开时只出现滚动条，主窗口尺寸不被撑大。
        # 关键：滚动区/内容容器/分组框全部保持透明，直接透出 QTabWidget 的面板
        # 底色——与其它标签页一致，且自动跟随系统深浅色主题（不写死任何颜色、
        # 不强制 palette 角色，避免"切换主题后输出页不变色"的冻结 bug）。
        widget = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 横向滚动条改为「按需出现」：原先 AlwaysOff 会让超宽内容的右侧被
        # 直接截断且无法滚动查看。英文译文比中文长 30~60%，在默认 880 宽的
        # 窗口下极易超宽。AsNeeded 保证正常宽度下不出现滚动条（观感不变），
        # 只有真的放不下时才出现，用作永不截断的兜底。
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        self.output_scroll = scroll
        inner = QWidget()
        inner.setAutoFillBackground(False)
        layout = QVBoxLayout(inner)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel(i18n.t("输出格式：")))
        # NoFlickerComboBox：普通 QComboBox 行为（框更大、支持鼠标滚轮），
        # 但下拉弹窗去掉 Windows DWM 入场动画，避免展开时闪烁。
        self.format_combo = NoFlickerComboBox()
        self.format_combo.addItems(
            ["JPEG XL (*.jxl)", "PNG (*.png)", "JPEG (*.jpg)"]
        )
        fmt_row.addWidget(self.format_combo)
        # 选择 JPEG 输出时，在下拉框右侧提醒：仅支持「无损 JPEG 转码的 JXL
        # 重建 JPG」，而非任意 JXL。默认隐藏，选中时由 _update_format_hint 显示。
        self.format_hint_label = QLabel(i18n.t("仅支持无损 JPEG 转码的 JXL 无损重建 JPG"))
        self.format_hint_label.setVisible(False)
        self.format_hint_label.setStyleSheet("color: #888; font-size: 11px;")
        fmt_row.addWidget(self.format_hint_label)
        # 切换输出格式时立即刷新命令预览与提示（无需手动点「自定义命令」）。
        self.format_combo.currentTextChanged.connect(
            lambda _=None: (
                self._save_jxl_output(),
                self._update_cmd_preview(),
                self._update_format_hint(),
                self._update_discard_checkbox_state(),
            )
        )
        fmt_row.addStretch(1)
        layout.addLayout(fmt_row)

        # ---- JXL 编码参数（仅输出 JPEG XL 时生效；输出 PNG / JPEG 时走解码）----
        enc_group = QGroupBox(i18n.t("JXL 编码参数"))
        enc_group.setAutoFillBackground(False)
        enc_layout = QVBoxLayout(enc_group)

        # 编码模式：有损 / 无损 / JPG 无损重编码（互斥单选）。
        #
        # 注意：effort（--effort，1-9，默认 7）原本放在本行右侧，但英文译文
        # 比中文长 30~60%，三个单选（尤其 "JPG lossless re-encode
        # (--lossless_jpeg=1)"）与 effort 标签挤在同一行时，整行需要 811px，
        # 而默认窗口仅 880（可视宽约 810~830），超出部分因输出页关闭了横向
        # 滚动条而被直接截断。故把 effort 下移到质量行右侧：两行各自变窄，
        # 整页宽度需求由 max(两行) 决定，显著下降。
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(i18n.t("编码模式：")))
        self.lossy_radio = QRadioButton(i18n.t("有损"))
        self.lossless_radio = QRadioButton(i18n.t("无损"))
        self.lossless_jpeg_radio = QRadioButton(i18n.t("JPG 无损重编码 (--lossless_jpeg=1)"))
        self.lossy_radio.setChecked(True)
        self.encode_mode_group = QButtonGroup(self)
        self.encode_mode_group.addButton(self.lossy_radio)
        self.encode_mode_group.addButton(self.lossless_radio)
        self.encode_mode_group.addButton(self.lossless_jpeg_radio)
        mode_row.addWidget(self.lossy_radio)
        mode_row.addWidget(self.lossless_radio)
        mode_row.addWidget(self.lossless_jpeg_radio)
        mode_row.addStretch(1)
        enc_layout.addLayout(mode_row)

        # 质量滑块（--quality，0-100，默认 90）：仅「有损」模式可用。
        # 右侧用 QSpinBox 显示数值，支持键盘输入与鼠标上下箭头微调。
        # effort 与本行同行（见上方 mode_row 注释：为压低整页宽度需求而下移）。
        qual_row = QHBoxLayout()
        qual_row.addWidget(QLabel(i18n.t("质量 (--quality)：")))
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(0, 100)
        self.quality_slider.setValue(90)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 100)
        self.quality_spin.setValue(90)
        self.quality_slider.valueChanged.connect(self.quality_spin.setValue)
        self.quality_spin.valueChanged.connect(self.quality_slider.setValue)
        qual_row.addWidget(self.quality_slider, stretch=1)
        qual_row.addWidget(self.quality_spin)
        qual_row.addSpacing(16)
        qual_row.addWidget(QLabel(i18n.t("速度/质量权衡 (--effort)：")))
        self.effort_combo = NoFlickerComboBox()
        self.effort_combo.addItems([str(i) for i in range(1, 10)])
        self.effort_combo.setCurrentText("7")
        qual_row.addWidget(self.effort_combo)
        enc_layout.addLayout(qual_row)

        # ---- 高级参数（可折叠分组，默认收起；基础参数一律不动）----
        self._build_advanced_ui(enc_layout)

        # 切换模式：非「有损」时禁用质量控件，但保留其显示值，以便切回时沿用。
        # Use each radio's toggled signal (fires on both user clicks and
        # programmatic setChecked) so the enable/disable state is always correct.
        self.lossy_radio.toggled.connect(
            lambda _=None: self._on_encode_mode_changed()
        )
        self.lossless_radio.toggled.connect(
            lambda _=None: self._on_encode_mode_changed()
        )
        self.lossless_jpeg_radio.toggled.connect(
            lambda _=None: self._on_encode_mode_changed()
        )
        # Persist any manual change to quality / effort immediately, and keep
        # the command preview (when not in custom-command mode) in sync.
        self.quality_spin.valueChanged.connect(
            lambda _=None: (self._save_jxl_output(), self._update_cmd_preview())
        )
        self.effort_combo.currentTextChanged.connect(
            lambda _=None: (self._save_jxl_output(), self._update_cmd_preview())
        )
        layout.addWidget(enc_group)

        dest_group = QGroupBox(i18n.t("输出位置"))
        dest_group.setAutoFillBackground(False)
        dest_layout = QVBoxLayout(dest_group)
        self.same_folder_radio = QRadioButton(i18n.t("原文件夹"))
        self.custom_folder_radio = QRadioButton(i18n.t("文件夹"))
        self.same_folder_radio.setChecked(True)
        self.dest_group = QButtonGroup(self)
        self.dest_group.addButton(self.same_folder_radio)
        self.dest_group.addButton(self.custom_folder_radio)
        dest_layout.addWidget(self.same_folder_radio)

        custom_row = QHBoxLayout()
        # Editable field + a dropdown arrow that opens a QMenu listing the
        # folder history with a per-row "✕" delete. The edit and the arrow are
        # laid out with no gap and share one rounded border so they look like a
        # single QComboBox. The list itself is a plain QMenu (Fusion-styled, no
        # Windows popup animation / flicker) opened manually on click and
        # anchored to the field's bottom-left (see _open_folder_menu) — using
        # setMenu() would render the button as a split button (double arrow)
        # and override our positioning.
        self._folder_history = []
        self.custom_folder_edit = QLineEdit()
        self.custom_folder_edit.setPlaceholderText(
            i18n.t("选择或输入自定义输出文件夹，下拉可查看历史路径")
        )
        self.custom_folder_edit.setEnabled(False)
        self.folder_menu = FolderMenu(self)
        self._apply_theme_to_folder_menu()
        self.custom_folder_dropdown = QToolButton()
        self.custom_folder_dropdown.setArrowType(Qt.DownArrow)
        self.custom_folder_dropdown.setFixedWidth(22)
        self.custom_folder_dropdown.setEnabled(False)
        self.custom_folder_dropdown.clicked.connect(self._open_folder_menu)
        # Warm the popup on first hover (see MainWindow.eventFilter), so the
        # one-off native-window creation happens just before a likely click
        # rather than during application startup.
        self.custom_folder_dropdown.setMouseTracking(True)
        self.custom_folder_dropdown.installEventFilter(self)
        # Equal height: stretch both to the row height so the arrow button
        # lines up with the editable field (native controls otherwise pick
        # their own heights and look misaligned).
        self.custom_folder_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.custom_folder_dropdown.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._rebuild_folder_menu()
        # 浏览按钮改为紧凑省略号「…」小方块，与旁边的「自定义文件夹下拉箭头」
        # 按钮（custom_folder_dropdown，宽 22）做成等宽按钮对。宽度直接对齐下拉
        # 按钮（Fixed 策略 + 同宽），标准省略号 U+2026 仅约 12px，22px 内完整显示。
        # 用 QToolButton 而非 QPushButton：旁边下拉箭头本就是 QToolButton，二者同型
        # 才能保证 22px 在 Fusion 与原生下都完整显示「…」不裁切——QPushButton 的
        # 原生内容内边距比 QToolButton 大，22px 下原生会轻微裁切。文字失去「浏览」
        # 语义，补 tooltip 说明用途；setToolButtonStyle(TextOnly) 确保纯文字显示。
        browse_text = "\u2026"
        self.browse_folder_button = QToolButton()
        self.browse_folder_button.setText(browse_text)
        self.browse_folder_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.browse_folder_button.setEnabled(False)
        self.browse_folder_button.setToolTip(i18n.t("浏览文件夹..."))
        self.browse_folder_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.browse_folder_button.setFixedWidth(self.custom_folder_dropdown.width())
        # Native controls: a plain QLineEdit next to a plain QToolButton arrow.
        # No custom border / container / stylesheet -- the pair follows the
        # system light/dark theme automatically (no white-on-white, no inverted
        # colours). They sit with NO gap (setSpacing(0)) so they read as one
        # unit, like a native combo box; the arrow is simply a native button
        # attached to the field. The menu opens on click -- NOT via setMenu(),
        # which would render a split "double-arrow" button. A small gap is kept
        # before the separate "浏览..." button.
        custom_row.setSpacing(0)
        custom_row.addWidget(self.custom_folder_edit, stretch=1)
        custom_row.addWidget(self.custom_folder_dropdown)
        custom_row.addSpacing(6)
        custom_row.addWidget(self.browse_folder_button)
        # 把输入框上移到「自定义文件夹」单选按钮同一行：左侧只占按钮自身宽度
        # （shortened），输入框右侧（箭头 + 6px 间隙 + 浏览按钮）布局与间隙保持原样。
        custom_line = QHBoxLayout()
        custom_line.setSpacing(8)
        custom_line.addWidget(self.custom_folder_radio)
        custom_line.addLayout(custom_row, stretch=1)
        dest_layout.addLayout(custom_line)

        self.custom_folder_radio.toggled.connect(
            lambda checked: (
                self.custom_folder_edit.setEnabled(checked),
                self.custom_folder_dropdown.setEnabled(checked),
                self.browse_folder_button.setEnabled(checked),
                self._update_structure_checkbox_state(),
            )
        )
        self.browse_folder_button.clicked.connect(self._on_browse_folder)
        self.custom_folder_edit.textChanged.connect(
            lambda _=None: self._on_custom_folder_changed()
        )
        # 输出位置 + 文件名 水平并排：文件名区域移到输出位置框右侧，
        # 输出位置框不再占满整行（向左压缩），为右侧文件名区域腾出空间。
        dest_name_row = QHBoxLayout()
        dest_name_row.setSpacing(12)
        dest_name_row.addWidget(dest_group, stretch=1)

        name_group = QGroupBox(i18n.t("文件名"))
        name_group.setAutoFillBackground(False)
        # 文件名区域只需紧凑容纳「保持原文件名 / 添加后缀：_converted」两行，
        # 不抢占输出位置框的水平空间。
        name_group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        name_layout = QVBoxLayout(name_group)
        self.keep_name_radio = QRadioButton(i18n.t("保持原文件名"))
        self.add_suffix_radio = QRadioButton(i18n.t("添加后缀："))
        self.keep_name_radio.setChecked(True)
        self.name_group = QButtonGroup(self)
        self.name_group.addButton(self.keep_name_radio)
        self.name_group.addButton(self.add_suffix_radio)

        suffix_row = QHBoxLayout()
        suffix_row.addWidget(self.add_suffix_radio)
        self.suffix_edit = QLineEdit("_converted")
        self.suffix_edit.setEnabled(False)
        suffix_row.addWidget(self.suffix_edit)
        suffix_row.addStretch(1)

        name_layout.addWidget(self.keep_name_radio)
        name_layout.addLayout(suffix_row)
        self.add_suffix_radio.toggled.connect(
            lambda checked: self.suffix_edit.setEnabled(checked)
        )
        # 文件名区域加入水平并排行（位于输出位置框右侧），不 stretch，
        # 仅按自身内容宽度显示，剩余空间全给输出位置框。
        dest_name_row.addWidget(name_group)
        layout.addLayout(dest_name_row)

        # ---- 选项（输出文件已存在时的冲突策略）----
        options_group = QGroupBox(i18n.t("选项"))
        options_group.setAutoFillBackground(False)
        options_layout = QVBoxLayout(options_group)
        exist_row = QHBoxLayout()
        exist_row.addWidget(QLabel(i18n.t("当输出文件已经存在时：")))
        # NoFlickerComboBox：与输出格式下拉保持一致（框更大、支持滚轮），
        # 下拉弹窗去掉 Windows DWM 入场动画避免闪烁。
        self.on_exist_combo = NoFlickerComboBox()
        # 与动作类型同构：显示 t(策略)，userData 存原始中文策略。
        # 持久化与转换时的分支判断都读 currentData()，英文界面下不会把
        # "Overwrite" 当成策略存进 ini（否则下次启动 findData 落空、策略丢默认）。
        for _eid in ("替换", "询问", "跳过", "重命名"):
            self.on_exist_combo.addItem(i18n.t(_eid), _eid)
        _ei = self.on_exist_combo.findData("替换")
        if _ei >= 0:
            self.on_exist_combo.setCurrentIndex(_ei)
        exist_row.addWidget(self.on_exist_combo)
        exist_row.addStretch(1)
        options_layout.addLayout(exist_row)
        # 切换冲突策略时立即持久化（与 _save_jxl_output 同一约定）。
        self.on_exist_combo.currentTextChanged.connect(
            lambda _=None: self._save_jxl_output()
        )
        # 「编码结果更大时丢弃输出（保留原文件）」：仅 JXL 输出生效。勾选后，
        # 当 JXL 输出字节数 ≥ 原文件时，丢弃该无用 JXL 输出、保留原文件；
        # 否则照常保留 JXL。PNG/JPEG 输出时此选项无意义，自动置灰。默认不勾选。
        self.discard_if_larger_check = QCheckBox(i18n.t("编码结果更大时丢弃输出（保留原文件）"))
        self.discard_if_larger_check.setToolTip(
            i18n.t("勾选后，当 JXL 输出文件不小于原文件时，丢弃该 JXL 并保留原文件"
            "（转换无收益）。仅 JXL 输出生效；选 PNG/JPEG 输出时自动禁用。")
        )
        self.discard_if_larger_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        options_layout.addWidget(self.discard_if_larger_check)

        # 「保留文件夹结构」+ 子选项「保留上级目录」：仅「自定义文件夹」输出模式生效
        # （「原文件夹」模式下输出本就落在源文件各自所在目录，结构天然保留，无需此选项）。
        # 勾选后，输出文件按源文件相对「根」的子路径镜像到自定义文件夹；
        # 子选项额外把根上移一级，使被添加文件夹本身成为输出下的顶层段。默认均不勾选。
        self.structure_check = QCheckBox(i18n.t("保留文件夹结构"))
        self.structure_check.setToolTip(
            i18n.t("勾选后，输出文件将按源文件原有的文件夹层级，镜像到自定义输出文件夹中"
            "（而非全部拍平到同一目录）。仅「文件夹」输出模式生效；选「原文件夹」时自动禁用。")
        )
        self.structure_check.toggled.connect(
            lambda _=None: (
                self._save_jxl_output(),
                self._update_structure_checkbox_state(),
            )
        )
        options_layout.addWidget(self.structure_check)

        _parent_row = QHBoxLayout()
        _parent_row.addSpacing(20)  # 视觉缩进，表明其为「保留文件夹结构」的子选项
        self.parent_check = QCheckBox(i18n.t("保留上级目录"))
        self.parent_check.setToolTip(
            i18n.t("勾选后，被添加的文件夹本身也会作为一层目录出现在输出中"
            "（例如拖入「照片」文件夹，输出为 输出目录/照片/图片）。"
            "仅「保留文件夹结构」已勾选且为「文件夹」输出模式时生效。")
        )
        self.parent_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        _parent_row.addWidget(self.parent_check)
        options_layout.addLayout(_parent_row)

        # 「保持原创建时间」/「保持原修改时间」：勾选后，成功转换的输出文件
        # 将继承原文件的对应时间戳（而非使用转换当天的当前时间）。默认不勾选。
        # 两项语义成对，合并到同一行以节省垂直空间（窗口默认高度有限）。
        _ts_row = QHBoxLayout()
        _ts_row.setSpacing(24)
        self.preserve_ctime_check = QCheckBox(i18n.t("保持原创建时间"))
        self.preserve_ctime_check.setToolTip(
            i18n.t("勾选后，成功转换的输出文件将保留原文件的创建时间"
            "（仅 Windows 有效，需 pywin32；其他平台无创建时间概念，自动忽略）。")
        )
        self.preserve_ctime_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        _ts_row.addWidget(self.preserve_ctime_check)

        self.preserve_mtime_check = QCheckBox(i18n.t("保持原修改时间"))
        self.preserve_mtime_check.setToolTip(
            i18n.t("勾选后，成功转换的输出文件将保留原文件的修改时间"
            "（文件管理器中显示的「修改日期」与原文件一致）。")
        )
        self.preserve_mtime_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        _ts_row.addWidget(self.preserve_mtime_check)
        _ts_row.addStretch(1)
        options_layout.addLayout(_ts_row)

        # 「删除原文件」：勾选后，转换成功的原文件在批处理结束后移入回收站，
        # 失败的源文件保持不变。默认不勾选（保守，避免误删）。
        self.delete_original_check = QCheckBox(i18n.t("删除原文件（成功转换后移入回收站）"))
        self.delete_original_check.setToolTip(
            i18n.t("勾选后，成功转换的原文件将在转换结束后移入系统回收站；"
            "转换失败的文件不会被删除。")
        )
        self.delete_original_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        options_layout.addWidget(self.delete_original_check)

        # ---- 转换完毕之后的后处理动作 ----
        # 与「选项」组水平并排：利用「选项」组原本独占整行时在右侧留下的空白，
        # 避免整页过宽或另起一行。两组均按自身内容宽度紧凑显示，行尾的
        # addStretch 把剩余空间推到最右，从而「压缩」了选项区右侧的空白。
        done_group = QGroupBox(i18n.t("转换完毕之后"))
        done_group.setAutoFillBackground(False)
        done_layout = QVBoxLayout(done_group)
        self.open_explorer_check = QCheckBox(i18n.t("打开资源管理器"))
        self.open_explorer_check.setToolTip(
            i18n.t("转换全部完成后，自动打开输出文件夹（资源管理器窗口）。")
        )
        self.open_explorer_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        done_layout.addWidget(self.open_explorer_check)

        self.clear_input_check = QCheckBox(i18n.t('清除"输入"文件'))
        self.clear_input_check.setToolTip(
            i18n.t("转换全部完成后，清空输入列表中的文件（不会删除磁盘上的原始文件）。")
        )
        self.clear_input_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        done_layout.addWidget(self.clear_input_check)

        self.beep_check = QCheckBox(i18n.t("过程结束时发出提示音"))
        self.beep_check.setToolTip(
            i18n.t("转换全部完成后播放一声提示音，便于离开电脑时也能知晓任务结束。")
        )
        self.beep_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        done_layout.addWidget(self.beep_check)

        self.exit_after_check = QCheckBox(i18n.t("转换完毕之后退出"))
        self.exit_after_check.setToolTip(
            i18n.t("转换全部完成后自动退出本程序。")
        )
        self.exit_after_check.toggled.connect(
            lambda _=None: self._save_jxl_output()
        )
        done_layout.addWidget(self.exit_after_check)
        # 末尾 addStretch(1)：把 4 个复选框全部顶到顶部，剩余垂直空间全部沉底，
        # 强制「从上到下自然排列、不做垂直均分」（分组框被 50/50 撑高时也不会被拉散）。
        done_layout.addStretch(1)

        # 「选项」组与「转换完毕之后」组左右各占一半（1:1 均分整行宽度）。
        # 两者都带 stretch=1、行尾不再 addStretch，从而严格左右对半、不向任一侧偏。
        # 「转换完毕之后」组内的复选框已在 done_layout 末尾 addStretch 顶到顶部，
        # 自上而下自然排列，分组框被撑高时也绝不均分。
        options_done_row = QHBoxLayout()
        options_done_row.setSpacing(12)
        options_done_row.addWidget(options_group, 1)
        options_done_row.addWidget(done_group, 1)
        layout.addLayout(options_done_row)

        layout.addStretch(1)
        # Note: 开始转换 按钮已移至窗口底部常驻栏，此处不再放置。
        scroll.setWidget(inner)
        # QScrollArea.setWidget() 会把内容 widget 的 autoFillBackground 强制打开
        # （默认填 Base 角色），从而盖住透明、造成输出页变色。这里在 setWidget
        # 之后再关掉它，让整条透明链透出 QTabWidget 面板色、并跟随主题。
        inner.setAutoFillBackground(False)
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return widget

    # ------------------------------------------------------------------
    # 输出标签：高级参数（可折叠分组 + 命令预览 + 重置）
    # ------------------------------------------------------------------
    def _build_advanced_ui(self, enc_layout):
        """构建「高级参数」折叠分组（4 个语义子组）、命令预览条与重置链接。

        控件由模块级 ``_ADVANCED_SCHEMA`` 数据驱动生成，全部默认不勾选，
        即不向 cjxl 追加任何额外参数，保持与改动前完全一致的行为。

        折叠用独立的箭头按钮控制内容区显隐（而非 checkable QGroupBox），
        因为 checkable 分组在未勾选时会自动禁用全部子控件，会与我们按模式
        的逐项置灰逻辑冲突。
        """
        # 非 checkable 的标题框；折叠/展开由 adv_toggle 控制内部内容显隐。
        self.adv_group = QGroupBox(i18n.t("高级参数"))
        adv_outer = QVBoxLayout(self.adv_group)

        # 折叠头：箭头按钮 + 「已设置 N 项」摘要 + 右侧「重置高级参数」按钮。
        header_row = QHBoxLayout()
        self.adv_toggle = QToolButton()
        self.adv_toggle.setArrowType(Qt.RightArrow)
        self.adv_toggle.setAutoRaise(True)
        self.adv_toggle.setFixedWidth(22)
        self.adv_toggle.clicked.connect(self._toggle_advanced)
        self.adv_summary = QLabel(i18n.t("已设置 0 项"))
        self.reset_adv_button = QPushButton(i18n.t("重置高级参数"))
        self.reset_adv_button.clicked.connect(self._reset_advanced)
        header_row.addWidget(self.adv_toggle)
        header_row.addWidget(self.adv_summary)
        header_row.addStretch(1)
        header_row.addWidget(self.reset_adv_button)
        adv_outer.addLayout(header_row)

        # 折叠内容：4 个子组。默认隐藏，点箭头展开。
        self.adv_content = QWidget()
        content_layout = QVBoxLayout(self.adv_content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # 4 个子组按 2×2 排布：质量精细 / 编码策略（上行）、保真合成 / 容器输出（下行）。
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        sub_order = ["质量精细", "编码策略", "保真合成", "容器输出"]
        sub_pos = {
            "质量精细": (0, 0), "编码策略": (0, 1),
            "保真合成": (1, 0), "容器输出": (1, 1),
        }
        sub_boxes = {}
        sub_layouts = {}
        for sg in sub_order:
            sb = QGroupBox(i18n.t(sg))
            sub_boxes[sg] = sb
            sub_layouts[sg] = QVBoxLayout(sb)
            r, c = sub_pos[sg]
            grid.addWidget(sb, r, c)

        self._adv_widgets = {}  # key -> (checkbox, value_widget_or_None, schema)
        for s in _ADVANCED_SCHEMA:
            row = QHBoxLayout()
            check = QCheckBox(i18n.t(s["label"]))
            check.setChecked(False)
            tip = s.get("tip")
            rendered_tip = None
            if tip:
                # _NUM_THREADS_TIP 模板含 %d（机器核心数），翻译后再代入，
                # 否则字典键（带 %d）与运行时字符串（已代入数字）永远对不上。
                rendered_tip = i18n.t(tip)
                if "%d" in rendered_tip:
                    rendered_tip %= _LOGICAL_CORES
                check.setToolTip(rendered_tip)
            row.addWidget(check)
            val_w = None
            if s["kind"] == "double":
                val_w = QDoubleSpinBox()
                val_w.setRange(s["min"], s["max"])
                val_w.setSingleStep(s["step"])
                val_w.setValue(s["default"])
                row.addWidget(val_w)
            elif s["kind"] == "int":
                val_w = QSpinBox()
                val_w.setRange(s["min"], s["max"])
                val_w.setValue(s["default"])
                row.addWidget(val_w)
            elif s["kind"] == "choice":
                val_w = NoFlickerComboBox()
                for v, t in s["choices"]:
                    val_w.addItem(i18n.t(t), v)
                idx = val_w.findData(s["default"])
                if idx >= 0:
                    val_w.setCurrentIndex(idx)
                row.addWidget(val_w)
            if val_w is not None and rendered_tip:
                val_w.setToolTip(rendered_tip)
            # switch / bool_value：仅复选框，无独立值控件
            sub_layouts[s["group"]].addLayout(row)
            self._adv_widgets[s["key"]] = (check, val_w, s)
            # 值控件随复选框启用/禁用（未勾选则不允许改值，也不传递该参数）。
            if val_w is not None:
                val_w.setEnabled(False)
            check.toggled.connect(
                lambda checked, vw=val_w: (vw.setEnabled(checked) if vw else None)
            )
            # 任一控件变化都刷新命令预览与摘要计数。
            check.toggled.connect(self._on_any_adv_changed)
            if val_w is not None:
                if isinstance(val_w, QDoubleSpinBox):
                    val_w.valueChanged.connect(self._on_any_adv_changed)
                elif isinstance(val_w, QSpinBox):
                    val_w.valueChanged.connect(self._on_any_adv_changed)
                else:
                    val_w.currentIndexChanged.connect(self._on_any_adv_changed)
        content_layout.addLayout(grid)

        self.adv_content.setVisible(False)
        adv_outer.addWidget(self.adv_content)
        enc_layout.addWidget(self.adv_group)

        # 命令预览 / 自定义命令：复选框控制「只读实时预览」还是「用户自定义命令」。
        # 未勾选时等同原命令预览（只读、随控件变化即时刷新）；勾选后可编辑，
        # 转换时直接执行用户编辑的命令（<输入>/<输出> 占位符逐文件替换）。
        cmd_row = QHBoxLayout()
        self.custom_cmd_check = QCheckBox(i18n.t("自定义命令："))
        self.custom_cmd_check.toggled.connect(self._on_custom_cmd_toggled)
        cmd_row.addWidget(self.custom_cmd_check)
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("cjxl <输入> <输出> -e 7 ...")
        mono = self.cmd_edit.font()
        mono.setFamily("Consolas")
        self.cmd_edit.setFont(mono)
        self.cmd_edit.setReadOnly(True)  # 默认只读，等同命令预览
        cmd_row.addWidget(self.cmd_edit, stretch=1)
        enc_layout.addLayout(cmd_row)

        self._update_adv_summary()
        self._update_cmd_preview()

    def _toggle_advanced(self):
        """展开/收起高级参数内容区，并同步箭头方向。"""
        visible = not self.adv_content.isVisible()
        self.adv_content.setVisible(visible)
        self.adv_toggle.setArrowType(
            Qt.DownArrow if visible else Qt.RightArrow
        )

    def _adv_value(self, s):
        """读取某高级参数值控件的当前值（double/int/choice）。"""
        val_w = self._adv_widgets[s["key"]][1]
        if s["kind"] == "choice":
            return val_w.currentData()
        return val_w.value()

    def _collect_advanced(self, mode):
        """收集当前模式下『已勾选』的高级参数，返回 converter.encode 可接受的 dict。

        不含 distance/quality/effort/lossless_jpeg（由调用方按模式处理）；
        distance 若被勾选也在此返回（key="distance"），由 _on_convert 取用。
        当前模式不可用的参数（被置灰）一律跳过。
        """
        adv = {}
        for s in _ADVANCED_SCHEMA:
            if mode not in s["modes"]:
                continue
            check, val_w, _ = self._adv_widgets[s["key"]]
            if not check.isEnabled() or not check.isChecked():
                continue
            if s["kind"] == "switch":
                adv[s["key"]] = True
            elif s["kind"] == "bool_value":
                adv[s["key"]] = s["value"]
            else:
                adv[s["key"]] = self._adv_value(s)
        return adv

    def _on_any_adv_changed(self, *_):
        self._update_adv_summary()
        self._update_cmd_preview()

    def _update_adv_summary(self):
        """更新折叠分组标题中的『已设置 N 项』摘要。"""
        mode = self._current_encode_mode()
        n = 0
        for s in _ADVANCED_SCHEMA:
            if mode not in s["modes"]:
                continue
            check, _, _ = self._adv_widgets[s["key"]]
            if check.isEnabled() and check.isChecked():
                n += 1
        self.adv_summary.setText(i18n.t("已设置 %d 项") % n)

    def _update_cmd_preview(self, force=False):
        """根据当前控件状态刷新底部 cjxl 命令预览（输入/输出用占位符）。

        当「自定义命令」被勾选且非强制（force=False）时，不覆盖用户在
        输入框中已编辑的命令文本。force=True 用于在勾选瞬间预填当前生成的
        命令，方便用户在此基础上修改。
        """
        if self.custom_cmd_check.isChecked() and not force:
            return
        # JPEG 输出格式：实际走 djxl 解码重建（无损 JPEG 转码的 JXL → 原图），
        # 而非 cjxl 编码。一旦选中 JPG 输出，预览立即展示 djxl 解码命令，
        # 无需等待放入 JXL 文件。
        fmt = self.format_combo.currentText().lower()
        if "jpg" in fmt:
            self.cmd_edit.setText("djxl <输入> <输出>")
            return
        mode = self._current_encode_mode()
        effort = int(self.effort_combo.currentText())
        if mode == "lossy":
            distance, quality, lj = None, self.quality_spin.value(), False
        elif mode == "lossless":
            distance, quality, lj = 0, None, False
        else:  # lossless_jpeg
            distance, quality, lj = None, None, True
        adv = dict(self._collect_advanced(mode))
        if "distance" in adv:
            # 显式 -d 距离覆盖 --quality（两者互斥）。
            distance = adv.pop("distance")
            quality = None
        # 复刻 converter.encode() 的 --lossless_jpeg 解析（converter.py:208-222）：
        # 有损 + 批次中含 JPG 输入时，实际命令会补 --lossless_jpeg=0（cjxl>=0.12
        # 默认翻成 1 且禁止 quality<100 的崩溃修复垫片）。预览用占位符路径，故改
        # 看选中输入里是否含 JPG，使预览标志位与实际命令保持一致。
        lj_flag = None
        if lj:
            lj_flag = 1
        elif quality is not None and self.input_files:
            if any(converter._is_jpeg(p) for p in self.input_files):
                lj_flag = 0
        args = converter.build_args(
            "<输入>", "<输出>", effort=effort, distance=distance,
            quality=quality, lossless_jpeg=lj_flag, **adv,
        )
        self.cmd_edit.setText(" ".join(args))

    def _on_custom_cmd_toggled(self, checked):
        """勾选「自定义命令」时在只读预览与可编辑自定义命令之间切换；
        勾选后同时整体置灰输出页全部编码参数控件——这些参数已被用户的
        自定义命令取代，继续可调节既无意义也易引起歧义。"""
        if checked:
            # 勾选：预填当前生成的命令，方便用户在此基础上修改。
            self._update_cmd_preview(force=True)
            self.cmd_edit.setReadOnly(False)
            self.cmd_edit.selectAll()
            self.cmd_edit.setFocus()
            self._set_encoding_controls_disabled()
        else:
            # 取消勾选：恢复只读，并依据编码模式/高级开关恢复各控件正常启用态。
            self.cmd_edit.setReadOnly(True)
            self._restore_encoding_controls_enabled()
            self._update_cmd_preview()
        self._save_jxl_output()

    def _set_encoding_controls_disabled(self):
        """勾选『自定义命令』后调用：整体置灰输出页全部编码参数控件。

        这些参数已被用户的自定义命令取代，调节无意义且易引起歧义，故统一禁用。
        注意：custom_cmd_check（开关本身，须可取消勾选）与 cmd_edit（命令正文，
        须可编辑）不在此列；其余编码参数——输出格式、编码模式三选一、--effort、
        --quality、全部高级参数、『启用高级参数』开关、『重置高级参数』按钮——均置灰。
        """
        self.format_combo.setEnabled(False)
        self.lossy_radio.setEnabled(False)
        self.lossless_radio.setEnabled(False)
        self.lossless_jpeg_radio.setEnabled(False)
        self.effort_combo.setEnabled(False)
        self.quality_slider.setEnabled(False)
        self.quality_spin.setEnabled(False)
        for s in _ADVANCED_SCHEMA:
            check, val_w, _ = self._adv_widgets[s["key"]]
            check.setEnabled(False)
            if val_w is not None:
                val_w.setEnabled(False)
        self.adv_threads_toggle.setEnabled(False)
        self.adv_num_threads_toggle.setEnabled(False)
        self.adv_effort10_toggle.setEnabled(False)
        reset_btn = getattr(self, "reset_adv_button", None)
        if reset_btn is not None:
            reset_btn.setEnabled(False)

    def _restore_encoding_controls_enabled(self):
        """取消『自定义命令』后调用：依据『编码模式 + 高级参数开关』恢复各控件
        正确的启用态（而非无脑全部启用——例如 --quality 仅在『有损』模式可用）。"""
        # 容器类/不受模式约束的控件先恢复可用。
        self.format_combo.setEnabled(True)
        self.lossy_radio.setEnabled(True)
        self.lossless_radio.setEnabled(True)
        self.lossless_jpeg_radio.setEnabled(True)
        self.effort_combo.setEnabled(True)
        self.adv_threads_toggle.setEnabled(True)
        reset_btn = getattr(self, "reset_adv_button", None)
        if reset_btn is not None:
            reset_btn.setEnabled(True)
        # 模式驱动的 --quality / 高级参数可用性。
        self._on_encode_mode_changed()
        # 高级开关驱动的 --num_threads 行与 effort 范围。
        self._apply_adv_threads_state(self.adv_threads_toggle.isChecked())

    def _lock_ui_for_convert(self):
        """转换进行中调用：禁用输入/输出/动作/设置四页的全部可编辑控件，
        仅保留状态页（看进度）与底部「停止」按钮可用，防止用户中途改动
        已冻结的 jobs 快照而产生『改了没反应』的困惑。"""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w is not self.status_tab:
                w.setEnabled(False)

    def _unlock_ui_after_convert(self):
        """转换结束后调用：恢复四页可交互，并依据当前『自定义命令』勾选态
        重建输出页各控件的细分 enabled 态（与转换开始前保持一致）。"""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w is not self.status_tab:
                w.setEnabled(True)
        # 输出页编码控件可能原本就因『自定义命令』而禁用，需按当前开关重建，
        # 不能无脑全部启用（否则自定义命令模式下编码参数会错误地变可用）。
        if getattr(self, "custom_cmd_check", None) is not None \
                and self.custom_cmd_check.isChecked():
            self._set_encoding_controls_disabled()
        else:
            self._restore_encoding_controls_enabled()

    def _reset_advanced(self):
        """将所有高级参数复位为默认（不勾选、值回默认、不传递）。"""
        for s in _ADVANCED_SCHEMA:
            check, val_w, _ = self._adv_widgets[s["key"]]
            check.setChecked(False)
            if val_w is not None:
                if s["kind"] == "choice":
                    idx = val_w.findData(s["default"])
                    if idx >= 0:
                        val_w.setCurrentIndex(idx)
                else:
                    val_w.setValue(s["default"])
                val_w.setEnabled(False)
        self._update_adv_summary()
        self._update_cmd_preview()
        self._save_jxl_output()

    def _save_advanced(self, settings):
        """持久化高级参数的『勾选状态 + 值』到已打开的 jxl_output 组。"""
        for s in _ADVANCED_SCHEMA:
            key = s["key"]
            check, val_w, _ = self._adv_widgets[key]
            settings.setValue("adv_%s_on" % key, check.isChecked())
            if val_w is not None:
                settings.setValue("adv_%s_val" % key, self._adv_value(s))

    def _load_advanced(self, settings):
        """从 jxl_output 组恢复高级参数（调用前须已按模式设置好启用状态）。"""
        for s in _ADVANCED_SCHEMA:
            key = s["key"]
            check, val_w, _ = self._adv_widgets[key]
            on = settings.value("adv_%s_on" % key, False)
            check.setChecked(on in (True, "true", "True", "1"))
            if val_w is not None:
                raw = settings.value("adv_%s_val" % key, s["default"])
                try:
                    val = type(s["default"])(raw)
                except (TypeError, ValueError):
                    val = s["default"]
                if s["kind"] == "choice":
                    idx = val_w.findData(val)
                    if idx >= 0:
                        val_w.setCurrentIndex(idx)
                else:
                    val_w.setValue(val)
                # 值控件是否可用取决于『模式可用且已勾选』。
                val_w.setEnabled(check.isEnabled() and check.isChecked())
        self._update_adv_summary()
        self._update_cmd_preview()

    def _build_status_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel(i18n.t("运行日志与环境信息：")))
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit, stretch=1)

        # 进度条 + 当前进度 / 预计剩余时间（位于日志框下方）
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        # 原生样式下 QProgressBar 又细又不会把百分比居中；强制用 Fusion 样式
        # 绘制进度条（厚度与居中均对齐 Fusion，沿用当前 palette 不破坏深色模式），
        # 与下拉框用 Fusion 规避闪烁的做法一致。Fusion 主题下本就是 Fusion，无副作用。
        self.progress_bar.setStyle(_fusion_style())
        # Fusion 样式下窗口失焦会把进度条填充色暗化/变黑；把 Inactive 组
        # 同步成 Active 组规避（复用 _sync_inactive_palette 的同一逻辑）。
        # 这里先调一次，保证构建阶段即生效，_apply_theme/系统主题切换时也会再调。
        self._sync_inactive_palette()
        layout.addWidget(self.progress_bar)

        progress_row = QHBoxLayout()
        self.progress_label = QLabel(i18n.t("当前进度：0 / 0 文件"))
        self.eta_label = QLabel(i18n.t("预计剩余：--"))
        progress_row.addWidget(self.progress_label)
        progress_row.addStretch(1)
        progress_row.addWidget(self.eta_label)
        layout.addLayout(progress_row)
        return widget

    def _build_settings_tab(self):
        widget = QWidget()
        # 整页包进 QScrollArea（与输出页一致）：选项增多导致超高时只出现滚动条，
        # 主窗口尺寸不被撑大；透明链透出 QTabWidget 面板色、自动跟随系统深浅主题。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 横向滚动条改为「按需出现」：原先 AlwaysOff 会让超宽内容的右侧被
        # 直接截断且无法滚动查看。英文译文比中文长 30~60%，在默认 880 宽的
        # 窗口下极易超宽。AsNeeded 保证正常宽度下不出现滚动条（观感不变），
        # 只有真的放不下时才出现，用作永不截断的兜底。
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setAutoFillBackground(False)
        layout = QVBoxLayout(inner)
        layout.setSpacing(10)
        # 2 列网格排布各分组框（两两并排），消除单列时每个框右侧的大片留白；
        # 「高级参数」保持原样、全宽置于最下方，其上方新增「选项」区。
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setSpacing(10)

        # 各分区统一用分组框（QGroupBox）框起，紧凑内边距，避免框显得过大。
        def _section(title):
            g = QGroupBox(title)
            inner = QVBoxLayout(g)
            inner.setContentsMargins(10, 6, 10, 6)
            inner.setSpacing(8)
            return g, inner

        # 左侧标签不强制最小宽度，下拉框紧贴标签文字（不强行对齐成一列）。
        # container 可以是 QBoxLayout（纵向追加一行），也可以是 QGridLayout
        # （按 row/col 落位，用于某个分组内部再做双列排布）。
        def _label_row(container, label_text, combo, tip="", row=0, col=0):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)
            lab = QLabel(label_text)
            if tip:
                lab.setToolTip(tip)
                combo.setToolTip(tip)
            row_layout.addWidget(lab)
            row_layout.addWidget(combo)
            row_layout.addStretch(1)
            if isinstance(container, QGridLayout):
                container.addLayout(row_layout, row, col)
            else:
                container.addLayout(row_layout)

        # ---- 窗口布局 ----
        win_group, win_inner = _section(i18n.t("窗口布局"))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_center = QPushButton(i18n.t("一键居中"))
        btn_center.setToolTip(i18n.t("将窗口移动到屏幕中央（不改变窗口大小）"))
        btn_center.clicked.connect(self._on_center_window)
        btn_fit = QPushButton(i18n.t("一键 6×3 排版"))
        btn_fit.setToolTip(i18n.t("将窗口恢复为默认的 6 列 × 3 行尺寸（不改变位置）"))
        btn_fit.clicked.connect(self._on_fit_window)
        btn_row.addWidget(btn_center)
        btn_row.addWidget(btn_fit)
        btn_row.addStretch(1)
        win_inner.addLayout(btn_row)

        hint = QLabel(i18n.t("窗口的大小与位置会自动保存，下次打开时原样恢复。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        win_inner.addWidget(hint)
        grid.addWidget(win_group, 0, 1)

        # ---- 常规 ----
        # 分组内部再分两列：左列放 主题 / 控件样式，右列与「主题」同排放 语言，
        # 避免右半边留白。
        theme_group, theme_inner = _section(i18n.t("常规"))
        theme_grid = QGridLayout()
        theme_grid.setColumnStretch(0, 1)
        theme_grid.setColumnStretch(1, 1)
        theme_grid.setSpacing(8)
        theme_inner.addLayout(theme_grid)
        # 主题（颜色方案）— 亮 / 暗 / 跟随系统
        color_tip = (
            i18n.t("跟随系统：自动跟随 Windows 当前是浅色还是深色模式（默认）。\n"
            "亮色：始终使用浅色外观。\n"
            "暗色：始终使用深色外观。")
        )
        self.color_scheme_combo = NoFlickerComboBox()
        for key in _COLOR_SCHEME_ORDER:
            self.color_scheme_combo.addItem(i18n.t(_COLOR_SCHEME_LABELS[key]), key)
        self._set_combo_min_width(self.color_scheme_combo)
        self._color_scheme_loading = True
        self.color_scheme_combo.setCurrentIndex(
            self.color_scheme_combo.findData(app_color_scheme())
        )
        self._color_scheme_loading = False
        self.color_scheme_combo.currentIndexChanged.connect(
            self._on_color_scheme_changed
        )
        _label_row(theme_grid, i18n.t("主题"), self.color_scheme_combo, color_tip,
                   row=0, col=0)
        # 控件样式（原"界面主题"）
        style_tip = (
            i18n.t("原生（无闪烁）：大部分界面保持系统原生外观，仅会闪烁的下拉菜单"
            "单独使用 Fusion 样式以消除 Windows 弹出动画闪烁（默认）。\n"
            "原生：完全使用系统原生外观，下拉菜单可能出现轻微闪烁。\n"
            "Fusion：整套界面使用 Qt 自带的 Fusion 样式。")
        )
        self.theme_combo = NoFlickerComboBox()
        for key in _THEME_ORDER:
            self.theme_combo.addItem(i18n.t(_THEME_LABELS[key]), key)
        self._set_combo_min_width(self.theme_combo)
        self._theme_loading = True
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(app_theme()))
        self._theme_loading = False
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        _label_row(theme_grid, i18n.t("控件样式"), self.theme_combo, style_tip,
                   row=0, col=1)
        # 语言（占位项：只提供选项，界面语言切换功能尚未实现）
        # 放在左列第二行（原「控件样式」位置），右列与「主题」同排的是控件样式。
        lang_tip = (
            i18n.t("选择界面显示语言。\n"
            "切换后需重启程序生效：界面文本是在窗口构建时就取定的，实时刷新"
            "每个控件既容易漏、又要额外缓存原文，得不偿失。\n"
            "注意：目前已收录的英文文案以「内部标识的显示名」为主"
            "（动作类型 / 水印位置 / 查看模式 / 冲突策略），"
            "其余界面文本会随翻译推进逐步补全，未收录的暂时保持中文。")
        )
        self.language_combo = NoFlickerComboBox()
        for key in _LANGUAGE_ORDER:
            self.language_combo.addItem(_LANGUAGE_LABELS[key], key)
        self._set_combo_min_width(self.language_combo)
        # 回显当前语言：启动入口 __main__.run() 会在构造窗口前按持久化值
        # 调 i18n.set_language()，这里只是把结果显示出来。
        _lang_idx = self.language_combo.findData(i18n.current_language())
        self.language_combo.setCurrentIndex(
            _lang_idx if _lang_idx >= 0
            else self.language_combo.findData(_LANGUAGE_DEFAULT)
        )
        # 立即持久化 + 提示重启。语言本身要等下次启动才由 __main__ 应用。
        self.language_combo.currentIndexChanged.connect(
            self._on_language_combo_changed)
        _label_row(theme_grid, i18n.t("语言"), self.language_combo, lang_tip,
                   row=1, col=0)

        # 本区下拉的最小宽度是按"构建时的样式"量出来的。切到「原生」后，Windows
        # 原生样式的箭头按钮与边框内边距都比 Fusion 宽，同一个最小宽度留给文字的
        # 空间就变少：窗口宽时看不出来，收到最窄时布局按这个偏小的旧下限压缩，
        # 文字被裁切。全局 _apply_theme 只重算它自己那份名单（控件样式 / CPU
        # 优先级 / CPU 核心 / effort），「主题」与「语言」不在其中，故在本区补一次。
        # 挂在 theme_combo 上、晚于 _on_theme_changed 连接，触发时样式已切换完毕。
        def _remeasure_regular_combos(_index):
            for combo in (self.color_scheme_combo, self.language_combo):
                self._set_combo_min_width(combo)

        self.theme_combo.currentIndexChanged.connect(_remeasure_regular_combos)
        grid.addWidget(theme_group, 0, 0)

        # ---- 转换进程 ----
        proc_group, proc_inner = _section(i18n.t("转换进程"))
        cpu_tip = (
            i18n.t("设置 cjxl / djxl 转换进程的 CPU 优先级（默认低于正常，"
            "减少对前台操作的影响）。")
        )
        self.cpu_priority_combo = NoFlickerComboBox()
        for key, label in (
            ("idle", i18n.t("空闲")),
            ("below_normal", i18n.t("低于正常")),
            ("normal", i18n.t("正常")),
            ("above_normal", i18n.t("高于正常")),
            ("high", i18n.t("高")),
        ):
            self.cpu_priority_combo.addItem(label, key)
        self._set_combo_min_width(self.cpu_priority_combo)
        self.cpu_priority_combo.setCurrentIndex(
            self.cpu_priority_combo.findData(converter.DEFAULT_PRIORITY)
        )
        self.cpu_priority_combo.currentIndexChanged.connect(
            self._on_cpu_priority_changed
        )
        _label_row(proc_inner, i18n.t("CPU 优先级"), self.cpu_priority_combo, cpu_tip)

        cores_tip = (
            i18n.t("转换时并行使用的 CPU 核心数，决定同时转换的文件数（多文件时）"
            "或单个大文件的线程数（单文件时）。「自动」等于本机逻辑核心数。")
        )
        self.cpu_cores_combo = NoFlickerComboBox()
        self.cpu_cores_combo.setToolTip(cores_tip)
        self.cpu_cores_combo.addItem(i18n.t("自动"), "auto")
        max_cores = os.cpu_count() or 1
        for n in range(1, max_cores + 1):
            self.cpu_cores_combo.addItem(str(n), n)
        self._set_combo_min_width(self.cpu_cores_combo)
        self.cpu_cores_combo.setCurrentIndex(self.cpu_cores_combo.findData("auto"))
        self.cpu_cores_combo.currentIndexChanged.connect(self._on_cpu_cores_changed)
        _label_row(proc_inner, i18n.t("CPU 核心使用数"), self.cpu_cores_combo, cores_tip)
        grid.addWidget(proc_group, 1, 0)

        # ---- 大图并发校准（一键傻瓜式，独立于「高级参数」） ----
        # 双队列调度器按「大图」判定把超大图独占满核、其余小图并行。判定阈值需按
        # 本机 CPU 校准。这里提供一键按钮：自动生成测试图、测 cjxl 多线程加速比、
        # 把像素阈值写入设置，全程无需用户配置任何参数。故刻意放在高级参数之外。
        # 用 _section 保持与其他分区一致的内边距，框体更紧凑。
        calib_group, calib_layout = _section(i18n.t("大图并发校准"))
        calib_layout.setSpacing(6)

        calib_tip = (
            i18n.t("双队列调度器会根据「大图」判定把超大图独占满核、其余小图并行，"
            "从而充分利用 CPU。判定阈值需按本机 CPU 能力校准：\n"
            "点击此按钮将自动生成若干测试图、测量 cjxl 在不同分辨率下的多线程"
            "加速比，并把适合本机的像素阈值写入设置——无需任何参数配置。\n"
            "首次启动时若尚未校准，会自动运行一次。")
        )
        self.calib_button = QPushButton(i18n.t("一键校准大图阈值（按本机 CPU）"))
        self.calib_button.setToolTip(calib_tip)
        self.calib_button.clicked.connect(self._on_calibrate_clicked)
        # 左对齐、保持自然宽度，避免撑满整行显得过大（与工具栏按钮一致）。
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.calib_button)
        btn_row.addStretch(1)
        calib_layout.addLayout(btn_row)

        # 当前已校准阈值展示（随自动/手动校准刷新）。
        self.calib_value_label = QLabel()
        self.calib_value_label.setWordWrap(True)
        self.calib_value_label.setStyleSheet("color: #888; font-size: 11px;")
        calib_layout.addWidget(self.calib_value_label)
        self._refresh_calib_value_label()

        grid.addWidget(calib_group, 1, 1)
        # 4 个区已入网格，先加入外层垂直布局（保持其在「选项」「高级参数」之上）。
        layout.addLayout(grid)

        # ---- 选项（零散开关区；位于高级参数上方） ----
        options_group, options_inner = _section(i18n.t("选项"))

        # 「退出时保存动作列表」：勾选后退出应用会序列化当前动作标签页的动作列表
        # 到 QSettings，下次启动自动恢复；关闭则退出时不保存（已存数据会被清除）。
        self.save_actions_on_exit_check = QCheckBox(i18n.t("退出时保存动作列表"))
        self.save_actions_on_exit_check.setToolTip(
            i18n.t("开启后，退出程序时会记住「动作」标签页里当前的动作列表，下次启动自动恢复；\n"
            "关闭则该列表不持久化（每次启动恢复到空）。")
        )
        self.save_actions_on_exit_check.setChecked(False)
        self.save_actions_on_exit_check.toggled.connect(self._save_actions_setting)
        options_inner.addWidget(self.save_actions_on_exit_check)

        layout.addWidget(options_group)
        self.options_group = options_group

        # ---- 高级参数区域（母开关 + 逐项子开关，子项默认禁用） ----
        # 「启用高级参数」仅作为母开关：勾选时解锁下方子项按钮，取消时全部置灰。
        # 每个子项是否真正生效由各自勾选决定（见 _on_adv_*_toggled）。
        adv_params_group = QGroupBox(i18n.t("高级参数"))
        adv_params_layout = QVBoxLayout(adv_params_group)
        adv_params_layout.setSpacing(6)

        adv_threads_tip = (
            i18n.t("母开关：勾选以解锁下方各项高级参数，可逐项单独开启；"
            "取消勾选则全部恢复默认行为。"
            "\n（首次勾选会弹出注意事项，可在弹窗中勾选「不再提醒」。）")
        )
        self.adv_threads_toggle = QCheckBox(i18n.t("启用高级参数"))
        self.adv_threads_toggle.setToolTip(adv_threads_tip)
        self.adv_threads_toggle.setChecked(False)
        self.adv_threads_toggle.toggled.connect(self._on_adv_threads_toggled)
        adv_params_layout.addWidget(self.adv_threads_toggle)

        # 子项 1：手动设置每文件线程数（--num_threads）。默认禁用（母开关关闭时置灰）。
        self.adv_num_threads_toggle = QCheckBox(i18n.t("手动设置每文件线程数 (--num_threads)"))
        self.adv_num_threads_toggle.setToolTip(
            i18n.t("开启后，输出页「线程数 (--num_threads)」行可手动填写；"
            "并行进程数 = CPU 核心使用数 ÷ 每文件线程数。")
        )
        self.adv_num_threads_toggle.setChecked(False)
        self.adv_num_threads_toggle.setEnabled(False)
        self.adv_num_threads_toggle.toggled.connect(self._on_adv_num_threads_toggled)
        adv_params_layout.addWidget(self.adv_num_threads_toggle)

        # 子项 2：解锁 effort 第 10 档。默认禁用（母开关关闭时置灰）。
        self.adv_effort10_toggle = QCheckBox(i18n.t("解锁 effort 第 10 档（最慢、质量最高）"))
        self.adv_effort10_toggle.setToolTip(
            i18n.t("开启后，输出页「速度/质量权衡 (--effort)」可选范围由 1–9 扩展到 1–10"
            "（第 10 档最慢、质量最高）。")
        )
        self.adv_effort10_toggle.setChecked(False)
        self.adv_effort10_toggle.setEnabled(False)
        self.adv_effort10_toggle.toggled.connect(self._on_adv_effort10_toggled)
        adv_params_layout.addWidget(self.adv_effort10_toggle)

        # 子项（母开关关闭时一并置灰，且其 A 模式生效以母开关开启为前提）：
        # JPEG 输出时遇到不可无损重建的 JXL 的处理。
        # 关闭（默认）= B 模式：弹一次确认，由用户决定是否以解码重编码方式输出；
        # 开启 = A 模式：直接跳过并在状态页记录，不弹确认。
        self.jpeg_hard_skip_check = QCheckBox(
            i18n.t("JPEG 输出：不可无损重建的 JXL 直接跳过（否则弹确认）")
        )
        self.jpeg_hard_skip_check.setToolTip(
            i18n.t("输出格式为 JPEG 时，若某 JXL 无法通过 djxl 比特级还原为原始 JPG"
            "（如非 JPEG 源编码、或重建数据已剥离），本开关决定处理方式：\n"
            "• 勾选（A 模式）：直接跳过该文件并在状态页记录原因；\n"
            "• 不勾选（默认，B 模式）：弹一次确认，由你决定是否以「解码为像素再重新"
            "编码为 JPG」的方式输出（有损的二次压缩，画质会下降）。")
        )
        self.jpeg_hard_skip_check.setChecked(False)
        self.jpeg_hard_skip_check.toggled.connect(self._on_jpeg_hard_skip_toggled)
        adv_params_layout.addWidget(self.jpeg_hard_skip_check)

        # 子项：解码侧线程控制。默认关闭——实测限制 djxl 线程数会让解码变慢
        # （解码的并行度远低于编码），故仅在用户需要精确控制 CPU 负载时才开。
        self.decode_threads_check = QCheckBox(i18n.t("解码侧也限制线程（djxl --num_threads）"))
        self.decode_threads_check.setToolTip(
            i18n.t("默认关闭：djxl 解码时不传 --num_threads，由它自己按机器决定（吃满核心，最快）。\n"
            "开启后：每个 djxl 进程按当前每文件线程预算限制线程数，使「CPU 核心使用数」"
            "在解码路径同样生效，可精确控制 CPU 负载。\n"
            "⚠️ 注意：启用后解码速度可能略有下降。20 核机实测多文件批量解码约慢 7%~13%"
            "——解码的线程收益本就远小于编码（满核 vs 单线程约 1.2x，编码是 2.3x），"
            "限制线程换不回等价的进程级并行。\n"
            "仅建议在需要精确控制 CPU 负载时开启（如转换时需同时跑其他重负载任务）。")
        )
        self.decode_threads_check.setChecked(False)
        self.decode_threads_check.toggled.connect(self._on_decode_threads_toggled)
        adv_params_layout.addWidget(self.decode_threads_check)

        # 保留原始扩展名：输出命名选项（非 cjxl 参数），独立于此「高级参数」母开关，
        # 始终可用、不随母开关置灰（它不是 cjxl 专家参数，只是输出文件命名行为）。
        # 默认关闭；开启后输出文件沿用输入扩展名。持久化复用 _save_jxl_output。
        self.preserve_ext_check = QCheckBox(i18n.t("保留原始扩展名（输出沿用输入扩展名，不再强制 .jxl）"))
        self.preserve_ext_check.setToolTip(
            i18n.t("开启后输出文件使用与输入相同的扩展名；关闭时用输出格式推导扩展名"
            "（如 .jxl / .png / .jpg）。若与「原文件夹 + 源文件带扩展名」组合导致"
            "输出路径等于输入，将跳过该文件以免覆盖源文件。")
        )
        self.preserve_ext_check.setChecked(False)
        self.preserve_ext_check.toggled.connect(self._save_jxl_output)
        adv_params_layout.addWidget(self.preserve_ext_check)

        self.adv_params_group = adv_params_group
        layout.addWidget(adv_params_group)

        layout.addStretch(1)
        scroll.setWidget(inner)
        inner.setAutoFillBackground(False)
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return widget

    def _on_cpu_priority_changed(self, _index):
        """Persist the CPU-priority choice whenever the user changes it."""
        self._save_conversion_settings()

    def _on_cpu_cores_changed(self, _index):
        """Persist the CPU-core-count choice whenever the user changes it."""
        self._save_conversion_settings()

    # ------------------------------------------------------------------
    # 大图并发校准（一键傻瓜式；独立于「高级参数」）
    # ------------------------------------------------------------------
    def _start_power_poll(self):
        """启动电源方案轮询：每 2 秒读一次注册表中的活动方案 GUID，切换时轻量提示。

        选注册表轮询而非 WM_POWERBROADCAST 监听：注册表值随方案切换由系统同步更新、
        零进程、不闪窗，且不受窗口消息路由影响，比 nativeEvent 可靠。
        """
        self._last_seen_state = power.get_power_state()
        if self._power_poll_timer is None:
            self._power_poll_timer = QTimer(self)
            self._power_poll_timer.setInterval(2000)
            self._power_poll_timer.timeout.connect(self._on_power_poll)
        self._power_poll_timer.start()

    def _on_power_poll(self):
        """定时器回调：完整电源状态（计划/插拔电/模式）与上次不同即视为切换，触发
        轻量提示（不自动跑）。"""
        state = power.get_power_state()
        if state == self._last_seen_state:
            return
        self._last_seen_state = state
        self._on_power_state_changed(state)

    def _check_cpu_signature(self):
        """检测 CPU 是否更换：更换则清空全部旧校准数据，当作首次启动重新校准。"""
        try:
            cur = power.cpu_signature()
        except Exception:
            return
        if not cur:
            return
        prev = calibrate.read_cpu_signature()
        if prev is None:
            calibrate.write_cpu_signature(cur)
            return
        if prev != cur:
            calibrate.clear_all_calibration()
            calibrate.write_cpu_signature(cur)
            self.log_edit.appendPlainText(
                i18n.t("检测到 CPU 型号变化，已清除旧校准数据，将按新硬件重新校准。"))
            self.statusBar().showMessage(i18n.t("CPU 已更换，已清除旧阈值并将重新校准"))

    def _on_power_state_changed(self, new_state):
        """电源状态（计划/插拔电/模式任一变化）：轻量提示 + 自动套用该状态下已记录的
        阈值（若有）。不自动跑校准。"""
        scheme, ac, mode = new_state
        if not (scheme or ac or mode):
            return  # 无法判定当前状态，静默忽略
        self._last_seen_state = new_state  # 与轮询去重，避免同一切换重复提示
        per = calibrate.read_per_state_floor_px(new_state) if new_state else None
        if per is not None:
            # 把该状态的记录值同步到通用键，使显示与兜底都与当前状态一致。
            calibrate.write_floor_px(per, scheme=scheme, ac=ac, mode=mode)
            mp = per / 1_000_000.0
            self.statusBar().showMessage(
                i18n.t("电源状态已切换，已自动套用该状态下的校准阈值（约 %.1f MP），如需更精确可重新校准")
                % mp)
            self.log_edit.appendPlainText(
                i18n.t("电源状态发生变化（计划=%s，供电=%s，模式=%s）：检测到该状态下已记录的阈值，"
                "已自动套用（约 %.1f MP）。")
                % (scheme or i18n.t("未知"), ac or i18n.t("未知"), mode or i18n.t("未知"), mp))
        else:
            self.statusBar().showMessage(i18n.t("电源状态发生变化，建议重新校准大图阈值"))
            self.log_edit.appendPlainText(
                i18n.t("电源状态发生变化（计划=%s，供电=%s，模式=%s）：当前状态无已记录阈值，"
                "建议点击「一键校准大图阈值」重新校准。")
                % (scheme or i18n.t("未知"), ac or i18n.t("未知"), mode or i18n.t("未知")))
        self._refresh_calib_value_label()

    def _refresh_calib_value_label(self):
        """刷新设置页「当前已校准阈值」说明文字。"""
        cores = os.cpu_count() or 1
        if calibrate.has_calibration():
            state = power.get_power_state()
            floor = read_big_image_floor_px(cores)  # 已按当前电源状态读取
            mp = floor / 1_000_000.0
            # 电源状态相关提醒：阈值按 CPU 与完整电源状态（计划/插拔电/模式）记忆，
            # 切换后建议重校准或自动套用该状态记录值。
            if calibrate.read_per_state_floor_px(state) is not None:
                note = i18n.t("（已套用当前电源状态记录的阈值）")
            else:
                note = i18n.t("（当前电源状态无记录，建议重新校准；或会自动套用同计划记录值）")
            self.calib_value_label.setText(
                i18n.t("当前已校准阈值：约 %.1f MP（%d 像素）。%s")
                % (mp, floor, note)
            )
        else:
            self.calib_value_label.setText(
                i18n.t("当前使用默认阈值（尚未校准）。建议点击上方按钮进行一次校准；"
                "阈值会按本机 CPU 与电源状态（计划/插拔电/模式）分别记忆。")
            )

    def _on_calibrate_clicked(self):
        """设置页「一键校准」按钮：手动触发一次校准。"""
        self._start_calibration(auto=False)

    def _start_calibration(self, auto):
        """启动后台校准线程（避免阻塞 UI）。重复点击或已在进行中则忽略。"""
        if self._calib_worker is not None and self._calib_worker.isRunning():
            self.statusBar().showMessage(i18n.t("校准正在进行中，请稍候…"))
            return
        self._calib_worker = CalibrateWorker(effort=7, runs=3, max_mp=64)
        self._calib_worker.log_signal.connect(self.log_edit.appendPlainText)
        self._calib_worker.status_signal.connect(self.statusBar().showMessage)
        self._calib_worker.done_signal.connect(self._on_calibration_done)
        if auto:
            self.log_edit.appendPlainText(
                i18n.t("首次启动检测到尚未校准，开始自动校准大图阈值…")
            )
            self.statusBar().showMessage(i18n.t("正在自动校准大图阈值（按本机 CPU）…"))
        else:
            self.log_edit.appendPlainText(i18n.t("开始手动校准大图阈值…"))
            self.statusBar().showMessage(i18n.t("正在校准大图阈值（按本机 CPU）…"))
        self.calib_button.setEnabled(False)
        self._calib_worker.start()

    def _auto_calibrate(self):
        """首次启动自动校准：已校准 / 无 cjxl 则跳过并说明。"""
        if calibrate.has_calibration():
            self._refresh_calib_value_label()
            return
        if not calibrate.cjxl_path():
            self.log_edit.appendPlainText(
                i18n.t("尚未校准且未找到 cjxl，跳过自动校准，继续使用默认阈值。")
            )
            self.statusBar().showMessage(i18n.t("未找到 cjxl，跳过自动校准"))
            return
        self._start_calibration(auto=True)

    def _on_calibration_done(self, floor_px):
        """校准线程结束：刷新状态栏/日志/阈值说明，恢复按钮可用。"""
        if floor_px is None:
            self.statusBar().showMessage(i18n.t("校准未完成（未找到 cjxl 或测量失败）"))
            self.log_edit.appendPlainText(
                i18n.t("校准未完成：未找到 cjxl 或测量失败，继续使用默认阈值。")
            )
        else:
            mp = floor_px / 1_000_000.0
            self.statusBar().showMessage(i18n.t("校准完成：大图阈值 = %.1f MP") % mp)
            self.log_edit.appendPlainText(
                i18n.t("校准完成，大图像素阈值已写入设置：约 %.1f MP。") % mp
            )
        self._refresh_calib_value_label()
        self.calib_button.setEnabled(True)
        self._calib_worker = None

    def _on_adv_threads_toggled(self, _checked):
        """母开关：勾选时解锁下方子项按钮，取消时全部置灰。子项是否生效由各子项
        自身勾选决定（见 _on_adv_*_toggled）。首次勾选（非加载期）弹注意事项。"""
        # 勾选『自定义命令』时编码参数已被整体置灰，跳过本联动（避免重新启用）。
        if self.custom_cmd_check.isChecked():
            self._set_encoding_controls_disabled()
            return
        enabled = self.adv_threads_toggle.isChecked()
        loading = getattr(self, "_conversion_loading", False) or getattr(self, "_jxl_loading", False)
        # 首次（且非加载期、未选「不再提醒」）勾选弹警告，确认用户了解子项作用。
        if enabled and not loading and not getattr(self, "_adv_warning_suppressed", False):
            self._maybe_warn_adv_params()
        self._apply_adv_threads_state(enabled)
        self._save_conversion_settings()
        # 加载阶段不刷新预览/持久化 jxl，否则会用默认态覆盖待恢复值。
        if loading:
            return
        # effort 范围与当前选择可能随子项变化（禁用时若原为 10 会被夹到 9），
        # 需同步持久化并刷新命令预览。
        self._save_jxl_output()
        self._update_cmd_preview()

    def _num_threads_enabled(self):
        """num_threads 行是否可用：需同时满足 (a) 母开关「启用高级参数」已勾选；
        (b) 子项「手动设置每文件线程数」已勾选；(c) 当前编码模式支持。任一不满足
        则置灰，避免母开关关闭却仍能勾选线程数，或子项未开却生效的回归。控件未
        构建时（输出页晚于本调用）返回 False。"""
        master = getattr(self, "adv_threads_toggle", None)
        if master is None or not master.isChecked():
            return False
        toggle = getattr(self, "adv_num_threads_toggle", None)
        if toggle is None or not toggle.isChecked():
            return False
        mode = self._current_encode_mode()
        for s in _ADVANCED_SCHEMA:
            if s["key"] == "num_threads":
                return mode in s["modes"]
        return False

    def _effort_allow_ten(self):
        """输出页 effort 是否允许第 10 档：需母开关与「解锁 effort 第 10 档」子项
        同时勾选。"""
        master = getattr(self, "adv_threads_toggle", None)
        sub = getattr(self, "adv_effort10_toggle", None)
        return bool(master and master.isChecked() and sub and sub.isChecked())

    def _sync_num_threads_row(self):
        """按「手动设置每文件线程数」子项勾选态，刷新输出页 num_threads 行的可用性。"""
        adv_widgets = getattr(self, "_adv_widgets", {})
        entry = adv_widgets.get("num_threads")
        if entry is not None:
            check, val_w, _ = entry
            nt_enabled = self._num_threads_enabled()
            check.setEnabled(nt_enabled)
            if val_w is not None:
                val_w.setEnabled(nt_enabled and check.isChecked())

    def _on_adv_num_threads_toggled(self, _checked):
        """子项「手动设置每文件线程数」勾选变化：刷新输出页 num_threads 行可用性
        并持久化。"""
        self._sync_num_threads_row()
        loading = getattr(self, "_conversion_loading", False) or getattr(self, "_jxl_loading", False)
        if not loading:
            self._save_jxl_output()
            self._update_cmd_preview()
        self._save_conversion_settings()

    def _on_adv_effort10_toggled(self, _checked):
        """子项「解锁 effort 第 10 档」勾选变化：调整输出页 effort 范围并持久化。"""
        self._set_effort_range(self._effort_allow_ten())
        loading = getattr(self, "_conversion_loading", False) or getattr(self, "_jxl_loading", False)
        if not loading:
            self._save_jxl_output()
            self._update_cmd_preview()
        self._save_conversion_settings()

    def _on_jpeg_hard_skip_toggled(self, _checked):
        """「JPEG 输出不可重建 JXL 直接跳过」开关变化：仅持久化（拦截逻辑在
        _on_convert 建 job 时按本开关即时生效，无需额外联动）。"""
        self._save_conversion_settings()

    def _on_decode_threads_toggled(self, _checked):
        """「解码侧也限制线程」开关变化：仅持久化（实际取值在转换时按当前阶段
        的每文件线程预算注入，见 ConvertWorker._decode_kwargs）。"""
        self._save_conversion_settings()

    def _show_warning_centered(self, title, text, parent=None):
        """弹一个居中到主窗口的警告框。

        ``QMessageBox.warning(self, ...)`` 在多屏 / 高 DPI / 父窗口未 show 等
        场景下会落到屏幕右上角而非居中。手动构造 ``QMessageBox(self)`` 再
        ``exec()``，Qt 会按「父窗口客户区中心」自动居中（与「输出文件已存在」
        框 ``_ask_on_exist`` 的写法一致）。**不要再 ``box.show()``** —— Qt
        内置居中只发生在 ``exec()`` 的隐式 show 上；显式 ``show()`` 会让 Qt
        在 (0,0) 显示后再被 move 拖到中心，造成一帧位置闪烁 + 偏下。
        """
        box = QMessageBox(parent if parent is not None else self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _maybe_warn_adv_params(self):
        """首次勾选「启用高级参数」时弹警告，确认用户了解各子项作用；提供「不再提醒」
        复选框，勾选后持久化 adv_warning_suppressed，下次不再弹。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(i18n.t("启用高级参数"))
        # 子项列表动态从「高级参数」组收集（排除母开关自身），新增子项自动进弹窗，
        # 无需每次手动同步文本。
        subitems = []
        group = getattr(self, "adv_params_group", None)
        if group is not None and group.layout() is not None:
            for i in range(group.layout().count()):
                w = group.layout().itemAt(i).widget()
                if isinstance(w, QCheckBox) and w is not self.adv_threads_toggle:
                    subitems.append(w.text())
        bullet = "\n".join("• %s" % t for t in subitems)
        box.setText(
            "您正在启用「高级参数」。\n\n"
            "这些选项会改变编码行为与系统资源占用，包括：\n"
            + (bullet + "\n\n" if bullet else "")
            + "请确认您了解上述子项的作用后再逐项开启；"
            "如需恢复默认行为，关闭「启用高级参数」即可。"
        )
        box.setStandardButtons(QMessageBox.Ok)
        cb = QCheckBox(i18n.t("不再提醒"))
        box.setCheckBox(cb)
        box.exec()
        if cb.isChecked():
            self._adv_warning_suppressed = True
            self._save_conversion_settings()

    def _apply_adv_threads_state(self, enabled):
        """「启用高级参数」母开关仅控制下方子项按钮的可用（解锁/置灰），不直接
        决定任何子项是否生效（由各子项自身勾选决定）。同步输出页 num_threads 行
        与 effort 范围到当前子项勾选态，并更新母开关 tooltip。控件未构建时安全跳过。"""
        # 解锁 / 置灰子项按钮（子项自身勾选态不变，由 checked 决定生效与否）。
        # jpeg_hard_skip_check / decode_threads_check 同样是母开关子项，一并跟随禁用。
        for t in (self.adv_num_threads_toggle, self.adv_effort10_toggle,
                  self.jpeg_hard_skip_check, self.decode_threads_check):
            t.setEnabled(enabled)
        # 同步输出页：num_threads 行可用性 + effort 可选范围。
        self._sync_num_threads_row()
        self._set_effort_range(self._effort_allow_ten())
        # 母开关说明文字并入 tooltip。
        if hasattr(self, "adv_threads_toggle"):
            if enabled:
                self.adv_threads_toggle.setToolTip(
                    i18n.t("已启用：下方「手动设置每文件线程数」「解锁 effort 第 10 档」"
                    "已解锁，可逐项单独开启；关闭则全部恢复默认行为。")
                )
            else:
                self.adv_threads_toggle.setToolTip(
                    i18n.t("未启用：所有高级子项均锁定为默认行为。勾选以解锁下方子项，"
                    "再自行决定是否逐项开启（首次开启会弹出注意事项）。")
                )

    def _set_effort_range(self, allow_ten):
        """按「启用高级参数」开关调整输出页 effort 下拉的可选范围。
        allow_ten=True -> 1..10；False -> 1..9。重建列表时尽量保留当前选择，
        越界（如关闭高级参数前选了 10）则夹到范围内最大档。
        不触发信号，故调用方负责在用户交互后持久化（见 _on_adv_threads_toggled）。"""
        combo = getattr(self, "effort_combo", None)
        if combo is None:
            return
        cur = combo.currentText()
        items = [str(i) for i in range(1, 11 if allow_ten else 10)]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        if cur in items:
            combo.setCurrentText(cur)
        else:
            combo.setCurrentText(items[-1])
        combo.blockSignals(False)

    def _on_center_window(self):
        """Move the window to the centre of the primary screen (keep size)."""
        screen = QApplication.primaryScreen()
        if screen is not None:
            sg = screen.availableGeometry()
            # Centre the window's OUTER (frame) rectangle within the usable work
            # area. frameGeometry()/move() share the frame coordinate space, so
            # this lands the visible window dead-centre. Using geometry()
            # (client size) here would centre only the inner rect and leave the
            # window sitting ~half a title-bar too high.
            fg = self.frameGeometry()
            x = sg.x() + max(0, (sg.width() - fg.width()) // 2)
            y = sg.y() + max(0, (sg.height() - fg.height()) // 2)
            self.move(x, y)
        self._save_geometry()
        self.statusBar().showMessage(i18n.t("窗口已居中"))

    def _on_fit_window(self):
        """Resize to the default 6x3 startup size (keep position)."""
        self._fit_window_to_grid(center=False)
        self._save_geometry()
        self.statusBar().showMessage(i18n.t("已恢复 6×3 默认尺寸"))

    # ---- window geometry persistence (QSettings) ----------------------

    def _save_geometry(self):
        """Persist the window size + position (native format, survives restart)."""
        settings = QSettings()
        settings.beginGroup("main_window")
        settings.setValue("geometry", self.saveGeometry())
        settings.endGroup()

    def _load_geometry(self):
        """Restore a previously persisted window geometry, if any.

        Returns True if a saved geometry was found and applied, False otherwise
        (so the caller can decide whether to fall back to the 6x3 fit).
        """
        settings = QSettings()
        settings.beginGroup("main_window")
        g = settings.value("geometry", None)
        settings.endGroup()
        if g is not None:
            self.restoreGeometry(g)
            return True
        return False

    # ---- JXL output-parameter persistence (QSettings) -----------------

    def _save_jxl_output(self):
        """Persist the JXL encode mode / quality / effort / advanced params so
        they survive a restart of the application. Called whenever any of those
        controls changes (and on close)."""
        # 加载期间（_load_jxl_output）不写回：否则 _on_encode_mode_changed 会在
        # 高级参数尚未恢复前先把默认值存回去，把已持久化的勾选状态覆盖掉。
        if getattr(self, "_jxl_loading", False):
            return
        settings = QSettings()
        settings.beginGroup("jxl_output")
        settings.setValue("mode", self._current_encode_mode())
        settings.setValue("quality", self.quality_spin.value())
        settings.setValue("effort", self.effort_combo.currentText())
        # 输出格式（JXL / PNG / JPG）：与编码参数一起持久化。
        settings.setValue("output_format", self.format_combo.currentText())
        # 输出文件已存在时的冲突策略（替换/询问/跳过/重命名）。
        settings.setValue("on_exist", self.on_exist_combo.currentData()
                          or self.on_exist_combo.currentText())
        # 删除原文件：勾选后成功转换的源文件移入回收站（QSettings 直接存 bool）。
        settings.setValue("delete_original", self.delete_original_check.isChecked())
        # 保持时间戳：输出文件继承原文件的创建/修改时间（QSettings 直接存 bool）。
        settings.setValue("preserve_ctime", self.preserve_ctime_check.isChecked())
        settings.setValue("preserve_mtime", self.preserve_mtime_check.isChecked())
        # 编码结果更大时丢弃输出（保留原文件）：仅 JXL 输出生效。
        settings.setValue("discard_if_larger", self.discard_if_larger_check.isChecked())
        # 保留文件夹结构 / 保留上级目录：仅「自定义文件夹」输出模式生效。
        settings.setValue("preserve_structure", self.structure_check.isChecked())
        settings.setValue("preserve_parent", self.parent_check.isChecked())
        # 保留原始扩展名：开启后输出文件沿用输入扩展名（QSettings 直接存 bool）。
        settings.setValue("preserve_original_extension", self.preserve_ext_check.isChecked())
        self._save_advanced(settings)
        # 自定义命令：勾选状态 + 已编辑的命令文本。
        settings.setValue("custom_cmd_on", self.custom_cmd_check.isChecked())
        settings.setValue("custom_cmd_text", self.cmd_edit.text())
        # 转换完毕之后的后处理动作（4 个独立开关，重启后由 _load_jxl_output 恢复）。
        settings.setValue("open_explorer", self.open_explorer_check.isChecked())
        settings.setValue("clear_input", self.clear_input_check.isChecked())
        settings.setValue("beep", self.beep_check.isChecked())
        settings.setValue("exit_after", self.exit_after_check.isChecked())
        settings.endGroup()

    def _update_discard_checkbox_state(self):
        """按当前输出格式刷新「编码结果更大时丢弃输出」复选框的可用状态。

        JXL 输出时启用（该选项才有意义）；选 PNG/JPEG 输出时置灰禁用，
        因为这两种格式不能因「输出更大」而被丢弃（用户是主动选定它们的）。
        """
        enabled = self._current_output_format() == "jxl"
        self.discard_if_larger_check.setEnabled(enabled)

    def _update_structure_checkbox_state(self):
        """按输出模式刷新「保留文件夹结构」及其子选项「保留上级目录」的可用状态。

        - 仅「文件夹」（自定义文件夹）输出模式下，保留结构才有意义
          （「原文件夹」模式下输出本就落在源文件各自所在目录，结构天然保留）；
        - 子选项「保留上级目录」额外要求主选项已勾选。
        """
        main_on = self.custom_folder_radio.isChecked()
        self.structure_check.setEnabled(main_on)
        self.parent_check.setEnabled(main_on and self.structure_check.isChecked())

    def _load_jxl_output(self):
        """Restore persisted JXL encode parameters onto the output-tab widgets.

        Safe to call only after the output tab (and thus the encode controls)
        has been built. Each mode's own detailed parameters are preserved even
        while disabled, so toggling back reuses the last value.
        """
        self._jxl_loading = True
        settings = QSettings()
        settings.beginGroup("jxl_output")
        mode = settings.value("mode", "lossy")
        quality = int(settings.value("quality", 90))
        effort = settings.value("effort", "7")

        if mode == "lossless":
            self.lossless_radio.setChecked(True)
        elif mode == "lossless_jpeg":
            self.lossless_jpeg_radio.setChecked(True)
        else:
            self.lossy_radio.setChecked(True)
        # Apply the mode-driven enable/disable state (and re-save) first.
        self._on_encode_mode_changed()
        # Restore the advanced params now that the per-mode enable state is set.
        self._load_advanced(settings)
        # Restore the displayed values; a disabled quality control still keeps
        # its value so returning to 有損 reuses it.
        self.quality_spin.setValue(quality)
        self.quality_slider.setValue(quality)
        if self.effort_combo.findText(effort) >= 0:
            self.effort_combo.setCurrentText(effort)
        # 恢复输出格式（JXL / PNG / JPG）。findText 校验避免脏 ini 触发异常。
        fmt = settings.value("output_format", "JPEG XL (*.jxl)")
        if self.format_combo.findText(fmt) >= 0:
            self.format_combo.setCurrentText(fmt)
        # 恢复输出文件已存在时的冲突策略（替换/询问/跳过/重命名）。
        on_exist = settings.value("on_exist", "替换")
        # 按 userData 匹配（ini 里存的一直是中文 ID），找不到再退回按显示名匹配，
        # 兼容早期可能写入的脏值。
        if self.on_exist_combo.findData(on_exist) >= 0:
            self.on_exist_combo.setCurrentIndex(
                self.on_exist_combo.findData(on_exist))
        elif self.on_exist_combo.findText(on_exist) >= 0:
            self.on_exist_combo.setCurrentText(on_exist)
        # 恢复「删除原文件」勾选状态（INI 把 bool 存为字符串，需显式解析）。
        self.delete_original_check.setChecked(
            str(settings.value("delete_original", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        # 恢复「保持原创建时间」/「保持原修改时间」勾选状态（同 delete_original 解析）。
        self.preserve_ctime_check.setChecked(
            str(settings.value("preserve_ctime", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        self.preserve_mtime_check.setChecked(
            str(settings.value("preserve_mtime", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        # 恢复「编码结果更大时丢弃输出（保留原文件）」勾选状态（同 delete_original 解析）。
        self.discard_if_larger_check.setChecked(
            str(settings.value("discard_if_larger", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        # 恢复「保留原始扩展名」勾选状态（同 delete_original 解析）。
        self.preserve_ext_check.setChecked(
            str(settings.value("preserve_original_extension", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        # 恢复「保留文件夹结构」/「保留上级目录」勾选状态（同 delete_original 解析）。
        self.structure_check.setChecked(
            str(settings.value("preserve_structure", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        self.parent_check.setChecked(
            str(settings.value("preserve_parent", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        # 恢复「转换完毕之后的后处理动作」4 个开关（INI 把 bool 存为字符串，需显式解析）。
        self.open_explorer_check.setChecked(
            str(settings.value("open_explorer", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        self.clear_input_check.setChecked(
            str(settings.value("clear_input", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        self.beep_check.setChecked(
            str(settings.value("beep", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        self.exit_after_check.setChecked(
            str(settings.value("exit_after", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        # 根据恢复后的输出格式刷新「丢弃输出」复选框的可用状态（PNG/JPEG 时置灰）。
        self._update_discard_checkbox_state()
        # 根据恢复后的输出模式刷新「保留文件夹结构」及其子选项的可用状态。
        self._update_structure_checkbox_state()
        # 恢复自定义命令：用 blockSignals 避免触发 _on_custom_cmd_toggled 的
        # 预填逻辑覆盖已持久化的命令文本。
        self.custom_cmd_check.blockSignals(True)
        # QSettings(INI) 把 bool 存成字符串 "true"/"false"，必须用字符串显式
        # 解析——直接 bool(settings.value(...)) 会让 "false" 也判为 True，
        # 导致「取消勾选后重新打开又变勾选」。
        self.custom_cmd_check.setChecked(
            str(settings.value("custom_cmd_on", False)).strip().lower()
            in ("true", "1", "yes", "on")
        )
        self.cmd_edit.setText(str(settings.value("custom_cmd_text", "")))
        if self.custom_cmd_check.isChecked():
            self.cmd_edit.setReadOnly(False)
            # 持久化为勾选：恢复时同样整体置灰编码参数（blockSignals 期间不会
            # 触发 _on_custom_cmd_toggled，故在此手动禁用）。
            self._set_encoding_controls_disabled()
        else:
            self.cmd_edit.setReadOnly(True)
            self._update_cmd_preview()
        self.custom_cmd_check.blockSignals(False)
        settings.endGroup()
        self._jxl_loading = False

    def closeEvent(self, event):
        # Persist the window layout so "where I left it" survives a restart.
        self._save_geometry()
        # Persist the JXL output parameters so they survive a restart too.
        self._save_jxl_output()
        # Persist the output-location / filename settings.
        self._save_output_settings()
        # Persist the conversion-process CPU priority.
        self._save_conversion_settings()
        # Persist the Input-tab "查看" view mode.
        self._save_view_mode()
        # Persist the chosen UI theme.
        self._save_theme()
        # Persist the action list (if "退出时保存动作列表" is enabled; otherwise
        # clears any previously stored list so it is not resurrected next launch).
        self._save_actions_setting()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Drag & drop: accept files/folders dropped anywhere on the window.
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        # 按 root 分组收集：文件夹拖入时其根=该文件夹，单文件拖入时根=None
        # （由 _add_input_paths 回退到文件自身所在目录）。
        by_root = {}
        for url in urls:
            if url.isLocalFile():
                path = os.path.normpath(url.toLocalFile())
                if os.path.isfile(path):
                    by_root.setdefault(None, []).append(path)
                elif os.path.isdir(path):
                    by_root.setdefault(path, []).extend(
                        self._collect_images_from_folder(path)
                    )
        added_total = sum(len(v) for v in by_root.values())
        if added_total:
            self.tabs.setCurrentWidget(self.input_tab)
            before = len(self.input_files)
            for root, files in by_root.items():
                self._add_input_paths(files, root=root)
            self.log_edit.appendPlainText(
                i18n.t("通过拖拽添加了 %d 个文件。") % (len(self.input_files) - before)
            )
        else:
            self.log_edit.appendPlainText(i18n.t("拖拽内容中没有可添加的文件。"))
        event.acceptProposedAction()

    def _collect_images_from_folder(self, folder):
        """Recursively collect all image files inside a folder."""
        result = []
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                ext = os.path.splitext(name)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    result.append(os.path.join(root, name))
        return result

    # ------------------------------------------------------------------
    # Input tab slots
    # ------------------------------------------------------------------
    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, i18n.t("选择输入文件"), "",
            i18n.t("图像文件 (*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp *.ppm *.pgm *.jxl *.avif);;所有文件 (*.*)"),
        )
        # 多选文件：每文件的根默认取其父目录（root=None 时 _add_input_paths 回退）。
        self._add_input_paths(paths, root=None)

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, i18n.t("选择文件夹"))
        if not folder:
            return
        # 递归收集（与拖拽一致），文件夹本身作为这批文件的根。
        added = self._collect_images_from_folder(folder)
        self._add_input_paths(added, root=folder)
        if not added:
            self.statusBar().showMessage(i18n.t("该文件夹内未发现支持的图像文件"))

    def _add_input_paths(self, paths, root=None):
        now = time.time()
        for path in paths:
            if path and path not in self.input_files:
                self.input_files.append(path)
                self._table_added[path] = now
                self._file_meta.pop(path, None)  # recompute on next paint
                # 记录根目录：显式传入则用之，否则回退到文件自身所在目录。
                self.input_roots[path] = root if root is not None else os.path.dirname(path)
        self._refresh_input_views()
        self.statusBar().showMessage(i18n.t("已添加 %d 个文件") % len(self.input_files))

    def _refresh_list(self):
        mode = getattr(self, "_last_view", "缩略图")
        is_icon = mode in THUMB_SIZES
        icon_size = THUMB_SIZES.get(mode, QSize(96, 96))
        # 作废上一轮任何在途的缩略图 worker：自增 epoch，旧 worker 的结果会被
        # _drain_thumb_results 按 epoch 丢弃，绝不回填到新列表的 item 上。
        if getattr(self, "_thumb_timer", None) is not None:
            self._thumb_timer.stop()
            self._thumb_timer = None
        self._stop_thumb_drain()
        self._thumb_epoch += 1
        self._thumb_items = {}
        self._thumb_results = queue.Queue()
        self._thumb_inflight = 0
        self._thumb_paused = False
        self._thumb_queue = []
        # Preserve the scroll position across the rebuild so a reorder (or any
        # refresh) does not snap the view back to the top.
        vbar = self.input_list.verticalScrollBar()
        hbar = self.input_list.horizontalScrollBar()
        saved_v = vbar.value() if vbar is not None else 0
        saved_h = hbar.value() if hbar is not None else 0
        gs = GRID_SIZES.get(mode, QSize(122, 154))
        box_square = min(
            gs.width(), gs.height() - self.list_delegate.NAME_BAND
        ) - 2 * THUMB_PAD
        placeholder = self._placeholder_thumbnail(box_square)
        # 缩略图按 box_square * dpr 的实际设备像素解码，delegate 1:1 绘制；
        # 整批 item 同 box_square、同 dpr，故 px 只算一次。
        dpr = self._thumb_dpr()
        px = max(1, int(round(box_square * dpr)))
        self.input_list.clear()
        for path in self.input_files:
            name = os.path.basename(path)
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            if is_icon:
                # 先显示灰占位 + 同步给个基础 tooltip（_image_info 很快，不含
                # 解码）；重活（缩略图解码）移交给子线程线程池，结果经
                # _thumb_items 映射回填，避免阻塞 GUI。
                item.setData(Qt.DecorationRole, placeholder)
                self._thumb_items[path] = item
                self._thumb_queue.append((path, box_square, px))
            else:
                tip = self._info_cache.get(path)
                if tip is None:
                    tip = self._image_info(path)
                    self._info_cache[path] = tip
                item.setToolTip(tip)
                # 列表视图下异步补齐尺寸（详情视图不可见，不在此投 worker）。
                self._ensure_dims(path)
            self.input_list.addItem(item)
        self._apply_filter(self.filter_edit.text())
        self.input_list.updateGeometries()
        if vbar is not None:
            vbar.setValue(saved_v)
        if hbar is not None:
            hbar.setValue(saved_h)
        # Qt defers the scrollbar range recomputation past this synchronous
        # rebuild, so the value set above can be clamped; restore it once more
        # after the layout settles so a reorder never snaps the view to top.
        self._restore_scroll(vbar, hbar, saved_v, saved_h)
        # Kick off batched thumbnail generation (no-op when not in icon mode).
        # 投递节拍器每 tick 向线程池投一批 worker；真正解码在子线程完成。
        if self._thumb_queue:
            self._thumb_timer = QTimer(self)
            self._thumb_timer.timeout.connect(self._process_thumb_batch)
            self._thumb_timer.start(0)

    def _placeholder_thumbnail(self, size):
        """A gray square pixmap used as the temporary thumbnail while the real
        one is being decoded asynchronously. Cached per size.

        Returns a plain ``QPixmap`` (not wrapped in ``QIcon``) so the delegate
        can draw it 1:1 without going through ``QIcon.pixmap()`` -- which would
        re-render the thumbnail whenever its cache key disagrees with the view's
        device pixel ratio.
        """
        cached = self._placeholder_cache.get(size)
        if cached is not None:
            return cached
        dpr = self._thumb_dpr()
        px = max(1, int(round(size * dpr)))
        pix = QPixmap(px, px)
        pix.fill(THUMB_PLACEHOLDER_BG)
        self._placeholder_cache[size] = pix
        return pix

    def _process_thumb_batch(self):
        """每个 tick 向线程池投递一批缩略图解码 worker（不再在主线程同步解码）。

        真正的解码在子线程完成，结果经 ``_thumb_results`` 队列回传，由
        ``_drain_thumb_results`` 在主线程回填。投递节拍器停转时机不变：队列清空即停。
        """
        queue = getattr(self, "_thumb_queue", None)
        if not queue:
            if getattr(self, "_thumb_timer", None) is not None:
                self._thumb_timer.stop()
                self._thumb_timer = None
            return
        if getattr(self, "_thumb_paused", False):
            return  # 拖拽/框选中：暂不投递，等待 _resume_thumb_batch
        if self._thumb_pool is None:
            self._thumb_pool = QThreadPool(self)
        epoch = self._thumb_epoch
        batch = queue[:THUMB_BATCH]
        self._thumb_queue = queue[THUMB_BATCH:]
        for path, box_square, px in batch:
            worker = self._ThumbWorker(self, epoch, path, box_square, px)
            self._thumb_inflight += 1
            self._thumb_pool.start(worker)
        self._start_thumb_drain()
        if not self._thumb_queue:
            if getattr(self, "_thumb_timer", None) is not None:
                self._thumb_timer.stop()
                self._thumb_timer = None

    def _pause_thumb_batch(self):
        """Stop the thumbnail decode timer WITHOUT discarding the queue, so a
        drag / rubber-band selection (and its auto-scroll) is never starved by
        synchronous image decoding on the main thread. Resume with
        ``_resume_thumb_batch`` once the interaction finishes.

        This is what keeps rubber-band selection smooth at the page edges: the
        auto-scroll timer and the per-tick thumbnail decoding used to fight for
        the same event loop, so scrolling stuttered badly while images decoded.
        """
        timer = getattr(self, "_thumb_timer", None)
        if timer is not None:
            timer.stop()
            self._thumb_timer = None
        self._thumb_paused = True

    def _resume_thumb_batch(self):
        """Restart a paused thumbnail decode batch if work is still queued."""
        if not getattr(self, "_thumb_paused", False):
            return
        self._thumb_paused = False
        queue = getattr(self, "_thumb_queue", None)
        if queue and getattr(self, "_thumb_timer", None) is None:
            self._thumb_timer = QTimer(self)
            self._thumb_timer.timeout.connect(self._process_thumb_batch)
            self._thumb_timer.start(0)

    def _refresh_table(self):
        # The header is built ONCE (see _build_table_header) and then left
        # alone: Qt owns the visual column order (from header drags) and the
        # per-column hidden / width state. We never call setHorizontalHeaderLabels
        # again, so a logical index stays pinned to its TABLE_COLUMNS position
        # forever -- that is what makes header-click->sort and column-drag->
        # reorder coexist cleanly, and what keeps the column layout STABLE across
        # view switches (the old code rebuilt the header on every refresh, which
        # renumbered logical indices and silently corrupted the sort mapping and
        # the remembered widths).
        table = self.input_table
        header = table.horizontalHeader()
        vbar = table.verticalScrollBar()
        hbar = table.horizontalScrollBar()
        saved_v = vbar.value() if vbar is not None else 0
        saved_h = hbar.value() if hbar is not None else 0

        if not self._table_header_built:
            self._build_table_header()
            self._table_header_built = True

        # Sort (view-only): order a copy of input_files by the active column so
        # the underlying processing order (input_files) is never changed by a
        # header click. Numeric columns sort numerically via _sort_key.
        rows = list(self.input_files)
        sort_key = self._table_sort
        if sort_key is not None:
            skey, asc = sort_key
            rows.sort(key=lambda p: self._sort_key(p, skey), reverse=not asc)

        header.blockSignals(True)
        # Full rebuild: clear first so a row drag never leaves a stale / empty
        # cell behind (this is what previously caused "drag-reorder blanks a
        # row's text" in the Details view). Every row is recreated from
        # input_files, so no leftover item survives.
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for r, path in enumerate(rows):
            meta = self._file_metadata(path)
            # Same hover-info tooltip the list / thumbnail views already show:
            # cache it in _info_cache so we don't re-read the file on every
            # refresh (and keep it in sync with the list view's tooltip text).
            tip = self._info_cache.get(path)
            if tip is None:
                tip = self._image_info(path)
                self._info_cache[path] = tip
            # 仅当详情视图真正可见时才异步补齐尺寸；不可见时尺寸由缩略图 /
            # 列表视图的对应路径负责，避免对不可见表格重复投解码 worker。
            if getattr(self, "input_stack", None) is not None and \
                    self.input_stack.currentWidget() is self.input_table:
                self._ensure_dims(path)
            for c, (key, _l, _v, _w, _a) in enumerate(TABLE_COLUMNS):
                item = QTableWidgetItem(self._table_cell_text(meta, key))
                item.setTextAlignment(_a)
                item.setData(Qt.UserRole, path)
                item.setToolTip(tip)
                table.setItem(r, c, item)

        # Column widths (the last visible column is left to stretch,
        # Explorer-style, so its layout-controlled width is never stored).
        last_visible = -1
        for c in range(len(TABLE_COLUMNS)):
            if not table.isColumnHidden(c):
                last_visible = c
        for c, (key, _l, _v, _w, _a) in enumerate(TABLE_COLUMNS):
            if c == last_visible:
                continue
            w = self._table_widths.get(key)
            if w:
                table.setColumnWidth(c, w)

        # Column visibility.
        for c, (key, _l, _v, _w, _a) in enumerate(TABLE_COLUMNS):
            table.setColumnHidden(c, key in self._table_hidden)
        header.blockSignals(False)

        # Row drag-reorder is ALWAYS allowed, even when a sort is active.
        # Dropping a row while sorted clears the sort (see InputTableWidget.
        # dropEvent), so the rearranged order becomes the new explicit custom
        # order and the header arrow disappears.
        table.setDragEnabled(True)
        table.setDragDropMode(QAbstractItemView.InternalMove)

        # Sort indicator arrow on the active column.
        header.setSortIndicatorShown(sort_key is not None)
        if sort_key is not None:
            try:
                idx = [k for (k, _l, _v, _w, _a) in TABLE_COLUMNS].index(sort_key[0])
                header.setSortIndicator(
                    idx,
                    Qt.AscendingOrder if sort_key[1] else Qt.DescendingOrder,
                )
            except ValueError:
                header.setSortIndicatorShown(False)

        self._apply_filter(self.filter_edit.text())
        table.updateGeometries()
        if vbar is not None:
            vbar.setValue(saved_v)
        if hbar is not None:
            hbar.setValue(saved_h)
        self._restore_scroll(vbar, hbar, saved_v, saved_h)

    def _build_table_header(self):
        # Build the header exactly once. Logical index == position in
        # TABLE_COLUMNS, and that never changes afterwards, so every header
        # handler can resolve a clicked logical index to a column key directly
        # (no drift after a header drag). Column order / visibility / widths are
        # then owned by Qt and persist across refreshes.
        table = self.input_table
        header = table.horizontalHeader()
        header.blockSignals(True)
        # Clear and recreate the sections so the visual order is guaranteed to
        # start at the default (logical == visual). This is essential for
        # "重置列设置", which triggers a rebuild to undo any header drags.
        table.setColumnCount(0)
        table.setColumnCount(len(TABLE_COLUMNS))
        table.setHorizontalHeaderLabels(
            [i18n.t(label) for (_k, label, _v, _w, _a) in TABLE_COLUMNS]
        )
        header.setSectionsMovable(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setHighlightSections(False)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        for c, (key, _l, _v, w, _a) in enumerate(TABLE_COLUMNS):
            table.setColumnWidth(c, w)
        for c, (key, _l, _v, _w, _a) in enumerate(TABLE_COLUMNS):
            table.setColumnHidden(c, key in self._table_hidden)
        if self._table_sort is not None:
            idx = [k for (k, _l, _v, _w, _a) in TABLE_COLUMNS].index(self._table_sort[0])
            header.setSortIndicator(
                idx,
                Qt.AscendingOrder if self._table_sort[1] else Qt.DescendingOrder,
            )
        # Restore the user's saved visual column order (default == TABLE_COLUMNS
        # order). Done while signals are blocked so the programmatic moves don't
        # re-trigger a save.
        order = self._table_saved_order
        if order:
            key_to_logical = {
                k: i for i, (k, _l, _v, _w, _a) in enumerate(TABLE_COLUMNS)
            }
            for i in range(len(order)):
                wanted = key_to_logical.get(order[i])
                if wanted is None:
                    continue
                if header.logicalIndex(i) != wanted:
                    header.moveSection(header.visualIndex(wanted), i)
        header.blockSignals(False)
        # Capture future header drags (column reorder) for persistence.
        header.sectionMoved.connect(self._save_table_layout)

    # ---- Details-view helpers (columns / sort / metadata) -----------------

    @staticmethod
    def _format_datetime(ts):
        if not ts:
            return "-"
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        except (ValueError, OSError):
            return "-"

    @staticmethod
    def _table_cell_text(meta, key):
        if key == "name":
            return meta["name"]
        if key == "format":
            return meta["format"]
        if key == "size":
            return meta["size_text"]
        if key == "modified":
            return meta["modified_text"]
        if key == "created":
            return meta["created_text"]
        if key == "operated":
            return meta["operated_text"]
        if key == "resolution":
            return meta["res_text"]
        if key == "ratio":
            return meta["ratio_text"]
        if key == "path":
            return meta["path"]
        return ""

    def _file_metadata(self, path):
        """Lazily compute (and cache) the per-file details-view metadata."""
        cached = self._file_meta.get(path)
        if cached is not None:
            return cached
        name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lstrip(".").upper() or "-"
        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = 0
        size_text = self._format_size(path)
        try:
            modified = os.path.getmtime(path)
        except OSError:
            modified = 0.0
        try:
            created = os.path.getctime(path)
        except OSError:
            created = 0.0
        added = self._table_added.get(path, modified)
        w, h = get_image_dims(path)
        area = w * h
        res_text = ratio_text = "-"
        ratio_value = 0.0
        if w and h:
            res_text = "%d × %d" % (w, h)
            g = math.gcd(w, h)
            rw, rh = w // g, h // g
            # Keep the ratio compact: "16:9" for clean divisors, else "1.78:1".
            ratio_text = (
                "%d:%d" % (rw, rh) if rw <= 16 or rh <= 16
                else "%.2f:1" % (w / float(h))
            )
            ratio_value = w / float(h)
        meta = {
            "name": name,
            "format": ext,
            "size_bytes": size_bytes,
            "size_text": size_text,
            "modified": modified,
            "modified_text": self._format_datetime(modified),
            "created": created,
            "created_text": self._format_datetime(created),
            "operated": added,
            "operated_text": self._format_datetime(added),
            "w": w,
            "h": h,
            "area": area,
            "res_text": res_text,
            "ratio_text": ratio_text,
            "ratio_value": ratio_value,
            "path": path,
        }
        self._file_meta[path] = meta
        return meta

    def _sort_key(self, path, key):
        m = self._file_metadata(path)
        if key == "name":
            return m["name"].lower()
        if key == "format":
            return m["format"].lower()
        if key == "size":
            return m["size_bytes"]
        if key == "modified":
            return m["modified"]
        if key == "created":
            return m["created"]
        if key == "operated":
            return m["operated"]
        if key == "resolution":
            return m["area"]
        if key == "ratio":
            return m["ratio_value"]
        if key == "path":
            return m["path"].lower()
        return m["name"].lower()

    def _on_table_header_clicked(self, logical):
        # The logical index is STABLE (== the column's position in
        # TABLE_COLUMNS) because the header is built only once, so this always
        # resolves to the column the user actually clicked -- no drift after a
        # header drag (the old bug sorted the wrong column and broke toggling).
        if not (0 <= logical < len(TABLE_COLUMNS)):
            return
        key = TABLE_COLUMNS[logical][0]
        if self._table_sort and self._table_sort[0] == key:
            self._table_sort = (key, not self._table_sort[1])
        else:
            self._table_sort = (key, True)
        self._refresh_table()
        self._save_table_layout()

    def _on_table_section_resized(self, logical, old, new):
        # Persist the resized width. Skip the stretched (last visible) column so
        # its layout-controlled width doesn't overwrite a real saved width.
        if not (0 <= logical < len(TABLE_COLUMNS)):
            return
        table = self.input_table
        last_visible = -1
        for c in range(table.columnCount()):
            if not table.isColumnHidden(c):
                last_visible = c
        if logical == last_visible:
            return
        key = TABLE_COLUMNS[logical][0]
        self._table_widths[key] = new
        self._save_table_layout()

    def _build_table_header_menu(self):
        menu = QMenu(self.input_table)
        menu.addAction(i18n.t("显示列：")).setEnabled(False)
        for key, label, _v, _w, _a in TABLE_COLUMNS:
            act = QAction(i18n.t(label), menu)
            act.setCheckable(True)
            act.setChecked(key not in self._table_hidden)
            act.triggered.connect(
                lambda _checked=False, k=key: self._toggle_table_column(k)
            )
            menu.addAction(act)
        menu.addSeparator()
        reset_act = QAction(i18n.t("重置列设置"), menu)
        reset_act.triggered.connect(self._reset_table_columns)
        menu.addAction(reset_act)
        return menu

    def _on_table_header_context_menu(self, point):
        # `point` is a global (screen) coordinate from the header's
        # contextMenuEvent override.
        self._build_table_header_menu().exec(point)

    def _toggle_table_column(self, key):
        visible_count = len(TABLE_COLUMNS) - len(self._table_hidden)
        if key in self._table_hidden:
            self._table_hidden.remove(key)
        else:
            if visible_count <= 1:
                return  # keep at least one column visible
            self._table_hidden.append(key)
            if self._table_sort and self._table_sort[0] == key:
                self._table_sort = None
        self._refresh_table()
        self._save_table_layout()

    def _reset_table_columns(self):
        self._table_hidden = [
            k for (k, _l, v, _w, _a) in TABLE_COLUMNS if not v
        ]
        self._table_widths = {
            k: w for (k, _l, _v, w, _a) in TABLE_COLUMNS
        }
        self._table_sort = None
        self._table_saved_order = []
        # Force a header rebuild so the column ORDER also resets to default.
        self._table_header_built = False
        self._refresh_table()
        self._save_table_layout()

    # ---- column-layout persistence (QSettings) ---------------------------

    def _save_table_layout(self):
        """Persist the current Details column layout to QSettings.

        Captures exactly what the user configured in the GUI: per-column
        widths (read straight from the live header, no manual px counting),
        which columns are hidden, the visual column order, and the active
        sort. Survives a restart.
        """
        table = self.input_table
        header = table.horizontalHeader()
        settings = QSettings()
        settings.beginGroup("details_columns")

        # Widths: only the user-sized columns (the stretched last column is
        # skipped by _on_table_section_resized, so it never lands here).
        widths = []
        for key, _l, _v, _w, _a in TABLE_COLUMNS:
            w = self._table_widths.get(key)
            if w:
                widths.append("%s:%d" % (key, w))
        settings.setValue("widths", widths)

        # Hidden columns.
        settings.setValue("hidden", list(self._table_hidden))

        # Visual order, derived from the live header so it always matches what
        # the user sees (independent of any saved/derived state variable).
        order = []
        if header is not None and header.count() == len(TABLE_COLUMNS):
            for v in range(header.count()):
                li = header.logicalIndex(v)
                if 0 <= li < len(TABLE_COLUMNS):
                    order.append(TABLE_COLUMNS[li][0])
        if order:
            settings.setValue("order", order)

        # Sort state. Store the direction as an int (0/1): QSettings serialises
        # a Python bool to the string "true"/"false", and bool("false") is True
        # (non-empty string) on read-back, which would flip the direction.
        if self._table_sort is not None:
            settings.setValue(
                "sort", [self._table_sort[0], 1 if self._table_sort[1] else 0]
            )
        else:
            settings.remove("sort")

        settings.endGroup()

    def _load_table_layout(self):
        """Load a previously persisted Details column layout, if any.

        Overrides the in-code defaults with whatever the user last set in the
        GUI. Safe to call even when no saved layout exists (leaves defaults
        untouched).
        """
        settings = QSettings()
        settings.beginGroup("details_columns")

        def _as_list(v):
            if v is None:
                return []
            if isinstance(v, str):
                return [v]
            try:
                return list(v)
            except TypeError:
                return []

        # Widths ("key:px").
        for entry in _as_list(settings.value("widths", [])):
            if isinstance(entry, str) and ":" in entry:
                k, v = entry.split(":", 1)
                try:
                    if any(k == kk for kk, _l, _v, _w, _a in TABLE_COLUMNS):
                        self._table_widths[k] = int(v)
                except ValueError:
                    pass

        # Hidden columns (keep at least one visible).
        hidden = [h for h in _as_list(settings.value("hidden", []))
                  if any(h == kk for kk, _l, _v, _w, _a in TABLE_COLUMNS)]
        if 0 < len(hidden) < len(TABLE_COLUMNS):
            self._table_hidden = hidden

        # Visual order (applied when the header is (re)built).
        order = [o for o in _as_list(settings.value("order", []))
                 if any(o == kk for kk, _l, _v, _w, _a in TABLE_COLUMNS)]
        if len(order) == len(TABLE_COLUMNS):
            # De-duplicate defensively; if anything is off, ignore the saved order.
            if len(set(order)) == len(order):
                self._table_saved_order = order

        # Sort state.
        s = settings.value("sort", None)
        if isinstance(s, (list, tuple)) and len(s) == 2:
            key, asc = s
            if any(key == kk for kk, _l, _v, _w, _a in TABLE_COLUMNS):
                try:
                    self._table_sort = (key, bool(int(asc)))
                except (ValueError, TypeError):
                    pass

        settings.endGroup()

    def _restore_scroll(self, vbar, hbar, saved_v, saved_h):
        """Re-apply the saved scroll offsets after the layout has settled.

        Qt recomputes the scrollbar range lazily (during the next paint/layout
        pass), so a value set synchronously right after a rebuild can be clamped
        to the old (stale) maximum and the view snaps back to the top. Re-setting
        it on the next event loop tick, once the new geometry is in place, keeps
        the scroll position stable across reorders/refreshes.
        """
        def _do():
            if vbar is not None:
                vbar.setValue(saved_v)
            if hbar is not None:
                hbar.setValue(saved_h)
        QTimer.singleShot(0, _do)

    def _refresh_input_views(self):
        # 详情表格仅在可见时重建：拖入 / 集合变化无需为不可见表格做整表重建
        # （此前还会在每条路径上同步跑 djxl 取尺寸，是拖入大图卡顿的根因）。
        if getattr(self, "input_stack", None) is not None and \
                self.input_stack.currentWidget() is self.input_table:
            self._refresh_table()
        self._refresh_list()
        # 输入集合变化会影响命令预览里的 --lossless_jpeg=0（有损 + JPG 时），
        # 这里统一刷新一次，使预览与实际命令保持同步。
        self._update_cmd_preview()
        # 输入数量是「输入 [N个]」标签标题的数据源：所有增删入口最终都会
        # 走到这里，故在此统一刷新（顺序调整走 _sync_files_from_*，另行刷新）。
        self._update_tab_titles()

    @staticmethod
    def _reorder_paths(paths, rows, target):
        """Return a reordered copy of `paths` with the items at `rows` moved to
        the insertion seam `target` (an index into the ORIGINAL `paths`,
        0..len(paths) inclusive).

        This is the correct move-within-list algorithm. The naive
        ``remaining[:t] + dragged + remaining[t:]`` shifts by one because the
        dragged items are removed first, which turns a "drop at my own seam"
        (the blue line you see beside the item) into a swap with the next item.
        Rebuilding the list explicitly keeps an item in place when it is dropped
        back at its own seam, and never drops a file.
        """
        dragged = [paths[r] for r in rows]
        drag_set = set(rows)
        new = []
        inserted = False
        for i, p in enumerate(paths):
            if i == target and not inserted:
                new.extend(dragged)
                inserted = True
            if i not in drag_set:
                new.append(p)
        if not inserted:
            new.extend(dragged)
        return new

    def _thumb_dpr(self):
        """Device pixel ratio used when rendering thumbnails (>=1).

        Uses the actual input-list widget's ratio so it matches the value the
        delegate's ``paint`` reads (``view.devicePixelRatio()``). Using the
        primary screen here previously caused a DPR mismatch on multi-monitor /
        HiDPI setups: the rendered pixmap's size then disagreed with what
        ``paint`` requested, so ``QIcon.pixmap()`` missed its cache and
        re-scaled the thumbnail on EVERY repaint -- which made rubber-band
        auto-scroll stutter badly (hundreds of re-renders per frame).
        """
        try:
            w = getattr(self, "input_list", None)
            if w is not None:
                dpr = w.devicePixelRatio()
                if dpr > 0:
                    return max(1.0, float(dpr))
        except Exception:
            pass
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                return max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            pass
        return 1.0

    def _make_thumbnail(self, path, size=96):
        """返回正方形缩略图 ``QPixmap``（letterbox 居中），主线程同步路径。

        供需要立即拿到结果的调用（如测试、同步回退）使用；常规列表缩略图已改走
        子线程异步解码（见 ``_process_thumb_batch``）。解码分辨率按 ``size * dpr``
        上限钳制，复用 ``_thumb_cache``（存 QImage）避免重复解码。
        """
        dpr = self._thumb_dpr()
        px = max(1, int(round(size * dpr)))
        img, _tip = self._decode_thumb(path, px)
        if img is None or img.isNull():
            pm = QPixmap(px, px)
            pm.fill(THUMB_PLACEHOLDER_BG)
            return pm
        return QPixmap.fromImage(img)

    @staticmethod
    def _decode_to_qimage(path, px):
        """子线程调用：把 path 解码成 px×px 离屏 ``QImage``（letterbox 居中）。

        绝不创建/操作 QPixmap（QPixmap 只能在 GUI 线程使用）。失败时返回灰色
        占位 QImage，调用方据此继续显示占位，不会崩溃。
        """
        img = QImage(px, px, QImage.Format_ARGB32)
        img.fill(THUMB_PLACEHOLDER_BG)
        try:
            display = _display_path(path)
            if display is None:
                return img
            reader = QImageReader(display)
            src_size = reader.size()
            if src_size.isValid() and not src_size.isNull():
                sw, sh = src_size.width(), src_size.height()
                # 顺带把原图尺寸记进 _DIMS_CACHE，详情/列表视图不必再单独解码。
                with _DIMS_CACHE_LOCK:
                    if _DIMS_CACHE.get(path) in (None, (0, 0)):
                        _DIMS_CACHE[path] = (sw, sh)
                scale = min(px / sw, px / sh)
                fw = max(1, int(round(sw * scale)))
                fh = max(1, int(round(sh * scale)))
                reader.setScaledSize(QSize(fw, fh))
                rimg = reader.read()
                if not rimg.isNull():
                    painter = QPainter(img)
                    painter.drawImage((px - fw) // 2, (px - fh) // 2, rimg)
                    painter.end()
                    return img
        except Exception:
            pass
        # 兜底：整图解码后缩放（原生 QImageReader 失败时的退路）。
        display = _display_path(path)
        if display is None:
            return img
        src = QImage(display)
        if src.isNull():
            return img
        with _DIMS_CACHE_LOCK:
            if _DIMS_CACHE.get(path) in (None, (0, 0)):
                _DIMS_CACHE[path] = (src.width(), src.height())
        scaled = src.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(img)
        x = (px - scaled.width()) // 2
        y = (px - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
        painter.end()
        return img

    def _decode_thumb(self, path, px):
        """线程安全的缩略图解码入口：返回 ``(QImage, tooltip)``。

        在子线程调用。复用模块级锁保护的 ``_thumb_cache``（QImage）与 ``_info_cache``，
        避免重复解码与重复读取文件头。
        """
        with _INFO_CACHE_LOCK:
            tip = self._info_cache.get(path)
        if tip is None:
            tip = self._image_info(path)
            with _INFO_CACHE_LOCK:
                self._info_cache.setdefault(path, tip)
        img_key = (path, px)
        with _THUMB_IMG_LOCK:
            cached = self._thumb_cache.get(img_key)
        if cached is not None:
            return cached, tip
        img = self._decode_to_qimage(path, px)
        with _THUMB_IMG_LOCK:
            self._thumb_cache[img_key] = img
        return img, tip

    class _ThumbWorker(QRunnable):
        """在 ``QThreadPool`` 内同步解码单张缩略图，完成后把结果放入主窗口队列。

        不在 worker 内直接操作 GUI（QPixmap / QListWidgetItem 均不可跨线程），而是
        把 ``(epoch, path, box_square, QImage, tip)`` 放进 ``host._thumb_results``，
        由主线程的 drain 定时器取出回填——彻底避开 QRunnable 信号的生命周期陷阱。
        """

        def __init__(self, host, epoch, path, box_square, px):
            super().__init__()
            self.host = host
            self.epoch = epoch
            self.path = path
            self.box_square = box_square
            self.px = px

        def run(self):
            try:
                img, tip = self.host._decode_thumb(self.path, self.px)
            except Exception:
                img, tip = None, ""
            self.host._thumb_results.put(
                (self.epoch, self.path, self.box_square, img, tip)
            )


    class _DimsWorker(QRunnable):
        """在子线程异步解码取尺寸，完成后通知主线程刷新。

        与 ``_ThumbWorker`` 同策略：不持有/操作任何 GUI 对象，只把解码完成的
        ``path`` 放入主窗口队列，由主线程 ``_drain_dims`` 抽取后刷新可见 item
        的 tooltip 与分辨率列。主线程调用 ``_ensure_dims`` 只做去重判断，
        绝不在此路径上同步跑 ``djxl``。
        """

        def __init__(self, host, path):
            super().__init__()
            self.host = host
            self.path = path

        def run(self):
            try:
                disp = _display_path(self.path)
                if disp is not None:
                    reader = QImageReader(disp)
                    if reader.canRead():
                        size = reader.size()
                        if size.isValid() and not size.isNull():
                            with _DIMS_CACHE_LOCK:
                                _DIMS_CACHE[self.path] = (
                                    size.width(), size.height())
            except Exception:
                pass
            finally:
                # 无论解码成功与否都通知主线程（成功刷新尺寸，失败则从在途集合移除）。
                try:
                    self.host._dims_queue.put(self.path)
                except Exception:
                    pass

    def _ensure_dims(self, path):
        """尺寸未知时异步解码补齐，绝不阻塞主线程。

        被 ``_refresh_table`` / ``_refresh_list`` 每行调用：主线程只做去重判断，
        真正的解码在 ``_thumb_pool`` 子线程跑；完成后 ``_drain_dims`` 刷新可见
        item。已解码（缓存命中）或已在途则跳过。
        """
        with _DIMS_CACHE_LOCK:
            cur = _DIMS_CACHE.get(path)
        if cur and cur != (0, 0):
            return
        if path in self._dims_inflight:
            return
        self._dims_inflight.add(path)
        if self._thumb_pool is None:
            self._thumb_pool = QThreadPool(self)
        self._thumb_pool.start(self._DimsWorker(self, path))
        self._start_dims_drain()

    def _start_dims_drain(self):
        if getattr(self, "_dims_drain_timer", None) is not None:
            return
        t = QTimer(self)
        t.timeout.connect(self._drain_dims)
        t.start(16)
        self._dims_drain_timer = t

    def _drain_dims(self):
        q = self._dims_queue
        while not q.empty():
            path = q.get()
            self._dims_inflight.discard(path)
            self._refresh_dims_for(path)
        if not self._dims_inflight and q.empty():
            t = getattr(self, "_dims_drain_timer", None)
            if t is not None:
                t.stop()
                self._dims_drain_timer = None

    def _refresh_dims_for(self, path):
        """尺寸补齐后刷新该 path 在所有可见视图里的 tooltip 与分辨率/比率列。"""
        with _INFO_CACHE_LOCK:
            self._info_cache.pop(path, None)
        self._file_meta.pop(path, None)
        tip = self._image_info(path)
        with _INFO_CACHE_LOCK:
            self._info_cache[path] = tip
        # 详细信息表格
        table = self.input_table
        for r in range(table.rowCount()):
            it0 = table.item(r, 0)
            if it0 is None or it0.data(Qt.UserRole) != path:
                continue
            for c in range(table.columnCount()):
                cell = table.item(r, c)
                if cell is None:
                    continue
                cell.setToolTip(tip)
                if TABLE_COLUMNS[c][0] in ("resolution", "ratio"):
                    cell.setText(
                        self._table_cell_text(
                            self._file_metadata(path), TABLE_COLUMNS[c][0]))
            break
        # 列表视图
        for i in range(self.input_list.count()):
            it = self.input_list.item(i)
            if it is not None and it.data(Qt.UserRole) == path:
                it.setToolTip(tip)
                break
        # 缩略图视图
        titem = self._thumb_items.get(path)
        if titem is not None:
            titem.setToolTip(tip)

    def _drain_thumb_results(self):
        """主线程节拍器：取出子线程解码结果并回填到对应 item。

        用 epoch 令牌丢弃陈旧结果（列表已重建的旧 worker）；已从列表移除的 item
        通过 ``_thumb_items`` 映射查找失败而跳过。回填只做 QPixmap.fromImage
        （一次浅拷贝，极快），不触碰任何解码重活。
        """
        q = self._thumb_results
        while not q.empty():
            epoch, path, box_square, img, tip = q.get()
            self._thumb_inflight = max(0, self._thumb_inflight - 1)
            if epoch != self._thumb_epoch:
                continue  # 列表已重建，结果过期，丢弃
            item = self._thumb_items.get(path)
            if item is None:
                continue  # item 已不在当前列表
            if img is not None and not img.isNull():
                pm = QPixmap.fromImage(img)
                item.setData(Qt.DecorationRole, pm)
                # 缩略图解码完成：原图尺寸已可得，刷新 tooltip（含尺寸信息）。
                self._refresh_dims_for(path)
            elif tip:
                item.setToolTip(tip)
            self._thumb_items.pop(path, None)
        if self._thumb_inflight <= 0 and q.empty():
            self._stop_thumb_drain()

    def _start_thumb_drain(self):
        if getattr(self, "_thumb_drain_timer", None) is not None:
            return
        t = QTimer(self)
        t.timeout.connect(self._drain_thumb_results)
        t.start(16)  # ~60fps 抽取，解码完成尽快显示
        self._thumb_drain_timer = t

    def _stop_thumb_drain(self):
        t = getattr(self, "_thumb_drain_timer", None)
        if t is not None:
            t.stop()
            self._thumb_drain_timer = None

    def _image_info(self, path):
        name = os.path.basename(path)
        is_jxl = path.lower().endswith(".jxl")
        ext = "JXL" if is_jxl else (os.path.splitext(path)[1].lstrip(".").upper() or i18n.t("未知"))
        size_text = self._format_size(path)
        dims = i18n.t("未知")
        w, h = get_image_dims(path)
        if w and h:
            dims = "%d x %d" % (w, h)
        # EXR 额外展示头部解析出的通道 / 压缩信息（悬停提示更实用）。
        extra = ""
        if path.lower().endswith(".exr"):
            try:
                meta = formats.parse_exr_header(path)
                ch = "、".join(
                    "%s(%s)" % (c["name"], c["type"]) for c in meta.get("channels", [])
                ) or i18n.t("未知")
                extra = i18n.t("\n通道：%s\n压缩：%s") % (ch, meta.get("compression") or i18n.t("未知"))
            except Exception:
                extra = ""
        return i18n.t("文件名：%s\n格式：%s\n尺寸：%s\n大小：%s\n路径：%s%s") % (
            name, ext, dims, size_text, path, extra,
        )

    @staticmethod
    def _format_size(path):
        try:
            size = os.path.getsize(path)
        except OSError:
            return "-"
        if size >= 1024 * 1024:
            return "%.1f MB" % (size / (1024.0 * 1024.0))
        return "%.1f KB" % (size / 1024.0)

    def _on_view_changed(self, text):
        # De-duplicate: currentIndexChanged and activated both fire on a popup
        # pick, and the user may re-pick the current item. Skip redundant work
        # (and the double _refresh_list flicker) when nothing actually changes.
        if text == getattr(self, "_last_view", None):
            return
        self._last_view = text
        if getattr(self, "view_button", None) is not None:
            # text 是内部 ID，显示给用户的是它的译文
            self.view_button.setText(i18n.t(text))
        # Persist immediately so the choice survives even an abnormal exit.
        # Must run before the per-mode early returns below (the "详细信息"
        # branch returns early and would otherwise skip the save).
        # The guard inside _save_view_mode blocks the load-time restore.
        self._save_view_mode()
        if text == "详细信息":
            self.input_stack.setCurrentWidget(self.input_table)
            self._refresh_table()
            return
        self.input_stack.setCurrentWidget(self.input_list)
        if text == "列表":
            # Drag-select only (no reorder); use the default delegate and let
            # Qt lay out normal list rows. Clear the grid (an empty QSize) and
            # the icon size so rows are packed normally; setting gridSize to
            # (0,0) previously collapsed every row onto the same spot.
            self.input_list.setViewMode(QListWidget.ListMode)
            self.input_list.setDragDropMode(QAbstractItemView.NoDragDrop)
            self.input_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.input_list.setGridSize(QSize())
            self.input_list.setIconSize(QSize(0, 0))
            # A fresh default delegate (not None) is required: setItemDelegate(
            # None) does not restore the built-in delegate in Qt6 and leaves the
            # list unable to compute row geometry (rows collapse / overlap).
            self.input_list.setItemDelegate(QStyledItemDelegate(self.input_list))
        else:
            # Thumbnail modes: fixed-ratio grid + drag-sort. The custom delegate
            # forces every cell to the grid size (constant ratio, name always
            # visible).
            #
            # uniformItemSizes MUST be True here: every thumbnail cell is the
            # same grid size, so Qt can use a single cached item size and lay
            # out in O(1). With it OFF (the old setting), Qt recomputed the
            # geometry of EVERY item on each layout pass; when you drag a tile to
            # the viewport edge, Qt's auto-scroll re-lays-out the whole list
            # every frame, which stuttered badly with many images. Snap + Adjust
            # let items be drag-reordered and reflow when the window is resized
            # (instead of staying fixed at N columns).
            self.input_list.setViewMode(QListWidget.IconMode)
            self.input_list.setDragDropMode(QAbstractItemView.InternalMove)
            self.input_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.input_list.setDragEnabled(True)
            self.input_list.setDefaultDropAction(Qt.MoveAction)
            icon_size = THUMB_SIZES.get(text, QSize(96, 96))
            self.input_list.setIconSize(icon_size)
            self.input_list.setGridSize(GRID_SIZES.get(text, QSize(122, 154)))
            self.input_list.setUniformItemSizes(True)
            self.input_list.setMovement(QListWidget.Snap)
            self.input_list.setResizeMode(QListWidget.Adjust)
            self.input_list.setSpacing(0)
            self.input_list.setItemDelegate(self.list_delegate)
        self.input_list.setWordWrap(False)
        self.input_list.setTextElideMode(Qt.ElideRight)
        self._refresh_list()

    def _selected_paths(self):
        """Return the set of input file paths currently selected, regardless of
        which view (table or list) is active. Paths are used (not row indices)
        so removal stays correct even when the details table is sorted and its
        display order no longer matches input_files order."""
        if self.input_stack.currentWidget() is self.input_table:
            paths = set()
            for index in self.input_table.selectedIndexes():
                item = self.input_table.item(index.row(), 0)
                p = item.data(Qt.UserRole) if item is not None else None
                if p:
                    paths.add(p)
            return paths
        return {
            item.data(Qt.UserRole)
            for item in self.input_list.selectedItems()
            if item.data(Qt.UserRole)
        }

    def _sync_files_from_list(self):
        """Rebuild input_files from the (reordered) list item order."""
        order = []
        for i in range(self.input_list.count()):
            item = self.input_list.item(i)
            path = item.data(Qt.UserRole)
            if path:
                order.append(path)
        self.input_files = order
        self._refresh_table()
        self._update_tab_titles()
        self.statusBar().showMessage(i18n.t("已调整顺序，共 %d 个文件") % len(self.input_files))

    def _sync_files_from_table(self):
        """Rebuild input_files from the (reordered) table row order."""
        order = []
        for r in range(self.input_table.rowCount()):
            item = self.input_table.item(r, 0)
            path = item.data(Qt.UserRole) if item is not None else None
            if path:
                order.append(path)
        self.input_files = order
        self._refresh_list()
        self._update_tab_titles()
        self.statusBar().showMessage(i18n.t("已调整顺序，共 %d 个文件") % len(self.input_files))

    def _on_remove_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        self.input_files = [p for p in self.input_files if p not in paths]
        for p in paths:
            self._file_meta.pop(p, None)
            self._table_added.pop(p, None)
        self._refresh_input_views()
        self.statusBar().showMessage(i18n.t("已移除，剩余 %d 个文件") % len(self.input_files))

    def _on_clear_inputs(self):
        self.input_files.clear()
        self._file_meta.clear()
        self._table_added.clear()
        self._refresh_input_views()
        self.statusBar().showMessage(i18n.t("已清空输入列表"))

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _apply_filter(self, text):
        text = (text or "").strip().lower()
        table = self.input_table
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            path = item.data(Qt.UserRole) if item is not None else None
            name = os.path.basename(path).lower() if path else ""
            match = (text == "") or (text in name)
            table.setRowHidden(r, not match)
        for index, path in enumerate(self.input_files):
            item = self.input_list.item(index)
            if item is not None:
                name = os.path.basename(path).lower()
                match = (text == "") or (text in name)
                item.setHidden(not match)

    def _on_remove_filtered(self, mode):
        text = (self.filter_edit.text() or "").strip().lower()
        keep = []
        removed = 0
        for path in self.input_files:
            name = os.path.basename(path).lower()
            matched = (text == "") or (text in name)
            if mode == "filtered":
                if matched:
                    removed += 1
                else:
                    keep.append(path)
            else:  # unfiltered
                if matched:
                    keep.append(path)
                else:
                    removed += 1
        self.input_files = keep
        self._refresh_input_views()
        self.statusBar().showMessage(
            i18n.t("已移除 %d 个文件，剩余 %d 个") % (removed, len(self.input_files))
        )

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def _open_preview(self, path):
        if not path or not os.path.isfile(path):
            return
        dialog = PreviewDialog(path, self)
        dialog.exec()

    # ------------------------------------------------------------------
    # Actions tab slots
    # ------------------------------------------------------------------
    def _fit_on_source_change(self):
        """「切换预览源后自动适应窗口」复选框的当前值（控件未就绪时按 True）。"""
        cb = getattr(self, "fit_on_source_change_check", None)
        if cb is None:
            return True
        return bool(cb.isChecked())

    def _load_fit_on_source_change(self):
        """恢复「切换预览源后自动适应窗口」勾选状态（默认启用）。"""
        settings = QSettings()
        settings.beginGroup("actions")
        checked = settings.value("fit_on_source_change", True, type=bool)
        settings.endGroup()
        cb = getattr(self, "fit_on_source_change_check", None)
        if cb is not None:
            cb.setChecked(bool(checked))

    def _save_fit_on_source_change(self):
        """持久化「切换预览源后自动适应窗口」勾选状态。"""
        settings = QSettings()
        settings.beginGroup("actions")
        settings.setValue(
            "fit_on_source_change", self._fit_on_source_change())
        settings.endGroup()

    def _update_tab_titles(self):
        """刷新「输入」/「动作」标签标题上的数量统计。

        输入 → 「输入 [N个]」；动作 → 「动作 [启用数/总数]」。
        增删输入文件、增删动作、勾选启用状态变化时都要调用。
        """
        tabs = getattr(self, "tabs", None)
        if tabs is None:
            return
        input_idx = getattr(self, "_input_tab_index", 0)
        action_idx = getattr(self, "_actions_tab_index", 1)
        count = len(getattr(self, "input_files", None) or [])
        if 0 <= input_idx < tabs.count():
            tabs.setTabText(input_idx, i18n.t("输入 [%d个]") % count)
        if 0 <= action_idx < tabs.count():
            enabled, total = self._action_counts()
            tabs.setTabText(action_idx, i18n.t("动作 [%d/%d]") % (enabled, total))

    def _insert_action_item(self, row, action, render_preview=True):
        """在 ``row`` 处插入一个**全新**的动作项（item 与 widget 都新建）。

        所有动作项的创建都必须走这里，保证信号连接与启用状态一致。
        ⚠️ 绝不能复用从列表摘下来的 item / widget（见 ``_on_move_action_for``
        的注释）—— 那是点击上移/下移闪退的根因。

        ``row`` 为 None 或越界时追加到末尾。``render_preview`` 控制是否立即
        刷新动作预览——批量恢复时为 False，待全部加完后再统一刷新一次。
        """
        item = QListWidgetItem()
        item.setData(Qt.UserRole, action)
        widget = ActionItemWidget(
            action, on_change=self._on_action_param_changed)
        # summary 由 ActionItemWidget 内部根据 action 派生；保留属性以便
        # 其他代码（move/refresh 路径）继续可读写。
        item.setSizeHint(widget.sizeHint())
        if row is None or row < 0 or row >= self.action_list.count():
            self.action_list.addItem(item)
        else:
            self.action_list.insertItem(row, item)
        self.action_list.setItemWidget(item, widget)
        widget.item = item
        # ⚠️ QPushButton.clicked 会带一个 checked 布尔实参，若写成
        # ``lambda it=item: ...``，该实参会顶掉默认参数 it（变成 bool）→
        # ``action_list.row(False)`` 报 TypeError。故必须用首参吃掉它。
        widget.enable_check.toggled.connect(
            lambda checked, it=item: self._on_action_toggled(it, checked))
        widget.up_button.clicked.connect(
            lambda _checked=False, it=item: self._on_move_action_for(it, -1))
        widget.down_button.clicked.connect(
            lambda _checked=False, it=item: self._on_move_action_for(it, 1))
        widget.remove_button.clicked.connect(
            lambda _checked=False, it=item: self._on_remove_action_for(it))
        self._update_tab_titles()
        if render_preview:
            self._render_action_preview()
        return item

    def _add_action_item(self, action, render_preview=True):
        """根据动作字典在列表末尾追加一个动作项。

        供「手动添加动作」(``_on_add_action``) 与「启动恢复已保存动作列表」
        (``_load_actions_setting``) 共用。
        """
        return self._insert_action_item(None, action, render_preview)

    def _on_action_toggled(self, item, checked):
        """启用勾选框变化：写回动作数据、刷新标签计数与预览。

        fit=False：同图刷新（启用/停用不换图），保留用户当前缩放位置。
        """
        row = self.action_list.row(item)
        if row < 0:
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        data["enabled"] = bool(checked)
        item.setData(Qt.UserRole, data)
        self._update_tab_titles()
        self._render_action_preview(fit=False)

    def _on_action_param_changed(self, item, key, value):
        """inline 参数控件变化：写回动作数据、防抖触发预览、刷新摘要。

        防抖 250ms：连续拖动 spinbox 时不每个值都重渲染，而是在用户停顿
        后再调一次 ``_render_action_preview``。
        """
        row = self.action_list.row(item)
        if row < 0:
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        data.setdefault("params", {})[key] = value
        item.setData(Qt.UserRole, data)
        # 同步摘要文本（保留类型指示；只在 label 上更新，不动 inline 控件，
        # 避免 valueChanged 回调形成死循环）。
        widget = self.action_list.itemWidget(item)
        if widget is not None:
            try:
                widget.summary_label.setText(self._action_summary(data))
            except Exception:
                pass
        if self._param_change_timer is None:
            self._param_change_timer = QTimer(self)
            self._param_change_timer.setSingleShot(True)
            # fit=False：参数变化是同图刷新，保留用户当前缩放位置。
            # 否则拖动滑块时图片一直弹回适应窗口，干扰看细节。
            self._param_change_timer.timeout.connect(
                lambda: self._render_action_preview(fit=False))
        self._param_change_timer.start(250)

    def _on_add_action(self):
        """按当前下拉选中的类型，以默认参数直接追加一个动作项（不再弹参数对话框）。

        参数改为 inline 暴露在列表项上：用户可即时调整任意参数，无需切换
        弹窗（旧 ActionParamDialog 已不再被 UI 调用，仅留作历史代码）。
        """
        # 取 userData（原始中文 ID）而不是显示文本：英文界面下显示的是 "Resize"，
        # 但 action dict 必须存 "调整大小"，否则 apply_actions 判断失效。
        name = self.action_combo.currentData() or self.action_combo.currentText()
        defaults = dict(processor.DEFAULT_PARAMS.get(name, {}))
        action = {"type": name, "params": defaults, "enabled": True}
        self._add_action_item(action)
        self.statusBar().showMessage(i18n.t("已添加动作：%s") % i18n.t(name))

    def _action_summary(self, action):
        """Short human-readable summary of an action (shown in the list).

        ⚠️ ``atype`` 同时是内部 ID（下面所有分支判断、以及 processor 的
        apply_actions 都按字面量比较它），不能直接拿来显示。显示一律用
        ``name = i18n.t(atype)`` —— 英文界面下显示 "Resize"，而 action dict
        与配置文件里仍然是 "调整大小"。
        """
        atype = action.get("type", "")
        name = i18n.t(atype)
        p = action.get("params", {}) or {}
        if atype == "调整大小":
            w, h = int(p.get("width", 0) or 0), int(p.get("height", 0) or 0)
            algo = p.get("algorithm") or "LANCZOS"
            size = ("%dx%d" % (w, h)) if (w and h) else (
                (i18n.t("宽%d") % w) if w else ((i18n.t("高%d") % h) if h else i18n.t("自动")))
            return "%s (%s, %s)" % (name, size, algo)
        if atype == "旋转":
            return "%s (%d°)" % (name, int(p.get("angle", 0) or 0))
        if atype == "水印":
            return "%s (%s)" % (name, p.get("text", ""))
        if atype == "亮度/对比度":
            return i18n.t("%s (亮%.1f/对%.1f)") % (
                name, float(p.get("brightness", 1.0)), float(p.get("contrast", 1.0)))
        if atype == "锐化":
            return "%s (%.1f)" % (name, float(p.get("factor", 1.0)))
        if atype == "裁剪":
            return "%s (%d,%d %dx%d)" % (
                name, int(p.get("left", 0)), int(p.get("top", 0)),
                int(p.get("width", 0)), int(p.get("height", 0)))
        if atype == "规格化":
            co = int(p.get("cutoff", 0) or 0)
            return ("%s" % name) if co == 0 else ("%s (cutoff=%d‰)" % (name, co))
        if atype == "曝光":
            return "%s (%+.1f EV)" % (name, float(p.get("ev", 0.0) or 0.0))
        if atype == "阴影/高光":
            return i18n.t("%s (影%.2f/亮%.2f)") % (
                name, float(p.get("shadow", 1.0) or 1.0),
                float(p.get("highlight", 1.0) or 1.0))
        return name

    def _collect_actions(self):
        """Return the ordered list of **enabled** action dicts (处理用)。

        未勾选（停用）的动作不参与处理，但仍保留在列表与持久化数据中。
        顺序 = 列表从上到下的顺序，``processor.apply_actions`` 依此链式执行。
        """
        return [a for a in self._all_action_data() if a.get("enabled", True)]

    def _all_action_data(self):
        """Return every action dict in list order, 含已停用的（持久化用）。"""
        actions = []
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            data = item.data(Qt.UserRole)
            if isinstance(data, dict) and "type" in data:
                actions.append(data)
        return actions

    def _action_counts(self):
        """(启用数, 总数) —— 供标签标题的「动作 [n/m]」使用。"""
        all_actions = self._all_action_data()
        enabled = sum(1 for a in all_actions if a.get("enabled", True))
        return enabled, len(all_actions)

    def _on_remove_action_for(self, item):
        """Remove a specific action item (used by each item's 移除 button).

        fit=False：删除动作是同图刷新（动作链变化但图源不变），保留缩放。
        """
        row = self.action_list.row(item)
        if row < 0:
            return
        self._discard_action_item(row)
        self._update_tab_titles()
        self._render_action_preview(fit=False)

    def _discard_action_item(self, row):
        """把第 row 项从列表摘除，并显式销毁其 itemWidget。

        摘除时必须让 widget 一并退场：``takeItem`` 只把 item 交还给调用方，
        widget 仍挂在 viewport 下，若放任不管会残留在界面上或成为野控件。
        """
        item = self.action_list.takeItem(row)
        if item is None:
            return None
        widget = self.action_list.itemWidget(item)
        if widget is not None:
            # 先断开关联再延迟销毁：deleteLater 保证即使此刻正处在该 widget
            # 内部按钮的 clicked 回调中，也能安全返回后再销毁，不会 use-after-free。
            self.action_list.removeItemWidget(item)
            widget.setParent(None)
            widget.deleteLater()
        return item

    def _on_move_action_for(self, item, delta):
        """Move a specific action item up/down (used by each item's buttons).

        ⚠️ 闪退根因（务必保持现在的写法）：旧实现是
            ``widget = itemWidget(item)`` → ``takeItem(row)`` →
            ``insertItem(new_row, item)`` → ``setItemWidget(item, widget)``
        即**把摘下来的 widget 原样塞回去**。``takeItem`` 会把该项的
        indexWidget 从 ``QAbstractItemView`` 的 persistent widget 表中摘除，
        Qt 会在随后的刷新中释放它；等到再把同一个 widget ``setItemWidget``
        回去，这个控件已进入销毁流程，而视图在绘制 / 事件分发时仍会访问它
        → 真实桌面上点击上移/下移直接闪退（offscreen 不绘制，所以测不出来）。

        修复：移动 = 「摘除旧项并让其 widget 一并退场」+「在目标行插入全新
        item 与全新 widget」。旧 widget 一经摘除就永不复用。
        """
        row = self.action_list.row(item)
        if row < 0:
            return
        new_row = row + delta
        if not (0 <= new_row < self.action_list.count()):
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        self._discard_action_item(row)
        new_item = self._insert_action_item(new_row, data, render_preview=False)
        # 选中跟随数据走，用户连点同一按钮可以持续移动。
        self.action_list.setCurrentItem(new_item)
        # fit=False：移动是同图刷新（动作链顺序变但内容变），保留缩放。
        self._render_action_preview(fit=False)

    def _on_clear_actions(self):
        self.action_list.clear()
        self._update_tab_titles()
        self._render_action_preview()

    def _on_remove_selected_actions(self):
        """Delete key handler: remove all selected action items."""
        rows = sorted(
            {index.row() for index in self.action_list.selectedIndexes()},
            reverse=True,
        )
        if not rows:
            return
        for r in rows:
            self._discard_action_item(r)
        self._update_tab_titles()
        self._render_action_preview()

    # ------------------------------------------------------------------
    # Actions-tab live preview
    # ------------------------------------------------------------------
    # 动作页预览解码上限改为动态计算（见 _render_action_preview），不再用常量。


    class _ActionPreviewWorker(QRunnable):
        """在子线程解码动作页预览源（JXL 走 djxl、AVIF 走 Pillow）+ 应用动作，
        产出 original / processed 两张 QImage 回传主线程。QPixmap 不能跨线程，
        故 worker 只构建 QImage，主线程再做 QPixmap.fromImage 回填。"""

        def __init__(self, host, epoch, path, max_side, actions):
            super().__init__()
            self.host = host
            self.epoch = epoch
            self.path = path
            self.max_side = max_side
            self.actions = actions  # list[dict]，JSON 安全、可跨线程传递

        def run(self):
            try:
                from PIL import Image
                from libjxl_gui import processor
                loadable = _display_path(self.path)
                if loadable is None:
                    raise RuntimeError(
                        i18n.t("无法解码该图片（%s）") % os.path.basename(self.path))
                img = Image.open(loadable)
                img.load()
                w, h = img.size
                if max(w, h) > self.max_side:
                    scale = self.max_side / float(max(w, h))
                    img = img.resize(
                        (max(1, int(round(w * scale))),
                         max(1, int(round(h * scale)))),
                        Image.LANCZOS,
                    )
                original_qi = MainWindow._pil_to_qimage(img)
                if self.actions:
                    processed = processor.apply_actions(
                        img.copy(), self.actions)
                    processed_qi = MainWindow._pil_to_qimage(processed)
                else:
                    processed_qi = original_qi
                self.host._action_preview_queue.put(
                    (self.epoch, original_qi, processed_qi, None))
            except Exception as exc:  # noqa: BLE001 - 把错误回传主线程显示
                self.host._action_preview_queue.put(
                    (self.epoch, None, None, str(exc)))

    def _on_tab_changed(self, index):
        if self.tabs.widget(index) == self.actions_tab:
            self._render_action_preview()

    def _refresh_preview_sources(self):
        combo = self.preview_source_combo
        if combo is None:
            return
        previous = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        for i, path in enumerate(self.input_files):
            combo.addItem("%d. %s" % (i + 1, os.path.basename(path)))
        idx = combo.findText(previous)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _current_preview_source(self):
        idx = self.preview_source_combo.currentIndex()
        if 0 <= idx < len(self.input_files):
            return self.input_files[idx]
        return None

    @staticmethod
    def _pil_to_qimage(img):
        img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        return QImage(data, img.width, img.height, QImage.Format_RGBA8888)

    def _render_action_preview(self, fit=True):
        """刷新动作预览（异步）。

        ``fit`` 控制回填时是否重置缩放到适应窗口：
        - True：图源切换/首次加载——需要 fit 让用户先看到完整图
        - False：参数/勾选/移动/删除触发的同图刷新——保留用户当前缩放位置，
          否则拖动参数滑块时图片一直被弹回适应窗口，干扰看细节
        通过 ``self._next_preview_fit`` 传给回填路径 ``_drain_action_preview``。
        """
        self._next_preview_fit = bool(fit)
        self._refresh_preview_sources()
        path = self._current_preview_source()
        self._cancel_preview_loading_hint()
        if path is None:
            self.preview_view.setVisible(False)
            # 必须重置文本：否则若此前进入过「加载中」状态（文本已被改写成
            # "预览加载中…"），此处只隐藏 preview_view、显示 preview_msg 却
            # 保留了旧文本，清空输入后再回动作页就会一直显示「加载中」。
            self.preview_msg.setText(
                i18n.t("请先在「输入」标签添加图片，\n再在此处预览动作效果。")
            )
            self.preview_msg.setVisible(True)
            self.preview_msg_container.setVisible(True)
            self._preview_original_pixmap = None
            self._preview_processed_pixmap = None
            self._action_preview_epoch += 1  # 作废任何在途 worker
            self._stop_action_preview_drain()
            return
        # 解码（djxl/Pillow）+ apply_actions 全部移到子线程；主线程只负责
        # 投递与回填，切标签 / 选大图源都不会再冻结 GUI。epoch 令牌保证只
        # 有最新一次请求的回传结果被采用。
        self._action_preview_epoch += 1
        epoch = self._action_preview_epoch
        actions = self._collect_actions()
        if self._thumb_pool is None:
            self._thumb_pool = QThreadPool(self)
        # 解码上限 = OOM 保护上限：一般图片（单边 ≤ _PREVIEW_OOM_CAP）按原生
        # 分辨率解码，fit 始终「从大到小缩」清晰；仅超巨图才等比缩到上限以内。
        max_side = _PREVIEW_OOM_CAP
        worker = self._ActionPreviewWorker(
            self, epoch, path, max_side, actions)
        self._action_preview_busy = True
        self._ensure_action_preview_drain()
        # 切图 / 改动作瞬间：若当前已有预览图，【保持旧图不动】——不隐藏视图、
        # 不切「加载中」文字。旧逻辑无条件走「隐藏视图 + 显示加载中」，形成
        # 「图消失 → 文字 → 图回来」的三步跳变；该跳变与解码耗时无关，小图
        # 解码只要几毫秒却仍完整跳变一次，所以「不管大图小图都闪」。
        # 只有解码超过 _PREVIEW_LOADING_HINT_DELAY 仍未完成（真·大图）才切到
        # 加载态，此时跳变一次是合理反馈。
        if (self.preview_view.isVisible()
                and self._preview_processed_pixmap is not None):
            self._schedule_preview_loading_hint()
        else:
            # 当前无图可留（首次进入 / 上次失败 / 清空后重新添加）：必须给出
            # 即时反馈，否则预览区会长时间空白。
            self._preview_fit_deferred = True
            self.preview_view.setVisible(False)
            self.preview_msg.setText(i18n.t("预览加载中…"))
            self.preview_msg.setVisible(True)
            self.preview_msg_container.setVisible(True)
        self._thumb_pool.start(worker)

    def _schedule_preview_loading_hint(self):
        """延迟显示「预览加载中…」，快速完成的解码根本不会显示它。"""
        if self._preview_loading_timer is None:
            self._preview_loading_timer = QTimer(self)
            self._preview_loading_timer.setSingleShot(True)
            self._preview_loading_timer.timeout.connect(
                self._show_preview_loading)
        self._preview_loading_timer.start(_PREVIEW_LOADING_HINT_DELAY)

    def _cancel_preview_loading_hint(self):
        """解码已回填 / 请求作废时取消挂起的加载提示。"""
        if self._preview_loading_timer is not None:
            self._preview_loading_timer.stop()

    def _show_preview_loading(self):
        if not self._action_preview_busy:
            return  # 已在阈值内回填，无需提示（也就不会闪）
        self._preview_fit_deferred = True
        self.preview_view.setVisible(False)
        self.preview_msg.setText(i18n.t("预览加载中…"))
        self.preview_msg.setVisible(True)
        self.preview_msg_container.setVisible(True)

    def _ensure_action_preview_drain(self):
        if self._action_preview_drain is None:
            self._action_preview_drain = QTimer(self)
            self._action_preview_drain.timeout.connect(
                self._drain_action_preview)
            self._action_preview_drain.start(16)

    def _stop_action_preview_drain(self):
        if self._action_preview_drain is not None:
            self._action_preview_drain.stop()
            self._action_preview_drain = None

    def _drain_action_preview(self):
        handled = False
        while True:
            try:
                epoch, original_qi, processed_qi, err = \
                    self._action_preview_queue.get_nowait()
            except queue.Empty:
                break
            handled = True
            # 过期结果（源 / 动作已变）：直接丢弃，绝不回填到新选择。
            if epoch != self._action_preview_epoch:
                continue
            self._action_preview_busy = False
            self._cancel_preview_loading_hint()
            if err is not None:
                # 只显文件名而非完整路径，避免长路径撑大窗口（同 test 断言）。
                path = self._current_preview_source()
                err_text = err
                if path:
                    full = os.path.abspath(path) + os.sep
                    err_text = err_text.replace(full, "")
                    err_text = err_text.replace(path, os.path.basename(path))
                self.preview_view.setVisible(False)
                self.preview_msg.setText(i18n.t("预览失败：%s") % err_text)
                self.preview_msg.setVisible(True)
                self.preview_msg_container.setVisible(True)
                self._preview_original_pixmap = None
                self._preview_processed_pixmap = None
                continue
            # 子线程产出的 QImage 在此转 QPixmap（仅一次拷贝，极快）。
            self._preview_original_pixmap = QPixmap.fromImage(original_qi)
            self._preview_processed_pixmap = QPixmap.fromImage(processed_qi)
            # 视图此前被隐藏（首次进入 / 已切到加载态 / 上次失败）时才需要切
            # 显隐：此时布局会重算，fit 必须推迟一拍拿最终 viewport。
            # 若视图本来就在显示旧图（本修复的「保持旧图」路径），则完全不碰
            # 显隐 —— 布局零变化，下一步可同步 fit，做到「旧图→新图」一次重绘。
            if not self.preview_view.isVisible():
                self._preview_fit_deferred = True
                # 先让视图可见、隐藏提示容器（两者均占 stretch=1），再回填并
                # fit：否则 fit 时 preview_msg_container 仍可见会抢走一半高度。
                self.preview_view.setVisible(True)
                self.preview_msg.setVisible(False)
                self.preview_msg_container.setVisible(False)
            # fit 标志来自 _render_action_preview(fit=...)：图源切换/首次加载
            # 才 fit；参数/勾选/移动触发的同图刷新保留用户缩放位置。
            self._apply_preview_pixmap(fit=self._next_preview_fit)
        # 队列空且无在途请求时停止节拍器，避免常驻空转。
        if not handled and not self._action_preview_busy:
            self._stop_action_preview_drain()

    def _apply_preview_pixmap(self, fit=True):
        """显示当前模式（处理后 / 原图）的 pixmap。

        fit=True 时切换图片后自动 fit 到屏幕（消除沿用上一张缩放比例的问题）；
        peek（按住显示原图 / 松开恢复）传 fit=False，避免重置用户的手动缩放。

        fit 的时机分两种：
        - 布局刚变化（视图由隐藏转可见，见 _preview_fit_deferred）：resize 是
          异步的，必须推迟到下一事件循环，同步 fit 会拿到未落定的 viewport。
        - 布局未变（视图一直在显示旧图）：viewport 尺寸稳定，【同步 fit】。
          这与 set_pixmap 处于同一次事件回调内，Qt 在事件末尾只绘制一次，
          不会出现「新图 + 上一张的 transform」的中间帧 —— 这是切图闪烁的
          第二个来源（无条件 singleShot(0) 会先按旧缩放画一帧，再 fit 重画）。
        """
        pix = (
            self._preview_original_pixmap
            if self._preview_mode == "original"
            else self._preview_processed_pixmap
        )
        if pix is not None:
            self.preview_view.set_pixmap(pix)
            if fit and self.preview_view.isVisible():
                if self._preview_fit_deferred:
                    QTimer.singleShot(0, self.preview_view.fit)
                else:
                    self.preview_view.fit()
        self._preview_fit_deferred = False

    def _preview_show_original(self):
        """Press-and-hold peek: show the un-processed source image."""
        if self._preview_original_pixmap is None:
            return
        self._preview_mode = "original"
        self._apply_preview_pixmap(fit=False)

    def _preview_show_processed(self):
        """Release: return to the processed (action-applied) preview."""
        if self._preview_processed_pixmap is None:
            return
        self._preview_mode = "processed"
        self._apply_preview_pixmap(fit=False)

    def _preview_actual(self):
        self.preview_view.zoom_to_actual()

    # ------------------------------------------------------------------
    # Output tab slots
    # ------------------------------------------------------------------
    def _on_browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, i18n.t("选择输出文件夹"))
        if folder:
            folder = os.path.normpath(folder)
            self.custom_folder_edit.setText(folder)
            self._add_folder_history(folder)
            self._save_output_settings()

    def _on_custom_folder_changed(self):
        """Persist the typed/selected custom folder. Suppressed while we are
        restoring saved settings (so loading doesn't bounce back a save)."""
        if self._output_loading:
            return
        self._save_output_settings()

    def _add_folder_history(self, path):
        """Record a custom output folder into the history (most recent first,
        de-duplicated, capped) and refresh the history menu."""
        if not path:
            return
        history = list(getattr(self, "_folder_history", []))
        if path in history:
            history.remove(path)
        history.insert(0, path)
        max_history = 20
        if len(history) > max_history:
            history = history[:max_history]
        self._folder_history = history
        self._rebuild_folder_menu()

    def _on_folder_history_delete(self, row):
        """Remove a single entry from the folder-history (triggered by the
        per-row "✕" button). The history menu stays open so more entries can
        be removed in a row; preserves the currently typed text.

        The row is removed *in place* — only the deleted QWidgetAction is
        taken out and the remaining rows are re-numbered — instead of
        rebuilding the whole menu. Rebuilding (clear() + repopulate) on a
        visible popup collapses every widget and repaints the list, which
        reads as a flicker on every delete."""
        if not (0 <= row < len(self._folder_history)):
            return
        self._folder_history.pop(row)
        actions = self.folder_menu.actions()
        if 0 <= row < len(actions):
            act = actions[row]
            self.folder_menu.removeAction(act)
            act.deleteLater()  # release the QWidgetAction + its row widget
        # Re-number the surviving rows so their delete signals keep pointing
        # at the correct index (the widget emits its own _row, not the closure).
        for r, act in enumerate(self.folder_menu.actions()):
            w = act.defaultWidget()
            if isinstance(w, HistoryRowWidget):
                w.set_row(r)
        # A blank menu looks broken; show the placeholder when nothing remains.
        if not self._folder_history:
            empty = QAction(i18n.t("(无历史记录)"), self.folder_menu)
            empty.setEnabled(False)
            self.folder_menu.addAction(empty)
        self._save_output_settings()

    def _rebuild_folder_menu(self):
        """Rebuild the custom-folder history menu from self._folder_history.
        Each row is a HistoryRowWidget (clickable path + per-row delete)."""
        self.folder_menu.clear()
        if not getattr(self, "_folder_history", []):
            empty = QAction(i18n.t("(无历史记录)"), self.folder_menu)
            empty.setEnabled(False)
            self.folder_menu.addAction(empty)
            return
        for idx, path in enumerate(self._folder_history):
            row = HistoryRowWidget(path, idx, self.folder_menu)
            row.selected.connect(lambda p=path: self._select_history(p))
            row.deleteRequested.connect(
                lambda r=idx: self._on_folder_history_delete(r))
            act = QWidgetAction(self.folder_menu)
            act.setDefaultWidget(row)
            self.folder_menu.addAction(act)

    def _select_history(self, path):
        """Choose a history entry: fill the editable field and close the menu."""
        self.custom_folder_edit.setText(path)
        self.folder_menu.hide()

    def _open_folder_menu(self):
        """Open the folder-history menu manually (triggered by the arrow
        button's clicked signal). Positioning it here — instead of via
        setMenu()/aboutToShow — lets the popup anchor to the bottom-left of the
        whole combo unit and match its width, like a QComboBox dropdown."""
        # Keep the popup's colours in sync with the current system light/dark
        # theme. Fusion caches its palette and only re-reads it on a theme
        # change event, which can arrive *after* the first open following a
        # switch — leaving the list showing the previous theme's colours.
        # Re-applying the live application palette and rebuilding the rows on
        # every open guarantees the text always uses the current theme.
        self.folder_menu.setPalette(QApplication.palette())
        self._rebuild_folder_menu()
        combined_w = (
            self.custom_folder_edit.width() + self.custom_folder_dropdown.width()
        )
        pos = self.custom_folder_edit.mapToGlobal(
            QPoint(0, self.custom_folder_edit.height())
        )
        self.folder_menu.setMinimumWidth(combined_w)
        self.folder_menu.popup(pos)

    def _sync_inactive_palette(self):
        """Keep every selection-indicator control's Inactive palette group
        equal to its Active group, so a selected control does not dim / turn
        black when the window loses focus.

        Root cause: Qt repaints the controls of an unfocused window using the
        QPalette.Inactive colour group. Under the native Windows style the
        checked indicator of a radio/checkbox is drawn with the QPalette.Accent
        role, whose Inactive-group value resolves to black while the Active
        group holds the OS accent (blue). Syncing Inactive -> Active makes the
        control keep its focused colour when unfocused. Re-derived from the live
        application palette so it also tracks system theme switches.

        Only radio buttons and checkboxes are covered here: both are driven by
        the Accent role. Push buttons are intentionally left out so their
        genuine inactive dimming is preserved."""
        app_pal = QApplication.palette()
        synced = QPalette(app_pal)
        for role_int in range(
            QPalette.ColorRole.Window.value,
            QPalette.ColorRole.PlaceholderText.value + 1,
        ):
            role = QPalette.ColorRole(role_int)
            synced.setColor(
                QPalette.ColorGroup.Inactive, role,
                app_pal.color(QPalette.ColorGroup.Active, role),
            )
        for rb in self.findChildren(QRadioButton):
            rb.setPalette(synced)
        for cb in self.findChildren(QCheckBox):
            cb.setPalette(synced)
        # 进度条同样规避 Fusion 样式在窗口失焦时把填充色暗化/变黑：
        # 把 Inactive 组设为与 Active 组一致，失焦时颜色保持焦点态。
        if hasattr(self, "progress_bar"):
            self.progress_bar.setPalette(synced)

    def changeEvent(self, event):
        """React to a system light/dark theme switch while the app is running:
        refresh the custom-folder history popup's colours (live, if it is open,
        otherwise on the next open), and keep radio buttons from dimming when
        the window is unfocused."""
        if event.type() == QEvent.PaletteChange:
            # The menu may not exist yet when Qt delivers an early palette
            # change during construction.
            menu = getattr(self, "folder_menu", None)
            if menu is not None:
                menu.setPalette(QApplication.palette())
                if menu.isVisible():
                    self._rebuild_folder_menu()
            self._sync_inactive_palette()
            # System light/dark switch: re-sync the dropdown popups, whose
            # palette is a snapshot taken at style-apply time.
            self._refresh_combo_styles()
        super().changeEvent(event)

    # ---- output location / filename persistence (QSettings) ----------

    def _save_output_settings(self):
        """Persist the output-location choice, the custom folder, the filename
        naming choice, the suffix, and the folder-history list."""
        settings = QSettings()
        settings.beginGroup("output_settings")
        settings.setValue(
            "dest_mode",
            "custom" if self.custom_folder_radio.isChecked() else "same",
        )
        settings.setValue("custom_folder", self.custom_folder_edit.text())
        settings.setValue(
            "name_mode",
            "suffix" if self.add_suffix_radio.isChecked() else "keep",
        )
        settings.setValue("suffix", self.suffix_edit.text())
        settings.setValue("folder_history", getattr(self, "_folder_history", []))
        settings.endGroup()

    def _load_output_settings(self):
        """Restore persisted output-location / filename settings and populate the
        custom-folder history dropdown. Safe to call after the output tab is
        built."""
        self._output_loading = True
        settings = QSettings()
        settings.beginGroup("output_settings")
        dest_mode = settings.value("dest_mode", "same")
        custom_folder = settings.value("custom_folder", "")
        name_mode = settings.value("name_mode", "keep")
        suffix = settings.value("suffix", "_converted")
        history = settings.value("folder_history", [])
        settings.endGroup()

        if not isinstance(history, list):
            history = [history] if history else []
        self._folder_history = list(history)

        # The restored active custom folder is the "last-used" path; make sure
        # it is also visible in the history dropdown even if it was only typed
        # (never browsed/converted) in the previous session.
        if custom_folder and os.path.isdir(custom_folder) \
                and custom_folder not in self._folder_history:
            self._folder_history.insert(0, custom_folder)

        # Populate the history menu from the restored list, then set the active
        # custom folder text (block signals so this doesn't trigger a save).
        self.custom_folder_edit.blockSignals(True)
        self._rebuild_folder_menu()
        self.custom_folder_edit.setText(custom_folder)
        self.custom_folder_edit.blockSignals(False)

        if dest_mode == "custom":
            self.custom_folder_radio.setChecked(True)
        else:
            self.same_folder_radio.setChecked(True)

        if name_mode == "suffix":
            self.add_suffix_radio.setChecked(True)
        else:
            self.keep_name_radio.setChecked(True)
        self.suffix_edit.setText(suffix)

        self._output_loading = False

    # ---- conversion-process settings persistence (QSettings) ----------
    def _save_conversion_settings(self):
        """Persist the conversion-process settings (CPU priority, core count,
        advanced-thread toggle) to QSettings.

        Guarded by ``_conversion_loading`` so restoring on launch does not
        immediately re-save (and clobber) the stored value.
        """
        if getattr(self, "_conversion_loading", False):
            return
        settings = QSettings()
        settings.beginGroup("conversion")
        settings.setValue("cpu_priority", self.cpu_priority_combo.currentData())
        settings.setValue("cpu_cores", self.cpu_cores_combo.currentData())
        settings.setValue("adv_threads_enabled", self.adv_threads_toggle.isChecked())
        settings.setValue("adv_num_threads", self.adv_num_threads_toggle.isChecked())
        settings.setValue("adv_effort10", self.adv_effort10_toggle.isChecked())
        settings.setValue(
            "jpeg_hard_skip", getattr(self, "jpeg_hard_skip_check", None)
            and self.jpeg_hard_skip_check.isChecked()
        )
        settings.setValue(
            "decode_threads", getattr(self, "decode_threads_check", None)
            and self.decode_threads_check.isChecked()
        )
        settings.setValue(
            "adv_warning_suppressed",
            bool(getattr(self, "_adv_warning_suppressed", False)),
        )
        settings.endGroup()

    def _load_conversion_settings(self):
        """Restore the persisted conversion-process settings onto the UI.
        Safe to call only after the settings tab (and thus the combos) is built."""
        self._conversion_loading = True
        settings = QSettings()
        settings.beginGroup("conversion")
        key = settings.value("cpu_priority", converter.DEFAULT_PRIORITY)
        cores = settings.value("cpu_cores", "auto")
        adv_enabled = settings.value("adv_threads_enabled", False, type=bool)
        adv_num_threads = settings.value("adv_num_threads", False, type=bool)
        adv_effort10 = settings.value("adv_effort10", False, type=bool)
        jpeg_hard_skip = settings.value("jpeg_hard_skip", False, type=bool)
        decode_threads = settings.value("decode_threads", False, type=bool)
        adv_warning_suppressed = settings.value(
            "adv_warning_suppressed", False, type=bool
        )
        settings.endGroup()
        self._adv_warning_suppressed = bool(adv_warning_suppressed)
        if not isinstance(key, str) or self.cpu_priority_combo.findData(key) < 0:
            key = converter.DEFAULT_PRIORITY
        self.cpu_priority_combo.setCurrentIndex(
            self.cpu_priority_combo.findData(key)
        )
        if self.cpu_cores_combo.findData(cores) < 0:
            cores = "auto"
        self.cpu_cores_combo.setCurrentIndex(self.cpu_cores_combo.findData(cores))
        # 子项勾选态需在母开关联动前恢复，_apply_adv_threads_state 按其刷新输出页。
        self.adv_num_threads_toggle.setChecked(bool(adv_num_threads))
        self.adv_effort10_toggle.setChecked(bool(adv_effort10))
        if getattr(self, "jpeg_hard_skip_check", None) is not None:
            self.jpeg_hard_skip_check.setChecked(bool(jpeg_hard_skip))
        if getattr(self, "decode_threads_check", None) is not None:
            self.decode_threads_check.setChecked(bool(decode_threads))
        self.adv_threads_toggle.setChecked(bool(adv_enabled))
        # 高级参数子项按钮可用性由母开关控制；此时输出页已构建，可安全联动。
        self._apply_adv_threads_state(bool(adv_enabled))
        self._conversion_loading = False

    # ---- application theme (persisted) --------------------------------
    def _init_theme(self):
        """Read the persisted theme and apply it BEFORE the UI is built, so
        every NoFlickerComboBox constructed during the build uses the correct
        dropdown style from the start. Default is 原生（无闪烁）."""
        settings = QSettings()
        settings.beginGroup("appearance")
        theme = settings.value("theme", "native_noflicker")
        settings.endGroup()
        if theme not in _THEME_ORDER:
            theme = "native_noflicker"
        set_app_theme(theme)
        self._apply_app_style(theme)
        # Apply the persisted color scheme (light/dark/follow-system) on top
        # of the chosen control style. Must run after the style is in place so
        # the active QStyle receives the ColorScheme change immediately.
        self._init_color_scheme()

    # ---- application color scheme (persisted) ------------------------
    def _init_color_scheme(self):
        """Read the persisted color scheme and apply it BEFORE the UI is built.

        Default is "follow_system", which keeps the existing behaviour: the
        palette tracks the OS / Qt style hints automatically.
        """
        settings = QSettings()
        settings.beginGroup("appearance")
        scheme = settings.value("color_scheme", "follow_system")
        settings.endGroup()
        if scheme not in _COLOR_SCHEME_ORDER:
            scheme = "follow_system"
        # Push the scheme into QStyleHints so the active QStyle paints with
        # light/dark colours from the very first frame; nothing else to do at
        # this stage — the UI is not built yet, so no widget refresh is needed.
        set_app_color_scheme(scheme)

    # ---- actions-tab persistence (save action list on exit) ------------
    def _save_actions_setting(self):
        """Persist the action-list save-on-exit toggle, and (when enabled) the
        current action list itself, to QSettings.

        Guarded by ``_actions_loading`` so restoring on launch does not immediately
        re-serialize (and is not needed to, since load writes directly). When the
        toggle is OFF we remove any previously stored list, so a later launch never
        resurrects stale actions — matching the "关闭 = 不保存" semantics.
        Called both on toggle and from closeEvent.
        """
        if getattr(self, "_actions_loading", False):
            return
        # 必须用「全量」列表：_collect_actions() 只返回已启用的动作，若用它
        # 持久化，被取消勾选（停用）的动作会在下次启动时凭空消失。
        actions = self._all_action_data()
        settings = QSettings()
        settings.beginGroup("actions")
        save_on_exit = bool(
            getattr(self, "save_actions_on_exit_check", None)
            and self.save_actions_on_exit_check.isChecked()
        )
        settings.setValue("save_on_exit", save_on_exit)
        if save_on_exit:
            try:
                settings.setValue(
                    "list", json.dumps(actions, ensure_ascii=False)
                )
            except (TypeError, ValueError):
                settings.remove("list")
        else:
            settings.remove("list")
        settings.endGroup()

    def _load_actions_setting(self):
        """Restore the action-list save-on-exit toggle and, when it was enabled,
        rebuild the action list from QSettings. Safe to call only after the
        actions tab (and thus ``self.action_list``) has been built.
        """
        self._actions_loading = True
        settings = QSettings()
        settings.beginGroup("actions")
        save_on_exit = settings.value("save_on_exit", False, type=bool)
        if getattr(self, "save_actions_on_exit_check", None) is not None:
            self.save_actions_on_exit_check.setChecked(bool(save_on_exit))
        if save_on_exit:
            raw = settings.value("list", "", type=str)
            actions = []
            if raw:
                try:
                    actions = json.loads(raw)
                except (TypeError, ValueError):
                    actions = []
            if isinstance(actions, list):
                for a in actions:
                    if isinstance(a, dict) and "type" in a:
                        self._add_action_item(a, render_preview=False)
                # 批量恢复后统一刷新一次预览，避免逐项重复渲染。
                self._render_action_preview()
        settings.endGroup()
        self._actions_loading = False

    def _apply_app_style(self, theme):
        """Set the application-wide style: Fusion for the 'fusion' theme, the
        captured native style otherwise. A fresh style is created each call so
        re-applying the same QStyle instance never collides on ownership."""
        name = "Fusion" if theme == "fusion" else self._native_style_name
        style = QStyleFactory.create(name)
        if style is not None:
            QApplication.setStyle(style)
        # Fusion paints the checked radio/checkbox indicator (and other accent
        # controls) using the QPalette.Accent role, which defaults to blue. For
        # the Fusion theme we override that accent to black, so the checked
        # indicator background is black instead of blue. Other themes keep their
        # native accent unchanged.
        if theme == "fusion":
            pal = QApplication.palette()
            pal.setColor(QPalette.Accent, QColor(0, 0, 0))
            QApplication.setPalette(pal)
        else:
            # Restore the palette captured at startup so native themes keep the
            # real system colors. standardPalette() can return a generic beige
            # palette that makes the Windows style look washed out / wrong.
            QApplication.setPalette(self._original_app_palette)

    def _apply_theme(self, theme):
        """Apply a theme in full: update global state, switch the app-wide
        style, and re-style every existing dropdown (comboboxes + the folder
        menu) so the change takes effect immediately, with no restart."""
        set_app_theme(theme)
        self._apply_app_style(theme)
        self._refresh_combo_styles()
        self._apply_theme_to_folder_menu()
        # Native vs Fusion styles have different arrow / frame margins, so the
        # explicitly-sized combos need their minimum width recalculated.
        for combo in (getattr(self, "theme_combo", None),
                      getattr(self, "cpu_priority_combo", None),
                      getattr(self, "cpu_cores_combo", None),
                      getattr(self, "effort_combo", None)):
            if combo is not None:
                self._set_combo_min_width(combo)
        self._sync_inactive_palette()
        # Native vs Fusion frames differ by 1px at the bottom; keep the root
        # layout's bottom margin in sync with the active theme so the gap above
        # the bottom button bar stays visually correct after a switch.
        central = self.centralWidget()
        if central is not None and central.layout() is not None:
            central.layout().setContentsMargins(
                ROOT_MARGIN_LTR, ROOT_MARGIN_LTR, ROOT_MARGIN_LTR,
                _root_bottom_margin())
        self.statusBar().showMessage(
            i18n.t("主题已切换为：%s") % i18n.t(_THEME_LABELS.get(theme, theme)))

    def _refresh_combo_styles(self):
        """Re-apply the active style and palette to every dropdown.

        ``NoFlickerComboBox`` pins its popup view to a *snapshot* of the
        application palette (that is what keeps the native selection indicator
        blue instead of black), so that snapshot goes stale on every palette
        change and has to be refreshed explicitly. Called on theme switches,
        colour-scheme switches and system light/dark switches.
        """
        for cb in self.findChildren(NoFlickerComboBox):
            cb._apply_fusion_style()

    def _set_combo_min_width(self, combo):
        """Set the combo's minimum width to fit its widest item under the
        current style. This is needed because the native Windows style's arrow
        button and frame padding are wider than Fusion's, so a width measured
        for Fusion will truncate text when the user switches to native."""
        combo.updateGeometry()
        combo.setMinimumWidth(combo.sizeHint().width() + 8)

    def _apply_theme_to_folder_menu(self):
        """The custom-folder history popup always renders with the Fusion
        style, so its look is identical under both the "原生" and
        "原生（无闪烁）" themes (the latter is the canonical look the user
        wants). Other dropdowns keep following the active theme; this popup is
        intentionally forced to Fusion for cross-theme consistency."""
        if not hasattr(self, "folder_menu"):
            return
        fusion = _fusion_style()
        if fusion is not None:
            self.folder_menu.setStyle(fusion)

    def _on_theme_changed(self, _index):
        """Persist and apply the newly chosen theme."""
        self._apply_theme(self.theme_combo.currentData())
        self._save_theme()

    def _save_theme(self):
        """Persist the chosen theme. Guarded so restoring on launch / building
        the settings tab does not clobber the stored value."""
        if getattr(self, "_theme_loading", False):
            return
        settings = QSettings()
        settings.beginGroup("appearance")
        settings.setValue("theme", self.theme_combo.currentData())
        settings.endGroup()
        settings.sync()

    def _on_language_combo_changed(self, _index):
        """持久化所选语言，并明确告知「重启后生效」。

        这里**不**调用 ``i18n.set_language()``：界面上成百上千处文本在窗口
        构建时就已取定，运行时改字典只会让新旧语言混在一起（新弹出的控件
        是英文、已存在的是中文）。语言由 ``__main__.run()`` 在下次启动、
        构造窗口之前统一应用。
        """
        code = self.language_combo.currentData()
        if code not in _LANGUAGE_ORDER:
            return
        settings = QSettings()
        settings.beginGroup("appearance")
        settings.setValue("language", code)
        settings.endGroup()
        settings.sync()
        label = _LANGUAGE_LABELS.get(code, code)
        # label 是语言自称名（简体中文 / English），按约定保持原样不翻译。
        tip = i18n.t("语言已切换为%s，重启程序后生效。") % label
        self.statusBar().showMessage(tip, 8000)

    def _apply_color_scheme(self, scheme):
        """Apply a color scheme in full: update the global key, push the new
        scheme to QStyleHints, and force every top-level widget to repaint so
        the change is visible immediately, with no restart.

        The setColorScheme() call fires QEvent.PaletteChange on each top-level
        widget, which is already routed through ``changeEvent`` to refresh
        the custom-folder popup palette and re-sync the Inactive palette
        group (so selected radio/checkbox indicators stay blue when the
        window loses focus under dark mode).
        """
        if scheme not in _COLOR_SCHEME_ORDER:
            return
        set_app_color_scheme(scheme)
        # Force every top-level widget to repaint; some styles cache the
        # palette and only re-read it on a PaletteChange event, but a manual
        # update() guarantees the new colours show even on offscreen tests
        # and on Windows native style where the PaletteChange may be delayed.
        for w in QApplication.topLevelWidgets():
            w.update()
        # Dropdown popups hold a palette snapshot, so they must be re-synced
        # explicitly — otherwise an already-created popup keeps the colours of
        # the previous scheme until the style is switched away and back.
        self._refresh_combo_styles()
        self._sync_inactive_palette()
        self.statusBar().showMessage(
            i18n.t("颜色方案已切换为：%s") % i18n.t(_COLOR_SCHEME_LABELS.get(scheme, scheme)))

    def _on_color_scheme_changed(self, _index):
        """Persist and apply the newly chosen color scheme."""
        self._apply_color_scheme(self.color_scheme_combo.currentData())
        self._save_color_scheme()

    def _save_color_scheme(self):
        """Persist the chosen color scheme. Guarded so restoring on launch /
        building the settings tab does not clobber the stored value."""
        if getattr(self, "_color_scheme_loading", False):
            return
        settings = QSettings()
        settings.beginGroup("appearance")
        settings.setValue("color_scheme", self.color_scheme_combo.currentData())
        settings.endGroup()
        settings.sync()

    # ---- Input-tab view-mode persistence (QSettings) --------------------
    def _save_view_mode(self):
        """Persist the current Input-tab "查看" view mode to QSettings.

        Guarded by ``_view_loading`` so the default applied during build and
        the restore on launch do not clobber the stored value before it is
        read.
        """
        if getattr(self, "_view_loading", False):
            return
        settings = QSettings()
        settings.beginGroup("input_view")
        settings.setValue("mode", getattr(self, "_last_view", "缩略图"))
        settings.endGroup()
        # Flush immediately so the choice survives even an abnormal exit
        # (the other settings rely on the closeEvent flush; view mode is
        # changed interactively and should be durable the moment it changes).
        settings.sync()

    def _load_view_mode(self):
        """Restore the persisted Input-tab "查看" view mode.

        Safe only after the input tab (and thus view_button / _on_view_changed)
        is built. Mirrors the conversion-settings restore: the save guard is
        held while applying so the restore itself never re-writes the value.
        """
        self._view_loading = True
        settings = QSettings()
        settings.beginGroup("input_view")
        mode = settings.value("mode", "缩略图")
        settings.endGroup()
        if mode not in VIEW_MODES:
            mode = "缩略图"
        self._on_view_changed(mode)
        self._view_loading = False

    def _current_encode_mode(self):
        """Return the active JXL encode mode as one of the keys used by the
        converter: 'lossy', 'lossless', or 'lossless_jpeg'."""
        if self.lossless_radio.isChecked():
            return "lossless"
        if self.lossless_jpeg_radio.isChecked():
            return "lossless_jpeg"
        return "lossy"

    def _on_encode_mode_changed(self):
        # 勾选『自定义命令』时编码参数已被整体置灰，不随编码模式变化重新启用。
        if self.custom_cmd_check.isChecked():
            self._set_encoding_controls_disabled()
            return
        # Only the 有损 mode exposes the --quality control. In the other two
        # modes we disable it (greyed) but KEEP its displayed value, so that
        # returning to 有损 reuses the last setting. Effort stays enabled for
        # all modes.
        is_lossy = self.lossy_radio.isChecked()
        self.quality_slider.setEnabled(is_lossy)
        self.quality_spin.setEnabled(is_lossy)
        # 高级参数：按当前编码模式启用/禁用各旋钮（不可用的自动置灰，且不参与转换）。
        # num_threads 行还需母开关「启用高级参数」+ 子项「手动设置每文件线程数」同时
        # 勾选才可用（见 _num_threads_enabled），不能仅按编码模式判定，否则母开关
        # 关闭或子项未开时仍可勾选线程数。
        mode = self._current_encode_mode()
        for s in _ADVANCED_SCHEMA:
            check, val_w, _ = self._adv_widgets[s["key"]]
            enabled = mode in s["modes"]
            if s["key"] == "num_threads":
                enabled = self._num_threads_enabled()
            check.setEnabled(enabled)
            if val_w is not None:
                val_w.setEnabled(enabled and check.isChecked())
        self._update_adv_summary()
        self._update_cmd_preview()
        self._save_jxl_output()

    # ------------------------------------------------------------------
    # Convert
    # ------------------------------------------------------------------
    def _on_convert(self):
        if not self.input_files:
            self.statusBar().showMessage(i18n.t("错误：请先在「输入」中添加文件"))
            self.log_edit.appendPlainText(i18n.t("错误：输入列表为空。"))
            return

        tools = converter.check_tools()
        if not tools["cjxl"] or not tools["djxl"]:
            missing = []
            if not tools["cjxl"]:
                missing.append("cjxl")
            if not tools["djxl"]:
                missing.append("djxl")
            msg = (
                i18n.t("未检测到 libjxl 命令行工具（%s），无法执行转换。\n\n"
                "请确认 libjxl 已正确安装，并将其所在目录加入系统的 PATH 环境变量，"
                "然后重新启动本程序。") % "、".join(missing)
            )
            self.statusBar().showMessage(i18n.t("错误：cjxl / djxl 未就绪"))
            self.log_edit.appendPlainText(
                i18n.t("错误：%s 未就绪，请确认 libjxl 已安装并加入 PATH。")
                % i18n.t(" 与 ").join(missing)
            )
            self._show_warning_centered(i18n.t("libjxl 未就绪"), msg)
            return

        actions = self._collect_actions()
        if actions and not processor.AVAILABLE:
            self.statusBar().showMessage(i18n.t("错误：Pillow 未安装，无法执行动作"))
            self.log_edit.appendPlainText(
                i18n.t("错误：未检测到 Pillow 库，无法执行图像处理动作。"
                "请用命令 `pip install Pillow` 安装后重试。")
            )
            return

        # Remember a valid custom output folder (typed or picked): record it into
        # the history dropdown and persist, so it's recalled next launch.
        if self.custom_folder_radio.isChecked():
            cf = self.custom_folder_edit.text().strip()
            if cf:
                self._add_folder_history(cf)
                self._save_output_settings()

        # Derive cjxl encode parameters from the output-tab controls. The three
        # modes are mutually exclusive: only 有损 uses --quality; 无损 forces
        # distance 0; JPG 无损重编码 adds --lossless_jpeg=1. Effort is shared.
        mode = self._current_encode_mode()
        effort = int(self.effort_combo.currentText())
        quality = self.quality_spin.value()
        if mode == "lossy":
            distance, quality_arg, lossless_jpeg = None, quality, False
        elif mode == "lossless":
            distance, quality_arg, lossless_jpeg = 0, None, False
        else:  # lossless_jpeg
            distance, quality_arg, lossless_jpeg = None, None, True

        # 收集高级参数（数据驱动）。distance 若被显式勾选，则覆盖 quality
        # （两者互斥：用 -d 距离时不再传 --quality）。
        adv = self._collect_advanced(mode)
        dist_val = adv.pop("distance", None)
        if dist_val is not None:
            distance = dist_val
            quality_arg = None

        # 自定义命令：勾选时以用户编辑的命令替代自动拼装。命令必须包含
        # <输入> 与 <输出> 占位符（逐文件替换为真实路径），否则无法正确执行。
        custom_cmd = None
        if self.custom_cmd_check.isChecked():
            raw = self.cmd_edit.text().strip()
            if not raw:
                self.statusBar().showMessage(i18n.t("错误：自定义命令为空"))
                self.log_edit.appendPlainText(i18n.t("错误：自定义命令已勾选但内容为空。"))
                return
            if "<输入>" not in raw or "<输出>" not in raw:
                self._show_warning_centered(
                    i18n.t("自定义命令格式"),
                    i18n.t("自定义命令必须同时包含 <输入> 和 <输出> 占位符"
                    "（会被替换为每个文件的真实路径）。"),
                )
                return
            custom_cmd = raw

        # Build the job list on the UI thread (reads widget state safely),
        # then hand it to a worker thread so the GUI stays responsive.
        # In JPG 无损重编码 mode only JPG inputs are valid (cjxl's
        # --lossless_jpeg=1 re-encodes an actual JPEG bitstream); non-JPG
        # files are skipped and reported in the status log.
        jobs = []
        skipped = []
        skipped_jpg = []
        skipped_jpg_nonrecon = []
        nonrecon_jpg = []
        out_fmt = self._current_output_format()
        # jpeg_hard_skip 是「启用高级参数」的子项：母开关关闭时即便此前勾选过也不生效。
        jpeg_hard_skip = bool(
            getattr(self, "adv_threads_toggle", None)
            and self.adv_threads_toggle.isChecked()
            and getattr(self, "jpeg_hard_skip_check", None)
            and self.jpeg_hard_skip_check.isChecked()
        )
        for src in self.input_files:
            if mode == "lossless_jpeg" and not _is_jpeg(src):
                skipped.append(src)
                continue
            # JPEG 输出格式仅支持 JXL 输入：djxl 重建 JPG 只对「JPEG 无损转码的
            # JXL」成立，非 JXL 文件（普通图片/其他格式）无法通过 djxl 重建为 JPG。
            if out_fmt == "jpg" and not src.lower().endswith(".jxl"):
                skipped_jpg.append(src)
                continue
            # JPEG 输出 + 非自定义命令：校验该 JXL 是否能比特级重建为原 JPG。
            # 不可重建者按开关决定：A 模式直接跳过；B 模式留入 jobs（将以解码
            # 重编码方式输出），并在下方弹一次确认。
            if out_fmt == "jpg" and not custom_cmd and src.lower().endswith(".jxl"):
                action = _jpeg_recon_action(src, jpeg_hard_skip)
                if action == "skip":
                    skipped_jpg_nonrecon.append(src)
                    continue
                if action == "confirm":
                    nonrecon_jpg.append(src)
            out_path = self._build_output_path(src)
            out_is_jxl = out_path.lower().endswith(".jxl")
            jobs.append((src, out_path, out_is_jxl))

        # JPEG 输出 + 非自定义命令：若系统缺 jxlinfo，弹一次「建议安装」推荐框
        # （jxlinfo 头解析远快于回退的 djxl 全解码校验，缺失会拖慢批量校验）。
        if out_fmt == "jpg" and not custom_cmd:
            self._maybe_prompt_jxlinfo()

        # B 模式：存在不可重建 JXL 时弹一次确认；取消则中止整批转换。
        if nonrecon_jpg and not jpeg_hard_skip:
            n = len(nonrecon_jpg)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle(i18n.t("JPEG 输出质量提示"))
            box.setText(
                i18n.t("检测到 %d 个 JXL 无法通过 djxl 比特级还原为原始 JPG"
                "（如非 JPEG 源编码、或重建数据已剥离）。\n\n"
                "若继续，这些文件将以「解码为像素再重新编码为 JPG」的方式输出，"
                "属于有损的二次压缩，画质会进一步下降。\n\n"
                "是否继续？") % n
            )
            b_continue = box.addButton(i18n.t("继续"), QMessageBox.AcceptRole)
            b_cancel = box.addButton(i18n.t("取消"), QMessageBox.RejectRole)
            box.setDefaultButton(b_cancel)
            box.setStandardButtons(QMessageBox.NoButton)
            box.exec()
            if box.clickedButton() is not b_continue:
                self.statusBar().showMessage(i18n.t("已取消转换"))
                self.log_edit.appendPlainText(
                    i18n.t("已取消转换（用户拒绝将不可重建 JXL 以重编码方式输出为 JPG）。")
                )
                return

        # JPG 无损重编码模式：把被跳过的非 JPG 文件在状态中提示出来。
        if skipped:
            self.statusBar().showMessage(
                i18n.t("已跳过 %d 个非 JPG 文件（JPG 无损重编码仅处理 JPG）") % len(skipped)
            )
            self.log_edit.appendPlainText(
                i18n.t("提示：JPG 无损重编码模式仅支持 JPG 输入，以下 %d 个非 JPG 文件已跳过：")
                % len(skipped)
            )
            for s in skipped:
                self.log_edit.appendPlainText("    - %s" % s)
        # JPEG 输出格式：把被跳过的非 JXL 文件在状态中提示出来。
        if skipped_jpg:
            self.statusBar().showMessage(
                i18n.t("已跳过 %d 个非 JXL 文件（JPEG 输出仅重建 JXL）") % len(skipped_jpg)
            )
            self.log_edit.appendPlainText(
                i18n.t("提示：JPEG 输出格式仅支持 JXL 输入（无损 JPEG 转码的 JXL 可重建原图），"
                "以下 %d 个非 JXL 文件已跳过：") % len(skipped_jpg)
            )
            for s in skipped_jpg:
                self.log_edit.appendPlainText("    - %s" % s)
        # A 模式跳过的不可重建 JXL：在状态中提示出来。
        if skipped_jpg_nonrecon:
            self.statusBar().showMessage(
                i18n.t("已跳过 %d 个不可重建 JXL（JPEG 输出，已开启直接跳过）")
                % len(skipped_jpg_nonrecon)
            )
            self.log_edit.appendPlainText(
                i18n.t("提示：以下 %d 个 JXL 无法比特级重建为 JPG（已开启「直接跳过」），已跳过：")
                % len(skipped_jpg_nonrecon)
            )
            for s in skipped_jpg_nonrecon:
                self.log_edit.appendPlainText("    - %s" % s)
        # 输出文件已存在时的冲突策略（替换/询问/跳过/重命名）：在主线程预处理，
        # 不在 worker 线程弹窗。「替换」即 cjxl/djxl 默认覆盖，原样保留 jobs。
        jobs, skipped_exist, exist_cancelled = self._resolve_existing_outputs(jobs)
        if exist_cancelled:
            self.statusBar().showMessage(i18n.t("已取消转换"))
            self.log_edit.appendPlainText(
                i18n.t("已取消转换（用户在「文件已存在」冲突询问中选择了取消）。")
            )
            return
        if skipped_exist:
            self.log_edit.appendPlainText(
                i18n.t("提示：以下 %d 个输出文件已存在且策略为「跳过」，已跳过：")
                % len(skipped_exist)
            )
            for s in skipped_exist:
                self.log_edit.appendPlainText("    - %s" % s)
        if not jobs:
            self.statusBar().showMessage(i18n.t("没有可处理的文件，转换未开始"))
            self.log_edit.appendPlainText(
                i18n.t("错误：当前没有可处理的文件，转换未开始。")
            )
            return

        # Disconnect any stale worker and prepare a fresh one.
        if self._convert_worker is not None:
            try:
                self._convert_worker.deleteLater()
            except Exception:
                pass
            self._convert_worker = None

        self.convert_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._lock_ui_for_convert()
        self._stop_requested = False
        # Jump to the 状态 tab so the user can watch progress live.
        self.tabs.setCurrentWidget(self.status_tab)
        self._convert_worker = ConvertWorker(
            jobs, actions, effort, distance, quality_arg, lossless_jpeg,
            self.cpu_priority_combo.currentData(), advanced=adv,
            custom_cmd=custom_cmd,
            cpu_cores=self.cpu_cores_combo.currentData(),
            adv_threads_enabled=self.adv_num_threads_toggle.isChecked(),
            # 解码侧线程控制是「启用高级参数」的子项：母开关关闭时即便此前勾选过也不生效。
            decode_threads_enabled=bool(
                self.adv_threads_toggle.isChecked()
                and getattr(self, "decode_threads_check", None) is not None
                and self.decode_threads_check.isChecked()
            ),
            out_fmt=out_fmt,
            delete_original=self.delete_original_check.isChecked(),
            discard_if_larger=self.discard_if_larger_check.isChecked(),
            preserve_ctime=self.preserve_ctime_check.isChecked(),
            preserve_mtime=self.preserve_mtime_check.isChecked(),
        )
        self._convert_worker.log_signal.connect(self.log_edit.appendPlainText)
        self._convert_worker.status_signal.connect(self.statusBar().showMessage)
        self._convert_worker.finished_signal.connect(self._on_convert_finished)
        self._convert_worker.progress_signal.connect(self._on_progress_update)
        # 重置进度条与文案，并记录起点用于预计剩余时间
        self.progress_bar.setMaximum(max(1, len(jobs)))
        self.progress_bar.setValue(0)
        self.progress_label.setText(i18n.t("当前进度：0 / %d 文件") % len(jobs))
        self.eta_label.setText(i18n.t("预计剩余：--"))
        self._convert_start_time = time.time()
        self._convert_worker.start()

    def _maybe_prompt_jxlinfo(self):
        """JPEG 输出 + 非自定义命令时，若系统未安装 jxlinfo，弹窗推荐安装
        （jxlinfo 仅解析文件头，远快于回退的 djxl 全量解码校验；缺失会拖慢大批量
        转换前的重建可行性校验）。提供「不再提示」复选框，勾选后本次会话不再弹。"""
        if converter.find_jxlinfo() is not None:
            return
        if getattr(self, "_jxlinfo_remind_suppressed", False):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(i18n.t("建议安装 jxlinfo"))
        box.setText(
            i18n.t("检测到当前系统未安装 jxlinfo（libjxl 工具集的一部分）。\n\n"
            "在「JPEG 输出」模式下，本程序需要逐个判断 JXL 是否可比特级重建为原 JPG。"
            "有 jxlinfo 时仅解析文件头（毫秒级）；未安装则回退到 djxl 全量解码校验，"
            "大批量转换时会明显变慢。\n\n"
            "建议前往 libjxl 发布页安装 jxlinfo 并加入 PATH，可显著提升校验性能。"
            "（不影响功能，仅影响速度。）")
        )
        box.setStandardButtons(QMessageBox.Ok)
        cb = QCheckBox(i18n.t("不再提示"))
        box.setCheckBox(cb)
        box.exec()
        if cb.isChecked():
            self._jxlinfo_remind_suppressed = True

    def _on_convert_finished(self):
        worker = self._convert_worker
        stopped = self._stop_requested
        now = time.time()
        log = self.log_edit.appendPlainText

        # 汇总块
        log("")
        log(i18n.t("已输入文件： %d") % worker._stat_processed)
        log(i18n.t("已输出文件： %d") % worker._stat_ok)
        log(i18n.t("错误： %d") % worker._stat_err)
        log("")
        log(i18n.t("输入文件总大小： %s") % _format_bytes(worker._stat_in_bytes))
        log(i18n.t("输出文件总大小： %s") % _format_bytes(worker._stat_out_bytes))
        if worker._stat_in_bytes > 0:
            ratio = (worker._stat_out_bytes - worker._stat_in_bytes) \
                / worker._stat_in_bytes * 100.0
            log(i18n.t("文件大小比例： %s") % ("%+d%%" % round(ratio)))
        else:
            log(i18n.t("文件大小比例： --"))
        log("")
        duration = now - worker._stat_started
        if duration < 1:
            log(i18n.t("总持续时间： 不到 1 秒"))
        else:
            total = int(round(duration))
            if total < 60:
                log(i18n.t("总持续时间： %d 秒") % total)
            else:
                days = total // 86400
                h = (total % 86400) // 3600
                m = (total % 3600) // 60
                s = total % 60
                if days > 0:
                    log(i18n.t("总持续时间： %d 天 %d 时 %d 分 %d 秒")
                        % (days, h, m, s))
                elif h > 0:
                    log(i18n.t("总持续时间： %d 时 %d 分 %d 秒") % (h, m, s))
                else:
                    log(i18n.t("总持续时间： %d 分 %d 秒") % (m, s))
        log("")
        if stopped:
            log("转换停止：" + _format_datetime(now))
            self.statusBar().showMessage(i18n.t("转换已停止"))
            self._stop_requested = False
        else:
            log("转换完成：" + _format_datetime(now))
            self.statusBar().showMessage(
                i18n.t("转换完成：%d 个文件") % worker._stat_ok
            )
        log("")

        # 删除原文件：将本批「成功转换」的源文件移入回收站（非永久删除）。
        # 失败的文件不会进入 _ok_sources，故不会被删除。逐文件容错：单个失败
        # 不影响其余，并在日志中提示保留原因。
        # 仅在本批正常完成（未点「停止」）时执行：用户中途中止时保留全部原文件，
        # 避免「中止却丢失已转换原文件」的意外数据损失，可安全地重新运行。
        ok_sources = getattr(worker, "_ok_sources", None)
        if ok_sources and not stopped:
            n_del = 0
            for src in ok_sources:
                try:
                    _move_to_recycle_bin(src)
                    n_del += 1
                except Exception as exc:
                    log(i18n.t("删除原文件失败（已保留）：%s —— %s") % (src, exc))
            if n_del:
                log(i18n.t("已将 %d 个成功转换的原文件移入回收站。") % n_del)
            if n_del < len(ok_sources):
                log(i18n.t("注意：%d 个原文件因删除失败而保留。") % (len(ok_sources) - n_del))
            log("")

        # 「转换完毕之后」的后处理动作（4 个独立开关，由 _save_jxl_output 持久化）：
        # 仅在正常完成（未点「停止」）时执行——与「删除原文件」同一判定，避免
        # 中止时意外清空输入列表或自动退出。执行顺序刻意安排为
        # 打开资源管理器 -> 清除输入文件 -> 提示音 -> 退出，保证「退出」永远最后，
        # 且「打开资源管理器」需要的输出目录在「清除输入文件」清空前就已算出。
        if not stopped:
            self._run_post_convert_actions()

        # 进度条收尾：停在已处理数（正常完成=总数，中止=部分），清除预计剩余。
        self.progress_bar.setValue(worker._stat_processed)
        self.eta_label.setText(i18n.t("预计剩余：--"))

        self.convert_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._unlock_ui_after_convert()
        if worker is not None:
            worker.deleteLater()
            self._convert_worker = None

    def _run_post_convert_actions(self):
        """转换正常完成后执行的「转换完毕之后」后处理动作。

        4 个开关各自独立、由 _save_jxl_output 持久化。顺序固定为
        打开资源管理器 -> 清除输入文件 -> 提示音 -> 退出：
        - 打开资源管理器依赖输入文件路径推导输出目录，必须先于「清除输入文件」，
          否则输入列表被清空后将无法定位输出文件夹；
        - 退出放最后，确保前三个动作（尤其是 explorer / beep）都已触发。
        任意一步失败都不应阻断其余步骤，故各步独立 try 容错。
        """
        # 1) 打开资源管理器：定位输出目录（可能多个，取最近公共祖先），统一打开。
        if self.open_explorer_check.isChecked():
            try:
                self._open_output_in_explorer()
            except Exception as exc:  # 打开文件夹失败不应中断其余动作
                self.log_edit.appendPlainText(
                    i18n.t("打开资源管理器失败：%s") % exc
                )

        # 2) 清除「输入」文件（仅清空列表，不删除磁盘原始文件）。
        if self.clear_input_check.isChecked():
            try:
                self._on_clear_inputs()
            except Exception as exc:
                self.log_edit.appendPlainText(
                    i18n.t("清除输入列表失败：%s") % exc
                )

        # 3) 过程结束时发出提示音。
        if self.beep_check.isChecked():
            try:
                QApplication.beep()
            except Exception as exc:
                self.log_edit.appendPlainText(
                    i18n.t("播放提示音失败：%s") % exc
                )

        # 4) 转换完毕之后退出：必须最后执行。
        if self.exit_after_check.isChecked():
            try:
                QApplication.quit()
            except Exception as exc:
                self.log_edit.appendPlainText(
                    i18n.t("自动退出失败：%s") % exc
                )

    def _open_output_in_explorer(self):
        """根据当前输入文件推导输出目录，并在资源管理器中打开。

        输出目录由各源文件经 _build_output_path 推算后取 dirname 得到；
        多个（如开启「保留文件夹结构」散布到不同子目录）时取它们的
        最近公共祖先（commonpath），让窗口落在用户最可能关心的根目录。
        某个目录不存在（尚未实际写出）时跳过该目录，但只要有一个有效即打开。
        """
        dirs = set()
        for src in self.input_files:
            out = self._build_output_path(src)
            d = os.path.dirname(out)
            if d:
                dirs.add(d)
        dirs = [d for d in dirs if os.path.isdir(d)]
        if not dirs:
            return
        if len(dirs) == 1:
            target = dirs[0]
        else:
            try:
                target = os.path.commonpath(dirs)
            except ValueError:
                # 跨盘符等情况无法取公共祖先时，退回首个目录。
                target = sorted(dirs)[0]
        if target and os.path.isdir(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _on_progress_update(self, processed, total):
        """Update the progress bar, current count, and ETA from worker progress."""
        if total <= 0:
            return
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(processed)
        self.progress_label.setText(
            i18n.t("当前进度：%d / %d 文件") % (processed, total)
        )
        start = getattr(self, "_convert_start_time", None)
        elapsed = time.time() - start if start else 0.0
        if processed > 0 and elapsed > 0:
            avg_per_file = elapsed / processed
            remaining = total - processed
            eta = avg_per_file * remaining
            self.eta_label.setText(i18n.t("预计剩余：%s") % _format_duration(eta))
        else:
            self.eta_label.setText(i18n.t("预计剩余：--"))

    def _on_convert_stop(self):
        if self._convert_worker is None or not self._convert_worker.isRunning():
            return
        self._stop_requested = True
        self.stop_button.setEnabled(False)
        self.log_edit.appendPlainText(i18n.t("正在停止……（当前文件处理完毕后中止）"))
        self._convert_worker.request_stop()

    def _current_output_format(self):
        """返回输出格式键：jxl / png / jpg（按 format_combo 当前文本判断）。

        与 :meth:`_build_output_path` 共用同一判定，避免扩展名推导逻辑散落多处。
        """
        lower = self.format_combo.currentText().lower()
        if "jpg" in lower:
            return "jpg"
        if "png" in lower:
            return "png"
        return "jxl"

    def _update_format_hint(self):
        """选择「JPEG」输出时在下拉框右侧显示限制提示，其余格式隐藏。"""
        self.format_hint_label.setVisible(
            self._current_output_format() == "jpg"
        )

    def _build_output_path(self, src):
        base, _ = os.path.splitext(src)
        lower = self.format_combo.currentText().lower()
        if "jpg" in lower:
            out_ext = ".jpg"
        elif "png" in lower:
            out_ext = ".png"
        else:
            out_ext = ".jxl"

        # 保留原始扩展名（高级参数，默认关闭）：开启后输出文件沿用输入的扩展名，
        # 而非按输出格式推导（如 .jxl/.png/.jpg）。输入无扩展名时回退到格式推导值。
        if getattr(self, "preserve_ext_check", None) and self.preserve_ext_check.isChecked():
            src_ext = os.path.splitext(src)[1].lower()
            if src_ext:
                out_ext = src_ext

        if self.custom_folder_radio.isChecked():
            folder = self.custom_folder_edit.text().strip()
            if folder:
                # 手动输入或选择的目标文件夹可能尚不存在：自动创建之，
                # 否则 os.path.isdir 为 False 会静默回退到原文件夹，
                # 表现为「手动输入自定义文件夹路径无效」。空路径才回退源目录。
                try:
                    os.makedirs(folder, exist_ok=True)
                except OSError:
                    pass
                if getattr(self, "structure_check", None) and self.structure_check.isChecked():
                    # 保留文件夹结构：按源文件相对「根」的子路径，镜像到自定义文件夹下。
                    root = self.input_roots.get(src)
                    if not root:
                        root = os.path.dirname(src)
                    # 保留上级目录：根上移一级，使被添加文件夹本身成为输出下的顶层段。
                    if getattr(self, "parent_check", None) and self.parent_check.isChecked():
                        parent = os.path.dirname(root)
                        if parent and parent != root:
                            root = parent
                    src_dir = os.path.dirname(src)
                    try:
                        rel = os.path.relpath(src_dir, root)
                    except ValueError:
                        rel = ""  # 跨盘符等无法取相对路径时退化为拍平
                    if rel in ("", "."):
                        out_dir = folder
                    else:
                        out_dir = os.path.join(folder, rel)
                    try:
                        os.makedirs(out_dir, exist_ok=True)
                    except OSError:
                        pass  # 目录创建失败时在编码阶段报错，此处不中断
                    base = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0])
                else:
                    base = os.path.join(folder, os.path.splitext(os.path.basename(src))[0])

        if self.add_suffix_radio.isChecked():
            suffix = self.suffix_edit.text().strip()
            if suffix:
                base = base + suffix

        return base + out_ext

    # ------------------------------------------------------------------
    # 输出文件已存在时的冲突策略（替换 / 询问 / 跳过 / 重命名）
    # ------------------------------------------------------------------
    def _resolve_existing_outputs(self, jobs):
        """按「当输出文件已经存在时」策略预处理 job 列表。

        在主线程执行（不进入 worker 线程弹窗）。返回
        ``(已解析 jobs, 被跳过的输出路径列表, 是否用户取消全部)``。

        - 替换：cjxl/djxl 默认即覆盖已存在文件，原样返回 jobs，无需额外处理；
        - 跳过：输出已存在则移出 jobs（不调用编码），记入 skipped；
        - 重命名：输出已存在则改写为 ``name (1).ext`` 等首个不冲突路径；
        - 询问：对首个冲突文件逐个弹窗，用户可选
          全部替换 / 替换 / 跳过 / 重命名 / 取消全部。选择「全部替换」后，
          本批次剩余冲突文件不再弹窗，统一按「替换」处理。
        """
        # 每批次重新初始化「全部替换」标志，避免跨批次沿用。
        self._replace_all_pending = False
        # 取 userData（中文 ID）而非显示文本 —— 下面的分支判断全靠它。
        on_exist = (self.on_exist_combo.currentData()
                    or self.on_exist_combo.currentText())
        if on_exist == "替换":
            return jobs, [], False
        resolved, skipped, cancelled = [], [], False
        for src, out_path, out_is_jxl in jobs:
            if not os.path.exists(out_path):
                resolved.append((src, out_path, out_is_jxl))
                continue
            if on_exist == "跳过":
                skipped.append(out_path)
            elif on_exist == "重命名":
                resolved.append((src, self._uniquify_path(out_path), out_is_jxl))
            elif on_exist == "询问":
                if getattr(self, "_replace_all_pending", False):
                    resolved.append((src, out_path, out_is_jxl))
                    continue
                choice = self._ask_on_exist(out_path)
                if choice == "replace":
                    resolved.append((src, out_path, out_is_jxl))
                elif choice == "replace_all":
                    self._replace_all_pending = True
                    resolved.append((src, out_path, out_is_jxl))
                elif choice == "skip":
                    skipped.append(out_path)
                elif choice == "rename":
                    resolved.append((src, self._uniquify_path(out_path), out_is_jxl))
                else:  # cancel
                    cancelled = True
                    break
        return resolved, skipped, cancelled

    def _uniquify_path(self, path):
        """若 ``path`` 已存在，返回 ``base (1).ext`` 形式、首个尚未存在的路径。"""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while True:
            cand = "%s (%d)%s" % (base, i, ext)
            if not os.path.exists(cand):
                return cand
            i += 1

    def _ask_on_exist(self, out_path):
        """输出文件已存在且策略为「询问」时，弹窗让用户决定单个文件的处理方式。

        返回 ``"replace"`` / ``"skip"`` / ``"rename"`` / ``"cancel"``。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(i18n.t("输出文件已存在"))
        box.setText(i18n.t("输出文件已存在：\n%s\n\n如何处理该文件？") % out_path)
        b_replace_all = box.addButton(i18n.t("全部替换"), QMessageBox.AcceptRole)
        b_replace = box.addButton("替换", QMessageBox.AcceptRole)
        b_skip = box.addButton("跳过", QMessageBox.RejectRole)
        b_rename = box.addButton("重命名", QMessageBox.ActionRole)
        b_cancel = box.addButton(i18n.t("取消全部"), QMessageBox.DestructiveRole)
        # 清空标准按钮，避免额外 OK/Cancel 与自定义按钮叠加。
        box.setStandardButtons(QMessageBox.NoButton)
        box.exec()
        clicked = box.clickedButton()
        if clicked == b_replace_all:
            return "replace_all"
        if clicked == b_replace:
            return "replace"
        if clicked == b_skip:
            return "skip"
        if clicked == b_rename:
            return "rename"
        return "cancel"


    # ------------------------------------------------------------------
    # Environment detection (written into the 状态 tab log)
    # ------------------------------------------------------------------
    def _refresh_environment(self):
        os_id = converter.detect_os()
        tools = converter.check_tools()
        cjxl_state = i18n.t("已找到") if tools["cjxl"] else i18n.t("未找到")
        djxl_state = i18n.t("已找到") if tools["djxl"] else i18n.t("未找到")
        self.log_edit.appendPlainText(
            i18n.t("检测到操作系统：%s | cjxl：%s | djxl：%s")
            % (os_id, cjxl_state, djxl_state)
        )
        if tools["cjxl"]:
            version = converter.get_cjxl_version()
            if version:
                self.log_edit.appendPlainText(version)
        if not tools["cjxl"] or not tools["djxl"]:
            self.statusBar().showMessage(i18n.t("提示：cjxl / djxl 未完全就绪"))
        else:
            self.statusBar().showMessage(i18n.t("环境就绪：cjxl 与 djxl 均可用"))


class CalibrateWorker(QThread):
    """在后台线程运行大图像素地板校准，避免阻塞 UI。

    进度通过 status_signal（状态栏）与 log_signal（状态标签页日志）回流；
    done_signal 携带校准结果（int 像素地板 或 None）。
    """

    log_signal = Signal(str)
    status_signal = Signal(str)
    done_signal = Signal(object)

    def __init__(self, effort=7, runs=3, max_mp=64, parent=None):
        super().__init__(parent)
        self.effort = effort
        self.runs = runs
        self.max_mp = max_mp
        self._abort = False

    def run(self):
        def progress_cb(msg):
            if self._abort:
                return
            self.status_signal.emit(msg)

        def log_cb(line):
            if self._abort:
                return
            self.log_signal.emit(line)

        floor_px = calibrate.run_calibration(
            progress_cb=progress_cb,
            log_cb=log_cb,
            effort=self.effort,
            runs=self.runs,
            max_mp=self.max_mp,
        )
        if floor_px is not None:
            # 按当前完整电源状态记录阈值（同时写通用键、状态专属复合键、兼容方案键），
            # 并记下 CPU 指纹，以便切换计划/插拔电/模式自动套用、或换硬件时丢弃旧数据。
            try:
                state = power.get_power_state()
                calibrate.write_cpu_signature(power.cpu_signature())
            except Exception:
                state = (None, None, None)
            calibrate.write_floor_px(
                floor_px, scheme=state[0], ac=state[1], mode=state[2])
        self.done_signal.emit(floor_px)


class ConvertWorker(QThread):
    """Run the cjxl/djxl conversion loop off the UI thread.

    The job list (``(src, out_path, out_is_jxl)`` tuples) and the action list
    are built on the UI thread and passed in, so this worker never touches any
    Qt widget. Progress, logs and completion are reported back via signals.
    """

    log_signal = Signal(str)
    status_signal = Signal(str)
    finished_signal = Signal()
    progress_signal = Signal(int, int)  # (已处理文件数, 总文件数)

    def __init__(self, jobs, actions, effort=7, distance=None,
                 quality=None, lossless_jpeg=False,
                 priority=converter.DEFAULT_PRIORITY, advanced=None,
                 custom_cmd=None, cpu_cores="auto", adv_threads_enabled=False,
                 decode_threads_enabled=False,
             out_fmt="jxl", discard_if_larger=False,
             delete_original=False,
             preserve_ctime=False, preserve_mtime=False):
        super().__init__()
        self.jobs = jobs
        self.actions = actions  # possibly empty list
        self.effort = effort
        self.distance = distance
        self.quality = quality
        self.lossless_jpeg = lossless_jpeg
        self.priority = priority
        # 高级参数 dict（来自 UI _collect_advanced）；键名与 converter.encode 一致，
        # 由 _encode_kwargs 直接展开并覆盖同名基础参数（例如显式 -d 距离）。
        self.advanced = advanced or {}
        # 自定义命令：若提供，逐文件执行该命令（<输入>/<输出> 占位符替换），
        # 取代自动拼装的 cjxl 调用。None 表示不使用自定义命令。
        self.custom_cmd = custom_cmd
        # CPU 核心使用数：int（用户选定）或 "auto"（本机逻辑核心数）。决定并行度。
        self.cpu_cores = cpu_cores
        # 是否启用高级参数手动设置每文件线程数（--num_threads）。
        self.adv_threads_enabled = adv_threads_enabled
        # 解码侧线程控制：默认关闭（djxl 自己吃满核心最快，见 _decode_kwargs 说明）。
        self.decode_threads_enabled = decode_threads_enabled
        # 输出格式键（jxl / png / jpg），供 _encode_tag 在解码/重建路径下
        # 返回正确的重建标签，避免误用 JXL 编码标签（如 [VarDCT, q90]）。
        self._out_fmt = out_fmt
        # 删除原文件：勾选时，成功转换的源文件在批处理结束后由主线程移入回收站。
        self.delete_original = delete_original
        # 编码结果更大时丢弃输出（保留原文件）：仅 JXL 输出生效；PNG/JPEG 不适用。
        self.discard_if_larger = discard_if_larger
        # 保持时间戳：成功转换后是否把输出文件的时间属性还原为与原文件一致。
        self.preserve_ctime = preserve_ctime
        self.preserve_mtime = preserve_mtime
        # 收集「成功转换」的源文件路径，供主线程在转换结束后移入回收站。
        # 仅当 delete_original 为真且该文件成功（ok）才入列；失败的不入列。
        self._ok_sources = []
        # 由 _resolve_concurrency 在 run() 开头计算；_encode_kwargs 用它统一覆盖
        # 高级参数里的 num_threads，避免与文件级并行叠加导致超订。
        self._per_file_threads = None
        self._total = 0
        self._stopped = False
        # 大图像素地板：优先读 QSettings 校准值（tools/calibrate_floor.py 写入），
        # 否则回退到按 CPU 核数的启发式估计。分类时用于「绝对值」闸门。
        self.big_image_floor_px = read_big_image_floor_px(self._effective_cores())

    def _encode_kwargs(self):
        """Encode keyword arguments shared by every cjxl invocation."""
        kw = {
            "effort": self.effort,
            "distance": self.distance,
            "quality": self.quality,
            "lossless_jpeg": self.lossless_jpeg,
            "priority": self.priority,
        }
        kw.update(self.advanced)
        # 并行池统一控制每文件线程数：覆盖高级参数里的 num_threads（若存在），
        # 防止与文件级并行叠加导致 CPU 超订。单文件时这里会设为全部核心。
        if getattr(self, "_per_file_threads", None) is not None:
            kw["num_threads"] = self._per_file_threads
        return kw

    def _decode_kwargs(self):
        """djxl 解码用的关键字参数（与 :meth:`_encode_kwargs` 对应）。

        ``--num_threads`` **默认不传**（``decode_threads_enabled=False``），让 djxl
        自己按机器决定、吃满核心——20 核机上实测：解码的线程收益远小于编码
        （满核 vs 单线程约 1.2x，编码是 2.3x），多文件并发时限制解码线程反而
        慢 7%~13%。所以它做成设置页开关而非常驻行为。

        开启后取当前阶段的每文件线程预算 ``_per_file_threads``（与 cjxl 编码同一
        口径），使「CPU 核心使用数」在解码路径同样生效，代价是上述的速度损失。
        """
        kw = {"priority": self.priority}
        if self.decode_threads_enabled:
            per_file = getattr(self, "_per_file_threads", None)
            # 排除 bool（int 子类）+ 未计算（None）的情形，避免拼出非法参数。
            if isinstance(per_file, int) and not isinstance(per_file, bool):
                kw["num_threads"] = per_file
        return kw

    def _resolve_concurrency(self, n_jobs):
        """把「CPU 核心使用数」解析为 (cores, per_file_threads, pool_size)。

        - 多文件：同时跑 pool_size 个 cjxl 进程；每个进程 --num_threads=1（或
          高级开关打开时为用户设定值），总核占用 ≈ 设定核心数，不会超订。
        - 单文件：不开多进程，直接把全部核心交给这一个 cjxl（--num_threads=cores），
          否则单文件只用 1 核太浪费。

        高级参数里显式设定 --num_threads 时（见 :func:`_adv_num_threads`），
        单文件一律照用；多文件按档位收缩进程数：
          * ``-1``（工具自己决定，会吃满核心）-> pool_size=1，只能串行；
          * ``0``（禁用多线程）-> 进程不占线程预算，开满 min(n_jobs, cores)；
          * ``N>=1`` -> pool_size = min(n_jobs, cores // N)。
        """
        cores = self.cpu_cores
        if not isinstance(cores, int) or cores < 1:
            cores = os.cpu_count() or 1
        adv_num = _adv_num_threads(self.adv_threads_enabled, self.advanced)
        if n_jobs <= 1:
            per_file = adv_num if adv_num is not None else cores
            pool_size = 1
        else:
            per_file = adv_num if adv_num is not None else 1
            if per_file == -1:
                pool_size = 1
            elif per_file == 0:
                pool_size = min(n_jobs, cores)
            else:
                pool_size = max(1, min(n_jobs, cores // per_file))
        return cores, per_file, pool_size

    def _big_threads(self, cores):
        """大图阶段的每文件线程数：默认独占满核，用户显式设定时照用。

        大图是串行逐个跑的，给满核心最快；但高级参数里若显式设了
        ``--num_threads``（-1 / 0 / N）就该尊重用户意图——否则「禁用多线程」
        这类设置在含大图的批次里会静默失效。
        """
        adv = _adv_num_threads(self.adv_threads_enabled, self.advanced)
        return cores if adv is None else adv

    def _encode_tag(self):
        """Bracketed, human-readable description of how files are encoded.

        Mirrors the 状态 tab per-file line, e.g. '[Modular, lossless]' for the
        lossless mode, '[VarDCT, q90]' for lossy, '[JPEG lossless]' for the JPG
        re-encode mode. The conversion parameters are identical for every job,
        so this is computed once before the loop.
        """
        # 自定义命令模式：统一标记为 [自定义命令]（与输出格式无关，优先级最高）。
        if self.custom_cmd:
            return i18n.t("[自定义命令]")
        # 输出格式非 JXL（PNG / JPEG）走解码/重建路径，不经过 cjxl 编码，
        # 不应显示 JXL 的编码标签（如 [VarDCT, q90]），否则会误导。
        out_fmt = getattr(self, "_out_fmt", "jxl")
        if out_fmt == "jpg":
            return i18n.t("[JPEG 重建]")
        if out_fmt == "png":
            return i18n.t("[PNG 重建]")
        # 高级参数 -d 会覆盖基础 distance，两者取其一。以下仅 JXL 输出生效。
        dist = self.advanced.get("distance", self.distance)
        if self.lossless_jpeg:
            return "[JPEG lossless]"
        if dist == 0:
            # 无损 JPEG XL 始终走 Modular 模式。
            return "[Modular, lossless]"
        # 有损：默认 VarDCT，显式 modular=1 时走 Modular。
        if self.advanced.get("modular") == 1:
            codec = "Modular"
        else:
            codec = "VarDCT"
        if self.quality is not None:
            return "[%s, q%d]" % (codec, self.quality)
        return "[%s]" % codec

    def request_stop(self):
        """Ask the loop to stop. Sets a flag checked between files and kills
        the in-flight cjxl/djxl child process so a long single-file job does
        not block the stop request."""
        self._stopped = True
        converter.terminate_current()

    def _make_temp(self, suffix):
        """Create an empty temp file with the given suffix and return its path."""
        import tempfile
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return path

    def _process_with_actions(self, src, out_path, out_is_jxl, actions, tmp_files):
        """Open ``src`` (decoding .jxl via djxl first), run the Pillow actions,
        then write ``out_path`` as JXL (cjxl) or PNG. Returns ``(success, message)``.
        """
        from PIL import Image  # local import; guarded by processor.AVAILABLE

        if src.lower().endswith(".jxl"):
            tmp_src = self._make_temp(".png")
            tmp_files.append(tmp_src)
            ok, msg = converter.decode(src, tmp_src, **self._decode_kwargs())
            if not ok:
                return False, i18n.t("djxl 解码失败：%s") % msg, ""
            img = Image.open(tmp_src)
        else:
            img = Image.open(src)

        img = processor.apply_actions(img, actions)

        if out_is_jxl:
            tmp_png = self._make_temp(".png")
            tmp_files.append(tmp_png)
            img.save(tmp_png, "PNG")
            return converter.encode(tmp_png, out_path, **self._encode_kwargs())
        img.save(out_path)
        return True, i18n.t("已保存为 PNG。"), ""

    def _run_custom_command(self, src, out_path):
        """执行用户自定义命令（<输入>/<输出> 占位符替换为真实路径后）。

        返回 (ok, message)，与 converter.encode 的契约一致，便于 run() 复用
        同一套状态统计逻辑。
        """
        cmd = (self.custom_cmd or "").replace("<输入>", src).replace(
            "<输出>", out_path
        )
        try:
            tokens = shlex.split(cmd, posix=False)
        except ValueError as exc:
            return False, i18n.t("自定义命令解析失败：%s") % exc, ""
        if not tokens:
            return False, i18n.t("自定义命令为空。"), ""
        ok, msg, err = converter._run(tokens, priority=self.priority)
        return ok, msg, converter.parse_encoding_tag(err)

    def _encode_source(self, src, out_path, tmp_files):
        """Encode a non-.jxl source into ``out_path`` via cjxl.

        cjxl's built-in reader only accepts PNG/APNG/GIF/JPEG/EXR/PPM/PFM/PAM/
        PGX (and JXL). Formats like WebP/BMP/TIFF are NOT readable by cjxl, which
        then fails with "Getting pixel data failed". As a fallback we decode
        such a source with Pillow into a temporary PNG — preserving the embedded
        ICC profile so colors stay accurate — and re-encode that.

        When the output is a PNG, cjxl cannot help (it only ever produces JXL),
        so we decode with Pillow and save the raster directly. This is the
        "any image -> PNG" path and avoids a ".png" file that is actually JXL
        data (a fake PNG).
        """
        if out_path.lower().endswith(".png"):
            # 输出 PNG：cjxl 只能产出 jxl，无法真正输出 png。非 jxl 输入直接由
            # Pillow 解码并保存为 png（保留 ICC 配置），避免“名不副实的假 png”。
            if not processor.AVAILABLE:
                return False, i18n.t("输出 PNG 需要 Pillow 支持（请先安装 Pillow）"), ""
            try:
                from PIL import Image
                img = Image.open(src)
                icc = img.info.get("icc_profile")
                if icc:
                    img.save(out_path, "PNG", icc_profile=icc)
                else:
                    img.save(out_path, "PNG")
                return True, i18n.t("已保存为 PNG（Pillow 解码）。"), ""
            except Exception as exc:
                detail = str(exc).replace(chr(92) + chr(92), chr(92))
                return False, i18n.t("Pillow 解码失败：%s") % detail, ""
        ok, message, tag = converter.encode(src, out_path, **self._encode_kwargs())
        if ok:
            return True, message, tag
        # Native encode failed — fall back to a Pillow-based decode for inputs
        # cjxl cannot read directly (e.g. WebP, BMP, TIFF).
        if not processor.AVAILABLE:
            return False, message + "\n    （提示：安装 Pillow 后可兼容 WebP 等更多输入格式）", ""
        try:
            from PIL import Image
            img = Image.open(src)
            tmp_png = self._make_temp(".png")
            tmp_files.append(tmp_png)
            icc = img.info.get("icc_profile")
            if icc:
                img.save(tmp_png, "PNG", icc_profile=icc)
            else:
                img.save(tmp_png, "PNG")
        except Exception as exc:
            # Pillow's UnidentifiedImageError embeds repr(path), which shows
            # the backslashes doubled ("F:\\..."). Normalize to a single
            # backslash so the path reads naturally in the status log.
            detail = str(exc).replace("\\\\", "\\")
            return False, i18n.t("cjxl 无法读取该输入格式，且 Pillow 解码失败：%s") % detail, ""
        ok2, msg2, tag2 = converter.encode(tmp_png, out_path, **self._encode_kwargs())
        if ok2:
            return True, i18n.t("通过 Pillow 兼容解码（输入格式 cjxl 不支持）后编码完成。"), tag2
        return False, msg2, tag2

    def run(self):
        """Run the conversion as a dual-queue scheduler.

        文件按像素数分类（见 :meth:`_classify_jobs`）：
          * 小图 -> 并行池，每图分配一部分核心（``cores // min(k, cores)`` 线程），
            多张小图同时转、不超订；
          * 大图 -> 独占全部核心、逐个串行，单张大图不会让其余 CPU 闲置。

        每文件线程数经 ``_encode_kwargs`` 注入（覆盖高级参数里的 num_threads），
        使总 CPU 占用贴近「CPU 核心使用数」预算。
        """
        total = len(self.jobs)
        self._total = total
        self._stat_started = time.time()
        self._stat_processed = 0
        self._stat_ok = 0
        self._stat_err = 0
        self._stat_in_bytes = 0
        self._stat_out_bytes = 0
        cores = self._effective_cores()
        auto = not isinstance(self.cpu_cores, int)
        try:
            self.log_signal.emit(_LOG_SEPARATOR)
            self.log_signal.emit(
                i18n.t("并发设置：核心数=%s，双队列调度（大图独占满核 / 小图并行均分）")
                % (self.cpu_cores if not auto else i18n.t("自动"))
            )
            self.log_signal.emit("")
            self.log_signal.emit("开始转换：" + _format_datetime(self._stat_started))
            self.log_signal.emit("")
            if total == 0:
                return

            all_indexed = list(enumerate(self.jobs, start=1))
            small, big, nt_small, pool_small = self._classify_jobs(all_indexed)
            nt_big = self._big_threads(cores)
            self.log_signal.emit(
                i18n.t("调度分类：小图 %d 张（每图 %d 线程并行）/ 大图 %d 张（每图 %d 线程）")
                % (len(small), nt_small, len(big), nt_big)
            )
            self.log_signal.emit("")

            # 阶段 1：小图并行池。整批跑完后才放大图，保证大图启动时无小图在跑、
            # 真正独占全部核心（见 docs/design-decisions.md 双队列说明）。
            self._per_file_threads = nt_small
            if small:
                self._run_pool(small, pool_small or 1)

            # 阶段 2：大图逐个串行（在途小图已在阶段 1 跑完，此处完全独占）。
            # 线程数默认给满核心；若用户显式设了 --num_threads（含 0 / -1 档位）
            # 则照用，否则大图会无视该设置。
            if big and not self._stopped:
                self._per_file_threads = nt_big
                for indexed_job in big:
                    if self._stopped:
                        break
                    self._run_single(indexed_job)

            if self._stopped:
                self.log_signal.emit(i18n.t("已停止。"))
        finally:
            self.finished_signal.emit()

    def _run_pool(self, indexed_jobs, pool_size):
        """把 ``indexed_jobs``（``(index, job)`` 列表）提交到 bounded 线程池运行。

        与旧的单池循环一致：``pool_size`` 个 cjxl 进程并发，每个用
        ``self._per_file_threads`` 线程；完成后即时记录结果，保证每个文件的
        两行日志相邻不交错。
        """
        import concurrent.futures as cf

        executor = cf.ThreadPoolExecutor(max_workers=max(1, pool_size))
        futures = {}
        pending = list(indexed_jobs)

        def submit_next():
            while pending and len(futures) < max(1, pool_size):
                idx, job = pending.pop(0)
                fut = executor.submit(self._process_job, idx, *job)
                futures[fut] = (idx, job[0])

        submit_next()
        while futures and not self._stopped:
            done, _ = cf.wait(
                list(futures), timeout=0.1,
                return_when=cf.FIRST_COMPLETED,
            )
            for fut in done:
                rec = futures.pop(fut, None)
                if rec is None:
                    continue
                idx, src = rec
                if fut.cancelled():
                    continue
                try:
                    res = fut.result()
                except Exception as exc:
                    res = (False, i18n.t("处理出错：%s") % exc, 0, 0, True)
                ok, message, tag, in_size, out_size, stopped, discarded = res
                if stopped:
                    continue
                self._record_result(idx, src, ok, message, in_size,
                                   out_size, tag, discarded)
            submit_next()
        if self._stopped:
            for fut in list(futures):
                fut.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        else:
            executor.shutdown(wait=True)

    def _run_single(self, indexed_job):
        """同步处理单个（大图）任务，使用当前 ``_per_file_threads``（调用方设为满核）。"""
        if self._stopped:
            return
        idx, job = indexed_job
        res = self._process_job(idx, *job)
        ok, message, tag, in_size, out_size, stopped, discarded = res
        if stopped:
            return
        self._record_result(idx, job[0], ok, message, in_size, out_size, tag, discarded)

    def _effective_cores(self):
        """返回有效核心数（'auto' -> 本机逻辑核心数）。"""
        cores = self.cpu_cores
        if not isinstance(cores, int) or cores < 1:
            cores = os.cpu_count() or 1
        return cores

    def _classify_jobs(self, indexed_jobs):
        """把 ``[(index, job), ...]`` 分为小图 / 大图两个队列。

        一张图被判为「大图」满足下列任一即可：
          * 像素数 >= 已校准地板 ``big_image_floor_px``（硬门槛，独立生效）；
          * 像素数 > ``BIG_IMAGE_RATIO`` × 本批次像素中位数（离群兜底：未达地板
            但明显偏大的图也被隔离，并兼容未校准 / 低核禁用地板的情况）。

        两道闸门取并集而非交集：中位数判定依赖批次内存在可并行的小图，单张图或
        整批同类大图时中位数==自身、相对闸门恒假，若取交集会把它们误判为小图。
        返回 ``(small, big, nt_small,
        pool_small)``，其中 ``nt_small``/``pool_small`` 给出小图并行池配置
        （每小图 ``cores // min(k, cores)`` 线程）。
        """
        cores = self._effective_cores()
        floor_px = self.big_image_floor_px
        adv_num = _adv_num_threads(self.adv_threads_enabled, self.advanced)

        pixels = []
        for _idx, job in indexed_jobs:
            w, h = get_image_dims(job[0])
            pixels.append(w * h if (w and h) else 0)
        valid = [p for p in pixels if p > 0]

        def _small_pool(k):
            if adv_num is not None:
                nt = adv_num
                if adv_num == -1:
                    # 每个进程都会按机器默认吃满核心，同时跑多个必然超订。
                    pool = 1
                elif adv_num == 0:
                    # 单线程进程不占线程预算，按核心数开满进程数。
                    pool = min(k or 1, cores)
                else:
                    pool = max(1, min(k, cores // adv_num)) if adv_num <= cores else 1
            else:
                nt = max(1, cores // min(k or 1, cores))
                pool = min(k, cores)
            return nt, pool

        if not valid:
            # 全部尺寸未知 → 当作小图整批均分核心（不判大图）。
            small = list(indexed_jobs)
            k = len(small)
            nt_small, pool_small = _small_pool(k)
            return small, [], nt_small, pool_small

        median_px = sorted(valid)[len(valid) // 2]
        thr = BIG_IMAGE_RATIO * median_px
        small, big = [], []
        for (idx, job), p in zip(indexed_jobs, pixels):
            if p >= floor_px or p > thr:
                big.append((idx, job))
            else:
                small.append((idx, job))
        k = len(small)
        nt_small, pool_small = _small_pool(k)
        return small, big, nt_small, pool_small

    def _process_job(self, index, src, out_path, out_is_jxl):
        """Process a single job synchronously (in its own thread) and return a
        result tuple ``(ok, message, tag, in_size, out_size, stopped)``.

        ``stopped`` is True when the job failed only because the user pressed 停止
        mid-run (the child cjxl/djxl was terminated); such jobs are not counted as
        errors by the caller.
        """
        try:
            in_size = _safe_getsize(src)
            self.status_signal.emit(
                i18n.t("正在处理 (%d/%d)：%s") % (index, self._total, os.path.basename(src))
            )
            tmp_files = []
            try:
                if self.custom_cmd:
                    # 自定义命令模式：跳过 Pillow 动作与自动拼装，直接执行用户命令。
                    ok, message, tag = self._run_custom_command(src, out_path)
                elif self.actions:
                    ok, message, tag = self._process_with_actions(
                        src, out_path, out_is_jxl, self.actions, tmp_files
                    )
                elif src.lower().endswith(".jxl"):
                    if out_is_jxl:
                        # cjxl natively transcodes a JXL input into a JXL
                        # output — drop --lossless_jpeg (JPEG-only flag).
                        kw = self._encode_kwargs()
                        kw.pop("lossless_jpeg", None)
                        ok, message, tag = converter.encode(src, out_path, **kw)
                    else:
                        # Decoding a JXL into a raster (PNG) needs djxl.
                        ok, message = converter.decode(
                            src, out_path, **self._decode_kwargs()
                        )
                        tag = ""
                else:
                    ok, message, tag = self._encode_source(src, out_path, tmp_files)
            finally:
                for t in tmp_files:
                    try:
                        if os.path.exists(t):
                            os.remove(t)
                    except OSError:
                        pass
            # 若运行过程中被中止，子进程被杀会返回失败；标记为 stopped 不计入统计。
            if (not ok) and self._stopped:
                return (False, message, "", in_size, 0, True, False)
            out_size = _safe_getsize(out_path) if ok else 0
            discarded = False
            # 编码结果更大时丢弃输出（保留原文件）：仅 JXL 输出适用。
            # PNG/JPEG 是用户主动选定的输出格式，不能因「更大」而丢弃。
            if (ok and self.discard_if_larger and self._out_fmt == "jxl"
                    and in_size > 0 and out_size >= in_size):
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                    discarded = True
                    self.log_signal.emit(
                        i18n.t("编码结果较大（%d ≥ %d 字节），已丢弃 JXL 输出，保留原文件。")
                        % (out_size, in_size)
                    )
                except OSError as exc:
                    self.log_signal.emit(
                        i18n.t("丢弃较大 JXL 输出失败（已保留）：%s —— %s") % (out_path, exc)
                    )
            # 保持时间戳：成功且未丢弃时，把输出文件的时间属性还原为与原文件一致。
            # 任一失败都不影响转换结果（ok 保持 True），仅记日志；丢弃的输出已不存在。
            if ok and not discarded and (self.preserve_mtime or self.preserve_ctime):
                try:
                    if self.preserve_mtime:
                        _preserve_mtime(src, out_path)
                    if self.preserve_ctime:
                        _preserve_ctime(src, out_path)
                except Exception as exc:
                    self.log_signal.emit(
                        i18n.t("保持时间戳失败（已忽略）：%s —— %s") % (out_path, exc)
                    )
            return (ok, message, tag, in_size, (0 if discarded else out_size), False, discarded)
        except Exception as exc:
            stopped = self._stopped
            return (False, i18n.t("处理出错：%s") % exc, "", in_size, 0, stopped, False)

    def _record_result(self, index, src, ok, message, in_size, out_size, tag,
                       discarded=False):
        """Update running statistics and emit the per-file log block.

        The '>>> [n/m] path' header and the size/failure line are emitted here
        together at job completion (the orchestrator thread), so each file's two
        lines stay adjacent even under parallel execution — no scrambled order.
        """
        self.log_signal.emit(">>> [%d/%d] %s" % (index, self._total, src))
        self._stat_processed += 1
        self._stat_in_bytes += in_size
        self.progress_signal.emit(self._stat_processed, self._total)
        if ok:
            self._stat_out_bytes += out_size
            self._stat_ok += 1
            # 删除原文件：本文件已成功转换，登记源路径，待主线程批处理结束后
            # 统一移入回收站。但若本次「丢弃了输出」（无产出），则视为未成功产出，
            # 不登记源——与「保留原文件」语义一致，避免误删原文件。
            if self.delete_original and not discarded:
                self._ok_sources.append(src)
            # 优先用 cjxl 真实输出抓取的编码标签；若解析为空（如 djxl 解码、
            # 自定义命令无 Encoding 行），兜底用规则推导。
            final_tag = tag or self._encode_tag()
            if not discarded:
                self.log_signal.emit(
                    _format_size_change(in_size, out_size, final_tag)
                )
        else:
            self._stat_err += 1
            self.log_signal.emit(i18n.t("处理失败：%s") % message)


class ActionParamDialog(QDialog):
    """Modal dialog that collects parameters for a single action type.

    ``get_params()`` returns the parameter dict for the chosen action type;
    invalid combinations (e.g. resize with both dimensions left at 0) are
    rejected in :meth:`accept` with a warning instead of closing the dialog.
    """

    def __init__(self, action_type, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("动作参数 - %s") % action_type)
        self.action_type = action_type
        self._controls = {}  # name -> (widget, getter)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        if action_type == "调整大小":
            w = QSpinBox()
            w.setRange(0, 100000)
            w.setSpecialValueText(i18n.t("自动(按比例)"))
            h = QSpinBox()
            h.setRange(0, 100000)
            h.setSpecialValueText(i18n.t("自动(按比例)"))
            form.addRow(i18n.t("目标宽度 (像素, 0=自动):"), w)
            form.addRow(i18n.t("目标高度 (像素, 0=自动):"), h)
            self._controls["width"] = (w, lambda: w.value())
            self._controls["height"] = (h, lambda: h.value())
        elif action_type == "旋转":
            a = QSpinBox()
            a.setRange(-360, 360)
            a.setValue(90)
            form.addRow(i18n.t("顺时针角度 (度):"), a)
            self._controls["angle"] = (a, lambda: a.value())
        elif action_type == "水印":
            t = QLineEdit("Sample")
            fs = QSpinBox()
            fs.setRange(8, 400)
            fs.setValue(32)
            op = QSpinBox()
            op.setRange(0, 255)
            op.setValue(128)
            pos = NoFlickerComboBox()
            for _pid in processor.WATERMARK_POSITIONS:
                pos.addItem(i18n.t(_pid), _pid)
            _pi = pos.findData("右下")
            if _pi >= 0:
                pos.setCurrentIndex(_pi)
            col = NoFlickerComboBox()
            col.addItems(["white", "black"])
            col.setCurrentText("white")
            form.addRow(i18n.t("水印文字:"), t)
            form.addRow(i18n.t("字号:"), fs)
            form.addRow(i18n.t("透明度 (0-255):"), op)
            form.addRow(i18n.t("位置:"), pos)
            form.addRow(i18n.t("颜色:"), col)
            self._controls["text"] = (t, lambda: t.text())
            self._controls["font_size"] = (fs, lambda: fs.value())
            self._controls["opacity"] = (op, lambda: op.value())
            self._controls["position"] = (pos, lambda: pos.currentData())
            self._controls["color"] = (col, lambda: col.currentText())
        elif action_type == "亮度/对比度":
            b = QDoubleSpinBox()
            b.setRange(0.0, 3.0)
            b.setSingleStep(0.1)
            b.setValue(1.0)
            c = QDoubleSpinBox()
            c.setRange(0.0, 3.0)
            c.setSingleStep(0.1)
            c.setValue(1.0)
            form.addRow(i18n.t("亮度 (1.0=不变):"), b)
            form.addRow(i18n.t("对比度 (1.0=不变):"), c)
            self._controls["brightness"] = (b, lambda: b.value())
            self._controls["contrast"] = (c, lambda: c.value())
        elif action_type == "锐化":
            f = QDoubleSpinBox()
            f.setRange(0.0, 5.0)
            f.setSingleStep(0.1)
            f.setValue(1.5)
            form.addRow(i18n.t("锐化强度 (1.0=不变):"), f)
            self._controls["factor"] = (f, lambda: f.value())
        elif action_type == "裁剪":
            for label, name in (
                (i18n.t("左边距 (像素):"), "left"),
                (i18n.t("上边距 (像素):"), "top"),
                (i18n.t("宽度 (像素, 0=到边界):"), "width"),
                (i18n.t("高度 (像素, 0=到边界):"), "height"),
            ):
                sp = QSpinBox()
                sp.setRange(0, 100000)
                sp.setValue(0)
                form.addRow(label, sp)
                self._controls[name] = (sp, lambda sp=sp: sp.value())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if self.action_type == "调整大小":
            w = int(self._controls["width"][1]())
            h = int(self._controls["height"][1]())
            if w == 0 and h == 0:
                self.parent()._show_warning_centered(
                    i18n.t("参数无效"), i18n.t("调整大小需至少设置宽度或高度之一。"))
                return
        elif self.action_type == "裁剪":
            w = int(self._controls["width"][1]())
            h = int(self._controls["height"][1]())
            if w == 0 and h == 0:
                self.parent()._show_warning_centered(
                    i18n.t("参数无效"), i18n.t("裁剪需至少设置宽度或高度之一。"))
                return
        elif self.action_type == "水印":
            if not (self._controls["text"][1]() or "").strip():
                self.parent()._show_warning_centered(
                    i18n.t("参数无效"), i18n.t("水印文字不能为空。"))
                return
        super().accept()

    def get_params(self):
        params = {}
        for name, (widget, getter) in self._controls.items():
            params[name] = getter()
        return params

