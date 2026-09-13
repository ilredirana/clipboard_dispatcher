[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

# ==================== 辅助函数 ====================

function Get-ApplicationVersion {
    param([Parameter(Mandatory)][string]$VersionPath)

    $content = Get-Content -LiteralPath $VersionPath -Raw
    $match = [regex]::Match($content, '(?m)^APP_VERSION\s*=\s*"(?<version>\d+\.\d+(?:\.\d+)?)"$')
    if (-not $match.Success) {
        throw "无法从应用版本文件读取 APP_VERSION：$VersionPath"
    }
    return $match.Groups['version'].Value
}

function Copy-ReleaseFile {
    param(
        [Parameter(Mandatory)][string]$SourcePath,
        [Parameter(Mandatory)][string]$DestinationPath
    )
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "发布文件不存在：$SourcePath"
    }
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
}

function Read-YesNo {
    param([Parameter(Mandatory)][string]$Prompt, [string]$Default = 'Y')
    $suffix = if ($Default -eq 'Y') { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $input = Read-Host "$Prompt $suffix"
        if ($input -eq '') { $input = $Default }
        switch ($input.ToUpper()) {
            'Y' { return $true }
            'N' { return $false }
        }
        Write-Host '请输入 Y 或 N。' -ForegroundColor Yellow
    }
}

# ==================== 交付物选择 ====================

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  Clipboard Dispatcher 发布构建' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''

$buildWindows = Read-YesNo '是否构建 Windows 单文件程序？'
$buildDockerImage = Read-YesNo '是否构建 Docker 预构建镜像？'
$buildDockerSource = Read-YesNo '是否生成 Docker 源码部署包？'

Write-Host ''
Write-Host '已选择的交付物：' -ForegroundColor Green
if ($buildWindows)      { Write-Host '  [x] Windows 单文件程序 (.exe)' }
else                    { Write-Host '  [ ] Windows 单文件程序 (.exe)' -ForegroundColor DarkGray }
if ($buildDockerImage)  { Write-Host '  [x] Docker 预构建镜像' }
else                    { Write-Host '  [ ] Docker 预构建镜像' -ForegroundColor DarkGray }
if ($buildDockerSource) { Write-Host '  [x] Docker 源码部署包' }
else                    { Write-Host '  [ ] Docker 源码部署包' -ForegroundColor DarkGray }
Write-Host ''

if (-not ($buildWindows -or $buildDockerImage -or $buildDockerSource)) {
    throw '未选择任何交付物，已停止构建。'
}

# ==================== 初始化路径 ====================

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$versionPath = Join-Path $projectRoot 'src\app\server\version.py'
$version = Get-ApplicationVersion -VersionPath $versionPath
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "未找到项目虚拟环境 Python：$pythonPath"
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

# ==================== 质量检查（仅在需要构建产物时执行） ====================

if ($buildWindows -or $buildDockerImage -or $buildDockerSource) {
    Write-Host '--- 质量检查 ---' -ForegroundColor Cyan

    & $pythonPath (Join-Path $PSScriptRoot 'validate_release.py')
    if ($LASTEXITCODE -ne 0) { throw '发行资源校验失败，已停止本地发布。' }

    & $pythonPath -m ruff check (Join-Path $projectRoot 'src') (Join-Path $projectRoot 'scripts')
    if ($LASTEXITCODE -ne 0) { throw '静态检查失败，已停止本地发布。' }

    & $pythonPath -m pytest -q (Join-Path $projectRoot 'src\tests')
    if ($LASTEXITCODE -ne 0) { throw '集成测试失败，已停止本地发布。' }

    Write-Host '质量检查通过。' -ForegroundColor Green
}

# ==================== Windows 构建 ====================

if ($buildWindows) {
    Write-Host '--- 构建 Windows 单文件程序 ---' -ForegroundColor Cyan

    & (Join-Path $PSScriptRoot 'build_windows.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Windows 单文件程序构建失败。' }

    & $pythonPath (Join-Path $PSScriptRoot 'smoke_windows_build.py')
    if ($LASTEXITCODE -ne 0) { throw 'Windows 单文件程序验证失败。' }

    $windowsName = "ClipboardDispatcher-$version-windows-x64.exe"
    $windowsSourcePath = Join-Path $projectRoot 'dist\ClipboardDispatcher.exe'
    $windowsOutputPath = Join-Path $outputRoot $windowsName

    Copy-ReleaseFile -SourcePath $windowsSourcePath -DestinationPath $windowsOutputPath
    $windowsHash = (Get-FileHash -LiteralPath $windowsOutputPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$windowsOutputPath.sha256" -Value "$windowsHash  $windowsName" -Encoding ascii

    Write-Host "Windows 程序已生成：$windowsOutputPath" -ForegroundColor Green
}

# ==================== Docker 预构建镜像 ====================

if ($buildDockerImage) {
    Write-Host '--- 构建 Docker 预构建镜像 ---' -ForegroundColor Cyan

    $image = "clipboard-dispatcher:$version"
    $dockerfilePath = Join-Path $projectRoot 'src\Dockerfile'

    & docker build --tag $image --file $dockerfilePath $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "Docker 镜像构建失败：$image" }

    $packageName = "ClipboardDispatcher-$version-docker-image"
    $packagePath = Join-Path $outputRoot $packageName
    if (Test-Path -LiteralPath $packagePath) {
        Remove-Item -LiteralPath $packagePath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $packagePath -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packagePath 'data') -Force | Out-Null

    Set-Content -LiteralPath (Join-Path $packagePath 'docker-compose.yaml') -Encoding utf8 -Value @"
services:
  clipboard_dispatcher:
    image: $image
    restart: unless-stopped
    ports:
      - "`${CLIPBOARD_DISPATCHER_PORT:-9888}:8000"
    volumes:
      - ./data:/app/data
    environment:
      HTTP_PROXY:
      HTTPS_PROXY:
      http_proxy:
      https_proxy:
      NO_PROXY: `${NO_PROXY:-localhost,127.0.0.1}
      no_proxy: `${no_proxy:-localhost,127.0.0.1}
      TZ: Asia/Shanghai
"@
    & docker save --output (Join-Path $packagePath "clipboard-dispatcher-$version.tar") $image
    if ($LASTEXITCODE -ne 0) { throw "Docker 镜像导出失败：$image" }
    & docker compose -f (Join-Path $packagePath 'docker-compose.yaml') config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Docker 镜像部署包 Compose 配置校验失败。' }
    Set-Content -LiteralPath (Join-Path $packagePath 'README.md') -Encoding utf8 -Value @"
# Clipboard Dispatcher Docker 镜像部署

1. 执行 docker load -i clipboard-dispatcher-$version.tar 导入镜像。
2. 执行 docker compose up -d 启动服务。
3. 访问 http://<NAS 地址>:9888，管理员 Token 位于 data/config.json。
"@

    $archivePath = Join-Path $outputRoot "$packageName.zip"
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Compress-Archive -LiteralPath $packagePath -DestinationPath $archivePath -CompressionLevel Optimal
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$archivePath.sha256" -Value "$archiveHash  $(Split-Path -Leaf $archivePath)" -Encoding ascii

    Write-Host "Docker 镜像部署包已生成：$packagePath" -ForegroundColor Green
}

# ==================== Docker 源码部署包 ====================

if ($buildDockerSource) {
    Write-Host '--- 打包 Docker 源码部署包 ---' -ForegroundColor Cyan

    $packageName = "ClipboardDispatcher-$version-docker-source"
    $packagePath = Join-Path $outputRoot $packageName

    if (Test-Path -LiteralPath $packagePath) {
        Remove-Item -LiteralPath $packagePath -Recurse -Force
    }

    # 创建目录结构
    New-Item -ItemType Directory -Path $packagePath -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packagePath 'data') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packagePath 'docs') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packagePath 'src') -Force | Out-Null

    # 生成源码部署专用 Compose。
    $sourceComposeContent = @"
# Docker 源码直接运行部署
# 使用方式：在本目录下执行 docker compose up -d

services:
  clipboard_dispatcher:
    image: python:3.11-slim
    working_dir: /src
    entrypoint: ["/bin/sh", "/src/docker-entrypoint.sh"]
    command: ["python", "app/main.py"]
    restart: unless-stopped
    ports:
      - "`${CLIPBOARD_DISPATCHER_PORT:-9888}:8000"
    volumes:
      - ./src:/src:ro
      - ./data:/app/data
    environment:
      DOCKER_MODE: "1"
      PYTHONUNBUFFERED: "1"
      PIP_DISABLE_PIP_VERSION_CHECK: "1"
      HTTP_PROXY:
      HTTPS_PROXY:
      http_proxy:
      https_proxy:
      NO_PROXY: `${NO_PROXY:-localhost,127.0.0.1}
      no_proxy: `${no_proxy:-localhost,127.0.0.1}
      TZ: Asia/Shanghai
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      start_period: 120s
      retries: 3
"@
    Set-Content -LiteralPath (Join-Path $packagePath 'docker-compose.yaml') -Value $sourceComposeContent -Encoding utf8

    # 文档
    Set-Content -LiteralPath (Join-Path $packagePath 'README.md') -Encoding utf8 -Value @"
# Clipboard Dispatcher Docker 源码部署

1. 在当前目录执行 docker compose up -d。
2. 访问 http://<NAS 地址>:9888，管理员 Token 位于 data/config.json。

源码部署直接使用 python:3.11-slim，不需要 Dockerfile。
修改 Python、页面或 Tasker 文件后执行 docker compose restart，无需重新构建镜像。
修改 requirements-server.txt 后执行 docker compose restart，容器会重新安装依赖。
"@
    Copy-ReleaseFile -SourcePath (Join-Path $projectRoot 'docs\reverse-proxy.zh-CN.md') -DestinationPath (Join-Path $packagePath 'docs\reverse-proxy.zh-CN.md')

    # 运行入口脚本。
    Copy-ReleaseFile -SourcePath (Join-Path $projectRoot 'src\docker-entrypoint.sh') -DestinationPath (Join-Path $packagePath 'src\docker-entrypoint.sh')

    # 依赖文件
    Copy-ReleaseFile -SourcePath (Join-Path $projectRoot 'src\requirements-server.txt') -DestinationPath (Join-Path $packagePath 'src\requirements-server.txt')

    # 应用源码（排除 tests、缓存等）
    $srcSource = Join-Path $projectRoot 'src\app'
    $srcDest = Join-Path $packagePath 'src\app'
    Copy-Item -LiteralPath $srcSource -Destination $srcDest -Recurse -Force
    # 清理缓存目录
    Get-ChildItem -LiteralPath $srcDest -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

    # Tasker 资源
    Copy-Item -LiteralPath (Join-Path $projectRoot 'src\tasker') -Destination (Join-Path $packagePath 'src\tasker') -Recurse -Force

    # data 目录占位
    Set-Content -LiteralPath (Join-Path $packagePath 'data\.gitkeep') -Value '运行时配置和日志保存在此目录，请勿删除。' -Encoding utf8

    # 校验 compose 配置
    $composePath = Join-Path $packagePath 'docker-compose.yaml'
    & docker compose -f $composePath config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Docker 源码部署包 Compose 配置校验失败。' }

    # 打包 zip
    $archivePath = Join-Path $outputRoot "$packageName.zip"
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Compress-Archive -LiteralPath $packagePath -DestinationPath $archivePath -CompressionLevel Optimal
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$archivePath.sha256" -Value "$archiveHash  $(Split-Path -Leaf $archivePath)" -Encoding ascii

    Write-Host "Docker 源码部署包已生成：$packagePath" -ForegroundColor Green
}

# ==================== 完成 ====================

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  发布构建完成' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host "输出目录：$outputRoot" -ForegroundColor Green
