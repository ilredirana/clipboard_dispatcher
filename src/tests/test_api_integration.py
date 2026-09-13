"""FastAPI 与配置持久化集成测试。"""

import json
import re
import time
from io import BytesIO
from urllib.parse import quote

import config_manager
import pytest
import server.app as app_module
from client import api_client as clipboard_api_client
from client import sse_listener
from fastapi.testclient import TestClient
from PIL import Image
from server import routes_provisioning
from server.app import create_app
from server.auth import create_local_login_code


@pytest.fixture
def api_client(tmp_path):
    config_manager.init_at(str(tmp_path))
    config_manager.complete_initialization("a" * 32)
    _device, device_token = config_manager.create_device("测试设备", "windows", time.time())
    with app_module._login_failures_lock:
        app_module._login_failures.clear()
    app = create_app()
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        admin_token = config_manager.get("server.auth_token", "")
        yield (
            client,
            {"Authorization": f"Bearer {admin_token}"},
            admin_token,
            {"Authorization": f"Bearer {device_token}"},
        )


def test_default_config_generates_random_token(tmp_path):
    config_manager.init_at(str(tmp_path))

    token = config_manager.get("server.auth_token", "")
    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))

    assert len(token) >= 32
    assert persisted["server"]["auth_token"] == token
    assert persisted["system"]["initialized"] is False


def test_docker_generates_admin_token_without_environment_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_MODE", "1")
    monkeypatch.delenv("CLIPBOARD_DISPATCHER_ADMIN_TOKEN", raising=False)

    config_manager.init_at(str(tmp_path))

    assert config_manager.is_initialized() is True
    assert len(config_manager.get("server.auth_token", "")) >= 32


def test_docker_admin_token_environment_variable_initializes_server(monkeypatch, tmp_path):
    admin_token = "d" * 32
    monkeypatch.setenv("DOCKER_MODE", "1")
    monkeypatch.setenv("CLIPBOARD_DISPATCHER_ADMIN_TOKEN", admin_token)

    config_manager.init_at(str(tmp_path))

    assert config_manager.is_initialized() is True
    assert config_manager.get("server.auth_token") == admin_token


def test_docker_settings_hide_desktop_only_controls(monkeypatch, tmp_path):
    admin_token = "d" * 32
    monkeypatch.setenv("DOCKER_MODE", "1")
    monkeypatch.setenv("CLIPBOARD_DISPATCHER_ADMIN_TOKEN", admin_token)
    config_manager.init_at(str(tmp_path))

    with TestClient(create_app()) as client:
        client.cookies.set("auth_token", admin_token)
        response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="cfg-auto-upload"' not in response.text
    assert 'id="cfg-auto-start"' not in response.text
    assert 'id="cfg-server-enabled"' not in response.text
    assert 'id="cfg-host"' not in response.text
    assert 'id="cfg-port"' not in response.text
    assert 'id="cfg-auth-token"' in response.text
    assert "首次启动自动生成管理员 Token" in response.text


def test_docker_runtime_enforces_internal_listener(monkeypatch, tmp_path):
    config_manager.init_at(str(tmp_path))
    config_manager.set("server.enabled", False)
    config_manager.set("server.host", "127.0.0.1")
    config_manager.set("server.port", 9000)
    monkeypatch.setenv("DOCKER_MODE", "1")
    monkeypatch.setenv("CLIPBOARD_DISPATCHER_ADMIN_TOKEN", "d" * 32)

    config_manager.init_at(str(tmp_path))

    assert config_manager.get("server.enabled") is True
    assert config_manager.get("server.host") == "0.0.0.0"
    assert config_manager.get("server.port") == 8000


def test_docker_rejects_internal_listener_update(monkeypatch, tmp_path):
    admin_token = "d" * 32
    monkeypatch.setenv("DOCKER_MODE", "1")
    monkeypatch.setenv("CLIPBOARD_DISPATCHER_ADMIN_TOKEN", admin_token)
    config_manager.init_at(str(tmp_path))

    with TestClient(create_app()) as client:
        response = client.put(
            "/api/config/server",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "enabled": True,
                "host": "0.0.0.0",
                "port": 9000,
                "public_base_url": "",
                "clipboard_ttl": 300,
                "max_payload_size": 5_242_880,
                "enable_image_sync": True,
                "max_image_size": 512_000,
            },
        )

    assert response.status_code == 400
    assert "0.0.0.0:8000" in response.json()["message"]


def test_windows_local_login_code_sets_auth_cookie(api_client):
    client, _headers, _token, _device_headers = api_client
    code = create_local_login_code()

    response = client.get(f"/local-login/{code}", follow_redirects=False)
    dashboard = client.get("/")
    reused = client.get(f"/local-login/{code}")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "auth_token=" in response.headers["set-cookie"]
    assert dashboard.status_code == 200
    assert reused.status_code == 403


def test_nested_config_migration_fills_missing_fields(tmp_path):
    partial = {
        "server": {"enabled": False},
        "remote_config": {"url": "https://example.com/removed-config.json"},
        "version": {"app_version": "0.9", "api_level": "0.9", "supported_api_levels": ["0.9"]},
        "ios": {"push_link": "https://example.com/push", "pull_link": "https://example.com/pull"},
    }
    (tmp_path / "config.json").write_text(json.dumps(partial), encoding="utf-8")

    config_manager.init_at(str(tmp_path))

    assert config_manager.get("server.enabled", True) is False
    assert config_manager.get("server.port", 0) == 8000
    assert len(config_manager.get("server.auth_token", "")) >= 32
    assert config_manager.get("client.debounce_delay_ms", 0) == 500
    assert config_manager.is_initialized() is True
    assert "remote_config" not in config_manager.get_all()
    assert "version" not in config_manager.get_all()
    assert "ios" not in config_manager.get_all()
    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert "remote_config" not in persisted
    assert "version" not in persisted
    assert "ios" not in persisted


def test_legacy_device_hashes_migrate_to_persisted_tokens(tmp_path):
    config_manager.init_at(str(tmp_path))
    device, old_token = config_manager.create_device("旧设备", "windows", time.time())
    legacy = config_manager.get_all()
    legacy["client"]["device_id"] = device.device_id
    legacy["client"]["auth_token"] = old_token
    legacy_record = next(item for item in legacy["devices"]["records"] if item["device_id"] == device.device_id)
    legacy_record.pop("token")
    legacy_record["token_hash"] = "a" * 64
    legacy["server"]["enabled"] = False
    (tmp_path / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

    config_manager.init_at(str(tmp_path))

    migrated = config_manager.get_device(device.device_id)
    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    persisted_record = next(
        item for item in persisted["devices"]["records"] if item["device_id"] == device.device_id
    )
    assert migrated.token != old_token
    assert config_manager.get("client.auth_token") == migrated.token
    assert config_manager.get_device_by_token(old_token) is None
    assert "token_hash" not in persisted_record
    assert persisted_record["token"] == migrated.token


def test_public_url_status_reports_missing_and_insecure_urls(api_client):
    client, admin_headers, _admin_token, _device_headers = api_client

    missing = client.get("/api/config/server/public-url-status", headers=admin_headers)
    config_manager.set("server.public_base_url", "http://192.168.1.10:9888")
    insecure = client.get("/api/config/server/public-url-status", headers=admin_headers)
    assert missing.status_code == 200
    assert missing.json() == {
        "state": "not_configured",
        "url": "",
        "detail": "尚未配置公开服务地址",
    }
    assert insecure.status_code == 200
    assert insecure.json()["state"] == "insecure_http"
    assert "未使用 HTTPS" in insecure.json()["detail"]


def test_first_run_requires_setup_before_sync(tmp_path):
    config_manager.init_at(str(tmp_path))
    app = create_app()
    token = config_manager.get("server.auth_token", "")

    with TestClient(app) as client:
        setup_page = client.get("/setup")
        dashboard = client.get("/", follow_redirects=False)
        rejected = client.post(
            "/api/clipboard/upload",
            headers={"Authorization": f"Bearer {token}"},
            json={"kind": "text", "content": "初始化前请求"},
        )

    assert setup_page.status_code == 200
    assert "完成首次设置" in setup_page.text
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/setup"
    assert rejected.status_code == 503
    assert "服务尚未初始化" in rejected.json()["message"]


def test_frontend_stylesheet_uses_content_version(tmp_path):
    config_manager.init_at(str(tmp_path))
    app = create_app()

    with TestClient(app) as client:
        setup_page = client.get("/setup")
        stylesheet_match = re.search(
            r'href="(/static/css/style\.css\?v=[a-f0-9]{12})"',
            setup_page.text,
        )
        assert stylesheet_match is not None
        stylesheet = client.get(stylesheet_match.group(1))
        favicon = client.get("/favicon.ico")

    assert setup_page.headers["cache-control"] == "no-store"
    assert 'rel="icon" type="image/svg+xml"' in setup_page.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert stylesheet.text.startswith(":root")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert favicon.content.startswith(b"<svg")


def test_ios_provisioning_page_uses_four_shortcut_entries_and_one_configuration_qr(api_client):
    client, _headers, admin_token, _device_headers = api_client
    client.cookies.set("auth_token", admin_token)

    page = client.get("/devices")
    script = client.get("/static/js/app.js")

    assert page.status_code == 200
    assert 'data-provisioning-role="ios-configuration-shortcut-qr"' in page.text
    assert 'data-provisioning-role="ios-push-shortcut-qr"' in page.text
    assert 'data-provisioning-role="ios-pull-shortcut-qr"' in page.text
    assert 'data-provisioning-role="ios-screenshot-upload-shortcut-qr"' in page.text
    assert 'data-provisioning-role="ios-config-qr"' in page.text
    assert "ios-config-download" not in page.text
    assert "ios-config-json" not in page.text
    assert "configuration_shortcut_url" in script.text
    assert "screenshot_upload_shortcut_url" in script.text
    assert "快捷指令链接待补充" in script.text


def test_setup_completes_initialization_and_allows_sync(tmp_path):
    config_manager.init_at(str(tmp_path))
    app = create_app()
    admin_token = "b" * 32

    with TestClient(app) as client:
        mismatch = client.post(
            "/setup",
            data={"auth_token": admin_token, "confirm_auth_token": "c" * 32},
        )
        initialized = client.post(
            "/setup",
            data={"auth_token": admin_token, "confirm_auth_token": admin_token},
            follow_redirects=False,
        )
        device = client.post(
            "/api/devices",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "初始化测试设备", "platform": "windows"},
        )
        device_token = device.json()["token"]
        upload = client.post(
            "/api/clipboard/upload",
            headers={"Authorization": f"Bearer {device_token}"},
            json={"kind": "text", "content": "初始化后请求"},
        )

    assert mismatch.status_code == 422
    assert "不一致" in mismatch.text
    assert initialized.status_code == 303
    assert initialized.headers["location"] == "/login"
    assert device.status_code == 201
    assert config_manager.is_initialized() is True
    assert upload.status_code == 200


def test_status_requires_authentication(api_client):
    client, headers, _token, _device_headers = api_client

    unauthorized = client.get("/api/config/status")
    authorized = client.get("/api/config/status", headers=headers)

    assert unauthorized.status_code == 401
    assert "token" in unauthorized.json()["message"]
    assert authorized.status_code == 200


def test_dashboard_redirects_to_login_without_cookie(api_client):
    client, _headers, _token, _device_headers = api_client

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_query_token_is_rejected(api_client):
    client, _headers, token, _device_headers = api_client

    response = client.get(f"/api/clipboard/download?token={token}")

    assert response.status_code == 401


def test_login_cookie_authenticates_web_api(api_client):
    client, _headers, token, _device_headers = api_client

    login = client.post("/login", data={"token": token}, follow_redirects=False)
    response = client.get("/api/config/all")

    assert login.status_code == 303
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert response.status_code == 200


def test_login_rate_limit_blocks_repeated_invalid_tokens(api_client):
    client, _headers, _token, _device_headers = api_client

    for _ in range(5):
        response = client.post("/login", data={"token": "invalid"})
        assert response.status_code == 200
    limited = client.post("/login", data={"token": "invalid"})

    assert limited.status_code == 429


def test_public_config_exposes_only_editable_fields(api_client):
    client, headers, _token, _device_headers = api_client

    response = client.get("/api/config/all", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"server", "client", "system"}
    assert "auth_token" not in data["server"]
    assert "auth_token" not in data["client"]
    assert "auth_token_masked" not in data["server"]
    assert "last_processed_version" not in data["client"]
    assert data["server"]["enable_image_sync"] is True
    assert data["server"]["max_image_size"] == 5_242_880
    assert data["server"]["public_base_url"] == ""
    assert data["system"] == {"auto_start": False}


def test_unconfigured_tasker_artifacts_are_not_exposed(api_client):
    client, headers, _token, _device_headers = api_client

    assert client.get("/setup/tasker/import", headers=headers).status_code == 404
    assert client.get("/setup/tasker/qr/upload", headers=headers).status_code == 404
    assert client.get("/setup/tasker/project/qr", headers=headers).status_code == 404
    assert client.get("/setup/tasker/download/download").status_code == 404
    assert client.get("/setup/tasker/project").status_code == 404


def test_status_reports_runtime_mode(api_client):
    client, headers, _token, _device_headers = api_client

    response = client.get("/api/config/status", headers=headers)

    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "windows_all_in_one"


def test_windows_device_creation_returns_connection_configuration(api_client):
    client, headers, admin_token, _device_headers = api_client
    config_manager.set("server.public_base_url", "https://clipboard.example.com")

    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "办公室电脑", "platform": "windows"},
    )
    payload = created.json()
    windows = payload["windows"]
    configuration = json.loads(windows["configuration_json"])

    assert created.status_code == 201
    assert payload["ios"] is None
    assert payload["tasker"] is None
    assert windows["server_url"] == "https://clipboard.example.com"
    assert windows["auth_token"] == payload["token"]
    assert windows["auth_token"] != admin_token
    assert windows["device_id"] == payload["device_id"]
    assert configuration == {
        "server_url": "https://clipboard.example.com",
        "auth_token": payload["token"],
        "device_id": payload["device_id"],
    }


def test_windows_all_in_one_uses_admin_token_without_client_connection_configuration(api_client):
    client, _headers, admin_token, _device_headers = api_client

    upload = client.post(
        "/api/clipboard/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"kind": "text", "content": "本机 Windows 同步"},
    )

    assert config_manager.get_runtime_mode() == "windows_all_in_one"
    assert config_manager.get("client.server_url") == ""
    assert config_manager.get("client.auth_token") == ""
    assert config_manager.get("client.device_id") == ""
    assert config_manager.effective_auth_token() == admin_token
    assert upload.status_code == 200
    assert upload.json() == {"kind": "text"}


def test_windows_all_in_one_rejects_remote_admin_token_sync(api_client):
    _client, _headers, admin_token, _device_headers = api_client

    with TestClient(create_app(), client=("192.168.1.20", 50000)) as remote_client:
        response = remote_client.post(
            "/api/clipboard/upload",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"kind": "text", "content": "远程管理员请求"},
        )

    assert response.status_code == 401


def test_device_name_is_unique_and_is_used_as_device_id(api_client):
    client, headers, _admin_token, _device_headers = api_client
    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "测试 iPhone", "platform": "ios"},
    )
    duplicate = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "测试 iPhone", "platform": "android"},
    )
    renamed = client.put(
        "/api/devices/%E6%B5%8B%E8%AF%95%20iPhone/name",
        headers=headers,
        json={"name": "新的 iPhone"},
    )
    old_configuration = client.get("/api/devices/%E6%B5%8B%E8%AF%95%20iPhone/configuration", headers=headers)
    new_configuration = client.get("/api/devices/%E6%96%B0%E7%9A%84%20iPhone/configuration", headers=headers)

    assert created.status_code == 201
    assert created.json()["device_id"] == "测试 iPhone"
    assert duplicate.status_code == 409
    assert renamed.status_code == 200
    assert renamed.json()["device_id"] == "新的 iPhone"
    assert old_configuration.status_code == 404
    assert new_configuration.status_code == 200


def test_windows_all_in_one_rejects_independent_client_configuration_import(api_client):
    client, headers, _admin_token, _device_headers = api_client

    response = client.put(
        "/api/config/client/import",
        headers=headers,
        json={
            "server_url": "https://clipboard.example.com",
            "auth_token": "w" * 32,
            "device_id": "远程 Windows",
        },
    )

    assert response.status_code == 409
    assert "无需导入客户端配置" in response.json()["message"]


def test_windows_configuration_import_is_atomic(api_client):
    client, headers, _admin_token, _device_headers = api_client
    config_manager.set("server.enabled", False)
    configuration = {
        "server_url": "https://clipboard.example.com/",
        "auth_token": "w" * 32,
        "device_id": "windows-device-1234",
    }

    response = client.put("/api/config/client/import", headers=headers, json=configuration)

    assert response.status_code == 200
    assert config_manager.get("client.server_url") == "https://clipboard.example.com"
    assert config_manager.get("client.auth_token") == configuration["auth_token"]
    assert config_manager.get("client.device_id") == configuration["device_id"]


def test_windows_configuration_import_rejects_invalid_data(api_client):
    client, headers, _admin_token, _device_headers = api_client
    original = config_manager.get_all()["client"]

    response = client.put(
        "/api/config/client/import",
        headers=headers,
        json={"server_url": "invalid", "auth_token": "short", "device_id": "short"},
    )

    assert response.status_code == 422
    assert config_manager.get_all()["client"] == original


def test_devices_have_dedicated_page(api_client):
    client, _headers, admin_token, _device_headers = api_client
    client.cookies.set("auth_token", admin_token)

    devices = client.get("/devices")
    settings = client.get("/settings")

    assert devices.status_code == 200
    assert 'id="devices-page"' in devices.text
    assert 'id="device-management-section"' in devices.text
    assert 'id="device-management-section"' not in settings.text


def test_device_current_configuration_reuses_persisted_token(api_client):
    client, headers, _admin_token, _device_headers = api_client
    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "可重复查看的电脑", "platform": "windows"},
    ).json()

    viewed = client.get(
        f"/api/devices/{created['device_id']}/configuration",
        headers=headers,
    )
    viewed_again = client.get(
        f"/api/devices/{created['device_id']}/configuration",
        headers=headers,
    )

    assert viewed.status_code == 200
    assert viewed_again.status_code == 200
    assert viewed.json()["token"] == created["token"]
    assert viewed_again.json()["token"] == created["token"]
    assert viewed.json()["windows"]["auth_token"] == created["token"]
    assert config_manager.get_device(created["device_id"]).token == created["token"]


def test_device_credentials_are_scoped_and_revocable(api_client):
    client, admin_headers, admin_token, device_headers = api_client

    created = client.post(
        "/api/devices",
        headers=admin_headers,
        json={"name": "Android 手机", "platform": "android"},
    )
    device = created.json()
    pending_project_url = device["tasker"]["project_url"]
    new_device_headers = {"Authorization": f"Bearer {device['token']}"}
    admin_sync = client.post(
        "/api/clipboard/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"kind": "text", "content": "管理员不能同步"},
    )
    device_admin_api = client.get("/api/config/status", headers=device_headers)
    device_sync = client.post(
        "/api/clipboard/upload",
        headers=new_device_headers,
        json={"kind": "text", "content": "设备可以同步"},
    )
    renamed = client.put(
        f"/api/devices/{device['device_id']}/name",
        headers=admin_headers,
        json={"name": "已重命名 Android 手机"},
    )
    renamed_device_id = renamed.json()["device_id"]
    current_project_url = client.get(
        f"/api/devices/{quote(renamed_device_id, safe='')}/configuration",
        headers=admin_headers,
    ).json()["tasker"]["project_url"]
    devices = client.get("/api/devices", headers=admin_headers)
    disabled = client.put(
        f"/api/devices/{quote(renamed_device_id, safe='')}/enabled",
        headers=admin_headers,
        json={"enabled": False},
    )
    revoked_sync = client.post(
        "/api/clipboard/upload",
        headers=new_device_headers,
        json={"kind": "text", "content": "已撤销设备不能同步"},
    )
    stale_project = client.get(pending_project_url)
    revoked_project = client.get(current_project_url)
    deleted = client.delete(
        f"/api/devices/{quote(renamed_device_id, safe='')}",
        headers=admin_headers,
    )
    deleted_project = client.get(current_project_url)

    assert created.status_code == 201
    assert len(device["token"]) >= 32
    assert admin_sync.status_code == 200
    assert device_admin_api.status_code == 401
    assert device_sync.status_code == 200
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "已重命名 Android 手机"
    assert renamed_device_id == "已重命名 Android 手机"
    assert devices.status_code == 200
    assert all("token" not in item and "token_hash" not in item for item in devices.json())
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert revoked_sync.status_code == 401
    assert stale_project.status_code == 404
    assert revoked_project.status_code == 200
    assert device["token"].encode("utf-8") in revoked_project.content
    assert deleted.status_code == 204
    assert deleted_project.status_code == 404


def test_ios_device_creation_returns_ready_to_import_configuration(api_client):
    client, headers, admin_token, _device_headers = api_client
    config_manager.set("server.public_base_url", "https://clipboard.example.com")

    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "个人 iPhone", "platform": "ios"},
    )
    payload = created.json()
    configuration = json.loads(payload["ios"]["configuration_json"])
    ios_device = next(record for record in config_manager.list_devices() if record.device_id == payload["device_id"])

    assert created.status_code == 201
    assert ios_device.platform == "ios"
    assert payload["base_url"] == "https://clipboard.example.com"
    assert payload["windows"] is None
    assert payload["tasker"] is None
    assert payload["ios"]["configuration_shortcut_url"] == (
        "https://www.icloud.com/shortcuts/23557a2803e54944a3b681035f65888e"
    )
    assert "<svg" in payload["ios"]["configuration_shortcut_qr_svg"]
    assert payload["ios"]["push_shortcut_url"].startswith("https://www.icloud.com/shortcuts/")
    assert payload["ios"]["pull_shortcut_url"].startswith("https://www.icloud.com/shortcuts/")
    assert payload["ios"]["screenshot_upload_shortcut_url"] == (
        "https://www.icloud.com/shortcuts/01516358ef4b432a965653b8ddcbcf45"
    )
    assert "<svg" in payload["ios"]["push_shortcut_qr_svg"]
    assert "<svg" in payload["ios"]["pull_shortcut_qr_svg"]
    assert "<svg" in payload["ios"]["screenshot_upload_shortcut_qr_svg"]
    assert "<svg" in payload["ios"]["configuration_qr_svg"]
    assert configuration["api_base_url"] == "https://clipboard.example.com"
    assert configuration["device_id"] == payload["device_id"]
    assert configuration["auth_token"] != admin_token
    assert configuration["auth_token"] == ios_device.token


def test_health_check_reports_healthy_and_security_headers(api_client):
    client, _headers, _token, _device_headers = api_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_runtime_mode_uses_docker_environment(tmp_path, monkeypatch):
    config_manager.init_at(str(tmp_path))
    monkeypatch.setenv("DOCKER_MODE", "1")

    assert config_manager.get_runtime_mode() == "docker_server"


def test_runtime_mode_uses_server_only_environment(tmp_path, monkeypatch):
    config_manager.init_at(str(tmp_path))
    monkeypatch.delenv("DOCKER_MODE", raising=False)
    monkeypatch.setenv("SERVER_ONLY", "1")

    assert config_manager.get_runtime_mode() == "server_only"


def test_runtime_mode_reports_client_only_when_server_disabled(tmp_path, monkeypatch):
    config_manager.init_at(str(tmp_path))
    monkeypatch.delenv("DOCKER_MODE", raising=False)
    monkeypatch.delenv("SERVER_ONLY", raising=False)
    monkeypatch.setattr(config_manager.sys, "platform", "win32")
    config_manager.set("server.enabled", False)

    assert config_manager.get_runtime_mode() == "client_only"


def test_sse_listener_ignores_own_event_and_downloads_remote_item(tmp_path, monkeypatch):
    config_manager.init_at(str(tmp_path))
    config_manager.set("server.enabled", False)
    config_manager.set("client.device_id", "local-device")
    downloaded_item = clipboard_api_client.RemoteClipboardItem(
        kind="text",
        content="远程内容",
    )
    applied_items = []
    monkeypatch.setattr(sse_listener.api_client, "download", lambda: downloaded_item)
    monkeypatch.setattr(sse_listener, "_apply_remote_item", applied_items.append)

    sse_listener._handle_event(
        json.dumps({"source_device_id": "local-device"})
    )
    sse_listener._handle_event(
        json.dumps({"source_device_id": "remote-device"})
    )

    assert applied_items == [downloaded_item]


def test_android_device_creation_returns_configured_tasker_project(api_client):
    """测试 Android 设备创建返回 Tasker 接入资产。"""
    client, headers, _admin_token, _device_headers = api_client
    config_manager.set("server.public_base_url", "https://clipboard.example.com")

    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "个人 Android", "platform": "android"},
    )
    data = created.json()
    tasker = data["tasker"]
    project = client.get(tasker["project_url"])
    repeated_project = client.get(tasker["project_url"])
    project_qr = client.get(tasker["project_qr_url"])
    upload_task = client.get(tasker["upload_url"])

    assert created.status_code == 201
    assert data["windows"] is None
    assert data["ios"] is None
    assert "expires_at" not in tasker
    assert tasker["project_url"].startswith("https://clipboard.example.com/setup/tasker/configured/")
    assert quote(data["device_id"], safe="") in tasker["project_url"]
    assert project_qr.status_code == 200
    assert project_qr.headers["content-type"] == "image/svg+xml"
    assert project.status_code == 200
    assert project.headers["cache-control"] == "no-store"
    assert "Clipboard%20Dispatcher.prj.xml" in project.headers["content-disposition"]
    assert upload_task.status_code == 200
    assert upload_task.headers["cache-control"] == "no-store"
    assert repeated_project.status_code == 200
    assert repeated_project.content == project.content

def test_failed_tasker_generation_keeps_stable_download_available(api_client, monkeypatch):
    client, headers, _admin_token, _device_headers = api_client
    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "Tasker 生成失败测试", "platform": "android"},
    ).json()
    project_url = created["tasker"]["project_url"]
    original_builder = routes_provisioning._build_configured_tasker_project_xml

    def fail_generation(base_url: str, auth_token: str, device_id: str) -> str:
        raise RuntimeError(
            f"测试生成失败: base_url={base_url}, token_length={len(auth_token)}, device_id={device_id}"
        )

    monkeypatch.setattr(routes_provisioning, "_build_configured_tasker_project_xml", fail_generation)
    with pytest.raises(RuntimeError, match="测试生成失败"):
        client.get(project_url)

    monkeypatch.setattr(routes_provisioning, "_build_configured_tasker_project_xml", original_builder)
    retry = client.get(project_url)

    assert retry.status_code == 200
    assert b"<TaskerData" in retry.content


def test_existing_mobile_device_reprovision_rotates_token_without_creating_device(api_client):
    client, headers, _admin_token, _device_headers = api_client
    created = client.post(
        "/api/devices",
        headers=headers,
        json={"name": "需重新接入的 Android", "platform": "android"},
    ).json()
    device_count = len(config_manager.list_devices())
    old_headers = {"Authorization": f"Bearer {created['token']}"}
    old_project_url = created["tasker"]["project_url"]

    provisioned = client.post(
        f"/api/devices/{created['device_id']}/provision",
        headers=headers,
    )
    data = provisioned.json()
    old_sync = client.post("/api/clipboard/upload", headers=old_headers, json={"kind": "text", "content": "旧凭据"})
    new_sync = client.post(
        "/api/clipboard/upload",
        headers={"Authorization": f"Bearer {data['token']}"},
        json={"kind": "text", "content": "新凭据"},
    )
    stale_download = client.get(old_project_url)

    assert provisioned.status_code == 200
    assert data["device_id"] == created["device_id"]
    assert data["token"] != created["token"]
    assert data["tasker"]["project_url"] == old_project_url
    assert len(config_manager.list_devices()) == device_count
    assert old_sync.status_code == 401
    assert new_sync.status_code == 200
    assert stale_download.status_code == 200
    assert created["token"].encode("utf-8") not in stale_download.content
    assert data["token"].encode("utf-8") in stale_download.content


def test_invalid_config_is_rejected_without_persistence(api_client):
    client, headers, _token, _device_headers = api_client
    original_port = config_manager.get("server.port", 0)

    response = client.put(
        "/api/config/server",
        headers=headers,
        json={
            "enabled": True,
            "host": "0.0.0.0",
            "port": "invalid",
            "public_base_url": "",
            "clipboard_ttl": 300,
            "max_payload_size": 5_242_880,
            "enable_image_sync": True,
            "max_image_size": 512_000,
        },
    )

    assert response.status_code == 422
    assert response.json()["message"] == "请求数据校验失败"
    assert config_manager.get("server.port", 0) == original_port


def test_server_config_updates_storage_ttl_immediately(api_client):
    client, headers, _token, _device_headers = api_client

    response = client.put(
        "/api/config/server",
        headers=headers,
        json={
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8000,
            "public_base_url": "",
            "clipboard_ttl": 42,
            "max_payload_size": 5_242_880,
            "enable_image_sync": True,
            "max_image_size": 512_000,
        },
    )

    from server.routes_clipboard import storage

    assert response.status_code == 200
    assert storage is not None
    assert storage.ttl == 42


def test_server_token_rotation_invalidates_old_token(api_client):
    client, headers, _token, _device_headers = api_client
    new_token = "n" * 32

    rotated = client.put(
        "/api/config/server/token",
        headers=headers,
        json={"auth_token": new_token},
    )
    old_credentials = client.get("/api/config/status", headers=headers)
    new_credentials = client.get(
        "/api/config/status",
        headers={"Authorization": f"Bearer {new_token}"},
    )

    assert rotated.status_code == 200
    assert old_credentials.status_code == 401
    assert new_credentials.status_code == 200


def test_clipboard_empty_download_returns_204(api_client):
    """测试无剪贴板时下载返回 204。"""
    client, _admin_headers, _token, device_headers = api_client

    download = client.get("/api/clipboard/download", headers=device_headers)

    assert download.status_code == 204
    assert download.headers["x-clipboard-kind"] == "empty"


def test_invalid_tasker_device_has_request_error(api_client):
    client, _headers, _token, _device_headers = api_client

    response = client.get("/setup/tasker/configured/invalid/upload")

    assert response.status_code == 404
    assert "Tasker 设备不存在" in response.json()["message"]


def test_clipboard_api_uses_only_upload_download_and_stream(api_client):
    client, _admin_headers, _token, source_headers = api_client
    _target_device, target_token = config_manager.create_device("同步目标", "android", time.time())
    target_headers = {"Authorization": f"Bearer {target_token}"}

    text_upload = client.post(
        "/api/clipboard/upload",
        headers=source_headers,
        json={"kind": "text", "content": "跨设备文本"},
    )
    text_download = client.get("/api/clipboard/download", headers=target_headers)
    image_bytes = _png_bytes()
    image_upload = client.post(
        "/api/clipboard/upload",
        headers={**target_headers, "Content-Type": "application/octet-stream"},
        content=image_bytes,
    )
    image_download = client.get("/api/clipboard/download", headers=source_headers)

    assert text_upload.status_code == 200
    assert text_upload.json() == {"kind": "text"}
    assert text_download.status_code == 200
    assert text_download.headers["x-clipboard-kind"] == "text"
    assert text_download.text == "跨设备文本"
    assert image_upload.status_code == 200
    assert image_upload.json() == {"kind": "image"}
    assert image_download.status_code == 200
    assert image_download.headers["x-clipboard-kind"] == "image"
    assert image_download.headers["content-type"] == "image/png"
    assert client.get("/api/clipboard/latest", headers=source_headers).status_code == 404
    assert client.get("/api/clipboard/capabilities", headers=source_headers).status_code == 404
    assert client.get("/api/clipboard/items/obsolete", headers=source_headers).status_code == 404


def test_clipboard_text_upload_download_and_payload_limit(api_client):
    """测试统一上传端点的文本上传、下载和载荷限制。"""
    client, _admin_headers, _token, device_headers = api_client

    upload = client.post(
        "/api/clipboard/upload",
        headers=device_headers,
        json={"kind": "text", "content": "同步内容"},
    )
    download = client.get("/api/clipboard/download", headers=device_headers)

    assert upload.status_code == 200
    assert upload.json() == {"kind": "text"}
    assert download.status_code == 200
    assert download.headers["x-clipboard-kind"] == "text"
    assert download.text == "同步内容"

    config_manager.set("server.max_payload_size", 3)
    rejected = client.post(
        "/api/clipboard/upload",
        headers=device_headers,
        json={"kind": "text", "content": "abcd"},
    )

    assert rejected.status_code == 413


def test_dashboard_tracks_cross_device_sync_activities(api_client):
    """测试控制台跨设备同步活动统计。"""
    client, admin_headers, _admin_token, source_headers = api_client
    target_device, target_token = config_manager.create_device("同步目标", "android", time.time())
    target_headers = {"Authorization": f"Bearer {target_token}"}

    text_upload = client.post(
        "/api/clipboard/upload",
        headers=source_headers,
        json={"kind": "text", "content": "跨设备文本"},
    )
    target_download = client.get("/api/clipboard/download", headers=target_headers)
    first_status = client.get("/api/config/status", headers=admin_headers).json()
    assert text_upload.status_code == 200
    assert target_download.status_code == 200
    assert first_status["cross_device_sync_count"] == 1
    assert len(first_status["recent_sync_activities"]) >= 1
    assert first_status["recent_sync_activities"][0]["content_type"] == "text"

    image_upload = client.post(
        "/api/clipboard/upload",
        headers={**target_headers, "Content-Type": "image/png"},
        content=_png_bytes(),
    )
    image_download = client.get("/api/clipboard/download", headers=source_headers)

    final_status = client.get("/api/config/status", headers=admin_headers).json()
    assert image_upload.status_code == 200
    assert image_download.status_code == 200
    assert final_status["cross_device_sync_count"] == 2
    latest_activity = final_status["recent_sync_activities"][0]
    assert latest_activity["device_id"] == "测试设备"
    assert latest_activity["source_device_id"] == target_device.device_id
    assert latest_activity["direction"] == "download"
    assert latest_activity["content_type"] == "image"
    assert latest_activity["occurred_at"] > 0


def test_clipboard_storage_keeps_only_latest_item(api_client):
    """测试剪贴板存储只保留最新条目。"""
    client, _admin_headers, _token, device_headers = api_client
    first_upload = client.post(
        "/api/clipboard/upload",
        headers=device_headers,
        json={"kind": "text", "content": "第一条文本"},
    )
    second_upload = client.post(
        "/api/clipboard/upload",
        headers=device_headers,
        json={"kind": "text", "content": "第二条文本"},
    )
    download = client.get("/api/clipboard/download", headers=device_headers)

    assert first_upload.status_code == 200
    assert second_upload.status_code == 200
    assert download.status_code == 200
    assert download.text == "第二条文本"


def test_clipboard_image_upload_and_download(api_client):
    """测试统一上传端点的图片上传和下载。"""
    client, _admin_headers, _token, device_headers = api_client
    image_bytes = _png_bytes()

    upload = client.post(
        "/api/clipboard/upload",
        headers={**device_headers, "Content-Type": "image/png"},
        content=image_bytes,
    )

    assert upload.status_code == 200
    assert upload.json() == {"kind": "image"}

    download = client.get("/api/clipboard/download", headers=device_headers)

    assert download.status_code == 200
    assert download.headers["x-clipboard-kind"] == "image"
    assert download.headers["content-type"] == "image/png"
    assert download.content == image_bytes


def test_clipboard_image_upload_enforces_size_limit_and_server_switch(api_client):
    """测试图片上传的服务端开关和大小限制。"""
    client, _admin_headers, _token, device_headers = api_client
    image_bytes = _png_bytes()
    image_headers = {**device_headers, "Content-Type": "image/png"}

    config_manager.set("server.enable_image_sync", False)
    disabled = client.post("/api/clipboard/upload", headers=image_headers, content=image_bytes)
    assert disabled.status_code == 403

    config_manager.set("server.enable_image_sync", True)
    config_manager.set("server.max_image_size", len(image_bytes) - 1)
    oversized = client.post("/api/clipboard/upload", headers=image_headers, content=image_bytes)
    assert oversized.status_code == 413


def test_clipboard_image_upload_rejects_invalid_payload(api_client):
    """测试图片上传拒绝无效请求体。"""
    client, _admin_headers, _token, device_headers = api_client
    response = client.post(
        "/api/clipboard/upload",
        headers={**device_headers, "Content-Type": "image/png"},
        content=b"not-an-image",
    )

    assert response.status_code == 422


def _png_bytes() -> bytes:
    """创建确定性的测试 PNG。"""
    with BytesIO() as output:
        Image.new("RGB", (2, 2), (1, 2, 3)).save(output, format="PNG")
        return output.getvalue()
