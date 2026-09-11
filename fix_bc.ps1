# 战国 · BC 纠正跑（Windows 服务器）—— 权重留下，只做 DAgger 纠正
#
# 用法：右键 →「使用 PowerShell 运行」（脚本自提权）
#
# 与 train_bc.ps1 的区别：那个是**从头 BC**（--teacher v8，20 BC + 20 DAgger）；
# 这个是**纠正**（--init 已有权重，纯 DAgger），用来把旧权重要拉回新老师。
# 起因：v8 修了两处（军队能走自家地、木锁死）之后老师变了，而旧权重是照**旧老师**
# 克隆的 —— 重跑整轮 BC 要几十局且会把已学会的再学一遍，从旧权重直接纠正更省。
#
# 为什么必须是 ps1（2026-09-11 踩的坑）：
#   **`Start-Process` 从 ssh 会话里起的子进程，会随 ssh 会话结束被一起带走。**
#   试过一次：日志只留下 `--init` 那一行（flush=True），第一局都没跑完进程就没了
#   （`Get-Process python` 数到 0）。nssm 服务 / 这个提权窗口才是能跨会话活下来的。
#
# 跑的是：
#   --init rl\runs\bc\v9_70.pt  --teacher v9
#   --episodes 20  --dagger-from 0  --turns 70  --ckpt-every 1
#   --out rl\runs\bc\v8_70_fix.pt

$ErrorActionPreference = "Stop"

$Root = "C:\Users\northwind\zhanguo-rl"
$Py   = "C:\Users\northwind\anaconda3\envs\zhanguo-rl\python.exe"
$Svc  = "zhanguo-rl"
$Init = "rl\runs\bc\v9_70.pt"

# ---------------------------------------------------------------- 自提权
$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[提权] 以管理员身份重新启动本脚本…" -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    )
    exit
}

[Console]::OutputEncoding = [Text.Encoding]::UTF8

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " 战国 · BC 纠正跑（v9 老师 / 20 局纯 DAgger / 权重留下）" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------- 旧服务是什么？
# 用户 2026-09-11 问「你考虑服务器的旧服务了吗」—— 这件事之前没有答案，先把配置打出来。
# （非管理员会话**看不到** nssm 服务，会报 "Cannot find any service"；提权后才看得到。）
Write-Host ""
Write-Host "[旧服务] $Svc 的配置：" -ForegroundColor Yellow
$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssm) {
    foreach ($k in @("Application", "AppParameters", "AppDirectory", "Start", "AppStdout", "AppStderr")) {
        $v = (& nssm get $Svc $k 2>&1) -join " "
        Write-Host ("         {0,-14} = {1}" -f $k, $v)
    }
    $st = (& nssm status $Svc 2>&1) -join " "
    Write-Host ("         {0,-14} = {1}" -f "Status", $st)
} else {
    Write-Host "         没找到 nssm 命令，改用 sc.exe 看：" -ForegroundColor Gray
    & sc.exe qc $Svc 2>&1 | ForEach-Object { Write-Host "         $_" }
}
Write-Host ""
Write-Host "         （恢复自启：Set-Service -Name $Svc -StartupType Automatic; Start-Service $Svc）" -ForegroundColor Gray

# ---------------------------------------------------------------- 前置检查
if (-not (Test-Path $Root)) { Write-Host "[错误] 找不到 $Root" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
Set-Location $Root
if (-not (Test-Path $Py)) { Write-Host "[错误] 找不到 $Py" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
if (-not (Test-Path $Init)) {
    Write-Host "[错误] 找不到起点权重 $Init —— 先跑一次 train_bc.ps1" -ForegroundColor Red
    Read-Host "回车退出"; exit 1
}
$ck = Get-Item $Init
Write-Host ("[检查] 起点权重 {0}  {1:N0} 字节  {2}" -f $Init, $ck.Length, $ck.LastWriteTime) -ForegroundColor Green

# ---------------------------------------------------------------- 清场（三层幂等）
Write-Host ""
Write-Host "[清理] 关掉别的训练，免得抢 rl\runs\ 下的检查点…" -ForegroundColor Yellow
try {
    $s = Get-Service -Name $Svc -ErrorAction Stop
    if ($s.Status -ne "Stopped") {
        Stop-Service -Name $Svc -Force -ErrorAction Stop
        try { (Get-Service -Name $Svc).WaitForStatus("Stopped", "00:00:30") } catch { }
        Write-Host "       服务 $Svc 已停止" -ForegroundColor Green
    } else { Write-Host "       服务 $Svc 本来就是 Stopped" -ForegroundColor Gray }
    Set-Service -Name $Svc -StartupType Disabled -ErrorAction SilentlyContinue
} catch { Write-Host "       没有 $Svc 这个服务（或读不到），跳过" -ForegroundColor Gray }

$killed = 0
foreach ($p in @(Get-Process python -EA SilentlyContinue |
                 Where-Object { try { $_.Path -eq $Py } catch { $false } })) {
    Write-Host "       杀 conda python PID $($p.Id)" -ForegroundColor Yellow
    Stop-Process -Id $p.Id -Force -EA SilentlyContinue; $killed++
}
try {
    foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA Stop |
                     Where-Object { $_.CommandLine -match 'rl[\\/]?(train|bc)' })) {
        Write-Host "       杀训练进程 PID $($p.ProcessId)" -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -EA SilentlyContinue; $killed++
    }
} catch { Write-Host "       （命令行兜底查不了，跳过）" -ForegroundColor Gray }
Start-Sleep -Seconds 2
$left = @(Get-Process python -EA SilentlyContinue |
          Where-Object { try { $_.Path -eq $Py } catch { $false } })
if ($left) {
    Write-Host "[错误] 还有 python 没杀掉（PID $($left.Id -join ',')），先手动处理" -ForegroundColor Red
    Read-Host "回车退出"; exit 1
}
Write-Host "[清理] 完成（共处理 $killed 个）" -ForegroundColor Green

# ---------------------------------------------------------------- 开跑
$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "rl\runs\bc"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "fix-$stamp.log"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$bcArgs = @(
    "-m", "rl.bc",
    "--init", $Init,
    "--teacher", "v9",
    "--episodes", "20",
    "--dagger-from", "0",
    "--turns", "70",
    "--ckpt-every", "1",
    "--out", "rl\runs\bc\v9_70_fix.pt"
)

Write-Host ""
Write-Host "[启动] $Py" -ForegroundColor Green
Write-Host "       $($bcArgs -join ' ')" -ForegroundColor Green
Write-Host "[日志] $Log   （UTF-8：读它要 Get-Content -Encoding UTF8）" -ForegroundColor Green
Write-Host ""

$t0 = Get-Date
& $Py @bcArgs 2>&1 | Tee-Object -FilePath $Log
$code = $LASTEXITCODE
$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host " 纠正完成 · 用时 $mins 分钟" -ForegroundColor Green
    Write-Host " 权重：$Root\rl\runs\bc\v9_70_fix.pt" -ForegroundColor Green
    Write-Host ""
    Write-Host " 下一步（重新打分，同一把尺子）：" -ForegroundColor Cyan
    Write-Host "   & `"$Py`" -m rl.compare --ckpt rl\runs\bc\v9_70_fix.pt --episodes 20 --turns 70"
    Write-Host "   & `"$Py`" -m rl.topk    --ckpt rl\runs\bc\v9_70_fix.pt --teacher v9 --turns 70"
} else {
    Write-Host " 纠正退出，exit code = $code · 用时 $mins 分钟" -ForegroundColor Red
    Write-Host " 看日志：$Log" -ForegroundColor Yellow
}
Write-Host "==============================================================" -ForegroundColor Cyan
Read-Host "回车退出"
