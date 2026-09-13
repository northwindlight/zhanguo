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


def _turn(env):
    return int(env.world.turn)


def _res(env):
    return env.world.nations[env.agent].res


def _price(env, g):
    return float(env.world.prices.get(g, 0.0))

ALLPER = {}
print(f"{EPS} 局 × {TURNS} 回合；同种子配对（逐局重设 torch 种子）\n")
print(f"{'ckpt':<18}{'市场成功/局':>13}{'build成功/局':>14}{'撞墙率':>9}"
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
    # ★逐局留存：ECS 要的配对符号检验 + 分栏（切法 A）都要它
    PER = collections.defaultdict(list)
    for ep in range(EPS):
        torch.manual_seed(2000 + ep)          # 与 probe_validity 同款，保证配对
        obs = env.reset(900_000 + ep)         # 与评估同一批图
        turns_seen = 0
        cur_turn, turn_buy, turn_sell = None, {}, {}
        ep_build = ep_mkt = ep_rt = 0          # 本局计数（切法 A 用）
        ep_curve = {}                          # 第 20/40/70 回合的累计消费
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
            # ★W1：换回合时结算上一回合的**同物资往返量**（老师结构上恒为 0）
            t_now = _turn(env)
            if t_now != cur_turn:
                if cur_turn is not None:
                    _rt = sum(min(b, turn_sell.get(g, 0))
                              for g, b in turn_buy.items())
                    agg["rt"] += _rt
                    ep_rt += _rt
                cur_turn, turn_buy, turn_sell = t_now, {}, {}
            idx, _lp, _v2 = act(m, obs_, win=win_)
            a = obs_.cand["actions"][idx]
            kind = a.kind
            obs, _r, done, info = env.step(a)
            agg["n"] += 1
            if not info["ok"]:
                agg["rej"] += 1
            elif kind in ("buy", "sell"):
                agg["mkt"] += 1                # ★成功的市场动作（买或卖）
                ep_mkt += 1
                if kind == "buy":
                    turn_buy[a.sub] = turn_buy.get(a.sub, 0) + a.amount
                    if a.sub == "补给":
                        agg["sup_buy"] += a.amount      # ★W2：买补给（用户点名的那条）
                else:
                    turn_sell[a.sub] = turn_sell.get(a.sub, 0) + a.amount
            elif kind == "build":
                agg["build"] += 1
                ep_build += 1
            turns_seen = info["turn"]
            if turns_seen in (20, 40, 70) and turns_seen not in ep_curve:
                ep_curve[turns_seen] = info["spend_total"]
            if turns_seen == MARK[0] and "t70" not in agg:
                agg["t70"] = info["spend_total"]
            if done:
                t_end.append(info["spend_total"])
                # ★W2/W3：局末补给存量 + 库存折金 + 剩余金
                me = env.agent
                res = _res(env)
                agg["sup_end"] += res.get("补给", 0)
                agg["gold_end"] += res.get("黄金", 0)
                agg["inv_val"] += sum(res.get(g, 0) * _price(env, g)
                                      for g in ("粮", "木", "铁", "马", "装备", "补给"))
                break
        t70.append(agg.get("t70", float("nan")))
        agg.pop("t70", None)
        PER["build"].append(ep_build)
        PER["mkt"].append(ep_mkt)
        PER["rt"].append(ep_rt)
        for t in (20, 40, 70):
            if t in ep_curve:
                PER[f"T{t}"].append(ep_curve[t])

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
    print(f"  ★W1 同回合往返量/局 = {per('rt'):.1f}（**老师结构上恒为 0**，任何 >0 都是相对老师的纯浪费）")
    print(f"  ★W2 买补给/局 = {per('sup_buy'):.0f}　局末补给存量 = {per('sup_end'):.0f}"
          f"　⇒ 买入 ≫ 吃掉+缓冲 就是囤积")
    print(f"  ★W3 局末库存折金 = {per('inv_val'):,.0f}　局末剩余金 = {per('gold_end'):,.0f}"
          f"（老师贴 0 金跑）")
    ALLPER[ck_path.split("/")[-1]] = {k: list(v) for k, v in PER.items()}

# ★配对符号检验（ECS 2026-09-14 要的显著性）：逐局比，数同向的局数
if len(ALLPER) >= 2:
    keys = list(ALLPER)
    print("\n===== 配对符号检验（逐局同向计数；8/8 ⇒ p≈0.008，6/8 只算倾向）=====")
    for a, b in zip(keys, keys[1:]):
        A, B = ALLPER[a], ALLPER[b]
        for m in ("build", "mkt", "rt", "T20", "T40", "T70"):
            if m not in A or m not in B:
                continue
            n = min(len(A[m]), len(B[m]))
            up = sum(1 for i in range(n) if B[m][i] > A[m][i])
            dn = sum(1 for i in range(n) if B[m][i] < A[m][i])
            print(f"  {a} → {b}　{m:>6}: 升 {up}/{n}　降 {dn}/{n}"
                  f"　（均值 {sum(A[m][:n]) / n:,.0f} → {sum(B[m][:n]) / n:,.0f}）")
    print("  ★切法 A（开局前 20 回合构成天然相同）：看 T20 那一行 ——"
          "\n    若 T20 已显著下降 ⇒ 策略本身变了（原因侧）；持平 ⇒ 帝国小 ⇒ 构成变（结果侧）")
