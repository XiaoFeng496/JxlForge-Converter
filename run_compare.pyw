#! E:/Python/Python312/pythonw.exe
# -*- coding: utf-8 -*-
"""版本对比启动器：从 git 拉取指定旧提交，与当前工作树**并排运行两个真实 GUI**。

用途
----
有些界面缺陷（例如「控件样式=原生时下拉菜单选中行左侧竖线变黑」）只在真实
Windows 主题 + 原生 QStyle 下出现，写 A/B 探针复刻旧实现很容易复现不出来。
本工具直接把旧提交导成一份**完整可运行的独立副本**，与当前工作树各起一个
进程、左右并排，肉眼直接对比。

用法
----
双击本文件（.pyw 由 Python Launcher 的 pyw.exe 无控制台加载）。
可选环境变量：
  LIBJXL_COMPARE_REF   要对比的旧提交，默认 e48a1f1
  LIBJXL_COMPARE_NO_GIT 设为 1 时跳过导出，直接使用已有的 compare_old/

注意
----
两个 GUI 共用同一份 QSettings（%APPDATA%\\libjxl\\libjxl-gui.ini），因此主题
设置在两边是一致的——这正是对比所需要的。启动前会自动备份该 ini。
"""
import ctypes
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time
from ctypes import wintypes
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
LEGACY_REF = os.environ.get("LIBJXL_COMPARE_REF", "e48a1f1")
LEGACY_DIR = os.path.join(REPO_ROOT, "compare_old")
MARKER_NAME = ".compare_marker"

# 优先用独立安装的 Git for Windows（E:\Git），避免 PortableGit 的
# credential helper-selector 弹窗；找不到再退回 PATH 里的 git。
_GIT_CANDIDATES = (
    r"E:\Git\cmd\git.exe",
    r"E:\Git\bin\git.exe",
)


# --------------------------------------------------------------------------
# 旧版本导出
# --------------------------------------------------------------------------
def _find_git():
    for path in _GIT_CANDIDATES:
        if os.path.isfile(path):
            return path
    found = shutil.which("git")
    return found or "git"


def export_legacy(ref, log):
    """把 ref 对应的提交导出到 LEGACY_DIR，返回 (ok, message)。

    用 ``git archive`` 把树打成 tar 流，再由 Python 的 tarfile 解包——全程只读
    .git，**不会在 .git 内增删文件**，因此不会触发 safe-delete 守卫。
    """
    if os.environ.get("LIBJXL_COMPARE_NO_GIT") == "1":
        if os.path.isfile(os.path.join(LEGACY_DIR, "main.py")) or os.path.isdir(
            os.path.join(LEGACY_DIR, "libjxl_gui")
        ):
            log("跳过导出（LIBJXL_COMPARE_NO_GIT=1），复用已有副本。")
            return True, "复用已有副本"
        return False, "LIBJXL_COMPARE_NO_GIT=1 但 compare_old/ 不是有效的项目副本"

    git = _find_git()
    log("git: %s" % git)
    try:
        proc = subprocess.run(
            [git, "rev-parse", "--verify", ref + "^{commit}"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return False, "调用 git 失败：%s" % exc
    if proc.returncode != 0:
        return False, "找不到提交 %s（%s）" % (
            ref,
            proc.stderr.decode("utf-8", "replace").strip(),
        )
    full_sha = proc.stdout.decode("ascii", "replace").strip()
    log("目标提交 %s -> %s" % (ref, full_sha))

    try:
        proc = subprocess.run(
            [git, "archive", "--format=tar", full_sha],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return False, "git archive 失败：%s" % exc
    if proc.returncode != 0:
        return False, "git archive 失败：%s" % (
            proc.stderr.decode("utf-8", "replace").strip()
        )

    # 目录清理：只清理本脚本自己创建过的目录（有 marker 文件），避免误删。
    if os.path.isdir(LEGACY_DIR):
        if not os.path.isfile(os.path.join(LEGACY_DIR, MARKER_NAME)):
            return False, (
                "compare_old/ 已存在且缺少标记文件 %s，拒绝清理，"
                "请手动确认后删除该目录再重试。" % MARKER_NAME
            )
        log("清理旧副本 …")
        shutil.rmtree(LEGACY_DIR)

    os.makedirs(LEGACY_DIR, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(LEGACY_DIR)
    with open(os.path.join(LEGACY_DIR, MARKER_NAME), "w", encoding="utf-8") as fh:
        fh.write("exported_from=%s\nat=%s\n" % (full_sha, datetime.now().isoformat(timespec="seconds")))
    log("已导出到 %s" % LEGACY_DIR)
    return True, full_sha


# --------------------------------------------------------------------------
# 设置文件备份
# --------------------------------------------------------------------------
def backup_settings_ini(log):
    src = os.path.join(
        os.environ.get("APPDATA", ""), "libjxl", "libjxl-gui.ini"
    )
    if not os.path.isfile(src):
        log("未找到真实设置文件，跳过备份：%s" % src)
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(REPO_ROOT, "compare_old_ini_backup")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "libjxl-gui-%s.ini" % stamp)
    shutil.copy2(src, dest)
    log("设置文件已备份：%s" % dest)
    return dest


# --------------------------------------------------------------------------
# 窗口并排摆放（ctypes，无需 pywin32）
# --------------------------------------------------------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
_user32.SetWindowPos.restype = wintypes.BOOL


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


def _top_level_windows_of_pid(pid):
    found = []

    @_EnumWindowsProc
    def _cb(hwnd, _lparam):
        pid_out = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value == pid and _user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    _user32.EnumWindows(_cb, 0)
    return found


def _area_of(hwnd):
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return 0
    return max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)


def place_window(pid, side):
    """把 pid 的最大可见顶层窗口摆到屏幕左/右半边。返回 hwnd 或 None。"""
    hwnds = _top_level_windows_of_pid(pid)
    if not hwnds:
        return None
    hwnd = max(hwnds, key=_area_of)
    screen_w = _user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = _user32.GetSystemMetrics(SM_CYSCREEN)
    half = screen_w // 2
    x = 0 if side == "left" else half
    _user32.SetWindowPos(
        hwnd, None, x, 0, half, screen_h, SWP_NOZORDER | SWP_NOACTIVATE
    )
    return hwnd


# --------------------------------------------------------------------------
# 控制面板
# --------------------------------------------------------------------------
def _spawn(cwd, log):
    """在 cwd 启动一个 GUI 进程。用 -c 方式，不依赖 main.py 是否存在。"""
    code = (
        "import sys, os;"
        "sys.path.insert(0, os.getcwd());"
        "from libjxl_gui.__main__ import run;"
        "run()"
    )
    log("启动：cwd=%s" % cwd)
    try:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=cwd)
    except OSError as exc:
        log("启动失败：%s" % exc)
        return None
    log("  pid=%d" % proc.pid)
    return proc


def main():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QApplication, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
        QPushButton, QVBoxLayout, QWidget,
    )

    class Panel(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("libjxl GUI 版本对比")
            self.resize(760, 520)
            self.legacy_proc = None
            self.new_proc = None
            self.place_queue = []  # [(proc, side, deadline)]

            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(8)

            title = QLabel(
                "版本对比 — 旧版 <b>%s</b>（git 导出副本） vs 新版（当前工作树）" % LEGACY_REF
            )
            title.setTextFormat(Qt.RichText)
            layout.addWidget(title)

            tip = QLabel(
                "对比方法：两个窗口都进入「设置 → 常规 → 控件样式」选「<b>原生</b>」，"
                "再打开任意下拉菜单，看选中项左侧竖线的颜色。"
            )
            tip.setTextFormat(Qt.RichText)
            tip.setWordWrap(True)
            layout.addWidget(tip)

            self.status_legacy = QLabel("旧版：未启动")
            self.status_new = QLabel("新版：未启动")
            self.path_legacy = QLabel("路径：%s" % LEGACY_DIR)
            self.path_new = QLabel("路径：%s" % REPO_ROOT)
            for lbl in (self.status_legacy, self.status_new):
                lbl.setStyleSheet("font-weight: 600;")
            for lbl in (self.path_legacy, self.path_new):
                lbl.setStyleSheet("opacity: 0.75;")
                lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(self.status_legacy)
            layout.addWidget(self.path_legacy)
            layout.addWidget(self.status_new)
            layout.addWidget(self.path_new)

            btn_row = QHBoxLayout()
            self.btn_both = QPushButton("并排启动对比")
            self.btn_legacy = QPushButton("仅启动旧版")
            self.btn_new = QPushButton("仅启动新版")
            self.btn_stop = QPushButton("停止全部")
            self.btn_reexport = QPushButton("重新导出旧版")
            self.btn_both.clicked.connect(lambda: self.launch(both=True))
            self.btn_legacy.clicked.connect(lambda: self.launch("legacy"))
            self.btn_new.clicked.connect(lambda: self.launch("new"))
            self.btn_stop.clicked.connect(self.stop_all)
            self.btn_reexport.clicked.connect(self.reexport)
            for b in (self.btn_both, self.btn_legacy, self.btn_new,
                      self.btn_stop, self.btn_reexport):
                btn_row.addWidget(b)
            layout.addLayout(btn_row)

            self.log_box = QPlainTextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setMaximumBlockCount(500)
            layout.addWidget(self.log_box, 1)

            self.timer = QTimer(self)
            self.timer.setInterval(200)
            self.timer.timeout.connect(self._tick)
            self.timer.start()

            backup_settings_ini(self.log)
            self.reexport()

        # -- helpers ----------------------------------------------------
        def log(self, msg):
            stamp = datetime.now().strftime("%H:%M:%S")
            line = "[%s] %s" % (stamp, msg)
            self.log_box.appendPlainText(line)
            if os.environ.get("LIBJXL_COMPARE_ECHO") == "1":
                # .pyw 下 stdout 不可用，回显一律走 stderr。
                sys.stderr.write(line + "\n")
                sys.stderr.flush()

        def reexport(self):
            ok, info = export_legacy(LEGACY_REF, self.log)
            if ok:
                self.log("旧版副本就绪（%s）" % info)
                self.path_legacy.setText("路径：%s" % LEGACY_DIR)
            else:
                self.log("导出失败：%s" % info)

        def launch(self, which=None, both=False):
            if both:
                targets = [("legacy", "left"), ("new", "right")]
            else:
                targets = [(which, "left")]
            for name, side in targets:
                if name == "legacy":
                    if not os.path.isdir(os.path.join(LEGACY_DIR, "libjxl_gui")):
                        self.log("旧版副本不存在，先导出。")
                        self.reexport()
                        if not os.path.isdir(os.path.join(LEGACY_DIR, "libjxl_gui")):
                            self.log("旧版副本仍不可用，放弃启动。")
                            continue
                    cwd = LEGACY_DIR
                    attr = "legacy_proc"
                else:
                    cwd = REPO_ROOT
                    attr = "new_proc"
                existing = getattr(self, attr)
                if existing is not None and existing.poll() is None:
                    self.log("%s 已在运行（pid=%d），跳过。" % (name, existing.pid))
                    continue
                proc = _spawn(cwd, self.log)
                setattr(self, attr, proc)
                if proc is not None:
                    self.place_queue.append(
                        (proc, side, time.time() + 10.0, attr)
                    )

        def stop_all(self):
            for attr in ("legacy_proc", "new_proc"):
                proc = getattr(self, attr)
                if proc is not None and proc.poll() is None:
                    self.log("终止 pid=%d" % proc.pid)
                    proc.terminate()
                setattr(self, attr, None)
            self.place_queue.clear()

        def _tick(self):
            # 窗口定位
            still = []
            for proc, side, deadline, attr in self.place_queue:
                hwnd = place_window(proc.pid, side)
                if hwnd is not None:
                    self.log("%s 窗口已定位（%s半屏，hwnd=%d）" % (attr, side, hwnd))
                elif time.time() < deadline:
                    still.append((proc, side, deadline, attr))
                else:
                    self.log("%s 窗口定位超时，未找到可见顶层窗口。" % attr)
            self.place_queue = still

            # 状态刷新
            for attr, label, name in (
                ("legacy_proc", self.status_legacy, "旧版"),
                ("new_proc", self.status_new, "新版"),
            ):
                proc = getattr(self, attr)
                if proc is None:
                    state = "未启动"
                elif proc.poll() is None:
                    state = "运行中（pid=%d）" % proc.pid
                else:
                    state = "已退出（code=%s）" % proc.returncode
                    setattr(self, attr, None)
                label.setText("%s：%s" % (name, state))

        def closeEvent(self, event):
            running = any(
                getattr(self, a) is not None and getattr(self, a).poll() is None
                for a in ("legacy_proc", "new_proc")
            )
            if running:
                from PySide6.QtWidgets import QMessageBox
                ans = QMessageBox.question(
                    self, "退出对比器",
                    "两个对比窗口仍在运行。退出对比器不会关闭它们，是否继续？",
                )
                if ans != QMessageBox.Yes:
                    event.ignore()
                    return
            event.accept()

    app = QApplication.instance() or QApplication(sys.argv)
    win = Panel()
    win.show()
    # Escape hatch for automated smoke checks: build the whole panel but skip
    # the blocking event loop.
    if os.environ.get("LIBJXL_COMPARE_NO_EXEC") == "1":
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
