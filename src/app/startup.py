"""桌面启动所需的监听端口和独立错误提示。"""

import ctypes
import hashlib
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager


@contextmanager
def instance_lock(config_directory: str) -> Iterator[None]:
    """同一配置目录只允许一个 Windows 实例，避免重复启动和同时迁移配置。"""
    if sys.platform != "win32":
        yield
        return
    identity = os.path.normcase(os.path.realpath(config_directory)).encode("utf-8")
    name = f"Local\\ClipboardDispatcher.{hashlib.sha256(identity).hexdigest()}"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        if error == 183:
            raise RuntimeError(f"程序已运行，请使用已有的托盘图标。配置目录：{config_directory}")
        yield
    finally:
        if not kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def desktop_listeners(host: str, port: int) -> Iterator[list[socket.socket]]:
    """预先独占绑定配置地址，并保留本机控制台和管理员同步使用的 IPv4 回环入口。"""
    addresses: set[tuple[int, str]] = set()
    for family, _kind, _protocol, _name, address in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM,
    ):
        address_host = str(address[0])
        if family == socket.AF_INET6 and address[3]:
            address_host = f"{address_host}%{address[3]}"
        addresses.add((family, address_host))
    if (socket.AF_INET, "0.0.0.0") not in addresses:
        addresses.add((socket.AF_INET, "127.0.0.1"))

    with ExitStack() as stack:
        listeners: list[socket.socket] = []
        for family, address_host in sorted(addresses):
            listener = stack.enter_context(socket.socket(family, socket.SOCK_STREAM))
            if sys.platform == "win32":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                listener.bind((address_host, port))
            except OSError as exc:
                raise OSError(
                    exc.errno,
                    f"无法绑定监听地址 {address_host}:{port}：{exc}。"
                    "请确认程序没有重复启动、端口未被其他程序占用，并且监听 IP 属于本机。",
                ) from exc
            listeners.append(listener)
        yield listeners


def show_startup_error(message: str) -> None:
    """在无法写日志时仍显示错误；纯服务端模式通过标准错误报告。"""
    if sys.platform != "win32" or os.environ.get("SERVER_ONLY") == "1" or os.environ.get("DOCKER_MODE") == "1":
        print(message, file=sys.stderr)
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    user32.MessageBoxW.restype = ctypes.c_int
    if not user32.MessageBoxW(None, message, "Clipboard Dispatcher 启动失败", 0x10):
        raise ctypes.WinError(ctypes.get_last_error())
