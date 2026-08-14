# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Headless verification of the Details-view header refactor.

Validates the bug fixes:
  1. 升降序 (asc/desc) sorts the column the user actually clicked -- no logical
     index drift after a header drag (the old "sort hits the wrong column").
  2. Column widths + visual order survive a table refresh (simulated view
     switch): the header is built ONCE, so re-refreshing never renumbers
     logical indices or resets widths/order.
  3. The new 创建日期 column is present and toggleable via the right-click menu.
  4. Resetting columns restores the default order.
"""

import os
import sys
import tempfile

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QTableWidget

from libjxl_gui.main_window import MainWindow, TABLE_COLUMNS


def main():
    failures = []
    app = QApplication.instance() or QApplication(sys.argv[:1])
    # Mirror __main__.run(): persist to a .ini file (not the registry) so the
    # test doesn't write into the user's Windows registry.
    QSettings.setDefaultFormat(QSettings.IniFormat)
    app.setOrganizationName("libjxl")
    app.setApplicationName("libjxl-gui")

    # Start from a clean slate so the persistence feature (which reads
    # QSettings on init) doesn't pollute the default-layout assertions.
    QSettings().remove("details_columns")
    QSettings().remove("main_window")

    keys = [k for (k, _l, _v, _w, _a) in TABLE_COLUMNS]
    key_logical = {k: i for i, k in enumerate(keys)}

    # --- 3. 创建日期 column exists + is in the menu ---
    if "created" not in keys:
        failures.append("created column missing from TABLE_COLUMNS")
    w = MainWindow()
    menu = w._build_table_header_menu()
    labels = [a.text() for a in menu.actions() if a.text()]
    if "创建日期" not in labels:
        failures.append("创建日期 missing from header context menu")

    # --- 3b. default visibility + widths per spec ---
    if w._table_hidden != ["operated"]:
        failures.append("default hidden set wrong: %r" % w._table_hidden)
    expect_w = {
        "name": 200, "format": 64, "size": 92, "modified": 140,
        "created": 140, "operated": 140, "resolution": 110,
        "ratio": 70, "path": 240,
    }
    for k, ew in expect_w.items():
        if w._table_widths.get(k) != ew:
            failures.append("default width %s=%r, expected %r"
                            % (k, w._table_widths.get(k), ew))
    if "清除排序" in labels:
        failures.append("清除排序 should have been removed from header menu")

    # Build a few temp files so sorting has real data.
    tmp = tempfile.mkdtemp()
    paths = []
    for n in ("b.txt", "a.txt", "c.txt"):
        p = os.path.join(tmp, n)
        with open(p, "w") as f:
            f.write("x")
        paths.append(p)
    w.input_files = paths

    # Build the header exactly once (simulates first switch to 详细信息).
    w._refresh_table()

    # --- 0. hover-info tooltip: every table cell must carry the same info
    #         tooltip the list / thumbnail views show (regression: 详细信息
    #         view used to set no tooltip, so hovering a file showed nothing).
    for r in range(w.input_table.rowCount()):
        for c in range(w.input_table.columnCount()):
            it = w.input_table.item(r, c)
            if it is None:
                failures.append("table cell (%d,%d) is None" % (r, c))
                continue
            tip = it.toolTip()
            base = os.path.basename(w.input_table.item(r, 0).data(
                Qt.UserRole) if c == 0 else it.data(Qt.UserRole))
            if not tip:
                failures.append("table cell (%d,%d) has no tooltip" % (r, c))
            elif base not in tip:
                failures.append(
                    "table cell (%d,%d) tooltip does not name the file: %r"
                    % (r, c, tip)
                )

    # --- 1. click-to-sort maps correctly, even after a header drag ---
    # Drag column 0 (name) to visual position 2. Logical indices are fixed, so
    # clicking logical index of "format" must STILL sort by format.
    header = w.input_table.horizontalHeader()
    header.moveSection(0, 2)
    w._on_table_header_clicked(key_logical["format"])
    if w._table_sort is None or w._table_sort[0] != "format":
        failures.append(
            "after drag, click(logical=format) sorted %r, expected 'format'"
            % (w._table_sort,)
        )
    # Second click toggles to descending (still format).
    w._on_table_header_clicked(key_logical["format"])
    if w._table_sort[0] != "format" or w._table_sort[1] is not False:
        failures.append("toggle to descending failed: %r" % (w._table_sort,))

    # Sort by name ascending -> rows ordered a, b, c.
    w._on_table_header_clicked(key_logical["name"])
    got = [
        w.input_table.item(r, 0).text()
        for r in range(w.input_table.rowCount())
    ]
    if got != ["a.txt", "b.txt", "c.txt"]:
        failures.append("name ascending order wrong: %r" % got)

    # --- 2. width + visual order survive a refresh (view switch) ---
    # Record visual order + a non-last column width, then refresh again.
    vis_before = [header.visualIndex(i) for i in range(len(keys))]
    # Resize the "name" column to 333 px (simulate a user drag-resize).
    w._on_table_section_resized(key_logical["name"], 0, 333)
    if w._table_widths.get("name") != 333:
        failures.append("width not stored: %r" % w._table_widths.get("name"))
    w._refresh_table()  # simulated view switch back to 详细信息
    vis_after = [header.visualIndex(i) for i in range(len(keys))]
    if vis_after != vis_before:
        failures.append(
            "visual column order changed on refresh: %r -> %r"
            % (vis_before, vis_after)
        )
    if w.input_table.columnWidth(key_logical["name"]) != 333:
        failures.append(
            "name width not re-applied after refresh: %r"
            % w.input_table.columnWidth(key_logical["name"])
        )

    # --- visibility toggle keeps at least one column + hides correctly ---
    visible_before = len(keys) - len(w._table_hidden)
    # Hide "format" (hidden by default? no -> it's visible). Toggle it.
    w._toggle_table_column("format")
    if "format" not in w._table_hidden:
        failures.append("toggle did not hide format")
    w._toggle_table_column("format")  # show again
    if "format" in w._table_hidden:
        failures.append("toggle did not re-show format")

    # Cannot hide the last visible column.
    # Hide everything except one column forcibly, then ensure guard holds.
    for k in list(keys):
        if k != "name":
            w._table_hidden.append(k)
    # Try to hide the final one -> should be refused.
    w._toggle_table_column("name")
    if "name" in w._table_hidden:
        failures.append("last visible column was hidden (guard failed)")
    w._table_hidden = ["operated"]  # restore to default visible set

    # --- 4. reset restores default order ---
    header.moveSection(0, 2)
    w._table_header_built = False
    w._refresh_table()  # rebuild -> default order
    for i in range(len(keys)):
        if header.visualIndex(i) != i:
            failures.append("reset did not restore default column order")
            break

    # --- 5. row drag is always enabled (even when sorted); drop clears sort ---
    w._table_sort = ("name", True)
    w._refresh_table()
    if not w.input_table.dragEnabled():
        failures.append("row drag disabled while sorted (must stay enabled)")
    if w.input_table.dragDropMode() != QTableWidget.InternalMove:
        failures.append("row drag mode not InternalMove while sorted")

    # Simulate a real drop: select row 0, drop at seam 2. The dropEvent must
    # reorder by the DISPLAYED order and clear the active sort (arrow gone).
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import QMimeData, QPoint, Qt as _Qt
    w._refresh_list = lambda: None  # skip QPixmap work (crashes offscreen)
    w.statusBar().showMessage = lambda *a, **k: None
    w.input_table.selectRow(0)
    w.input_table.drop_index = 2
    md = QMimeData()
    md.setText("internal-move")  # hasUrls() == False -> treated as internal
    ev = QDropEvent(QPoint(0, 0), _Qt.MoveAction, md, _Qt.LeftButton, _Qt.NoModifier)
    w.input_table.dropEvent(ev)
    if w._table_sort is not None:
        failures.append("drop did not clear sort: %r" % (w._table_sort,))
    got2 = [
        w.input_table.item(r, 0).text() for r in range(w.input_table.rowCount())
    ]
    # Sorted was [a,b,c]; seam 2 (before item index 2 = c) moves a between
    # b and c -> [b,a,c].
    if got2 != ["b.txt", "a.txt", "c.txt"]:
        failures.append("drop reorder (displayed order) wrong: %r" % got2)

    # --- 6. persistence: widths / order / hidden / sort survive a restart ---
    w._table_widths["name"] = 175
    w._table_widths["size"] = 80
    # reorder: move "name" (logical 0) to visual position 2
    header.moveSection(0, 2)
    w._toggle_table_column("path")  # hide path
    w._table_sort = ("size", False)
    w._save_table_layout()
    QSettings().sync()  # flush to backing store before re-reading
    # New window should load the persisted layout on init.
    w2 = MainWindow()
    if w2._table_widths.get("name") != 175 or w2._table_widths.get("size") != 80:
        failures.append("persisted widths not restored: %r" % w2._table_widths)
    if "path" not in w2._table_hidden:
        failures.append("persisted hidden (path) not restored")
    if w2._table_sort != ("size", False):
        failures.append("persisted sort not restored: %r" % (w2._table_sort,))
    # Build w2's header (it is built lazily) so the saved order is applied and
    # visualIndex queries are meaningful.
    w2._refresh_table()
    hdr2 = w2.input_table.horizontalHeader()
    name_logical = keys.index("name")
    if hdr2.visualIndex(name_logical) != 2:
        failures.append("persisted column order not restored: name visual=%d"
                        % hdr2.visualIndex(name_logical))
    # --- 7. Settings tab + window geometry persistence ---
    if not hasattr(w, "settings_tab"):
        failures.append("settings tab not created")
    else:
        from PySide6.QtWidgets import QPushButton as _PB
        texts = [b.text() for b in w.settings_tab.findChildren(_PB)]
        if "一键居中" not in texts:
            failures.append("一键居中 button missing in settings tab")
        if "一键 6×3 排版" not in texts:
            failures.append("一键 6×3 排版 button missing in settings tab")

    # Geometry persistence mechanics. The exact save/restore round-trip is
    # platform-dependent in headless (no real window frame), but on the desktop
    # Qt's saveGeometry/restoreGeometry round-trips faithfully; we assert the
    # plumbing: saving writes non-null bytes, load returns True with bytes and
    # False once they're cleared.
    QSettings().remove("main_window")
    w.move(123, 456)
    w.resize(900, 600)
    w._save_geometry()
    QSettings().sync()
    if not QSettings().contains("main_window/geometry"):
        failures.append("geometry not saved to QSettings")
    if not w._load_geometry():
        failures.append("geometry load returned False with saved bytes")
    QSettings().remove("main_window/geometry")
    if w._load_geometry():
        failures.append("geometry load returned True after removal")

    # Buttons behave correctly on a fresh window (no saved geometry). Resize it
    # to fit within the headless screen first, so the platform won't clamp the
    # centred position (a window wider than the screen can't be centred in
    # offscreen, which is a test-env artifact, not a code bug).
    w3 = MainWindow()
    w3.resize(600, 500)
    size_before = (w3.width(), w3.height())
    w3._on_center_window()
    # Centre the OUTER (frame) rectangle, not just the client rect — otherwise
    # the visible window sits ~half a title-bar too high.
    fg = w3.frameGeometry()
    cx = fg.x() + fg.width() // 2
    cy = fg.y() + fg.height() // 2
    screen = QApplication.primaryScreen().availableGeometry()
    scx = screen.x() + screen.width() // 2
    scy = screen.y() + screen.height() // 2
    if abs(cx - scx) > 2 or abs(cy - scy) > 2:
        failures.append("一键居中 did not centre: center=(%d,%d) screen=(%d,%d)"
                        % (cx, cy, scx, scy))
    if (w3.width(), w3.height()) != size_before:
        failures.append("一键居中 changed size: %r" % ((w3.width(), w3.height()),))

    # 一键 6×3 排版: restores the default 6x3 size. The fit pins the window's
    # minimum size to exactly 6x3. In headless the window frame is measured as
    # negative (no layout pass before show), so we allow a small tolerance; on
    # the real desktop the frame is positive and the minimum is >= 6*122 x 3*154.
    # Position is kept because centre=False.
    before_pos = (w3.x(), w3.y())
    w3._on_fit_window()
    MIN_W = 6 * 122 - 40
    MIN_H = 3 * 154 - 40
    if w3.minimumWidth() < MIN_W or w3.minimumHeight() < MIN_H:
        failures.append("一键 6×3 排版 did not pin 6x3 minimum size: %r"
                        % ((w3.minimumWidth(), w3.minimumHeight()),))

    QSettings().remove("details_columns")  # clean up
    QSettings().remove("main_window")  # clean up

    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("ALL_OK: header refactor verified (%d columns)" % len(keys))


if __name__ == "__main__":
    main()
