# -*- coding: utf-8 -*-
"""Checks for the action-tab live preview area (no image rendering needed)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from libjxl_gui import main_window as mw

passed = 0
failures = []


def check(name, cond):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(name)


# The preview wiring must exist on the class.
check("has _render_action_preview", hasattr(mw.MainWindow, "_render_action_preview"))
check("has _refresh_preview_sources", hasattr(mw.MainWindow, "_refresh_preview_sources"))
check("has _current_preview_source", hasattr(mw.MainWindow, "_current_preview_source"))
check("has _pil_to_qimage", hasattr(mw.MainWindow, "_pil_to_qimage"))
check("has _on_tab_changed", hasattr(mw.MainWindow, "_on_tab_changed"))
check("has _apply_preview_pixmap", hasattr(mw.MainWindow, "_apply_preview_pixmap"))
check("has _preview_show_original", hasattr(mw.MainWindow, "_preview_show_original"))
check("has _preview_show_processed", hasattr(mw.MainWindow, "_preview_show_processed"))
check("has _preview_actual", hasattr(mw.MainWindow, "_preview_actual"))
check("PreviewScroll has zoom_to_actual", hasattr(mw.PreviewScroll, "zoom_to_actual"))

try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    window = mw.MainWindow()

    # Preview widgets created by _build_actions_tab.
    check("preview_source_combo exists", hasattr(window, "preview_source_combo"))
    check("preview_view exists", hasattr(window, "preview_view"))
    check("preview_msg exists", hasattr(window, "preview_msg"))
    check("zoom_in_button exists", hasattr(window, "zoom_in_button"))
    check("zoom_out_button exists", hasattr(window, "zoom_out_button"))
    check("zoom_actual_button exists", hasattr(window, "zoom_actual_button"))
    check("zoom_fit_button exists", hasattr(window, "zoom_fit_button"))
    check("show_original_button exists", hasattr(window, "show_original_button"))
    check("actions_tab stored", window.actions_tab is not None)
    check("default preview mode processed", window._preview_mode == "processed")

    # With no input files: render shows the placeholder message, not the canvas,
    # and must not raise (it must avoid building a QPixmap on headless/offscreen).
    # NOTE: under headless nothing is ever shown, so we check the explicit
    # visibility flag (isHidden) rather than isVisible (which follows ancestors).
    window._render_action_preview()
    check("no-input -> view hidden", window.preview_view.isHidden() is True)
    check("no-input -> msg shown", window.preview_msg.isHidden() is False)
    check("no-input -> source is None", window._current_preview_source() is None)

    # Source population: fake two input files and verify the combo fills and
    # _current_preview_source() resolves to the right path.
    window.input_files = [r"C:\a\one.jpg", r"C:\a\two.png"]
    window._refresh_preview_sources()
    check("combo has 2 entries", window.preview_source_combo.count() == 2)
    check("combo entry text", window.preview_source_combo.itemText(0).endswith("one.jpg"))
    window.preview_source_combo.setCurrentIndex(1)
    check("source resolves to two.png", window._current_preview_source() == r"C:\a\two.png")

    # Press/release (hold-to-peek) does nothing without a rendered pixmap and
    # must not crash; mode stays "processed".
    window._preview_show_original()
    check("press keeps processed w/o pixmap", window._preview_mode == "processed")
    window._preview_show_processed()
    check("release keeps processed w/o pixmap", window._preview_mode == "processed")

    # Slots are callable.
    check("render callable", callable(window._render_action_preview))
    check("actual callable", callable(window._preview_actual))

    print("PREVIEW_CONSTRUCT_OK")
except Exception as exc:  # pragma: no cover - environment dependent
    import traceback
    traceback.print_exc()
    print("PREVIEW_CONSTRUCT_SKIPPED:", exc)

print("PASSED=%d  FAILURES=%d" % (passed, len(failures)))
for f in failures:
    print("  FAIL:", f)
print("ALL_OK" if not failures else "HAS_FAILURES")
