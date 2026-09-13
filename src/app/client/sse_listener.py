"""SSE 监听器，收到通知后下载最新剪贴板并写入本机。"""

import json
import logging
import threading

import config_manager
import httpx
import pyperclip
from client import ClipboardReadError, api_client
from client.clipboard_monitor import read_clipboard_item, set_clipboard_silent, set_image_clipboard_silent
from client.windows_clipboard import WindowsClipboardError
from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

_INITIAL_RETRY = 2
_MAX_RETRY = 60


class UpdateEvent(BaseModel):
    """服务端发送的剪贴板更新通知。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_device_id: str


def sse_listener_loop(stop_event: threading.Event) -> None:
    """订阅 SSE，并在接到远程更新时下载当前剪贴板。"""
    retry_delay = _INITIAL_RETRY
    while not stop_event.is_set():
        if not config_manager.get("client.enable_auto_download", True):
            stop_event.wait(_INITIAL_RETRY)
            continue

        url = config_manager.effective_sse_url()
        if not url or "://" not in url:
            logger.warning("SSE URL not configured, retrying in %ds", retry_delay)
            stop_event.wait(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY)
            continue

        headers = api_client.request_headers()
        logger.info("SSE connecting to %s", url)
        try:
            # 连接/重连时补一次当前云端状态，弥补离线期间可能错过的事件。
            _sync_current_item()
            with (
                httpx.Client(timeout=httpx.Timeout(connect=10, read=90, write=10, pool=10)) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                response.raise_for_status()
                retry_delay = _INITIAL_RETRY
                logger.info("SSE connected")
                for line in response.iter_lines():
                    if stop_event.is_set():
                        return
                    if not config_manager.get("client.enable_auto_download", True):
                        break
                    if url != config_manager.effective_sse_url() or headers != api_client.request_headers():
                        break
                    if line.startswith("data:"):
                        _handle_event(line[len("data:"):].strip())
        except httpx.HTTPError as exc:
            logger.warning("SSE connection error: %s", exc)
        except (ClipboardReadError, api_client.ClipboardApiError, WindowsClipboardError, pyperclip.PyperclipException) as exc:
            logger.error("SSE sync error: %s", exc)

        if stop_event.is_set():
            return
        logger.info("SSE reconnecting in %ds", retry_delay)
        stop_event.wait(retry_delay)
        retry_delay = min(retry_delay * 2, _MAX_RETRY)


def _handle_event(raw: str) -> None:
    """处理一条 SSE 通知。"""
    try:
        event = UpdateEvent.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError):
        logger.warning("Ignoring invalid SSE clipboard event")
        return

    # 来源判断留在 SSE 事件层；新版 /download 不再把设备 ID 放进 HTTP Header/Body。
    if _is_own_device(event.source_device_id):
        return
    _sync_current_item()


def _sync_current_item() -> None:
    """下载并应用当前远程剪贴板。"""
    item = api_client.download()
    if item is None:
        return
    _apply_remote_item(item)


def _is_own_device(source_device_id: str) -> bool:
    """判断事件来源是否为当前客户端设备。"""
    device_id = config_manager.effective_device_id()
    return bool(device_id and device_id == source_device_id)


def _apply_remote_item(item: api_client.RemoteClipboardItem) -> None:
    """仅在内容与本地不同的情况下写入远程条目。"""
    local_item = read_clipboard_item()

    if item.kind == "text":
        if not isinstance(item.content, str):
            raise api_client.InvalidApiResponseError("远程文本条目内容类型无效")
        if local_item.kind == "text" and local_item.content == item.content:
            return
        set_clipboard_silent(item.content)
        logger.info("Text clipboard synced from cloud", extra={"size": len(item.content.encode("utf-8"))})
        return

    if not isinstance(item.content, bytes):
        raise api_client.InvalidApiResponseError("远程图片条目内容类型无效")
    if local_item.kind == "image" and local_item.content == item.content:
        return
    set_image_clipboard_silent(item.content)
    logger.info("Image clipboard synced from cloud", extra={"size": len(item.content)})