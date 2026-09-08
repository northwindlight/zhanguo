# 战国 · 终局结算（Windows PowerShell）
# 解析存档按 GDP/军队/领土/固定资产打分，然后把各国 AI 请进结算厅聊 5 轮。
# 用法：
#   .\settle.ps1                  # 打分 + 结算厅（寄语：终端逐国输入，或 -remarks 指定）
#   .\settle.ps1 -no-chat         # 只打分，不进聊天室
#   .\settle.ps1 -remarks 结算寄语.json -rounds 5   # 其余参数原样透传给 settlement.py
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "mp_save.json")) {
    Write-Host "[错误] 当前目录没有 mp_save.json（先用 start.ps1 跑一局）" -ForegroundColor Red
    Read-Host "回车退出"; exit 1
}

# ---- 找 Python ----
$py = $null
foreach ($c in @("python", "py")) {
    try { & $c --version *> $null; if ($LASTEXITCODE -eq 0) { $py = $c; break } } catch { }
}
if (-not $py) { Write-Host "[错误] 未找到 Python（需 3.10+）" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
if ($py -eq "py") { $py = @("py", "-3") }

# ---- 虚拟环境：复用 start.ps1 建的 .venv ----
if (Test-Path ".venv\Scripts\python.exe") {
    $py = @(".venv\Scripts\python.exe")
}
& $py[0] $py[1..($py.Length-1)] -c "import openai" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[提示] 缺 openai 库：进聊天室需要它（只打分可用 -no-chat）。" -ForegroundColor Yellow
    Write-Host "       先跑一次 .\start.ps1 会自动装好依赖。"
}

Write-Host "[启动] 战国 · 终局结算（打分 + 结算厅）…"
& $py[0] $py[1..($py.Length-1)] settlement.py @args
Read-Host "回车退出"
