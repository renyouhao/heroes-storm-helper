$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw '源码安装需要 Node.js/npm，请安装 Node.js LTS 后重新打开 PowerShell，再运行本脚本。发行包运行无需 Node.js。'
}
& node --version
if ($LASTEXITCODE -ne 0) { throw 'Node.js 无法运行，请重新安装 Node.js LTS' }
& npm.cmd --version
if ($LASTEXITCODE -ne 0) { throw 'npm 无法运行，请重新安装 Node.js LTS' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { py -3 -m venv .venv }
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
& '.venv/Scripts/python.exe' -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw '安装工程失败' }
Push-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend')
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '构建前端界面失败' }
    if (-not (Test-Path -LiteralPath 'dist/index.html')) { throw '缺少前端构建产物' }
} finally { Pop-Location }
& '.venv/Scripts/python.exe' -m pip list --format=freeze --exclude-editable | Set-Content -LiteralPath requirements-lock.txt -Encoding UTF8
Write-Host '安装完成。运行 启动助手.ps1；离线预览使用 -Demo，旧界面使用 -Legacy。新主界面需要 Microsoft Edge WebView2 Runtime。'
