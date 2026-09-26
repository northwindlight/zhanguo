#!/bin/bash
# 等两臂**任意一臂**落一个新 iter 行，把新行打出来就**退出**（最多等 MAX 秒）。
#
# ★ 为什么不用 `tail -F` 常驻：那东西**永远不退出** ⇒ 只在"有事件"时才响，
#   而"没事件"和"管道碎了"长得一模一样（本次实测：两次常驻监视，一次都没送到，
#   而远端日志明明在往前走）。⇒ 换成"**有界的等待循环**"：无论发生什么都会退出，
#   退出就是一次通知；"静默超时"也显式打一行出来，不会伪装成安静。
# ★★ 工作目录**从环境变量取**（缺省才是 GPU 机那个路径）——
#   2026-09-26 踩的：这个脚本里写死 `cd /root/zhanguo`，而租的 GPU 机一被回收、
#   换到 ECS 上跑（仓库在 `~/zhanguo`）就当场 `Permission denied`。
#   跨机器的脚本里**一个绝对路径都不该写死**。
cd "${ZHANGUO_DIR:-/root/zhanguo}" || exit 1
MAX=${1:-1500}
# ★ 日志文件名**自动找最新的**（`run_par.sh` 每次起炉按时间戳新建一个）——
#   写死名字的结果：重起一次之后这个脚本还在盯**已经死掉的那个文件**，
#   于是"没事件"其实是"盯错文件了"（我 2026-09-25 就这么白等过一次）。
F1=${2:-$(ls -t rl/runs/mem1_*.log 2>/dev/null | head -1)}
F2=${3:-$(ls -t rl/runs/base1_*.log 2>/dev/null | head -1)}
[ -n "$F1" ] && [ -n "$F2" ] || { echo "找不到日志文件"; exit 1; }
echo "盯：$F1 / $F2"

# ★★ 数 iter 行**只能用这个**：`grep -c` 在"零匹配"时**既打印 `0`、又以退出码 1 结束**
#   ⇒ 写成 `grep -c … || echo 0` 会得到**两行** `0`（`"0\n0"`），
#   算术比较拿到它当场报 `integer expression expected`，而且**从此永远判不出"有新 iter"**
#   ⇒ 只能在超时后报"静默"（我 2026-09-25 就这么踩了一次，还先怀疑了炉子）。
#   文件不存在时 `grep -c` 什么都不打印 ⇒ `${n:-0}` 兜住。
count_iter() { local n; n=$(grep -cE '^\[' "$1" 2>/dev/null); echo "${n:-0}"; }
n1=$(count_iter "$F1")
n2=$(count_iter "$F2")
echo "起等：mem1 已有 $n1 个 iter，base1 已有 $n2 个（最多等 ${MAX}s）"
for _ in $(seq 1 $((MAX / 20))); do
  sleep 20
  a=$(count_iter "$F1")
  b=$(count_iter "$F2")
  if [ "$a" -gt "$n1" ] || [ "$b" -gt "$n2" ]; then
    [ "$a" -gt "$n1" ] && { echo "== mem1 =="; grep -E '^\[' "$F1" | tail -n $((a - n1)); }
    [ "$b" -gt "$n2" ] && { echo "== base1 =="; grep -E '^\[' "$F2" | tail -n $((b - n2)); }
    exit 0
  fi
done
echo "== 静默 ${MAX}s：两臂都没有新 iter —— 三态都报出来 =="
echo "-- 进程（空 = 都退出了）--"; pgrep -a -f '[p]ython -u -m rl.train' | cut -c1-90
echo "-- mem1 尾 --";    tail -c 200 "$F1"
echo "-- base1 尾 --";   tail -c 200 "$F2"
echo "-- 日志文件 --";   ls -la "$F1" "$F2" | awk '{print $5, $9}'