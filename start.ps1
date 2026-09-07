# 战国·多国 AI 对战 · 一键启动（Windows PowerShell）
# 需要机器上已有 Python 3.10+（py 启动器或 python 都行）；依赖装进程序目录 .venv，不污染系统。
# 用法：
#   .\start.ps1                    # 读档续局（无档则新开）
#   .\start.ps1 --new --turns 10   # 开新局跑 10 回合；Ctrl-C 存档退出
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 国内用户：pip 默认走清华镜像（离海外的用户可改）
$PipMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"

# ---- 找 Python ----
$py = $null
foreach ($c in @("python", "py")) {
    try { & $c --version *> $null; if ($LASTEXITCODE -eq 0) { $py = $c; break } } catch { }
}
if (-not $py) { Write-Host "[错误] 未找到 Python（需 3.10+），请先安装" -ForegroundColor Red; Read-Host "回车退出"; exit 1 }
if ($py -eq "py") { $py = @("py", "-3") }

# ---- 配置：没有 mp_config.json 就从模板生成并提示填写 ----
if (-not (Test-Path "mp_config.json")) {
    Copy-Item mp_config.example.json mp_config.json
    Write-Host "[初始化] 已从模板生成 mp_config.json —— 请先填好每国的 base_url / api_key / model 再运行。" -ForegroundColor Yellow
    Read-Host "回车退出"
    exit 1
}

# ---- 虚拟环境：默认装在程序目录（.venv） ----
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "[初始化] 创建虚拟环境 .venv（仅首次）…"
    & $py -m venv .venv
}

# ---- 依赖（装进 .venv，缺才装） ----
& cmd.exe /c "`"$venvPy`" -c ""import openai"" >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[初始化] 安装依赖（清华镜像，版本由 requirements.txt 控制）…"
    & $venvPy -m pip install -i $PipMirror --timeout 60 -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[提示] 清华镜像拉取失败（网络/分流原因），改用官方源重试…"
        & $venvPy -m pip install --timeout 60 -r requirements.txt
    }
}

Write-Host "[启动] 战国·多国 AI 对战（看海模式：终端实时流 + mp_journal.md）…" -ForegroundColor Green
& $venvPy mp_run.py @args
