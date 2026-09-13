"""Windows 图片剪贴板读写，使用系统 Clipboard API，不依赖 pywin32。"""

import ctypes
import sys
import time
from ctypes import wintypes
from io import BytesIO

from PIL import Image, ImageGrab

_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002
_OPEN_ATTEMPTS = 10
_OPEN_INTERVAL_SECONDS = 0.05


class WindowsClipboardError(RuntimeError):
    """Windows 剪贴板 API 调用失败。"""


def read_image_png() -> bytes | None:
    """读取当前剪贴板图片并统一编码为 PNG；非图片时返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        result = ImageGrab.grabclipboard()
    except OSError as exc:
        raise WindowsClipboardError(f"读取 Windows 图片剪贴板失败: {exc}") from exc
    if not isinstance(result, Image.Image):
        return None

    with BytesIO() as output:
        result.save(output, format="PNG", optimize=True)
        return output.getvalue()


def write_image_png(content: bytes) -> None:
    """把 PNG、JPEG 或 WebP 字节以 DIB 格式写入 Windows 剪贴板。"""
    if sys.platform != "win32":
        raise WindowsClipboardError("图片剪贴板写入仅支持 Windows")
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            image = source.convert("RGB")
    except OSError as exc:
        raise WindowsClipboardError(f"图片数据无法解码: {exc}") from exc

    with BytesIO() as output:
        image.save(output, format="BMP")
        dib = output.getvalue()[14:]
    _set_dib(dib)


def _set_dib(dib: bytes) -> None:
    """将 DIB 字节写入 CF_DIB 剪贴板格式。"""
    user32, kernel32 = _get_windows_apis()
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(dib))
    if not handle:
        raise WindowsClipboardError(f"GlobalAlloc 失败，错误码: {ctypes.get_last_error()}")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise WindowsClipboardError(f"GlobalLock 失败，错误码: {ctypes.get_last_error()}")
    try:
        ctypes.memmove(pointer, dib, len(dib))
    finally:
        kernel32.GlobalUnlock(handle)

    _open_clipboard(user32)
    transferred = False
    try:
        if not user32.EmptyClipboard():
            raise WindowsClipboardError(f"EmptyClipboard 失败，错误码: {ctypes.get_last_error()}")
        if not user32.SetClipboardData(_CF_DIB, handle):
            raise WindowsClipboardError(f"SetClipboardData 失败，错误码: {ctypes.get_last_error()}")
        transferred = True
    finally:
        user32.CloseClipboard()
        if not transferred:
            kernel32.GlobalFree(handle)


def _open_clipboard(user32: ctypes.CDLL) -> None:
    """等待其他进程释放剪贴板后打开。"""
    for attempt in range(_OPEN_ATTEMPTS):
        if user32.OpenClipboard(None):
            return
        if attempt + 1 < _OPEN_ATTEMPTS:
            time.sleep(_OPEN_INTERVAL_SECONDS)
    raise WindowsClipboardError(f"OpenClipboard 失败，错误码: {ctypes.get_last_error()}")


def _get_windows_apis() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """加载并配置本模块使用的 Windows API 签名。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return user32, kernel32
