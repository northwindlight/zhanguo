#!/usr/bin/env bash
# 在**一台多核机**上起 N 个**独立 worker**，共享**一个联赛库**。
#
#   ./rl/run_par.sh up <N> -- <传给 rl.train 的参数>
#   ./rl/run_par.sh status [N]
#   ./rl/run_par.sh kill   [N]
#   ./rl/run_par.sh logs   <i>
#
# ★★ 为什么是"多个完整 worker"而不是"一堆纯评估进程"（用户 2026-09-25：
#    「为了以后并行，池子够大并行抽，**起多个独立进程筛**」）：
#      每个 worker 都是完整的 `rl.train`（抽签 → 对局 → 训练 → 冻快照）。
#      ⇒ 它**同时**把**池子**和**策略**推快 N 倍，而且成员来自**不同血脉** = 真分化
#        （单进程的快照只是同一条血脉的不同时刻）。
#
# ★★ 那个共享库（`--league-db`）就是为这件事做的：
#      WAL + **原子自增**（`games=games+1`）⇒ N 个进程同时记账**一局都不丢**；
#      `refresh()` 保证判据读的是库里的真值；快照 mid 带 worker 标识 ⇒ 不会互相覆盖。
#
# ★ 用法示例（12-20 图、t-max 150、每 3 iter 冻一份快照）：
#     ./rl/run_par.sh up 32 -- --episodes 2 --t-max 150 --max-steps 20000 \
#         --size-min 12 --size-max 20 --pool 5 --halls-known \
#         --league-db rl/runs/league.db --league-mains 2 \
#         --league-snapshot-every 3 --restart-after 5 --ckpt-every 2
#
# ★★ 纪律（都在 PLAN §12.2 第 8 条里，这里再点名一次）：
#   ① 每个 worker 的 `--out` **自动**分开（`rl/runs/par/wNN.pt`）—— 别让两个进程
#      往同一个 .pt 里写（原子写只保证"不写坏"，不保证"不互相覆盖"）。
#   ② `--league-cache` 按内存调小：**每 worker 每份缓存 ≈ 5.5MB**，
#      N=32、cache=24 ⇒ 32×130MB ≈ 4.2GB。内存充足就不用管。
#   ③ ★★ **线程数要按机器定，别写死**（`THREADS_PER_WORKER`，缺省 1）：
#      时间构成里 **update 约 90%**，而 update 全是 matmul/注意力 ⇒ **多线程能铺开**；
#      collect（Python 沙盒 + B=1 前向）基本单线程。
#      ⇒ 在**真多核**机器上，"少数 worker × 每 worker 多线程" 通常优于 "N 个单线程 worker"。
#      ⚠ 但 `--threads 1` 在 **ECS 上是对的**（1 物理核 + SMT，开 2 线程实测慢 3.4×）——
#        **那条结论不能搬**。真多核机器上先实测 scaling 再定。
#      ★ 经验摆法：`N×T ≈ 物理核数`，且 `N` 别太小（N = 血脉数 = 池子的多样性来源）。
#   ④ **别给两个 worker 同一份 `--resume`**：它们会从同一条血脉出发
#      （要分化就该各自不同 —— 缺省让每个 worker 从**公共起点**出发即可，
#       之后的随机抽签会把它们推开）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

RUNDIR="${ZHANGUO_PAR_DIR:-$HERE/rl/runs/par}"
LOGDIR="$HERE/rl/runs"
PY="${ZHANGUO_PY:-$HOME/.venv/bin/python}"
# ★ 会话前缀（可改）⇒ **能起两组、各给不同参数**（例如一组 `--device cpu`、一组 `--device cuda`）
SESSION_PREFIX="${ZHANGUO_PAR_PREFIX:-w}"
# ★ 每个 worker 用几个 torch 线程（见上面 ③）。缺省 1 = 老行为（ECS 上是对的）。
THREADS_PER_WORKER="${THREADS_PER_WORKER:-1}"

# 进程级线程环境：必须在 python 起来**之前**设（import torch 之后再设就晚了）
THREAD_ENV=(
  OMP_NUM_THREADS="$THREADS_PER_WORKER" MKL_NUM_THREADS="$THREADS_PER_WORKER"
  OPENBLAS_NUM_THREADS="$THREADS_PER_WORKER" NUMEXPR_NUM_THREADS="$THREADS_PER_WORKER"
  VECLIB_MAXIMUM_THREADS="$THREADS_PER_WORKER"
  PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
)

usage() { sed -n '2,12p' "$0"; exit 2; }

cmd="${1:-}"; shift || usage

case "$cmd" in
  up)
    N="${1:-}"; shift || usage
    [ "${1:-}" = "--" ] && shift
    [[ "$N" =~ ^[0-9]+$ ]] || { echo "第一个参数是 worker 数（正整数）"; usage; }
    # ★ 共享库是这件事的**前提**，缺了它每个 worker 各训各的 = 白起
    case " $* " in
      *" --league-db "*) ;;
      *) echo "[错误] 必须显式给 `--league-db`（共享联赛库是并行的前提）" >&2; exit 2 ;;
    esac
    mkdir -p "$RUNDIR"
    LOG="$LOGDIR/par_$(date +%m%d_%H%M).log"
    echo "起 $N 个 worker ⇒ 库见 --league-db；各自存档 $RUNDIR/wNN.pt" | tee -a "$LOG"
    for i in $(seq 1 "$N"); do
      s="${SESSION_PREFIX}${i}"
      if tmux has-session -t "$s" 2>/dev/null; then
        echo "  跳过 $s（已在跑）" | tee -a "$LOG"; continue
      fi
      CMD=(env "${THREAD_ENV[@]}" "$PY" -u -m rl.train --threads "$THREADS_PER_WORKER"
           --out "$RUNDIR/w$(printf %02d "$i").pt" "$@")
      printf -v QUOTED '%q ' "${CMD[@]}"
      INNER="$QUOTED 2>&1 | tee -a '$LOGDIR/${s}_$(date +%m%d_%H%M).log'; echo \"[$s 退出码 \${PIPESTATUS[0]}]\""
      printf -v INNER_Q '%q' "$INNER"
      tmux new-session -d -s "$s" "bash -c $INNER_Q"
      echo "  ✔ $s" | tee -a "$LOG"
    done
    echo "查看：$0 status $N    单看：$0 logs <i>    结束：$0 kill $N"
    ;;

  status)
    N="${1:-0}"
    [ "$N" = "0" ] && N="$(tmux ls 2>/dev/null | grep -cE "^${SESSION_PREFIX}[0-9]+:" || true)"
    alive=0
    for i in $(seq 1 "${N:-0}"); do
      s="${SESSION_PREFIX}${i}"
      if tmux has-session -t "$s" 2>/dev/null; then alive=$((alive + 1)); fi
    done
    echo "worker 存活 $alive / $N"
    echo "--- 各档存档 ---"
    ls -la "$RUNDIR" 2>/dev/null | tail -n +2 | awk '{printf "  %s  %s\n", $5, $9}' | tail -5
    ;;

  logs)
    i="${1:-1}"; exec tmux attach -t "${SESSION_PREFIX}${i}"
    ;;

  kill)
    N="${1:-0}"
    [ "$N" = "0" ] && N="$(tmux ls 2>/dev/null | grep -cE "^${SESSION_PREFIX}[0-9]+:" || true)"
    for i in $(seq 1 "${N:-0}"); do
      s="${SESSION_PREFIX}${i}"
      tmux kill-session -t "$s" 2>/dev/null && echo "已结束 $s" || true
    done
    ;;

  *) usage ;;
esac