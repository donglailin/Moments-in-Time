"""浏览器降级 HTTP 服务的安全边界与生命周期测试。

覆盖：token 路径、Host/Origin 校验、Content-Type 限制、API 白名单、目录穿越、
心跳、以及"任务运行中不得因空闲超时退出"。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import browser_mode  # noqa: E402


class FakeApi:
    """只实现服务端会用到的少量接口。"""

    def __init__(self) -> None:
        self.browser_mode = True
        self.busy = False
        self.cancelled: list[str] = []

    def bootstrap(self) -> dict:
        return {"ok": True, "browserMode": True, "modeReason": "测试", "downloadUrl": "https://example.invalid"}

    def check_path(self, path: str) -> dict:
        return {"ok": True, "exists": True}

    def cancel(self, kind: str) -> dict:
        self.cancelled.append(kind)
        return {"ok": True}

    def quit(self) -> dict:
        return {"ok": True, "browser": True}

    def _busy(self) -> bool:
        return self.busy


def request(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, data


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.api = FakeApi()
        cls.server = browser_mode.BrowserModeServer(
            cls.api, os.path.join(ROOT, "web"), "测试", "https://example.invalid",
            idle_timeout=3600,
        )
        cls.server.start()
        cls.port = cls.server.port
        cls.token = cls.server.token

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def api_path(self, name: str) -> str:
        return f"/{self.token}/api/{name}"

    def json_body(self, args=None) -> bytes:
        return json.dumps({"args": args or []}).encode("utf-8")

    # ---------------------------------------------------------------- token
    def test_url_contains_token_and_is_loopback(self) -> None:
        self.assertTrue(self.server.url.startswith("http://127.0.0.1:"))
        self.assertEqual(self.server.url, f"http://127.0.0.1:{self.port}/{self.token}/")
        self.assertGreaterEqual(len(self.token), 16)

    def test_page_without_token_is_rejected(self) -> None:
        status, _ = request(self.port, "GET", "/")
        self.assertEqual(status, 403)

    def test_page_with_token_serves_injected_bridge(self) -> None:
        status, data = request(self.port, "GET", f"/{self.token}/")
        self.assertEqual(status, 200)
        html = data.decode("utf-8")
        self.assertIn("window.pywebview", html)
        self.assertNotIn("<!--TFP_BRIDGE-->", html)
        self.assertIn("fetch('api/'", html)

    def test_token_root_redirects_with_trailing_slash(self) -> None:
        status, _ = request(self.port, "GET", f"/{self.token}")
        self.assertEqual(status, 302)

    # ---------------------------------------------------------------- Host / Origin
    def test_foreign_host_header_rejected(self) -> None:
        status, _ = request(self.port, "GET", f"/{self.token}/", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _ = request(
            self.port, "POST", self.api_path("bootstrap"), self.json_body(),
            headers={"Host": "evil.example", "Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)

    def test_foreign_origin_rejected(self) -> None:
        status, _ = request(
            self.port, "POST", self.api_path("bootstrap"), self.json_body(),
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

    def test_same_origin_accepted(self) -> None:
        status, data = request(
            self.port, "POST", self.api_path("bootstrap"), self.json_body(),
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["ok"])

    # ---------------------------------------------------------------- 请求约束
    def test_non_json_content_type_rejected(self) -> None:
        body = self.json_body().decode("utf-8")
        status, _ = request(
            self.port, "POST", self.api_path("bootstrap"), body.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(status, 415)

    def test_private_method_not_exposed(self) -> None:
        status, data = request(
            self.port, "POST", self.api_path("_busy"), self.json_body(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 404)
        self.assertFalse(json.loads(data)["ok"])

    def test_unknown_method_not_exposed(self) -> None:
        status, _ = request(
            self.port, "POST", self.api_path("open_file"), self.json_body(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 404)

    def test_path_traversal_blocked(self) -> None:
        status, _ = request(self.port, "GET", f"/{self.token}/../api.py")
        self.assertEqual(status, 404)

    def test_heartbeat_ok(self) -> None:
        status, data = request(
            self.port, "POST", self.api_path("heartbeat"), self.json_body(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["ok"])

    def test_large_body_rejected(self) -> None:
        status, _ = request(
            self.port, "POST", self.api_path("bootstrap"), b"{}",
            headers={"Content-Type": "application/json", "Content-Length": str(64 * 1024 * 1024)},
        )
        self.assertEqual(status, 413)


class IdleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeApi()
        self.server = browser_mode.BrowserModeServer(
            self.api, os.path.join(ROOT, "web"), "测试", "https://example.invalid",
            idle_timeout=0.4,
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.shutdown()

    def test_no_heartbeat_exits_idle(self) -> None:
        self.assertTrue(self._wait_stopped(4.0))

    def test_busy_job_prevents_idle_exit(self) -> None:
        self.api.busy = True
        time.sleep(2.2)
        self.assertFalse(self.server._stop.is_set(), "任务运行中不应因空闲退出")
        status, _ = request(self.server.port, "GET", f"/{self.server.token}/")
        self.assertEqual(status, 200, "服务应仍然可用")
        self.api.busy = False
        self.assertTrue(self._wait_stopped(4.0))

    def _wait_stopped(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.server._stop.is_set():
                return True
            time.sleep(0.1)
        return False


class UrlFileTests(unittest.TestCase):
    """单实例复用地址文件时的形状校验：没有 token 的地址一律不采信。"""

    def setUp(self) -> None:
        import main

        self.main = main
        self.tmp = tempfile.mkdtemp(prefix="tfp_url_")
        self.url_file = os.path.join(self.tmp, "shiji_browser_url.txt")
        self._orig = main.BROWSER_URL_FILE
        main.BROWSER_URL_FILE = self.url_file

    def tearDown(self) -> None:
        self.main.BROWSER_URL_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rejects_url_without_token(self) -> None:
        with open(self.url_file, "w", encoding="utf-8") as fh:
            fh.write("http://127.0.0.1:1/")
        self.assertEqual(self.main._read_browser_url(), "")

    def test_rejects_non_loopback_url(self) -> None:
        with open(self.url_file, "w", encoding="utf-8") as fh:
            fh.write("http://evil.example:8080/aaaaaaaaaaaaaaaaaaaa/")
        self.assertEqual(self.main._read_browser_url(), "")

    def test_rejects_dead_loopback_url(self) -> None:
        with open(self.url_file, "w", encoding="utf-8") as fh:
            fh.write("http://127.0.0.1:1/aaaaaaaaaaaaaaaaaaaa/")
        self.assertEqual(self.main._read_browser_url(), "")

    def test_discard_removes_file(self) -> None:
        with open(self.url_file, "w", encoding="utf-8") as fh:
            fh.write("http://127.0.0.1:1/aaaaaaaaaaaaaaaaaaaa/")
        self.main._discard_browser_url()
        self.assertFalse(os.path.exists(self.url_file))


if __name__ == "__main__":
    unittest.main(verbosity=2)
