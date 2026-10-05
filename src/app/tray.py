"""系统托盘图标 — pystray 封装。"""

import ctypes
import logging
import queue
import sys
import threading
import time
import webbrowser
from collections.abc import Callable

import config_manager
import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class WindowsTrayIcon(pystray.Icon):
    """检查 Windows 通知区域返回值，等待开机时尚未就绪的 Explorer。"""

    def _start_setup(self, setup: Callable[[pystray.Icon], None] | None) -> None:
        """使用可取消的就绪等待，窗口创建失败时允许安装线程结束。"""
        self._setup_ready = threading.Event()
        self._setup_cancelled = threading.Event()

        def setup_handler() -> None:
            self._setup_ready.wait()
            if self._setup_cancelled.is_set():
                return
            if setup is None:
                self.visible = True
            else:
                setup(self)

        self._setup_thread = threading.Thread(target=setup_handler, name="TraySetup")
        self._setup_thread.start()

    def _mark_ready(self) -> None:
        """菜单创建完成后才允许安装图标。"""
        super()._mark_ready()
        self._setup_ready.set()

    def _run(self) -> None:
        """事件循环结束或创建窗口失败后，释放并等待安装线程。"""
        try:
            super()._run()
        finally:
            self._setup_cancelled.set()
            self._setup_ready.set()
            self._setup_thread.join(timeout=15)
            if self._setup_thread.is_alive():
                raise TimeoutError("托盘安装线程未在 15 秒内结束")

    def _show(self) -> None:
        from pystray._util import win32

        self._assert_icon_handle()
        data = win32.NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
            hWnd=self._hwnd,
            hID=id(self),
            uFlags=win32.NIF_MESSAGE | win32.NIF_ICON | win32.NIF_TIP,
            uCallbackMessage=win32.WM_NOTIFY,
            hIcon=self._icon_handle,
            szTip=self.title,
        )
        for attempt in range(20):
            if win32.Shell_NotifyIcon(win32.NIM_ADD, data):
                return
            logger.warning("Windows tray registration retry", extra={"attempt": attempt + 1})
            if attempt < 19:
                time.sleep(0.5)
        raise OSError("Shell_NotifyIconW(NIM_ADD) 返回 FALSE，通知区域无法添加图标。请确认 Windows Explorer 已启动。")


def _create_icon(size: int = 64) -> Image.Image:
    """绘制系统托盘图标：紫色圆形 + 白色矩形模拟剪贴板。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill=(108, 99, 255, 255))
    pad = size // 5
    draw.rectangle([pad, pad + 4, size - pad, size - pad],
                   fill=(255, 255, 255, 230))
    clip_w = size // 4
    clip_x = (size - clip_w) // 2
    draw.rectangle([clip_x, pad - 2, clip_x + clip_w, pad + 6],
                   fill=(220, 220, 220, 255))
    return img


def _open_dashboard(_icon, _item):
    """托盘菜单回调：在默认浏览器中打开控制台。"""
    port = config_manager.get("server.port", 8000)
    if not config_manager.is_initialized():
        webbrowser.open(f"http://127.0.0.1:{port}/setup")
        return
    from server.auth import create_local_login_code

    code = create_local_login_code()
    webbrowser.open(f"http://127.0.0.1:{port}/local-login/{code}")


def _toggle_auto_start(icon, item):
    """托盘菜单回调：切换开机自启状态。"""
    current = config_manager.get("system.auto_start", False)
    config_manager.set_auto_start(not current)
    icon.update_menu()


def _quit(icon, _item, stop_event):
    """托盘菜单回调：设置停止事件并退出托盘。"""
    logger.info("Quit requested from tray")
    stop_event.set()
    icon.stop()


_global_icon: pystray.Icon | None = None


def notify_system(title: str, message: str):
    """发送系统右下角气泡通知。需托盘图标已创建。"""
    if _global_icon:
        _global_icon.notify(message, title)


def show_tray(icon: pystray.Icon) -> None:
    """完成图标加载后记录桌面启动成功，供打包冒烟测试验证。"""
    icon.visible = True
    logger.info("System tray ready")


def run_tray(icon: pystray.Icon) -> None:
    """将托盘安装线程错误传回主线程，避免无图标的后台进程继续运行。"""
    errors: queue.Queue[OSError | ValueError | RuntimeError] = queue.Queue()

    def setup(current_icon: pystray.Icon) -> None:
        try:
            show_tray(current_icon)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.put(exc)
            current_icon.stop()

    icon.run(setup=setup)
    if not errors.empty():
        raise errors.get_nowait()


def create_tray(stop_event) -> pystray.Icon:
    """创建系统托盘图标。返回后调用 icon.run() 将阻塞主线程直到用户退出。"""
    global _global_icon

    menu = pystray.Menu(
        pystray.MenuItem("打开控制台", _open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: "当前模式: 服务端 (内置)" if config_manager.get("server.enabled") else "当前模式: 仅客户端",
            None, enabled=False
        ),
        pystray.MenuItem(
            lambda item: "开机自启: ✓" if config_manager.get("system.auto_start") else "开机自启: ✗",
            _toggle_auto_start,
        ),
        pystray.MenuItem("退出", lambda icon, item: _quit(icon, item, stop_event)),
    )

    server_mode_text = "服务端模式" if config_manager.get("server.enabled") else "客户端模式"
    icon_type = WindowsTrayIcon if sys.platform == "win32" else pystray.Icon
    _global_icon = icon_type(
        name="ClipboardDispatcher",
        icon=_create_icon(),
        title=f"Clipboard Dispatcher — {server_mode_text}",
        menu=menu,
    )
    return _global_icon
