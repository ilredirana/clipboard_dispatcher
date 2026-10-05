"""验证 Windows 产物在代理环境下能启动本地服务并加载真实托盘。"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import httpx


def wait_for_health(url: str, timeout_seconds: float) -> str:
    """等待健康检查成功，超时则抛出明确错误。"""
    deadline = time.monotonic() + timeout_seconds
    last_error = "服务尚未启动"
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:
                if response.status != 200:
                    raise RuntimeError(f"健康检查返回 HTTP {response.status}")
                return response.read().decode("utf-8")
        except (HTTPException, OSError, RuntimeError) as exc:
            last_error = str(exc)
            time.sleep(0.3)
    raise RuntimeError(f"Windows 产物未在 {timeout_seconds:.1f} 秒内通过健康检查：{last_error}")


def reserve_local_port() -> int:
    """获取一个当前未被占用的本地 TCP 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def prepare_smoke_config(config_directory: Path, port: int, project_root: Path) -> None:
    """创建单文件程序启动前需要的独立配置，避免占用用户端口与配置目录。"""
    app_directory = project_root / "src" / "app"
    sys.path.insert(0, str(app_directory))
    import config_manager

    config_manager.init_at(str(config_directory))
    config_manager.set("server.port", port)
    config_manager.set("server.host", "::1")
    config_manager.set("client.enable_auto_upload", False)
    config_path = config_directory / "config.json"
    config_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8-sig")


def find_listening_process_id(port: int) -> int | None:
    """返回监听指定 TCP 端口的进程 ID。"""
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    port_suffix = f":{port}"
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) != 5 or columns[0] != "TCP" or columns[3] != "LISTENING":
            continue
        if columns[1].endswith(port_suffix):
            return int(columns[4])
    return None


def get_process_path(process_id: int) -> Path:
    """读取 Windows 进程的可执行文件路径。"""
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-Process -Id {process_id}).Path",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    path = result.stdout.strip()
    if not path:
        raise RuntimeError(f"无法读取监听进程的可执行文件路径：PID {process_id}")
    return Path(path)


def terminate_process_tree(process: subprocess.Popen[bytes], port: int, executable: Path) -> None:
    """终止单文件引导进程及其监听指定端口的派生应用进程。"""
    target_process_id = find_listening_process_id(port)
    if target_process_id is not None:
        target_path = get_process_path(target_process_id)
        if target_path.resolve() != executable.resolve():
            raise RuntimeError(
                f"监听 smoke test 端口的进程不属于当前构建：PID {target_process_id}，路径 {target_path}"
            )
    else:
        target_process_id = process.pid
    subprocess.run(
        ["taskkill", "/PID", str(target_process_id), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if process.poll() is None:
        process.wait(timeout=10)


def remove_smoke_directory(path: Path) -> None:
    """等待应用进程释放日志文件后删除临时配置目录。"""
    resolved_path = path.resolve()
    if resolved_path.parent != Path(tempfile.gettempdir()).resolve() or not resolved_path.name.startswith(
        "clipboard-desktop-smoke-"
    ):
        raise ValueError(f"拒绝删除冒烟测试临时目录之外的路径：{resolved_path}")
    last_error: OSError | None = None
    for _attempt in range(10):
        try:
            shutil.rmtree(resolved_path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.3)
    if last_error is None:
        raise RuntimeError(f"临时目录删除失败：{path}")
    raise RuntimeError(f"临时目录未能删除：{path}，原因：{last_error}") from last_error


def main() -> int:
    """启动并验证 Windows 可执行文件。"""
    if sys.platform != "win32":
        raise RuntimeError("Windows 产物 smoke test 必须在 Windows 执行")

    project_root = Path(__file__).resolve().parents[1]
    executable = project_root / "dist" / "ClipboardDispatcher.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"未找到 Windows 产物：{executable}")

    environment = os.environ.copy()
    environment.pop("DOCKER_MODE", None)
    environment.pop("SERVER_ONLY", None)
    # 使用未监听的代理端口，确保本机启动不依赖系统代理可用性。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as proxy_socket:
        proxy_socket.bind(("127.0.0.1", 0))
        proxy_port = int(proxy_socket.getsockname()[1])
    environment.update(
        HTTP_PROXY=f"http://127.0.0.1:{proxy_port}",
        HTTPS_PROXY=f"http://127.0.0.1:{proxy_port}",
        ALL_PROXY=f"socks5://127.0.0.1:{proxy_port}",
        NO_PROXY="",
    )
    # 使用独立目录，避免覆盖或删除用户在 dist 中的配置和日志。
    config_dir = Path(tempfile.mkdtemp(prefix="clipboard-desktop-smoke-"))
    environment["SSL_CERT_FILE"] = str(config_dir / "missing-certificate.pem")
    smoke_executable = config_dir / executable.name
    shutil.copy2(executable, smoke_executable)
    port = reserve_local_port()
    prepare_smoke_config(config_dir, port, project_root)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [str(smoke_executable)],
            cwd=config_dir,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result = wait_for_health(f"http://127.0.0.1:{port}/health", 15.0)
        health = json.loads(result)
        if health.get("initialized") is not False:
            raise RuntimeError("Windows 产物首次启动未进入初始化状态")
        wait_for_tray(process, config_dir / "app.log", 15.0)
        token = secrets.token_urlsafe(32)
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/setup",
                data={"auth_token": token, "confirm_auth_token": token},
            )
            if response.status_code != 303:
                raise RuntimeError(f"桌面初始化失败：HTTP {response.status_code}，{response.text}")
        # 超过旧版健康检查超时时间后再次检查，避免把短暂存活误判为成功。
        time.sleep(12)
        if process.poll() is not None:
            raise RuntimeError(f"托盘加载后进程退出：exit_code={process.returncode}")
        wait_for_health(f"http://127.0.0.1:{port}/health", 3.0)
        wait_for_health(f"http://[::1]:{port}/health", 3.0)
        log_content = (config_dir / "app.log").read_text(encoding="utf-8")
        if "SSE connected" not in log_content or "Clipboard API request failed" in log_content:
            raise RuntimeError(f"代理环境下本机同步连接失败：\n{log_content}")
        print(result)
        print("桌面托盘加载成功，代理环境下持续运行验证通过")
    finally:
        if process is not None:
            terminate_process_tree(process, port, smoke_executable)
        log_path = config_dir / "app.log"
        if log_path.is_file():
            print(log_path.read_text(encoding="utf-8"))
        remove_smoke_directory(config_dir)
    return 0


def wait_for_tray(process: subprocess.Popen[bytes], log_path: Path, timeout_seconds: float) -> None:
    """等待真实图标加载完成，启动线程异常或进程退出则明确失败。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"托盘就绪前进程退出：exit_code={process.returncode}")
        if log_path.is_file():
            content = log_path.read_text(encoding="utf-8")
            if "Unhandled" in content or "Traceback" in content:
                raise RuntimeError(f"桌面启动出现异常：\n{content}")
            if "System tray ready" in content:
                return
        time.sleep(0.3)
    raise TimeoutError(f"托盘未在 {timeout_seconds} 秒内加载，日志路径：{log_path}")


if __name__ == "__main__":
    raise SystemExit(main())
