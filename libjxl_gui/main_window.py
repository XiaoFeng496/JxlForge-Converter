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
import atexit
import shutil
import shlex
import tempfile

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QSize,
    Qt,
    QSettings,
    QTimer,
    QThread,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QPalette,
    QFontMetrics,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPixmap,
    QWheelEvent,
    QCursor,
    QMouseEvent,
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

from . import converter, processor


# ----------------------------------------------------------------------
# Preview / thumbnail support for formats Qt cannot load natively
#
# Qt's built-in image readers (QImageReader / QPixmap) do NOT understand some
# modern containers (JPEG XL, AVIF), so such files yield a null pixmap — no
# thumbnail and no double-click preview. We decode those to a temporary PNG
# (via djxl for JXL, via Pillow for AVIF — both already available) and let Qt
# read that instead. Decoded PNGs are cached per source path so repeated
# thumbnails / previews don't re-decode every time.
# ----------------------------------------------------------------------
_DECODE_TO_PNG_EXTS = {".jxl", ".avif"}

_DECODE_PNG_CACHE = {}       # src_path -> decoded temporary PNG path
_DECODE_TEMP_DIR = None


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


def _decode_to_png(path):
    """Decode *path* (a JXL or AVIF file) to a temporary PNG.

    Returns the temporary PNG path on success, or ``None`` on failure.
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
    return None


def _display_path(path):
    """Return a path Qt can actually load for ``path``.

    For natively-supported formats this is ``path`` itself. For JXL/AVIF it
    decodes to a cached temporary PNG and returns that path, or ``None`` if
    decoding failed. Callers treat ``None`` as "cannot display".
    """
    if not path.lower().endswith(tuple(_DECODE_TO_PNG_EXTS)):
        return path
    cached = _DECODE_PNG_CACHE.get(path)
    if cached is not None and os.path.isfile(cached):
        return cached
    try:
        png = _decode_to_png(path)
        if png is not None:
            _DECODE_PNG_CACHE[path] = png
            return png
    except Exception:
        pass
    return None


# Backwards-compatible alias (older tests / callers).
_jxl_display_path = _display_path


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif",
    ".tiff", ".webp", ".ppm", ".pgm", ".jxl", ".avif",
}

# Extensions that denote a real JPEG bitstream. cjxl's --lossless_jpeg=1 can
# only re-encode an actual JPEG, so the JPG 无损重编码 mode must skip others.
JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".jfif"}


def _is_jpeg(path):
    """Whether *path* is a JPEG file, judged by its extension (case-insensitive)."""
    return os.path.splitext(path)[1].lower() in JPEG_EXTENSIONS


# ----------------------------------------------------------------------
# Logging helpers for the 状态 (status) tab conversion report
# ----------------------------------------------------------------------
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
        return "%d 秒" % seconds
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return "%d 分 %d 秒" % (minutes, secs)
    hours = minutes // 60
    mins = minutes % 60
    return "%d 时 %d 分" % (hours, mins)


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
                "已调整顺序，共 %d 个文件" % len(new_order)
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
                "已调整顺序，共 %d 个文件" % len(new_order)
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
        if self._pixmap_item is None:
            return
        self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
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
            self.fit()


class PreviewDialog(QDialog):
    """Centered image preview with wheel zoom and drag-to-pan."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        # Qt cannot load JXL natively, so decode .jxl files to a temporary PNG
        # via djxl first; for every other format this just returns the path.
        display = _display_path(path)
        self.base_pixmap = QPixmap(display) if display else QPixmap()

        name = os.path.basename(path)
        self.setWindowTitle("预览：%s" % name)

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
        self.zoom_in_button = QPushButton("放大 +")
        self.zoom_out_button = QPushButton("缩小 -")
        self.zoom_actual_button = QPushButton("1:1")
        self.fit_button = QPushButton("适应窗口")
        self.close_button = QPushButton("关闭")
        bar.addWidget(self.zoom_in_button)
        bar.addWidget(self.zoom_out_button)
        bar.addWidget(self.zoom_actual_button)
        bar.addWidget(self.fit_button)
        bar.addStretch(1)
        bar.addWidget(self.close_button)
        root.addLayout(bar)

        if self.base_pixmap.isNull():
            self.scroll = None
            msg = QLabel("无法加载图片：%s" % name)
            msg.setAlignment(Qt.AlignCenter)
            root.addWidget(msg, stretch=1)
        else:
            self.scroll = PreviewScroll(self)
            self.scroll.set_pixmap(self.base_pixmap)
            root.addWidget(self.scroll, stretch=1)

        self.zoom_in_button.clicked.connect(lambda: self._zoom(1.2))
        self.zoom_out_button.clicked.connect(lambda: self._zoom(1 / 1.2))
        self.zoom_actual_button.clicked.connect(self._zoom_actual)
        self.fit_button.clicked.connect(self._fit)
        self.close_button.clicked.connect(self.close)

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
    """Two-row widget embedded in each action-list item: the action summary on
    the top line and a compact 上移 / 下移 / 移除 button row beneath it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.item = None  # back-reference, set by the caller
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(5)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.up_button = QPushButton("上移")
        self.down_button = QPushButton("下移")
        self.remove_button = QPushButton("移除")
        for b in (self.up_button, self.down_button, self.remove_button):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        root.addLayout(btn_row)


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
_FUSION_STYLE = None


def _fusion_style():
    global _FUSION_STYLE
    if _FUSION_STYLE is None:
        _FUSION_STYLE = QStyleFactory.create("Fusion")
    return _FUSION_STYLE


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


class NoFlickerComboBox(QComboBox):
    """QComboBox that does not flicker on Windows 10/11. The dropdown popup
    is shown frameless: a frameless (caption-less) top-level window is exempt
    from the DWM slide/fade entrance animation that otherwise flickers. A solid
    background is forced on the popup container so no black flash appears, and
    the Fusion style is applied for clean, native-free rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._apply_fusion_style()
        self.view().setStyleSheet(
            "QAbstractItemView { border: 1px solid palette(mid); "
            "background: palette(base); }"
        )

    def _apply_fusion_style(self):
        """Style this combobox's popup to avoid the Windows DWM flicker, but
        only when the active theme wants dropdowns Fusion-styled. In the pure
        native theme the combobox follows the application-wide (native) style."""
        fusion = _fusion_style()
        if fusion is not None and dropdowns_use_fusion():
            self.setStyle(fusion)
        else:
            # Inherit the application-wide style so the widget reflects the
            # current theme (native, or global Fusion) instead of staying Fusion.
            self.setStyle(QApplication.style())

    def showPopup(self):
        super().showPopup()
        # Only the no-flicker themes (原生（无闪烁） / Fusion) render the popup
        # as a frameless Fusion window; the pure-native theme keeps the system
        # native popup (which may show the Windows DWM entrance animation).
        if not dropdowns_use_fusion():
            return
        # The popup is the top-level window that owns the view. Mark it
        # frameless so Windows does not run the DWM entrance animation.
        container = self.view().window()
        if container is None:
            return
        geo = container.geometry()
        container.setWindowFlags(
            container.windowFlags()
            | Qt.FramelessWindowHint
            | Qt.NoDropShadowWindowHint
        )
        # Solid background on the container (not just the view) so the area
        # around the list never shows through as black during the show.
        container.setStyleSheet(
            "QFrame { background: palette(base); "
            "border: 1px solid palette(mid); }"
        )
        container.setGeometry(geo)
        container.show()


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
        del_btn.setToolTip("删除该历史记录")
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
    {"key": "num_threads", "flag": "--num_threads", "label": "线程数 (--num_threads)",
     "kind": "int", "default": 4, "min": 1, "max": 32,
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
     "group": "容器输出", "modes": ("lossy", "lossless", "lossless_jpeg")},
    {"key": "codestream_level", "flag": "--codestream_level", "label": "码流等级 (--codestream_level)",
     "kind": "int", "default": 5, "min": 0, "max": 10,
     "group": "容器输出", "modes": ("lossy", "lossless", "lossless_jpeg")},
]


class MainWindow(QMainWindow):
    """Main application window (XnConvert-style four tabs)."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("JXL 转换器")
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
        self._convert_worker = None  # background conversion thread (or None)
        self._stop_requested = False  # True while a user-initiated stop is pending
        self._thumb_cache = {}  # (path, size) -> QPixmap  (avoid regenerating)
        self._info_cache = {}   # path -> tooltip text  (avoid re-reading files)
        self._placeholder_cache = {}  # size -> placeholder QPixmap
        self._thumb_timer = None  # batch timer for async thumbnail generation
        self._thumb_queue = []   # pending (item, path, box_square) batches
        self._sized = False     # resize-to-fit (6x3) once, on first show
        self._env_refreshed = False  # _refresh_environment done once, after show
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
            self._settings_loaded = True
        self._bench_enabled = os.environ.get("LIBJXL_BENCH") == "1"
        self._bench_done = False
        self._bench_show_ts = None
        self._app_start = None  # 由 __main__ 在 show 前写入（进程启动时刻）
        # Restore the persisted Input-tab "查看" view mode (after the input
        # tab is built and the default view applied during build).
        self._load_view_mode()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F9:
            self._perf_enabled = not self._perf_enabled
            if self._perf_enabled:
                self._perf_paints = []
                self._perf_fps = 0.0
                self._perf_drag_last = 0.0
                self._perf_status_t = 0.0
                self.statusBar().showMessage(
                    "性能监测已开启（F9 关闭）：请在缩略图视图下框选并拖到边缘触发自动翻页"
                )
            else:
                self.statusBar().showMessage("性能监测已关闭")
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
                "性能监测 绘制 %.2f ms/帧 (峰值 %.2f) | 拖拽中 FPS %.0f"
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
        vp_h = ROWS * grid.height() + sbh
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
        # Pin the window's minimum size to the 6x3 fit so the actions tab (which
        # is intrinsically wider) can never clamp the startup window wider than
        # 6x3. This is the floor; the user can still enlarge the window at any
        # time.
        self.setMinimumSize(w, h)

    # ------------------------------------------------------------------
    # UI construction (all visible strings are Chinese)
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.tabs = QTabWidget()
        self.input_tab = self._build_input_tab()
        self.tabs.addTab(self.input_tab, "输入")
        self.tabs.addTab(self._build_actions_tab(), "动作")
        self.tabs.addTab(self._build_output_tab(), "输出")
        self.status_tab = self._build_status_tab()
        self.tabs.addTab(self.status_tab, "状态")
        self.settings_tab = self._build_settings_tab()
        self.tabs.addTab(self.settings_tab, "设置")
        root.addWidget(self.tabs, stretch=1)

        # Persistent bottom bar: 转换 (left) + 停止 + 关闭 (right).
        bottom = QHBoxLayout()
        self.convert_button = QPushButton("转换")
        self.convert_button.setMinimumHeight(42)
        self.convert_button.clicked.connect(self._on_convert)
        self.stop_button = QPushButton("停止")
        self.stop_button.setMinimumHeight(42)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_convert_stop)
        self.close_button = QPushButton("关闭")
        self.close_button.setMinimumHeight(42)
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.convert_button)
        bottom.addWidget(self.stop_button)
        bottom.addStretch(1)
        bottom.addWidget(self.close_button)
        root.addLayout(bottom, stretch=0)

        self.statusBar().showMessage("就绪")
        self.setAcceptDrops(True)

        # Radio buttons keep their selected (blue) colour even when the window
        # loses focus instead of dimming to gray (see _sync_radio_inactive_palette).
        self._sync_radio_inactive_palette()

    def _build_input_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        toolbar = QHBoxLayout()
        self.add_files_button = QPushButton("添加文件")
        self.add_folder_button = QPushButton("添加文件夹")
        self.remove_button = QPushButton("移除")
        self.clear_button = QPushButton("清空")
        for button in (
            self.add_files_button, self.add_folder_button,
            self.remove_button, self.clear_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)

        # Filter bar: drop-down button (left) + text box "快速过滤" (right).
        self.filter_button = QToolButton()
        self.filter_button.setText("过滤")
        filter_menu = QMenu(self.filter_button)
        filter_menu.addAction(
            "移除已过滤的", lambda: self._on_remove_filtered("filtered")
        )
        filter_menu.addAction(
            "移除未过滤的", lambda: self._on_remove_filtered("unfiltered")
        )
        self.filter_button.setMenu(filter_menu)
        self.filter_button.setPopupMode(QToolButton.InstantPopup)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("快速过滤")
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
        toolbar.addWidget(QLabel("查看："))
        self.view_button.setText("缩略图")
        self.view_button.setPopupMode(QToolButton.InstantPopup)
        self.view_menu = QMenu(self.view_button)
        for mode in VIEW_MODES:
            act = self.view_menu.addAction(mode)
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
        toolbar.addWidget(QLabel("动作类型："))
        self.action_combo = NoFlickerComboBox()
        self.action_combo.addItems(
            ["调整大小", "旋转", "水印", "亮度/对比度", "锐化", "裁剪"]
        )
        self.add_action_button = QPushButton("添加动作")
        self.clear_action_button = QPushButton("清空")
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
        right = QGroupBox("预览")
        right_layout = QVBoxLayout(right)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("预览源："))
        self.preview_source_combo = NoFlickerComboBox()
        self.preview_source_combo.setMinimumWidth(160)
        src_row.addWidget(self.preview_source_combo, stretch=1)
        right_layout.addLayout(src_row)

        # Preview toolbar: zoom in / out, 1:1, fit-to-window, toggle original.
        # The buttons use an Ignored horizontal policy so they shrink (instead
        # of overflowing / clipping) when the panel is narrow (e.g. at the
        # default 6x3 window width).
        pbar = QHBoxLayout()
        self.zoom_in_button = QPushButton("放大")
        self.zoom_out_button = QPushButton("缩小")
        self.zoom_actual_button = QPushButton("1:1")
        self.zoom_fit_button = QPushButton("适应窗口")
        self.show_original_button = QPushButton("显示原图")
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
            "请先在「输入」标签添加图片，\n再在此处预览动作效果。"
        )
        self.preview_msg.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.preview_msg)

        hint = QLabel("预览为示意效果，可能与最终输出不完全一致。")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        right_layout.addWidget(hint)

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
        self.preview_source_combo.currentIndexChanged.connect(
            self._render_action_preview
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        self.output_scroll = scroll
        inner = QWidget()
        inner.setAutoFillBackground(False)
        layout = QVBoxLayout(inner)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("输出格式："))
        # NoFlickerComboBox：普通 QComboBox 行为（框更大、支持鼠标滚轮），
        # 但下拉弹窗去掉 Windows DWM 入场动画，避免展开时闪烁。
        self.format_combo = NoFlickerComboBox()
        self.format_combo.addItems(["JPEG XL (*.jxl)", "PNG (*.png)"])
        fmt_row.addWidget(self.format_combo)
        fmt_row.addStretch(1)
        layout.addLayout(fmt_row)

        # ---- JXL 编码参数（仅输出 JXL 时生效；输出 PNG 时由 Pillow 直存）----
        enc_group = QGroupBox("JXL 编码参数")
        enc_group.setAutoFillBackground(False)
        enc_layout = QVBoxLayout(enc_group)

        # 编码模式：有损 / 无损 / JPG 无损重编码（互斥单选）。
        # effort（--effort，1-9，默认 7）放在模式右侧，三种模式通用，
        # 给下方高级参数预留垂直空间。
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("编码模式："))
        self.lossy_radio = QRadioButton("有损")
        self.lossless_radio = QRadioButton("无损")
        self.lossless_jpeg_radio = QRadioButton("JPG 无损重编码 (--lossless_jpeg=1)")
        self.lossy_radio.setChecked(True)
        self.encode_mode_group = QButtonGroup(self)
        self.encode_mode_group.addButton(self.lossy_radio)
        self.encode_mode_group.addButton(self.lossless_radio)
        self.encode_mode_group.addButton(self.lossless_jpeg_radio)
        mode_row.addWidget(self.lossy_radio)
        mode_row.addWidget(self.lossless_radio)
        mode_row.addWidget(self.lossless_jpeg_radio)
        mode_row.addStretch(1)
        mode_row.addWidget(QLabel("速度/质量权衡 (--effort)："))
        self.effort_combo = NoFlickerComboBox()
        self.effort_combo.addItems([str(i) for i in range(1, 10)])
        self.effort_combo.setCurrentText("7")
        mode_row.addWidget(self.effort_combo)
        enc_layout.addLayout(mode_row)

        # 质量滑块（--quality，0-100，默认 90）：仅「有损」模式可用。
        # 右侧用 QSpinBox 显示数值，支持键盘输入与鼠标上下箭头微调。
        qual_row = QHBoxLayout()
        qual_row.addWidget(QLabel("质量 (--quality)："))
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

        dest_group = QGroupBox("输出位置")
        dest_group.setAutoFillBackground(False)
        dest_layout = QVBoxLayout(dest_group)
        self.same_folder_radio = QRadioButton("保持原文件夹")
        self.custom_folder_radio = QRadioButton("自定义文件夹")
        self.same_folder_radio.setChecked(True)
        self.dest_group = QButtonGroup(self)
        self.dest_group.addButton(self.same_folder_radio)
        self.dest_group.addButton(self.custom_folder_radio)
        dest_layout.addWidget(self.same_folder_radio)
        dest_layout.addWidget(self.custom_folder_radio)

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
            "选择或输入自定义输出文件夹，下拉可查看历史路径"
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
        self.browse_folder_button = QPushButton("浏览...")
        self.browse_folder_button.setEnabled(False)
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
        dest_layout.addLayout(custom_row)

        self.custom_folder_radio.toggled.connect(
            lambda checked: (
                self.custom_folder_edit.setEnabled(checked),
                self.custom_folder_dropdown.setEnabled(checked),
                self.browse_folder_button.setEnabled(checked),
            )
        )
        self.browse_folder_button.clicked.connect(self._on_browse_folder)
        self.custom_folder_edit.textChanged.connect(
            lambda _=None: self._on_custom_folder_changed()
        )
        layout.addWidget(dest_group)

        name_group = QGroupBox("文件名")
        name_group.setAutoFillBackground(False)
        name_layout = QVBoxLayout(name_group)
        self.keep_name_radio = QRadioButton("保持原文件名")
        self.add_suffix_radio = QRadioButton("添加后缀：")
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
        layout.addWidget(name_group)

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
        self.adv_group = QGroupBox("高级参数")
        adv_outer = QVBoxLayout(self.adv_group)

        # 折叠头：箭头按钮 + 「已设置 N 项」摘要 + 右侧「重置高级参数」按钮。
        header_row = QHBoxLayout()
        self.adv_toggle = QToolButton()
        self.adv_toggle.setArrowType(Qt.RightArrow)
        self.adv_toggle.setAutoRaise(True)
        self.adv_toggle.setFixedWidth(22)
        self.adv_toggle.clicked.connect(self._toggle_advanced)
        self.adv_summary = QLabel("已设置 0 项")
        self.reset_adv_button = QPushButton("重置高级参数")
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
            sb = QGroupBox(sg)
            sub_boxes[sg] = sb
            sub_layouts[sg] = QVBoxLayout(sb)
            r, c = sub_pos[sg]
            grid.addWidget(sb, r, c)

        self._adv_widgets = {}  # key -> (checkbox, value_widget_or_None, schema)
        for s in _ADVANCED_SCHEMA:
            row = QHBoxLayout()
            check = QCheckBox(s["label"])
            check.setChecked(False)
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
                    val_w.addItem(t, v)
                idx = val_w.findData(s["default"])
                if idx >= 0:
                    val_w.setCurrentIndex(idx)
                row.addWidget(val_w)
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
        self.custom_cmd_check = QCheckBox("自定义命令：")
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
        self.adv_summary.setText("已设置 %d 项" % n)

    def _update_cmd_preview(self, force=False):
        """根据当前控件状态刷新底部 cjxl 命令预览（输入/输出用占位符）。

        当「自定义命令」被勾选且非强制（force=False）时，不覆盖用户在
        输入框中已编辑的命令文本。force=True 用于在勾选瞬间预填当前生成的
        命令，方便用户在此基础上修改。
        """
        if self.custom_cmd_check.isChecked() and not force:
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
        """勾选「自定义命令」时在只读预览与可编辑自定义命令之间切换。"""
        if checked:
            # 勾选：预填当前生成的命令，方便用户在此基础上修改。
            self._update_cmd_preview(force=True)
            self.cmd_edit.setReadOnly(False)
            self.cmd_edit.selectAll()
            self.cmd_edit.setFocus()
        else:
            # 取消勾选：恢复只读，并重新同步为实时生成的预览。
            self.cmd_edit.setReadOnly(True)
            self._update_cmd_preview()
        self._save_jxl_output()

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

        layout.addWidget(QLabel("运行日志与环境信息："))
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
        # 同步成 Active 组规避（复用 _sync_radio_inactive_palette 的同一逻辑）。
        # 这里先调一次，保证构建阶段即生效，_apply_theme/系统主题切换时也会再调。
        self._sync_radio_inactive_palette()
        layout.addWidget(self.progress_bar)

        progress_row = QHBoxLayout()
        self.progress_label = QLabel("当前进度：0 / 0 文件")
        self.eta_label = QLabel("预计剩余：--")
        progress_row.addWidget(self.progress_label)
        progress_row.addStretch(1)
        progress_row.addWidget(self.eta_label)
        layout.addLayout(progress_row)
        return widget

    def _build_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        layout.addWidget(QLabel("窗口布局"))

        # Two layout helpers in a single compact row (native button height
        # instead of the oversized 40px ones).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_center = QPushButton("一键居中")
        btn_center.setToolTip("将窗口移动到屏幕中央（不改变窗口大小）")
        btn_center.clicked.connect(self._on_center_window)
        btn_fit = QPushButton("一键 6×3 排版")
        btn_fit.setToolTip("将窗口恢复为默认的 6 列 × 3 行尺寸（不改变位置）")
        btn_fit.clicked.connect(self._on_fit_window)
        btn_row.addWidget(btn_center)
        btn_row.addWidget(btn_fit)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        hint = QLabel("窗口的大小与位置会自动保存，下次打开时原样恢复。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(QLabel("主题"))

        # Theme choice: 原生（无闪烁）(default) / 原生 / Fusion. Persisted to
        # QSettings and applied globally (see _init_theme / _apply_theme).
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_label = QLabel("界面主题")
        theme_tip = (
            "原生（无闪烁）：大部分界面保持系统原生外观，仅会闪烁的下拉菜单"
            "单独使用 Fusion 样式以消除 Windows 弹出动画闪烁（默认）。\n"
            "原生：完全使用系统原生外观，下拉菜单可能出现轻微闪烁。\n"
            "Fusion：整套界面使用 Qt 自带的 Fusion 样式。"
        )
        theme_label.setToolTip(theme_tip)
        theme_row.addWidget(theme_label)
        self.theme_combo = NoFlickerComboBox()
        self.theme_combo.setToolTip(theme_tip)
        for key in _THEME_ORDER:
            self.theme_combo.addItem(_THEME_LABELS[key], key)
        self._set_combo_min_width(self.theme_combo)
        self._theme_loading = True
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(app_theme()))
        self._theme_loading = False
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        layout.addWidget(QLabel("转换进程"))

        # The CPU-priority explanation is surfaced as a native tooltip on the
        # row (the same box that appears when hovering a thumbnail): a small
        # box pops up while the pointer rests on the control and disappears on
        # move-away — no layout shift, and it matches the rest of the UI.
        cpu_tip = (
            "设置 cjxl / djxl 转换进程的 CPU 优先级（默认低于正常，"
            "减少对前台操作的影响）。"
        )

        cpu_row = QHBoxLayout()
        cpu_row.setSpacing(8)
        cpu_label = QLabel("CPU 优先级")
        cpu_label.setToolTip(cpu_tip)
        cpu_row.addWidget(cpu_label)
        priorities = (
            ("idle", "空闲"),
            ("below_normal", "低于正常"),
            ("normal", "正常"),
            ("above_normal", "高于正常"),
            ("high", "高"),
        )
        self.cpu_priority_combo = NoFlickerComboBox()
        self.cpu_priority_combo.setToolTip(cpu_tip)
        for key, label in priorities:
            self.cpu_priority_combo.addItem(label, key)
        # Size the combo to fit the widest label under its current style
        # (native vs Fusion have different arrow / frame margins), and keep
        # it wide enough after a theme switch by recomputing in _apply_theme.
        self._set_combo_min_width(self.cpu_priority_combo)
        self.cpu_priority_combo.setCurrentIndex(
            self.cpu_priority_combo.findData(converter.DEFAULT_PRIORITY)
        )
        self.cpu_priority_combo.currentIndexChanged.connect(
            self._on_cpu_priority_changed
        )
        cpu_row.addWidget(self.cpu_priority_combo)
        cpu_row.addStretch(1)
        layout.addLayout(cpu_row)

        # ---- CPU 核心使用数（转换并行度） ----
        cores_tip = (
            "转换时并行使用的 CPU 核心数，决定同时转换的文件数（多文件时）"
            "或单个大文件的线程数（单文件时）。「自动」等于本机逻辑核心数。"
        )
        cores_row = QHBoxLayout()
        cores_row.setSpacing(8)
        cores_label = QLabel("CPU 核心使用数")
        cores_label.setToolTip(cores_tip)
        cores_row.addWidget(cores_label)
        self.cpu_cores_combo = NoFlickerComboBox()
        self.cpu_cores_combo.setToolTip(cores_tip)
        self.cpu_cores_combo.addItem("自动", "auto")
        max_cores = os.cpu_count() or 1
        for n in range(1, max_cores + 1):
            self.cpu_cores_combo.addItem(str(n), n)
        # 按当前样式计算最小宽度（原生/Fusion 箭头与边框边距不同），并在切换
        # 主题时由 _apply_theme 重新计算，避免原生主题下截断。
        self._set_combo_min_width(self.cpu_cores_combo)
        self.cpu_cores_combo.setCurrentIndex(self.cpu_cores_combo.findData("auto"))
        self.cpu_cores_combo.currentIndexChanged.connect(self._on_cpu_cores_changed)
        cores_row.addWidget(self.cpu_cores_combo)
        cores_row.addStretch(1)
        layout.addLayout(cores_row)

        # ---- 启用高级参数（手动设置每文件线程数） ----
        adv_threads_tip = (
            "未启用：每文件线程数（--num_threads）由「CPU 核心使用数」自动分配，"
            "并行池自动控核；\n输出页「速度/质量权衡 (--effort)」仅可选 1–9。启用后"
            "可手动设置每文件线程数，并行进程数 = 核心数 ÷ 每文件线程数，并解锁 effort 第 10 档。"
        )
        self.adv_threads_toggle = QCheckBox(
            "启用高级参数"
        )
        self.adv_threads_toggle.setToolTip(adv_threads_tip)
        self.adv_threads_toggle.setChecked(False)
        self.adv_threads_toggle.toggled.connect(self._on_adv_threads_toggled)
        layout.addWidget(self.adv_threads_toggle)

        layout.addStretch(1)
        return widget

    def _on_cpu_priority_changed(self, _index):
        """Persist the CPU-priority choice whenever the user changes it."""
        self._save_conversion_settings()

    def _on_cpu_cores_changed(self, _index):
        """Persist the CPU-core-count choice whenever the user changes it."""
        self._save_conversion_settings()

    def _on_adv_threads_toggled(self, _checked):
        """开关联动：启用/禁用高级「线程数 (--num_threads)」行，扩展/收窄输出页
        effort 可选范围（启用→1..10，禁用→1..9），更新提示文字，并持久化。"""
        enabled = self.adv_threads_toggle.isChecked()
        self._apply_adv_threads_state(enabled)
        self._save_conversion_settings()
        # 加载阶段（_conversion_loading / _jxl_loading）不持久化、不刷新预览，
        # 否则会用「尚未恢复的默认状态」覆盖刚从 QSettings 读取、待恢复的值。
        if getattr(self, "_conversion_loading", False) or getattr(self, "_jxl_loading", False):
            return
        # effort 范围与当前选择可能随开关变化（禁用时若原为 10 会被夹到 9），
        # 需同步持久化并刷新命令预览。
        self._save_jxl_output()
        self._update_cmd_preview()

    def _apply_adv_threads_state(self, enabled):
        """根据「启用高级参数」开关，启用/禁用高级参数里的 num_threads 行，
        扩展/收窄输出页 effort 可选范围（启用→1..10，禁用→1..9），并更新
        设置页提示文字。相关控件未构建时（输出页/设置页晚于本调用）安全跳过。"""
        adv_widgets = getattr(self, "_adv_widgets", {})
        entry = adv_widgets.get("num_threads")
        if entry is not None:
            check, val_w, _ = entry
            check.setEnabled(enabled)
            if val_w is not None:
                val_w.setEnabled(enabled and check.isChecked())
        # 启用高级参数后，输出页 effort 可选范围扩展到 1..10；否则仅 1..9。
        self._set_effort_range(enabled)
        # 说明文字不再作为独立可见小字（部分主题下 palette(mid) 颜色异常），
        # 改为并入开关的悬停浮窗，跟随主题原生 tooltip 配色。
        if hasattr(self, "adv_threads_toggle"):
            if enabled:
                self.adv_threads_toggle.setToolTip(
                    "已启用：可手动设置每文件线程数（--num_threads），并行进程数 "
                    "= 核心数 ÷ 每文件线程数；\n同时输出页「速度/质量权衡 (--effort)」"
                    "解锁第 10 档（最慢、质量最高）。"
                )
            else:
                self.adv_threads_toggle.setToolTip(
                    "未启用：每文件线程数（--num_threads）由「CPU 核心使用数」自动"
                    "分配，并行池自动控核；\n输出页「速度/质量权衡 (--effort)」仅可选 "
                    "1–9，启用高级参数后可选用第 10 档（最慢、质量最高）。"
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
        self.statusBar().showMessage("窗口已居中")

    def _on_fit_window(self):
        """Resize to the default 6x3 startup size (keep position)."""
        self._fit_window_to_grid(center=False)
        self._save_geometry()
        self.statusBar().showMessage("已恢复 6×3 默认尺寸")

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
        self._save_advanced(settings)
        # 自定义命令：勾选状态 + 已编辑的命令文本。
        settings.setValue("custom_cmd_on", self.custom_cmd_check.isChecked())
        settings.setValue("custom_cmd_text", self.cmd_edit.text())
        settings.endGroup()

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
        added = []
        for url in urls:
            if url.isLocalFile():
                path = os.path.normpath(url.toLocalFile())
                if os.path.isfile(path):
                    added.append(path)
                elif os.path.isdir(path):
                    added.extend(self._collect_images_from_folder(path))
        if added:
            self.tabs.setCurrentWidget(self.input_tab)
            before = len(self.input_files)
            self._add_input_paths(added)
            self.log_edit.appendPlainText(
                "通过拖拽添加了 %d 个文件。" % (len(self.input_files) - before)
            )
        else:
            self.log_edit.appendPlainText("拖拽内容中没有可添加的文件。")
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
            self, "选择输入文件", "",
            "图像文件 (*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp *.ppm *.pgm *.jxl *.avif);;所有文件 (*.*)",
        )
        self._add_input_paths(paths)

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        added = []
        for name in sorted(os.listdir(folder)):
            ext = os.path.splitext(name)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                added.append(os.path.join(folder, name))
        self._add_input_paths(added)
        if not added:
            self.statusBar().showMessage("该文件夹内未发现支持的图像文件")

    def _add_input_paths(self, paths):
        now = time.time()
        for path in paths:
            if path and path not in self.input_files:
                self.input_files.append(path)
                self._table_added[path] = now
                self._file_meta.pop(path, None)  # recompute on next paint
        self._refresh_input_views()
        self.statusBar().showMessage("已添加 %d 个文件" % len(self.input_files))

    def _refresh_list(self):
        mode = getattr(self, "_last_view", "缩略图")
        is_icon = mode in THUMB_SIZES
        icon_size = THUMB_SIZES.get(mode, QSize(96, 96))
        # Cancel any in-flight thumbnail batch from a previous refresh so a rapid
        # view switch / reorder never lets stale batches repaint dead items.
        if getattr(self, "_thumb_timer", None) is not None:
            self._thumb_timer.stop()
            self._thumb_timer = None
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
        self.input_list.clear()
        for path in self.input_files:
            name = os.path.basename(path)
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            if is_icon:
                # Defer the (slow) per-image work: show a gray placeholder and a
                # bare tooltip immediately, then fill thumbnails + info in small
                # batches on a timer so the first switch to a thumbnail mode
                # stays responsive instead of blocking on a synchronous decode
                # of every image.
                item.setData(Qt.DecorationRole, placeholder)
                self._thumb_queue.append((item, path, box_square))
            else:
                tip = self._info_cache.get(path)
                if tip is None:
                    tip = self._image_info(path)
                    self._info_cache[path] = tip
                item.setToolTip(tip)
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
        """Decode a few thumbnails (and lazily compute their tooltips) per timer
        tick. Spreading the work keeps the UI interactive during the first
        switch to a thumbnail view with hundreds of images."""
        queue = getattr(self, "_thumb_queue", None)
        if not queue:
            if getattr(self, "_thumb_timer", None) is not None:
                self._thumb_timer.stop()
                self._thumb_timer = None
            return
        batch = queue[:THUMB_BATCH]
        self._thumb_queue = queue[THUMB_BATCH:]
        for item, path, box_square in batch:
            tip = self._info_cache.get(path)
            if tip is None:
                tip = self._image_info(path)
                self._info_cache[path] = tip
            item.setToolTip(tip)
            item.setData(Qt.DecorationRole, self._make_thumbnail(path, box_square))
        if not self._thumb_queue:
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
            [label for (_k, label, _v, _w, _a) in TABLE_COLUMNS]
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
        w = h = area = 0
        res_text = ratio_text = "-"
        ratio_value = 0.0
        reader = QImageReader(path)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            w, h = size.width(), size.height()
            area = w * h
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
        menu.addAction("显示列：").setEnabled(False)
        for key, label, _v, _w, _a in TABLE_COLUMNS:
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(key not in self._table_hidden)
            act.triggered.connect(
                lambda _checked=False, k=key: self._toggle_table_column(k)
            )
            menu.addAction(act)
        menu.addSeparator()
        reset_act = QAction("重置列设置", menu)
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
        self._refresh_table()
        self._refresh_list()
        # 输入集合变化会影响命令预览里的 --lossless_jpeg=0（有损 + JPG 时），
        # 这里统一刷新一次，使预览与实际命令保持同步。
        self._update_cmd_preview()

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
        """Return a *square* thumbnail ``QPixmap`` (letterboxed) so every item
        box keeps the same aspect ratio regardless of the source proportions.

        The pixmap is rendered at ``size * dpr`` device pixels. The delegate
        draws it 1:1 (it reads the same ``view.devicePixelRatio()``), so it is
        sharp on HiDPI screens and never re-rendered. Results are cached per
        (path, size) so reordering / refreshing hundreds of files does not
        re-decode every image from disk each time.

        Decoding is done straight to the thumbnail size via ``QImageReader``
        (with the source aspect ratio preserved through letterboxing). For large
        source images this is dramatically faster than loading the full bitmap
        and then scaling it down.
        """
        key = (path, size)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        dpr = self._thumb_dpr()
        px = max(1, int(round(size * dpr)))
        thumb = QPixmap(px, px)
        thumb.fill(THUMB_PLACEHOLDER_BG)

        try:
            display = _display_path(path)
            if display is None:
                # JXL decode failed (or not a loadable image at all): keep the
                # gray placeholder rather than crashing on a null pixmap.
                self._thumb_cache[key] = thumb
                return thumb
            reader = QImageReader(display)
            src_size = reader.size()
            if src_size.isValid() and not src_size.isNull():
                sw, sh = src_size.width(), src_size.height()
                scale = min(px / sw, px / sh)
                fw = max(1, int(round(sw * scale)))
                fh = max(1, int(round(sh * scale)))
                reader.setScaledSize(QSize(fw, fh))
                img = reader.read()
                if not img.isNull():
                    painter = QPainter(thumb)
                    painter.drawImage((px - fw) // 2, (px - fh) // 2, img)
                    painter.end()
                    self._thumb_cache[key] = thumb
                    return thumb
        except Exception:
            pass

        # Fallback: decode the full pixmap and scale it down.
        display = _display_path(path)
        if display is None:
            self._thumb_cache[key] = thumb
            return thumb
        src = QPixmap(display)
        if src.isNull():
            self._thumb_cache[key] = thumb
            return thumb
        scaled = src.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(thumb)
        x = (px - scaled.width()) // 2
        y = (px - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        self._thumb_cache[key] = thumb
        return thumb

    def _image_info(self, path):
        name = os.path.basename(path)
        is_jxl = path.lower().endswith(".jxl")
        ext = "JXL" if is_jxl else (os.path.splitext(path)[1].lstrip(".").upper() or "未知")
        size_text = self._format_size(path)
        dims = "未知"
        display = _display_path(path)
        if display is not None:
            reader = QImageReader(display)
            if reader.canRead():
                s = reader.size()
                if s.isValid():
                    dims = "%d x %d" % (s.width(), s.height())
        return "文件名：%s\n格式：%s\n尺寸：%s\n大小：%s\n路径：%s" % (
            name, ext, dims, size_text, path,
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
            self.view_button.setText(text)
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
        self.statusBar().showMessage("已调整顺序，共 %d 个文件" % len(self.input_files))

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
        self.statusBar().showMessage("已调整顺序，共 %d 个文件" % len(self.input_files))

    def _on_remove_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        self.input_files = [p for p in self.input_files if p not in paths]
        for p in paths:
            self._file_meta.pop(p, None)
            self._table_added.pop(p, None)
        self._refresh_input_views()
        self.statusBar().showMessage("已移除，剩余 %d 个文件" % len(self.input_files))

    def _on_clear_inputs(self):
        self.input_files.clear()
        self._file_meta.clear()
        self._table_added.clear()
        self._refresh_input_views()
        self.statusBar().showMessage("已清空输入列表")

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
            "已移除 %d 个文件，剩余 %d 个" % (removed, len(self.input_files))
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
    def _on_add_action(self):
        name = self.action_combo.currentText()
        dialog = ActionParamDialog(name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        params = dialog.get_params()
        action = {"type": name, "params": params}
        item = QListWidgetItem()
        item.setData(Qt.UserRole, action)
        widget = ActionItemWidget()
        widget.summary_label.setText(self._action_summary(action))
        item.setSizeHint(widget.sizeHint())
        self.action_list.addItem(item)
        self.action_list.setItemWidget(item, widget)
        widget.item = item
        widget.up_button.clicked.connect(
            lambda: self._on_move_action_for(item, -1))
        widget.down_button.clicked.connect(
            lambda: self._on_move_action_for(item, 1))
        widget.remove_button.clicked.connect(
            lambda: self._on_remove_action_for(item))
        self.statusBar().showMessage("已添加动作：%s" % self._action_summary(action))
        self._render_action_preview()

    def _action_summary(self, action):
        """Short human-readable summary of an action (shown in the list)."""
        atype = action.get("type", "")
        p = action.get("params", {}) or {}
        if atype == "调整大小":
            w, h = int(p.get("width", 0) or 0), int(p.get("height", 0) or 0)
            if w and h:
                return "%s (%dx%d)" % (atype, w, h)
            if w:
                return "%s (宽%d)" % (atype, w)
            if h:
                return "%s (高%d)" % (atype, h)
            return atype
        if atype == "旋转":
            return "%s (%d°)" % (atype, int(p.get("angle", 0) or 0))
        if atype == "水印":
            return "%s (%s)" % (atype, p.get("text", ""))
        if atype == "亮度/对比度":
            return "%s (亮%.1f/对%.1f)" % (
                atype, float(p.get("brightness", 1.0)), float(p.get("contrast", 1.0)))
        if atype == "锐化":
            return "%s (%.1f)" % (atype, float(p.get("factor", 1.0)))
        if atype == "裁剪":
            return "%s (%d,%d %dx%d)" % (
                atype, int(p.get("left", 0)), int(p.get("top", 0)),
                int(p.get("width", 0)), int(p.get("height", 0)))
        return atype

    def _collect_actions(self):
        """Return the ordered list of action dicts from the action list."""
        actions = []
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            data = item.data(Qt.UserRole)
            if isinstance(data, dict) and "type" in data:
                actions.append(data)
        return actions

    def _on_remove_action_for(self, item):
        """Remove a specific action item (used by each item's 移除 button)."""
        row = self.action_list.row(item)
        if row < 0:
            return
        self.action_list.takeItem(row)
        self._render_action_preview()

    def _on_move_action_for(self, item, delta):
        """Move a specific action item up/down (used by each item's buttons)."""
        row = self.action_list.row(item)
        if row < 0:
            return
        new_row = row + delta
        if 0 <= new_row < self.action_list.count():
            widget = self.action_list.itemWidget(item)
            self.action_list.takeItem(row)
            self.action_list.insertItem(new_row, item)
            if widget is not None:
                self.action_list.setItemWidget(item, widget)
            self._render_action_preview()

    def _on_clear_actions(self):
        self.action_list.clear()
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
            self.action_list.takeItem(r)
        self._render_action_preview()

    # ------------------------------------------------------------------
    # Actions-tab live preview
    # ------------------------------------------------------------------
    PREVIEW_MAX_SIDE = 1400  # cap the preview source so rendering stays snappy

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

    def _render_action_preview(self):
        self._refresh_preview_sources()
        path = self._current_preview_source()
        if path is None:
            self.preview_view.setVisible(False)
            self.preview_msg.setVisible(True)
            self._preview_original_pixmap = None
            self._preview_processed_pixmap = None
            return
        try:
            from PIL import Image
            from . import processor
            img = Image.open(path)
            img.load()
            w, h = img.size
            if max(w, h) > self.PREVIEW_MAX_SIDE:
                scale = self.PREVIEW_MAX_SIDE / float(max(w, h))
                img = img.resize(
                    (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                    Image.LANCZOS,
                )
            # Keep both the original and the processed result so the 显示原图
            # button can switch between them without re-decoding from disk.
            self._preview_original_pixmap = QPixmap.fromImage(
                self._pil_to_qimage(img)
            )
            actions = self._collect_actions()
            if actions:
                processed = processor.apply_actions(img.copy(), actions)
                self._preview_processed_pixmap = QPixmap.fromImage(
                    self._pil_to_qimage(processed)
                )
            else:
                self._preview_processed_pixmap = self._preview_original_pixmap
            self._apply_preview_pixmap()
            self.preview_view.setVisible(True)
            self.preview_msg.setVisible(False)
        except Exception as exc:  # noqa: BLE001 - surface any preview failure
            self.preview_view.setVisible(False)
            self.preview_msg.setText("预览失败：%s" % exc)
            self.preview_msg.setVisible(True)

    def _apply_preview_pixmap(self):
        """Show the pixmap for the current mode (processed / original)."""
        pix = (
            self._preview_original_pixmap
            if self._preview_mode == "original"
            else self._preview_processed_pixmap
        )
        if pix is not None:
            self.preview_view.set_pixmap(pix)

    def _preview_show_original(self):
        """Press-and-hold peek: show the un-processed source image."""
        if self._preview_original_pixmap is None:
            return
        self._preview_mode = "original"
        self._apply_preview_pixmap()

    def _preview_show_processed(self):
        """Release: return to the processed (action-applied) preview."""
        if self._preview_processed_pixmap is None:
            return
        self._preview_mode = "processed"
        self._apply_preview_pixmap()

    def _preview_actual(self):
        self.preview_view.zoom_to_actual()

    # ------------------------------------------------------------------
    # Output tab slots
    # ------------------------------------------------------------------
    def _on_browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
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
            empty = QAction("(无历史记录)", self.folder_menu)
            empty.setEnabled(False)
            self.folder_menu.addAction(empty)
        self._save_output_settings()

    def _rebuild_folder_menu(self):
        """Rebuild the custom-folder history menu from self._folder_history.
        Each row is a HistoryRowWidget (clickable path + per-row delete)."""
        self.folder_menu.clear()
        if not getattr(self, "_folder_history", []):
            empty = QAction("(无历史记录)", self.folder_menu)
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

    def _sync_radio_inactive_palette(self):
        """Keep every radio button's Inactive palette group equal to its Active
        group, so a selected radio does not dim to gray when the window loses
        focus (Qt otherwise draws inactive controls with the QPalette.Inactive
        palette). Re-derived from the live application palette so it also tracks
        theme switches."""
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
            self.folder_menu.setPalette(QApplication.palette())
            if self.folder_menu.isVisible():
                self._rebuild_folder_menu()
            self._sync_radio_inactive_palette()
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
        settings.endGroup()
        if not isinstance(key, str) or self.cpu_priority_combo.findData(key) < 0:
            key = converter.DEFAULT_PRIORITY
        self.cpu_priority_combo.setCurrentIndex(
            self.cpu_priority_combo.findData(key)
        )
        if self.cpu_cores_combo.findData(cores) < 0:
            cores = "auto"
        self.cpu_cores_combo.setCurrentIndex(self.cpu_cores_combo.findData(cores))
        self.adv_threads_toggle.setChecked(bool(adv_enabled))
        # 高级参数 num_threads 行受开关控制；此时输出页已构建，可安全联动。
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
        for cb in self.findChildren(NoFlickerComboBox):
            cb._apply_fusion_style()
        self._apply_theme_to_folder_menu()
        # Native vs Fusion styles have different arrow / frame margins, so the
        # explicitly-sized combos need their minimum width recalculated.
        for combo in (getattr(self, "theme_combo", None),
                      getattr(self, "cpu_priority_combo", None),
                      getattr(self, "cpu_cores_combo", None),
                      getattr(self, "effort_combo", None)):
            if combo is not None:
                self._set_combo_min_width(combo)
        self._sync_radio_inactive_palette()
        self.statusBar().showMessage(
            "主题已切换为：%s" % _THEME_LABELS.get(theme, theme))

    def _set_combo_min_width(self, combo):
        """Set the combo's minimum width to fit its widest item under the
        current style. This is needed because the native Windows style's arrow
        button and frame padding are wider than Fusion's, so a width measured
        for Fusion will truncate text when the user switches to native."""
        combo.updateGeometry()
        combo.setMinimumWidth(combo.sizeHint().width() + 8)

    def _apply_theme_to_folder_menu(self):
        """Style the custom-folder history popup. It uses Fusion only when the
        active theme wants dropdowns Fusion-styled; otherwise it follows the
        application-wide (native) style."""
        if not hasattr(self, "folder_menu"):
            return
        if dropdowns_use_fusion():
            fusion = _fusion_style()
            if fusion is not None:
                self.folder_menu.setStyle(fusion)
        else:
            self.folder_menu.setStyle(QApplication.style())

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
        # Only the 有损 mode exposes the --quality control. In the other two
        # modes we disable it (greyed) but KEEP its displayed value, so that
        # returning to 有损 reuses the last setting. Effort stays enabled for
        # all modes.
        is_lossy = self.lossy_radio.isChecked()
        self.quality_slider.setEnabled(is_lossy)
        self.quality_spin.setEnabled(is_lossy)
        # 高级参数：按当前编码模式启用/禁用各旋钮（不可用的自动置灰，且不参与转换）。
        mode = self._current_encode_mode()
        for s in _ADVANCED_SCHEMA:
            check, val_w, _ = self._adv_widgets[s["key"]]
            enabled = mode in s["modes"]
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
            self.statusBar().showMessage("错误：请先在「输入」中添加文件")
            self.log_edit.appendPlainText("错误：输入列表为空。")
            return

        tools = converter.check_tools()
        if not tools["cjxl"] or not tools["djxl"]:
            missing = []
            if not tools["cjxl"]:
                missing.append("cjxl")
            if not tools["djxl"]:
                missing.append("djxl")
            msg = (
                "未检测到 libjxl 命令行工具（%s），无法执行转换。\n\n"
                "请确认 libjxl 已正确安装，并将其所在目录加入系统的 PATH 环境变量，"
                "然后重新启动本程序。" % "、".join(missing)
            )
            self.statusBar().showMessage("错误：cjxl / djxl 未就绪")
            self.log_edit.appendPlainText(
                "错误：%s 未就绪，请确认 libjxl 已安装并加入 PATH。"
                % " 与 ".join(missing)
            )
            QMessageBox.warning(self, "libjxl 未就绪", msg)
            return

        actions = self._collect_actions()
        if actions and not processor.AVAILABLE:
            self.statusBar().showMessage("错误：Pillow 未安装，无法执行动作")
            self.log_edit.appendPlainText(
                "错误：未检测到 Pillow 库，无法执行图像处理动作。"
                "请用命令 `pip install Pillow` 安装后重试。"
            )
            return

        # Remember a valid custom output folder (typed or picked): record it into
        # the history dropdown and persist, so it's recalled next launch.
        if self.custom_folder_radio.isChecked():
            cf = self.custom_folder_edit.text().strip()
            if cf and os.path.isdir(cf):
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
                self.statusBar().showMessage("错误：自定义命令为空")
                self.log_edit.appendPlainText("错误：自定义命令已勾选但内容为空。")
                return
            if "<输入>" not in raw or "<输出>" not in raw:
                QMessageBox.warning(
                    self, "自定义命令格式",
                    "自定义命令必须同时包含 <输入> 和 <输出> 占位符"
                    "（会被替换为每个文件的真实路径）。"
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
        for src in self.input_files:
            if mode == "lossless_jpeg" and not _is_jpeg(src):
                skipped.append(src)
                continue
            out_path = self._build_output_path(src)
            out_is_jxl = out_path.lower().endswith(".jxl")
            jobs.append((src, out_path, out_is_jxl))

        # JPG 无损重编码模式：把被跳过的非 JPG 文件在状态中提示出来。
        if skipped:
            self.statusBar().showMessage(
                "已跳过 %d 个非 JPG 文件（JPG 无损重编码仅处理 JPG）" % len(skipped)
            )
            self.log_edit.appendPlainText(
                "提示：JPG 无损重编码模式仅支持 JPG 输入，以下 %d 个非 JPG 文件已跳过："
                % len(skipped)
            )
            for s in skipped:
                self.log_edit.appendPlainText("    - %s" % s)
        if not jobs:
            self.statusBar().showMessage("没有可处理的 JPG 文件，转换未开始")
            self.log_edit.appendPlainText(
                "错误：当前没有可处理的 JPG 文件，转换未开始。"
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
        self._stop_requested = False
        # Jump to the 状态 tab so the user can watch progress live.
        self.tabs.setCurrentWidget(self.status_tab)
        self._convert_worker = ConvertWorker(
            jobs, actions, effort, distance, quality_arg, lossless_jpeg,
            self.cpu_priority_combo.currentData(), advanced=adv,
            custom_cmd=custom_cmd,
            cpu_cores=self.cpu_cores_combo.currentData(),
            adv_threads_enabled=self.adv_threads_toggle.isChecked(),
        )
        self._convert_worker.log_signal.connect(self.log_edit.appendPlainText)
        self._convert_worker.status_signal.connect(self.statusBar().showMessage)
        self._convert_worker.finished_signal.connect(self._on_convert_finished)
        self._convert_worker.progress_signal.connect(self._on_progress_update)
        # 重置进度条与文案，并记录起点用于预计剩余时间
        self.progress_bar.setMaximum(max(1, len(jobs)))
        self.progress_bar.setValue(0)
        self.progress_label.setText("当前进度：0 / %d 文件" % len(jobs))
        self.eta_label.setText("预计剩余：--")
        self._convert_start_time = time.time()
        self._convert_worker.start()

    def _on_convert_finished(self):
        worker = self._convert_worker
        stopped = self._stop_requested
        now = time.time()
        log = self.log_edit.appendPlainText

        # 汇总块
        log("")
        log("已输入文件： %d" % worker._stat_processed)
        log("已输出文件： %d" % worker._stat_ok)
        log("错误： %d" % worker._stat_err)
        log("")
        log("输入文件总大小： %s" % _format_bytes(worker._stat_in_bytes))
        log("输出文件总大小： %s" % _format_bytes(worker._stat_out_bytes))
        if worker._stat_in_bytes > 0:
            ratio = (worker._stat_out_bytes - worker._stat_in_bytes) \
                / worker._stat_in_bytes * 100.0
            log("文件大小比例： %s" % ("%+d%%" % round(ratio)))
        else:
            log("文件大小比例： --")
        log("")
        duration = now - worker._stat_started
        if duration < 1:
            log("总持续时间： 不到 1 秒")
        else:
            log("总持续时间： %d 秒" % int(round(duration)))
        log("")
        if stopped:
            log("转换停止: " + _format_datetime(now))
            self.statusBar().showMessage("转换已停止")
            self._stop_requested = False
        else:
            log("转换完成: " + _format_datetime(now))
            self.statusBar().showMessage(
                "转换完成：%d 个文件" % worker._stat_ok
            )

        # 进度条收尾：停在已处理数（正常完成=总数，中止=部分），清除预计剩余。
        self.progress_bar.setValue(worker._stat_processed)
        self.eta_label.setText("预计剩余：--")

        self.convert_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if worker is not None:
            worker.deleteLater()
            self._convert_worker = None

    def _on_progress_update(self, processed, total):
        """Update the progress bar, current count, and ETA from worker progress."""
        if total <= 0:
            return
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(processed)
        self.progress_label.setText(
            "当前进度：%d / %d 文件" % (processed, total)
        )
        start = getattr(self, "_convert_start_time", None)
        elapsed = time.time() - start if start else 0.0
        if processed > 0 and elapsed > 0:
            avg_per_file = elapsed / processed
            remaining = total - processed
            eta = avg_per_file * remaining
            self.eta_label.setText("预计剩余：%s" % _format_duration(eta))
        else:
            self.eta_label.setText("预计剩余：--")

    def _on_convert_stop(self):
        if self._convert_worker is None or not self._convert_worker.isRunning():
            return
        self._stop_requested = True
        self.stop_button.setEnabled(False)
        self.log_edit.appendPlainText("正在停止……（当前文件处理完毕后中止）")
        self._convert_worker.request_stop()

    def _build_output_path(self, src):
        base, _ = os.path.splitext(src)
        lower = self.format_combo.currentText().lower()
        out_ext = ".png" if "png" in lower else ".jxl"

        if self.custom_folder_radio.isChecked():
            folder = self.custom_folder_edit.text().strip()
            if folder and os.path.isdir(folder):
                base = os.path.join(folder, os.path.splitext(os.path.basename(src))[0])

        if self.add_suffix_radio.isChecked():
            suffix = self.suffix_edit.text().strip()
            if suffix:
                base = base + suffix

        return base + out_ext


    # ------------------------------------------------------------------
    # Environment detection (written into the 状态 tab log)
    # ------------------------------------------------------------------
    def _refresh_environment(self):
        os_id = converter.detect_os()
        tools = converter.check_tools()
        cjxl_state = "已找到" if tools["cjxl"] else "未找到"
        djxl_state = "已找到" if tools["djxl"] else "未找到"
        self.log_edit.appendPlainText(
            "检测到操作系统：%s | cjxl：%s | djxl：%s"
            % (os_id, cjxl_state, djxl_state)
        )
        if tools["cjxl"]:
            version = converter.get_cjxl_version()
            if version:
                self.log_edit.appendPlainText(version)
        if not tools["cjxl"] or not tools["djxl"]:
            self.statusBar().showMessage("提示：cjxl / djxl 未完全就绪")
        else:
            self.statusBar().showMessage("环境就绪：cjxl 与 djxl 均可用")


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
                 custom_cmd=None, cpu_cores="auto", adv_threads_enabled=False):
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
        # 由 _resolve_concurrency 在 run() 开头计算；_encode_kwargs 用它统一覆盖
        # 高级参数里的 num_threads，避免与文件级并行叠加导致超订。
        self._per_file_threads = None
        self._total = 0
        self._stopped = False

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

    def _resolve_concurrency(self, n_jobs):
        """把「CPU 核心使用数」解析为 (cores, per_file_threads, pool_size)。

        - 多文件：同时跑 pool_size 个 cjxl 进程；每个进程 --num_threads=1（或
          高级开关打开时为用户设定值），总核占用 ≈ 设定核心数，不会超订。
        - 单文件：不开多进程，直接把全部核心交给这一个 cjxl（--num_threads=cores），
          否则单文件只用 1 核太浪费。
        """
        cores = self.cpu_cores
        if not isinstance(cores, int) or cores < 1:
            cores = os.cpu_count() or 1
        adv_num = None
        if self.adv_threads_enabled:
            adv_num = self.advanced.get("num_threads")
            if not isinstance(adv_num, int) or adv_num < 1:
                adv_num = None
        if n_jobs <= 1:
            per_file = adv_num if adv_num else cores
            pool_size = 1
        else:
            per_file = adv_num if adv_num else 1
            if per_file and per_file > 0:
                pool_size = max(1, min(n_jobs, cores // per_file))
            else:
                pool_size = n_jobs
        return cores, per_file, pool_size

    def _encode_tag(self):
        """Bracketed, human-readable description of how files are encoded.

        Mirrors the 状态 tab per-file line, e.g. '[Modular, lossless]' for the
        lossless mode, '[VarDCT, q90]' for lossy, '[JPEG lossless]' for the JPG
        re-encode mode. The conversion parameters are identical for every job,
        so this is computed once before the loop.
        """
        # 自定义命令模式：统一标记为 [自定义命令]。
        if self.custom_cmd:
            return "[自定义命令]"
        # 高级参数 -d 会覆盖基础 distance，两者取其一。
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
            ok, msg = converter.decode(src, tmp_src, priority=self.priority)
            if not ok:
                return False, "djxl 解码失败：%s" % msg, ""
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
        return True, "已保存为 PNG。", ""

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
            return False, "自定义命令解析失败：%s" % exc, ""
        if not tokens:
            return False, "自定义命令为空。", ""
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
                return False, "输出 PNG 需要 Pillow 支持（请先安装 Pillow）", ""
            try:
                from PIL import Image
                img = Image.open(src)
                icc = img.info.get("icc_profile")
                if icc:
                    img.save(out_path, "PNG", icc_profile=icc)
                else:
                    img.save(out_path, "PNG")
                return True, "已保存为 PNG（Pillow 解码）。", ""
            except Exception as exc:
                detail = str(exc).replace(chr(92) + chr(92), chr(92))
                return False, "Pillow 解码失败：%s" % detail, ""
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
            return False, "cjxl 无法读取该输入格式，且 Pillow 解码失败：%s" % detail, ""
        ok2, msg2, tag2 = converter.encode(tmp_png, out_path, **self._encode_kwargs())
        if ok2:
            return True, "通过 Pillow 兼容解码（输入格式 cjxl 不支持）后编码完成。", tag2
        return False, msg2, tag2

    def run(self):
        """Run the conversion as a bounded pool of concurrent cjxl/djxl processes.

        Each job runs in its own worker thread (via ThreadPoolExecutor), so several
        files convert in parallel. The GIL is released during the subprocess
        ``communicate()`` wait, so the cjxl/djxl processes truly overlap. The
        number of concurrent processes (``pool_size``) and the per-file thread
        count (``per_file_threads``) are derived from the user's "CPU 核心使用数"
        setting; together they keep total CPU usage near the chosen budget without
        oversubscription (each child is launched with an explicit ``--num_threads``,
        overriding any advanced value).
        """
        import concurrent.futures as cf

        total = len(self.jobs)
        self._total = total
        self._stat_started = time.time()
        self._stat_processed = 0
        self._stat_ok = 0
        self._stat_err = 0
        self._stat_in_bytes = 0
        self._stat_out_bytes = 0
        cores, per_file, pool_size = self._resolve_concurrency(total)
        self._per_file_threads = per_file

        try:
            self.log_signal.emit(
                "开始转换: " + _format_datetime(self._stat_started)
            )
            self.log_signal.emit("")
            self.log_signal.emit(
                "并发设置：核心数=%s，每文件线程=%d，并行进程=%d"
                % (self.cpu_cores if isinstance(self.cpu_cores, int) else "自动",
                   per_file, pool_size)
            )
            if total == 0:
                return
            executor = cf.ThreadPoolExecutor(max_workers=pool_size)
            futures = {}  # fut -> (index, src)
            pending = list(enumerate(self.jobs, start=1))

            def submit_next():
                while pending and len(futures) < pool_size:
                    index, job = pending.pop(0)
                    src = job[0]
                    fut = executor.submit(self._process_job, index, *job)
                    futures[fut] = (index, src)

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
                    index, src = rec
                    if fut.cancelled():
                        continue
                    try:
                        res = fut.result()
                    except Exception as exc:
                        res = (False, "处理出错：%s" % exc, 0, 0, True)
                    ok, message, tag, in_size, out_size, stopped = res
                    if stopped:
                        # 被用户在运行中中止（子进程被杀）——不计入成功/失败。
                        continue
                    self._record_result(index, src, ok, message, in_size,
                                       out_size, tag)
                submit_next()
            if self._stopped:
                self.log_signal.emit("已停止。")
                for fut in list(futures):
                    fut.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)
        finally:
            self.finished_signal.emit()

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
                "正在处理 (%d/%d)：%s" % (index, self._total, os.path.basename(src))
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
                            src, out_path, priority=self.priority
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
                return (False, message, "", in_size, 0, True)
            out_size = _safe_getsize(out_path) if ok else 0
            return (ok, message, tag, in_size, out_size, False)
        except Exception as exc:
            stopped = self._stopped
            return (False, "处理出错：%s" % exc, "", in_size, 0, stopped)

    def _record_result(self, index, src, ok, message, in_size, out_size, tag):
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
            # 优先用 cjxl 真实输出抓取的编码标签；若解析为空（如 djxl 解码、
            # 自定义命令无 Encoding 行），兜底用规则推导。
            final_tag = tag or self._encode_tag()
            self.log_signal.emit(
                _format_size_change(in_size, out_size, final_tag)
            )
        else:
            self._stat_err += 1
            self.log_signal.emit("处理失败：%s" % message)


class ActionParamDialog(QDialog):
    """Modal dialog that collects parameters for a single action type.

    ``get_params()`` returns the parameter dict for the chosen action type;
    invalid combinations (e.g. resize with both dimensions left at 0) are
    rejected in :meth:`accept` with a warning instead of closing the dialog.
    """

    def __init__(self, action_type, parent=None):
        super().__init__(parent)
        self.setWindowTitle("动作参数 - %s" % action_type)
        self.action_type = action_type
        self._controls = {}  # name -> (widget, getter)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        if action_type == "调整大小":
            w = QSpinBox()
            w.setRange(0, 100000)
            w.setSpecialValueText("自动(按比例)")
            h = QSpinBox()
            h.setRange(0, 100000)
            h.setSpecialValueText("自动(按比例)")
            form.addRow("目标宽度 (像素, 0=自动):", w)
            form.addRow("目标高度 (像素, 0=自动):", h)
            self._controls["width"] = (w, lambda: w.value())
            self._controls["height"] = (h, lambda: h.value())
        elif action_type == "旋转":
            a = QSpinBox()
            a.setRange(-360, 360)
            a.setValue(90)
            form.addRow("顺时针角度 (度):", a)
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
            pos.addItems(processor.WATERMARK_POSITIONS)
            pos.setCurrentText("右下")
            col = NoFlickerComboBox()
            col.addItems(["white", "black"])
            col.setCurrentText("white")
            form.addRow("水印文字:", t)
            form.addRow("字号:", fs)
            form.addRow("透明度 (0-255):", op)
            form.addRow("位置:", pos)
            form.addRow("颜色:", col)
            self._controls["text"] = (t, lambda: t.text())
            self._controls["font_size"] = (fs, lambda: fs.value())
            self._controls["opacity"] = (op, lambda: op.value())
            self._controls["position"] = (pos, lambda: pos.currentText())
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
            form.addRow("亮度 (1.0=不变):", b)
            form.addRow("对比度 (1.0=不变):", c)
            self._controls["brightness"] = (b, lambda: b.value())
            self._controls["contrast"] = (c, lambda: c.value())
        elif action_type == "锐化":
            f = QDoubleSpinBox()
            f.setRange(0.0, 5.0)
            f.setSingleStep(0.1)
            f.setValue(1.5)
            form.addRow("锐化强度 (1.0=不变):", f)
            self._controls["factor"] = (f, lambda: f.value())
        elif action_type == "裁剪":
            for label, name in (
                ("左边距 (像素):", "left"),
                ("上边距 (像素):", "top"),
                ("宽度 (像素, 0=到边界):", "width"),
                ("高度 (像素, 0=到边界):", "height"),
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
                QMessageBox.warning(self, "参数无效",
                                    "调整大小需至少设置宽度或高度之一。")
                return
        elif self.action_type == "裁剪":
            w = int(self._controls["width"][1]())
            h = int(self._controls["height"][1]())
            if w == 0 and h == 0:
                QMessageBox.warning(self, "参数无效",
                                    "裁剪需至少设置宽度或高度之一。")
                return
        elif self.action_type == "水印":
            if not (self._controls["text"][1]() or "").strip():
                QMessageBox.warning(self, "参数无效", "水印文字不能为空。")
                return
        super().accept()

    def get_params(self):
        params = {}
        for name, (widget, getter) in self._controls.items():
            params[name] = getter()
        return params

