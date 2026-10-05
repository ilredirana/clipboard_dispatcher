# Clipboard Dispatcher

[简体中文](readme.md) | [English](readme.en.md)

A self-hosted clipboard service for syncing text and images. Run the Windows application with its built-in server, or deploy the server with Docker. Both use the same server implementation, configuration format, and sync API, with no dependency on a specific NAS vendor.

## Navigation

| Task | Resource |
| --- | --- |
| Download the Windows EXE | [Latest EXE](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe) · [SHA256](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe.sha256) · [Releases](https://github.com/ilredirana/clipboard_dispatcher/releases) |
| Read the Chinese project overview | [简体中文 README](readme.md) |
| Deploy the server, add devices, and troubleshoot | [User guide (Chinese)](docs/使用说明.zh-CN.md) |
| Configure HTTPS for remote access | [Reverse proxy guide (Chinese)](docs/reverse-proxy.zh-CN.md) |
| Import and troubleshoot Android Tasker projects | [Tasker guide (Chinese)](src/tasker/README.zh-CN.md) |

## Download the Windows EXE

1. Open the [latest release](https://github.com/ilredirana/clipboard_dispatcher/releases/latest) and read its release notes.
2. Download `ClipboardDispatcher.exe` and `ClipboardDispatcher.exe.sha256` from **Assets** and place them in the same directory.
3. Verify the checksum using the PowerShell commands below, then run the EXE.

You can also download the [EXE](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe) and [SHA256 file](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe.sha256) directly. Release assets are stored in GitHub Releases and are not subject to the 30-day Actions artifact retention period.

Development builds are available under [Build Windows EXE](https://github.com/ilredirana/clipboard_dispatcher/actions/workflows/build-windows.yml). Open a successful run, download `ClipboardDispatcher-windows-x64` from **Artifacts**, and extract it. These builds are retained for 30 days and require a GitHub login to download; see [GitHub's download instructions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

## Supported features

1. Text sync.
2. PNG, JPEG, and WebP image sync.
3. A Windows clipboard client.
4. QR codes for iOS Shortcuts and device configuration. The current shortcuts support devices whose system language is Chinese; see [TODO](#todo).
5. Android Tasker tasks and project XML files.
6. Separate tokens for each sync device.

File sync is outside the scope of this version.

The server stores only the latest clipboard item. The default expiration is 300 seconds and can be changed in the management page. Clipboard content and sync activity are held in memory and cleared when the server restarts. Device records and runtime settings are persisted in `config.json`.

## Windows application

1. Download `ClipboardDispatcher.exe` and its `.sha256` file as described above. Place them in a directory writable by your user account.
2. Verify the SHA256 checksum, then run the EXE. No separate Python or Docker installation is required.
3. Double-click the system tray icon to open the setup page and set an administrator token of at least 32 characters.
4. After setup, the application syncs text and images using the local server address, computer name, and administrator token. No separate device record or configuration import is needed for this local Windows instance.

Verify the checksum in PowerShell:

```powershell
$expected = (Get-Content .\ClipboardDispatcher.exe.sha256).Split(' ')[0]
$actual = (Get-FileHash .\ClipboardDispatcher.exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'SHA256 verification failed' }
```

The application writes `config.json` and `app.log` beside the EXE. Use “退出” (Exit) in the tray menu to quit. Before updating, quit the application, keep `config.json` in its directory, and replace the EXE.

To run from source, use 64-bit Python 3.12 and execute these commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src\requirements.txt
.\.venv\Scripts\python.exe src\app\main.py
```

After creating the virtual environment, build the executable:

```powershell
.\scripts\build_windows.ps1
```

The output is `dist\ClipboardDispatcher.exe`. It runs the local server, clipboard monitor, SSE listener, and system tray.

If startup fails, read the startup error message and `app.log` beside the EXE. Check the configuration JSON, directory write permissions, port availability, and whether Windows Explorer is running. Only one instance can use a given configuration directory; quit the existing instance before restarting.

### GitHub automated builds

The workflow is defined in [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) and runs when:

- A commit is pushed to `main`.
- A tag beginning with `v` is pushed.
- A Pull Request targeting `main` is opened or updated.
- **Run workflow** is selected on the Actions page.

The pipeline uses Windows and 64-bit Python 3.12. It runs Ruff, tests, release resource checks, PyInstaller packaging, and a packaged EXE smoke test before uploading the EXE and SHA256 file. The cloud smoke test runs in server-only mode. Actual tray loading is verified by the local desktop smoke test.

Pushing a version tag also publishes the verified EXE and checksum to GitHub Releases. The tag must be `v<application-version>`: for example, `v1.0.0` for application version `1.0.0`. Branch pushes and Pull Requests produce development builds only.

Before publishing a new version, update and commit the versions in `src/app/server/version.py` and `src/docker-compose.image.yaml`, then create and push the matching tag. For example, after updating to `1.0.1`:

```bash
git tag -a v1.0.1 -m "Clipboard Dispatcher 1.0.1"
git push origin v1.0.1
```

Use a new matching tag for subsequent versions; do not overwrite published versions.

## Docker server

Docker Engine and Docker Compose v2 are required. You can run the source directly or build a local image.

### Run the source directly

From the repository root:

```bash
docker compose -f src/docker-compose.yaml up -d
```

This Compose configuration uses `python:3.11-slim` directly, without a Dockerfile. The repository's `src` directory is mounted read-only and includes the application and Tasker resources. On first startup, the container installs dependencies from `requirements-server.txt`. Dependencies remain in the container filesystem; runtime configuration is stored in the repository's `data` directory.

After changing Python code, UI files, or Tasker resources:

```bash
docker compose -f src/docker-compose.yaml restart
```

Use the same command after changing `requirements-server.txt`; the entrypoint detects the change and reinstalls dependencies. The Dockerfile is used for image deployment packages.

Dependencies are not persisted in a volume. Removing or recreating the container, including with `docker compose down`, causes dependencies to be installed again on the next startup.

### Local image

The image is built locally. Images included in offline deployment packages also install Python dependencies when the container first starts, so the first startup still requires access to a package index.

Build the image:

```powershell
.\scripts\build_docker_image.ps1 -Architecture amd64 -Image clipboard-dispatcher:1.0.0
```

Start the server:

```bash
docker compose -f src/docker-compose.image.yaml up -d
```

Override the image name with `CLIPBOARD_DISPATCHER_IMAGE` and the host port with `CLIPBOARD_DISPATCHER_PORT`. The default host port is `9888`. For dependency installation, Compose forwards the host's `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, and `https_proxy` variables to the container.

### Deployment packages

`build_release.ps1` can generate `ClipboardDispatcher-<version>-docker-source` and `ClipboardDispatcher-<version>-docker-image`. In a source package directory, run `docker compose up -d`. In an image package directory, first run `docker load -i clipboard-dispatcher-<version>.tar`, then `docker compose up -d`.

On first startup, Docker generates an administrator token, writes `data/config.json`, and completes initialization automatically. No `.env` file or separate initialization script is required. Open `http://127.0.0.1:9888` on the host, or `http://<server-address>:9888` from another device. Read `server.auth_token` in `data/config.json` to log in.

Docker runs only the server. Add separate Windows, iOS, or Android device credentials in the management page before connecting those clients. The container runs as root to support read-only source mounts on Windows and NAS systems. Runtime data is stored in `data`.

Check the server:

```bash
docker compose -f src/docker-compose.yaml ps
curl http://127.0.0.1:9888/health
```

For access across networks, configure an HTTPS reverse proxy and set “公开服务地址” (Public server URL) in the management page. Generated Windows configurations, iOS configurations, and Tasker QR codes use this address.

Select “检测 HTTPS 公开地址” (Check public HTTPS URL) to validate `<public-url>/health` using the server's trusted certificate chain. The result distinguishes certificate errors, connection failures, HTTP URLs, and unhealthy responses. Caddy and Nginx examples are in the [reverse proxy guide (Chinese)](docs/reverse-proxy.zh-CN.md).

## Initialization and device management

1. On Windows, visit `/setup` on first startup to set the administrator token. Docker initializes automatically; log in with `server.auth_token` from `data/config.json`.
2. Open “同步设备” (Sync devices) to add a separate Windows, iOS, or Android client. Device names must be unique and are also used as device IDs.
3. Select “查看当前配置” (View current configuration). Windows devices receive the server URL, device ID, token, and downloadable JSON, which can be imported under “同步行为” (Sync behavior). iOS devices receive four shortcut QR codes and a device configuration QR code. Android devices receive Tasker project and task downloads and QR codes.
4. Device tokens are persisted in plain text so current configurations remain available after refreshing the page. Reissuing a token invalidates new requests using the old token; import the updated configuration on the device. Renaming a device also changes its device ID, so download and import a new configuration.
5. Device tokens cannot access administration APIs. The Windows application accepts the administrator token for clipboard sync only through local loopback requests.
6. Disabling a device rejects new upload, download, and SSE connection requests. Its configuration can still be viewed and downloaded. Deleting a device makes its configuration and Tasker download endpoints return 404.

## Image limits

Image sync is enabled by default. The default maximum image size is `5242880` bytes, or 5 MiB. Images are limited to 400 million pixels. The management page's “服务端配置” (Server settings) controls whether image sync is enabled and the maximum image size.

## iOS Shortcuts

Create an iOS device under “同步设备” (Sync devices) and open its current configuration. Follow the page's instructions to import the configuration, clipboard upload, automatic screenshot upload, and clipboard download shortcuts. Use the device configuration QR code to connect. The phone must be able to reach the configured public HTTPS server URL.

The current shortcuts support only iOS devices whose system language is Chinese. English and other system languages are not supported yet. See [TODO](#todo) for the planned adaptation and link updates.

## Android Tasker

Create or select an Android device in the management page to obtain:

1. A configured `Clipboard Dispatcher.prj.xml` containing upload and download tasks.
2. Configured XML files for the individual upload and download tasks.
3. Tasks with the public server URL and device token already embedded; no Tasker global variables are required.
4. Reusable download links tied to the device ID. Each download uses the device's current token, so reissuing a token does not change the link.
5. Import instructions and sync limitations in the [Tasker guide (Chinese)](src/tasker/README.zh-CN.md).

## Validation and builds

After creating the virtual environment, install development and build dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r src\requirements-dev.txt -r src\requirements-build.txt
```

Run checks and builds from the repository root. The actual tray smoke test requires an interactive Windows desktop:

```powershell
.\.venv\Scripts\python.exe -m pytest -q src\tests
.\.venv\Scripts\python.exe -m ruff check src scripts
.\.venv\Scripts\python.exe scripts\validate_release.py
docker compose -f src\docker-compose.yaml config
docker compose -f src\docker-compose.image.yaml config
.\scripts\build_windows.ps1
.\.venv\Scripts\python.exe scripts\smoke_windows_ci.py
.\.venv\Scripts\python.exe scripts\smoke_windows_build.py
```

For a complete local release, use the interactive script and select the Windows executable, Docker image package, and Docker source package as needed:

```powershell
.\scripts\build_release.ps1 -OutputDirectory .\release
```

The local script names Windows release files `ClipboardDispatcher-<version>-windows-x64.exe`, while Releases and Actions use `ClipboardDispatcher.exe`. The script also generates checksums, Docker deployment directories, and ZIP files. Running it again replaces deployment directories and ZIP files for the same version.

## TODO

- [ ] **Multilingual iOS Shortcuts**: The current shortcuts work only on iOS devices whose system language is Chinese. English and other languages are not supported yet. A future update needs to adapt the shortcuts for additional languages and replace the shared shortcut links and import QR codes in the management page. Users of devices configured in English or another language should wait for the updated shortcuts.
