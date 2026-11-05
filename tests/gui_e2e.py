"""真实 pywebview 窗口端到端自测。

流程：启动真实窗口（真实 web 资源 + 真实 Api）→ 扫描临时目录 → 文件夹树勾选联动
（含半选）→ 下钻/返回 → 全选 → 导出 → 校验目标目录只有导出文件（无 CSV 清单）→ 关闭窗口。

运行：python tests/gui_e2e.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webview  # noqa: E402

import api as api_mod  # noqa: E402
import fileutil  # noqa: E402
import store  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}", flush=True)


def make_fixture(base: str) -> tuple[str, str]:
    src = os.path.join(base, "源 文件夹")
    out = os.path.join(base, "导出 结果")
    now = time.time()
    for rel, content in [
        ("root1.txt", "r1"),
        ("root2.png", "r2"),
        (os.path.join("Alpha", "a1.txt"), "a1"),
        (os.path.join("Alpha", "a2.txt"), "a2"),
        (os.path.join("Beta", "sub", "b1.md"), "b1"),
        (os.path.join("Gamma", "g1.log"), "g1"),
    ]:
        path = os.path.join(src, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.utime(path, (now, now))
    return src, out


def js(window, expr: str):
    return window.evaluate_js(expr)


def wait_js(window, expr: str, predicate, timeout: float, label: str):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = js(window, expr)
        except Exception as exc:  # noqa: BLE001
            last = f"<error: {exc}>"
        if predicate(last):
            return last
        time.sleep(0.2)
    raise TimeoutError(f"{label} 超时，最后取值：{last!r}")


def run(window, app, src: str, out: str) -> None:
    try:
        # 1. 等界面就绪（boot 完成 → 扫描按钮可用）
        wait_js(
            window,
            "document.getElementById('btn-scan').disabled",
            lambda v: v is False,
            25,
            "界面就绪",
        )
        name = js(window, "document.getElementById('source-path').textContent")
        same_source = os.path.normcase(str(name)) == os.path.normcase(src)
        check("启动后源路径来自设置", same_source, f"source={name!r}")

        # 2. 扫描
        js(window, "document.getElementById('btn-scan').click()")
        wait_js(
            window,
            "document.getElementById('sum-files').textContent",
            lambda v: str(v).strip().isdigit() and int(str(v)) == 6,
            60,
            "扫描完成",
        )
        folders = js(window, "document.querySelectorAll('#tbody tr.folder-row').length")
        files = js(window, "document.querySelectorAll('#tbody tr.file-row').length")
        check("根层文件夹行 = 3", folders == 3, f"folders={folders}")
        check("根层文件行 = 2", files == 2, f"files={files}")
        check(
            "根层合计 6 个文件",
            js(window, "document.getElementById('sum-files').textContent") == "6",
            "sum-files=6",
        )

        # 3. 勾选子文件夹 Alpha → 表头半选、Alpha 行选中
        js(window, "document.querySelector('input[data-fcheck=\"Alpha\"]').click()")
        time.sleep(0.15)
        check("勾选子文件夹后表头半选", js(window, "document.getElementById('check-all').indeterminate") is True, "")
        check(
            "Alpha 行复选框为选中",
            js(window, "document.querySelector('input[data-fcheck=\"Alpha\"]').checked") is True,
            "",
        )
        summary = js(window, "document.getElementById('selection-summary').textContent")
        check("已选计数=2", "2" in str(summary), f"summary={summary!r}")

        # 4. 进入 Alpha：全选本层 → 表头应为全选
        js(window, "document.querySelector('tr.folder-row .folder-open').click()")
        wait_js(
            window,
            "document.getElementById('check-all').checked",
            lambda v: v is True,
            10,
            "进入已全选文件夹",
        )
        check("进入 Alpha 后表头全选", True, "2/2 已选")
        sub_rows = js(window, "document.querySelectorAll('#tbody tr.file-row').length")
        check("Alpha 内文件行 = 2", sub_rows == 2, f"rows={sub_rows}")

        # 5. 取消一个文件 → 半选；再重新勾选 → 全选
        js(window, "document.querySelector('#tbody tr.file-row input[data-check]').click()")
        time.sleep(0.15)
        check("取消单个文件后表头半选", js(window, "document.getElementById('check-all').indeterminate") is True, "")
        js(window, "document.querySelector('#tbody tr.file-row input[data-check]').click()")
        time.sleep(0.15)
        check("重新勾选后表头全选", js(window, "document.getElementById('check-all').checked") is True, "")

        # 6. 返回根层，全选 6 个文件
        js(window, "document.getElementById('btn-up').click()")
        time.sleep(0.2)
        js(window, "document.getElementById('btn-select-all').click()")
        time.sleep(0.2)
        summary = js(window, "document.getElementById('selection-summary').textContent")
        check("根层全选 6 个", "6" in str(summary), f"summary={summary!r}")
        check(
            "根层表头全选",
            js(window, "document.getElementById('check-all').checked") is True,
            "",
        )

        # 7. 导出并等待完成
        js(window, "document.getElementById('btn-export').click()")
        deadline = time.time() + 60
        while time.time() < deadline:
            snap = app._export_job.snapshot() if app._export_job else {}
            if snap.get("status") in ("done", "cancelled", "error"):
                break
            time.sleep(0.2)
        wait_js(
            window,
            "document.getElementById('modal').hidden",
            lambda v: v is False,
            15,
            "导出结果弹窗",
        )
        res = app.export_result()
        check("导出成功数=6", res.get("ok") and res.get("succeeded") == 6, f"result={ {k: res.get(k) for k in ('succeeded','skipped','failed','cancelled')} }")
        check("导出未失败", res.get("failed") == 0, f"failed={res.get('failed')}")

        expected = [
            "root1.txt",
            "root2.png",
            os.path.join("Alpha", "a1.txt"),
            os.path.join("Alpha", "a2.txt"),
            os.path.join("Beta", "sub", "b1.md"),
            os.path.join("Gamma", "g1.log"),
        ]
        missing = [rel for rel in expected if not os.path.isfile(os.path.join(out, rel))]
        check("目标目录结构完整（保留相对路径）", not missing, f"missing={missing}")

        found = []
        for base, _dirs, files in os.walk(out):
            for name in files:
                found.append(os.path.relpath(os.path.join(base, name), out))
        extra = sorted(set(found) - set(expected))
        check("目标目录无 CSV 清单等额外文件", extra == [] and len(found) == len(expected), f"extra={extra} files={found}")

        summary_text = js(window, "document.getElementById('modal-sub').textContent")
        check("导出弹窗显示 6 个文件", "6" in str(summary_text), f"modal={summary_text!r}")

    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("端到端流程未抛异常", False, "见上方 traceback")
    finally:
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    base = tempfile.mkdtemp(prefix="tfp_gui_")
    src, out = make_fixture(base)
    store._CONFIG_PATH = os.path.join(base, "settings.json")
    store.save(
        {
            "source": src,
            "dest": out,
            "preset": "all",
            "recursive": True,
            "extensions": "",
            "keyword": "",
            "mode": "copy",
            "preserve": True,
            "conflict": "rename",
        }
    )

    app = api_mod.Api()
    window = webview.create_window(
        "时迹 · GUI 自测",
        fileutil.resource_path("web", "index.html"),
        js_api=app,
        width=1100,
        height=760,
        background_color="#F3F5F6",
    )
    app._window = window
    webview.start(func=run, args=(window, app, src, out), gui="edgechromium", debug=False)

    failed = [item for item in CHECKS if not item[1]]
    print(f"\n共 {len(CHECKS)} 项检查，失败 {len(failed)} 项", flush=True)
    if failed:
        for name, _, detail in failed:
            print(f"  FAIL: {name} {detail}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
