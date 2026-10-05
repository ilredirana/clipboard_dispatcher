"""Clipboard Dispatcher v2 — 统一入口。

融合服务端 + 客户端 + 系统托盘。
"""

import logging
import multiprocessing
import os
import socket
import sys
import threading
from contextlib import ExitStack
from types import TracebackType

import uvicorn

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

_base_dir = os.path.dirname(os.path.abspath(__file__))
if _base_dir not in sys.path:
    sys.path.insert(0, _base_dir)

import config_manager

logger = logging.getLogger("clipboard_dispatcher")

stop_event = threading.Event()


def _log_unhandled_exception(
    exception_type: type[BaseException], exception: BaseException, traceback: TracebackType | None,
) -> None:
    """将无控制台程序的未处理异常写入日志，同时保留原异常报告。"""
    logger.critical("Unhandled application exception", exc_info=(exception_type, exception, traceback))
    sys.__excepthook__(exception_type, exception, traceback)


def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
    """记录后台线程异常的完整堆栈。"""
    logger.critical(
        "Unhandled worker exception",
        extra={"worker": args.thread.name if args.thread is not None else None},
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
    threading.__excepthook__(args)


def _start_thread(target, name: str) -> threading.Thread:
    """创建并启动一个守护线程，随主进程退出自动终止。"""
    t = threading.Thread(target=target, daemon=True, name=name)
    t.start()
    return t


def _start_server(
    port: int, host: str, listeners: list[socket.socket] | None,
) -> tuple[threading.Thread, uvicorn.Server]:
    """使用已预留的端口启动服务器，并统一写入应用日志。"""
    from server.app import create_app

    server = uvicorn.Server(uvicorn.Config(
        create_app(), host=host, port=port, log_level="info", access_log=True, log_config=None,
    ))
    thread = _start_thread(lambda: server.run(sockets=listeners), "UvicornServer")
    return thread, server


def _start_clipboard_monitor() -> threading.Thread:
    """启动剪贴板轮询监视线程。"""
    from client.clipboard_monitor import clipboard_monitor_loop
    return _start_thread(lambda: clipboard_monitor_loop(stop_event), "ClipboardMonitor")


def _start_sse_listener() -> threading.Thread:
    """启动 SSE 推送监听线程。"""
    from client.sse_listener import sse_listener_loop
    return _start_thread(lambda: sse_listener_loop(stop_event), "SSEListener")


def _wait_for_server(server: uvicorn.Server, thread: threading.Thread, timeout: float) -> None:
    """等待当前实例真正开始监听，避免误认其他进程的健康接口。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("本地服务启动线程已退出，请检查 app.log 中的服务错误。")
        if server.started:
            return
        time.sleep(0.05)
    raise TimeoutError(f"本地服务未在 {timeout} 秒内完成启动，请检查 app.log。")


def main() -> None:
    config_dir = config_manager.get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    log_path = os.path.join(config_dir, "app.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    config_manager.init()
    is_headless = config_manager.get_runtime_mode() in {"docker_server", "server_only"}
    if is_headless and not config_manager.get("server.enabled"):
        raise RuntimeError("SERVER_ONLY/DOCKER_MODE 已启用，但配置关闭了服务端。请启用服务端或移除该环境变量。")

    port = config_manager.get("server.port", 8000)
    host = config_manager.get("server.host", "0.0.0.0") if config_manager.get("server.enabled") else "127.0.0.1"
    threads: list[threading.Thread] = []
    with ExitStack() as stack:
        listeners: list[socket.socket] | None = None
        if not is_headless:
            from startup import desktop_listeners

            listeners = stack.enter_context(desktop_listeners(host, port))
        server_thread, server = _start_server(port, host, listeners)
        threads.append(server_thread)
        try:
            _wait_for_server(server, server_thread, 10.0)
            if not is_headless:
                from tray import create_tray, run_tray

                icon = create_tray(stop_event)
                threads.append(_start_clipboard_monitor())
                threads.append(_start_sse_listener())
                logger.info("Client workers started")
                run_tray(icon)
            else:
                logger.info("Running in headless mode; desktop tray is disabled")
                try:
                    while not stop_event.wait(1):
                        if not server_thread.is_alive():
                            raise RuntimeError("服务端线程意外退出，请检查 app.log。")
                except KeyboardInterrupt:
                    logger.info("Stopping server")
        finally:
            stop_event.set()
            server.should_exit = True
            for thread in threads:
                thread.join(timeout=3)
    logger.info("Goodbye!")


def run() -> int:
    """在日志初始化之前建立启动错误边界，失败时保留原配置并明确退出。"""
    from startup import instance_lock, show_startup_error

    multiprocessing.freeze_support()
    sys.excepthook = _log_unhandled_exception
    threading.excepthook = _log_thread_exception
    try:
        with instance_lock(config_manager.get_config_dir()):
            main()
    except (OSError, ValueError, TypeError, RuntimeError, ImportError) as exc:
        logger.exception("Application startup failed")
        show_startup_error(
            f"{exc}\n\n配置和日志目录：{config_manager.get_config_dir()}\n"
            "请检查配置、端口和文件权限。目录不可写时，请将程序与 config.json 一起移到有写权限的目录。"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
