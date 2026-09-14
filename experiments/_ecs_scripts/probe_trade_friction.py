# -*- coding: utf-8 -*-
"""J1：**逐笔实录**市场交易的真实金摩擦（点差+冲击+取整一起量），不依赖解析式。

## 与 Pi 的 `probe_roundtrip_gold.py`（J0，纯点差下界）的关系
- J0：往返件数 × 结算时 mid × 0.10 —— 只算点差、且 mid 用在回合末均衡价，**是下界**。
- **J1（本脚本）**：每笔成功的 buy/sell，记
  `p0 = 成交前 world.prices[good]`、`成交后 p1`、`实付/实收`（= 成交前后国库黄金差，逐笔只有市场动金，
  买/卖不推回合 ⇒ 差分干净）。**单笔摩擦 = |实扣/实收 − n×p0|**，天然含 点差(±5%) + 冲击((p1−p0)/2) + int(round) 尾差。
  再按解析式把三者拆开报（冲击 = n×|p1−p0|/2；点差 = n×(p0+p1)/2×0.10；残差 = 总摩擦 − 两者）。

## 空转归属（公式，写进报告头，Pi 口径 min=同回合同物资）
`空转份额_g,局 = 同回合往返件数(g) / 该物资该局总买入件数`（买入=0 ⇒ 份额=0）
⇒ **空转摩擦 = Σ_g,局 份额 × Σf(g)**（"share 法"）；另报 **逐笔法**：只加总"卖出回合内买回"能明确配对的回合的两侧摩擦（保守下界）。
两法都给，取 max 报"合计"。

## 口径（必须与 J0/验收同批，门槛由 J0 把）
图 `reset(900000+ep)`、种子 `manual_seed(2000+ep)`、turns=200、`use_exec=False`（act 默认）。

用法：python probe_trade_friction.py <ckpt> [--episodes 8] [--turns 200] [--csv 路径]

## v1.1（Pi 13:00 点名要的两列，零额外机时顺带报）
每局结束记 **局末剩金**（world.res(agent,黄金)）与 **库存折金**（六可交易物资存量，各按
基准价 MARKET[g] 与局末市价 prices[g] 两口径——原始两列都给，取哪个归 Pi）。
老师贴 0 金跑 ⇒ "没花出去"（剩金+库存）可能比"被烧掉"（空转）重要一个量级。
"""
import collections
import sys

import torch

from game import MARKET, MARKET_SPREAD
from rl.env import KINDS, ZhanguoEnv
from rl.ppo import act
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

EPS, TURNS = 8, 200
CKPTS = []
CSV = ""
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--episodes":
        i += 1; EPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--csv":
        i += 1; CSV = sys.argv[i]
    else:
        CKPTS.append(a)
    i += 1
if not CKPTS:
    print(__doc__); sys.exit(2)

NAME = {"粮": "粮食", "木": "木头", "铁": "矿石", "马": "石油", "油": "石油"}

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())

for ck_path in CKPTS:
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(ck_path, map_location="cpu", weights_only=False)
    m.load_state_dict(ck["model"], strict=False)
    m.eval()

    trades = []           # (ep, turn, good, side, n, p0, p1, ideal, settled, fric, impact, spreadf, roundf)
    leftovers = []        # (局末剩金, 库存折基准价, 库存折局末市价)
    rt = collections.Counter()          # (ep, good) -> 同回合往返件数（J0 同款结算逻辑）
    bought = collections.Counter()      # (ep, good) -> 买入件数
    sold = collections.Counter()
    ep_spend = []
    for ep in range(EPS):
        torch.manual_seed(2000 + ep)
        obs = env.reset(900_000 + ep)
        cur, tbuy, tsell = None, {}, {}
        while True:
            win = tokenize(env, obs)
            t_now = env.world.turn
            if t_now != cur:
                if cur is not None:
                    for g, b in tbuy.items():
                        u = min(b, tsell.get(g, 0))
                        if u:
                            rt[(ep, g)] += u
                cur, tbuy, tsell = t_now, {}, {}
            idx, _lp, _v = act(m, obs, win=win)
            a = obs.cand["actions"][idx]
            kind = a.kind
            if kind in ("buy", "sell"):
                g = NAME.get(a.sub, a.sub)
                p0 = float(env.world.prices.get(g, MARKET.get(g, 0.0)))
                gold0 = float(env.world.res(env.agent, "黄金"))
                obs, _r, done, info = env.step(a)
                ok = bool(info.get("ok"))
                gold1 = float(env.world.res(env.agent, "黄金"))
                if ok:
                    p1 = float(env.world.prices.get(g, p0))
                    delta = gold1 - gold0
                    ideal = a.amount * p0
                    if kind == "buy":
                        fric = -delta - ideal            # 实付 − n·p0
                        bought[(ep, g)] += a.amount
                        tbuy[g] = tbuy.get(g, 0) + a.amount
                    else:
                        fric = ideal - delta             # n·p0 − 实收
                        sold[(ep, g)] += a.amount
                        tsell[g] = tsell.get(g, 0) + a.amount
                    impact = a.amount * abs(p1 - p0) / 2
                    spreadf = a.amount * (p0 + p1) / 2 * (MARKET_SPREAD / 2)
                    trades.append((ep, t_now, g, kind, a.amount, p0, p1, ideal,
                                   abs(delta), fric, impact, spreadf, fric - impact - spreadf))
            else:
                obs, _r, done, info = env.step(a)
            if done:
                ep_spend.append(float(info["spend_total"]))
                ag = env.agent
                wg = float(env.world.res(ag, "黄金"))
                inv_b = inv_m = 0.0
                for g2 in ("粮食", "木头", "矿石", "石油", "装备", "补给"):
                    q = float(env.world.res(ag, g2))
                    inv_b += q * MARKET.get(g2, 0)
                    inv_m += q * float(env.world.prices.get(g2, 0))
                leftovers.append((wg, inv_b, inv_m))
                break

    tot_f = sum(t[9] for t in trades)
    tot_imp = sum(t[10] for t in trades)
    tot_spr = sum(t[11] for t in trades)
    tot_rnd = sum(t[12] for t in trades)
    # share 法空转摩擦：逐 (ep,good)
    share_fric = 0.0
    for (ep, g), r in rt.items():
        f_g = sum(t[9] for t in trades if t[0] == ep and t[2] == g)
        b_g = bought.get((ep, g), 0)
        if b_g:
            share_fric += f_g * r / b_g
    n_units = sum(rt.values())
    n_tr = len(trades)
    ts = sum(ep_spend)
    print(f"===== {ck_path}（第 {ck.get('iter','?')} 块）J1 逐笔摩擦 =====")
    print(f"  成功交易笔数 {n_tr}（{n_tr/EPS:.1f}/局）；总买入 {sum(bought.values()):,.0f} 件、卖出 {sum(sold.values()):,.0f} 件")
    print(f"  全部交易总摩擦 {tot_f:,.0f} 金（{tot_f/EPS:.1f}/局） = 冲击 {tot_imp:,.0f} + 点差 {tot_spr:,.0f} + 取整 {tot_rnd:,.0f}（残差 {tot_f-tot_imp-tot_spr-tot_rnd:+.2f}）")
    print(f"  同回合往返件数 {n_units:,.0f}（{n_units/EPS:.1f}/局）—— **对 J0 门槛：应为 2936.8±0.1**")
    print(f"  share 法空转摩擦 ≈ {share_fric:,.0f} 金（{share_fric/EPS:.1f}/局 = 终局消费的 {share_fric/ts:.2%}）")
    print(f"  终局消费均值 {ts/EPS:,.0f}（J0 同批 ECS 侧基线 14,163）")
    if leftovers:
        import statistics as stx
        wgm = stx.mean(x[0] for x in leftovers)
        ibm = stx.mean(x[1] for x in leftovers)
        imm = stx.mean(x[2] for x in leftovers)
        print(f"  局末剩金 {wgm:,.0f} + 库存折金（基准价）{ibm:,.0f} = {wgm + ibm:,.0f} 金/局"
              f"（占终局消费 {(wgm + ibm) / (ts / EPS):.2%}）   库存折局末市价 {imm:,.0f}")
        print("  逐局(剩金, 折基准, 折末价)：" + "  ".join(f"({a:,.0f},{b:,.0f},{c:,.0f})" for a, b, c in leftovers))
    print("  逐物资：件数/摩擦/冲击/点差")
    per_g = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0])
    for t in trades:
        pg = per_g[t[2]]
        pg[0] += t[4]; pg[1] += t[9]; pg[2] += t[10]; pg[3] += t[11]; pg[4] += 1
    for g, (u, f, im, sp, k) in sorted(per_g.items(), key=lambda x: -x[1][1]):
        print(f"    {g:<4} 件 {u:>8,.0f}  摩擦 {f:>9,.1f}（冲击 {im:>8,.1f} / 点差 {sp:>8,.1f}）笔数 {k}")
    if CSV:
        import csv as _csv
        with open(CSV, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["ep", "turn", "good", "side", "n", "p0", "p1", "ideal", "settled",
                        "friction", "impact", "spread", "rounding"])
            w.writerows(trades)
        print(f"  逐笔 CSV：{CSV}（{len(trades)} 行）")
