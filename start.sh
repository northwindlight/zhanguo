#!/usr/bin/env bash
# 战国·多国 AI 对战 · 一键启动（Linux / macOS）
# 首次运行会在程序目录自动创建 .venv 虚拟环境并安装依赖，不污染系统 Python。
# 用法：
#   ./start.sh               # 读档续局（无档则新开）
#   ./start.sh --new --turns 10   # 开新局跑 10 回合；Ctrl-C 存档退出
#   其余参数原样透传给 mp_run.py（如 --config 自配文件 --save 自定存档）
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "[错误] 未找到 python3"; exit 1; }

# 国内用户：pip 默认走清华镜像（离海外的用户可改成官方源）
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

# ---- 配置：没有 mp_config.json 就从模板生成并提示填写 ----
if [ ! -f "mp_config.json" ]; then
    cp mp_config.example.json mp_config.json
    echo "[初始化] 已从模板生成 mp_config.json —— 请先填好每国的 base_url / api_key / model 再运行。"
    exit 1
fi

# ---- 虚拟环境：默认装在程序目录（.venv） ----
if [ ! -x ".venv/bin/python" ]; then
    echo "[初始化] 创建虚拟环境 .venv（仅首次）…"
    python3 -m venv .venv
fi
PY=.venv/bin/python

# ---- 依赖（装进 .venv，缺才装） ----
if ! "$PY" -c "import openai" >/dev/null 2>&1; then
    echo "[初始化] 安装依赖（清华镜像，版本由 requirements.txt 控制）…"
    if ! "$PY" -m pip install -i "$PIP_MIRROR" --timeout 60 -r requirements.txt; then
        echo "[提示] 清华镜像拉取失败（网络/分流原因），改用官方源重试…"
        "$PY" -m pip install --timeout 60 -r requirements.txt
    fi
fi

echo "[启动] 战国·多国 AI 对战（看海模式：终端实时流 + mp_journal.md）…"
exec "$PY" mp_run.py "$@"
