"""DAgger 回合边界审计：标签到底有没有对齐、end_turn 有没有发错。

复刻 rl/bc.py 的 DAgger 分支，但把每一步的 (回合, 已走步数 k, 老师序列长度,
标签种类, 是否未匹配, 是否结束回合) 全打出来，专门盯两件事：

  ① k < len(seq) 时标签**必须**是老师的动作 —— 出现 end_turn 就是 bug
  ② 一个回合里被贴上 end_turn 的步数，不该超过"老师回合长度之后"的那部分

用法：
  .venv/bin/python -m experiments.dagger_audit            # dagger_every=1
  .venv/bin/python -m experiments.dagger_audit 2          # 复现 dagger_every=2 的 bug
"""
from __future__ import annotations

import copy
import random
import sys

from rl.bc import get_teacher, match, to_action
from rl.env import ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import act as _act
from rl.env import KINDS

EVERY = int(sys.argv[1]) if len(sys.argv) > 1 else 1
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
SEED = 0

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=512)
obs = env.reset(SEED)
model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                  sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                  n_tiles=16 ** 2)
teacher_fn = get_teacher("v9", TURNS)

rng = random.Random(SEED)
last_turn = -1
seq: list = []

print(f"dagger_every={EVERY}  turns={TURNS}  seed={SEED}")
print(f"{'回合':>4} {'k':>3} {'len(seq)':>8}  {'标签':<28} {'结束回合':>8}  备注")
print("-" * 86)

spurious = 0      # k < len(seq) 却发了 end_turn  ← 真 bug 的签名
miss = 0
steps = 0
end_labeled: dict[int, int] = {}      # 每回合被贴 end_turn 的步数
teacher_len: dict[int, int] = {}
while True:
    t = env.world.turn
    if t != last_turn:
        last_turn = t
        seq = []
        if t % max(1, EVERY) == 0:
            w2 = copy.deepcopy(env.world)
            teacher_fn(w2, env.agent, rng, max_actions=10 ** 9,
                       on_action=lambda tool, args: seq.append((tool, args)))
        teacher_len[t] = len(seq)

    k = env.turn_actions
    if k < len(seq):
        i = match(obs.cand["actions"], to_action(*seq[k]))
        tag = f"老师 {seq[k][0]} {str(seq[k][1])[:16]}"
        is_end_label = False
    else:
        i = next((j for j, a in enumerate(obs.cand["actions"])
                  if a.kind == "end_turn"), None)
        tag = "end_turn（k 超出老师长度）"
        is_end_label = True

    note = ""
    if i is None:
        miss += 1
        note = "★未匹配"
    if is_end_label and k < len(seq):
        spurious += 1
        note += " ★★不该发 end_turn"
    if is_end_label:
        end_labeled[t] = end_labeled.get(t, 0) + 1

    idx, _lp, _v = _act(model, obs)
    chosen = obs.cand["actions"][idx]
    obs, _r, done, info = env.step(chosen)
    steps += 1
    print(f"{t:>4} {k:>3} {len(seq):>8}  {tag:<28} {str(info['ended']):>8}  "
          f"学生选={chosen.kind} {note}")
    if done:
        break

print("-" * 86)
print(f"总步数 {steps}  未匹配 {miss}  不该发的 end_turn {spurious}")
print("\n每回合：老师回合长度 vs 被贴 end_turn 的步数")
for t in sorted(teacher_len):
    n = teacher_len[t]
    e = end_labeled.get(t, 0)
    flag = ""
    if n == 0:
        flag = "  ← 老师没被问（seq 空）→ 整回合都在教 end_turn ★BUG"
    print(f"  回合 {t}: 老师 {n:>3} 步   贴了 end_turn {e:>3} 步{flag}")
