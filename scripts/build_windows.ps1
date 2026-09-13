$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$entryPoint = Join-Path $projectRoot 'src\app\main.py'
$appPath = Join-Path $projectRoot 'src\app'
$uiPath = Join-Path $projectRoot 'src\app\ui'
$taskerPath = Join-Path $projectRoot 'src\tasker'
$distPath = Join-Path $projectRoot 'dist'
$buildPath = Join-Path $projectRoot 'build'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "未找到项目虚拟环境 Python：$pythonPath"
}
if (-not (Test-Path -LiteralPath $entryPoint)) {
    throw "未找到应用入口：$entryPoint"
}
if (-not (Test-Path -LiteralPath $uiPath)) {
    throw "未找到 UI 资源目录：$uiPath"
}
if (-not (Test-Path -LiteralPath $taskerPath)) {
    throw "未找到 Tasker 资源目录：$taskerPath"
}

& $pythonPath -m pip install -r (Join-Path $projectRoot 'src\requirements.txt')
& $pythonPath -m pip install -r (Join-Path $projectRoot 'src\requirements-build.txt')
& $pythonPath -m PyInstaller --noconfirm --clean --windowed --onefile --name ClipboardDispatcher --distpath $distPath --workpath $buildPath --paths $appPath --collect-submodules server --collect-submodules client --hidden-import config_manager --hidden-import tray --add-data "$uiPath;ui" --add-data "$taskerPath;tasker" $entryPoint
