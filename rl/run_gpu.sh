#!/usr/bin/env bash
# GPU 机房上跑任务的入口（**按租的那台机器改，换机房先改这里**）。
#
# 与 `run_ecs.sh` 的关系：**同一套纪律，两个不同的差异**——
#   1. 这台机器有 CUDA，所以默认 `--device cuda`（`run_ecs.sh` 是纯 CPU 机器）；
#   2. python 与仓库路径**不写死**：按候选表探测（见下面的 `PY`），
#      仓库就是本脚本所在的 checkout（`HERE`）——rsync 过去的，**不是 git 检出**。
#
# 历次机房（换机器时更新）：
#   2026-09-12  RTX 3080 Ti 12G / 72 vCPU 容器 / 251 GB   （已退）
#   2026-09-13  RTX 3060 12G / **10 物理核独占** / 23 GB / Ubuntu 22.04
#               `ssh -p 22194 linux@175.155.64.171`，python 用机房预装的
#               `/home/linux/anaconda3/envs/torch2.4_cuda12.1`（torch 2.4.1+cu121 现成，
#               免去 download.pytorch.org 拉 2.5G —— 那站在国内持续传输会卡死）。
#               ★这台**物理核是独占的**（Thread/core=1），不是上一台那种共享容器；
#               采样瓶颈（env rollout）上反而更稳。
#
# 照搬 `run_ecs.sh` 踩过的坑，一条都不省：
#   ① **必须在 import torch 之前**设好线程环境变量（之后设已经晚了）；
#   ② **必须 tmux** —— ssh 一断进程就被带走（Windows 训练机上丢过一次）；
#   ③ 日志与退出码落盘，`status`/`logs` 不用猜。
#
# 用法（**在这台机器上跑**，不是在 Pi 上）：
#   ./rl/run_gpu.sh bc --teacher v10 --episodes 1000 --block 5 ...
#   ./rl/run_gpu.sh status | logs | kill
#
# ★从 Pi 上起任务：`ssh -i ~/.ssh/id_ed25519_gpu -p 22194 linux@175.155.64.171 './rl/run_gpu.sh train ...'`
#   —— 但**先 rsync**（`git ls-files -z | rsync -a --from0 --files-from=- ...`，
#   注意 `git ls-files` 只带**已跟踪**文件，新文件不提交就同步不过去）。
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
    echo "--- GPU ---"
    nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || true
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

[ $# -ge 1 ] || { sed -n '2,20p' "$0"; exit 2; }

JOB="$1"; shift
LOG="$LOGDIR/${JOB}_$(date +%m%d_%H%M).log"
PY="${ZHANGUO_PY:-}"
if [ -z "$PY" ]; then
  # 按候选表探测，**别写死**（换机房就是换一行的事）：
  #   先找机房预装的 conda 环境，再找自建 venv。
  for c in \
    /home/linux/anaconda3/envs/torch2.4_cuda12.1/bin/python \
    /root/venv/bin/python \
    "$HOME/venv/bin/python" \
    "$HOME/.venv/bin/python"
  do
    [ -x "$c" ] && PY="$c" && break
  done
fi
[ -n "$PY" ] && [ -x "$PY" ] || { echo "[错误] 找不到可用的 python，用 ZHANGUO_PY=... 指定" >&2; exit 3; }

# ★**torch 的 CPU 线程开大反而慢**（小矩阵 + 同步开销）——实测口径见 `rl/hw.py`。
#   梯度在 GPU 上，CPU 侧只剩 collate/numpy/env，给 4 就够。
#   `--threads 0`（自动=物理核数）会在核多的机器上算出灾难值（72 核 → 36）。
#   换机器可用 `ZHANGUO_THREADS=` 覆盖。
THREADS="${ZHANGUO_THREADS:-4}"
THREAD_ENV=(
  OMP_NUM_THREADS=$THREADS
  MKL_NUM_THREADS=$THREADS
  OPENBLAS_NUM_THREADS=$THREADS
  NUMEXPR_NUM_THREADS=$THREADS
  PYTHONIOENCODING=utf-8
  PYTHONUNBUFFERED=1
)

# 显式指定 CUDA 设备可用性：装了 torch+cuda 却没卡时**早点炸**，别跑一半才发现。
"$PY" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 3)" || {
  echo "[错误] torch.cuda.is_available() == False —— 检查驱动/torch 版本" >&2; exit 3; }

CMD=(env "${THREAD_ENV[@]}" "$PY" -u -m "rl.${JOB}" --threads "$THREADS" --device cuda "$@")

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[错误] tmux 会话 ${SESSION} 已在跑 —— 别同时跑两个训练。" >&2
  echo "       先：./rl/run_gpu.sh status  /  ./rl/run_gpu.sh kill" >&2
  exit 1
fi

printf -v QUOTED '%q ' "${CMD[@]}"
# tmux 用 `sh -c` 起命令，而 `PIPESTATUS`/`tee` 是 bash 的 —— 显式套一层 bash。
INNER="$QUOTED 2>&1 | tee '$LOG'; echo \"[退出码 \${PIPESTATUS[0]}]\""
printf -v INNER_Q '%q' "$INNER"
tmux new-session -d -s "$SESSION" "bash -c $INNER_Q"

echo "已启动 tmux 会话 ${SESSION}：rl.${JOB} $*"
echo "日志：$LOG"
echo "查看：./rl/run_gpu.sh logs     结束：./rl/run_gpu.sh kill"
