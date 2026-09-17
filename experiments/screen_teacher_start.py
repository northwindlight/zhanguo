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
    python experiments/screen_teacher_start.py [turns] [n] [teacher] [mode] [--jobs N] [--offset K]
    # 默认 80 回合 / 67 张 / v11plus / mode=block
    #   mode=block —— seed = 序号 × 7919（**训练炉的 67 张图**，与 `rl/bc.py:_map_seed` 同源）
    #   mode=range —— seed = **offset + 序号**（0..N-1 那片，与 `medium_pool.json` 同空间）
    # `--jobs N`：多进程并行（老师是纯 Python，**受核数限制，多核机器上线性加速**）——
    #   64 核的 GPU 机上扫 3000 张只要几分钟，别在 4 核 Pi 上干等两小时。

输出：rl/maps/teacher_start_<mode>_<n>.json —— 每张一行（首攻/首扩/各节点领地），
      外加 `start_within_20_attack` / `start_within_20_expand` 两个 seed 列表。
      ⚠ **不碰** `rl/maps/degenerate.json`（另一个口径：150 回合的退化筛查）。
"""
import json
import multiprocessing as mp
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.env import ZhanguoEnv          # noqa: E402


def get_teacher(which: str):
    """★**不走 `rl.bc.get_teacher`**：那个模块 import torch，而本筛查**纯 CPU**、
    一行 torch 都用不上（`rl/env.py` 也是 torch-free）。为了在 torch 还没装好的
    机器上立刻能跑，这里直接问 `rule_ai` 注册表 —— 与 `rl/bc.get_teacher` 同一处
    权威（版本名→模块路径的映射只在 `rule_ai.py` 那一处）。"""
    import rule_ai
    return rule_ai.resolve(which)[1]

_pure = [a for a in sys.argv[1:] if not a.startswith("--")]
TURNS = int(_pure[0]) if len(_pure) > 0 else 80
N_MAPS = int(_pure[1]) if len(_pure) > 1 else 67
TEACHER = _pure[2] if len(_pure) > 2 else "v11plus"
MODE = _pure[3] if len(_pure) > 3 else "block"


def _opt(name, default):
    if name in sys.argv:
        return int(sys.argv[sys.argv.index(name) + 1])
    return default


JOBS = _opt("--jobs", 1)
OFFSET = _opt("--offset", 0)
BLOCK_STRIDE = 7919                    # ★与 rl/bc.py 的 `_map_seed` 同源，别改
START_GATE = 20                        # "20 回合内启动"的那道门
OUT = Path(f"rl/maps/teacher_start_{MODE}_{N_MAPS}.json")
MARK_TURNS = tuple(t for t in (10, 20, 50, 80) if t <= TURNS)   # 报领地快照的节点

# —— 每个 worker 自己建一份 env/teacher，**复用它跑多张图**（env.reset 逐图重置）——
_G: dict = {}


def _init(turns: int, teacher: str) -> None:
    _G["teacher"] = get_teacher(teacher)
    _G["env"] = ZhanguoEnv(map_size=16, max_turns=turns)


def _one(idx: int) -> dict:
    """跑一张图，返回这一行。seed 由 mode 决定（见文件头）。"""
    env = _G["env"]
    teacher = _G["teacher"]
    seed = idx * BLOCK_STRIDE + OFFSET if MODE == "block" else idx + OFFSET
    env.reset(seed, map_seed=seed)
    env.world.max_turns = TURNS
    rng = random.Random(0xB4BE)        # 老师自己的 RNG：固定 ⇒ 同图必得同结果
    hit = [None]

    def on_action(tool, _args):
        if tool == "attack" and hit[0] is None:
            hit[0] = env.world.turn

    first_expand = None
    marks: dict[int, int] = {}
    for k in range(TURNS):
        teacher(env.world, env.agent, rng, max_actions=10 ** 9, on_action=on_action)
        env.world.resolve_turn()
        if first_expand is None and len(env.world.own_tiles(env.agent)) > 5:
            first_expand = env.world.turn
        if (k + 1) in MARK_TURNS:
            marks[k + 1] = len(env.world.own_tiles(env.agent))
        if k + 1 < TURNS:
            env.world.begin_turn()
    return {"block": idx, "map_seed": seed, "first_attack": hit[0],
            "first_expand": first_expand, "tiles_at": marks,
            "tiles_end": len(env.world.own_tiles(env.agent)),
            "spend": env.world.spend_total(env.agent)}


_how = (f"seed = 序号 × {BLOCK_STRIDE} + {OFFSET}（训练炉那批）" if MODE == "block"
        else f"seed = {OFFSET}..{OFFSET + N_MAPS - 1}")
print(f"筛「老师何时启动」：{N_MAPS} 张（{_how}），老师 {TEACHER}，"
      f"跑到 {TURNS} 回合（16×16，不抖动），并发 {JOBS}", flush=True)

if JOBS > 1:
    with mp.Pool(JOBS, initializer=_init, initargs=(TURNS, TEACHER)) as pool:
        rows = []
        for i, r in enumerate(pool.imap_unordered(_one, range(N_MAPS), chunksize=4)):
            rows.append(r)
            if (i + 1) % 200 == 0:
                print(f"  … {i + 1}/{N_MAPS}", flush=True)
    rows.sort(key=lambda r: r["block"])
else:
    _init(TURNS, TEACHER)
    rows = [_one(i) for i in range(N_MAPS)]

for r in rows:
    fa = "—" if r["first_attack"] is None else str(r["first_attack"])
    fe = "—" if r["first_expand"] is None else str(r["first_expand"])
    print(f"  #{r['block']:>4}  seed {r['map_seed']:>7}  首攻 {fa:>4}  首扩 {fe:>4}  "
          f"领地 {r['tiles_at'].get(20, '-'):>4}(20) → {r['tiles_at'].get(50, '-'):>4}(50) → "
          f"{r['tiles_end']:>4}({TURNS})", flush=True)


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
