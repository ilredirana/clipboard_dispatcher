"""使用真实网络和文件验证同步重试、并发连接和配置提交。"""

import hashlib
import itertools
import json
import socket
import stat
import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config_manager
import pytest
from client import api_client, clipboard_monitor
from config_schema import WindowsClientConfiguration


@contextmanager
def running_server(handler: type[BaseHTTPRequestHandler]) -> Iterator[ThreadingHTTPServer]:
    """为场景启动独立真实 HTTP 服务，并在结束时释放监听线程。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="SyncTestServer")
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def configure_client(directory: Path, url: str, token: str) -> None:
    """使用持久化配置接入测试服务。"""
    config_manager.init_at(str(directory))
    config_manager.set("server.enabled", False)
    config_manager.import_windows_client_configuration(WindowsClientConfiguration(
        server_url=url, auth_token=token, device_id="测试客户端",
    ))


def test_retry_keeps_original_server_and_token(tmp_path: Path) -> None:
    """第一次请求断线且配置切换后，重试仍仅向原服务器发送原凭据。"""
    received_a: list[str | None] = []
    received_b: list[str | None] = []

    class ServerB(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received_b.append(self.headers.get("Authorization"))
            self.send_response(204)
            self.end_headers()

    with running_server(ServerB) as server_b:
        url_b = f"http://127.0.0.1:{server_b.server_port}"

        class ServerA(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                received_a.append(self.headers.get("Authorization"))
                if len(received_a) == 1:
                    config_manager.import_windows_client_configuration(WindowsClientConfiguration(
                        server_url=url_b, auth_token="b" * 32, device_id="切换后的客户端",
                    ))
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(204)
                self.end_headers()

        with running_server(ServerA) as server_a:
            configure_client(tmp_path, f"http://127.0.0.1:{server_a.server_port}", "a" * 32)
            assert api_client.download() is None
            assert received_a == ["Bearer " + "a" * 32] * 2
            assert received_b == []
            assert config_manager.get_sync_connection() == (url_b, "b" * 32)


def test_parallel_requests_survive_server_change(tmp_path: Path) -> None:
    """旧请求等待响应时启动新服务器请求，旧连接不得被关闭。"""
    request_started = threading.Event()
    release_response = threading.Event()
    received_a: list[str | None] = []
    received_b: list[str | None] = []

    class ServerA(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received_a.append(self.headers.get("Authorization"))
            request_started.set()
            if not release_response.wait(8):
                raise TimeoutError("测试未释放旧服务响应")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"from-a")

    class ServerB(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received_b.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"from-b")

    with running_server(ServerA) as server_a, running_server(ServerB) as server_b:
        configure_client(tmp_path, f"http://127.0.0.1:{server_a.server_port}", "a" * 32)
        with ThreadPoolExecutor(max_workers=2) as executor:
            original = executor.submit(api_client.download)
            try:
                assert request_started.wait(5)
                config_manager.import_windows_client_configuration(WindowsClientConfiguration(
                    server_url=f"http://127.0.0.1:{server_b.server_port}",
                    auth_token="b" * 32, device_id="切换后的客户端",
                ))
                changed = executor.submit(api_client.download)
                assert changed.result(timeout=5) == api_client.RemoteClipboardItem("text", "from-b")
            finally:
                release_response.set()
            assert original.result(timeout=5) == api_client.RemoteClipboardItem("text", "from-a")
        assert received_a == ["Bearer " + "a" * 32]
        assert received_b == ["Bearer " + "b" * 32]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 文件只读属性")
@pytest.mark.parametrize("update", [
    lambda: config_manager.set("client.device_id", "新名称"),
    lambda: config_manager.update_section("client", {"device_id": "新名称"}),
    lambda: config_manager.import_windows_client_configuration(WindowsClientConfiguration(
        server_url="http://127.0.0.1:2", auth_token="b" * 32, device_id="新名称",
    )),
    lambda: config_manager.complete_initialization("b" * 32),
], ids=["single-setting", "section", "client-import", "initialization"])
def test_failed_config_write_preserves_memory_and_disk(tmp_path: Path, update: Callable[[], None]) -> None:
    """通过真实只读文件阻止替换，所有更新入口均保留原配置。"""
    configure_client(tmp_path, "http://127.0.0.1:1", "a" * 32)
    previous = config_manager.get_all()
    path = tmp_path / "config.json"
    previous_bytes = path.read_bytes()
    path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            update()
        assert config_manager.get_all() == previous
        assert path.read_bytes() == previous_bytes
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_debounce_uploads_final_clipboard_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """防抖内从 A 变为 B 后，通过真实 HTTP 只上传最终稳定内容 B。"""
    stop = threading.Event()
    received: list[str] = []

    class UploadServer(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body["content"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"kind":"text"}')
            stop.set()

    items = tuple(
        clipboard_monitor.LocalClipboardItem("text", value, hashlib.sha256(value.encode("utf-8")).hexdigest())
        for value in ("initial", "a", "b")
    )
    readings = itertools.chain(items, itertools.repeat(items[-1]))
    # 仅控制原生剪贴板读取边界，配置、监听循环和网络上传均真实执行。
    monkeypatch.setattr(clipboard_monitor, "_read_item_for_loop", lambda: next(readings))
    with running_server(UploadServer) as server:
        configure_client(tmp_path, f"http://127.0.0.1:{server.server_port}", "a" * 32)
        config_manager.set("client.debounce_delay_ms", 50)
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(clipboard_monitor.clipboard_monitor_loop, stop)
            try:
                assert stop.wait(5)
            finally:
                stop.set()
            worker.result(timeout=5)
        assert received == ["b"]
