# -*- coding: utf-8 -*-
"""筛**老师多少回合才启动**的图（用户 2026-09-17：「找老师 20 回合内启动的图」）。

为什么要它：教开局时（`--turns 50`）老师**在 50 回合前一格都不打**（实测 seed
0/7919/15838/23757 逐 25 回合量：25/50 回合全是开局的 5 格）。若整炉都建在这种图上，
学生学到的「开局」= 只攒钱建造、从不扩张 —— 那不是开局，是半张谱。挑出**老师早早
动起来**的图，开局那 50 回合里才有"该打谁"的示范。

"启动"取两个口径，都报出来（**不预设哪个才算**）：
  · `first_attack` —— 老师第一次发 `attack` 动作的回合
  · `first_expand` —— 自家领地第一次超过开局 5 格的回合（真打下来了）

图集口径与训练**逐字对齐**：`map_seed = 0 + 块号 × 7919`（`rl/bc.py:_map_seed`，
`--seed 0 --block 3`）；**不抖动**（与当前那炉 `--rules-jitter 0` 同口径）。

用法：
    python experiments/screen_teacher_start.py [turns] [n_maps] [teacher]
    # 默认 80 回合 / 67 张 / v11plus

输出：rl/maps/teacher_start.json —— 每块一行（首攻回合 / 首扩回合 / 各节点领地），
      外加 `start_within_20_attack` / `start_within_20_expand` 两个块号列表。
      ⚠ **不碰** `rl/maps/degenerate.json`（那是另一个口径的产物：150 回合的退化筛查）。
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.env import ZhanguoEnv          # noqa: E402
import rl.bc as bc                     # noqa: E402

TURNS = int(sys.argv[1]) if len(sys.argv) > 1 else 80
N_MAPS = int(sys.argv[2]) if len(sys.argv) > 2 else 67
TEACHER = sys.argv[3] if len(sys.argv) > 3 else "v11plus"
BLOCK_STRIDE = 7919                    # ★与 rl/bc.py 的 `_map_seed` 同源，别改
START_GATE = 20                        # "20 回合内启动"的那道门
OUT = Path("rl/maps/teacher_start.json")
MARK_TURNS = (10, 20, 50, 80)          # 报领地快照的几个节点

teacher = bc.get_teacher(TEACHER)
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
rng = random.Random(0xB4BE)            # 固定流：同图必得同结果

print(f"筛「老师何时启动」：{N_MAPS} 张（seed = 块号 × {BLOCK_STRIDE}），"
      f"老师 {TEACHER}，跑到 {TURNS} 回合（16×16，不抖动）", flush=True)

rows = []
for block in range(N_MAPS):
    seed = 0 + block * BLOCK_STRIDE
    env.reset(seed, map_seed=seed)
    env.world.max_turns = TURNS
    first_attack = None
    first_expand = None
    marks: dict[int, int] = {}
    # `nonlocal` 在模块级循环里绑不上（循环不是函数作用域）⇒ 用可变单元装。
    hit = [None]

    def on_action(tool, _args):
        if tool == "attack" and hit[0] is None:
            hit[0] = env.world.turn

    for k in range(TURNS):
        teacher(env.world, env.agent, rng, max_actions=10 ** 9, on_action=on_action)
        first_attack = hit[0]
        env.world.resolve_turn()
        if first_expand is None and len(env.world.own_tiles(env.agent)) > 5:
            first_expand = env.world.turn
        if (k + 1) in MARK_TURNS:
            marks[k + 1] = len(env.world.own_tiles(env.agent))
        if k + 1 < TURNS:
            env.world.begin_turn()

    rows.append({
        "block": block, "map_seed": seed,
        "first_attack": first_attack, "first_expand": first_expand,
        "tiles_at": marks,
        "tiles_end": len(env.world.own_tiles(env.agent)),
        "spend": env.world.spend_total(env.agent),
    })
    fa = "—" if first_attack is None else str(first_attack)
    fe = "—" if first_expand is None else str(first_expand)
    print(f"  块{block:>3}  seed {seed:>7}  首攻 {fa:>4}  首扩 {fe:>4}  "
          f"领地 {marks.get(20, '-'):>4}(20) → {marks.get(50, '-'):>4}(50) → "
          f"{rows[-1]['tiles_end']:>4}({TURNS})", flush=True)


def _within(key: str) -> list[int]:
    return sorted(r["block"] for r in rows
                  if r[key] is not None and r[key] <= START_GATE)


early_attack = _within("first_attack")
early_expand = _within("first_expand")
never = sorted(r["block"] for r in rows if r["first_expand"] is None)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "turns": TURNS, "n_maps": N_MAPS, "teacher": TEACHER,
    "block_stride": BLOCK_STRIDE, "seed0": 0, "jitter": 0.0,
    "start_gate": START_GATE,
    "start_within_20_attack": early_attack,
    "start_within_20_expand": early_expand,
    "never_expanded": never,
    "rows": rows,
}, ensure_ascii=False, indent=1), encoding="utf-8")

n = N_MAPS
print(f"\n★{START_GATE} 回合内**有进攻**：{len(early_attack)}/{n} —— 块 {early_attack}")
print(f"★{START_GATE} 回合内**真打下地**：{len(early_expand)}/{n} —— 块 {early_expand}")
print(f"  跑满 {TURNS} 回合仍一格没打下的：{len(never)}/{n} —— 块 {never}")
if not early_expand:
    print(f"  ⚠ 没有一张图在 {START_GATE} 回合内扩地 —— 这个口径下「早早启动的图」为空，"
          f"要教「开局就动」得放宽门（或换个口径，比如按 first_attack 取）")
print(f"已写 {OUT}")
