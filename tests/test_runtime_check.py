"""WebView2 检测与降级分类的单元测试（不触碰真实注册表 / 系统环境）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runtime_check as rc  # noqa: E402


def present_registry():
    return {
        "path": rc.REG_PATHS[0],
        "values": {"pv": "120.0.2210.91", "location": r"C:\Program Files (x86)\EdgeWebView"},
    }


class DetectTests(unittest.TestCase):
    def test_missing_when_no_registry_entry(self) -> None:
        state = rc.detect(registry_get=lambda: None, path_exists=lambda p: True, loader_candidates=lambda: ["x"])
        self.assertEqual(state.status, rc.STATUS_MISSING)
        self.assertIn("未检测到", state.reason)
        self.assertIn(rc.DOWNLOAD_URL, rc.browser_mode_reason(state))
        self.assertIn("浏览器模式", rc.browser_mode_reason(state))

    def test_broken_when_registry_exists_but_path_missing(self) -> None:
        state = rc.detect(
            registry_get=present_registry,
            path_exists=lambda p: False,
            loader_candidates=lambda: ["x"],
        )
        self.assertEqual(state.status, rc.STATUS_BROKEN)
        self.assertIn("安装目录不存在", state.reason)
        self.assertEqual(state.version, "120.0.2210.91")
        self.assertIn(rc.DOWNLOAD_URL, rc.browser_mode_reason(state))

    def test_broken_when_registry_has_no_version(self) -> None:
        state = rc.detect(
            registry_get=lambda: {"path": rc.REG_PATHS[0], "values": {"pv": "0.0.0.0"}},
            path_exists=lambda p: True,
            loader_candidates=lambda: ["x"],
        )
        self.assertEqual(state.status, rc.STATUS_BROKEN)
        self.assertIn("没有有效版本号", state.reason)

    def test_broken_when_loader_missing(self) -> None:
        state = rc.detect(
            registry_get=present_registry,
            path_exists=lambda p: "msedgewebview2.exe" in p.lower(),
            loader_candidates=lambda: ["missing.dll"],
        )
        self.assertEqual(state.status, rc.STATUS_BROKEN)
        self.assertIn("WebView2Loader", state.reason)

    def test_present_when_registry_path_and_loader_exist(self) -> None:
        state = rc.detect(
            registry_get=present_registry,
            path_exists=lambda p: True,
            loader_candidates=lambda: ["WebView2Loader.dll"],
        )
        self.assertEqual(state.status, rc.STATUS_PRESENT)
        self.assertTrue(state.available)
        self.assertIn("120.0.2210.91", state.detail)

    def test_force_missing_switch(self) -> None:
        state = rc.detect(registry_get=present_registry, path_exists=lambda p: True, force_missing=True)
        self.assertEqual(state.status, rc.STATUS_MISSING)
        self.assertIn("强制模拟", state.reason)


class ClassifyTests(unittest.TestCase):
    def test_timeout(self) -> None:
        category, message = rc.classify_startup_failure(timed_out=True)
        self.assertEqual(category, "timeout")
        self.assertIn("超时", message)

    def test_timeout_from_exception(self) -> None:
        category, message = rc.classify_startup_failure(error=TimeoutError("timed out"))
        self.assertEqual(category, "timeout")
        self.assertIn("超时", message)

    def test_blocked_by_security_software(self) -> None:
        category, message = rc.classify_startup_failure(error="Access is denied (0x80070005)")
        self.assertEqual(category, "blocked")
        self.assertIn("杀毒", message)
        self.assertIn(rc.DOWNLOAD_URL, rc.browser_mode_reason(rc.Detection("broken", "x"), category))

    def test_blocked_by_chinese_keyword(self) -> None:
        category, _ = rc.classify_startup_failure(error="启动被安全软件拦截")
        self.assertEqual(category, "blocked")

    def test_generic_init_failure(self) -> None:
        category, message = rc.classify_startup_failure(error="RuntimeNotFound")
        self.assertEqual(category, "init_failed")
        self.assertIn("初始化失败", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
