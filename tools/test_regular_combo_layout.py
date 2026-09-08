# -*- coding: utf-8 -*-
# 验证：常规分组的 theme_grid 三个 combo 的 cell 分配正确，且不同 row 的 cell
# y 坐标不同（保证不重叠）。
# 注：QComboBox.mapTo(parent, ...) 在 offscreen 平台下不可信，所以用
# theme_grid.itemAtPosition(r,c).geometry() 拿 cell 在 grid 局部坐标系中的矩形。
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="regular_layout_"))
os.environ["APPDATA"] = str(tmp)
os.environ["LOCALAPPDATA"] = str(tmp)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp))

sys.path.insert(0, r"F:\Agent工作空间\JxlForge-Converter")
import jxlforge.main_window as mw

app = QApplication.instance() or QApplication([])

w = mw.MainWindow()
w.setFixedSize(880, 640)
w.show()
# 切到设置页（如果存在 QTabWidget）
for _ in range(5):
    app.processEvents()
# 找所有 QTabWidget 并切到最后一页（设置页通常在末尾）
def find_and_show_last_tab(obj):
    from PySide6.QtWidgets import QTabWidget
    if isinstance(obj, QTabWidget):
        # 触发当前页布局构建
        for i in range(obj.count()):
            obj.setCurrentIndex(i)
            app.processEvents()
        return True
    for child in obj.children():
        if find_and_show_last_tab(child):
            return True
    return False
find_and_show_last_tab(w)
for _ in range(5):
    app.processEvents()

# 找 theme_grid：2 列的 QGridLayout，里面有 3 个 combo
def find_theme_grid():
    all_grids = []
    def visit(obj):
        if isinstance(obj, QGridLayout) and obj.columnCount() == 2:
            all_grids.append(obj)
        for child in obj.children():
            visit(child)
    visit(w)
    return all_grids

all_grids = find_theme_grid()
print(f"找到 {len(all_grids)} 个 2 列 QGridLayout")
# 找含 3 个 combo 的那个
theme_grid = None
for gi, g in enumerate(all_grids):
    n_combo = 0
    combo_names = []
    for r in range(g.rowCount()):
        for c in range(g.columnCount()):
            it = g.itemAtPosition(r, c)
            if isinstance(it, QHBoxLayout):
                for i in range(it.count()):
                    w_ = it.itemAt(i).widget()
                    # 全量迁移后 NoFlickerComboBox 是 SwitchableComboBox 代理
                    # （QWidget，非 QComboBox），用它判定下拉而非原生 QComboBox。
                    if isinstance(w_, mw.NoFlickerComboBox):
                        n_combo += 1
                        combo_names.append(w_.objectName() or type(w_).__name__)
    print(f"  grid[{gi}]: {g.rowCount()}x{g.columnCount()}, combos={n_combo} {combo_names}")
    if n_combo >= 3:
        theme_grid = g
        break
assert theme_grid is not None, "没找到含 3 个 combo 的 grid"


def cell_geom(row, col):
    """返回 cell 在 grid 局部坐标中的 rect（x, y, w, h）。"""
    it = theme_grid.itemAtPosition(row, col)
    if it is None:
        return None
    g = it.geometry()
    return (g.x(), g.y(), g.width(), g.height())


g00 = cell_geom(0, 0)
g01 = cell_geom(0, 1)
g10 = cell_geom(1, 0)
g11 = cell_geom(1, 1)
print(f"(0,0) 主题  : {g00}")
print(f"(0,1) 语言  : {g01}")
print(f"(1,0) 控件样式: {g10}")
print(f"(1,1) 占位  : {g11}")

errs = 0

# 1) (0,0) 必须有 cell（主题）
if g00 is None:
    print("FAIL (0,0) 主题 为空")
    errs += 1
else:
    print("OK   (0,0) 主题 有 cell")

# 2) (0,1) 必须有 cell（语言）
if g01 is None:
    print("FAIL (0,1) 语言 为空")
    errs += 1
else:
    print("OK   (0,1) 语言 有 cell")

# 3) (1,0) 必须有 cell（控件样式）
if g10 is None:
    print("FAIL (1,0) 控件样式 为空")
    errs += 1
else:
    print("OK   (1,0) 控件样式 有 cell")

# 4) (1,1) 必须返回和 (1,0) 同一个 item（colspan=2 正确表现）
it10 = theme_grid.itemAtPosition(1, 0)
it11 = theme_grid.itemAtPosition(1, 1)
if it10 is None or it11 is None:
    print(f"FAIL (1,0) 或 (1,1) 缺 item: 10={it10}, 11={it11}")
    errs += 1
elif it10 is not it11:
    print(f"FAIL (1,0) 和 (1,1) 不是同一个 item（colspan 没生效）")
    errs += 1
else:
    print("OK   (1,0) 和 (1,1) 是同一个 item（colspan=2 生效）")

# 5) 顶行 y 必须 < 底行 y（不重叠的关键）
if g00 and g10 and g00[1] < g10[1]:
    print(f"OK   顶行 y={g00[1]} < 底行 y={g10[1]}（不重叠）")
elif g00 and g10:
    print(f"FAIL 顶行 y={g00[1]} >= 底行 y={g10[1]}（同行！）")
    errs += 1

# 6) 顶行两个 cell 同一 y（同行）
if g00 and g01 and g00[1] == g01[1]:
    print(f"OK   主题/语言 同行 y={g00[1]}")
elif g00 and g01:
    print(f"FAIL 主题 y={g00[1]} != 语言 y={g01[1]}")
    errs += 1

# 7) 控件样式 cell 宽度应 >= 主题+语言 cell 宽度之和（跨两列）
if g00 and g01 and g10:
    row0_total = g00[2] + g01[2]
    if g10[2] >= row0_total - 4:  # 允许小误差
        print(f"OK   控件样式宽 {g10[2]} >= 顶行两 cell 宽之和 {row0_total}")
    else:
        print(f"FAIL 控件样式宽 {g10[2]} < 顶行两 cell 宽之和 {row0_total}")
        errs += 1

print(f"\nFAIL={errs}")
sys.exit(1 if errs else 0)
