"""Clipboard Dispatcher 客户端组件。"""

import pyperclip


class ClipboardReadError(RuntimeError):
    """读取系统剪贴板失败。"""


def safe_clipboard_read() -> str:
    """读取文本剪贴板，失败时抛出可处理的明确异常。"""
    try:
        return pyperclip.paste() or ""
    except pyperclip.PyperclipException as exc:
        raise ClipboardReadError(f"读取文本剪贴板失败: {exc}") from exc
