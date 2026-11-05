"""时迹 · 文件时间筛选器 —— 程序入口。

windowed（无控制台）方式启动时正常运行完全静默，不写任何日志文件；
只有致命错误（启动失败、主线程未捕获异常、后台线程异常）才弹出一个
简短的系统 MessageBox 告知用户原因。
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from urllib.parse import urlparse

WINDOW_TITLE = "时迹 · 文件时间筛选器"
MUTEX_NAME = "Local\\ShiJi.FileTimeFilter.SingleInstance"
BROWSER_URL_FILE = os.path.join(tempfile.gettempdir(), "shiji_browser_url.txt")
# 浏览器模式地址必须携带随机 token 路径，防止陈旧/被替换的地址文件把用户导向任意本地页面
_BROWSER_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]{16,}/$")

_MUTEX_HANDLE = None
_FALLBACK_LOCK = threading.Lock()
_FALLBACK_STARTED = False


# ---------------------------------------------------------------- 路径
def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _resource(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", _app_dir())
    return os.path.join(base, *parts)


def _fatal(message: str) -> None:
    """唯一的异常出口：弹窗提示（不写盘，保持一两行摘要）。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
    except Exception:  # noqa: BLE001
        pass


def _install_hooks() -> None:
    def _excepthook(exc_type, exc, tb):
        _fatal(f"程序遇到未处理的错误：\n{exc_type.__name__}: {exc}")

    def _thread_hook(args):
        _fatal(
            f"后台线程 {getattr(args.thread, 'name', '?')} 出错：\n"
            f"{args.exc_type.__name__}: {args.exc_value}"
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook


# ---------------------------------------------------------------- Win32 辅助
def _set_dpi_awareness() -> None:
    """进程级 Per-Monitor V2，跨屏 / 125%-150% 缩放下坐标换算一致。"""
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 单实例
def _activate_existing() -> bool:
    """把已经在运行的窗口还原并置前；找到窗口返回 True。"""
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        # 必须精确匹配：浏览器标签页的标题会变成「时迹 · 文件时间筛选器 - Google Chrome」，
        # 用包含匹配会把旧的浏览器标签误当成原生窗口去激活。
        if buf.value == WINDOW_TITLE:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(proto(_cb), 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _read_browser_url() -> str:
    """读取浏览器模式实例发布的回环地址，并确认服务仍然存活。

    只接受形如 http://127.0.0.1:<端口>/<随机token>/ 的地址：没有 token 的地址
    不再复用，避免旧版本残留文件或被替换的文件把用户导向任意本地页面。
    """
    try:
        with open(BROWSER_URL_FILE, "r", encoding="utf-8") as fh:
            url = fh.read().strip()
    except OSError:
        return ""
    try:
        parsed = urlparse(url)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and parsed.query == ""
            and parsed.fragment == ""
            and _BROWSER_PATH_RE.match(parsed.path or "") is not None
        )
    except ValueError:
        valid = False
    if not valid:
        return ""
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=1.5) as resp:
            if resp.status != 200:
                return ""
    except Exception:  # noqa: BLE001
        return ""
    return url


def _discard_browser_url() -> None:
    """本进程已持有单实例互斥体，磁盘上的地址文件只可能是上次残留，直接清理。"""
    try:
        os.remove(BROWSER_URL_FILE)
    except OSError:
        pass


def _open_existing_browser_mode() -> bool:
    """首实例处于浏览器模式且没有原生窗口时，重新打开它的页面。"""
    url = _read_browser_url()
    if not url:
        return False
    if os.environ.get("TFP_BROWSER_NO_OPEN") == "1":
        return True
    try:
        import webbrowser

        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001
        return False


def _acquire_single_instance() -> bool:
    """命名互斥体保证只有一个实例；返回 False 表示已有实例在运行。"""
    global _MUTEX_HANDLE
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    _MUTEX_HANDLE = handle
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        for _ in range(12):  # 首实例可能还在加载界面，稍等再找窗口
            if _activate_existing():
                return False
            time.sleep(0.25)
        if _open_existing_browser_mode():
            return False
        _fatal("时迹已在运行。\n\n如果看不到窗口，请稍候几秒，或结束任务管理器中的“时迹”进程后重试。")
        return False
    return True


def _hwnd_of_this_window() -> int:
    """按本进程 pid + 标题找主窗口，避免多实例时误取别的窗口。"""
    user32 = ctypes.windll.user32
    found = []

    def _cb(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value != WINDOW_TITLE:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            found.append(hwnd)
            return False
        return True

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proto(_cb), 0)
    return int(found[0]) if found else 0


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _primary_work_area() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return 0, 0, 1920, 1040
    r = info.rcWork
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _dpi_scale() -> float:
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(1.0, dpi / 96.0)
    except Exception:  # noqa: BLE001
        return 1.0


def _initial_size() -> tuple[tuple[int, int], tuple[int, int]]:
    """返回传给 pywebview 的**逻辑像素**尺寸（pywebview 会再按 DPI 换算成物理像素）。

    目标 1280x830 逻辑像素，并限制在工作区内（多屏时取主屏），避免 150% 缩放下超出屏幕。
    """
    scale = _dpi_scale()
    _, _, work_w, work_h = _primary_work_area()
    logical_work_w = max(1, int(work_w / scale))
    logical_work_h = max(1, int(work_h / scale))
    max_w = int(logical_work_w * 0.92)
    max_h = int(logical_work_h * 0.92)
    width = min(max_w, max(960, 1280))
    height = min(max_h, max(620, 830))
    min_w = min(width, max(900, 1000))
    min_h = min(height, max(540, 600))
    return (width, height), (min_w, min_h)


def _apply_icon() -> None:
    hwnd = _hwnd_of_this_window()
    if not hwnd:
        return
    candidates = [
        _resource("assets", "app.ico"),
        os.path.join(_app_dir(), "assets", "app.ico"),
        os.path.abspath(sys.executable),
    ]
    user32 = ctypes.windll.user32
    user32.SendMessageW.restype = ctypes.c_ssize_t
    for icon_source in candidates:
        if not icon_source or not os.path.exists(icon_source):
            continue
        big = ctypes.c_void_p()
        small = ctypes.c_void_p()
        try:
            if not ctypes.windll.shell32.ExtractIconExW(
                ctypes.c_wchar_p(icon_source), 0, ctypes.byref(big), ctypes.byref(small), 1
            ):
                continue
            if big.value:
                user32.SendMessageW(ctypes.c_void_p(hwnd), 0x0080, 1, big)
            if small.value:
                user32.SendMessageW(ctypes.c_void_p(hwnd), 0x0080, 0, small)
            return
        except Exception:  # noqa: BLE001
            continue


def _clamp_window() -> None:
    """启动时确保窗口完整可见；最大化/最小化状态一律跳过，避免位置错乱。"""
    hwnd = _hwnd_of_this_window()
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        return

    rect = _RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return
    monitor = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)  # nearest
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
        return
    work = info.rcWork
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    # WinForms 在高 DPI 下会把请求尺寸再放大，可能超出工作区；统一收敛到 92% 以内
    max_w = int((work.right - work.left) * 0.92)
    max_h = int((work.bottom - work.top) * 0.92)
    new_w = min(width, max_w)
    new_h = min(height, max_h)

    visible_w = min(rect.right, work.right) - max(rect.left, work.left)
    visible_h = min(rect.bottom, work.bottom) - max(rect.top, work.top)
    title_visible = min(rect.bottom, work.bottom) - max(rect.top, work.top)
    need_move = (
        visible_w < 200
        or visible_h < 120
        or rect.top < work.top - 2
        or rect.left > work.right - 120
        or rect.top > work.bottom - 34
        or title_visible < 34
        or rect.right > work.right + 1
        or rect.bottom > work.bottom + 1
    )
    if new_w != width or new_h != height:
        need_move = True

    if need_move:
        x = work.left + max(0, (work.right - work.left - new_w) // 2)
        y = work.top + max(0, (work.bottom - work.top - new_h) // 2)
        user32.SetWindowPos(
            ctypes.c_void_p(hwnd), None, x, y, new_w, new_h, 0x0004 | 0x0010
        )


# ---------------------------------------------------------------- 拖拽
def _setup_drag_drop(app, window) -> None:
    """把拖拽事件挂到 #drop-zone（WebView2 页面能拿到真实本地路径）。"""
    time.sleep(1.0)
    try:
        elements = window.dom.get_elements("#drop-zone")
        if not elements:
            return
        elements[0].events.drop += app.on_files_dropped
    except Exception:  # noqa: BLE001
        pass  # 页面未就绪或窗口已关闭，拖拽不可用但不影响主流程


# ---------------------------------------------------------------- 浏览器降级
def _notice(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x40)
    except Exception:  # noqa: BLE001
        pass


def _run_browser_mode(reason: str, rescue: bool = False) -> None:
    """在本机回环地址启动界面，并打开系统默认浏览器。"""
    global _FALLBACK_STARTED
    with _FALLBACK_LOCK:
        if _FALLBACK_STARTED:
            return
        _FALLBACK_STARTED = True

    import browser_mode as browser_fallback
    import fileutil
    import runtime_check
    from api import Api

    app = Api()
    app.browser_mode = True
    app.mode_reason = reason
    server = browser_fallback.BrowserModeServer(
        app, fileutil.resource_path("web"), reason, runtime_check.DOWNLOAD_URL
    )
    url = server.start()
    try:
        with open(BROWSER_URL_FILE, "w", encoding="utf-8") as fh:
            fh.write(url)
    except OSError:
        pass

    opened = False
    if os.environ.get("TFP_BROWSER_NO_OPEN") == "1":
        pass
    else:
        try:
            import webbrowser

            opened = bool(webbrowser.open(url))
        except Exception:  # noqa: BLE001
            opened = False
        if not opened:
            _notice(
                "Microsoft Edge WebView2 运行时不可用，已切换到浏览器模式。\n\n"
                f"请手动打开：{url}\n\n"
                f"安装 WebView2 后重新打开即为原生窗口：{runtime_check.DOWNLOAD_URL}"
            )

    try:
        server.wait()
    finally:
        server.shutdown()
        try:
            os.remove(BROWSER_URL_FILE)
        except OSError:
            pass
        if rescue:
            os._exit(0)


# ---------------------------------------------------------------- 入口
def main() -> None:
    _install_hooks()
    _set_dpi_awareness()

    if not _acquire_single_instance():
        return

    # 已拿到互斥体：之前进程（尤其是崩溃的浏览器模式）留下的地址文件必然失效
    _discard_browser_url()

    import runtime_check

    force_missing = os.environ.get("WEBVIEW2_FORCE_MISSING") == "1"
    state = runtime_check.detect(force_missing=force_missing)
    if not state.available:
        _run_browser_mode(runtime_check.browser_mode_reason(state))
        return

    import webview  # 延迟到确定需要原生窗口后再导入

    import fileutil
    from api import Api

    app = Api()
    size, min_size = _initial_size()
    window = webview.create_window(
        WINDOW_TITLE,
        fileutil.resource_path("web", "index.html"),
        js_api=app,
        width=size[0],
        height=size[1],
        min_size=min_size,
        background_color="#F3F5F6",
        resizable=True,
        maximized=False,
        frameless=False,
        easy_drag=False,
        text_select=True,
        confirm_close=False,
        zoomable=False,
        draggable=False,
    )
    app._window = window

    def _on_loaded() -> None:
        try:
            _apply_icon()
            _clamp_window()
        except Exception:  # noqa: BLE001
            pass  # 图标/位置修正失败不影响使用
        threading.Thread(
            target=_setup_drag_drop, args=(app, window), daemon=True,
            name="drag-drop",
        ).start()

    shown = threading.Event()
    finished = threading.Event()
    failure: dict[str, tuple[str, str]] = {}

    def _on_shown() -> None:
        shown.set()

    window.events.loaded += _on_loaded
    try:
        window.events.shown += _on_shown
    except Exception:  # noqa: BLE001
        pass

    def _watchdog() -> None:
        """30 秒内窗口未显示：按超时归类，关闭原生窗口并等待降级。"""
        if shown.wait(30.0) or finished.is_set():
            return
        category, detail = runtime_check.classify_startup_failure(timed_out=True)
        failure.setdefault("value", (category, detail))
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass
        if finished.wait(15.0):
            return
        threading.Thread(
            target=_run_browser_mode,
            args=(runtime_check.browser_mode_reason(state, category), True),
            name="browser-rescue",
        ).start()

    threading.Thread(target=_watchdog, name="startup-watchdog", daemon=True).start()

    try:
        webview.start(gui="edgechromium", debug=bool(os.environ.get("TFP_DEBUG")))
    except Exception as exc:  # noqa: BLE001
        category, detail = runtime_check.classify_startup_failure(exc)
        failure.setdefault("value", (category, detail))
    finally:
        finished.set()

    pending = failure.get("value")
    if pending:
        category, detail = pending
        _run_browser_mode(runtime_check.browser_mode_reason(state, category))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _fatal(f"程序启动失败：\n{type(exc).__name__}: {exc}")
        sys.exit(1)
