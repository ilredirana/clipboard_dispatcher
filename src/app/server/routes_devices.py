"""设备凭据与设备生命周期管理 API。"""

import time

import config_manager
from config_schema import (
    ConfigModel,
    DeviceName,
    DevicePlatform,
    DeviceRecord,
    WindowsClientConfiguration,
    validate_device_name,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator
from server import routes_provisioning
from server.auth import verify_token

router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceCreateRequest(ConfigModel):
    """创建同步设备的请求。"""

    name: DeviceName
    platform: DevicePlatform

    _validate_name = field_validator("name")(validate_device_name)


class DeviceNameUpdate(ConfigModel):
    """设备名称更新请求。"""

    name: DeviceName

    _validate_name = field_validator("name")(validate_device_name)


class DeviceEnabledUpdate(ConfigModel):
    """设备启用状态更新请求。"""

    enabled: bool


class DeviceResponse(BaseModel):
    """不包含明文 Token 的设备公开记录。"""

    device_id: str
    name: str
    platform: DevicePlatform
    created_at: float
    last_seen_at: float | None
    enabled: bool


class WindowsProvisioningResponse(BaseModel):
    """已写入设备凭据的 Windows 客户端配置。"""

    server_url: str
    auth_token: str
    device_id: str
    configuration_json: str
    configuration_filename: str


class DeviceProvisionedResponse(DeviceResponse):
    """返回当前明文 Token 和对应平台的接入资产。"""

    token: str
    base_url: str
    windows: WindowsProvisioningResponse | None
    ios: routes_provisioning.IOSProvisioningResponse | None
    tasker: routes_provisioning.TaskerProvisioningResponse | None


def _serialize_device(record: DeviceRecord) -> DeviceResponse:
    """移除明文 Token 后返回设备列表记录。"""
    return DeviceResponse(
        device_id=record.device_id,
        name=record.name,
        platform=record.platform,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
        enabled=record.enabled,
    )


def _build_provisioned_response(
    record: DeviceRecord,
    request: Request,
) -> DeviceProvisionedResponse:
    """根据设备平台和当前 Token 生成接入资产。"""
    base_url = routes_provisioning.get_external_url(request)
    windows_configuration = WindowsClientConfiguration(
        server_url=base_url,
        auth_token=record.token,
        device_id=record.device_id,
    )
    windows = (
        WindowsProvisioningResponse(
            server_url=base_url,
            auth_token=record.token,
            device_id=record.device_id,
            configuration_json=windows_configuration.model_dump_json(),
            configuration_filename="clipboard-dispatcher-windows-config.json",
        )
        if record.platform == "windows"
        else None
    )
    ios = (
        routes_provisioning.create_ios_provisioning(base_url, record.device_id, record.token)
        if record.platform == "ios"
        else None
    )
    tasker = (
        routes_provisioning.create_tasker_provisioning(base_url, record.device_id)
        if record.platform == "android"
        else None
    )
    return DeviceProvisionedResponse(
        **_serialize_device(record).model_dump(),
        token=record.token,
        base_url=base_url,
        windows=windows,
        ios=ios,
        tasker=tasker,
    )


@router.get("", response_model=list[DeviceResponse])
async def list_all_devices(_token: str = Depends(verify_token)) -> list[DeviceResponse]:
    """列出所有设备，不返回任何明文同步 Token。"""
    return [_serialize_device(record) for record in config_manager.list_devices()]


@router.post("", response_model=DeviceProvisionedResponse, status_code=status.HTTP_201_CREATED)
async def create_new_device(
    request: Request,
    data: DeviceCreateRequest,
    _token: str = Depends(verify_token),
) -> DeviceProvisionedResponse:
    """创建设备并返回已写入该设备凭据的平台导入资产。"""
    try:
        record, _created_token = config_manager.create_device(data.name, data.platform, time.time())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _build_provisioned_response(record, request)


@router.get("/{device_id}/configuration", response_model=DeviceProvisionedResponse)
async def get_device_configuration(
    device_id: str,
    request: Request,
    _token: str = Depends(verify_token),
) -> DeviceProvisionedResponse:
    """读取设备当前 Token，并生成可查看、可下载的接入配置。"""
    try:
        record = config_manager.get_device(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _build_provisioned_response(record, request)


@router.post("/{device_id}/provision", response_model=DeviceProvisionedResponse)
async def provision_existing_device(
    device_id: str,
    request: Request,
    _token: str = Depends(verify_token),
) -> DeviceProvisionedResponse:
    """重新签发选定设备凭据并返回免配置导入资产。"""
    try:
        record, _rotated_token = config_manager.rotate_device_token(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _build_provisioned_response(record, request)


@router.put("/{device_id}/name", response_model=DeviceResponse)
async def rename_device(
    device_id: str,
    data: DeviceNameUpdate,
    _token: str = Depends(verify_token),
) -> DeviceResponse:
    """重命名设备。"""
    try:
        return _serialize_device(config_manager.update_device_name(device_id, data.name))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{device_id}/enabled", response_model=DeviceResponse)
async def set_device_enabled(
    device_id: str,
    data: DeviceEnabledUpdate,
    _token: str = Depends(verify_token),
) -> DeviceResponse:
    """启用或禁用设备，同步凭据立即生效或失效。"""
    try:
        record = config_manager.update_device_enabled(device_id, data.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_device(record)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(device_id: str, _token: str = Depends(verify_token)) -> Response:
    """删除设备，同步凭据立即失效。"""
    try:
        config_manager.delete_device(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
