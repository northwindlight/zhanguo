# Start the stock distribution interactively on Windows.
# Usage: .\start-stock.ps1 [-Check] [-Language en-US|zh-CN]
param(
    [switch]$Check,
    [string]$Language = "en-US"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = "python"
if (Test-Path ".\.venv\Scripts\python.exe") { $py = ".\.venv\Scripts\python.exe" }

if (-not (Test-Path "mp_config.json")) {
    Copy-Item "mp_config.stock.example.json" "mp_config.json"
    Write-Host "created mp_config.json from the template - fill in base_url / api_key / model, then rerun."
    Write-Host "(seats with client = `"sim`" need no key.)"
    exit 0
}

& $py -c "import pams" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "pams==0.3.0 is missing: $py -m pip install -r requirements-stock.txt" -ForegroundColor Red
    exit 2
}

$extra = @("--config", "mp_config.json", "--language", $Language)
if ($Check) { $extra += "--check-config" }
& $py mp_run.py @extra
