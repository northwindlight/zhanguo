#!/usr/bin/env bash
# 在阿里云 ECS（唯一训练机）上跑任何 RL 任务的**唯一入口**。
#
#   ./rl/run_ecs.sh bc --teacher v9 --episodes 40 --dagger-from 20 --turns 70 \
#       --ckpt-every 4 --out rl/runs/bc/v9_70.pt
#   ./rl/run_ecs.sh train --resume rl/runs/bc/v9_70.pt ...
#   ./rl/run_ecs.sh status          # 看当前任务
#   ./rl/run_ecs.sh logs            # 跟日志
#
# 它管三件事，全是踩过的坑：
#
# 1. **线程全部钉死为 1。** ECS 是 1 物理核 + SMT；SMT 那个逻辑核对向量计算
#    收益为零（FP 单元单线程已吃满），实测 torch 开到 2 线程反而**慢 3.4×**。
#    这里在**进程启动前**设好 OMP/MKL/OpenBLAS 环境变量（import torch 之后再设就晚了），
#    并显式传 `--threads 1`（脚本自己的默认值 0="自动=物理核"在 ECS 上也算出 1，
#    这里传死是第二道保险）。
#
# 2. **必须 tmux。** Windows 训练机上 ssh 会话一断进程就被带走，丢过一次；
#    这里是同一个道理，别图快用 `ssh host 'python ...'` 裸起。
#
# 3. **日志与退出码落盘**，这样 `status`/`logs` 不用猜，ssh 断了也还在。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

SESSION="${ZHANGUO_TMUX:-rl}"
LOGDIR="$HERE/rl/runs"
mkdir -p "$LOGDIR"

case "${1:-}" in
  status)
    tmux ls 2>/dev/null | grep -E "^${SESSION}:" || echo "没有正在跑的任务（tmux 会话 ${SESSION} 不存在）"
    echo "--- 最近日志 ---"
    for f in "$LOGDIR"/*.log; do
      [ -e "$f" ] || continue
      printf '%s: ' "$(basename "$f")"
      grep -c . "$f" 2>/dev/null | tr -d '\n'; echo " 行"
    done
    exit 0
    ;;
  logs)
    exec tmux attach -t "$SESSION"
    ;;
  kill)
    tmux kill-session -t "$SESSION" && echo "已结束 ${SESSION}"
    exit 0
    ;;
esac

[ $# -ge 1 ] || { sed -n '2,12p' "$0"; exit 2; }

JOB="$1"; shift
LOG="$LOGDIR/${JOB}_$(date +%m%d_%H%M).log"
PY="${ZHANGUO_PY:-$HOME/.venv/bin/python}"

# 进程级线程环境：必须在 python 起来**之前**设（import torch 之后再设子进程也晚了）
THREAD_ENV=(
  OMP_NUM_THREADS=1
  MKL_NUM_THREADS=1
  OPENBLAS_NUM_THREADS=1
  NUMEXPR_NUM_THREADS=1
  VECLIB_MAXIMUM_THREADS=1
  PYTHONIOENCODING=utf-8
  PYTHONUNBUFFERED=1
)

CMD=(env "${THREAD_ENV[@]}" "$PY" -u -m "rl.${JOB}" --threads 1 "$@")

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[错误] tmux 会话 ${SESSION} 已在跑 —— 别同时跑两个训练。" >&2
  echo "       先：./rl/run_ecs.sh status  /  ./rl/run_ecs.sh kill" >&2
  exit 1
fi

printf -v QUOTED '%q ' "${CMD[@]}"
# tmux 用 `sh -c` 起命令，而 `PIPESTATUS`/`tee` 这套是 bash 的 —— 显式套一层 bash，
# 免得出问题时日志静默为空（比报错更难查）。
INNER="$QUOTED 2>&1 | tee '$LOG'; echo \"[退出码 \${PIPESTATUS[0]}]\""
printf -v INNER_Q '%q' "$INNER"
tmux new-session -d -s "$SESSION" "bash -c $INNER_Q"

echo "已启动 tmux 会话 ${SESSION}：rl.${JOB} $*"
echo "日志：$LOG"
echo "查看：./rl/run_ecs.sh logs     结束：./rl/run_ecs.sh kill"
