"""设置持久化。

优先写入程序所在目录（便于便携使用/U 盘带走），目录不可写时回退到
%APPDATA%\\ShiJi\\settings.json。完全不使用注册表。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import fileutil

DEFAULT_SETTINGS = {
    "source": "",
    "dest": "",
    "preset": "week",
    "customStart": "",
    "customEnd": "",
    "recursive": True,
    "extensions": "",
    "keyword": "",
    "mode": "copy",
    "preserve": True,
    "conflict": "rename",
    "sort": "mtime_desc",
    "typeFilter": "all",
    "search": "",
}


_CONFIG_PATH: str | None = None
_SAVE_LOCK = threading.Lock()


def _appdata_path() -> str:
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "ShiJi", "settings.json")


def config_path() -> str:
    """返回实际（或将要）使用的配置文件路径。

    规则：
      1. 程序目录已有 settings.json → 便携模式，直接沿用；
      2. 打包运行且程序目录可写 → 写在 exe 旁边（U 盘带走可用）；
      3. 其它情况 → %APPDATA%\\ShiJi\\settings.json。
    """
    global _CONFIG_PATH
    if _CONFIG_PATH:
        return _CONFIG_PATH

    local = os.path.join(fileutil.app_dir(), "settings.json")
    if os.path.isfile(local):
        _CONFIG_PATH = local
    elif getattr(sys, "frozen", False) and fileutil.dir_writable(fileutil.app_dir()):
        _CONFIG_PATH = local
    else:
        _CONFIG_PATH = _appdata_path()
    return _CONFIG_PATH


def load() -> dict:
    path = config_path()
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(fileutil.long_path(path), "r", encoding="utf-8-sig") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key in DEFAULT_SETTINGS and isinstance(value, type(DEFAULT_SETTINGS[key])):
                    data[key] = value
                elif key in DEFAULT_SETTINGS and DEFAULT_SETTINGS[key] == "" and isinstance(value, str):
                    data[key] = value
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> str:
    """原子写入；多线程调用（前端防抖保存）时串行化，避免共享临时文件互相覆盖。"""
    path = config_path()
    payload = {key: data.get(key, DEFAULT_SETTINGS[key]) for key in DEFAULT_SETTINGS}
    with _SAVE_LOCK:
        folder = os.path.dirname(path)
        try:
            os.makedirs(fileutil.long_path(folder), exist_ok=True)
        except OSError:
            pass
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(fileutil.long_path(tmp), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        last_error: OSError | None = None
        for attempt in range(4):
            try:
                os.replace(fileutil.long_path(tmp), fileutil.long_path(path))
                return path
            except OSError as exc:  # 杀毒软件/索引服务可能短暂占用目标文件
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        try:
            os.remove(fileutil.long_path(tmp))
        except OSError:
            pass
        if last_error:
            raise last_error
        return path


def is_portable() -> bool:
    """配置文件是否存放在程序目录（便携模式）。"""
    return os.path.normcase(config_path()).startswith(os.path.normcase(fileutil.app_dir() + os.sep))
