"""Clipboard Dispatcher v2 — 统一入口。

融合服务端 + 客户端 + 系统托盘。
"""

import logging
import multiprocessing
import os
import sys
import threading

import uvicorn

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

_base_dir = os.path.dirname(os.path.abspath(__file__))
if _base_dir not in sys.path:
    sys.path.insert(0, _base_dir)

import config_manager

config_dir = config_manager.get_config_dir()
os.makedirs(config_dir, exist_ok=True)
log_path = os.path.join(config_dir, "app.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout if sys.stdout else open(os.devnull, "w", encoding="utf-8"))
    ]
)
logger = logging.getLogger("clipboard_dispatcher")

stop_event = threading.Event()


def _start_thread(target, name: str) -> threading.Thread:
    """创建并启动一个守护线程，随主进程退出自动终止。"""
    t = threading.Thread(target=target, daemon=True, name=name)
    t.start()
    return t


def _start_server(port: int, host: str) -> tuple[threading.Thread, uvicorn.Server]:
    """在守护线程中启动 uvicorn 服务器。"""
    from server.app import create_app

    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="info", access_log=True)
    )
    thread = _start_thread(server.run, "UvicornServer")
    return thread, server


def _start_clipboard_monitor() -> threading.Thread:
    """启动剪贴板轮询监视线程。"""
    from client.clipboard_monitor import clipboard_monitor_loop
    return _start_thread(lambda: clipboard_monitor_loop(stop_event), "ClipboardMonitor")


def _start_sse_listener() -> threading.Thread:
    """启动 SSE 推送监听线程。"""
    from client.sse_listener import sse_listener_loop
    return _start_thread(lambda: sse_listener_loop(stop_event), "SSEListener")


def _wait_for_server(port: int, timeout: float = 10.0) -> bool:
    """轮询健康检查端点直到服务器就绪或超时。"""
    import time

    import httpx

    deadline = time.time() + timeout
    with httpx.Client(timeout=2) as client:
        while time.time() < deadline:
            try:
                resp = client.get(f"http://127.0.0.1:{port}/health")
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
    return False


def main() -> None:
    multiprocessing.freeze_support()

    config_manager.init()

    logger.info("Server enabled: %s", config_manager.get("server.enabled"))

    # Docker / Linux / SERVER_ONLY 模式：无 GUI，无剪贴板操作
    is_headless = os.environ.get("DOCKER_MODE") == "1" or \
                  os.environ.get("SERVER_ONLY") == "1" or \
                  sys.platform == "linux"

    if is_headless and not config_manager.get("server.enabled"):
        logger.error("Running in headless/Linux mode but server is disabled. Exiting.")
        sys.exit(1)

    threads: list[threading.Thread] = []

    port = config_manager.get("server.port", 8000)
    # 服务端模式绑定 0.0.0.0；客户端模式仅绑定 127.0.0.1 用于本地配置 UI
    if config_manager.get("server.enabled"):
        host = config_manager.get("server.host", "0.0.0.0")
        server_thread, server = _start_server(port, host)
        threads.append(server_thread)
    else:
        logger.info("Server disabled — running in client-only mode with local config UI")
        if not config_manager.get("client.server_url"):
            logger.warning("client.server_url not set! Sync will not work.")
        host = "127.0.0.1"
        server_thread, server = _start_server(port, host)
        threads.append(server_thread)

    if not _wait_for_server(port) or not server_thread.is_alive():
        server.should_exit = True
        logger.error("Server/UI failed to start within timeout!")
        sys.exit(1)

    # 桌面模式：启动客户端组件 + 系统托盘（icon.run() 阻塞直到退出）
    if not is_headless:
        threads.append(_start_clipboard_monitor())
        threads.append(_start_sse_listener())
        logger.info("Client workers started")

        from tray import create_tray
        icon = create_tray(stop_event)
        logger.info("System tray ready — double-click to open dashboard")
        icon.run()
    else:
        logger.info("Running in Headless / Pure Server Mode. Client tools and GUI disabled.")
        import time
        try:
            while not stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, stopping...")
            stop_event.set()

    stop_event.set()
    server.should_exit = True
    for t in threads:
        t.join(timeout=3)
    logger.info("Goodbye!")


if __name__ == "__main__":
    main()
