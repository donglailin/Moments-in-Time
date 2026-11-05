"""无 WebView2 时的浏览器降级模式。

在本机回环地址启动一个仅监听 127.0.0.1 的 HTTP 服务：
- 静态资源来自 web/ 目录；
- 把 Api 的白名单方法暴露为 POST /<token>/api/<方法名>；
- 页面内注入 bridge.js，构造 window.pywebview.api（fetch 转发）。

安全边界（重要）：
1. 只绑定 127.0.0.1；
2. 每次启动生成随机 token，所有静态资源与 API 都必须在 URL 路径中携带 token，
   防止本机其它网页/进程盲扫端口后调用接口（CSRF），也避免地址文件被替换后
   打开任意本地页面；
3. 校验 Host 头必须严格等于 127.0.0.1:<端口> 或 localhost:<端口>，阻止
   DNS rebinding 把恶意域名解析到本机后读取接口响应；
4. 校验 Origin 头（若存在）必须同源；API 仅接受 application/json；
5. API 走显式白名单，不再用 getattr 反射任意公开属性；
6. 强制空闲退出与退出请求都会等待进行中的扫描/导出结束，避免中途杀进程。
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

_ROOT_REDIRECT = object()  # 仅 token、无尾斜杠：需要重定向到 /<token>/
MAX_BODY_BYTES = 16 * 1024 * 1024
QUIT_WAIT_SECONDS = 8.0

# 只暴露前端实际使用的接口（新增公开方法不会自动暴露给浏览器）
API_ALLOWLIST = frozenset(
    {
        "bootstrap",
        "pick_folder",
        "check_path",
        "save_settings",
        "start_scan",
        "scan_result",
        "start_export",
        "export_result",
        "poll",
        "cancel",
        "reveal",
        "open_path",
        "quit",
    }
)


BRIDGE_JS = r"""
(function () {
  if (window.pywebview && window.pywebview.api) return;
  // 相对路径：页面地址为 /<token>/，因此解析到 /<token>/api/<name>
  const call = (name, args) => fetch('api/' + encodeURIComponent(name), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args: args })
  }).then((r) => r.json());
  window.pywebview = {
    api: new Proxy({}, {
      get: (_target, name) => (...args) => call(String(name), args)
    })
  };
  setInterval(() => { call('heartbeat', []).catch(() => {}); }, 5000);
})();
"""


class BrowserModeServer:
    def __init__(
        self,
        api,
        web_dir: str,
        reason: str,
        download_url: str,
        idle_timeout: float = 120.0,
        token: str | None = None,
    ) -> None:
        self.api = api
        self.web_dir = os.path.abspath(web_dir)
        self.reason = reason
        self.download_url = download_url
        self.idle_timeout = idle_timeout
        self.token = token or secrets.token_urlsafe(24)
        self._last_heartbeat = time.time()
        self._stop = threading.Event()
        self._quit_thread: threading.Thread | None = None
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self._origin = f"http://127.0.0.1:{self.port}"
        self.url = f"{self._origin}/{self.token}/"

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> str:
        threading.Thread(target=self._httpd.serve_forever, name="browser-http", daemon=True).start()
        threading.Thread(target=self._idle_watch, name="browser-idle", daemon=True).start()
        return self.url

    def _busy(self) -> bool:
        probe = getattr(self.api, "_busy", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:  # noqa: BLE001
                return False
        return False

    def _idle_watch(self) -> None:
        while not self._stop.wait(1.0):
            if time.time() - self._last_heartbeat <= self.idle_timeout:
                continue
            if self._busy():
                # 页面已关闭但扫描/导出仍在进行：先让任务做完，再退出
                continue
            self.shutdown()
            return

    def wait(self) -> None:
        self._stop.wait()

    def _shutdown_after_jobs(self) -> None:
        """退出请求：先取消任务，等任务收敛（最多 QUIT_WAIT_SECONDS 秒）再关服务。"""
        for kind in ("scan", "export"):
            try:
                self.api.cancel(kind)
            except Exception:  # noqa: BLE001
                pass
        deadline = time.time() + QUIT_WAIT_SECONDS
        while time.time() < deadline and self._busy():
            time.sleep(0.1)
        self.shutdown()

    def request_quit(self) -> None:
        if self._quit_thread is not None:
            return
        self._quit_thread = threading.Thread(
            target=self._shutdown_after_jobs, name="browser-quit", daemon=True
        )
        self._quit_thread.start()

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 请求处理
    def _build_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ShiJiBrowser/1.0"

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                pass  # 静默，不输出访问日志

            # ---------- 安全校验
            def _trusted(self) -> bool:
                host = (self.headers.get("Host") or "").strip().lower()
                allowed_hosts = {
                    f"127.0.0.1:{outer.port}",
                    f"localhost:{outer.port}",
                }
                if host not in allowed_hosts:
                    return False
                origin = (self.headers.get("Origin") or "").strip().lower()
                if origin and origin not in allowed_hosts and origin not in {
                    f"http://127.0.0.1:{outer.port}",
                    f"http://localhost:{outer.port}",
                }:
                    return False
                return True

            def _strip_token(self):
                """返回 token 之后的相对路径；仅 token 时返回 _ROOT_REDIRECT；不匹配返回 None。"""
                raw = unquote(urlparse(self.path).path)
                prefix = "/" + outer.token
                if raw == prefix:
                    return _ROOT_REDIRECT
                if raw.startswith(prefix + "/"):
                    return raw[len(prefix) + 1 :]
                return None

            def _base_headers(self, code: int, length: int, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self._base_headers(code, len(body), ctype)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def _json(self, code: int, data: object) -> None:
                self._send(
                    code,
                    json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )

            def _deny(self, code: int = 403, message: str = "Forbidden") -> None:
                self._send(code, message.encode("utf-8"), "text/plain; charset=utf-8")

            def _redirect(self, location: str) -> None:
                self._base_headers(302, 0, "text/plain; charset=utf-8")
                self.send_header("Location", location)
                self.end_headers()

            # ---------- GET：静态资源
            def do_GET(self) -> None:  # noqa: N802
                if not self._trusted():
                    self._deny()
                    return
                rest = self._strip_token()
                if rest is None:
                    self._deny(403, "Forbidden")
                    return
                if rest is _ROOT_REDIRECT:
                    # /<token> 补斜杠，保证页面内相对 fetch 解析到 /<token>/api/...
                    self._redirect(outer.url)
                    return
                if rest == "" or rest == "index.html":
                    self._serve_index()
                    return
                rel = rest.lstrip("/")
                full = os.path.abspath(os.path.join(outer.web_dir, *rel.split("/")))
                try:
                    inside = os.path.commonpath([full, outer.web_dir]) == outer.web_dir
                except ValueError:
                    inside = False
                if not inside or not os.path.isfile(full):
                    self._deny(404, "Not Found")
                    return
                try:
                    with open(full, "rb") as fh:
                        body = fh.read()
                except OSError:
                    self._deny(404, "Not Found")
                    return
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "css")):
                    ctype += "; charset=utf-8"
                self._send(200, body, ctype)

            def _serve_index(self) -> None:
                index = os.path.join(outer.web_dir, "index.html")
                try:
                    with open(index, "r", encoding="utf-8") as fh:
                        html = fh.read()
                except OSError:
                    self._deny(500, "index.html 不可用")
                    return
                bridge = f"<script>{BRIDGE_JS}</script>"
                html = html.replace("<!--TFP_BRIDGE-->", bridge)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

            # ---------- POST：API
            def do_POST(self) -> None:  # noqa: N802
                if not self._trusted():
                    self._deny()
                    return
                rest = self._strip_token()
                if rest is None or rest is _ROOT_REDIRECT or not rest.startswith("api/"):
                    self._deny(404, "Not Found")
                    return
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    self._deny(415, "仅接受 application/json")
                    return
                name = rest[len("api/"):].strip("/")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    self._deny(400, "Content-Length 非法")
                    return
                if length <= 0 or length > MAX_BODY_BYTES:
                    self._deny(413, "请求体为空或过大")
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, json.JSONDecodeError):
                    self._json(400, {"ok": False, "message": "请求体不是合法 JSON"})
                    return
                if not isinstance(payload, dict):
                    self._json(400, {"ok": False, "message": "请求体必须是对象"})
                    return

                if name == "heartbeat":
                    outer._last_heartbeat = time.time()
                    self._json(200, {"ok": True})
                    return

                if name not in API_ALLOWLIST:
                    self._json(404, {"ok": False, "message": "未知接口"})
                    return

                args = payload.get("args") or []
                if not isinstance(args, list):
                    self._json(400, {"ok": False, "message": "args 必须是数组"})
                    return
                method = getattr(outer.api, name, None)
                if not callable(method):
                    self._json(404, {"ok": False, "message": "未知接口"})
                    return
                try:
                    result = method(*args)
                except Exception as exc:  # noqa: BLE001
                    self._json(200, {"ok": False, "message": f"{type(exc).__name__}: {exc}"})
                    return
                try:
                    body = result if isinstance(result, dict) else {"ok": True, "data": result}
                    self._json(200, body)
                except (TypeError, ValueError) as exc:  # noqa: BLE001
                    self._json(500, {"ok": False, "message": f"结果无法序列化：{type(exc).__name__}"})
                if name == "quit":
                    outer.request_quit()

        return Handler
