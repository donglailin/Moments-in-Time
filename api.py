"""前端可调用的桥接接口（pywebview js_api）。"""

from __future__ import annotations

import os
import threading
from typing import Any

import webview

import exporter
import fileutil
import scanner
import store
from job import Job, JobCancelled

VERSION = "1.0.0"


def _err(message: str, **extra: Any) -> dict[str, Any]:
    data = {"ok": False, "message": message}
    data.update(extra)
    return data


def _as_int(value: Any) -> int | None:
    """前端可能传 null / 空串 / 浮点，统一安全转换，避免 int() 抛异常。"""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class Api:
    def __init__(self) -> None:
        # 注意：窗口句柄必须用私有名。pywebview 注入 API 时会递归反射所有公开属性，
        # 若公开保存 Window 对象，会一路反射到 .NET WebView2 的 COM 属性上，
        # 在非 UI 线程读取这些属性会抛 E_NOINTERFACE 甚至死锁（表现为启动长时间无响应）。
        self._window: Any = None
        self.browser_mode = False
        self.mode_reason = ""
        self.settings = store.load()
        self._scan_job: Job | None = None
        self._export_job: Job | None = None
        self._scan_result: dict[str, Any] | None = None
        self._export_result: dict[str, Any] | None = None
        self._items: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 基础
    def bootstrap(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": VERSION,
            "settings": self.settings,
            "settingsPath": store.config_path(),
            "portable": store.is_portable(),
            "configDir": os.path.dirname(store.config_path()),
            "browserMode": bool(self.browser_mode),
            "modeReason": self.mode_reason,
            "downloadUrl": "https://go.microsoft.com/fwlink/p/?LinkId=2124703",
        }

    def pick_folder(self, kind: str = "source") -> dict[str, Any]:
        if self.browser_mode:
            return {"ok": False, "browser": True, "message": "浏览器模式请手动输入绝对路径"}
        initial = self.settings.get(kind) or ""
        if not fileutil.is_dir(initial):
            initial = os.path.expanduser("~")
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER, directory=initial, allow_multiple=False
        )
        if not result:
            return {"ok": False, "message": "已取消"}
        return {"ok": True, "path": result[0]}

    def check_path(self, path: str) -> dict[str, Any]:
        exists = os.path.isdir(fileutil.long_path(path)) if path else False
        return {"ok": True, "exists": exists}

    def save_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            for key in store.DEFAULT_SETTINGS:
                if key in data:
                    self.settings[key] = data[key]
            path = store.save(self.settings)
            return {"ok": True, "path": path}
        except Exception as exc:
            return _err(f"保存设置失败：{exc}")

    # ------------------------------------------------------------------ 扫描
    def start_scan(self, options: dict[str, Any]) -> dict[str, Any]:
        if self._export_job is not None and self._export_job.snapshot()["status"] == "running":
            return _err("正在导出文件，请等待导出完成后再扫描。")

        root = str(options.get("root") or "").strip()
        if not fileutil.is_dir(root):
            return _err("源文件夹不存在或不可访问，请重新选择。")

        start_ms = _as_int(options.get("startMs"))
        end_ms = _as_int(options.get("endMs"))
        if start_ms is not None and end_ms is not None and end_ms <= start_ms:
            return _err("时间范围的结束时间必须晚于开始时间。")

        extensions = scanner.normalize_extensions(options.get("extensions"))
        keyword = str(options.get("keyword") or "")

        # 开启新扫描：终止上一个
        if self._scan_job is not None:
            self._scan_job.cancel()

        job = Job("scan")
        self._scan_job = job
        self._scan_result = None
        with self._lock:
            self._items = {}

        def worker() -> None:
            try:
                result = scanner.scan_folder(
                    root,
                    recursive=bool(options.get("recursive", True)),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    extensions=extensions,
                    keyword=keyword,
                    job=job,
                )
                if self._scan_job is not job:  # 期间又发起了新扫描，丢弃过期结果
                    return
                with self._lock:
                    self._items = {item["id"]: item for item in result["items"]}
                self._scan_result = result
                job.finish("done", message=f"扫描完成，共 {result['total']:,} 个文件")
            except JobCancelled:
                job.finish("cancelled", message="已取消扫描")
            except Exception as exc:  # noqa: BLE001
                job.finish("error", error=f"{type(exc).__name__}: {exc}", message="扫描失败")

        threading.Thread(target=worker, daemon=True, name="scan-worker").start()

        self._settings_side_effect(options)
        return {"ok": True}

    def scan_result(self) -> dict[str, Any]:
        if not self._scan_result:
            return {"ok": False, "message": "暂无扫描结果"}
        data = dict(self._scan_result)
        data["ok"] = True
        return data

    # ------------------------------------------------------------------ 导出
    def start_export(self, options: dict[str, Any]) -> dict[str, Any]:
        if self._scan_job is not None and self._scan_job.snapshot()["status"] == "running":
            return _err("正在扫描文件，请等待扫描完成。")
        if self._export_job is not None and self._export_job.snapshot()["status"] == "running":
            return _err("已有导出任务正在进行。")

        dest = str(options.get("dest") or "").strip()
        if not dest:
            return _err("请先选择导出文件夹。")
        try:
            fileutil.ensure_dir(dest)
        except OSError as exc:
            return _err(f"导出文件夹不可用：{exc}")
        if not fileutil.is_dir(dest):
            return _err("导出路径不是文件夹。")

        ids = options.get("ids") or []
        wanted = {i for i in (_as_int(x) for x in ids) if i is not None}
        with self._lock:
            items = [self._items[i] for i in sorted(wanted) if i in self._items]
        if not items:
            return _err("没有可导出的文件，请先勾选文件。")

        mode = options.get("mode") if options.get("mode") in ("copy", "move") else "copy"
        conflict = options.get("conflict")
        if conflict not in ("skip", "rename", "overwrite"):
            conflict = "rename"

        if mode == "copy":
            need = sum(int(item.get("size") or 0) for item in items)
            free = fileutil.free_bytes(dest)
            if free > 0 and need > free:
                return _err(
                    f"磁盘剩余空间不足：需要 {fileutil.human_size(need)}，"
                    f"目标磁盘仅剩 {fileutil.human_size(free)}。"
                )

        job = Job("export")
        self._export_job = job
        self._export_result = None

        def worker() -> None:
            try:
                result = exporter.export_files(
                    items,
                    dest,
                    mode=mode,
                    preserve=bool(options.get("preserve", True)),
                    conflict=conflict,
                    job=job,
                )
                if self._export_job is not job:
                    return
                self._export_result = result
                job.finish(
                    "cancelled" if result["cancelled"] else "done",
                    message=(
                        f"已取消，成功 {result['succeeded']} 个"
                        if result["cancelled"]
                        else f"导出完成：成功 {result['succeeded']} 个"
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                job.finish("error", error=f"{type(exc).__name__}: {exc}", message="导出失败")

        threading.Thread(target=worker, daemon=True, name="export-worker").start()

        self._settings_side_effect({"dest": dest, "mode": mode, "preserve": bool(options.get("preserve", True)), "conflict": conflict})
        return {"ok": True}

    def export_result(self) -> dict[str, Any]:
        if not self._export_result:
            return {"ok": False, "message": "暂无导出结果"}
        data = dict(self._export_result)
        data["ok"] = True
        return data

    # ------------------------------------------------------------------ 其它
    def poll(self) -> dict[str, Any]:
        return {
            "ok": True,
            "scan": self._scan_job.snapshot() if self._scan_job else None,
            "export": self._export_job.snapshot() if self._export_job else None,
        }

    def cancel(self, kind: str) -> dict[str, Any]:
        job = self._scan_job if kind == "scan" else self._export_job
        if job is None:
            return {"ok": False, "message": "没有正在进行的任务"}
        job.cancel()
        return {"ok": True}

    def reveal(self, path: str) -> dict[str, Any]:
        try:
            fileutil.reveal_in_explorer(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return _err(f"无法定位文件：{exc}")

    def open_path(self, path: str) -> dict[str, Any]:
        try:
            fileutil.open_path(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return _err(f"无法打开：{exc}")

    def quit(self) -> dict[str, Any]:
        # 浏览器模式下没有原生窗口，_window 为 None，这里的窗口调用安全跳过；
        # 真正的服务关闭由 browser_mode 的 quit 处理逻辑负责。
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception as exc:  # noqa: BLE001
            return _err(f"关闭窗口失败：{exc}")
        return {"ok": True, "browser": bool(self.browser_mode)}

    # ------------------------------------------------------------------ 内部
    def _busy(self) -> bool:
        """是否有扫描/导出正在运行（浏览器降级的空闲退出据此让路）。"""
        for job in (self._scan_job, self._export_job):
            if job is not None and job.snapshot()["status"] == "running":
                return True
        return False


    def _settings_side_effect(self, data: dict[str, Any]) -> None:
        """把最近一次操作的关键参数并入设置并异步落盘。"""
        for key, value in data.items():
            if key in store.DEFAULT_SETTINGS:
                self.settings[key] = value

        def flush() -> None:
            try:
                store.save(self.settings)
            except Exception:  # noqa: BLE001
                pass  # 设置落盘失败不影响本次操作结果

        threading.Thread(target=flush, daemon=True).start()

    def on_files_dropped(self, event: dict[str, Any]) -> None:
        """pywebview 的拖拽回调，转为 JS 调用。"""
        paths: list[dict[str, Any]] = []
        try:
            for file in (event.get("dataTransfer") or {}).get("files") or []:
                raw = file.get("pywebviewFullPath") or ""
                if not raw:
                    continue
                path = fileutil.display_path(raw)
                is_dir = os.path.isdir(fileutil.long_path(path))
                paths.append(
                    {
                        "path": path,
                        "isDir": is_dir,
                        "dir": path if is_dir else os.path.dirname(path),
                        "name": "" if is_dir else os.path.basename(path),
                    }
                )
        except Exception:  # noqa: BLE001
            pass  # 拖拽数据结构异常时按无文件处理
        if not paths or self._window is None:
            return
        import json

        try:
            self._window.evaluate_js(
                f"window.__tfpDrop && window.__tfpDrop({json.dumps(paths, ensure_ascii=False)})"
            )
        except Exception:  # noqa: BLE001
            pass  # 窗口关闭时注入失败，忽略
