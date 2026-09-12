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
        # ★move 必须分口径：**内部调动**（目标格本来就是自己的）vs **扩张性 move**
        #   （目标是中立/敌方）。2026-09-13 发现读数冲突：J.1 记的「ep400 move 0.4
        #   次/局」跟同期实测的 9.0 差 22 倍，最可能就是它只算了扩张性 move ——
        #   **在核实前两套数不能混用**。用 step **之前**的所有权判（move 成功后
        #   目标格归属会变，那时再判就全成"内部"了）。
        _internal = None
        if a.kind == "move":
            _internal = a.tile in set(env.world.own_tiles(env.agent))
        obs, _r, done, info = env.step(a)
        if info["ok"]:
            c[a.kind] += 1
            if _internal is not None:
                c["move:内部" if _internal else "move:扩张"] += 1
            if a.kind == "build" and sub == "兵营":
                c["建兵营"] += 1
                if first_barracks is None:
                    first_barracks = info["turn"]
        if done:
            break
    tiles = len(env.world.own_tiles(env.agent))
    rows.append((ep, c["建兵营"], first_barracks, c["recruit"], c["move"],
                 c["move:扩张"], c["attack"], tiles))
    print(f"  局{ep}:  兵营×{c['建兵营']}  首建 T{first_barracks}  "
          f"征兵{c['recruit']}  move{c['move']}(扩张{c['move:扩张']})  "
          f"attack{c['attack']}  领地{tiles}")

n = len(rows)
fb = sorted(r[2] for r in rows if r[2] is not None)
print(f"\n=== {CKPT}    {n} 局 × {TURNS} 回合（采样） ===")
print(f"  经济开局（有买卖/建造活动）：{'✓' if all(sum(r[1:6]) > 0 for r in rows) else '✗'}")
print(f"  建成兵营的图：{sum(1 for r in rows if r[1] > 0)}/{n}"
      f"     首次兵营中位：{fb[len(fb) // 2] if fb else '—'}"
      f"   逐图 {[r[2] for r in rows]}")
print(f"  征兵        {sum(r[3] for r in rows) / n:.1f} 次/局")
print(f"  move        {sum(r[4] for r in rows) / n:.1f} 次/局"
      f"   ← 其中**扩张性** {sum(r[5] for r in rows) / n:.1f}"
      f"（内部调动 {sum(r[4] - r[5] for r in rows) / n:.1f}）")
print(f"  attack      {sum(r[6] for r in rows) / n:.1f} 次/局")
print(f"  终局领地    均值 {sum(r[7] for r in rows) / n:.1f}   逐局 {[r[7] for r in rows]}")
