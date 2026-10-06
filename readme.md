# Clipboard Dispatcher

[简体中文](readme.md) | [English](readme.en.md)

个人自托管的文本与图片剪贴板同步服务，支持 Windows 一体化程序和通用 Docker 服务端。两种形态共用同一套服务端、配置格式与同步 API，不依赖单一 NAS 厂商。

## 文档导航

| 需要完成的事项 | 对应文档 |
| --- | --- |
| 下载 Windows EXE | [最新 EXE](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe) · [SHA256](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe.sha256) · [Releases](https://github.com/ilredirana/clipboard_dispatcher/releases) |
| 查看英文项目说明 | [English README](readme.en.md) |
| 部署服务、创建设备、处理常见问题 | [使用说明书](docs/使用说明.zh-CN.md) |
| 为公网访问配置 HTTPS | [HTTPS 反向代理部署](docs/reverse-proxy.zh-CN.md) |
| 导入和排查 Android Tasker 项目 | [Tasker 使用说明](src/tasker/README.zh-CN.md) |

## 下载 Windows EXE

### v1.0.1 安全升级

此版本修复 Android Tasker 配置下载的认证绕过。升级后，请在管理页为所有已有 Android 设备重新签发 Token，并重新下载、导入 Tasker 项目，以撤销旧版本已泄露的凭据。旧的无授权下载链接已停用。

1. 打开 [最新正式版本](https://github.com/ilredirana/clipboard_dispatcher/releases/latest)，阅读版本说明。
2. 在 **Assets** 中下载 `ClipboardDispatcher.exe` 和 `ClipboardDispatcher.exe.sha256`，放入同一目录。
3. 按下方 PowerShell 命令校验文件，然后运行 EXE。

也可直接下载 [EXE](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe) 和 [SHA256 校验文件](https://github.com/ilredirana/clipboard_dispatcher/releases/latest/download/ClipboardDispatcher.exe.sha256)。正式版本文件保存在 Releases 中，不受 Actions Artifacts 的 30 天保留期限制。

开发构建位于 [Build Windows EXE](https://github.com/ilredirana/clipboard_dispatcher/actions/workflows/build-windows.yml)。选择成功运行记录，在 **Artifacts** 中下载 `ClipboardDispatcher-windows-x64` 并解压。开发构建保留 30 天，下载需要登录 GitHub，操作说明见 [GitHub 官方文档](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)。

## 支持范围

1. 文本同步。
2. PNG、JPEG、WebP 图片同步。
3. Windows 剪贴板客户端。
4. iOS 快捷指令配置二维码，当前仅支持系统语言为中文的设备，详见 [TODO](#todo)。
5. Android Tasker 任务与项目 XML。
6. 每台同步设备独立 Token。

文件同步不在当前版本范围内。

服务端只保留最新一条剪贴板内容，默认有效期为 300 秒，可在管理页调整。文本、图片和同步活动记录保存在内存中，服务重启后清空；设备记录和运行配置保存在 `config.json`。

## Windows 一体化程序

1. 按上方下载说明取得 `ClipboardDispatcher.exe` 与 `.sha256` 文件，放入当前用户有写权限的目录。
2. 校验 SHA-256 后直接运行 EXE，无需额外安装 Python 或 Docker。
3. 双击系统托盘图标打开初始化页，设置至少 32 个字符的管理员 Token。
4. 初始化完成后，程序直接使用本机服务地址、计算机名称和管理员 Token 同步文本与图片，无需创建或导入本机 Windows 客户端配置。

PowerShell 校验命令：

```powershell
$expected = (Get-Content .\ClipboardDispatcher.exe.sha256).Split(' ')[0]
$actual = (Get-FileHash .\ClipboardDispatcher.exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'SHA-256 校验失败' }
```

程序配置和日志保存在 EXE 同目录的 `config.json` 与 `app.log`。退出程序使用托盘菜单“退出”。更新 EXE 前先退出程序，保留原目录中的 `config.json`，再替换 EXE。

从源码运行需要 Python 3.12（64 位）。在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src\requirements.txt
.\.venv\Scripts\python.exe src\app\main.py
```

创建上述虚拟环境后，构建可执行文件：

```powershell
.\scripts\build_windows.ps1
```

构建产物位于 `dist\ClipboardDispatcher.exe`。

该产物启动本机服务端、Windows 剪贴板监听、SSE 拉取和系统托盘，无需 Docker 与 NAS。

无法启动时，查看启动错误提示和同目录的 `app.log`，检查配置 JSON、目录写入权限、端口占用和 Windows Explorer 是否运行。同一配置目录只允许一个实例；重新启动前先退出已有实例。

### GitHub 自动构建

工作流位于 [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml)，以下操作会触发构建：

- 推送到 `main` 分支。
- 推送以 `v` 开头的标签。
- 创建或更新以 `main` 为目标分支的 Pull Request。
- 在 Actions 页面选择 **Run workflow** 手动运行。

流水线使用 Windows 和 Python 3.12（64 位），依次执行 Ruff、测试、发行资源校验、PyInstaller 打包和 EXE 启动验证，最后上传 EXE 与 SHA256 校验文件。云端 EXE 验证使用服务端模式；真实托盘加载由本地桌面冒烟测试验证。

推送版本标签还会将通过验证的 EXE 和校验文件发布到 GitHub Releases。标签必须为 `v<应用版本>`，例如应用版本为 `1.0.1` 时使用 `v1.0.1`。分支推送和 Pull Request 只生成开发构建。

发布新版本前，更新 `src/app/server/version.py` 和 `src/docker-compose.image.yaml` 中的版本并提交，然后创建、推送对应标签。以下以更新为 `1.0.1` 为例：

```bash
git tag -a v1.0.1 -m "Clipboard Dispatcher 1.0.1：填写版本说明和升级步骤"
git push origin v1.0.1
```

后续版本使用对应的新标签，不覆盖已经发布的版本。

## 通用 Docker 服务端

需要 Docker Engine 与 Docker Compose v2。项目提供两种 Docker 运行方式：直接运行源码和本地镜像。

### 直接运行源码

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

镜像在本地构建；离线部署包中的镜像也会在容器首次启动时安装 Python 依赖，因此首次启动仍需要访问依赖源。

先构建镜像：

```powershell
.\scripts\build_docker_image.ps1 -Architecture amd64 -Image clipboard-dispatcher:1.0.1
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

## 初始化与设备管理

1. Windows 首次启动访问 `/setup`，设置管理员 Token；Docker 首次启动自动完成初始化，直接使用 `data/config.json` 中的 `server.auth_token` 登录。
2. 登录管理页后，进入一级导航“同步设备”，创建需要独立接入的 Windows、iOS 或 Android 设备。设备名称必须唯一，并同时作为设备 ID。
3. 每个设备行提供“查看当前配置”按钮。Windows 显示服务地址、设备 ID、Token 和配置下载，目标 Windows 客户端可在“同步行为”中直接导入 JSON；iOS 显示配置导入、上传剪贴板、截图自动上传、下载剪贴板四个快捷指令二维码，以及设备配置二维码；Android 显示 Tasker 项目与单任务二维码和下载入口。
4. 设备 Token 以明文持久化，刷新页面后仍可查看和下载当前配置。“重新签发”会生成新 Token，旧 Token 的新请求将被拒绝，设备需重新导入配置。重命名设备也会改变设备 ID，需重新获取并导入接入配置。
5. 独立设备 Token 无法访问管理接口。Windows 一体化程序仅从本机回环地址使用管理员 Token 访问同步接口。
6. 禁用设备后，新的上传、下载和 SSE 连接请求会被拒绝，同时禁止获取接入配置并撤销待用下载票据。重新启用后需重新获取下载链接。删除设备后配置接口返回 404。

## 图片限制

服务端默认启用图片同步，单张图片最大 `5242880` 字节，即 5 MiB；图片分辨率上限为 4 亿像素。图片同步开关和单张图片大小上限在管理页“服务端配置”维护。

## iOS 快捷指令

在“同步设备”创建 iOS 设备，打开“查看当前配置”，按页面说明导入配置、上传剪贴板、截图自动上传和下载剪贴板四个快捷指令，并使用设备配置二维码完成接入。手机需要能够访问管理页中配置的 HTTPS 公开服务地址。

当前提供的快捷指令仅支持系统语言为中文的 iOS 设备，英文及其他语言尚未支持。多语言适配和链接更新计划见 [TODO](#todo)。

## Android Tasker

在“同步设备”创建或选择 Android 设备后，管理页提供以下免配置导入方式：

1. 已配置的 `Clipboard Dispatcher.prj.xml`，包含上传与拉取任务。
2. 已配置的上传、拉取单任务 XML。
3. 项目与任务已经写入公开服务地址和该设备的独立 Token，无需创建 Tasker 全局变量。
4. 下载链接使用 256 位随机 ticket，有效期 10 分钟，每个文件仅能成功下载一次；二维码预览不占用下载次数。ticket 绑定设备、文件类型和签发时的 Token，轮换 Token 后旧链接立即失效。过期或已下载时，点击“重新获取下载链接”。链接和二维码包含短时授权，请勿分享。

票据数据库 `provisioning_tickets.sqlite3` 与 `config.json` 位于同一目录，仅保存 ticket 和设备 Token 的 SHA256 摘要。应用访问日志会隐藏 Tasker URL 的查询参数；部署反向代理时也应关闭这些查询参数的日志记录。
5. 任务与图片同步限制见 [Tasker 使用说明](src/tasker/README.zh-CN.md)。

## 验证与构建

创建虚拟环境后，先安装开发和构建依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r src\requirements-dev.txt -r src\requirements-build.txt
```

在项目根目录执行检查与构建。真实托盘测试需要交互式 Windows 桌面：

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

完整发布使用本地交互式脚本，按提示选择 Windows 程序、Docker 镜像包和 Docker 源码包：

```powershell
.\scripts\build_release.ps1 -OutputDirectory .\release
```

本地脚本生成的 Windows 发布文件名为 `ClipboardDispatcher-<版本>-windows-x64.exe`，与 Releases 和 Actions 中的 `ClipboardDispatcher.exe` 不同。脚本还会生成校验文件、Docker 部署目录和 ZIP；重复执行会替换同版本的部署目录与 ZIP。

## TODO

- [ ] **iOS 快捷指令多语言支持**：当前快捷指令仅适用于系统语言为中文的 iOS 设备，尚不支持英文及其他语言。后续需完成多语言适配，并更新快捷指令分享链接与管理页中的导入二维码。英文及其他语言设备请等待更新后的快捷指令。
