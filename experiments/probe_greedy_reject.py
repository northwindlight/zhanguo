# -*- coding: utf-8 -*-
"""贪心路径上到底在点什么？—— 回答"采样平、贪心塌"这个组合。

## 为什么要问这个

`evaluate()` 报两个数：贪心（argmax）和采样。200+ 局里**采样几乎不动**
（11,494 → 10,913），而**贪心单调下滑**（12,494 → 7,735）。同一份权重、
同一个目标函数，两条读数走势相反 —— 必须解释。

**专家 2026-09-13 给的假设之一**：贪心选中的动作**恰好是"合法但会被引擎拒"**的
那一类。我们有旁证：`probe_exec_head.py` 实测
**AUC(策略自己的 logits, 真可执行性) = 0.217** —— 策略的偏好与"能不能点动"
**反相关**。那么 argmax 取的那个动作，很可能正是最点不动的。

**判据（专家给的预期值）**：
- 若这是主因 ⇒ 贪心路径的**拒绝率 > 20~30%**，且即时 reward ≤ 0
- 若拒绝率 < 5~10% 且即时消费增量正常 ⇒ **排除**这条

## 本探针做什么

同一批种子（配对），跑两档 —— **贪心** vs **采样** —— 每步记：
动作类型、`info["ok"]`、累计消费。
再按回合分段报消费曲线，看贪心是**从哪一步开始跑偏**。

用法：python experiments/probe_greedy_reject.py <ckpt> [episodes] [turns]
"""
import sys
import collections

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 200
MARKS = [25, 50, 100, TURNS]          # 消费曲线的取样回合

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
_miss, _ = m.load_state_dict(ck["model"], strict=False)
if _miss:
    print(f"（缺失 {len(_miss)} 项，随机初始化：{sorted(_miss)}）")
m.eval()
print(f"权重 {CKPT}（第 {ck.get('iter', '?')} 块）")
print(f"{EPS} 局 × {TURNS} 回合；两档**同种子配对**\n")


def run(deterministic: bool):
    """跑 EPS 局，返回逐局统计。★两档必须用同样的 reset 种子才是配对。"""
    out = []
    for ep in range(EPS):
        env.reset(200 + ep)                  # 与 probe_validity 同款种子
        acc = collections.Counter()
        curve = {}
        while True:
            # ★`_obs()` 只取一次：act 用的和下面 step 用的必须是**同一帧**
            #   （rl/train.py 里立过的规矩 —— 分开取会让两者指向不同状态）
            obs = env._obs()
            idx, _lp, _v = act(m, obs, deterministic=deterministic,
                               win=tokenize(env, obs))
            kind = obs.cand["actions"][idx].kind
            obs, r, done, info = env.step(obs.cand["actions"][idx])
            acc["n"] += 1
            acc["rej"] += (not info["ok"])
            acc[f"k:{kind}"] += 1
            acc[f"krej:{kind}"] += (not info["ok"])
            if info["turn"] in MARKS and info["turn"] not in curve:
                curve[info["turn"]] = info["spend_total"]
            if done:
                break
        s = env.summary()
        out.append((acc, curve, s))
    return out


summ = {}
for mode, label in ((True, "贪心（argmax）—— evaluate 的 eval_spend"),
                    (False, "采样 —— evaluate 的 eval_s_spend")):
    print(f"--- {label} ---")
    res = run(mode)
    tot_n = sum(a["n"] for a, _, _ in res)
    tot_rej = sum(a["rej"] for a, _, _ in res)
    kinds = collections.Counter()
    krej = collections.Counter()
    for a, _, _ in res:
        for k, v in a.items():
            if k.startswith("k:"):
                kinds[k[2:]] += v
            elif k.startswith("krej:"):
                krej[k[5:]] += v
    for ep, (a, curve, s) in enumerate(res):
        print(f"  局{ep}:  步数{a['n']:>5}  **被拒 {a['rej'] / max(1, a['n']):.1%}**"
              f"  消费 {s['spend_total']:>8,.0f}  地 {s['tiles']:>4}  军 {s['armies']:>3}")
    print(f"  ── **总拒绝率 {tot_rej / max(1, tot_n):.1%}**（{tot_rej}/{tot_n}）")
    print(f"     消费曲线 " + "  ".join(f"T{t}:{sum(c.get(t, 0) for _, c, _ in res) / len(res):,.0f}"
                                       for t in MARKS))
    top = kinds.most_common(5)
    print("     选中动作 Top5：" + "  ".join(
        f"{k} {v}（拒 {krej[k] / v:.0%}）" for k, v in top))
    print()
    summ[mode] = dict(rej=tot_rej / max(1, tot_n),
                      spend=sum(s["spend_total"] for _, _, s in res) / len(res),
                      tiles=sum(s["tiles"] for _, _, s in res) / len(res))

if len(summ) == 2:
    a, b = summ[True], summ[False]
    print("=== 两档之差 ===")
    print(f"  拒绝率   {a['rej']:.1%}（贪心） vs {b['rej']:.1%}（采样）")
    print(f"  消费     {a['spend']:,.0f}（贪心） vs {b['spend']:,.0f}（采样）")
    print(f"  领地     {a['tiles']:.1f} vs {b['tiles']:.1f}")
    print("\n判据：贪心拒绝率 >20~30% ⇒ 专家第 2 条成立（贪心专挑点不动的）；"
          "<5~10% ⇒ 排除。")
