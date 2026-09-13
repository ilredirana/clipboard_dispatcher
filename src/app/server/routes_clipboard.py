"""剪贴板同步 API，只提供上传、下载和 SSE 通知。"""

import json
import logging
from io import BytesIO
from typing import Literal

import config_manager
from config_schema import DeviceRecord
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, ValidationError
from server.auth import verify_device_token
from server.sse import event_bus
from server.storage import ClipboardEntry, ClipboardKind, MemoryStorage, SyncActivitySnapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clipboard", tags=["clipboard"])
storage: MemoryStorage | None = None

_IMAGE_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_MAX_IMAGE_PIXELS = 400_000_000
_MAX_TEXT_REQUEST_OVERHEAD = 8_192

# Pillow 的默认解压炸弹阈值低于服务端允许的图片像素上限。
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS


class TextUploadPayload(BaseModel):
    """文本上传请求。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["text"]
    content: str


def set_storage(value: MemoryStorage) -> None:
    """注入存储实例。"""
    global storage
    storage = value


def _get_storage() -> MemoryStorage:
    """获取已初始化的存储实例。"""
    if storage is None:
        raise RuntimeError("剪贴板存储尚未初始化")
    return storage


def get_sync_activity_snapshot() -> SyncActivitySnapshot:
    """返回控制台需要的内存同步活动统计。"""
    return _get_storage().get_sync_activity_snapshot()


async def _read_limited_body(request: Request, maximum: int) -> bytes:
    """以流式方式读取请求体，并在超过限制时中止。"""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 请求头无效") from exc
        if length > maximum:
            raise HTTPException(status_code=413, detail=f"请求体超过最大 {maximum} 字节限制")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > maximum:
            raise HTTPException(status_code=413, detail=f"请求体超过最大 {maximum} 字节限制")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_image(content: bytes) -> str:
    """验证图片格式和像素总量，并返回可信 MIME 类型。"""
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            width, height = image.size
            image_format = image.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail="请求体不是有效的 PNG、JPEG 或 WebP 图片") from exc
    if width < 1 or height < 1 or width * height > _MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=422, detail=f"图片像素总量必须在 1 到 {_MAX_IMAGE_PIXELS} 之间")
    if image_format not in _IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="仅支持 PNG、JPEG 和 WebP 图片")
    return _IMAGE_MIME_TYPES[image_format]


def _parse_text_upload(body: bytes) -> TextUploadPayload:
    """校验文本 JSON 上传请求；图片只允许使用原始二进制请求体上传。"""
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="文本上传请求必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="文本上传请求必须是 JSON 对象")
    try:
        return TextUploadPayload.model_validate(value)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="文本上传格式无效；图片请使用 application/octet-stream 或 image/* 原始二进制上传",
        ) from exc


def _event_payload(entry: ClipboardEntry) -> dict[str, str]:
    """生成 SSE 通知载荷。"""
    return {"source_device_id": entry.source_device_id}


def _download_headers(entry: ClipboardEntry | None) -> dict[str, str]:
    """生成下载响应头；内容本体不再携带元数据或 Base64 包装。"""
    headers = {"Cache-Control": "no-store"}
    if entry is None:
        headers["X-Clipboard-Kind"] = "empty"
        return headers
    headers["X-Clipboard-Kind"] = entry.kind.value
    return headers


@router.post("/upload")
async def upload(
    request: Request,
    background_tasks: BackgroundTasks,
    device: DeviceRecord = Depends(verify_device_token),
) -> dict[str, str]:
    """上传文本或图片剪贴板。

    文本：application/json，形如 {"kind":"text","content":"..."}。
    图片：application/octet-stream 或 image/*，请求体直接发送图片原始字节。
    """
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/") or content_type == "application/octet-stream":
        if not config_manager.get("server.enable_image_sync", True):
            raise HTTPException(status_code=403, detail="服务端已关闭图片同步")
        content = await _read_limited_body(request, config_manager.get("server.max_image_size", 5_242_880))
        if not content:
            raise HTTPException(status_code=422, detail="图片请求体不能为空")
        entry = _get_storage().set_image(content, _validate_image(content), device.device_id)
    else:
        maximum = config_manager.get("server.max_payload_size", 5_242_880)
        payload = _parse_text_upload(await _read_limited_body(request, maximum + _MAX_TEXT_REQUEST_OVERHEAD))
        content = payload.content
        if len(content.encode("utf-8")) > maximum:
            raise HTTPException(status_code=413, detail=f"文本负载超过最大 {maximum} 字节限制")
        entry = _get_storage().set_text(content, device.device_id)

    logger.info(
        "Clipboard uploaded",
        extra={"device_id": device.device_id, "kind": entry.kind.value, "size": entry.size},
    )
    background_tasks.add_task(event_bus.broadcast, _event_payload(entry))
    return {"kind": entry.kind.value}


@router.get("/download", response_class=Response)
async def download(device: DeviceRecord = Depends(verify_device_token)) -> Response:
    """单请求下载当前最新剪贴板，不使用 Base64。

    - 204：云端剪贴板为空。
    - text/plain; charset=utf-8：响应体就是 UTF-8 文本字节。
    - image/png、image/jpeg、image/webp：响应体就是图片原始字节。
    """
    entry = _get_storage().get()
    if entry is None:
        return Response(status_code=204, headers=_download_headers(None))

    _get_storage().record_download(entry, device.device_id)
    headers = _download_headers(entry)

    if entry.kind == ClipboardKind.TEXT:
        return Response(
            content=entry.content,
            media_type="text/plain; charset=utf-8",
            headers=headers,
        )

    return Response(
        content=entry.content,
        media_type=entry.mime_type,
        headers=headers,
    )


@router.get("/stream")
async def stream(_device: DeviceRecord = Depends(verify_device_token)) -> StreamingResponse:
    """订阅 SSE 剪贴板更新通知。"""
    return StreamingResponse(
        event_bus.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
