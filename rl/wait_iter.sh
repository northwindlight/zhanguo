#!/bin/bash
# 等两臂**任意一臂**落一个新 iter 行，把新行打出来就**退出**（最多等 MAX 秒）。
#
# ★ 为什么不用 `tail -F` 常驻：那东西**永远不退出** ⇒ 只在"有事件"时才响，
#   而"没事件"和"管道碎了"长得一模一样（本次实测：两次常驻监视，一次都没送到，
#   而远端日志明明在往前走）。⇒ 换成"**有界的等待循环**"：无论发生什么都会退出，
#   退出就是一次通知；"静默超时"也显式打一行出来，不会伪装成安静。
cd /root/zhanguo || exit 1
MAX=${1:-1500}
F1=rl/runs/mem1_0925_2022.log
F2=rl/runs/base1_0925_2005.log
n1=$(grep -cE '^\[' "$F1" 2>/dev/null || echo 0)
n2=$(grep -cE '^\[' "$F2" 2>/dev/null || echo 0)
echo "起等：mem1 已有 $n1 个 iter，base1 已有 $n2 个（最多等 ${MAX}s）"
for _ in $(seq 1 $((MAX / 20))); do
  sleep 20
  a=$(grep -cE '^\[' "$F1" 2>/dev/null || echo 0)
  b=$(grep -cE '^\[' "$F2" 2>/dev/null || echo 0)
  if [ "$a" -gt "$n1" ] || [ "$b" -gt "$n2" ]; then
    [ "$a" -gt "$n1" ] && { echo "== mem1 =="; grep -E '^\[' "$F1" | tail -n $((a - n1)); }
    [ "$b" -gt "$n2" ] && { echo "== base1 =="; grep -E '^\[' "$F2" | tail -n $((b - n2)); }
    exit 0
  fi
done
echo "== 静默 ${MAX}s：两臂都没有新 iter —— 三态都报出来 =="
echo "-- 进程（空 = 都退出了）--"; pgrep -a -f 'python -u -m rl.train' | cut -c1-90
echo "-- mem1 尾 --";    tail -c 200 "$F1"
echo "-- base1 尾 --";   tail -c 200 "$F2"
echo "-- 日志文件 --";   ls -la "$F1" "$F2" | awk '{print $5, $9}'