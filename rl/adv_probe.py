# -*- coding: utf-8 -*-
"""**优势里到底有没有信号** —— 拿现役 ckpt 打几局，把真实分布打出来（不是日志里量化过的）。

    python rl/adv_probe.py <ckpt> [局数] [size_min] [size_max]

★ 背景：`e/K` 188 个 iter 贴在 **1.00**（策略仍是"在合法动作上均匀"），
  而局本身**健康**（65% 分胜负、胜场甲乙近乎均等、先手胜 52%）
  ⇒ 环境在产信号，是**学习**没用上。两个候选：
    (a) **熵奖励赢了** —— 把策略持续往均匀推，压过优势的系统性部分；
    (b) **优势里没信号** —— GAE 把该有的东西抹成了噪声。
  这个探针分开量 (b)：把 `adv` 拆成「**塑形贡献**」与「**终端 ±1 贡献**」两块。

★★ 口径：完全照抄 `ppo_update` 的调用
  （`gae(rewards, values, dones, boots=...)`，**按成员/按方分开** —— 见
   `train.py:608` 的调用处；混着两方算就是拿对手的价值当自举，那是错的）。
   ★ 我顺手核过那条：`buf` 是 `{成员: [步]}`，所以调用处拿到的**只有一方的步** ✔。

★ 判据：
  · `|mean(adv)| / std(adv)` 极小 ⇒ 优势零均值（GAE 本该如此），要看**方差来源**；
  · 终端贡献占 `std(adv)` 的比例 —— 若终端碾压塑形 ⇒ 学的其实是"最后一击"，
    而 `(1-λ)(γλ)^k` 衰减让信号只覆盖**最后 ~30-50 步**（67 回合的局里那是少数）；
  · 塑形贡献若本身极小（`std ≪ 终端`）⇒ 全程大部分步**只有噪声**。
"""
import sys

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import scoring as S                        # noqa: E402
from rl import train as T                          # noqa: E402
from rl.model import build_model                    # noqa: E402
from rl.sandbox import Sandbox                      # noqa: E402

CKPT = sys.argv[1] if len(sys.argv) > 1 else "rl/runs/par_mem/mem1v1.pt"
NG = int(sys.argv[2]) if len(sys.argv) > 2 else 4
SMIN = int(sys.argv[3]) if len(sys.argv) > 3 else 10
SMAX = int(sys.argv[4]) if len(sys.argv) > 4 else 14
torch.set_num_threads(1)

sb0 = Sandbox(seed=1, size=SMIN, t_max=100, n_nations=2).reset()
nets = {p: build_model(mem_slots=8) for p in sb0.players}
ran = T._load_ckpt(CKPT, nets, mem_slots=8, log=lambda *a: None)
for n in nets.values():
    n.eval()
print(f"载入 {CKPT}（已完成 {ran} iter） · 打 {NG} 局 · size {SMIN}-{SMAX}\n")

RW, ADV, ADV_T, RET, VAL, LGP, NST = [], [], [], [], [], [], []
eps = []
for g in range(NG):
    size = SMIN + (g % (SMAX - SMIN + 1))
    sb = Sandbox(seed=9000 + g, size=size, t_max=100, n_nations=2,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    for n in nets.values():
        n.eval()
    steps, info = T.collect_episode(nets, sb, rng=np.random.default_rng(g))
    if not steps:
        continue
    eps.append((info, len(steps)))
    # ★ 按方分开算 GAE（照抄 train.py:608 的口径）
    for me in sb.players:
        ss = [s for s in steps if s.player == me]
        if not ss:
            continue
        rw = [s.reward for s in ss]
        va = [s.value for s in ss]
        dn = [s.done for s in ss]
        bt = [s.boot for s in ss]
        adv, ret = T.gae(rw, va, dn, boots=bt)
        # ★ 把**终端 ±1** 抹掉再算一遍 ⇒ 差值就是"终端那一击"的贡献
        rw0 = [0.0 if (d and abs(r) > 0.5) else r for r, d in zip(rw, dn)]
        adv_t, _ = T.gae(rw0, va, dn, boots=bt)
        RW += rw
        ADV += list(adv)
        ADV_T += list(adv - adv_t)
        RET += list(ret)
        VAL += va
        NST += [len(ss)]

RW = np.array(RW); ADV = np.array(ADV); ADV_T = np.array(ADV_T)
RET = np.array(RET); VAL = np.array(VAL)


def d(name, x):
    print(f"  {name:<22} n={len(x):>6}  均值 {x.mean():>+9.4f}  标准差 {x.std():>8.4f}"
          f"  |均值|/标准差 {abs(x.mean()) / (x.std() + 1e-12):>6.3f}"
          f"  p1/p50/p99 {np.percentile(x,1):>+7.3f}/{np.median(x):>+7.3f}/{np.percentile(x,99):>+7.3f}")


print("=== 逐步量的真实分布（日志里是 `:.3f` 量化过的，这里不量化）===")
d("奖励 reward", RW)
d("优势 adv", ADV)
d("  └ 终端贡献", ADV_T)
d("  └ 塑形贡献", ADV - ADV_T)
d("回报 ret", RET)
d("价值 value", VAL)
print()
nz = np.mean(np.abs(RW) > 1e-9)
big = np.mean(np.abs(RW) > 0.5)
print(f"  奖励非零占比 {nz:.1%}；|奖励|>0.5（终端那一击）占比 {big:.2%}")
print(f"  终端贡献占优势方差：{ADV_T.var() / (ADV.var() + 1e-12):.1%}"
      f"   塑形占：{(ADV - ADV_T).var() / (ADV.var() + 1e-12):.1%}")
print(f"  ★ 每方步数中位 {int(np.median(NST))} 步")

# ---- 终端那一击能往回传多远：GAE 权重 (1-λ)(γλ)^k ----
gl = 0.99 * 0.95
w = np.array([(1 - 0.95) * gl ** k for k in range(200)])
cum = np.cumsum(w) / w.sum()
for k in (10, 20, 30, 50, 100):
    print(f"     终点往前 {k:>3} 步：累计承载 {cum[k - 1]:>5.1%} 的终端信号")
print()
for info, n in eps:
    print(f"  局：{n:>4} 步  {info['turns']:>3} 回合  胜方 {info.get('winner') or '平'}"
          f"  截断={info['truncated']}")

# ================================================================ ★★ γ 对照
# 诊断：视野 `1/(1-γ)=100 步` 而一局 ~900 步 ⇒ 前 89% 的步拿不到终局信号。
# 证法（**离线、不用训练**）：同一批局、同一份 value，只换 γ 重算优势，
# 看**优势与"这方赢没赢"的相关性**在各段位置上起不起来。
print("\n\n=== ★★ γ 对照：优势与「这方赢没赢」的相关性（按位置分四段）===")
print("（`+1/0/-1` = 赢/平/输；相关系数按段算。视野 = 1/(1-γ) 步）\n")
RECS = []       # (相对位置 0..1, 每方优势表, 结局)
for g in range(NG):
    size = SMIN + (g % (SMAX - SMIN + 1))
    sb = Sandbox(seed=9000 + g, size=size, t_max=100, n_nations=2,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    for n in nets.values():
        n.eval()
    steps, info = T.collect_episode(nets, sb, rng=np.random.default_rng(g))
    if not steps:
        continue
    wm = set(info.get("winner_members") or ())
    for me in sb.players:
        ss = [s for s in steps if s.player == me]
        if len(ss) < 10:
            continue
        out = 1.0 if me in wm else (0.0 if not wm else -1.0)
        RECS.append((ss, out))

for gam in (0.99, 0.999, 0.9995):
    row = []
    for lo, hi in ((0.0, .25), (.25, .5), (.5, .75), (.75, 1.0)):
        xs, ys = [], []
        for ss, out in RECS:
            rw = [s.reward for s in ss]
            va = [s.value for s in ss]
            dn = [s.done for s in ss]
            adv, _ = T.gae(rw, va, dn, boots=[s.boot for s in ss], gamma=gam)
            n = len(ss)
            for t in range(int(lo * n), int(hi * n)):
                xs.append(adv[t]); ys.append(out)
        xs = np.array(xs); ys = np.array(ys)
        c = (np.corrcoef(xs, ys)[0, 1] if xs.std() > 0 and ys.std() > 0 else 0.0)
        row.append(c)
    print(f"  γ={gam:<7} 视野≈{1 / (1 - gam):>5.0f} 步   "
          + "  ".join(f"第{i + 1}段 r={c:>+6.3f}" for i, c in enumerate(row)))

print("\n★ 读法：若 γ=0.99 时**第 1 段**（前 25%）的 r ≈ 0，而 γ=0.999 明显抬起来"
      "\n  ⇒ 诊断成立（早期的步在数学上就没有终局信号）⇒ 该调 γ。"
      "\n★ 注意：这是**离线重算**，value 仍是老 γ 下训的 —— 只回答了"
      "\n  「γ 换成多少能把信号送到早期」，不回答「训起来会怎样」。")

# ================================================================ ★★ 优势的自相关
# 「学得到吗」的直接读数：PPO 的梯度是 `优势 × ∇logp`。若相邻步的优势**互不相关**
# （白噪声），那"这一串走法是好是坏"就无从累积，梯度平均下来趋零 ⇒ 策略不动。
print("\n\n=== ★★ 优势沿轨迹的自相关（lag=1..5）===")
acs = {k: [] for k in range(1, 6)}
for ss, _out in RECS:
    adv, _ = T.gae([s.reward for s in ss], [s.value for s in ss],
                   [s.done for s in ss], boots=[s.boot for s in ss])
    a = np.asarray(adv, np.float64)
    a = a - a.mean()
    d = (a * a).sum()
    if d <= 0:
        continue
    for k in acs:
        acs[k].append(float((a[:-k] * a[k:]).sum() / d))
for k, v in acs.items():
    v = np.array(v)
    print(f"  lag={k}: 中位 {np.median(v):>+6.3f}   均值 {v.mean():>+6.3f}"
          f"   （n={len(v)} 条轨迹）")
print("\n★ 读法：**白噪声 ⇒ ≈0**。若 5 个 lag 都在 ±0.05 内，说明一步一个样、"
      "\n  没有可累积的『这条线走得好不好』 —— 那 PPO 的梯度就是噪声，"
      "\n  策略只会被熵项推平。反之若明显为正（比如 0.5+），说明信号是连续的。")
