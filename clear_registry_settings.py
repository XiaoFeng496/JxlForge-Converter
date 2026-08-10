# -*- coding: utf-8 -*-
r"""清除 libjxl GUI 早期版本写入 Windows 注册表的持久化数据。

早期版本使用 QSettings 的原生格式（Windows 注册表）保存设置，
键路径为：HKEY_CURRENT_USER\Software\libjxl\libjxl-gui

新版已改用 .ini 文件（见 libjxl_gui/__main__.py 中的
QSettings.setDefaultFormat(QSettings.IniFormat)），本脚本仅用于
一次性清理迁移后遗留在注册表中的旧数据，不影响新版的 .ini 文件。

用法：
    python clear_registry_settings.py
（无需管理员权限，因为只操作 HKEY_CURRENT_USER。）
"""

import sys

try:
    import winreg
except ImportError:
    print("本脚本仅支持 Windows 系统，无法导入 winreg 模块。")
    sys.exit(1)


ORG = "libjxl"
APP = "libjxl-gui"

# 同时检查 32 位与 64 位视图，覆盖不同 Python 位数运行过旧版的情况。
TARGETS = [
    (winreg.HKEY_CURRENT_USER, r"Software\libjxl\libjxl-gui"),
    (winreg.HKEY_CURRENT_USER, r"Software\Wow6432Node\libjxl\libjxl-gui"),
]


def _del_tree(root, subpath):
    """递归删除注册表键（含所有子键）。返回 True 表示已删除。"""
    try:
        handle = winreg.OpenKey(
            root, subpath, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    except FileNotFoundError:
        return False
    # 先删除所有子键，再删除自身。
    while True:
        try:
            name = winreg.EnumKey(handle, 0)
        except OSError:
            break
        _del_tree(handle, name)
    winreg.CloseKey(handle)
    winreg.DeleteKey(root, subpath)
    return True


def _remove_empty_org(root, base):
    """若 org 键下已无子键，顺手删掉这个空的父键。"""
    org_path = r"%s\%s" % (base, ORG)
    try:
        handle = winreg.OpenKey(
            root, org_path, 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
    except FileNotFoundError:
        return
    try:
        subkey_count = winreg.QueryInfoKey(handle)[0]
    except OSError:
        subkey_count = 0
    winreg.CloseKey(handle)
    if subkey_count == 0:
        try:
            winreg.DeleteKey(root, org_path)
            print("已删除空的父键：%s\\%s" % (base, ORG))
        except OSError:
            pass


def main():
    removed_any = False
    for root, target in TARGETS:
        if _del_tree(root, target):
            print("已删除：HKEY_CURRENT_USER\\%s" % target)
            removed_any = True
        else:
            print("未找到（跳过）：HKEY_CURRENT_USER\\%s" % target)

    # 清理可能遗留的空 org 父键（Software\libjxl 与 Software\Wow6432Node\libjxl）。
    _remove_empty_org(winreg.HKEY_CURRENT_USER, r"Software")
    _remove_empty_org(winreg.HKEY_CURRENT_USER, r"Software\Wow6432Node")

    if removed_any:
        print("\n完成：旧注册表持久化数据已清除。")
    else:
        print("\n完成：没有发现需要清理的旧注册表数据（已是干净状态）。")


if __name__ == "__main__":
    main()
