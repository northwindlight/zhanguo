# 战国 · RL 训练启动器（Windows 服务器）—— BC 20 局 + DAgger 20 局
#
# 用法：右键 →「使用 PowerShell 运行」（脚本自提权；若只要跑训练、不碰服务，
#       也可以不提权直接跑，见下面 $SkipElevate）
#
# 训练口径（用户 2026-09-11 定）：
#   老师 = v8        —— **新基线**（20 图 T500 2381k / T300 843k；修掉了电厂失控）
#   视野 = 90        —— = 每局 70 回合 + 20（v8 的 ROI 回收期窗口 HORIZON）
#   每局 = 70 回合
#   局数 = 20 局纯 BC + 20 局 DAgger（共 40 局）
#   产物 = rl\runs\bc\v8_70.pt（每 4 局另存一份 ep<N>.pt，可中途量分）
#
# 为什么在服务器上跑：比 Pi 快约 4 倍（Pi 上实测 64 秒/局）。

$ErrorActionPreference = "Stop"

# 只跑训练的话不需要管理员；要动 nssm 服务才需要。默认提权（省得中途卡权限）。
$SkipElevate = $false

$Root = "C:\Users\northwind\zhanguo-rl"
$Py   = "C:\Users\northwind\anaconda3\envs\zhanguo-rl\python.exe"
$Svc  = "zhanguo-rl"

# ---------------------------------------------------------------- 自提权
if (-not $SkipElevate) {
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
}

# 中文输出别乱码（PS 5.1 控制台默认 GBK）
[Console]::OutputEncoding = [Text.Encoding]::UTF8

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host " 战国 · RL 训练（BC 20 + DAgger 20）  by v8 老师 / 视野 90" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------- 前置检查
if (-not (Test-Path $Root)) { Write-Host "[错误] 找不到 $Root" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
Set-Location $Root

if (-not (Test-Path $Py)) {
    Write-Host "[错误] 找不到 conda 环境里的 python：$Py" -ForegroundColor Red
    Write-Host "       查一下：conda env list" -ForegroundColor Yellow
    Read-Host "回车退出"; exit 1
}

# 代码是否已同步？拿 v8 那处修复当探针（同步到位才有「愿望单」这三个字）
$v8 = Join-Path $Root "expand_rule_v8.py"
if (-not (Test-Path $v8)) { Write-Host "[错误] 没有 expand_rule_v8.py，先跑 ~/bin/deploy-zhanguo 同步" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
if (-not (Select-String -Path $v8 -Pattern "愿望单" -SimpleMatch -Quiet)) {
    Write-Host "[警告] expand_rule_v8.py 里没有这次的电厂修复 —— 代码可能是旧的。" -ForegroundColor Yellow
    Write-Host "       先在 Pi 上跑：~/bin/deploy-zhanguo" -ForegroundColor Yellow
    Read-Host "回车继续（或 Ctrl-C 退出）"
} else {
    Write-Host "[检查] v8 代码是最新的（含电厂修复）" -ForegroundColor Green
}
Write-Host ("[检查] bc.py 支持 v8 老师：" + `
    $(if (Select-String -Path (Join-Path $Root "rl\bc.py") -Pattern '"v8"' -SimpleMatch -Quiet) { "是" } else { "否 —— 需要重新同步" }))

# 已经有训练在跑就别叠（会抢同一份检查点）
$running = @(Get-Process python -ErrorAction SilentlyContinue |
             Where-Object { try { $_.Path -eq $Py } catch { $false } })
if ($running) {
    Write-Host "[警告] 已有 zhanguo-rl 的 python 在跑（PID $($running.Id -join ',')）" -ForegroundColor Yellow
    Write-Host "       同时跑两个训练会互相抢 rl\runs\bc\ 下的检查点，建议先停掉。" -ForegroundColor Yellow
    Read-Host "回车继续（或 Ctrl-C 退出）"
}

# nssm 服务在的话提一句（非管理员会话看不到它，提权后应该看得到）
try {
    $s = Get-Service -Name $Svc -ErrorAction Stop
    Write-Host "[服务] $Svc 当前状态：$($s.Status)" -ForegroundColor Gray
} catch {
    Write-Host "[服务] 没读到 $Svc 服务（没装、或名字不同），不影响本次训练" -ForegroundColor Gray
}

# ---------------------------------------------------------------- 开跑
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "rl\runs\bc"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "train-$stamp.log"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# 注意：别叫 $args —— 那是 PowerShell 的保留自动变量，赋值会出怪事。
$bcArgs = @(
    "-m", "rl.bc",
    "--teacher", "v8",
    "--episodes", "40",
    "--dagger-from", "20",
    "--turns", "70",
    "--ckpt-every", "4",
    "--out", "rl\runs\bc\v8_70.pt"
)

Write-Host ""
Write-Host "[启动] $Py" -ForegroundColor Green
Write-Host "       $($bcArgs -join ' ')" -ForegroundColor Green
Write-Host "[日志] $Log" -ForegroundColor Green
Write-Host ""

$t0 = Get-Date
& $Py @bcArgs 2>&1 | Tee-Object -FilePath $Log
$code = $LASTEXITCODE
$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host " 训练结束（成功）· 用时 $mins 分钟" -ForegroundColor Green
    Write-Host " 权重：$Root\rl\runs\bc\v8_70.pt" -ForegroundColor Green
} else {
    Write-Host " 训练退出，exit code = $code · 用时 $mins 分钟" -ForegroundColor Red
    Write-Host " 看日志最后几行找原因：$Log" -ForegroundColor Yellow
}
Write-Host " 日志：$Log" -ForegroundColor Gray
Write-Host "==============================================================" -ForegroundColor Cyan
Read-Host "回车退出"
