"""Bearer Token 与 HttpOnly Cookie 鉴权。"""

import ipaddress
import secrets
import threading
import time

import config_manager
from config_schema import DeviceRecord
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates

_bearer = HTTPBearer(auto_error=False)
_LOCAL_LOGIN_CODE_TTL_SECONDS = 60.0
_local_login_codes: dict[str, float] = {}
_local_login_codes_lock = threading.Lock()

templates: Jinja2Templates | None = None


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """验证 Token 的 FastAPI 依赖注入。

    验证优先级：Authorization Header > HttpOnly Cookie。
    验证失败抛出 401。
    """
    _require_initialized()
    actual_token = config_manager.get("server.auth_token", "")

    if credentials and actual_token and secrets.compare_digest(credentials.credentials, actual_token):
        return credentials.credentials

    cookie_token = request.cookies.get("auth_token", "")
    if cookie_token and actual_token and secrets.compare_digest(cookie_token, actual_token):
        return cookie_token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing auth token",
    )


def _require_initialized() -> None:
    """确保服务已完成首次初始化。"""
    if not config_manager.is_initialized():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务尚未初始化，请先访问 /setup 完成管理员配置",
        )


async def verify_device_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> DeviceRecord:
    """验证同步设备 Token，Windows 一体化模式仅允许本机使用管理员 Token。"""
    _require_initialized()
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing device token",
        )
    if _is_windows_all_in_one_local_request(request, credentials.credentials):
        return DeviceRecord(
            device_id=config_manager.effective_device_id(),
            name=config_manager.effective_device_id(),
            platform="windows",
            created_at=0,
            last_seen_at=None,
            enabled=True,
            token=credentials.credentials,
        )
    device = config_manager.get_device_by_token(credentials.credentials)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or disabled device token",
        )
    config_manager.record_device_seen(device.device_id, time.time())
    return device


def _is_windows_all_in_one_local_request(request: Request, token: str) -> bool:
    """判断本机 Windows 一体化客户端是否使用管理员 Token 请求同步接口。"""
    if config_manager.get_runtime_mode() != "windows_all_in_one":
        return False
    if request.client is None or not _is_loopback_address(request.client.host):
        return False
    admin_token = config_manager.get("server.auth_token", "")
    return bool(admin_token and secrets.compare_digest(token, admin_token))


def set_auth_cookie(response: Response, token: str, secure: bool) -> None:
    """设置 HTTP-only 鉴权 Cookie，有效期 30 天。"""
    response.set_cookie(
        "auth_token",
        token,
        max_age=86400 * 30,
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def check_web_auth(request: Request) -> bool:
    """Web 页面鉴权守卫。

    检查 HttpOnly Cookie 中的 auth_token。
    """
    if not config_manager.is_initialized():
        return False
    token = request.cookies.get("auth_token", "")
    actual_token = config_manager.get("server.auth_token", "")
    return bool(token and actual_token and secrets.compare_digest(token, actual_token))


def create_local_login_code() -> str:
    """创建仅供 Windows 本机托盘使用的一次性登录码。"""
    now = time.monotonic()
    code = secrets.token_urlsafe(32)
    with _local_login_codes_lock:
        _purge_expired_local_login_codes(now)
        _local_login_codes[code] = now + _LOCAL_LOGIN_CODE_TTL_SECONDS
    return code


def consume_local_login_code(code: str, client_address: str) -> bool:
    """仅允许本机回环地址使用一次性登录码。"""
    if not _is_loopback_address(client_address):
        return False
    now = time.monotonic()
    with _local_login_codes_lock:
        _purge_expired_local_login_codes(now)
        expires_at = _local_login_codes.pop(code, None)
    return expires_at is not None and expires_at > now


def _is_loopback_address(client_address: str) -> bool:
    """判断 TCP 客户端地址是否为本机回环地址。"""
    try:
        return ipaddress.ip_address(client_address).is_loopback
    except ValueError:
        return False


def _purge_expired_local_login_codes(now: float) -> None:
    """清理过期的一次性登录码，调用方需持有锁。"""
    expired_codes = [code for code, expires_at in _local_login_codes.items() if expires_at <= now]
    for code in expired_codes:
        del _local_login_codes[code]
