# -*- coding: utf-8 -*-
"""熵涨 ⇒ 消费掉，中间是不是"概率质量被推向市场"？—— 验 ECS 2026-09-14 的机制链。

## 机制（ECS 读引擎后推导）

1. 候选集里**市场动作是固定 120 个**（6 物资 × 10 数量档 × 买/卖），
   不按钱/货过滤 ⇒ 开局占 **72~74%**，中期仍有 **44~55%**。
2. 熵正则 `−Σp log p` 的**不动点是候选集上的均匀分布** ⇒ 均匀下 50~74% 的
   概率质量落在市场 ⇒ **熵正则对这个候选集不是中性的，它结构性偏好市场动作**。
3. 市场动作**不计入 `spend_total`**（买卖不是消费），成功的倒手还亏 ~9.5%。
4. ⇒ 熵越高 ⇒ 越多步花在市场 ⇒ 越少金流进建造 ⇒ **消费掉**。

## 已有读数与它对得上

- ckpt_35 的**非 build 成功/步反而升**（0.656→0.676）、**撞墙率反而降**
  —— 若多出来的成功主要是**成功的买卖**，就正是这条链
- §V.7 里 ppo_v9 块 50 的 **`sell` 占 46% 步数**
- **B 臂（`ent_coef=0`）比 A 臂好** —— 不是熵本身有害，是「这个候选集上的熵」= 往市场推

## 判据（ECS 给的，事先写死）

同种子 8 局 × 200 回合，BC / ckpt_5 / ckpt_35，逐局记：

- **成立**：ckpt_35 的**成功买+卖/回合**显著 > ckpt_5 ≈ BC，且 **build/回合下降**
- **不成立**：三者市场行为无差 ⇒ 熵涨发生在 build/move 内部，这条链作废

顺带一趟跑出**「忘了开局 vs 后期没学好」**的判别（ECS 也想要的）：
记第 **70** 回合与第 **200** 回合的累计消费。

用法：python experiments/probe_market_drift.py <ckpt> [ckpt...] [--episodes N] [--turns T]
"""
import sys
import collections

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act, policy_logits

EPS, TURNS, CKPTS = 8, 200, []
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--episodes":
        i += 1; EPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    else:
        CKPTS.append(a)
    i += 1
if not CKPTS:
    print(__doc__); sys.exit(2)

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
MARK = [70, TURNS]

print(f"{EPS} 局 × {TURNS} 回合；同种子配对（逐局重设 torch 种子）\n")
print(f"{'ckpt':<18}{'市场成功/回合':>14}{'build成功/回合':>15}{'撞墙率':>9}"
      f"{'T70消费':>10}{'T200消费':>10}")

for ck_path in CKPTS:
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(ck_path, map_location="cpu", weights_only=False)
    m.load_state_dict(ck["model"], strict=False)
    m.eval()

    agg = collections.Counter()
    t70, t_end = [], []
    pmkt, pbase = [], []                       # ★概率质量口径（ECS 2026-09-14 建议）
    for ep in range(EPS):
        torch.manual_seed(2000 + ep)          # 与 probe_validity 同款，保证配对
        obs = env.reset(900_000 + ep)         # 与评估同一批图
        turns_seen = 0
        while True:
            obs_ = obs
            win_ = tokenize(env, obs_)
            # ★最直接的量：**概率质量落在市场候选上的比例**（不是行为结果）。
            #   行为结果会被"买不起/卖不出"过滤掉一层；概率质量才是策略的偏好本身。
            with torch.no_grad():
                lg, _v, cm = policy_logits(m, obs_, win=win_, use_exec=False)
                p = torch.softmax(lg[0].masked_fill(~cm[0], float("-inf")), dim=-1)
                kinds = [c.kind for c in obs_.cand["actions"]]
                mkt_idx = [i for i, k in enumerate(kinds) if k in ("buy", "sell")]
                valid = int(cm[0].sum())
                pmkt.append(float(p[mkt_idx].sum()))
                pbase.append(len(mkt_idx) / max(1, valid))   # 均匀基线
            idx, _lp, _v2 = act(m, obs_, win=win_)
            a = obs_.cand["actions"][idx]
            kind = a.kind
            obs, _r, done, info = env.step(a)
            agg["n"] += 1
            if not info["ok"]:
                agg["rej"] += 1
            elif kind in ("buy", "sell"):
                agg["mkt"] += 1                # ★成功的市场动作（买或卖）
            elif kind == "build":
                agg["build"] += 1
            turns_seen = info["turn"]
            if turns_seen == MARK[0] and "t70" not in agg:
                agg["t70"] = info["spend_total"]
            if done:
                t_end.append(info["spend_total"])
                break
        t70.append(agg.get("t70", float("nan")))
        agg.pop("t70", None)

    n = max(1, agg["n"])
    per = lambda k: agg[k] / EPS                   # 每局
    Pm = sum(pmkt) / len(pmkt)
    Pb = sum(pbase) / len(pbase)
    print(f"{ck_path.split('/')[-1]:<18}{per('mkt'):>14.0f}{per('build'):>15.0f}"
          f"{agg['rej'] / n:>8.1%}{sum(t70) / EPS:>10,.0f}{sum(t_end) / EPS:>10,.0f}")
    print(f"  步数/局 {agg['n'] / EPS:.0f}　市场 {per('mkt'):.0f} = 步数的 "
          f"{per('mkt') / (agg['n'] / EPS):.1%}")
    print(f"  ★P_mkt（概率质量落在市场）= {Pm:.3f}　均匀基线 {Pb:.3f}"
          f"　→ 偏好度 {Pm / max(1e-9, Pb):.2f}×")
