"""在无需交互式桌面的 Windows CI 中验证单文件程序、同步接口和打包资源。"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, ConfigDict, Field
from smoke_windows_build import remove_smoke_directory, reserve_local_port, terminate_process_tree, wait_for_health


class ProvisionedDevice(BaseModel):
    """仅读取构建验证所需的设备凭据，并校验接口响应。"""

    model_config = ConfigDict(strict=True)

    device_id: str = Field(min_length=1)
    token: str = Field(min_length=16)


def require_status(response: httpx.Response, expected: int) -> None:
    """接口状态不符时报告具体请求和响应。"""
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} 返回 HTTP {response.status_code}，"
            f"期望 {expected}，响应：{response.text}"
        )


def verify_packaged_api(base_url: str) -> None:
    """通过真实 HTTP 完成初始化、登录、设备接入和文本同步，并验证静态与 Tasker 资源。"""
    token = secrets.token_urlsafe(32)
    with httpx.Client(base_url=base_url, trust_env=False, timeout=10) as client:
        require_status(client.get("/setup"), 200)
        for path in ("/static/css/style.css", "/static/js/app.js", "/static/favicon.svg"):
            response = client.get(path)
            require_status(response, 200)
            if not response.content:
                raise RuntimeError(f"打包资源内容为空：{path}")
        require_status(client.post("/setup", data={"auth_token": token, "confirm_auth_token": token}), 303)
        require_status(client.post("/login", data={"token": token}), 303)
        require_status(client.get("/"), 200)
        response = client.post(
            "/api/devices",
            json={"name": "Windows CI", "platform": "android"},
            headers={"Authorization": f"Bearer {token}"},
        )
        require_status(response, 201)
        device = ProvisionedDevice.model_validate_json(response.content)
        headers = {"Authorization": f"Bearer {device.token}"}
        content = "Windows EXE 构建验证"
        require_status(
            client.post("/api/clipboard/upload", json={"kind": "text", "content": content}, headers=headers), 200
        )
        downloaded = client.get("/api/clipboard/download", headers=headers)
        require_status(downloaded, 200)
        if downloaded.text != content or downloaded.headers.get("X-Clipboard-Kind") != "text":
            raise RuntimeError("打包程序上传和下载的文本内容不一致")
        tasker = client.get(f"/setup/tasker/configured/{device.device_id}/project")
        require_status(tasker, 200)
        ElementTree.fromstring(tasker.content)
        health = client.get("/health")
        require_status(health, 200)
        if health.json().get("initialized") is not True:
            raise RuntimeError("打包程序初始化未生效")


def main() -> int:
    """使用独立目录运行当前 EXE，并在验证后清理自身进程和配置。"""
    if sys.platform != "win32":
        raise RuntimeError("Windows CI 产物验证必须在 Windows 执行")
    executable = Path(__file__).resolve().parents[1] / "dist" / "ClipboardDispatcher.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"未找到 Windows 产物：{executable}")
    config_directory = Path(tempfile.mkdtemp(prefix="clipboard-desktop-smoke-"))
    smoke_executable = config_directory / executable.name
    port = reserve_local_port()
    process: subprocess.Popen[bytes] | None = None
    try:
        shutil.copy2(executable, smoke_executable)
        config = {"server": {"enabled": True, "host": "127.0.0.1", "port": port}, "system": {"initialized": False}}
        (config_directory / "config.json").write_text(json.dumps(config), encoding="utf-8-sig")
        environment = {**os.environ, "SERVER_ONLY": "1"}
        environment.pop("DOCKER_MODE", None)
        process = subprocess.Popen(
            [str(smoke_executable)],
            cwd=config_directory,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{port}"
        health = json.loads(wait_for_health(f"{base_url}/health", 30.0))
        if health.get("initialized") is not False:
            raise RuntimeError("打包程序首次启动未进入初始化状态")
        verify_packaged_api(base_url)
        if process.poll() is not None:
            raise RuntimeError(f"打包程序验证完成前退出：exit_code={process.returncode}")
        print("Windows EXE 启动、初始化、同步和打包资源验证通过")
    finally:
        if process is not None:
            terminate_process_tree(process, port, smoke_executable)
        log_path = config_directory / "app.log"
        if log_path.is_file():
            print(log_path.read_text(encoding="utf-8"))
        remove_smoke_directory(config_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
