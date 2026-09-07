# -*- coding: utf-8 -*-
"""回归测试：自定义文件夹手动输入路径（含尚不存在的路径）必须生效。

覆盖：
  * 选中「文件夹」radio + 手动输入【尚不存在】的文件夹 -> 程序自动创建，
    输出落在该路径（修复「手动输入自定义文件夹路径无效」）。
  * 选中「文件夹」radio + 手动输入【已存在】的文件夹 -> 正常落在该路径。
  * 选中「文件夹」radio + 路径为空 -> 回退到源文件所在目录。
  * 手动输入的新路径会被记入文件夹历史（_on_convert 不再要求 isdir）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from jxlforge.main_window import MainWindow


def fresh_window():
    return MainWindow()


def check(desc, cond):
    print(("OK  " if cond else "FAIL") + " " + desc)
    if not cond:
        check.failed += 1


check.failed = 0


def _make_src():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "a.png")
    with open(src, "w") as f:
        f.write("x")
    return src


def main():
    # --- 场景1：手动输入【尚不存在】的文件夹 -----------------------------
    w = fresh_window()
    w.same_folder_radio.setChecked(False)
    w.custom_folder_radio.setChecked(True)
    src = _make_src()
    nonexist = os.path.join(tempfile.mkdtemp(), "out_new_sub")
    w.custom_folder_edit.setText(nonexist)
    out = w._build_output_path(src)
    check("场景1 选中radio + 输入不存在路径 -> 输出落在指定路径",
          os.path.dirname(out) == nonexist)
    check("场景1 指定路径被自动创建", os.path.isdir(nonexist))
    check("场景1 输出文件名正确 (a.jxl)",
          os.path.basename(out) == "a.jxl")

    # --- 场景2：手动输入【已存在】的文件夹 -------------------------------
    w2 = fresh_window()
    w2.same_folder_radio.setChecked(False)
    w2.custom_folder_radio.setChecked(True)
    src2 = _make_src()
    exist = tempfile.mkdtemp()
    w2.custom_folder_edit.setText(exist)
    out2 = w2._build_output_path(src2)
    check("场景2 选中radio + 输入已存在路径 -> 输出落在该路径",
          os.path.dirname(out2) == exist)

    # --- 场景3：路径为空 -> 回退源目录 -----------------------------------
    w3 = fresh_window()
    w3.same_folder_radio.setChecked(False)
    w3.custom_folder_radio.setChecked(True)
    src3 = _make_src()
    w3.custom_folder_edit.setText("")
    out3 = w3._build_output_path(src3)
    check("场景3 选中radio + 空路径 -> 回退源文件目录",
          os.path.dirname(out3) == os.path.dirname(src3))

    # --- 场景4：手动输入新路径进入文件夹历史 -----------------------------
    import jxlforge.converter as _converter
    _converter.check_tools = lambda: {"cjxl": True, "djxl": True}

    w4 = fresh_window()
    w4.same_folder_radio.setChecked(False)
    w4.custom_folder_radio.setChecked(True)
    src4 = _make_src()
    w4.input_files = [src4]
    newdir = os.path.join(tempfile.mkdtemp(), "history_new")
    w4.custom_folder_edit.setText(newdir)
    w4._on_convert()
    check("场景4 手动输入新路径被记入文件夹历史",
          newdir in getattr(w4, "_folder_history", []))
    # 让场景4 触发的后台转换线程在进程退出前结束，避免解释器关闭后 atexit 噪声
    wk = getattr(w4, "_convert_worker", None)
    if wk is not None:
        try:
            wk.wait(500)
        except Exception:
            pass

    print()
    if check.failed:
        print("HAS_FAILURES: %d" % check.failed)
    else:
        print("ALL_OK")


if __name__ == "__main__":
    main()
