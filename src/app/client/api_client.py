"""HTTP 客户端，与服务端的上传、下载接口通信。"""

import logging
import time
from dataclasses import dataclass
from typing import Literal

import config_manager
import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_ATTEMPTS = 3
_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_client: httpx.Client | None = None


class ClipboardApiError(RuntimeError):
    """剪贴板 API 调用失败。"""


class ServerApiError(ClipboardApiError):
    """服务端返回错误响应。"""


class InvalidApiResponseError(ClipboardApiError):
    """服务端响应结构无效。"""


class NetworkApiError(ClipboardApiError):
    """重试后仍无法完成网络请求。"""


@dataclass(frozen=True)
class RemoteClipboardItem:
    """客户端已下载的远程剪贴板内容。"""

    kind: Literal["text", "image"]
    content: str | bytes


def _get_client() -> httpx.Client:
    """获取复用的 HTTP 客户端。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(timeout=_TIMEOUT)
    return _client


def request_headers() -> dict[str, str]:
    """构建设备鉴权请求头。"""
    return {"Authorization": f"Bearer {config_manager.effective_auth_token()}"}


def _base_url() -> str:
    """返回当前有效的服务端地址。"""
    return config_manager.effective_server_url().rstrip("/")


def _raise_server_error(response: httpx.Response) -> None:
    """将非成功响应转换为包含服务端消息的异常。"""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            raise ServerApiError(f"服务端返回 HTTP {response.status_code}: {detail}")
        message = payload.get("message")
        if isinstance(message, str):
            raise ServerApiError(f"服务端返回 HTTP {response.status_code}: {message}")

    body = response.text.strip()
    suffix = f": {body}" if body else ""
    raise ServerApiError(f"服务端返回 HTTP {response.status_code}{suffix}")


def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    """发送请求，并仅对网络异常进行有限重试。"""
    last_error: httpx.HTTPError | None = None
    custom_headers = kwargs.pop("headers", None)
    headers = request_headers()
    if isinstance(custom_headers, dict):
        headers.update({str(key): str(value) for key, value in custom_headers.items()})

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = _get_client().request(
                method,
                f"{_base_url()}{path}",
                headers=headers,
                **kwargs,
            )
            if response.is_error:
                _raise_server_error(response)
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning(
                "Clipboard API request failed",
                extra={"path": path, "attempt": attempt, "error": str(exc)},
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(attempt)

    raise NetworkApiError(
        f"请求 {path} 在 {_MAX_ATTEMPTS} 次尝试后失败: {last_error}"
    ) from last_error


def upload(content: str) -> None:
    """以 JSON 上传文本剪贴板。"""
    _request(
        "POST",
        "/api/clipboard/upload",
        json={"kind": "text", "content": content},
    )
    logger.info("Text clipboard uploaded", extra={"size": len(content.encode("utf-8"))})


def upload_image(content: bytes) -> None:
    """以原始二进制请求体上传图片，不再使用 Base64。"""
    if not content:
        raise ValueError("图片剪贴板内容不能为空")

    _request(
        "POST",
        "/api/clipboard/upload",
        content=content,
        headers={
            **request_headers(),
            "Content-Type": "application/octet-stream",
        },
    )
    logger.info("Image clipboard uploaded", extra={"size": len(content)})


def download() -> RemoteClipboardItem | None:
    """按 Content-Type 下载当前剪贴板；文本为 UTF-8，图片为原始字节。"""
    response = _request("GET", "/api/clipboard/download")

    if response.status_code == 204:
        return None

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    if content_type == "text/plain":
        try:
            text = response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidApiResponseError("服务端文本响应不是有效的 UTF-8") from exc
        return RemoteClipboardItem("text", text)

    if content_type in _IMAGE_MIME_TYPES:
        if not response.content:
            raise InvalidApiResponseError("服务端返回了空图片响应")
        return RemoteClipboardItem("image", response.content)

    raise InvalidApiResponseError(
        f"服务端返回了不支持的 Content-Type: {content_type or '未提供'}"
    )