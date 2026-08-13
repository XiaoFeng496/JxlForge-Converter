# -*- coding: utf-8 -*-
"""Superseded by test_header_fix.py.

The Details-view header was refactored (header built once; Qt owns the visual
column order; logical index == TABLE_COLUMNS position). The assertions here
targeted the old _table_cols / _table_logical model and no longer apply.
Run test_header_fix.py for current coverage.
"""
import sys

if __name__ == "__main__":
    print("SKIP: superseded by test_header_fix.py")
    sys.exit(0)
