# -*- coding: utf-8 -*-
"""子环节探针：**链式任务要同时盯每一环**。

J.1 的教训（写下来过）：只看终局地数会给**假阴性** —— 它分不出「没建兵营」和
「建了兵营、征了兵、就是不出兵」。所以这个探针逐步数整条链：

    经济开局 → 建兵营（第几回合首建 / 几张图建成）→ 征兵 → move/attack → 终局领地

用法：python experiments/probe_chain.py <ckpt> [episodes] [turns]

采样策略（不是贪心）—— 那是 PPO 里的实际行为，也是"学生一局到底做了什么"的口径。
每局换一张图（`map_seed` 变），所以 `episodes` 张图独立。
"""
import sys
import collections
import torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 70

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)                      # `_terrain` 要等第一次 reset 才建出来
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"])
m.eval()

rows = []
for ep in range(EPS):
    torch.manual_seed(1000 + ep)          # 采样可复现（见 J.4：轨迹 A/B 必须重设）
    obs = env.reset(ep, map_seed=100 + ep)   # 每局一张不同的图
    c = collections.Counter()
    first_barracks = None
    while True:
        idx, _lp, _v = act(m, obs, win=tokenize(env, obs))
        a = obs.cand["actions"][idx]
        sub = getattr(a, "sub", None)
        # ★口径（用户 2026-09-13 澄清，别再搞错）：
        #   **扩张 = attack，且是唯一手段**（attack 自带移动）。`move` 只能走**野地**、
        #   作用是长途奔袭，而**用户不打算教它** ⇒ move 的次数**不是**扩张指标，
        #   别把它读成"动得不少"。所以这里只量 attack 的战果：**打了有没有占下地**。
        _own_before = len(env.world.own_tiles(env.agent)) if a.kind == "attack" else None
        obs, _r, done, info = env.step(a)
        if info["ok"]:
            c[a.kind] += 1
            if _own_before is not None and len(env.world.own_tiles(env.agent)) > _own_before:
                c["attack:占地"] += 1
            if a.kind == "build" and sub == "兵营":
                c["建兵营"] += 1
                if first_barracks is None:
                    first_barracks = info["turn"]
        if done:
            break
    tiles = len(env.world.own_tiles(env.agent))
    rows.append((ep, c["建兵营"], first_barracks, c["recruit"], c["attack"],
                 c["attack:占地"], c["move"], tiles))
    print(f"  局{ep}:  兵营×{c['建兵营']}  首建 T{first_barracks}  "
          f"征兵{c['recruit']}  attack{c['attack']}(占{c['attack:占地']})  "
          f"move{c['move']}(野地行军)  领地{tiles}")

n = len(rows)
fb = sorted(r[2] for r in rows if r[2] is not None)
atk = sum(r[4] for r in rows)
got = sum(r[5] for r in rows)
print(f"\n=== {CKPT}    {n} 局 × {TURNS} 回合（采样） ===")
print(f"  经济开局（有买卖/建造活动）：{'✓' if all(sum(r[1:7]) > 0 for r in rows) else '✗'}")
print(f"  建成兵营的图：{sum(1 for r in rows if r[1] > 0)}/{n}"
      f"     首次兵营中位：{fb[len(fb) // 2] if fb else '—'}"
      f"   逐图 {[r[2] for r in rows]}")
print(f"  征兵        {sum(r[3] for r in rows) / n:.1f} 次/局")
print(f"  ★attack     {atk / n:.1f} 次/局   ← **扩张的唯一手段**"
      f"；其中**占下地** {got / n:.1f}（成功率 {got / atk:.0%}）" if atk else "  attack 0")
print(f"  move        {sum(r[6] for r in rows) / n:.1f} 次/局"
      f"   ← 野地行军，**不是扩张指标**（用户不打算教这个）")
print(f"  终局领地    均值 {sum(r[7] for r in rows) / n:.1f}   逐局 {[r[7] for r in rows]}"
      f"   （开局 5 格 ⇒ 净增 {sum(r[7] for r in rows) / n - 5:.1f}）")
