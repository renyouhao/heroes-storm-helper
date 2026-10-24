param([switch]$Demo, [switch]$Legacy)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 安装依赖.ps1' }
if (-not $Legacy -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'frontend/dist/index.html'))) {
    throw '缺少主界面资源，请先运行 安装依赖.ps1；也可用 -Legacy 临时启动旧界面。'
}
$launchArgs = @('-m', 'hots_chat.app')
if ($Demo) { $launchArgs += '--demo' }
if ($Legacy) { $launchArgs += '--legacy-ui' }
& $python @launchArgs
if ($LASTEXITCODE -ne 0) { throw "助手退出，错误码：$LASTEXITCODE。请检查启动提示及 WebView2 Runtime。" }
