"""Windows 剪贴板监听，处理文本和图片并防止同步回音。"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Literal

import config_manager
import pyperclip
from client import ClipboardReadError, api_client, safe_clipboard_read
from client.windows_clipboard import WindowsClipboardError, read_image_png, write_image_png

logger = logging.getLogger(__name__)

_suppress_flag = threading.Event()

POLL_INTERVAL = 0.3


@dataclass(frozen=True)
class LocalClipboardItem:
    """本地剪贴板快照。"""

    kind: Literal["text", "image"]
    content: str | bytes
    sha256: str


def set_clipboard_silent(text: str) -> None:
    """静默写入文本剪贴板，避免被监听器重新上传。"""
    _suppress_flag.set()
    try:
        pyperclip.copy(text)
    finally:
        time.sleep(POLL_INTERVAL + 0.1)
        _suppress_flag.clear()


def set_image_clipboard_silent(content: bytes) -> None:
    """静默写入图片剪贴板，避免被监听器重新上传。"""
    _suppress_flag.set()
    try:
        write_image_png(content)
    finally:
        time.sleep(POLL_INTERVAL + 0.1)
        _suppress_flag.clear()


def read_clipboard_item() -> LocalClipboardItem:
    """优先读取图片剪贴板，未检测到图片时读取文本。"""
    image = read_image_png()
    if image is not None:
        return LocalClipboardItem("image", image, hashlib.sha256(image).hexdigest())
    text = safe_clipboard_read()
    return LocalClipboardItem("text", text, hashlib.sha256(text.encode("utf-8")).hexdigest())


def clipboard_monitor_loop(stop_event: threading.Event) -> None:
    """检测稳定的文本或图片变更，随后上传到服务端。"""
    logger.info("Clipboard monitor started")
    last_item = _read_item_for_loop()

    while not stop_event.is_set():
        current = _read_item_for_loop()
        if current is None:
            stop_event.wait(POLL_INTERVAL)
            continue
        if last_item is None:
            last_item = current

        if not config_manager.get("client.enable_auto_upload", True):
            last_item = current
            stop_event.wait(POLL_INTERVAL)
            continue
        if current.kind == last_item.kind and current.sha256 == last_item.sha256:
            stop_event.wait(POLL_INTERVAL)
            continue
        if _suppress_flag.is_set():
            last_item = current
            stop_event.wait(POLL_INTERVAL)
            continue
        if current.kind == "text" and config_manager.get("client.ignore_empty", True) and not current.content.strip():
            last_item = current
            stop_event.wait(POLL_INTERVAL)
            continue

        debounce_seconds = config_manager.get("client.debounce_delay_ms", 500) / 1000.0
        stop_event.wait(debounce_seconds)
        stable_item = _read_item_for_loop()
        if stable_item is None:
            stop_event.wait(POLL_INTERVAL)
            continue
        if stable_item.kind != current.kind or stable_item.sha256 != current.sha256:
            last_item = stable_item
            stop_event.wait(POLL_INTERVAL)
            continue

        try:
            _upload_item(stable_item)
        except api_client.ClipboardApiError as exc:
            logger.error("Clipboard upload failed: %s", exc)
        else:
            last_item = stable_item

        stop_event.wait(POLL_INTERVAL)


def _read_item_for_loop() -> LocalClipboardItem | None:
    """为监听循环读取快照并记录可操作错误。"""
    try:
        return read_clipboard_item()
    except (ClipboardReadError, WindowsClipboardError, pyperclip.PyperclipException) as exc:
        logger.error("Clipboard read failed: %s", exc)
        return None


def _upload_item(item: LocalClipboardItem) -> None:
    """根据剪贴板类型调用对应上传接口。"""
    if item.kind == "text":
        content = item.content
        if not isinstance(content, str):
            raise TypeError("文本剪贴板内容必须是字符串")
        api_client.upload(content)
        logger.info("Text clipboard uploaded", extra={"size": len(content.encode("utf-8"))})
        return
    content = item.content
    if not isinstance(content, bytes):
        raise TypeError("图片剪贴板内容必须是字节串")
    api_client.upload_image(content)
    logger.info("Image clipboard uploaded", extra={"size": len(content)})
