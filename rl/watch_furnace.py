# -*- coding: utf-8 -*-
"""看一眼炉子 —— **只读**，在 Pi 上跑，自己 ssh 到 ECS 读最新那份训练日志。

    python rl/watch_furnace.py [主机]        # 默认 ecs.northwind.site

★ 为什么要有它（用户 2026-09-28：「以后靠自动化，不要手操作」）：
  「策略形成没有」这个判断，我手动搓过三遍同样的日志解析 —— 每遍都可能
  因为正则写歪而得出**不一样的数**（已经栽过一次：漏了键的引号，
  整列 e/K 全 0，看着像"熵是 0"，其实是没匹配上）。固定成脚本，
  每次读的是同一把尺子。

★★ 判据（用户定的顺序：**先等策略形成，再分化/联赛**）：
  · 主判据 = **`e/K`**（归一化熵，1.00 = 均匀策略）。它掉下来才叫"形成"；
  · 旁证 = 平均回合变短（用户：「到时候回合时长短，快速筛选」）；
  · 胜率**不是**这一炉的判据（两个主玩家谁赢谁输还早）。

★ 退出码：**0 = 一切照旧；10 = 有事**（进程没了 / 跑完了 / `e/K` 明显动了）。
  —— 让调用方（cron / 我）不必读完整输出才知道要不要吭声。

★ 只读：只 `ssh` + `grep`/`ls`，不写 ECS 上任何东西，**绝不碰 `mp_save.json`**
  （连 rsync 都排除它）。
"""
from __future__ import annotations

import re
import subprocess
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "ecs.northwind.site"
REPO = "/home/northwind/zhanguo"
SEG = 30          # 滚动窗口（iter 数）
DROP = 0.10       # `e/K` 从本段起点掉这么多 ⇒ 算"明显动了"


def sh(cmd: str) -> str:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", HOST, cmd],
                       capture_output=True, text=True, timeout=120)
    return r.stdout


def main() -> int:
    # ---- 在哪一份日志：最新的那个 ----
    log = sh(f"cd {REPO} && ls -t rl/runs/train_*.log 2>/dev/null | head -1").strip()
    if not log:
        print("★ 找不到任何 train_*.log —— 这炉子从没起来过？")
        return 10

    alive = "rl:" in sh("tmux ls 2>/dev/null")
    raw = sh(f"cd {REPO} && grep -E '^\\[ *[0-9]+\\] 局数' {log}")

    re_it = re.compile(r"^\[\s*(\d+)\] 局数(\d+) 胜场\{'甲': (\d+), '乙': (\d+).*?"
                       r"先手胜(\d+) 平均回合([\d.]+).*")
    re_e = re.compile(r"(L[01])@\w+': 'pg=([+-][\d.]+) vf=([+-][\d.]+) "
                      r"ent=([+-][\d.]+)\(logK=([\d.]+) e/K=([\d.]+)\)'")
    rows = {}
    for ln in raw.splitlines():
        m = re_it.match(ln)
        if not m:
            continue
        rows[int(m.group(1))] = (
            int(m.group(2)), int(m.group(3)), int(m.group(4)),
            int(m.group(5)), float(m.group(6)),
            {k: float(ek) for k, _pg, _vf, _ent, _lk, ek in re_e.findall(ln)})

    print(f"日志  {log}")
    print(f"进程  {'tmux 会话 rl 在' if alive else '★★ tmux 会话 rl 不在（死了/跑完了）'}")

    done = sh(f"cd {REPO} && grep -c '\\[完成\\] 退出码 0' {log}").strip()
    if done and done != "0":
        print("★★ 日志里有 `[完成] 退出码 0` —— 这一炉跑完了")

    if not rows:
        print("（还没有任何 iter 落盘）")
        return 0 if alive else 10

    its = sorted(rows)
    last = its[-1]
    g = sum(rows[i][0] for i in its)
    a = sum(rows[i][1] for i in its)
    b = sum(rows[i][2] for i in its)
    print(f"进度  iter {its[0]}..{last}（{len(its)} 个） · 共 {g} 局"
          f" · 平局 {(g - a - b) / g * 100:.1f}%")

    head, tail = its[:SEG], its[-SEG:]
    notable = False

    def mean(seg, k):
        v = [rows[i][5][k] for i in seg if k in rows[i][5]]
        return sum(v) / len(v) if v else float("nan")

    print(f"\n{'':<5}{'e/K 首' + str(len(head)):>10}{'e/K 末' + str(len(tail)):>12}"
          f"{'Δ':>9}{'回合首':>9}{'回合末':>9}")
    for k in ("L0", "L1"):
        e0, e1 = mean(head, k), mean(tail, k)
        t0 = sum(rows[i][4] for i in head) / len(head)
        t1 = sum(rows[i][4] for i in tail) / len(tail)
        d = e1 - e0
        flag = ""
        if d <= -DROP:
            flag = "  ← ★ 明显在掉"
            notable = True
        print(f"{k:<5}{e0:>10.3f}{e1:>12.3f}{d:>+9.3f}{t0:>9.1f}{t1:>9.1f}{flag}")
    print(f"       （1.00 = 均匀策略；掉下来才叫「形成」）")

    print(f"\n席位  甲 {a / g * 100:.1f}% 乙 {b / g * 100:.1f}%  "
          f"先手胜 {sum(rows[i][3] for i in its) / g * 100:.1f}%"
          f"   ← 修好 `first` 之后这两个数才第一次有意义")

    gates = sh(f"cd {REPO} && grep -c '先手已连续赢' {log}").strip() or "0"
    ecs = sh(f"cd {REPO} && grep -oE '^\\[退出码 [0-9]+\\]' {log} | sort | uniq -c").strip()
    print(f"\n闸门  先手连赢触发 {gates} 次   （528 局里响 2 次属噪声，见 PLAN §12.24）")
    if ecs:
        print("退出码\n" + "\n".join("      " + l.strip() for l in ecs.splitlines()))

    if not alive:
        notable = True
    print("\n==> " + ("★ 有事，看上面" if notable else "照旧（`e/K` 没明显动）"))
    return 10 if notable else 0


if __name__ == "__main__":
    sys.exit(main())
