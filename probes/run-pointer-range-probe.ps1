param(
    [uint64]$TargetAddress=0x1c213290000,
    [uint64]$TargetEnd=0x1c2132a0000,
    [long]$MarkerAddress=0x1c21329aafb,
    [string]$ExpectedMarker='HOTS_CHAT_TEST_20261019_A7F3',
    [string]$OutputName='buffer-range-references.json',
    [uint64]$ExtraTarget=0
)
$ErrorActionPreference='Stop'
$compiler = New-Object System.CodeDom.Compiler.CompilerParameters
$compiler.CompilerOptions='/unsafe /optimize'
$compiler.ReferencedAssemblies.Add('System.dll') | Out-Null
$compiler.ReferencedAssemblies.Add('System.Core.dll') | Out-Null
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'PointerRangeProbe.cs'))) -CompilerParameters $compiler
# Verify actual matching code at the inclusive/exclusive boundaries before game access.
$fixture=New-Object byte[] 32
[BitConverter]::GetBytes([uint64]0x1000).CopyTo($fixture,0)
[BitConverter]::GetBytes([uint64]0x1fff).CopyTo($fixture,8)
[BitConverter]::GetBytes([uint64]0x2000).CopyTo($fixture,16)
$test=[PointerRangeProbe]::Match($fixture,32,0x3000,0x1000,0x2000)
if($test.Count -ne 2 -or $test[0].Address -ne '0x3000' -or $test[1].Address -ne '0x3008') { throw 'Pointer range boundary check failed' }
Write-Host 'Pointer range boundary check passed.'
$result=[PointerRangeProbe]::Scan(33340,$TargetAddress,$TargetEnd,$MarkerAddress,$ExpectedMarker,$ExtraTarget)
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot $OutputName) -Encoding UTF8
@{wall_seconds=$result.WallSeconds;cpu_seconds=$result.CpuSeconds;scanned_mib=$result.ScannedMiB;complete=$result.Complete;reference_count=$result.References.Count;references=@($result.References | Select-Object -First 12)} | ConvertTo-Json -Depth 5
