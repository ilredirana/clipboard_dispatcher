"""系统托盘图标 — pystray 封装。"""

import logging
import webbrowser

import config_manager
import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


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
    _global_icon = pystray.Icon(
        name="ClipboardDispatcher",
        icon=_create_icon(),
        title=f"Clipboard Dispatcher — {server_mode_text}",
        menu=menu,
    )
    return _global_icon
