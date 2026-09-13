"""移动设备导入资产与稳定下载路由。"""

import io
import json
import logging
from pathlib import Path
from urllib.parse import quote

import config_manager
import qrcode
import qrcode.image.svg
from config_schema import DeviceRecord
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from qrcode.constants import ERROR_CORRECT_M

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/setup", tags=["ios-setup"])

_PUSH_SHORTCUT_URL = "https://www.icloud.com/shortcuts/2e994556956c45e5a3e8863f9be78e1e"
_PULL_SHORTCUT_URL = "https://www.icloud.com/shortcuts/f4b97f84872a4d91b20a29c5f6088b3b"
_CONFIGURATION_SHORTCUT_URL = "https://www.icloud.com/shortcuts/23557a2803e54944a3b681035f65888e"
_SCREENSHOT_UPLOAD_SHORTCUT_URL = "https://www.icloud.com/shortcuts/01516358ef4b432a965653b8ddcbcf45"

_TASKER_FILES = {
    "upload": "Upload Clipboard.tsk.xml",
    "download": "Download Clipboard.tsk.xml",
}
_TASKER_PROFILE_FILE = "Clipboard Dispatcher.prj.xml"
_TASKER_ARTIFACTS = {"project", *_TASKER_FILES}


class IOSProvisioningResponse(BaseModel):
    """iOS 快捷指令下载链接和当前设备配置二维码。"""

    configuration_shortcut_url: str
    configuration_shortcut_qr_svg: str
    push_shortcut_url: str
    push_shortcut_qr_svg: str
    pull_shortcut_url: str
    pull_shortcut_qr_svg: str
    screenshot_upload_shortcut_url: str
    screenshot_upload_shortcut_qr_svg: str
    configuration_json: str
    configuration_qr_svg: str
    configuration_filename: str


class TaskerProvisioningResponse(BaseModel):
    """已写入设备变量的 Tasker 项目和单任务下载链接。"""

    project_url: str
    project_qr_url: str
    upload_url: str
    download_url: str
    upload_qr_url: str
    download_qr_url: str


def get_external_url(request: Request) -> str:
    """返回配置的公开地址，未配置时使用当前请求地址。"""
    configured_url = config_manager.get("server.public_base_url", "")
    if configured_url:
        return configured_url
    return str(request.base_url).rstrip("/")


def _generate_qr_svg(data_str: str) -> str:
    """生成 QR 码的 SVG 字符串。空输入返回空字符串。"""
    if not data_str:
        return ""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M)
    qr.add_data(data_str)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage)
    svg_buffer = io.BytesIO()
    img.save(svg_buffer)
    return svg_buffer.getvalue().decode("utf-8")


def _get_tasker_task_file(task_name: str) -> tuple[Path, str]:
    """返回受支持的 Tasker 任务文件，资源缺失时明确报错。"""
    filename = _TASKER_FILES.get(task_name)
    if not filename:
        raise HTTPException(status_code=404, detail="不存在的 Tasker 任务")

    tasker_file = Path(config_manager.get_tasker_dir()) / filename
    if not tasker_file.is_file():
        logger.error("Tasker task file is missing", extra={"path": str(tasker_file)})
        raise HTTPException(status_code=500, detail=f"Tasker 导入文件缺失: {filename}")
    return tasker_file, filename


def _escape_xml_text(value: str) -> str:
    """转义插入 Tasker XML 文本节点的动态值。"""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_configured_tasker_task_xml(
    task_name: str,
    base_url: str,
    auth_token: str,
    device_id: str,
) -> tuple[str, str]:
    """将设备当前配置写入任务脚本，生成可直接运行的单任务 XML。"""
    tasker_file, filename = _get_tasker_task_file(task_name)
    xml_content = tasker_file.read_text(encoding="utf-8")
    server_marker = 'tasker.getVariable("clip_server_url")'
    token_marker = 'tasker.getVariable("clip_auth_token")'
    configured_server = _escape_xml_text(json.dumps(base_url.rstrip("/"), ensure_ascii=False))
    configured_token = _escape_xml_text(json.dumps(auth_token, ensure_ascii=False))
    marker_counts = {
        server_marker: xml_content.count(server_marker),
        token_marker: xml_content.count(token_marker),
    }
    invalid_markers = [marker for marker, count in marker_counts.items() if count != 1]
    if invalid_markers:
        marker_details = ", ".join(
            f"{marker}={marker_counts[marker]}" for marker in invalid_markers
        )
        raise RuntimeError(f"Tasker 任务配置注入标记数量异常: {filename}, {marker_details}")
    xml_content = (
        xml_content.replace(server_marker, configured_server, 1)
        .replace(token_marker, configured_token, 1)
    )
    return xml_content, filename


def _get_tasker_project_file() -> tuple[Path, str]:
    """读取 Tasker 项目 Profile，资源或 Profile 节点缺失时明确报错。"""
    profile_file = Path(config_manager.get_tasker_dir()) / _TASKER_PROFILE_FILE
    return profile_file, "project"


def _build_configured_tasker_project_xml(base_url: str, auth_token: str, device_id: str) -> str:
    """生成包含剪贴板与传感器 Profiles 且已写入设备配置的完整 Tasker 项目。"""
    proj_file, filename = _get_tasker_project_file()
    xml_content = proj_file.read_text(encoding="utf-8")
    server_marker = '__CLIP_SERVER_URL__'
    token_marker = '__CLIP_AUTH_TOKEN__'
    configured_server = _escape_xml_text(base_url.rstrip("/"))
    configured_token = _escape_xml_text(auth_token)
    marker_counts = {
        server_marker: xml_content.count(server_marker),
        token_marker: xml_content.count(token_marker),
    }
    invalid_markers = [marker for marker, count in marker_counts.items() if count != 1]
    if invalid_markers:
        marker_details = ", ".join(
            f"{marker}={marker_counts[marker]}" for marker in invalid_markers
        )
        raise RuntimeError(f"Tasker 项目配置注入标记数量异常: {filename}, {marker_details}")
    xml_content = (
        xml_content.replace(server_marker, configured_server, 1)
        .replace(token_marker, configured_token, 1)
    )
    return xml_content


def _get_android_device(device_id: str, artifact_name: str) -> DeviceRecord:
    """读取稳定 Tasker 资产对应的 Android 设备。"""
    if artifact_name not in _TASKER_ARTIFACTS:
        raise HTTPException(status_code=404, detail="不存在的 Tasker 导入资产")
    try:
        record = config_manager.get_device(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Tasker 设备不存在") from exc
    if record.platform != "android":
        raise HTTPException(status_code=404, detail="设备不是 Android 平台")
    return record


def create_ios_provisioning(base_url: str, device_id: str, auth_token: str) -> IOSProvisioningResponse:
    """生成 iOS 四个快捷指令下载资产与设备配置二维码。"""
    configuration = json.dumps(
        {
            "api_base_url": base_url.rstrip("/"),
            "auth_token": auth_token,
            "device_id": device_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return IOSProvisioningResponse(
        configuration_shortcut_url=_CONFIGURATION_SHORTCUT_URL,
        configuration_shortcut_qr_svg=_generate_qr_svg(_CONFIGURATION_SHORTCUT_URL),
        push_shortcut_url=_PUSH_SHORTCUT_URL,
        push_shortcut_qr_svg=_generate_qr_svg(_PUSH_SHORTCUT_URL),
        pull_shortcut_url=_PULL_SHORTCUT_URL,
        pull_shortcut_qr_svg=_generate_qr_svg(_PULL_SHORTCUT_URL),
        screenshot_upload_shortcut_url=_SCREENSHOT_UPLOAD_SHORTCUT_URL,
        screenshot_upload_shortcut_qr_svg=_generate_qr_svg(_SCREENSHOT_UPLOAD_SHORTCUT_URL),
        configuration_json=configuration,
        configuration_qr_svg=_generate_qr_svg(configuration),
        configuration_filename="clipboard-dispatcher-ios-config.json",
    )


def create_tasker_provisioning(base_url: str, device_id: str) -> TaskerProvisioningResponse:
    """生成绑定设备 ID 且长期有效的 Tasker 项目和单任务链接。"""
    encoded_device_id = quote(device_id, safe="")
    prefix = f"{base_url.rstrip('/')}/setup/tasker/configured/{encoded_device_id}"
    qr_prefix = f"{base_url.rstrip('/')}/setup/tasker/configured/qr/{encoded_device_id}"
    return TaskerProvisioningResponse(
        project_url=f"{prefix}/project",
        project_qr_url=f"{qr_prefix}/project",
        upload_url=f"{prefix}/upload",
        download_url=f"{prefix}/download",
        upload_qr_url=f"{qr_prefix}/upload",
        download_qr_url=f"{qr_prefix}/download",
    )


@router.get("/tasker/configured/qr/{device_id}/{artifact_name}")
async def get_configured_tasker_qr(
    device_id: str,
    artifact_name: str,
    request: Request,
) -> Response:
    """生成指向设备当前 Tasker 导入资产的稳定二维码。"""
    _get_android_device(device_id, artifact_name)
    base_url = get_external_url(request)
    encoded_device_id = quote(device_id, safe="")
    return Response(
        content=_generate_qr_svg(
            f"{base_url.rstrip('/')}/setup/tasker/configured/{encoded_device_id}/{artifact_name}"
        ),
        media_type="image/svg+xml",
    )


@router.get("/tasker/configured/{device_id}/{artifact_name}")
async def download_configured_tasker_artifact(
    device_id: str,
    artifact_name: str,
    request: Request,
) -> Response:
    """按设备当前 Token 重复下载已配置的 Tasker 项目或单任务 XML。"""
    record = _get_android_device(device_id, artifact_name)
    base_url = get_external_url(request)
    if artifact_name == "project":
        xml_content = _build_configured_tasker_project_xml(
            base_url,
            record.token,
            record.device_id,
        )
        filename = "Clipboard Dispatcher.prj.xml"
    else:
        xml_content, filename = _build_configured_tasker_task_xml(
            artifact_name,
            base_url,
            record.token,
            record.device_id,
        )
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/ios")
async def ios_setup_redirect() -> RedirectResponse:
    """将旧移动端入口迁移到统一设备接入区域。"""
    return RedirectResponse(url="/devices#device-management-section", status_code=303)
