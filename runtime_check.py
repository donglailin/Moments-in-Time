"""WebView2 运行时检测与启动失败分类。

设计要点：检测逻辑全部依赖注入（注册表读取、路径存在性、Loader 探测），
便于在没有 WebView2 的机器上用单元测试覆盖各分支，不影响系统环境。
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field

DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
REG_PATHS = (
    rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{CLIENT_GUID}",
    rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{CLIENT_GUID}",
)

STATUS_PRESENT = "present"
STATUS_MISSING = "missing"
STATUS_BROKEN = "broken"


@dataclass
class Detection:
    status: str
    reason: str
    detail: str = ""
    version: str = ""
    install_path: str = ""
    loader_path: str = ""
    download_url: str = DOWNLOAD_URL
    checked_keys: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.status == STATUS_PRESENT


def _read_client_entry() -> dict | None:
    """依次读取 HKLM(WOW6432Node) / HKLM / HKCU，返回第一个含 pv 的项。"""
    try:
        import winreg
    except ImportError:
        return None

    # 第 3 个目标与第 2 个路径相同但根不同（HKCU），不能复用同一根，否则会重复读取
    targets = (
        (winreg.HKEY_LOCAL_MACHINE, REG_PATHS[0]),
        (winreg.HKEY_LOCAL_MACHINE, REG_PATHS[1]),
        (winreg.HKEY_CURRENT_USER, REG_PATHS[1]),
    )
    for root, path in targets:
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                values = {}
                for name in ("pv", "location"):
                    try:
                        values[name] = str(winreg.QueryValueEx(key, name)[0])
                    except OSError:
                        pass
                return {"path": path, "values": values}
        except OSError:
            continue
    return None


def _loader_candidates() -> list[str]:
    """WebView2Loader.dll 的可能位置：pywebview 包目录，以及打包后的 _MEIPASS。"""
    import sys

    bases: list[str] = []
    try:
        import importlib.util

        spec = importlib.util.find_spec("webview")
        if spec and spec.origin:
            bases.append(os.path.dirname(spec.origin))
    except Exception:  # noqa: BLE001
        pass
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        bases.append(os.path.join(meipass, "webview"))
        bases.append(meipass)

    machine = platform.machine().lower()
    # Loader 必须匹配 Python 进程位数（32 位 Python 跑在 64 位系统上仍需要 win-x86）
    is_64 = sys.maxsize > 2 ** 32
    if not is_64:
        arch = "win-x86"
    elif machine in ("arm64", "aarch64"):
        arch = "win-arm64"
    else:
        arch = "win-x64"
    paths: list[str] = []
    for base in bases:
        paths.append(os.path.join(base, "lib", "runtimes", arch, "native", "WebView2Loader.dll"))
        paths.append(os.path.join(base, "lib", "WebView2Loader.dll"))
    return paths


def _install_candidates(version: str, location: str) -> list[str]:
    names = ["msedgewebview2.exe", "Microsoft.Web.WebView2.Core.dll"]
    dirs = []
    if location:
        dirs.append(location)
    roots = [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if root:
            dirs.append(os.path.join(root, "Microsoft", "EdgeWebView", "Application", version))
    paths = []
    for base in dirs:
        for name in names:
            paths.append(os.path.join(base, name))
    return paths


def detect(
    registry_get=_read_client_entry,
    path_exists=os.path.exists,
    loader_candidates=_loader_candidates,
    force_missing: bool = False,
) -> Detection:
    """判断 WebView2 是否可用。

    force_missing=True 用于测试开关（环境变量 WEBVIEW2_FORCE_MISSING=1），
    模拟"运行时缺失"分支，不修改系统。
    """
    if force_missing:
        return Detection(
            status=STATUS_MISSING,
            reason="测试开关强制模拟：未检测到 Microsoft Edge WebView2 运行时",
            detail="WEBVIEW2_FORCE_MISSING=1",
        )

    entry = registry_get()
    if not entry:
        return Detection(
            status=STATUS_MISSING,
            reason="未检测到 Microsoft Edge WebView2 运行时",
            detail="注册表 HKLM/HKCU 均无 EdgeUpdate Clients 项",
            checked_keys=list(REG_PATHS),
        )

    values = entry.get("values") or {}
    version = str(values.get("pv") or "").strip()
    key_path = entry.get("path") or ""
    if not version or version in ("0.0.0.0", "0"):
        return Detection(
            status=STATUS_BROKEN,
            reason="WebView2 注册表项存在，但没有有效版本号（安装可能未完成）",
            detail=f"注册表：{key_path}",
            checked_keys=[key_path],
        )

    install_path = ""
    searched = _install_candidates(version, str(values.get("location") or ""))
    for candidate in searched:
        if path_exists(candidate):
            install_path = os.path.dirname(candidate)
            break
    if not install_path:
        return Detection(
            status=STATUS_BROKEN,
            reason="WebView2 注册表记录了版本，但安装目录不存在（运行时已损坏或被清理）",
            detail=f"版本 {version}；已查找：{searched[0] if searched else '（无候选路径）'}",
            version=version,
            checked_keys=[key_path],
        )

    loader = ""
    for candidate in loader_candidates():
        if path_exists(candidate):
            loader = candidate
            break
    if not loader:
        return Detection(
            status=STATUS_BROKEN,
            reason="程序缺少 WebView2Loader.dll（安装包不完整）",
            detail="请重新安装或重新打包程序",
            version=version,
            install_path=install_path,
            checked_keys=[key_path],
        )

    return Detection(
        status=STATUS_PRESENT,
        reason="WebView2 运行时可用",
        detail=f"版本 {version}",
        version=version,
        install_path=install_path,
        loader_path=loader,
        checked_keys=[key_path],
    )


def browser_mode_reason(detection: Detection, category: str = "") -> str:
    """浏览器模式的用户可见说明（含下载地址）。"""
    if category == "timeout":
        why = "WebView2 界面引擎初始化超过 30 秒，可能是安全软件拦截或运行时异常。"
    elif category == "blocked":
        why = "WebView2 初始化被杀毒或安全软件拦截（拒绝访问）。"
    elif category == "init_failed":
        why = "WebView2 界面引擎初始化失败。"
    elif detection.status == STATUS_BROKEN:
        why = f"{detection.reason}。"
    else:
        why = f"{detection.reason}。"
    return (
        f"已自动切换到浏览器模式：{why}"
        "功能仍可正常使用，但请手动输入文件夹路径（浏览器不允许网页读取本地完整路径）。"
        f"下载安装 WebView2：{DOWNLOAD_URL} ；安装后重新打开本程序即可恢复原生窗口。"
    )


def classify_startup_failure(error: object = None, timed_out: bool = False) -> tuple[str, str]:
    """把原生窗口启动失败归类为 timeout / blocked / init_failed。"""
    if timed_out:
        return "timeout", "WebView2 界面引擎初始化超时（30 秒内未显示窗口）。可能是安全软件拦截或运行时损坏。"
    text = str(error or "").lower()
    if isinstance(error, TimeoutError) or any(
        marker in text for marker in ("timed out", "timeout", "超时")
    ):
        return "timeout", f"WebView2 界面引擎初始化超时：{error}"
    blocked_markers = (
        "access is denied",
        "access denied",
        "拒绝访问",
        "0x80070005",
        "0x800704ec",
        "blocked by",
        "antivirus",
        "杀毒",
        "安全软件",
        "拦截",
    )
    if any(marker in text for marker in blocked_markers):
        return "blocked", f"WebView2 初始化请求被拒绝，可能被杀毒/安全软件拦截：{error}"
    return "init_failed", f"WebView2 界面引擎初始化失败：{error}"
