# -*- coding: utf-8 -*-
"""Headless 回归测试：启动参数 / 拖入文件自动加入输入。

验证两件事：
1. 启动参数解析（_parse_launch_paths）：从 argv 抽出真实存在的文件/文件夹，
   跳过 flag（如 --selftest）与不存在的路径。
2. MainWindow(initial_paths=...) 在首次 show 时自动把传入的文件/文件夹加入
   输入列表——单文件直接加、文件夹递归展开为其中支持的图像、去重、切到输入
   标签页并写日志。这正是「拖到 exe / 打开方式 / 命令行传参启动」期望的行为。
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from PySide6.QtCore import QTimer  # noqa: E402

from PIL import Image  # noqa: E402
from jxlforge.__main__ import _parse_launch_paths  # noqa: E402
from jxlforge.main_window import MainWindow  # noqa: E402


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def _make_png(path):
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path, "PNG")


def _make_tmp_tree():
    """造一棵临时目录树：root 下 2 个 png + 1 个 txt，sub 下 1 个 png。"""
    root = tempfile.mkdtemp(prefix="jxlforge_launch_")
    _make_png(os.path.join(root, "a.png"))
    _make_png(os.path.join(root, "b.png"))
    with open(os.path.join(root, "note.txt"), "w", encoding="utf-8") as fh:
        fh.write("not an image")
    sub = os.path.join(root, "sub")
    os.makedirs(sub)
    _make_png(os.path.join(sub, "c.png"))
    return root


# ---- 1. 启动参数解析 ----
def test_parse_launch_paths():
    tmp = _make_tmp_tree()
    try:
        good_png = os.path.join(tmp, "a.png")
        good_dir = tmp
        bad_path = os.path.join(tmp, "nope.png")
        argv = ["jxlforge",
                "--selftest",            # flag，应跳过
                good_png,               # 文件
                good_dir,               # 文件夹
                bad_path,               # 不存在，应跳过
                "-x"]                   # flag，应跳过
        got = _parse_launch_paths(argv)
        got_set = set(os.path.normcase(os.path.abspath(p)) for p in got)
        expect = {os.path.normcase(os.path.abspath(good_png)),
                  os.path.normcase(os.path.abspath(good_dir))}
        check("解析出真实存在的文件与文件夹", got_set == expect, str(got))
        check("跳过 flag 与不存在路径", len(got) == 2, "got %d" % len(got))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 2. 启动参数文件自动加入输入 ----
def test_launch_auto_add():
    tmp = _make_tmp_tree()
    try:
        good_png = os.path.join(tmp, "a.png")
        window = MainWindow(initial_paths=[good_png, tmp])  # 单文件 + 文件夹
        # 触发首次 showEvent（offscreen 下 show 即同步触发）。
        window.show()
        QApplication.processEvents()
        # 期望：a.png 直接加入；tmp 递归展开为 a.png/b.png/sub/c.png；
        # 去重后 tmp 下的 a.png 与单独传入的 a.png 合并为一条。
        added = set(os.path.normcase(p) for p in window.input_files)
        expect_files = {
            os.path.normcase(os.path.abspath(os.path.join(tmp, "a.png"))),
            os.path.normcase(os.path.abspath(os.path.join(tmp, "b.png"))),
            os.path.normcase(os.path.abspath(os.path.join(tmp, "sub", "c.png"))),
        }
        check("启动参数文件已自动加入输入", expect_files.issubset(added),
              "added=%d" % len(added))
        check("文件夹递归展开且去重", len(added) == 3, "added=%d" % len(added))
        check("首帧后已切到输入标签页",
              window.tabs.currentWidget() is window.input_tab)
        check("启动参数只处理一次（_launch_paths_consumed）",
              window._launch_paths_consumed is True)
        # txt 不应被当作图像加入
        check("非图像文件未被加入",
              not any(p.lower().endswith(".txt") for p in window.input_files))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 3. 无启动参数时不应改动输入 ----
def test_no_launch_paths():
    window = MainWindow(initial_paths=[])
    window.show()
    QApplication.processEvents()
    check("无启动参数时输入列表为空", window.input_files == [])
    check("无启动参数时标志已消费", window._launch_paths_consumed is True)


def main():
    test_parse_launch_paths()
    test_launch_auto_add()
    test_no_launch_paths()
    passed = sum(1 for _, ok in results if ok)
    failed = [n for n, ok in results if not ok]
    print("\n%d/%d 通过" % (passed, len(results)))
    if failed:
        print("失败：", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
