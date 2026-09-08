#!/usr/bin/env bash
# 战国 · 终局结算（Linux / macOS）
# 解析存档按 GDP/军队/领土/固定资产打分，然后把各国 AI 请进结算厅聊 5 轮。
# 用法：
#   ./settle.sh                  # 打分 + 结算厅（寄语：终端逐国输入，或 --remarks 指定）
#   ./settle.sh --no-chat        # 只打分，不进聊天室
#   ./settle.sh --table          # 只看表：打印成绩单即退出，不进聊天室/不写报告
#   ./settle.sh --remarks 结算寄语.json --rounds 5   # 其余参数原样透传给 settlement.py
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "[错误] 未找到 python3"; exit 1; }

[ -f "mp_save.json" ] || { echo "[错误] 当前目录没有 mp_save.json（先用 start.sh 跑一局）"; exit 1; }

# ---- 虚拟环境：复用 start.sh 建的 .venv，没有就用系统 python3 ----
if [ -x ".venv/bin/python" ]; then
    PY=.venv/bin/python
else
    PY=python3
fi
if ! "$PY" -c "import openai" >/dev/null 2>&1; then
    echo "[提示] 缺 openai 库：进聊天室需要它（只打分可用 --no-chat）。"
    echo "       先跑一次 ./start.sh 会自动装好依赖。"
fi

echo "[启动] 战国 · 终局结算（打分 + 结算厅）…"
exec "$PY" settlement.py "$@"
