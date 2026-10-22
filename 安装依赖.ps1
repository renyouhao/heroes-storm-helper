$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { py -3 -m venv .venv }
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
& '.venv/Scripts/python.exe' -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw '安装工程失败' }
& '.venv/Scripts/python.exe' -m pip list --format=freeze --exclude-editable | Set-Content -LiteralPath requirements-lock.txt -Encoding UTF8
Write-Host '安装完成。运行 启动助手.ps1。'
