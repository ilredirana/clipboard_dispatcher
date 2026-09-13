"""配置管理器 — JSON 持久化 + 开机自启 + 运行时热更新。

配置文件位置:
  - exe 模式: <exe所在目录>/config.json
  - 源码模式: 项目根目录/config.json
"""

import builtins
import copy
import json
import logging
import os
import secrets
import socket
import sys
import threading
from contextlib import suppress
from typing import Any, Literal

from config_schema import (
    AppConfig,
    DevicePlatform,
    DeviceRecord,
    ServerTokenUpdate,
    WindowsClientConfiguration,
    validate_device_name,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.json"
_DOCKER_ADMIN_TOKEN_ENV = "CLIPBOARD_DISPATCHER_ADMIN_TOKEN"
_config: dict = {}
_config_path: str = ""
_lock = threading.RLock()

RuntimeMode = Literal["windows_all_in_one", "docker_server", "server_only", "client_only"]
DeviceCredential = tuple[DeviceRecord, str]


def get_config_dir() -> str:
    """获取配置目录路径。按优先级：Docker → 打包 EXE → Linux 服务端 → 开发环境。"""
    if os.environ.get("DOCKER_MODE") == "1":
        return "/app/data"
    if getattr(sys, "frozen", False) and sys.platform != "win32":
        return os.path.join(os.path.expanduser("~"), ".clipboard_dispatcher")
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    if sys.platform == "linux" and os.environ.get("SERVER_ONLY") == "1":
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_dir() -> str:
    """获取 UI 资源目录。打包后指向 PyInstaller 解压目录，开发时指向 app/ui。"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "ui")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def get_tasker_dir() -> str:
    """获取 Tasker 导入任务目录。"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "tasker")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tasker")


def get_runtime_mode() -> RuntimeMode:
    """返回当前交付形态，供管理界面和健康检查使用。"""
    if os.environ.get("DOCKER_MODE") == "1":
        return "docker_server"
    if os.environ.get("SERVER_ONLY") == "1" or sys.platform == "linux":
        return "server_only"
    if get("server.enabled", True):
        return "windows_all_in_one"
    return "client_only"


def _default_config() -> dict:
    """返回默认配置模板。新增配置节在此定义，init() 会自动合并到已有配置。"""
    return {
        "server": {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8000,
            "public_base_url": "",
            "auth_token": secrets.token_urlsafe(32),
            "clipboard_ttl": 300,
            "max_payload_size": 5_242_880,
            "enable_image_sync": True,
            "max_image_size": 5_242_880,
        },
        "client": {
            "server_url": "",
            "auth_token": "",
            "device_id": "",
            "enable_auto_upload": True,
            "enable_auto_download": True,
            "debounce_delay_ms": 500,
            "ignore_empty": True,
        },
        "system": {
            "initialized": False,
            "auto_start": False,
        },
        "devices": {
            "records": [],
        },
    }


def _get_optional_docker_admin_token() -> str:
    """读取并校验可选的 Docker 管理员 Token。"""
    token = os.environ.get(_DOCKER_ADMIN_TOKEN_ENV, "")
    if not token:
        return ""
    try:
        return ServerTokenUpdate(auth_token=token).auth_token
    except ValidationError as exc:
        raise RuntimeError(
            f"环境变量 {_DOCKER_ADMIN_TOKEN_ENV} 无效，管理员 Token 必须为 32 至 512 个字符"
        ) from exc


def init() -> None:
    """在平台默认目录初始化配置管理器。"""
    init_at(get_config_dir())


def _merge_missing(current: dict, defaults: dict) -> dict:
    """递归补齐缺失配置，返回新的字典。"""
    merged = copy.deepcopy(current)
    for key, default_value in defaults.items():
        current_value = merged.get(key)
        if isinstance(default_value, dict) and isinstance(current_value, dict):
            merged[key] = _merge_missing(current_value, default_value)
        elif key not in merged:
            merged[key] = copy.deepcopy(default_value)
    return merged


def _validate_config(data: dict) -> dict:
    """验证完整配置并转换为可序列化字典。"""
    return AppConfig.model_validate(data).model_dump(mode="json")


def _migrate_device_tokens(data: dict) -> dict:
    """将旧设备哈希记录迁移为可查看的明文 Token。"""
    migrated = copy.deepcopy(data)
    devices = migrated.get("devices")
    if not isinstance(devices, dict):
        return migrated
    records = devices.get("records")
    if not isinstance(records, list):
        return migrated
    client = migrated.get("client")
    client_device_id = client.get("device_id", "") if isinstance(client, dict) else ""
    for record in records:
        if not isinstance(record, dict) or record.get("token"):
            continue
        token = secrets.token_urlsafe(32)
        record["token"] = token
        record.pop("token_hash", None)
        if isinstance(client, dict) and record.get("device_id") == client_device_id:
            client["auth_token"] = token
    return migrated


def _migrate_device_identifiers(data: dict) -> dict:
    """将历史随机设备 ID 迁移为设备名称，并拒绝重复名称。"""
    migrated = copy.deepcopy(data)
    devices = migrated.get("devices")
    if not isinstance(devices, dict):
        return migrated
    records = devices.get("records")
    if not isinstance(records, list):
        return migrated
    identifiers: dict[str, str] = {}
    names: set[str] = builtins.set()
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_name = record.get("name")
        if not isinstance(raw_name, str):
            continue
        name = validate_device_name(raw_name)
        if len(name) > 64:
            raise RuntimeError(f"设备名称超过 64 个字符，无法作为设备 ID：{name}")
        if name in names:
            raise RuntimeError(f"设备名称重复，无法迁移为唯一设备 ID：{name}")
        names.add(name)
        raw_device_id = record.get("device_id")
        if isinstance(raw_device_id, str):
            identifiers[raw_device_id] = name
        record["name"] = name
        record["device_id"] = name
    client = migrated.get("client")
    if isinstance(client, dict):
        current_device_id = client.get("device_id")
        if isinstance(current_device_id, str) and current_device_id in identifiers:
            client["device_id"] = identifiers[current_device_id]
    return migrated


def _is_windows_all_in_one_configuration(data: dict) -> bool:
    """判断给定配置是否对应 Windows 服务端与客户端一体化运行。"""
    if os.environ.get("DOCKER_MODE") == "1" or os.environ.get("SERVER_ONLY") == "1":
        return False
    server = data.get("server")
    return sys.platform == "win32" and isinstance(server, dict) and bool(server.get("enabled"))


def _clear_windows_all_in_one_client_credentials(data: dict) -> dict:
    """移除 Windows 一体化运行时不再使用的独立客户端连接配置。"""
    migrated = copy.deepcopy(data)
    if not _is_windows_all_in_one_configuration(migrated):
        return migrated
    client = migrated.get("client")
    if not isinstance(client, dict):
        return migrated
    client["server_url"] = ""
    client["auth_token"] = ""
    client["device_id"] = ""
    return migrated


def init_at(config_dir: str) -> None:
    """在明确指定的目录初始化配置管理器。"""
    global _config, _config_path
    _config_path = os.path.join(config_dir, _CONFIG_FILENAME)
    os.makedirs(os.path.dirname(_config_path), exist_ok=True)

    is_docker = os.environ.get("DOCKER_MODE") == "1"
    docker_admin_token = _get_optional_docker_admin_token() if is_docker else ""
    defaults = _default_config()

    try:
        with open(_config_path, encoding="utf-8") as f:
            _config = json.load(f)
    except FileNotFoundError:
        if is_docker:
            if docker_admin_token:
                defaults["server"]["auth_token"] = docker_admin_token
            defaults["system"]["initialized"] = True
        _config = _validate_config(defaults)
        save()
        logger.info("Default config created at %s", _config_path)
        return

    merged = _migrate_device_identifiers(_migrate_device_tokens(_merge_missing(_config, defaults)))
    legacy_devices_missing = "devices" not in _config
    existing_system = _config.get("system")
    if not isinstance(existing_system, dict) or "initialized" not in existing_system:
        merged["system"]["initialized"] = True
    if legacy_devices_missing and merged["server"]["enabled"]:
        merged["devices"]["records"] = []
    if is_docker:
        merged["server"]["enabled"] = True
        merged["server"]["host"] = "0.0.0.0"
        merged["server"]["port"] = 8000
        if docker_admin_token:
            merged["server"]["auth_token"] = docker_admin_token
        merged["system"]["initialized"] = True
    merged = _clear_windows_all_in_one_client_credentials(merged)
    validated = _validate_config(merged)
    changed = validated != _config
    _config = validated
    if changed:
        save()
        logger.info("Config migrated with missing fields")
    logger.info("Config loaded from %s", _config_path)


def _save_unlocked() -> None:
    """将配置写入磁盘（内部使用，调用方需持有 _lock）。"""
    temp_path = f"{_config_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(_config, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, _config_path)

def save() -> None:
    """线程安全地将配置写入磁盘。"""
    with _lock:
        _save_unlocked()


def get(key: str, default: Any = None) -> Any:
    """点号分隔路径读取配置值。如 get("server.port", 8000)。"""
    with _lock:
        keys = key.split(".")
        val: Any = _config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return copy.deepcopy(val)


def set(key: str, value: Any) -> None:
    """线程安全的点号分隔路径写入配置值并持久化。值未变则跳过写入。"""
    with _lock:
        keys = key.split(".")
        candidate = copy.deepcopy(_config)
        cfg = candidate
        for k in keys[:-1]:
            cfg = cfg.setdefault(k, {})
        if cfg.get(keys[-1]) == value:
            return
        cfg[keys[-1]] = value
        validated = _validate_config(candidate)
        _config.clear()
        _config.update(validated)
        _save_unlocked()


def get_all() -> dict:
    """返回完整配置的深拷贝，避免外部意外修改内部状态。"""
    with _lock:
        return copy.deepcopy(_config)


def is_initialized() -> bool:
    """返回当前服务是否已完成首次管理员初始化。"""
    return bool(get("system.initialized", False))


def _make_device_record(name: str, platform: DevicePlatform, token: str, created_at: float) -> DeviceRecord:
    """根据明文 Token 创建设备记录。"""
    return DeviceRecord(
        device_id=name,
        name=name,
        platform=platform,
        created_at=created_at,
        last_seen_at=None,
        enabled=True,
        token=token,
    )


def create_device(name: str, platform: DevicePlatform, created_at: float) -> DeviceCredential:
    """创建设备并持久化明文 Token。"""
    token = secrets.token_urlsafe(32)
    record = _make_device_record(name, platform, token, created_at)
    with _lock:
        candidate = copy.deepcopy(_config)
        _ensure_device_name_available(candidate["devices"]["records"], record.name, "")
        candidate["devices"]["records"].append(record.model_dump(mode="json"))
        validated = _validate_config(candidate)
        _config.clear()
        _config.update(validated)
        _save_unlocked()
    return record, token


def rotate_device_token(device_id: str) -> DeviceCredential:
    """重新签发设备 Token，旧 Token 在保存完成后立即失效。"""
    token = secrets.token_urlsafe(32)
    with _lock:
        candidate = copy.deepcopy(_config)
        records = candidate["devices"]["records"]
        for index, record in enumerate(records):
            if record["device_id"] != device_id:
                continue
            if not record["enabled"]:
                raise ValueError(f"设备已禁用，无法重新签发凭据：{device_id}")
            updated_record = {**record, "token": token}
            validated_record = DeviceRecord.model_validate(updated_record)
            records[index] = validated_record.model_dump(mode="json")
            validated = _validate_config(candidate)
            _config.clear()
            _config.update(validated)
            _save_unlocked()
            return validated_record, token
    raise KeyError(f"设备不存在：{device_id}")


def list_devices() -> list[DeviceRecord]:
    """返回包含当前明文 Token 的全部设备记录。"""
    records = get("devices.records", [])
    return [DeviceRecord.model_validate(record) for record in records]


def get_device(device_id: str) -> DeviceRecord:
    """按设备 ID 返回当前设备记录。"""
    record = next((item for item in list_devices() if item.device_id == device_id), None)
    if record is None:
        raise KeyError(f"设备不存在：{device_id}")
    return record


def get_device_by_token(token: str) -> DeviceRecord | None:
    """使用明文 Token 查找启用设备，管理员 Token 不参与匹配。"""
    for record in list_devices():
        if record.enabled and secrets.compare_digest(record.token, token):
            return record
    return None


def update_device_name(device_id: str, name: str) -> DeviceRecord:
    """更新设备名称，并同步更新作为设备 ID 的名称。"""
    with _lock:
        candidate = copy.deepcopy(_config)
        records = candidate["devices"]["records"]
        _ensure_device_name_available(records, name, device_id)
        for index, record in enumerate(records):
            if record["device_id"] != device_id:
                continue
            updated_record = DeviceRecord.model_validate({**record, "name": name, "device_id": name})
            records[index] = updated_record.model_dump(mode="json")
            if candidate["client"].get("device_id") == device_id:
                candidate["client"]["device_id"] = updated_record.device_id
            validated = _validate_config(candidate)
            _config.clear()
            _config.update(validated)
            _save_unlocked()
            return updated_record
    raise KeyError(f"设备不存在：{device_id}")


def update_device_enabled(device_id: str, enabled: bool) -> DeviceRecord:
    """启用或禁用指定设备并返回更新后的记录。"""
    return _update_device(device_id, {"enabled": enabled})


def _update_device(device_id: str, changes: dict[str, str | bool | float | None]) -> DeviceRecord:
    """应用单台设备字段变更并持久化。"""
    with _lock:
        candidate = copy.deepcopy(_config)
        records = candidate["devices"]["records"]
        for index, record in enumerate(records):
            if record["device_id"] != device_id:
                continue
            updated_record = {**record, **changes}
            validated_record = DeviceRecord.model_validate(updated_record)
            records[index] = validated_record.model_dump(mode="json")
            validated = _validate_config(candidate)
            _config.clear()
            _config.update(validated)
            _save_unlocked()
            return validated_record
    raise KeyError(f"设备不存在：{device_id}")


def _ensure_device_name_available(records: list[dict], name: str, current_device_id: str) -> None:
    """确保设备名称未被其他设备或本机一体化客户端占用。"""
    normalized_name = validate_device_name(name)
    if len(normalized_name) > 64:
        raise ValueError("设备名称不得超过 64 个字符")
    if (
        _is_windows_all_in_one_configuration(_config)
        and normalized_name == get_local_windows_device_name()
        and current_device_id != normalized_name
    ):
        raise ValueError(f"设备名称已被本机 Windows 一体化客户端占用：{normalized_name}")
    for record in records:
        if record.get("device_id") != current_device_id and record.get("name") == normalized_name:
            raise ValueError(f"设备名称已存在：{normalized_name}")


def delete_device(device_id: str) -> None:
    """删除指定设备，使其凭据立刻失效。"""
    with _lock:
        candidate = copy.deepcopy(_config)
        records = candidate["devices"]["records"]
        updated_records = [record for record in records if record["device_id"] != device_id]
        if len(updated_records) == len(records):
            raise KeyError(f"设备不存在：{device_id}")
        candidate["devices"]["records"] = updated_records
        validated = _validate_config(candidate)
        _config.clear()
        _config.update(validated)
        _save_unlocked()


def record_device_seen(device_id: str, seen_at: float) -> None:
    """按一分钟节流记录设备最后在线时间，避免每次同步都写入配置。"""
    record = next((item for item in list_devices() if item.device_id == device_id), None)
    if record is None:
        raise KeyError(f"设备不存在：{device_id}")
    if record.last_seen_at is not None and seen_at - record.last_seen_at < 60:
        return
    _update_device(device_id, {"last_seen_at": seen_at})


def complete_initialization(auth_token: str) -> None:
    """原子写入管理员 Token 并结束首次初始化。"""
    with _lock:
        if is_initialized():
            raise RuntimeError("服务已经完成初始化")
        candidate = copy.deepcopy(_config)
        candidate["server"]["auth_token"] = auth_token
        candidate["system"]["initialized"] = True
        candidate = _clear_windows_all_in_one_client_credentials(candidate)
        validated = _validate_config(candidate)
        _config.clear()
        _config.update(validated)
        _save_unlocked()


def update_section(section: str, data: dict) -> None:
    """线程安全地批量更新某个配置节下的键值对。"""
    with _lock:
        if section not in _config or not isinstance(_config[section], dict):
            return
        candidate = copy.deepcopy(_config)
        changed = any(candidate[section].get(k) != v for k, v in data.items())
        if changed:
            candidate[section].update(data)
            candidate = _clear_windows_all_in_one_client_credentials(candidate)
            validated = _validate_config(candidate)
            _config.clear()
            _config.update(validated)
            _save_unlocked()


def import_windows_client_configuration(configuration: WindowsClientConfiguration) -> None:
    """原子导入 Windows 客户端地址、设备 ID 和 Token。"""
    if get_runtime_mode() == "windows_all_in_one":
        raise ValueError("Windows 一体化模式直接使用本机服务端和管理员 Token，无需导入客户端配置")
    with _lock:
        candidate = copy.deepcopy(_config)
        candidate["client"].update(configuration.model_dump(mode="json"))
        validated = _validate_config(candidate)
        _config.clear()
        _config.update(validated)
        _save_unlocked()


def effective_server_url() -> str:
    """根据 server.enabled 模式返回实际生效的服务端 URL。"""
    if get("server.enabled"):
        return f"http://127.0.0.1:{get('server.port', 8000)}"
    return get("client.server_url", "")


def effective_auth_token() -> str:
    """根据 server.enabled 模式返回实际生效的鉴权 token。"""
    if get("server.enabled"):
        return get("server.auth_token", "")
    return get("client.auth_token", "")


def get_local_windows_device_name() -> str:
    """返回 Windows 一体化客户端自动使用的本机设备名称。"""
    name = os.environ.get("COMPUTERNAME", socket.gethostname())
    try:
        normalized_name = validate_device_name(name)
    except ValueError as exc:
        raise RuntimeError(f"本机 Windows 设备名称无效：{name}") from exc
    if len(normalized_name) > 64:
        raise RuntimeError(f"本机 Windows 设备名称超过 64 个字符：{normalized_name}")
    return normalized_name


def effective_device_id() -> str:
    """返回当前客户端同步时使用的设备名称。"""
    if get_runtime_mode() == "windows_all_in_one":
        return get_local_windows_device_name()
    return get("client.device_id", "")


def effective_sse_url() -> str:
    """根据当前模式拼接 SSE 订阅地址。"""
    base = effective_server_url().rstrip("/")
    return f"{base}/api/clipboard/stream"


def set_auto_start(enable: bool) -> bool:
    """启用或禁用 Windows 开机自启（写入注册表 HKCU Run）。非 Windows 平台返回 False。"""
    if sys.platform != "win32":
        return False
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "ClipboardDispatcher"

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )
        if enable:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                main_py = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "main.py"
                )
                cmd = f'"{sys.executable}" "{main_py}"'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
        else:
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, app_name)
        winreg.CloseKey(key)
        set("system.auto_start", enable)
        return True
    except OSError as exc:
        logger.error("Failed to set auto-start: %s", exc)
        return False
