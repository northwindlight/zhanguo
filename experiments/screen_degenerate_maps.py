# -*- coding: utf-8 -*-
"""筛**训练图**：把「老师启动不起来」的退化图挑出来，免得整炉在它们身上白烧。

为什么要它（用户 2026-09-17）：「先筛选地图，防止退化局」。
`rl/bc.py` 里已有 `episode_is_degenerate` 守卫，但它是**事后**的 —— 退化局照样要跑满
150 回合才发现，那几局还得占着 `--block` 的槽（块内 1 局纯 BC + 2 局 DAgger 全废）。
事前把图筛掉，省的是**整块**的时间。

判据**原样复用** `rl.bc.episode_is_degenerate`（退化 ≤7 格 / 健康 ≥12 格，相对化阈值
= 见过的中位数 × 0.3、不低于 8；`turns < 40` 不判）—— 不另立一套，免得两处口径漂。
差别只在"见过的中位数"：训练里是**在线累积**的，这里用**全池**中位数（更稳）。

图集口径与训练**逐字对齐**：`map_seed = 0 + 块号 × 7919`（`rl/bc.py:_map_seed`，`--seed 0`）。

用法：
    python experiments/screen_degenerate_maps.py [turns] [n_maps] [teacher]
    # 默认 150 回合 / 67 张 / v11plus（与 200 局 ÷ block 3 的炉一致）

输出：rl/maps/degenerate.json —— 每个块一行（图 seed / 领地 / 消费 / 是否退化），
      外加 `bad_blocks` 列表，供训练侧跳过。
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.env import ZhanguoEnv          # noqa: E402
import rl.bc as bc                     # noqa: E402

TURNS = int(sys.argv[1]) if len(sys.argv) > 1 else 150
N_MAPS = int(sys.argv[2]) if len(sys.argv) > 2 else 67
TEACHER = sys.argv[3] if len(sys.argv) > 3 else "v11plus"
BLOCK_STRIDE = 7919                    # ★与 rl/bc.py 的 `_map_seed` 同源，别改
OUT = Path("rl/maps/degenerate.json")

teacher = bc.get_teacher(TEACHER)
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
rng = random.Random(0xB4BE)            # 与 screen_maps.py 同款固定流：同图必得同结果

print(f"筛训练图：{N_MAPS} 张（seed = 块号 × {BLOCK_STRIDE}），各跑老师 "
      f"{TEACHER} {TURNS} 回合（16×16）", flush=True)

rows = []
for block in range(N_MAPS):
    seed = 0 + block * BLOCK_STRIDE
    env.reset(seed)
    for t in range(TURNS):
        teacher(env.world, env.agent, rng, max_actions=10 ** 9,
                on_action=lambda *a: None)
        env.world.resolve_turn()
        if t + 1 < TURNS:
            env.world.begin_turn()
    tiles = len(env.world.own_tiles(env.agent))
    spend = env.world.spend_total(env.agent)
    rows.append({"block": block, "map_seed": seed, "tiles": tiles, "spend": spend})
    print(f"  块{block:>3}  seed {seed:>7}  领地 {tiles:>4}  消费 {spend:>10,.0f}", flush=True)

# ★**两条判据一起用**（用户 2026-09-18）—— 分别记下是哪条命中的，便于日后归因
seen = [r["tiles"] for r in rows]
seen_spend = [r["spend"] for r in rows]
for r in rows:
    r["deg_tiles"] = bc.episode_is_degenerate(r["tiles"], seen, turns=TURNS)
    r["deg_spend"] = bc.episode_is_degenerate(
        999, seen, turns=TURNS, spend=r["spend"], seen_spend=seen_spend)   # 领地喂个健康值 ⇒ 只让②生效
    r["degenerate"] = r["deg_tiles"] or r["deg_spend"]

bad = [r["block"] for r in rows if r["degenerate"]]
tiles_sorted = sorted(seen)
med = tiles_sorted[len(tiles_sorted) // 2]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "turns": TURNS, "n_maps": N_MAPS, "teacher": TEACHER,
    "block_stride": BLOCK_STRIDE, "seed0": 0,
    "tiles_median": med, "tiles_min": tiles_sorted[0], "tiles_max": tiles_sorted[-1],
    "bad_blocks": bad,
    "rows": rows,
}, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"\n领地：min {tiles_sorted[0]}  中位 {med}  max {tiles_sorted[-1]}")
n_t = sum(1 for r in rows if r["deg_tiles"]); n_s = sum(1 for r in rows if r["deg_spend"])
print(f"★退化图 {len(bad)}/{N_MAPS}：块 {bad}")
print(f"  其中 **领地判据** 抓到 {n_t} 张、**消费判据** 抓到 {n_s} 张"
      f"（只被消费抓到的 {n_s - sum(1 for r in rows if r['deg_tiles'] and r['deg_spend'])} 张"
      f" —— 那些正是旧脚本会漏掉的）")
print(f"  ⇒ 跳掉这些块，省 {len(bad) * 3} 局（每块 3 局）")
print(f"已写 {OUT}")
