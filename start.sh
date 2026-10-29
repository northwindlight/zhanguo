#!/usr/bin/env bash
# 战国·多国 AI 对战 · 一键启动（Linux / macOS）
#
# **默认跑 native 发行版**（mp_config.json；发行版由配置里的 "mods" 决定，
#  没写就是 mods.toml = native）。
#
# 用法：
#   ./start.sh                      # 读 mp_config.json 续局（无档则新开）；默认 native
#   ./start.sh --new --turns 10     # 开新局跑 10 周期；中断保留最近完整结算存档
#   ./start.sh --check              # 配置体检：逐国核对模型接入，打印结果后退出，不开局
#   ./start.sh --config <文件>      # 用别的配置（发行版由该文件的 "mods" 决定）
#   ./start.sh --offline            # 换到零依赖的 minigame 开发模式（不需要任何 API key）
#   其余参数原样透传给 mp_run.py（如 --save 自定存档）
#
# 依赖策略：LLM 接入为标准库原生实现（2026-10-24 起，无 openai SDK），
# v14联合规划需要NumPy/SciPy；minigame离线示例仍可零依赖运行。
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "[错误] 未找到 python3"; exit 1; }

# 国内用户：pip 默认走清华镜像（离海外的用户可改成官方源）
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

CONFIG_PATH=mp_config.json
MODE=run
OFFLINE=0
ARGV=()
while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_PATH="${2:-}"; shift 2 || true ;;
        --config=*) CONFIG_PATH="${1#--config=}"; shift ;;
        --check) MODE=check; shift ;;
        --offline) OFFLINE=1; shift ;;
        --help|-h) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) ARGV+=("$1"); shift ;;
    esac
done

# ---- 离线模式：直接用零依赖的 minigame 发行版 ----
if [ "$OFFLINE" = "1" ]; then
    CONFIG_PATH=mp_config.minigame.example.json
    if [ ! -f "$CONFIG_PATH" ]; then
        echo "[错误] 找不到 $CONFIG_PATH（仓库里应有 mp_config.minigame.example.json）"
        exit 1
    fi
fi

# ---- 配置：没有 mp_config.json 就从模板生成并提示填写 ----
if [ ! -f "$CONFIG_PATH" ] && [ "$CONFIG_PATH" = mp_config.json ]; then
    cp mp_config.example.json mp_config.json
    echo "[初始化] 已从模板生成 mp_config.json（默认发行版：native）"
    echo "          请先填好每国要接模型的 base_url / api_key / model"
    echo "          （provider 可选，只有 openai / anthropic 两种，缺省 openai）"
    echo "          填完先跑 ./start.sh --check 体检；不想接模型就先用 ./start.sh --offline。"
    exit 1
fi
if [ ! -f "$CONFIG_PATH" ]; then
    echo "[错误] 配置不存在：$CONFIG_PATH"
    exit 1
fi

# ---- 配置体检：只用标准库，不必等虚拟环境与依赖 ----
if [ "$MODE" = "check" ]; then
    echo "[体检] 检查配置：$CONFIG_PATH"
    CHECK_PY=python3
    if [ -x ".venv/bin/python" ]; then CHECK_PY=.venv/bin/python; fi
    exec "$CHECK_PY" mp_run.py --config "$CONFIG_PATH" --check-config
fi

# ---- 项目虚拟环境包含v14候选规则AI的联合规划依赖 ----
if [ "$OFFLINE" = "0" ]; then
    if [ ! -x ".venv/bin/python" ]; then
        echo "[初始化] 创建虚拟环境 .venv（仅首次）…"
        python3 -m venv .venv
    fi
    PY=.venv/bin/python
    echo "[初始化] 安装依赖（清华镜像，版本由 requirements.txt 控制）…"
    if ! "$PY" -m pip install -i "$PIP_MIRROR" --timeout 60 -r requirements.txt; then
        echo "[提示] 清华镜像拉取失败（网络/分流原因），改用官方源重试…"
        "$PY" -m pip install --timeout 60 -r requirements.txt
    fi
elif [ -x ".venv/bin/python" ]; then
    PY=.venv/bin/python
else
    # 纯标准库发行版：不必装任何东西
    PY=python3
fi

echo "[启动] 战国·多国 AI 对战（看海模式：终端实时流 + mp_journal.md）…"
exec "$PY" mp_run.py --config "$CONFIG_PATH" ${ARGV+"${ARGV[@]}"}
