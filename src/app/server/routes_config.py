"""配置管理 API。"""

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import config_manager
from config_schema import (
    ClientConfigUpdate,
    ClientTokenUpdate,
    ServerConfigUpdate,
    ServerTokenUpdate,
    SystemConfigUpdate,
    WindowsClientConfiguration,
)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from server.auth import verify_token
from server.sse import event_bus
from server.version import APP_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

ProbeState = Literal[
    "not_configured",
    "insecure_http",
    "healthy_https",
    "certificate_error",
    "connection_error",
    "unhealthy_response",
]


@dataclass(frozen=True)
class PublicUrlProbeResult:
    """公开地址探测的结构化结果。"""

    state: ProbeState
    url: str
    detail: str


class ServerConfigResponse(BaseModel):
    enabled: bool
    host: str
    port: int
    public_base_url: str
    clipboard_ttl: int
    max_payload_size: int
    enable_image_sync: bool
    max_image_size: int


class ClientConfigResponse(BaseModel):
    server_url: str
    auth_token_configured: bool
    device_id: str
    enable_auto_upload: bool
    enable_auto_download: bool
    debounce_delay_ms: int
    ignore_empty: bool


class SystemConfigResponse(BaseModel):
    auto_start: bool


class PublicUrlStatusResponse(BaseModel):
    state: Literal[
        "not_configured",
        "insecure_http",
        "healthy_https",
        "certificate_error",
        "connection_error",
        "unhealthy_response",
    ]
    url: str
    detail: str


class ConfigResponse(BaseModel):
    server: ServerConfigResponse
    client: ClientConfigResponse
    system: SystemConfigResponse


class SyncActivityResponse(BaseModel):
    device_id: str
    source_device_id: str
    direction: Literal["upload", "download"]
    content_type: Literal["text", "image"]
    occurred_at: float


class StatusResponse(BaseModel):
    runtime_mode: str
    initialized: bool
    server_enabled: bool
    server_port: int
    sse_subscribers: int
    app_version: str
    cross_device_sync_count: int
    recent_sync_activities: list[SyncActivityResponse]


def _update_config_section(section: str, data: BaseModel) -> dict[str, str | list[str]]:
    """保存已通过模型验证的配置节。"""
    validated = data.model_dump(mode="json")
    config_manager.update_section(section, validated)
    updated = list(validated.keys())
    logger.info("Configuration section updated", extra={"section": section, "fields": updated})
    return {"status": "ok", "updated": updated}


@router.get("/all", response_model=ConfigResponse)
async def get_config(_token: str = Depends(verify_token)):
    cfg = config_manager.get_all()
    server = cfg["server"]
    client = cfg["client"]
    return {
        "server": {
            "enabled": server["enabled"],
            "host": server["host"],
            "port": server["port"],
            "public_base_url": server["public_base_url"],
            "clipboard_ttl": server["clipboard_ttl"],
            "max_payload_size": server["max_payload_size"],
            "enable_image_sync": server["enable_image_sync"],
            "max_image_size": server["max_image_size"],
        },
        "client": {
            "server_url": client["server_url"],
            "auth_token_configured": bool(client["auth_token"]),
            "device_id": client["device_id"],
            "enable_auto_upload": client["enable_auto_upload"],
            "enable_auto_download": client["enable_auto_download"],
            "debounce_delay_ms": client["debounce_delay_ms"],
            "ignore_empty": client["ignore_empty"],
        },
        "system": {"auto_start": cfg["system"]["auto_start"]},
    }


@router.put("/server")
async def update_server_config(data: ServerConfigUpdate, _token: str = Depends(verify_token)):
    if config_manager.get_runtime_mode() == "docker_server" and (
        not data.enabled or data.host != "0.0.0.0" or data.port != 8000
    ):
        raise HTTPException(
            status_code=400,
            detail="Docker 部署固定启用服务端并监听 0.0.0.0:8000；外部端口请在 docker-compose.yaml 中配置",
        )
    result = _update_config_section("server", data)
    from server.routes_clipboard import storage
    if storage:
        storage.ttl = data.clipboard_ttl
    return result


@router.get("/server/public-url-status", response_model=PublicUrlStatusResponse)
async def get_public_url_status(_token: str = Depends(verify_token)) -> PublicUrlStatusResponse:
    """检测已配置公开地址的 HTTPS 证书和健康检查响应。"""
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        probe_public_url,
        config_manager.get("server.public_base_url", ""),
        5.0,
    )
    return PublicUrlStatusResponse(
        state=result.state,
        url=result.url,
        detail=result.detail,
    )


def probe_public_url(public_url: str, timeout_seconds: float) -> PublicUrlProbeResult:
    """使用系统信任根请求公开地址的健康检查端点。"""
    if not public_url:
        return PublicUrlProbeResult(
            state="not_configured",
            url="",
            detail="尚未配置公开服务地址",
        )
    parsed = urlparse(public_url)
    if parsed.scheme != "https":
        return PublicUrlProbeResult(
            state="insecure_http",
            url=public_url,
            detail="公开服务地址未使用 HTTPS，iOS 与 Android 外网同步不应使用此地址",
        )
    health_url = f"{public_url.rstrip('/')}/health"
    request = Request(health_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read()
            if response.status != 200:
                return PublicUrlProbeResult(
                    state="unhealthy_response",
                    url=health_url,
                    detail=f"健康检查返回 HTTP {response.status}",
                )
    except HTTPError as exc:
        return PublicUrlProbeResult(
            state="unhealthy_response",
            url=health_url,
            detail=f"健康检查返回 HTTP {exc.code}",
        )
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            return PublicUrlProbeResult(
                state="certificate_error",
                url=health_url,
                detail=f"HTTPS 证书校验失败：{exc.reason}",
            )
        return PublicUrlProbeResult(
            state="connection_error",
            url=health_url,
            detail=f"无法连接公开地址：{exc.reason}",
        )
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        return PublicUrlProbeResult(
            state="unhealthy_response",
            url=health_url,
            detail=f"健康检查返回的 JSON 无效：{exc.msg}",
        )
    if not isinstance(payload, dict) or payload.get("status") != "healthy":
        return PublicUrlProbeResult(
            state="unhealthy_response",
            url=health_url,
            detail="健康检查响应未返回 status=healthy",
        )
    return PublicUrlProbeResult(
        state="healthy_https",
        url=health_url,
        detail="HTTPS 证书有效，公开健康检查正常",
    )


@router.put("/server/token")
async def update_server_token(data: ServerTokenUpdate, _token: str = Depends(verify_token)):
    config_manager.update_section("server", data.model_dump(mode="json"))
    return {"status": "ok"}


@router.put("/client")
async def update_client_config(data: ClientConfigUpdate, _token: str = Depends(verify_token)):
    return _update_config_section("client", data)


@router.put("/client/token")
async def update_client_token(data: ClientTokenUpdate, _token: str = Depends(verify_token)):
    config_manager.update_section("client", data.model_dump(mode="json"))
    return {"status": "ok"}


@router.put("/client/import")
async def import_windows_client_config(
    data: WindowsClientConfiguration,
    _token: str = Depends(verify_token),
):
    try:
        config_manager.import_windows_client_configuration(data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "Windows client configuration imported",
        extra={"server_url": data.server_url, "device_id": data.device_id},
    )
    return {"status": "ok", "updated": ["server_url", "auth_token", "device_id"]}


@router.put("/system")
async def update_system_config(data: SystemConfigUpdate, _token: str = Depends(verify_token)):
    current = config_manager.get("system.auto_start", False)
    if current != data.auto_start and not config_manager.set_auto_start(data.auto_start):
        raise HTTPException(status_code=400, detail="当前平台不支持修改开机自启")
    return {"status": "ok"}


@router.get("/status", response_model=StatusResponse)
async def get_status(_token: str = Depends(verify_token)):
    from server.routes_clipboard import get_sync_activity_snapshot

    activity_snapshot = get_sync_activity_snapshot()
    return StatusResponse(
        runtime_mode=config_manager.get_runtime_mode(),
        initialized=config_manager.is_initialized(),
        server_enabled=config_manager.get("server.enabled", False),
        server_port=config_manager.get("server.port", 8000),
        sse_subscribers=event_bus.subscriber_count,
        app_version=APP_VERSION,
        cross_device_sync_count=activity_snapshot.cross_device_sync_count,
        recent_sync_activities=[
            SyncActivityResponse(
                device_id=activity.device_id,
                source_device_id=activity.source_device_id,
                direction=activity.direction.value,
                content_type=activity.kind.value,
                occurred_at=activity.occurred_at,
            )
            for activity in activity_snapshot.recent_activities
        ],
    )
