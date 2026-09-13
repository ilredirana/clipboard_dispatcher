# Clipboard Dispatcher

个人自托管的文本与图片剪贴板同步服务，支持 Windows 一体化程序和通用 Docker 服务端。两种形态共用同一套服务端、配置格式与同步 API，不依赖单一 NAS 厂商。

## 文档导航

| 需要完成的事项 | 对应文档 |
| --- | --- |
| 部署服务、创建设备、处理常见问题 | [使用说明书](docs/使用说明.zh-CN.md) |
| 为公网访问配置 HTTPS | [HTTPS 反向代理部署](docs/reverse-proxy.zh-CN.md) |
| 导入和排查 Android Tasker 项目 | [Tasker 使用说明](src/tasker/README.zh-CN.md) |

## 支持范围

1. 文本同步。
2. PNG、JPEG、WebP 图片同步。
3. Windows 剪贴板客户端。
4. iOS 快捷指令配置二维码。
5. Android Tasker 任务与项目 XML。
6. 每台同步设备独立 Token。

文件同步不在当前版本范围内。

## Windows 一体化程序

1. 从本地发布目录取得 `ClipboardDispatcher-<版本>-windows-x64.exe` 与同名 `.sha256` 文件。
2. 校验 SHA-256 后直接运行 EXE，无需安装 Python、Docker 或其他运行库。
3. 双击系统托盘图标打开初始化页，设置至少 32 个字符的管理员 Token。
4. 初始化完成后，程序直接使用本机服务地址、计算机名称和管理员 Token 同步文本与图片，无需创建或导入本机 Windows 客户端配置。

PowerShell 校验命令：

```powershell
$expected = (Get-Content .\ClipboardDispatcher-1.0.0-windows-x64.exe.sha256).Split(' ')[0]
$actual = (Get-FileHash .\ClipboardDispatcher-1.0.0-windows-x64.exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'SHA-256 校验失败' }
```

程序配置保存在 EXE 同目录的 `config.json`。退出程序使用托盘菜单“退出”。

从源码运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src\requirements.txt
.\.venv\Scripts\python.exe src\app\main.py
```

构建可执行文件：

```powershell
.\scripts\build_windows.ps1
```

构建产物位于 `dist\ClipboardDispatcher.exe`。

该产物启动本机服务端、Windows 剪贴板监听、SSE 拉取和系统托盘，无需 Docker 与 NAS。

## 通用 Docker 服务端

需要 Docker Engine 与 Docker Compose v2。项目提供两种 Docker 运行方式：源码构建和离线镜像。

### 源码构建

在项目根目录执行：

```bash
docker compose -f src/docker-compose.yaml up -d
```

该 Compose 直接运行 `python:3.11-slim`，不使用 Dockerfile。项目的 `src` 目录以只读方式挂载到容器，其中包含应用代码和 Tasker 资源；首次启动时，容器根据 `requirements-server.txt` 安装 Python 依赖。依赖状态保留在容器文件系统中。该 Compose 使用项目根目录的 `data` 保存运行配置。

修改 Python、页面或 Tasker 文件后执行：

```bash
docker compose -f src/docker-compose.yaml restart
```

修改 `requirements-server.txt` 后执行同一重启命令，容器会检测文件变化并重新安装依赖。Dockerfile 仅用于构建镜像部署包。

依赖不使用数据卷持久化。`docker compose down`、删除容器或强制重建容器后，下次启动会再次安装依赖。

### 本地镜像

先构建镜像：

```powershell
.\scripts\build_docker_image.ps1 -Architecture amd64 -Image clipboard-dispatcher:1.0.0
```

再启动：

```bash
docker compose -f src/docker-compose.image.yaml up -d
```

镜像名称可通过 `CLIPBOARD_DISPATCHER_IMAGE` 覆盖，外部端口可通过 `CLIPBOARD_DISPATCHER_PORT` 覆盖。默认端口为 `9888`。启动时会安装运行依赖；可在宿主机设置 `HTTP_PROXY`、`HTTPS_PROXY`、`http_proxy`、`https_proxy`，Compose 会传入容器供 pip 使用。

### 发布目录

`build_release.ps1` 会分别生成 `ClipboardDispatcher-<版本>-docker-source` 和 `ClipboardDispatcher-<版本>-docker-image`。源码包进入目录后执行 `docker compose up -d`；镜像包先执行 `docker load -i clipboard-dispatcher-<版本>.tar`，再执行 `docker compose up -d`。

Docker 容器会自动生成管理员 Token、写入 `data/config.json` 并完成初始化，无需 `.env` 或额外脚本。管理页地址为 `http://127.0.0.1:9888`。Docker 仅运行服务端，Windows、iOS 和 Android 设备在管理页创建独立同步凭据后接入。

管理员 Token 保存在 `data/config.json` 的 `server.auth_token` 字段。首次登录时读取该字段即可。

容器以 root 身份运行，以兼容 Windows 和部分 NAS 对只读源码绑定目录的访问权限；运行数据保存在 `data` 目录。

查看运行状态：

```powershell
docker compose -f src\docker-compose.yaml ps
curl http://127.0.0.1:9888/health
```

公网与跨网络访问时，在反向代理中配置 HTTPS，并在管理页填写“公开服务地址”。Windows 配置、iOS 快捷指令配置和 Tasker 二维码将使用该地址。

填写后可在管理页点击“检测 HTTPS 公开地址”。服务端会使用系统信任的证书链访问 `<公开地址>/health`，并明确显示证书错误、连接失败、HTTP 地址或健康检查异常。

Caddy 与 Nginx 配置示例见 [HTTPS 反向代理部署](docs/reverse-proxy.zh-CN.md)。

生成完整本地发布目录：

```powershell
.\scripts\build_release.ps1 -OutputDirectory D:\ClipboardDispatcher-Releases
```

该命令生成 Windows 程序、校验文件、可直接执行 `docker compose up -d` 的 Docker 部署目录和 ZIP。重复执行时会替换同版本的 Docker 部署目录与 ZIP。

## 初始化与设备管理

1. 首次启动访问 `/setup`，设置管理员 Token。
2. 登录管理页后，进入一级导航“同步设备”，创建需要独立接入的 Windows、iOS 或 Android 设备。设备名称必须唯一，并同时作为设备 ID。
3. 每个设备行提供“查看当前配置”按钮。Windows 显示服务地址、设备 ID、Token 和配置下载，目标 Windows 客户端可在“同步行为”中直接导入 JSON；iOS 显示配置导入、上传剪贴板、截图自动上传、下载剪贴板四个快捷指令二维码，以及设备配置二维码；Android 显示 Tasker 项目与单任务二维码和下载入口。
4. 设备 Token 以明文持久化，刷新页面后仍可查看和下载当前配置。“重新签发”会生成新 Token 并立即废止旧 Token。
5. 独立设备 Token 无法访问管理接口。Windows 一体化程序仅从本机回环地址使用管理员 Token 访问同步接口。
6. 禁用设备后，该设备立即无法上传、下载和订阅 SSE，但当前配置仍可查看和下载；删除设备后配置与 Tasker 下载入口均返回 404。

## 图片限制

服务端默认启用图片同步，单张图片最大 `5242880` 字节，即 5 MB；图片分辨率上限为 4 亿像素。限制与开关在管理页“服务端配置”维护。

## Android Tasker

在“同步设备”创建或选择 Android 设备后，管理页提供以下免配置导入方式：

1. 已配置的 `Clipboard Dispatcher.prj.xml`，包含上传与拉取任务。
2. 已配置的上传、拉取单任务 XML。
3. 项目与任务已经写入公开服务地址和该设备的独立 Token，无需创建 Tasker 全局变量。
4. 下载链接绑定设备 ID，可重复使用；每次下载都会写入设备当前 Token，重新签发无需更换链接。
5. 任务与图片同步限制见 [Tasker 使用说明](src/tasker/README.zh-CN.md)。

## 验证与构建

```powershell
.\.venv\Scripts\python.exe -m pytest -q src\tests
.\.venv\Scripts\python.exe -m ruff check src scripts
.\.venv\Scripts\python.exe scripts\validate_release.py
docker compose -f src\docker-compose.yaml config
docker compose -f src\docker-compose.image.yaml config
.\scripts\build_windows.ps1
.\.venv\Scripts\python.exe scripts\smoke_windows_build.py
```

完整发布使用本地脚本：

```powershell
.\scripts\build_release.ps1 -OutputDirectory D:\ClipboardDispatcher-Releases
```
