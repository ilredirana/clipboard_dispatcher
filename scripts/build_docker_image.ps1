[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('amd64', 'arm64')]
    [string]$Architecture,

    [Parameter(Mandatory = $true)]
    [string]$Image
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'validate_release.py')
if ($LASTEXITCODE -ne 0) {
    throw '发行资源校验失败，已停止 Docker 构建。'
}

& docker buildx build --platform "linux/$Architecture" --load --tag $Image --file (Join-Path $projectRoot 'src\Dockerfile') $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Docker 镜像构建失败：$Image，架构 linux/$Architecture"
}
