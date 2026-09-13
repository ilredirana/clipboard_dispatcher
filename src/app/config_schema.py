"""应用配置的数据模型与外部数据校验。"""

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Port = Annotated[int, Field(strict=True, ge=1, le=65535)]
PositiveSeconds = Annotated[int, Field(strict=True, ge=1, le=31_536_000)]
PayloadSize = Annotated[int, Field(strict=True, ge=1, le=104_857_600)]
ImagePayloadSize = Annotated[int, Field(strict=True, ge=1, le=10_485_760)]
DebounceDelay = Annotated[int, Field(strict=True, ge=0, le=60_000)]
DevicePlatform = Literal["windows", "ios", "android"]
DeviceName = Annotated[str, Field(min_length=1, max_length=64)]


def validate_optional_http_url(value: str) -> str:
    """校验允许为空的 HTTP 或 HTTPS URL。"""
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("必须是完整的 HTTP 或 HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不得包含用户名或密码")
    return value.rstrip("/")


def validate_host(value: str) -> str:
    """校验监听主机名或 IP 文本。"""
    if value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("监听地址不得包含空白字符")
    return value


def validate_device_name(value: str) -> str:
    """校验设备名称，并确保其可直接作为 URL 中的设备标识。"""
    normalized = value.strip()
    if not normalized:
        raise ValueError("设备名称不能为空")
    if any(character in normalized for character in "/?#"):
        raise ValueError("设备名称不得包含 /、? 或 #")
    if any(character.isspace() and character not in " \t" for character in normalized):
        raise ValueError("设备名称不得包含换行或控制空白字符")
    return normalized


class ConfigModel(BaseModel):
    """配置模型基类，拒绝隐式类型转换并忽略无关字段。"""

    model_config = ConfigDict(extra="ignore", strict=True)


class ServerConfig(ConfigModel):
    enabled: bool
    host: Annotated[str, Field(min_length=1, max_length=255)]
    port: Port
    public_base_url: Annotated[str, Field(max_length=2048)]
    auth_token: Annotated[str, Field(min_length=32, max_length=512)]
    clipboard_ttl: PositiveSeconds
    max_payload_size: PayloadSize
    enable_image_sync: bool
    max_image_size: ImagePayloadSize

    _validate_host = field_validator("host")(validate_host)
    _validate_public_base_url = field_validator("public_base_url")(validate_optional_http_url)


class ClientConfig(ConfigModel):
    server_url: Annotated[str, Field(max_length=2048)]
    auth_token: Annotated[str, Field(max_length=4096)]
    device_id: Annotated[str, Field(max_length=128)]
    enable_auto_upload: bool
    enable_auto_download: bool
    debounce_delay_ms: DebounceDelay
    ignore_empty: bool

    _validate_server_url = field_validator("server_url")(validate_optional_http_url)


class SystemConfig(ConfigModel):
    initialized: bool
    auto_start: bool


class DeviceRecord(ConfigModel):
    device_id: DeviceName
    name: DeviceName
    platform: DevicePlatform
    created_at: Annotated[float, Field(ge=0)]
    last_seen_at: Annotated[float | None, Field(ge=0)]
    enabled: bool
    token: Annotated[str, Field(min_length=32, max_length=512)]

    _validate_device_id = field_validator("device_id")(validate_device_name)
    _validate_name = field_validator("name")(validate_device_name)

    @model_validator(mode="after")
    def validate_device_id_matches_name(self) -> "DeviceRecord":
        """设备名称同时作为唯一设备标识。"""
        if self.device_id != self.name:
            raise ValueError("设备 ID 必须与设备名称一致")
        return self


class DevicesConfig(ConfigModel):
    records: list[DeviceRecord]


class AppConfig(ConfigModel):
    server: ServerConfig
    client: ClientConfig
    system: SystemConfig
    devices: DevicesConfig


class ServerConfigUpdate(ConfigModel):
    enabled: bool
    host: Annotated[str, Field(min_length=1, max_length=255)]
    port: Port
    public_base_url: Annotated[str, Field(max_length=2048)]
    clipboard_ttl: PositiveSeconds
    max_payload_size: PayloadSize
    enable_image_sync: bool
    max_image_size: ImagePayloadSize

    _validate_host = field_validator("host")(validate_host)
    _validate_public_base_url = field_validator("public_base_url")(validate_optional_http_url)


class ClientConfigUpdate(ConfigModel):
    server_url: Annotated[str, Field(max_length=2048)]
    device_id: Annotated[str, Field(max_length=128)]
    enable_auto_upload: bool
    enable_auto_download: bool
    debounce_delay_ms: DebounceDelay
    ignore_empty: bool

    _validate_server_url = field_validator("server_url")(validate_optional_http_url)


class SystemConfigUpdate(ConfigModel):
    auto_start: bool


class ServerTokenUpdate(ConfigModel):
    auth_token: Annotated[str, Field(min_length=32, max_length=512)]


class ClientTokenUpdate(ConfigModel):
    auth_token: Annotated[str, Field(min_length=1, max_length=4096)]


class WindowsClientConfiguration(ConfigModel):
    """Windows 客户端可导入的连接配置。"""

    server_url: Annotated[str, Field(min_length=1, max_length=2048)]
    auth_token: Annotated[str, Field(min_length=32, max_length=512)]
    device_id: DeviceName

    _validate_server_url = field_validator("server_url")(validate_optional_http_url)
    _validate_device_id = field_validator("device_id")(validate_device_name)
