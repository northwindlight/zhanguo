# -*- coding: utf-8 -*-
"""**一局的耗时在 100 个回合里怎么分布**（用户的「核心是慢」）。

    python rl/turn_phase.py [latent|none] [局数] [size_min] [size_max] [t_max]

★★ 口径：**不手抄回路** —— 给 `phase_bench.py` 坑过一次之后的纪律（那次我手抄
  了一份回路，量出「前向 48~56%」，照它优化完拿整条回路 A/B 一验只有 0.996×）。
   这里改挂**边界**：
     · 挂 `sb.step`，用**相邻两次 `step` 的间隔**度量**一个完整决策点**的开销
       （观测 + 前向 + 采样 + 打分 + 上一步的回填），按**该决策点所属回合**入桶。
       ⇒ 每一毫秒都有着落，且**不需要抄循环里任何一行**。
     · `combat_probs.assess` / `obs_of` / `forward_state` / `_score` 单独挂表，
       同样按 `sb.turn` 入桶 ⇒ 能看出**哪个阶段随回合数增长**。
       ★ 它们是上面那笔总数的**切片**，不是相加项（`assess` 还嵌在 `frame_odds` 里）。
     · `sb.legal()` 的返回长度 = 候选动作数 ⇒ "局面变多大"的直接读数。

★ 判据：**后段回合是否显著更贵**。
    · 平 ⇒ 慢是均匀的，只能靠**并行 / 减数据（缩图、砍回合）**下手；
    · 后段爆炸 ⇒ 可以从**单位数 / 交战规模**下手（`assess` 是精确 DP，随参战
      单位组合**组合爆炸**，PLAN §12.2 已记过它是随仗打多大剧烈变化的大头）。

★★★ 2026-09-27 实测（1v1 / size 10-14 / t_max 100 / latent，6 局 14001 决策点，
    墙钟 530.5 s = **37.9 ms/决策点**）—— 答案是"后段爆炸"：

  | 回合 | ms/点 | 该段占比 | 战斗 DP | 观测 obs_of | **前向** | 候选数 |
  |---|---|---|---|---|---|---|
  | 1-10 | **11.9** | 1.8% | 0.73 | 2.07 | **7.83** | 29.8 |
  | 31-40 | 35.9 | 10.4% | 20.32 | 23.93 | 9.17 | 59.2 |
  | 41-50 | **64.4** | **20.6%** | 45.96 | 51.69 | 9.65 | 63.7 |
  | 91-100 | **64.3** | 14.1% | **43.60** | 48.89 | **11.18** | 89.5 |

  · **累计**：第 30 回合 11.1% · 第 50 回合 **42.1%** · 第 70 回合 **65.6%**
    ⇒ **后 50 个回合吃掉 58% 的墙钟**。
  · **前向是平的**（7.83 → 11.18，只 1.43×，涨的还只是候选数 29.8→89.5 带来的张量变大）
    ⇒ 它是**唯一涨得慢的东西**，"优化前向"这条路到顶了（现占总墙钟 ~17%）。
  · **战斗 DP 涨 60×**（0.73 → 43.60），后段一个占掉决策点的 68%。
  · **平局是纯亏**：3 个打满 100 回合的平局 = 351.8 s = **全部墙钟的 66%**，
    而一个胜负都不产；反过来又快又便宜的（34 回合 9.4 s）恰恰是分出胜负的。
    ⇒ 「平局率」和「核心是慢」是**同一个问题**。

  ★ 与 `dp_states_probe.py` 独立量出的「战斗 DP 占总墙钟 58.8%」吻合。

  ★★ 一处**顺手挖到的真浪费**：`_score` **每决策点被调 2.00 次**
    （`collect_episode` 里 `prev_score = _score(...)` 一次，`_reward()` 里又一次，
    `rl/train.py:382`）—— 后者算的就是前一者的下一帧版本，**传参就能省掉**。
    目前只值 1.7%，但它是白给的。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import combat_probs as CB                # noqa: E402
from rl import encode as E                       # noqa: E402
from rl import train as T                        # noqa: E402
from rl.model import build_model                  # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

mem = sys.argv[1] if len(sys.argv) > 1 else "latent"
NG = int(sys.argv[2]) if len(sys.argv) > 2 else 6
SMIN = int(sys.argv[3]) if len(sys.argv) > 3 else 10
SMAX = int(sys.argv[4]) if len(sys.argv) > 4 else 14
TMAX = int(sys.argv[5]) if len(sys.argv) > 5 else 100
BAND = 10
torch.set_num_threads(1)      # ★ Pi 是用户干活的机器，只占一个核
mem_slots = 8 if mem == "latent" else 0

PHASES = ["战斗DP assess", "  ├ frame_odds", "观测 obs_of", "前向 forward", "打分 _score"]
# 桶：回合 -> 各阶段累计秒
tot: dict[int, float] = {}          # 决策点总开销（step 间隔）
sec: dict[int, dict[str, float]] = {}   # 阶段切片
stp: dict[int, int] = {}            # 决策点数
cand: dict[int, list] = {}          # 候选动作数样本
CALLS: dict[str, int] = {}          # ★ 调用次数：拿它**核嵌套**（见下面残差那段）
games = []                          # (size, 回合数, 总秒)

for gi in range(NG):
    size = SMIN + (gi % (SMAX - SMIN + 1)) if SMAX > SMIN else SMIN
    sb = Sandbox(seed=9000 + gi, size=size, t_max=TMAX, n_nations=2,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    torch.manual_seed(gi)
    nets = {p: build_model(mem_slots=mem_slots) for p in sb.players}
    for n in nets.values():
        n.eval()

    def bump(name, dt):
        sec.setdefault(sb.turn, {}).setdefault(name, 0.0)
        sec[sb.turn][name] += dt
        CALLS[name] = CALLS.get(name, 0) + 1

    real_assess = CB.assess

    def assess(*a, **k):
        t = time.perf_counter()
        try:
            return real_assess(*a, **k)
        finally:
            bump("战斗DP assess", time.perf_counter() - t)
    CB.assess = assess
    real_fo = CB.frame_odds

    def frame_odds(*a, **k):
        t = time.perf_counter()
        try:
            return real_fo(*a, **k)
        finally:
            bump("  ├ frame_odds", time.perf_counter() - t)
    CB.frame_odds = frame_odds
    real_obs = E.obs_of

    def obs_of(*a, **k):
        t = time.perf_counter()
        try:
            return real_obs(*a, **k)
        finally:
            bump("观测 obs_of", time.perf_counter() - t)
    E.obs_of = obs_of
    real_sc = T._score

    def score(*a, **k):
        t = time.perf_counter()
        try:
            return real_sc(*a, **k)
        finally:
            bump("打分 _score", time.perf_counter() - t)
    T._score = score
    for n in nets.values():
        real_fs = n.forward_state

        def fs(batch, m=None, _r=real_fs):
            t = time.perf_counter()
            try:
                return _r(batch, m)
            finally:
                bump("前向 forward", time.perf_counter() - t)
        n.forward_state = fs
    real_legal = sb.legal

    def legal():
        r = real_legal()
        cand.setdefault(sb.turn, []).append(len(r))
        return r
    sb.legal = legal

    # ★ 决策点边界：相邻两次 step 的间隔 = 上一个决策点的全部开销
    state = {"t": time.perf_counter()}
    real_step = sb.step

    def step(a):
        gap = time.perf_counter() - state["t"]
        turn = sb.turn
        tot[turn] = tot.get(turn, 0.0) + gap
        stp[turn] = stp.get(turn, 0) + 1
        r = real_step(a)
        state["t"] = time.perf_counter()
        return r
    sb.step = step

    t0 = time.perf_counter()
    with torch.inference_mode():
        steps, info = T.collect_episode(nets, sb, rng=np.random.default_rng(gi))
    wall = time.perf_counter() - t0
    # 收尾那一段（最后一步之后的 is_terminal/_reward 等）也算进它所属的回合
    tail = time.perf_counter() - state["t"]
    tot[sb.turn] = tot.get(sb.turn, 0.0) + tail
    games.append((size, info["turns"], wall, len(steps)))
    print(f"  局 {gi + 1}/{NG} size={size} 回合 {info['turns']} "
          f"步 {len(steps)} 墙钟 {wall:.1f}s "
          f"胜方 {info.get('winner') or '平'}", flush=True)

    # 复原（下一局重新挂）
    CB.assess, CB.frame_odds, E.obs_of, T._score = real_assess, real_fo, real_obs, real_sc

gtot = sum(tot.values())
gstp = sum(stp.values())
print(f"\n合计 {gtot:.1f}s / {gstp} 决策点 = **{gtot / gstp * 1000:.1f} ms/决策点**"
      f"（{NG} 局，memory={mem}，size {SMIN}-{SMAX}，t_max={TMAX}）\n")

print(f"{'回合':>7}{'决策点':>8}{'ms/点':>9}{'该段占比':>10}"
      + "".join(f"{p.strip():>13}" for p in PHASES)
      + f"{'其余未归因':>11}{'候选数':>8}")
for lo in range(1, TMAX + 1, BAND):
    hi = lo + BAND - 1
    ks = [k for k in tot if lo <= k <= hi]
    if not ks:
        continue
    n = sum(stp.get(k, 0) for k in ks)
    if not n:
        continue
    tw = sum(tot[k] for k in ks)
    row = f"{f'{lo}-{hi}':>7}{n:>8}{tw / n * 1000:>9.1f}{tw / gtot:>9.1%}"
    for p in PHASES:
        v = sum(sec.get(k, {}).get(p, 0.0) for k in ks)
        row += f"{v / n * 1000:>13.2f}"
    # ★★ 只减**互不包含**的三项。嵌套关系（我第一版就搞反过，残差直接变负）：
    #     `assess` ⊂ `frame_odds` ⊂ `obs_of`（观测入口里调一次、往下传）
    #   ⇒ `frame_odds`/`assess` 是 `obs_of` 的**内层切片**，不能当独立项扣。
    top = sum(sec.get(k, {}).get(p, 0.0) for k in ks
              for p in ("观测 obs_of", "前向 forward", "打分 _score"))
    row += f"{(tw - top) / n * 1000:>11.2f}"
    cs = [c for k in ks for c in cand.get(k, [])]
    row += f"{np.mean(cs):>8.1f}" if cs else f"{'-':>8}"
    print(row, flush=True)

print("\n调用次数 / 决策点（★ 用它核嵌套：`frame_odds` 与 `assess` 应 <= `obs_of`）：")
for p in PHASES:
    print(f"  {p:<18}{CALLS.get(p, 0):>9} / {gstp} = {CALLS.get(p, 0) / gstp:>6.2f}")

# 累计曲线：打到第 T 回合时，已经花掉一局总时间的百分之多少
cum = np.cumsum([tot.get(k, 0.0) for k in range(1, TMAX + 2)])
print("\n累计（一局到此回合已花掉的墙钟占比）：")
for T in (10, 20, 30, 50, 70, 100):
    if T < len(cum):
        print(f"  到第 {T:>3} 回合：{cum[T - 1] / gtot:>6.1%}", flush=True)

print("\n★ 读法：若后段 ms/点 与头段接近 ⇒ 慢是**均匀**的（只能并行/减数据）；"
      "若后段翻几倍 ⇒ 贵在**交战规模**（`assess` 精确 DP 组合爆炸）。", flush=True)
