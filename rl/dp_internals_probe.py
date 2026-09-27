# -*- coding: utf-8 -*-
"""**战斗 DP 慢在哪：算法（状态数/转移数）还是 Python（每次操作的开销）？**

    python rl/dp_internals_probe.py [size] [t_max] [抓取起始回合]

★ 背景：`rl/dp_states_probe.py` 量到 DP 占总墙钟 **58.8%**，且
  时间 ≈ `0.65 ms + 0.33 ms × n_states` —— **0.33 ms 摊到"一个状态展开"上**。
  一个状态展开做的事并不多（建 `nxt`、递归、并两个 dict），
  330 µs 对它来说**太多了**。所以要么是每个状态的**转移数**很大，
  要么是**每次操作的 Python 开销**大。这个探针把两者分开。

★ 手法：
  · 跑一局到指定回合之后（默认 85），把那一帧的每个交战格 `build` 成 `Battle`
    存下来（`Battle` 是对局面的一次性快照，之后 `assess` 是纯函数）。
  · 挑最重的那个，用 **cProfile** 跑一遍 ⇒ `ncalls` 给出**状态展开次数**（算法的量），
    `tottime` 给出**纯循环**的耗时（Python 的量）。
  · 同时报 `len(agg)`（每个状态的**转移数**）与 `_agg_damage` 的命中情况。
  · ★ **冷/热分开**：生产路径 `frame_odds` 每帧都 `build` 新 `Battle`
    ⇒ `agg_cache` 每次都是**空**的，**冷的那次才是生产实际成本**。

★ 判据：
  · `µs/状态展开` 大（几百）而转移数小 ⇒ **Python 慢**（解释器开销），
    换 numpy/C 或减少 per-op 开销有量级空间；
  · 转移数 × 状态数 本身巨大 ⇒ **算法慢**（组合爆炸），只能削搜索空间。

★★★ 2026-09-27 实测（seed 9500 / size 14 / 第 70 回合最重的那格）—— **答案是 Python**：

    冷跑 9.61 ms，`n_states=59`，`rec` 调用 **2125** 次（= 59 × `len(agg)`36，完全对上）
    ⇒ **4.5 µs / 一次转移**。`len(agg)` 恒 ≤ **6^方数**（掷骰枚举）：2 方 36、3 方 216。

    cProfile（按 tottime，比例可信、绝对值被插桩放大）：

    | ncalls | 是谁 |
    |---|---|
    | 2125 | `rec` 自身（**55%**） |
    | 4248 / 2125 | `all` / `any` |
    | 10764 | `list.append` |
    | 1980 / 2125 | `_survivors` / `_absorbed` |
    | 8496 | `<genexpr>`（就是那两个 `all` 的生成器） |
    | 4320 / 6406 / 4248 | `dict.get` / `len` / `divmod` |

    **一个数值计算函数都没有。** 一次转移约 **20 个解释器级操作**，
    4.5 µs ÷ 20 ≈ **225 ns/操作** —— 正好是 CPython 的单操作成本。
    ⇒ **换 C/numpy 是 1~2 个数量级的空间，不是改算法。**

  ★★ 顺带量出**指数项**（改动前，`tests/test_rl_dp_identity.py` 那 8 个局面）：

    | 局面 | n_states | 冷跑 |
    |---|---|---|
    | 2v1 单兵种 | 37 | 6.0 ms |
    | 2v2 混编 | 145 | 23.0 ms |
    | 3v3 混编 | 254 | 43.1 ms |
    | 4v4 大仗 | 627 | 114.3 ms |
    | 3v2 带撤退 | 2158 | 315.4 ms |
    | **5 方混战** | **8767** | **11 961 ms（12 秒）** |

    **单次 `assess` 12 秒**，每状态 1.36 ms vs 2 方的 163 µs（**8.4×**）
    ⇒ 多方局的慢是**指数项**（`len(agg)` 随方数），1v1 课表恰好躲开了它。

  ★★★ 据此做的三处**纯 Python** 改动（`rl/combat_probs.py`，逐位相同，**DP ×1.66**）：
    ① 吸收态也进 memo（原来 1980/2125 次都在重算谓词 + 重建 `frozenset`）；
    ② `flat` 提出结果循环（原来 `all` 跑 4248 次、生成器 8496 次）；
    ③ 删死代码 `pre_hp`。
    守卫在 `tests/test_rl_dp_identity.py`（冻结基准 + 非空泛性），三条都故意破坏过。
"""
import cProfile
import io
import pstats
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import combat_probs as CB                # noqa: E402
from rl import train as T                        # noqa: E402
from rl.model import build_model                  # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 14
TMAX = int(sys.argv[2]) if len(sys.argv) > 2 else 100
FROM = int(sys.argv[3]) if len(sys.argv) > 3 else 70
SEED = int(sys.argv[4]) if len(sys.argv) > 4 else 9500   # ★ 9500 在 size13/14 实测打满 100 回合
torch.set_num_threads(1)      # ★ Pi 是用户干活的机器，只占一个核

sb = Sandbox(seed=SEED, size=SIZE, t_max=TMAX, n_nations=2, halls_known=True,
             territory=True, alliances="random2v2").reset()
torch.manual_seed(0)
nets = {p: build_model(mem_slots=8) for p in sb.players}
for n in nets.values():
    n.eval()

picked: list = []
real_assess = CB.assess


def assess(*a, **k):
    # ★ 从 FROM 回合起，每帧把交战格抄一份（只在没抄够时做，避免拖慢）
    if sb.turn >= FROM and len(picked) < 40:
        for c in CB.engaged_cells(sb.world):
            b = CB.build(sb.world, *c)
            if b is not None:
                picked.append((sb.turn, c, b))
    return real_assess(*a, **k)


CB.assess = assess
t0 = time.perf_counter()
with torch.inference_mode():
    _s, info = T.collect_episode(nets, sb, rng=np.random.default_rng(0))
CB.assess = real_assess
print(f"局：size={SIZE} 回合 {info['turns']} 墙钟 {time.perf_counter() - t0:.1f}s "
      f"胜方 {info.get('winner') or '平'}；抄下 {len(picked)} 个交战格快照",
      flush=True)
if not picked:
    print("★ 没抄到（这局没打到那个回合 / 没交战）—— 换 size 或把起始回合调小。")
    sys.exit(1)

# 挑最重的：先用冷跑时间粗排（每个 Battle 只跑一次，正是生产的形态）
scored = []
for turn, cell, b in picked:
    t = time.perf_counter()
    o = CB.assess(b)
    dt = (time.perf_counter() - t) * 1000
    scored.append((dt, turn, cell, b, o))
scored.sort(key=lambda r: -r[0])
print(f"\n最重的 5 个交战格（**冷跑**，生产同形态）：")
print(f"{'回合':>6}{'格':>10}{'ms':>10}{'n_states':>10}{'truncated':>12}")
for dt, turn, cell, b, o in scored[:5]:
    print(f"{turn:>6}{str(cell):>10}{dt:>10.2f}{o.n_states:>10}{o.truncated:>12.2e}")

dt, turn, cell, b, o = scored[0]
print(f"\n★★ 最重的那个：第 {turn} 回合 {cell}，冷跑 {dt:.2f} ms，"
      f"n_states={o.n_states} ⇒ **{dt / max(1, o.n_states) * 1000:.1f} µs/状态展开**")

# 每个状态的转移数：根状态签名对应的 agg 大小
st0 = tuple(b.init[F] for F in b.order)
sigs = tuple(CB._atk_sig(s) for s in st0)
agg0 = CB._agg_damage(b, sigs)
print(f"根状态：{len(b.order)} 方、伤害结果数 len(agg) = **{len(agg0)}**、"
      f"总状态数 {o.n_states} ⇒ 转移数下界 ≈ n_states × len(agg) = "
      f"**{o.n_states * len(agg0)}**")

# 冷/热：同一个 Battle 上再跑 → agg_cache 已经暖了
warm = []
for _ in range(5):
    t = time.perf_counter()
    CB.assess(b)
    warm.append((time.perf_counter() - t) * 1000)
print(f"热跑（同一个 Battle，agg_cache 已暖）中位 {np.median(warm):.2f} ms "
      f"⇒ 冷/热 = {dt / np.median(warm):.2f}×")

# ---- cProfile（冷：新建一个同样的 Battle 最准，但签名相同即可代表）
pr = cProfile.Profile()
pr.enable()
CB.assess(b)
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(12)
print("\n=== cProfile（按 tottime，前 12）===")
for line in s.getvalue().splitlines():
    if "ncalls" in line or "/" in line or "{" in line or "}" in line:
        print(line)
