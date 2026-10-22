param([switch]$Demo)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 安装依赖.ps1' }
if ($Demo) { & $python -m hots_chat.app --demo } else { & $python -m hots_chat.app }
