"""导出：复制 / 剪切文件到目标文件夹，处理重名冲突。"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any

import fileutil
from job import Job, JobCancelled

UPDATE_EVERY = 20


def _safe_rel(rel: str) -> str:
    rel = os.path.normpath(rel or "")
    if os.path.isabs(rel) or rel.startswith(".."):
        rel = os.path.basename(rel)
    return rel


def _unique_name(dest_dir: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    index = 1
    while True:
        candidate = f"{stem} ({index}){ext}"
        if not fileutil.exists(os.path.join(dest_dir, candidate)):
            return candidate
        index += 1


def _temp_target(target: str, idx: int) -> str:
    """目标目录内的临时文件名：先写临时文件再原子替换，避免半成品/覆盖丢数据。"""
    folder, name = os.path.split(target)
    return os.path.join(folder, f".{name}.shiji-tmp-{os.getpid()}-{idx}")


def _copy_atomic(src: str, target: str, idx: int) -> None:
    """复制到临时文件后 os.replace，失败时清理临时文件，原目标文件不受影响。"""
    tmp = _temp_target(target, idx)
    try:
        shutil.copy2(fileutil.long_path(src), fileutil.long_path(tmp))
        os.replace(fileutil.long_path(tmp), fileutil.long_path(target))
    except Exception:
        try:
            os.remove(fileutil.long_path(tmp))
        except OSError:
            pass
        raise


def _move_cross_volume(src: str, target: str, idx: int, overwrite: bool) -> None:
    """跨盘剪切：复制成功并替换目标后再删源；删源失败时尽量回滚，保证不丢源文件。"""
    tmp = _temp_target(target, idx)
    try:
        shutil.copy2(fileutil.long_path(src), fileutil.long_path(tmp))
        os.replace(fileutil.long_path(tmp), fileutil.long_path(target))
    except Exception:
        try:
            os.remove(fileutil.long_path(tmp))
        except OSError:
            pass
        raise
    try:
        os.remove(fileutil.long_path(src))
    except OSError as exc:
        if not overwrite:
            try:
                os.remove(fileutil.long_path(target))
            except OSError:
                pass
        raise OSError(f"已复制到目标，但删除源文件失败：{exc}") from exc


def export_files(
    items: list[dict[str, Any]],
    dest: str,
    *,
    mode: str = "copy",
    preserve: bool = True,
    conflict: str = "rename",
    job: Job,
) -> dict[str, Any]:
    started = time.perf_counter()
    total = len(items)
    job.update(phase="导出", total=total, processed=0, message=f"准备导出 {total:,} 个文件")
    try:
        fileutil.ensure_dir(dest)
    except OSError:
        pass

    succeeded = skipped = failed = 0
    processed = 0
    bytes_done = 0
    failures: list[dict[str, str]] = []
    exported_ids: list[int] = []
    cancelled = False

    for idx, item in enumerate(items, 1):
        src = item["path"]
        name = item["name"]
        rel = _safe_rel(item["rel"])
        target_dir = os.path.join(dest, os.path.dirname(rel)) if preserve else dest
        status = "成功"
        target = ""
        overwrite = False

        try:
            if job.cancelled:
                raise JobCancelled()
            fileutil.ensure_dir(target_dir)
            target = os.path.join(target_dir, name)

            if fileutil.exists(target):
                try:
                    same_file = os.path.samefile(fileutil.long_path(target), fileutil.long_path(src))
                except OSError:
                    same_file = os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(src))
            else:
                same_file = False

            if same_file:
                status = "跳过"
            elif fileutil.exists(target):
                if conflict == "skip":
                    status = "跳过"
                elif conflict == "overwrite":
                    if fileutil.is_dir(target):
                        raise IsADirectoryError(f"目标位置已存在同名文件夹：{name}")
                    # 不预先删除目标：由后面的原子替换覆盖；复制/移动失败时旧文件仍在
                    overwrite = True
                else:
                    new_name = _unique_name(target_dir, name)
                    target = os.path.join(target_dir, new_name)

            if status == "跳过":
                skipped += 1
            elif mode == "move":
                # 同盘优先原子重命名（覆盖模式也由 os.replace 原子完成）；
                # 跨盘回退为「复制到临时文件 → 替换目标 → 删除源文件」。
                try:
                    os.replace(fileutil.long_path(src), fileutil.long_path(target))
                except OSError:
                    _move_cross_volume(src, target, idx, overwrite)
                succeeded += 1
                exported_ids.append(item["id"])
                bytes_done += int(item.get("size") or 0)
            else:
                _copy_atomic(src, target, idx)
                succeeded += 1
                exported_ids.append(item["id"])
                bytes_done += int(item.get("size") or 0)
        except JobCancelled:
            cancelled = True
            break
        except Exception as exc:  # 单个文件失败不应中断整批
            failed += 1
            status = "失败"
            if len(failures) < 30:
                failures.append({"name": name, "path": src, "error": str(exc)})

        processed += 1

        if idx % UPDATE_EVERY == 0 or idx == total:
            job.update(processed=idx, current=item["rel"], message=f"已处理 {idx:,} / {total:,}")

    duration = int((time.perf_counter() - started) * 1000)
    result = {
        "dest": dest,
        "mode": mode,
        "preserve": preserve,
        "conflict": conflict,
        "total": total,
        "processed": processed,
        "succeeded": succeeded,
        "skipped": skipped,
        "failed": failed,
        "bytes": bytes_done,
        "cancelled": cancelled,
        "exported_ids": exported_ids,
        "failures": failures,
        "duration_ms": duration,
    }
    return result


__all__ = ["export_files", "JobCancelled"]
