"""扫描：按修改时间筛选文件夹内的文件。

分两个阶段，便于给出可信的进度：
  1. 枚举 + 读取元数据（scandir 的 stat 结果会被缓存，不产生额外 IO）
  2. 按时间范围筛选（纯内存计算）
"""

from __future__ import annotations

import os
import time
from typing import Any

import fileutil
from job import Job, JobCancelled

MAX_FILES = 100_000          # 结果上限，避免极端目录把内存吃光
UPDATE_EVERY = 200           # 每处理多少个文件回写一次进度
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def normalize_extensions(value: list[str] | str | None) -> list[str]:
    """把 "jpg, pdf；PNG" 之类的输入统一成不带点的小写扩展名列表。"""
    if isinstance(value, str):
        raw = value
        for sep in ("，", "；", "、", ";", "|", "\n", "\t"):
            raw = raw.replace(sep, ",")
        value = raw.split(",")
    return [item.strip().lstrip(".").lower() for item in (value or []) if item and item.strip()]


def _should_include(name: str, ext: str, extensions: set[str], keyword: str) -> bool:
    if extensions and ext not in extensions:
        return False
    if keyword and keyword not in name.lower():
        return False
    return True


def _is_reparse(entry: os.DirEntry) -> bool:
    """符号链接 / 目录联接（junction）都带重解析点，递归时必须跳过以免死循环。"""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def scan_folder(
    root: str,
    *,
    recursive: bool = True,
    start_ms: int | None = None,
    end_ms: int | None = None,
    extensions: list[str] | str | None = None,
    keyword: str = "",
    job: Job,
) -> dict[str, Any]:
    started = time.perf_counter()
    exts = set(normalize_extensions(extensions))
    keyword = (keyword or "").strip().lower()
    errors: list[dict[str, str]] = []
    error_count = 0

    def note_error(path: str, err: BaseException) -> None:
        nonlocal error_count
        error_count += 1
        if len(errors) < 30:
            errors.append({"path": fileutil.display_path(path), "error": str(err)})

    # ---------- 阶段 1：枚举 ----------
    job.update(phase="枚举文件", message="正在遍历文件夹…", processed=0, total=0)
    root_lp = fileutil.long_path(root)
    stack: list[tuple[str, str]] = [(root_lp, "")]
    candidates: list[tuple[str, str, str, str, int, int]] = []
    dirs_seen = 0
    truncated = False

    while stack:
        job.check_cancel()
        dir_lp, rel_dir = stack.pop()
        try:
            with os.scandir(dir_lp) as it:
                entries = list(it)
        except OSError as exc:
            note_error(dir_lp, exc)
            continue

        dirs_seen += 1
        for pos, entry in enumerate(entries, 1):
            if pos % 512 == 0:
                # 单个超大目录也要能及时取消，不必等到下一层
                job.check_cancel()
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            rel = os.path.join(rel_dir, entry.name) if rel_dir else entry.name
            if is_dir:
                # 不跟随符号链接 / 目录联接，避免循环目录
                if recursive and not _is_reparse(entry):
                    stack.append((entry.path, rel))
                continue

            name = entry.name
            ext = os.path.splitext(name)[1].lstrip(".").lower()
            if not _should_include(name.lower(), ext, exts, keyword):
                continue

            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                note_error(entry.path, exc)
                continue

            mtime_ms = int(st.st_mtime * 1000)
            if start_ms is not None and mtime_ms < start_ms:
                continue
            if end_ms is not None and mtime_ms >= end_ms:
                continue

            candidates.append(
                (entry.path, rel, name, ext, int(st.st_size), mtime_ms)
            )
            if len(candidates) >= MAX_FILES:
                truncated = True
                stack.clear()
                break

        job.update(
            processed=len(candidates),
            message=f"已发现 {len(candidates):,} 个文件 · 遍历 {dirs_seen:,} 个文件夹",
        )

    # ---------- 阶段 2：整理 ----------
    total = len(candidates)
    job.update(total=total, processed=0, phase="读取信息", message=f"共 {total:,} 个待筛选文件")
    items: list[dict[str, Any]] = []
    total_size = 0

    for idx, (full, rel, name, ext, size, mtime_ms) in enumerate(candidates, 1):
        if idx % UPDATE_EVERY == 0 or idx == total:
            job.check_cancel()
            job.update(processed=idx, current=rel)
        total_size += size
        items.append(
            {
                "id": idx,          # 排序后重新编号
                "name": name,
                "rel": rel,
                "path": fileutil.display_path(full),
                "dir": fileutil.display_path(os.path.dirname(full)),
                "ext": ext,
                "size": size,
                "mtime": mtime_ms,
            }
        )

    # ---------- 阶段 3：排序 ----------
    job.update(phase="整理结果", percent=99.0, message="按修改时间排序…")
    items.sort(key=lambda it: (-it["mtime"], it["name"].lower()))
    for i, item in enumerate(items, 1):
        item["id"] = i

    duration = int((time.perf_counter() - started) * 1000)
    return {
        "items": items,
        "total": len(items),
        "total_size": total_size,
        "dirs_seen": dirs_seen,
        "truncated": truncated,
        "errors": errors,
        "error_count": error_count,
        "duration_ms": duration,
    }


__all__ = ["scan_folder", "normalize_extensions", "MAX_FILES", "JobCancelled"]
