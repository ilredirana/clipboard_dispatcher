"""FastAPI 应用实例 — 挂载路由与全局异常处理。"""

import hashlib
import logging
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import config_manager
import server.auth as auth_module
from config_schema import ServerTokenUpdate
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from server import routes_clipboard, routes_config, routes_devices, routes_provisioning
from server.auth import check_web_auth, consume_local_login_code, set_auth_cookie
from server.storage import MemoryStorage
from server.version import APP_VERSION

logger = logging.getLogger(__name__)

_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_WINDOW_SECONDS = 300
_login_failures: dict[str, deque[float]] = {}
_login_failures_lock = threading.Lock()


def _get_asset_version(resource_dir: str) -> str:
    """根据前端静态资源内容生成缓存版本。"""
    digest = hashlib.sha256()
    for relative_path in ("static/css/style.css", "static/js/app.js", "static/favicon.svg"):
        digest.update((Path(resource_dir) / relative_path).read_bytes())
    return digest.hexdigest()[:12]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Clipboard Dispatcher server started on port %s",
                config_manager.get("server.port"))
    yield
    logger.info("Clipboard Dispatcher server stopped")


def _render_page(request: Request, template_name: str, context: dict, fallback: str):
    """渲染 Jinja2 模板页面，若模板未加载则返回纯 HTML 回退内容。"""
    if auth_module.templates:
        return auth_module.templates.TemplateResponse(request=request, name=template_name, context=context)
    return HTMLResponse(fallback)


def _get_client_address(request: Request) -> str:
    """返回当前 TCP 连接来源地址，不信任可伪造的转发请求头。"""
    if request.client is None:
        return "unknown"
    return request.client.host


def _is_login_rate_limited(client_address: str) -> bool:
    """判断指定来源在当前窗口内是否超过失败登录次数。"""
    now = time.monotonic()
    with _login_failures_lock:
        failures = _login_failures.get(client_address)
        if failures is None:
            return False
        while failures and now - failures[0] >= _LOGIN_FAILURE_WINDOW_SECONDS:
            failures.popleft()
        if not failures:
            del _login_failures[client_address]
            return False
        return len(failures) >= _LOGIN_FAILURE_LIMIT


def _record_login_failure(client_address: str) -> None:
    """记录一次失败登录并清理过期记录。"""
    now = time.monotonic()
    with _login_failures_lock:
        failures = _login_failures.setdefault(client_address, deque())
        while failures and now - failures[0] >= _LOGIN_FAILURE_WINDOW_SECONDS:
            failures.popleft()
        failures.append(now)


def _clear_login_failures(client_address: str) -> None:
    """成功登录后清除该来源的失败记录。"""
    with _login_failures_lock:
        _login_failures.pop(client_address, None)


def create_app() -> FastAPI:
    from fastapi.templating import Jinja2Templates

    app = FastAPI(
        title="Clipboard Dispatcher",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """返回直接可读的 HTTP 错误。"""
        return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        """返回请求校验错误。"""
        return JSONResponse(
            status_code=422,
            content={"message": "请求数据校验失败", "errors": jsonable_encoder(exc.errors())},
        )

    @app.middleware("http")
    async def set_security_headers(request: Request, call_next):
        """为管理页面和下载接口设置基础浏览器安全响应头。"""
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    ttl = config_manager.get("server.clipboard_ttl", 300)
    storage = MemoryStorage(ttl=ttl)
    routes_clipboard.set_storage(storage)

    app.include_router(routes_clipboard.router)
    app.include_router(routes_config.router)
    app.include_router(routes_devices.router)

    if config_manager.get("server.enabled"):
        app.include_router(routes_provisioning.router)

    resource_dir = config_manager.get_resource_dir()
    try:
        asset_version = _get_asset_version(resource_dir)
        app.mount("/static", StaticFiles(directory=f"{resource_dir}/static"), name="static")
        auth_module.templates = Jinja2Templates(directory=f"{resource_dir}/templates")
        auth_module.templates.env.globals["asset_version"] = asset_version
    except (OSError, RuntimeError) as exc:
        logger.warning("UI resources not found: %s", exc)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        """兼容浏览器默认的 favicon.ico 请求。"""
        return FileResponse(Path(resource_dir) / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if not config_manager.is_initialized():
            return RedirectResponse(url="/setup", status_code=303)
        if auth_module.templates:
            return auth_module.templates.TemplateResponse(request=request, name="login.html")
        return HTMLResponse("<h1>Login</h1><p>UI resources not found.</p>")

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request):
        if not config_manager.is_initialized():
            return RedirectResponse(url="/setup", status_code=303)
        client_address = _get_client_address(request)
        if _is_login_rate_limited(client_address):
            return HTMLResponse("<h1>Too Many Requests</h1><p>请 5 分钟后重试。</p>", status_code=429)
        form = await request.form()
        token = form.get("token", "")
        actual_token = config_manager.get("server.auth_token", "")
        if token and actual_token and secrets.compare_digest(str(token), actual_token):
            _clear_login_failures(client_address)
            resp = RedirectResponse(url="/", status_code=303)
            public_base_url = config_manager.get("server.public_base_url", "")
            use_secure_cookie = request.url.scheme == "https" or urlsplit(public_base_url).scheme == "https"
            set_auth_cookie(resp, str(token), use_secure_cookie)
            return resp
        _record_login_failure(client_address)
        if auth_module.templates:
            return auth_module.templates.TemplateResponse(request=request, name="login.html", context={"error": "无效的 Auth Token"})
        return HTMLResponse("<h1>Login Failed</h1><p>Invalid Token</p>")

    @app.get("/local-login/{code}")
    async def local_login(request: Request, code: str):
        """使用 Windows 托盘创建的一次性本机登录码写入浏览器 Cookie。"""
        if not config_manager.is_initialized():
            return RedirectResponse(url="/setup", status_code=303)
        if not consume_local_login_code(code, _get_client_address(request)):
            raise HTTPException(status_code=403, detail="本机登录链接无效、已过期或不是本机访问")
        response = RedirectResponse(url="/", status_code=303)
        set_auth_cookie(response, config_manager.get("server.auth_token", ""), False)
        return response

    def render_setup_page(request: Request, suggested_token: str, error: str, status_code: int):
        """渲染首次初始化页面，资源缺失时提供明确的文本响应。"""
        if auth_module.templates:
            return auth_module.templates.TemplateResponse(
                request=request,
                name="setup.html",
                context={
                    "suggested_token": suggested_token,
                    "error": error,
                    "app_version": APP_VERSION,
                },
                status_code=status_code,
            )
        return HTMLResponse(
            "<h1>首次初始化</h1><p>请恢复 UI 资源后访问此页面完成初始化。</p>",
            status_code=status_code,
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_get(request: Request):
        if config_manager.is_initialized():
            return RedirectResponse(url="/login", status_code=303)
        return render_setup_page(request, secrets.token_urlsafe(32), "", 200)

    @app.post("/setup", response_class=HTMLResponse)
    async def setup_post(request: Request):
        if config_manager.is_initialized():
            return RedirectResponse(url="/login", status_code=303)
        form = await request.form()
        auth_token = str(form.get("auth_token", ""))
        confirm_auth_token = str(form.get("confirm_auth_token", ""))
        if auth_token != confirm_auth_token:
            return render_setup_page(request, auth_token, "两次输入的管理员 Token 不一致", 422)
        try:
            validated = ServerTokenUpdate(auth_token=auth_token)
        except ValidationError as exc:
            return render_setup_page(request, auth_token, f"管理员 Token 无效：{exc.errors()[0]['msg']}", 422)
        try:
            config_manager.complete_initialization(validated.auth_token)
        except RuntimeError:
            return RedirectResponse(url="/login", status_code=303)
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        if not config_manager.is_initialized():
            return RedirectResponse(url="/setup", status_code=303)
        if not check_web_auth(request):
            return RedirectResponse(url="/login", status_code=303)

        return _render_page(request, "dashboard.html", {
            "server_enabled": config_manager.get("server.enabled", False),
            "app_version": APP_VERSION,
        }, "<h1>Clipboard Dispatcher</h1><p>UI resources not found.</p>")

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        if not config_manager.is_initialized():
            return RedirectResponse(url="/setup", status_code=303)
        if not check_web_auth(request):
            return RedirectResponse(url="/login", status_code=303)

        runtime_mode = config_manager.get_runtime_mode()
        return _render_page(request, "settings.html", {
            "server_enabled": config_manager.get("server.enabled", False),
            "desktop_runtime": runtime_mode in {"windows_all_in_one", "client_only"},
            "docker_runtime": runtime_mode == "docker_server",
            "app_version": APP_VERSION,
        }, "<h1>Settings</h1><p>UI resources not found.</p>")

    @app.get("/devices", response_class=HTMLResponse)
    async def devices_page(request: Request):
        if not config_manager.is_initialized():
            return RedirectResponse(url="/setup", status_code=303)
        if not check_web_auth(request):
            return RedirectResponse(url="/login", status_code=303)

        return _render_page(request, "devices.html", {
            "server_enabled": config_manager.get("server.enabled", False),
            "app_version": APP_VERSION,
        }, "<h1>Devices</h1><p>UI resources not found.</p>")

    @app.get("/health")
    async def health():
        """健康检查端点，无需鉴权。"""
        return {
            "status": "healthy",
            "initialized": config_manager.is_initialized(),
            "version": APP_VERSION,
        }

    return app
