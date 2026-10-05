"""使用真实文件、监听端口和 HTTP 请求验证桌面启动。"""

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import config_manager
import httpx
import pytest
from client.api_client import create_http_client
from main import _start_server, _wait_for_server
from startup import desktop_listeners, instance_lock


def test_bom_configuration_preserves_credentials(tmp_path: Path) -> None:
    """兼容 Windows 编辑器写入的 BOM，并保留原有凭据。"""
    config_manager.init_at(str(tmp_path))
    path = tmp_path / "config.json"
    original = path.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8-sig")
    config_manager.init_at(str(tmp_path))
    assert config_manager.get("server.auth_token") == json.loads(original)["server"]["auth_token"]


@pytest.mark.parametrize("content", ['{', 'null', '{"server":{"auth_token":"short"}}'])
def test_invalid_configuration_is_reported_without_overwrite(tmp_path: Path, content: str) -> None:
    """错误必须包含路径，损坏或不合法的配置不得被默认配置覆盖。"""
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises((ValueError, TypeError), match="config.json"):
        config_manager.init_at(str(tmp_path))
    assert path.read_text(encoding="utf-8") == content


def test_occupied_port_is_reported_before_starting_server() -> None:
    """真实占用端口时拒绝第二实例，避免连接到其他进程。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        if sys.platform == "win32":
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = int(occupied.getsockname()[1])
        with pytest.raises(OSError, match=str(port)), desktop_listeners("0.0.0.0", port):
            pytest.fail("端口冲突时不应进入启动流程")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 命名互斥对象")
def test_duplicate_instance_releases_lock_after_exit(tmp_path: Path) -> None:
    """重复启动明确失败，原实例退出后允许重新启动。"""
    with instance_lock(str(tmp_path)), pytest.raises(RuntimeError, match="程序已运行"), instance_lock(str(tmp_path)):
        pytest.fail("同一配置目录不能同时持有两个实例锁")
    with instance_lock(str(tmp_path)):
        assert tmp_path.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 文件只读属性")
def test_unwritable_log_exits_with_actionable_error(tmp_path: Path) -> None:
    """真实运行入口，验证日志尚未建立时的权限错误仍能报告并退出。"""
    application = tmp_path / "app"
    shutil.copytree(Path(__file__).resolve().parents[1] / "app", application)
    log_path = tmp_path / "app.log"
    log_path.touch()
    log_path.chmod(stat.S_IREAD)
    environment = {**os.environ, "SERVER_ONLY": "1", "PYTHONIOENCODING": "utf-8"}
    environment.pop("DOCKER_MODE", None)
    try:
        result = subprocess.run(
            [sys.executable, str(application / "main.py")], env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
        )
        assert result.returncode == 1
        assert str(tmp_path) in result.stderr
        assert "PermissionError" in result.stderr
        assert "app.log" in result.stderr
        assert "有写权限的目录" in result.stderr
    finally:
        log_path.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 原生托盘窗口")
def test_tray_window_failure_exits_without_pending_setup_thread() -> None:
    """真实 Win32 窗口创建失败后，子进程必须释放安装线程并正常结束。"""
    project_root = Path(__file__).resolve().parents[2]
    code = """
import json
import sys
sys.path.insert(0, 'src/app')
from PIL import Image
from tray import WindowsTrayIcon, run_tray
from pystray._util import win32
icon = WindowsTrayIcon('TrayFailureTest', Image.new('RGBA', (16, 16)), menu=None)
win32.UnregisterClass(icon._atom, win32.GetModuleHandle(None))
try:
    run_tray(icon)
except OSError:
    print(json.dumps({'setup_alive': icon._setup_thread.is_alive()}))
else:
    raise RuntimeError('Expected native window creation failure')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=project_root,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"setup_alive": False}


@pytest.mark.parametrize("host", ["0.0.0.0", "::1"])
def test_local_service_ignores_broken_proxy_and_certificate_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str,
) -> None:
    """真实启动双入口服务，验证回环请求不加载 SOCKS 和无效证书环境。"""
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    config_manager.init_at(str(tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as available:
        available.bind(("127.0.0.1", 0))
        port = int(available.getsockname()[1])
    with desktop_listeners(host, port) as listeners:
        thread, server = _start_server(port, host, listeners)
        try:
            _wait_for_server(server, thread, 5.0)
            url = f"http://127.0.0.1:{port}/health"
            with create_http_client(url, httpx.Timeout(3)) as client:
                response = client.get(url)
                assert response.status_code == 200
                assert response.json()["status"] == "healthy"
            if host == "::1":
                url = f"http://[::1]:{port}/health"
                with create_http_client(url, httpx.Timeout(3)) as client:
                    assert client.get(url).status_code == 200
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        assert not thread.is_alive()
