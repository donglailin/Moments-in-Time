"""路径与文件系统辅助工具。

Windows 上超过 260 字符的路径需要加 \\\\?\\ 前缀才能正常访问，
这里统一在真正调用文件系统 API 时补前缀，界面展示仍用普通路径。
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys


def long_path(path: str) -> str:
    """返回可安全用于文件系统 API 的绝对路径（超长时加 \\\\?\\ 前缀）。"""
    if not path:
        return path
    if path.startswith("\\\\?\\"):
        return path
    p = os.path.abspath(path)
    if len(p) < 240:
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def display_path(path: str) -> str:
    """把可能带 \\\\?\\ 前缀的路径还原成便于展示的形式。"""
    if not path:
        return path
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def human_size(num: int | float) -> str:
    num = float(num or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while num >= 1024 and idx < len(units) - 1:
        num /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(num)} B"
    return f"{num:.1f} {units[idx]}"


def format_mtime(epoch_ms: int | float) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(epoch_ms) / 1000.0).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "-"


def is_dir(path: str) -> bool:
    try:
        return os.path.isdir(long_path(path))
    except (OSError, ValueError):
        return False


def exists(path: str) -> bool:
    try:
        return os.path.exists(long_path(path))
    except (OSError, ValueError):
        return False


def ensure_dir(path: str) -> None:
    os.makedirs(long_path(path), exist_ok=True)


def dir_writable(path: str) -> bool:
    """探测目录是否可写（不写注册表，仅创建并删除临时文件）。"""
    probe = os.path.join(path, f".tfp_write_test_{os.getpid()}")
    try:
        os.makedirs(long_path(path), exist_ok=True)
        with open(long_path(probe), "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(long_path(probe))
        return True
    except OSError:
        try:
            os.remove(long_path(probe))
        except OSError:
            pass
        return False


def free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(long_path(path)).free
    except OSError:
        return -1


def reveal_in_explorer(path: str) -> None:
    """在资源管理器中选中某个文件。"""
    path = os.path.abspath(display_path(path))
    if os.path.isdir(path):
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    # explorer 的 /select 参数对逗号敏感，用引号包裹路径；CREATE_NO_WINDOW 避免闪出黑框
    subprocess.Popen(f'explorer /select,"{path}"', close_fds=True, creationflags=0x08000000)


def open_path(path: str) -> None:
    """用系统默认方式打开文件或文件夹。"""
    path = os.path.abspath(display_path(path))
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    os.startfile(path)  # type: ignore[attr-defined]


def app_dir() -> str:
    """程序所在目录（打包后为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts: str) -> str:
    """读取随程序发布的资源（兼容 PyInstaller 单文件解包目录）。"""
    base = getattr(sys, "_MEIPASS", app_dir())
    return os.path.join(base, *parts)
