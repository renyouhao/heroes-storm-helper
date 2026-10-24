param(
    [ValidateSet('A', 'B')][string]$方案 = 'B',
    [ValidateSet('subtitle', 'panel', 'input')][string]$状态 = 'input'
)
$ErrorActionPreference = 'Stop'
$previewPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $previewPython)) {
    throw '没有找到项目虚拟环境，请先运行 安装依赖.ps1。'
}
$previewScript = Join-Path $PSScriptRoot 'tools\preview_overlay_designs.py'
& $previewPython $previewScript --variant $方案 --mode $状态
if ($LASTEXITCODE -ne 0) {
    throw "悬浮窗预览退出，错误码：$LASTEXITCODE。请保留上方提示。"
}
