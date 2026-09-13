# -*- coding: utf-8 -*-
"""诊断：熵涨到底是「概率摊到点不动的候选上」，还是「合法集内真的变随机」？

专家 2026-09-13 给的判据：
    p_inv  = 概率质量落在**不可执行**候选上的比例（随机基线 ≈ 死选项占比）
    H_all  = 全部候选的熵
    H_val  = **valid 内重归一化**后的熵（q = p / Σ_valid p）
             ★只 mask 不重归一会低估，必须报重归一化熵

    判定：H_all↑ 但 H_val 平/↓，且 Δ(H_all−H_val) > 0.1 nat 或 corr(H_all, p_inv) > 0.6
          ⇒ **死选项泄漏**
          H_val 也↑ > 0.1 nat ⇒ **合法集内真随机**

★**本探针的便宜版本**（原方案要逐候选调引擎，184 个候选 × 每步 = deepcopy 184 次，太贵）：
   - `p_inv` 用**实际轨迹估计**：采样出来的动作 `env.step` 就返回 `ok`
     ⇒ 撞墙率 = 「采样落在死选项上的比例」的**无偏估计**，零额外成本。
   - `H_all` 从 logits 直接算，也便宜。
   - `H_val` 略去 —— 它需要"哪些候选此刻可执行"的全集，那正是最贵的那一步。
     **少了它就无法排除"合法集内真随机"，但 `corr(H_all, 撞墙率)` 已能指向泄漏。**

★★**2026-09-13 晚加的第二档：开关对照**（用户口径）

原版只测一档，用的是 `act()` 的默认 `use_exec=False` —— 而**训练时是开着软加权的**
（`train.py` 里 `use_exec=args.exec_head > 0`）。⇒ 原版答的是
「**主干自己学没学会避墙**」，**不是**「带权重后的实际行为」。这两件事必须分开：

    裸策略（`use_exec=False`）  = 主干自己学到了多少
    带软加权（`use_exec=True`） = 训练/推理时**实际**跑的那条策略

两档用**同一批种子**跑（配对对照，地图完全相同），差值才是"软加权到底顶了多少"。
⚠ 若 ckpt 里 `exec_head` 是**随机初始化的**（加头之前的旧 ckpt），第二档无意义
—— 脚本会检测并警告。

用法：python experiments/probe_validity.py <ckpt> [episodes] [turns]
"""
import sys
import collections
import math

import torch
import torch.nn.functional as F

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import policy_logits

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 30

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)                      # `_terrain` 要等第一次 reset 才建出来
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
# ★**必须 strict=False**：`exec_head` 是后加的，加头之前的 ckpt 没有这两个键。
#   用默认 strict=True 会让**所有旧权重都加载不了**、连基线都测不成
#   （2026-09-13 踩过：基线 ckpt_30 直接 RuntimeError，白跑一轮）。
_miss, _unexp = m.load_state_dict(ck["model"], strict=False)
if _miss:
    print(f"（缺失 {len(_miss)} 项，随机初始化：{sorted(_miss)}）")
if _unexp:
    print(f"（多余 {len(_unexp)} 项，已忽略：{sorted(_unexp)}）")
m.eval()

# `exec_head` 没训过 ⇒ 第二档拿随机头去加权，纯噪声，结论别信。
_exec_untrained = any(k.startswith("exec_head.") for k in _miss)

print(f"权重 {CKPT}   （训练到第 {ck.get('iter', '?')} 块）")
print(f"{EPS} 局 × {TURNS} 回合   采样档；两档同种子（配对对照）\n")


def run_mode(use_exec: bool):
    """跑一遍全部对局。★两档必须用**同样的 `torch.manual_seed`** 才是配对对照。"""
    rows = []
    for ep in range(EPS):
        torch.manual_seed(2000 + ep)      # 必须逐局重设：动作采样吃的是全局 torch RNG
        obs = env.reset(200 + ep)
        acc = collections.Counter()
        while True:
            win = tokenize(env, obs)
            # ★走 `policy_logits` 而不是自己算 —— 软加权公式只此一份（见 ppo.py）
            lg, _v, cm = policy_logits(m, obs, win=win, use_exec=use_exec)
            # H_all：全部**有效位**（非 padding）候选的熵
            p = torch.softmax(lg[0].masked_fill(~cm[0], float("-inf")), dim=-1)
            p = p[p > 0]
            h_all = float(-(p * p.log()).detach().sum())
            # 采样与 `act()` 逐字同款（同一个 multinomial 调用 ⇒ 同样消耗 RNG）
            logp = F.log_softmax(lg, dim=-1)
            idx = int(torch.multinomial(logp.exp(), 1).item())
            a = obs.cand["actions"][idx]
            kind = a.kind
            obs, _r, done, info = env.step(a)
            acc["n"] += 1
            acc["H"] += h_all
            if not info["ok"]:
                acc["撞"] += 1
            acc["build"] += (kind == "build" and info["ok"])
            acc["nonbuild_ok"] += (kind != "build" and info["ok"])
            if done:
                break
        n = max(1, acc["n"])
        rows.append((ep, acc["H"] / n, acc["撞"] / n,
                     acc["build"] / n, acc["nonbuild_ok"] / n))
    return rows


def corr(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx * dy else float("nan")


MODES = ((False, "裸策略（use_exec=False）—— 主干自己学到了多少"),
         (True, "带软加权（use_exec=True）—— 训练/推理时实际跑的那条"))

summary = {}
for use_exec, label in MODES:
    if use_exec and _exec_untrained:
        print(f"--- {label} ---")
        print("  ⚠ 跳过：这个 ckpt 的 exec_head 是**随机初始化**的"
              "（加头之前的旧权重），拿它加权等于加噪声，测了也不能信。\n")
        continue
    print(f"--- {label} ---")
    rows = run_mode(use_exec)
    for r in rows:
        print(f"  局{r[0]}:  H_all={r[1]:.3f}  撞墙率={r[2]:.1%}"
              f"  build成功/步={r[3]:.3f}  非build成功/步={r[4]:.3f}")
    N = len(rows)
    H = [r[1] for r in rows]
    rt = [r[2] for r in rows]
    bs = [r[3] for r in rows]
    nb = [r[4] for r in rows]
    q = max(1, N // 4)
    summary[use_exec] = dict(
        label=label, N=N, H=sum(H) / N, rt=sum(rt) / N, bs=sum(bs) / N,
        nb=sum(nb) / N, corr=corr(H, rt),
        early=sum(rt[:q]) / q, late=sum(rt[-q:]) / q)
    s = summary[use_exec]
    print(f"  ── H_all {s['H']:.3f} │ 撞墙率 {s['rt']:.1%} │ "
          f"build {s['bs']:.3f} │ 非build {s['nb']:.3f}")
    print(f"     ★corr(H_all, 撞墙率) = {s['corr']:+.2f}"
          f"   （> +0.6 ⇒ 熵涨主要来自死选项泄漏；≈0 ⇒ 合法集内变随机）")
    print(f"     前 25% → 后 25%：撞墙率 {s['early']:.1%} → {s['late']:.1%}"
          f"   （差 {s['late'] - s['early']:+.1%}，正=越玩越乱）\n")

if len(summary) == 2:
    a, b = summary[False], summary[True]
    print("=== 两档之差（带软加权 − 裸策略）===")
    print(f"  撞墙率    {a['rt']:+.1%} → {b['rt']:+.1%}"
          f"   （{b['rt'] - a['rt']:+.1%}；负 = 加权确实把动作从死选项上挪开了）")
    print(f"  非build   {a['nb']:.3f} → {b['nb']:.3f}   （{b['nb'] - a['nb']:+.3f}）")
    print(f"  H_all     {a['H']:.3f} → {b['H']:.3f}   （{b['H'] - a['H']:+.3f}）")
