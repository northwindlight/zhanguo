# -*- coding: utf-8 -*-
"""老师 v10 在 200 回合里**逐段**都在干什么？—— 回答"BC 到底能教到第几回合"。

## 为什么要问（2026-09-14）

与教师的差距账：**70 回合（BC 教过）学生是 87%、200 回合（没教过）只有 61%**
⇒ 差距主要不是"RL 没学好"，是"那 130 回合根本没教"。

用户口径：**200 回合是战争/外交的深水区，纯滚雪球的老师教不了**。
但 `mp_journal.md` 显示结构变更是**第 158 回合（匈奴登场）**，
在那之前游戏是同一套玩法（经济 + 打野人扩张）—— **而老师会做这个**。

⇒ **若老师在 70 回合之后仍在扩张/建造/征兵，那 BC/DAgger 教到 ~120-150 回合
就能把 87% 那段吃回来一大截**（比调 lr 值钱得多）。
⇒ 若老师 70 回合后基本躺平（只 end_turn / 只市场），那 87% 就是天花板。

## 量什么

把老师的 `on_action` 回调接上，**逐回合**记动作种类；
按 20 回合一段汇总，报每段的 build/recruit/attack/move/市场/end_turn 次数。

用法：python experiments/probe_teacher_profile.py [局数] [回合]
"""
import sys
import collections

from rl.env import ZhanguoEnv
from rl.bc import get_teacher

EPS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
BUCKET = 20

teacher = get_teacher("v10", turns=TURNS)
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
print(f"老师 v10，{EPS} 局 × {TURNS} 回合，逐 {BUCKET} 回合汇总动作种类\n")

agg = collections.defaultdict(collections.Counter)   # bucket -> kind -> 次数
last_spend = []
for ep in range(EPS):
    env.reset(900_000 + ep)
    w = env.world
    rng = __import__("random").Random(0xB4BE)
    turn = 0
    for turn in range(TURNS):
        seq = []
        teacher(w, env.agent, rng, max_actions=10 ** 9,
                on_action=lambda tool, args: seq.append(tool))
        b = (turn // BUCKET) * BUCKET
        for tool in seq:
            k = ("市场" if tool in ("buy", "sell") else
                 "扩张/进攻" if tool in ("attack",) else
                 "移动" if tool == "move" else
                 "建造" if tool == "build" else
                 "征兵" if tool == "recruit" else tool)
            agg[b][k] += 1
        agg[b]["__回合"] += 1
        w.resolve_turn()
        if turn + 1 < TURNS:
            w.begin_turn()
    last_spend.append(w.spend_total(env.agent))

print(f"{'回合段':<10}{'平均动作数/回合':>14}   明细")
for b in sorted(agg):
    c = agg[b]
    n = max(1, c["__回合"])
    tot = sum(v for k, v in c.items() if not k.startswith("__"))
    det = "  ".join(f"{k} {v / n:.1f}" for k, v in c.most_common()
                    if not k.startswith("__"))
    print(f"{b:>3}-{b + BUCKET - 1:<6}{tot / n:>14.1f}   {det}")

print(f"\n老师终局消费（{EPS} 局均值）：{sum(last_spend) / len(last_spend):,.0f}")
print("\n判据：若 70 回合之后「扩张/进攻」「建造」「征兵」仍有可观次数")
print("      ⇒ **BC 能教到更晚**（那 130 回合不是「没东西可教」）；")
print("      若只剩「市场」和「end_turn」⇒ 87% 就是天花板。")
