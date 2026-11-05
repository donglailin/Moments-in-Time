"""浏览器降级模式的 HTTP 探针（用于端到端验证）。

从 %TEMP%\\shiji_browser_url.txt 读取地址，检查：
  1. GET / 返回页面、桥接脚本已注入、横幅存在；
  2. POST /api/bootstrap 返回 browserMode=true 与下载地址；
  3. 可选 --scan：POST /api/start_scan 扫描临时目录并等待完成；
  4. 可选 --export：把扫描结果复制到临时目录，并断言目标目录只有原文件（无 CSV 清单）；
  5. 可选 --quit：POST /api/quit 让服务退出。

运行：python tests/browser_mode_probe.py [--scan] [--export] [--quit]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

URL_FILE = os.path.join(tempfile.gettempdir(), "shiji_browser_url.txt")


def post(base: str, name: str, args: list) -> dict:
    payload = json.dumps({"args": args}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/{name}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    try:
        with open(URL_FILE, "r", encoding="utf-8") as fh:
            base = fh.read().strip().rstrip("/")
    except OSError:
        print("FAIL 找不到浏览器模式地址文件")
        return 1

    with urllib.request.urlopen(base + "/", timeout=10) as resp:
        html = resp.read().decode("utf-8")
    checks = [
        ("页面可访问", len(html) > 10000),
        ("桥接脚本已注入", "window.pywebview" in html),
        ("降级横幅存在", "browser-banner" in html),
        ("占位符已替换", "<!--TFP_BRIDGE-->" not in html),
    ]

    info = post(base, "bootstrap", [])
    checks += [
        ("bootstrap 成功", bool(info.get("ok"))),
        ("browserMode=true", info.get("browserMode") is True),
        ("说明含浏览器模式", "浏览器模式" in str(info.get("modeReason"))),
        ("下载地址 = fwlink", info.get("downloadUrl", "").startswith("https://go.microsoft.com/fwlink/")),
    ]

    if "--scan" in sys.argv:
        root = os.path.join(tempfile.gettempdir(), "shiji_browser_probe")
        os.makedirs(root, exist_ok=True)
        for index in range(3):
            with open(os.path.join(root, f"probe{index}.txt"), "w", encoding="utf-8") as fh:
                fh.write("x" * (index + 1))
        started = post(
            base,
            "start_scan",
            [{"root": root, "recursive": True, "startMs": None, "endMs": None,
              "extensions": "", "keyword": ""}],
        )
        checks.append(("扫描启动", bool(started.get("ok"))))
        status = ""
        for _ in range(50):
            poll = post(base, "poll", [])
            status = (poll.get("scan") or {}).get("status", "")
            if status != "running":
                break
            time.sleep(0.2)
        result = post(base, "scan_result", [])
        items = result.get("items") or []
        checks.append(("扫描完成", status == "done"))
        checks.append(("扫描命中 3 个文件", len(items) == 3))

        if "--export" in sys.argv:
            out = os.path.join(tempfile.gettempdir(), "shiji_browser_probe_out")
            shutil.rmtree(out, ignore_errors=True)
            started = post(
                base,
                "start_export",
                [{"dest": out, "mode": "copy", "preserve": True, "conflict": "rename",
                  "ids": [it["id"] for it in items]}],
            )
            checks.append(("导出启动", bool(started.get("ok"))))
            export_status = ""
            for _ in range(100):
                poll = post(base, "poll", [])
                export_status = (poll.get("export") or {}).get("status", "")
                if export_status != "running":
                    break
                time.sleep(0.2)
            exported = post(base, "export_result", [])
            checks.append(("导出完成", export_status == "done"))
            checks.append(("导出成功 3 个", exported.get("succeeded") == 3))
            found = sorted(os.listdir(out)) if os.path.isdir(out) else []
            checks.append(
                (
                    "导出目录仅含 3 个原文件（无 CSV 清单）",
                    found == ["probe0.txt", "probe1.txt", "probe2.txt"],
                )
            )

    failures = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} | {name}")
        failures += 0 if ok else 1

    if "--quit" in sys.argv:
        try:
            post(base, "quit", [])
            print("PASS | 已请求退出")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN | 退出请求未确认：{exc}")

    print(f"共 {len(checks)} 项检查，失败 {failures} 项")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
