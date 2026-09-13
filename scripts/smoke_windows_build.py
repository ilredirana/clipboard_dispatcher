"""验证 PyInstaller Windows 产物可以启动服务端并响应健康检查。"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import suppress
from http.client import HTTPException
from pathlib import Path
from urllib.request import urlopen


def wait_for_health(url: str, timeout_seconds: float) -> str:
    """等待健康检查成功，超时则抛出明确错误。"""
    deadline = time.monotonic() + timeout_seconds
    last_error = "服务尚未启动"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
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
    last_error: OSError | None = None
    for _attempt in range(10):
        try:
            shutil.rmtree(path)
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
    environment["SERVER_ONLY"] = "1"
    # 配置写入 exe 所在目录（与 get_config_dir() 的 exe 分支一致）
    config_dir = executable.parent
    port = reserve_local_port()
    prepare_smoke_config(config_dir, port, project_root)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result = wait_for_health(f"http://127.0.0.1:{port}/health", 15.0)
        health = json.loads(result)
        if health.get("initialized") is not False:
            raise RuntimeError("Windows 产物首次启动未进入初始化状态")
        print(result)
    finally:
        if process is not None:
            terminate_process_tree(process, port, executable)
        # 清理 smoke test 生成的配置和日志文件
        for name in ("config.json", "app.log"):
            target = config_dir / name
            if target.is_file():
                with suppress(OSError):
                    target.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
