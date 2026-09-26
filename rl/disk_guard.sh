#!/bin/bash
# 磁盘闸：**免费 GPU 机的盘是硬顶**（30G，装完 torch 只剩 ~12G），
# 而用户 2026-09-26 定的池子口径是「**学一个增一个**」「**只增不删**」——
# 每份冻结快照 **6.03 MB**（实测），于是**磁盘寿命 = 快照速率**。
#
# ★★ 为什么必须"停炉"而不是"删旧快照"：
#   · 快照是**只增不删**的池子成员，库里有行、文件就是它的权重 ——
#     删文件 ⇒ 哪天抽到它 `_load_member` 直接抛（**不是**优雅跳过）。
#   · 所以磁盘满了没有"就地缓解"的办法，只能停。
#   · 停的代价很小：`zhanguo-gpu-pull.timer` 每 2 分钟把产物镜像回 Pi ⇒ **最多丢 2 分钟**。
#   反过来说，**不停**的代价很大：`torch.save` 写一半失败、SQLite 正在 WAL 上写 ——
#   这台机器随时被回收，库写坏了**没有第二份**。
#
# ★ 三态都要看得出来（用户定的规矩：「等待循环必须分没开始/在跑/已结束三态」）：
#   起闸打一行 → 每 HEARTBEAT 秒打一行心跳（带剩余空间）→ 触发或异常时打一行并退出。
#   心跳不是啰嗦：没有它，"闸没起来"和"闸在跑且一切正常"在日志上**长得一模一样**。
#
# 用法（在**训练机上**跑）：
#   ./rl/disk_guard.sh <阈值MB> <挂载点或目录> <tmux会话...>
#   ./rl/disk_guard.sh 2500 / rl                      # 剩 <2.5G 就把 rl 会话停掉
#
# ★★ 故意破坏一次（用户规矩：每个自动闸门都要确认它会响）：
#   tmux new-session -d -s dummy sleep 600
#   ./rl/disk_guard.sh 999999999 / dummy              # 阈值荒谬 ⇒ 立刻触发
#   ⇒ 应当看到「★ 磁盘闸触发」+「已停 dummy」，且 `tmux ls` 里 dummy 没了。
set -uo pipefail

THRESH_MB="${1:-2500}"
WATCH="${2:-/}"
shift 2 2>/dev/null || { echo "用法: $0 <阈值MB> <挂载点> <tmux会话...>"; exit 2; }
[ $# -ge 1 ] || { echo "[错误] 没给要停的 tmux 会话 —— 闸会响但停不掉任何东西"; exit 2; }
HEARTBEAT="${DISK_GUARD_HEARTBEAT:-1800}"   # 秒；★可调是为了测闸（测的时候调成 5）
LOG="${DISK_GUARD_LOG:-$HOME/disk_guard.log}"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

free_mb() { df -Pm "$WATCH" 2>/dev/null | awk 'NR==2{print $4}'; }

f=$(free_mb)
if [ -z "$f" ]; then
  say "[错误] 读不到 $WATCH 的剩余空间（df 没输出）—— 闸**没起来**，别当成'一切正常'"
  exit 3
fi
say "起闸：$WATCH 剩 ${f}MB，阈值 ${THRESH_MB}MB，盯 [$*]，心跳 ${HEARTBEAT}s"

last=$SECONDS
while :; do
  sleep 30
  f=$(free_mb)
  # ★ df 读不到 ⇒ 当场报错退出，**不要静默继续**：那等于闸门悄悄失效
  if [ -z "$f" ]; then say "[错误] df 读不到剩余空间 ⇒ 闸退出（训练没人看着了）"; exit 3; fi

  if [ "$f" -lt "$THRESH_MB" ]; then
    say "★★ 磁盘闸触发：${WATCH} 只剩 ${f}MB < ${THRESH_MB}MB ⇒ 停炉保库"
    for s in $*; do
      if tmux has-session -t "$s" 2>/dev/null; then
        tmux kill-session -t "$s" && say "   已停 tmux 会话 $s"
      else
        say "   会话 $s 本来就不在（可能已自己退出）"
      fi
    done
    say "停完剩余 ${f}MB（不会再长）。产物已在 Pi 的 rl/runs/gpu_pull/ 下。"
    exit 0
  fi

  if [ $((SECONDS - last)) -ge "$HEARTBEAT" ]; then
    say "心跳：剩 ${f}MB / 阈值 ${THRESH_MB}MB"
    last=$SECONDS
  fi
done
