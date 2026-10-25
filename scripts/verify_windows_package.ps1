param([Parameter(Mandatory=$true)][string]$Package, [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$taskOutput = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskMove = Join-Path $taskOutput '移动后的中文目录 portable test'
Expand-Archive -LiteralPath $Package -DestinationPath $taskMove
$taskBundle = Join-Path $taskMove 'Zhanguo-Windows-x64'
$taskExe = Join-Path $taskBundle 'Zhanguo.exe'
# A source checkout, configured model, installed interpreter or SDK must not
# silently supply a missing part of the portable package.
$env:ZHANGUO_ROOT = 'Z:\unavailable-source'
$env:ZHANGUO_PYTHON = 'Z:\unavailable-python.exe'
$env:ZHANGUO_DESKTOP_CONFIG = 'Z:\unavailable-private-config.json'
$env:ZHANGUO_RESUME = 'Z:\unavailable-private-save.json'
$env:DOTNET_ROOT = 'Z:\unavailable-dotnet'
$env:DOTNET_ROOT_X64 = 'Z:\unavailable-dotnet'
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"

function Run-GameCheck([string]$Name, [string[]]$GameArguments, [int]$Timeout = 180000) {
    $taskStdout = Join-Path $taskOutput "$Name.stdout.txt"
    $taskStderr = Join-Path $taskOutput "$Name.stderr.txt"
    $taskQuoted = ($GameArguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $taskProc = Start-Process -FilePath $taskExe -ArgumentList $taskQuoted -WorkingDirectory $env:SystemRoot -PassThru `
        -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr
    if (-not $taskProc.WaitForExit($Timeout)) { $taskProc.Kill($true); throw "$Name timed out" }
    if ($taskProc.ExitCode -ne 0) {
        Get-Content $taskStdout; Get-Content $taskStderr
        throw "$Name failed: $($taskProc.ExitCode)"
    }
}

$taskCheck = Join-Path $taskOutput 'portable-check.json'
Run-GameCheck 'portable' @('--headless', '--', '--portable-check', $taskCheck)
$taskReport = Get-Content -Raw $taskCheck | ConvertFrom-Json
if (-not $taskReport.packaged -or $taskReport.host_exit -ne 0 -or $taskReport.period -ne 1) { throw 'Invalid portable verification report' }

# ANGLE can render on the runner's Direct3D device. Validate a real window as
# well as the headless .NET/Python check; the distributed default stays OpenGL.
Run-GameCheck 'menu' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--menu-output', (Join-Path $taskOutput 'menu.png'))
if (-not (Test-Path (Join-Path $taskOutput 'menu.png'))) { throw 'Exported menu did not produce a rendered screenshot' }
Run-GameCheck 'world' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--smoke-output', (Join-Path $taskOutput 'world.png')) 300000
$taskWorld = Get-Content -Raw (Join-Path $taskOutput 'world.png.json') | ConvertFrom-Json
if ($taskWorld.period -lt 3 -or $taskWorld.artwork -ne 'unified-pixel-world' -or -not $taskWorld.readable_font) { throw 'Exported game did not load the current artwork/font/host' }
Write-Output 'WINDOWS_PORTABLE_VERIFIED'
