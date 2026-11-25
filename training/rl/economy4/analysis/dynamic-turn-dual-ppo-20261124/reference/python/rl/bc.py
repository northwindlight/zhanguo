"""阶段 0：规则 AI 示范上的行为克隆（带回合级 GRU 记忆 + 辅助监督）。只为合法性/基本经济的起点，不追求拟合老师。
`python3 -m rl.bc --games 300 --workers 12 --out runs/bc1`"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import time

import numpy as np


def _job(args):
    """一局 → 被录座位的列表 [(comp, xs, A, Mk, r, info)]。seat=-1 = 镜像两边同时录。"""
    seed, size, turns, opp, teacher, seat = args
    from . import demo
    seats = (0, 1) if seat < 0 else (seat,)
    out = []
    for r in demo.collect_seats(seed, size, turns, opp, teacher, seats=seats):
        comp = [({k: (v.astype(np.float16) if v.dtype == np.float32 else v) for k, v in o.items()}, i, ti)
                for o, i, ti in r["decs"]]
        out.append((comp, r["xs"], r["A"], r["Mk"], r["reward"], dict(r["info"], teacher=teacher, opp=opp)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--size", type=int, default=13)
    ap.add_argument("--turns", type=int, default=120)
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--mb", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--d", type=int, default=192)
    ap.add_argument("--layers", type=int, default=4)
    ap.add_argument("--arch", default="full", choices=["full", "xattn"], help="xattn：编码器只跑局面，候选用交叉注意力读局面")
    ap.add_argument("--cand-layers", type=int, default=1)
    ap.add_argument("--cand-self", action="store_true", help="xattn 候选块内先做候选间自注意力")
    ap.add_argument("--exec-prior", type=float, default=0.5)
    ap.add_argument("--aux-w", type=float, default=0.0)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--out", default="runs/bc1")
    ap.add_argument("--teachers", default="rule", help="老师轮换，逗号分隔：rule,w1,w1b,w2")
    ap.add_argument("--opps", default="rule,w1,w1b", help="对手轮换，逗号分隔")
    ap.add_argument("--wins-only", action="store_true", help="只保留老师灭国获胜的对局")
    ap.add_argument("--demos", default="", help="从 rl.collect 落盘的目录读示范（不现场采集）；逗号分隔多个目录")
    ap.add_argument("--seats", default="0", help="老师座位轮换，逗号分隔；-1 = 镜像两边同时录（w3 打 w3，只留赢家）")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ctx = mp.get_context("fork")
    opps, teachers, seats = a.opps.split(","), a.teachers.split(","), [int(x) for x in a.seats.split(",")]
    jobs = [(10_000 + i, a.size, a.turns, opps[i % len(opps)], teachers[(i // len(opps)) % len(teachers)],
             seats[(i // (len(opps) * len(teachers))) % len(seats)]) for i in range(a.games)]
    import collections
    tally = collections.Counter()
    t0 = time.time()
    eps = []                                  # 每局：(decs[(obs,idx,ti)], xs, A, Mk)
    rewards = []
    with ctx.Pool(a.workers) as pool:
        if a.demos:
            from .collect import load_dir
            flat = (ep for d in a.demos.split(",") for ep in load_dir(d))
        else:
            flat = (ep for res in pool.imap_unordered(_job, jobs) for ep in res)
        for k, (comp, xs, A, Mk, r, info) in enumerate(flat):
            won = info["winner"] == info["seat"]
            tally[(info["teacher"], info["opp"], info["seat"], "win" if won else "no")] += 1
            if a.wins_only and not won:
                continue
            eps.append((comp, xs, A, Mk))
            rewards.append(r)
            if (k + 1) % 20 == 0:
                print(f"采集 {k+1}/{a.games} 局, 决策 {sum(len(e[0]) for e in eps)}, 示范方平均奖励 {np.mean(rewards):.3f}, {time.time()-t0:.0f}s", flush=True)
    nd = sum(len(e[0]) for e in eps)
    print("老师×对手 胜/否：", dict(sorted(tally.items())), flush=True)
    print("保留对局", len(eps), "示范决策总数", nd, "采集用时", round(time.time() - t0), "s", flush=True)

    import torch
    import torch.nn.functional as F
    from .model import Policy, aux_loss
    from .obs import F_DIM, M_AUX, collate
    dev = a.device
    AC = lambda: torch.autocast(device_type=("cuda" if dev == "cuda" else "cpu"), dtype=torch.bfloat16, enabled=(dev == "cuda"))
    model = Policy(a.d, a.layers, exec_prior_weight=a.exec_prior, arch=a.arch, cand_layers=a.cand_layers, cand_self=a.cand_self).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)
    n_val = max(1, len(eps) // 10)
    tr_eps, va_eps = eps[n_val:], eps[:n_val]          # 按局切分（记忆是按局的，不能把同一局拆到两边）

    def pack(group):
        E = len(group)
        Tm = max(len(g[1]) for g in group)
        X = np.zeros((E, Tm, F_DIM), np.float32); AUX = np.zeros((E, Tm, M_AUX), np.float32); AM = np.zeros((E, Tm), bool)
        items = []
        for e, (decs, xs, A, Mk) in enumerate(group):
            X[e, : len(xs)] = xs; AUX[e, : len(xs)] = A; AM[e, : len(xs)] = Mk
            items += [(e, o, i, ti) for o, i, ti in decs]
        return [torch.from_numpy(z).to(dev) for z in (X, AUX, AM)], items

    def to_dev(b):
        return {k: torch.from_numpy(v).float().to(dev) if v.dtype == np.float16 else torch.from_numpy(v).to(dev) for k, v in b.items()}

    def run(group, train):
        (Xg, AUXg, AMg), items = pack(group)
        order = np.random.permutation(len(items)) if train else np.arange(len(items))[:6000]
        tot = dict(ce=0., ex=0., acc=0., aux=0., n=0)
        for i in range(0, len(order), a.mb):
            sel = order[i: i + a.mb]
            tb = to_dev(collate([items[j][1] for j in sel]))
            tgt = torch.tensor([items[j][2] for j in sel], device=dev)
            ei = torch.tensor([items[j][0] for j in sel], device=dev)
            ti = torch.tensor([items[j][3] for j in sel], device=dev)
            with torch.set_grad_enabled(train):
                Hs = model.mem_seq(Xg)                                  # 整局序列 BPTT（fp32）
                tb["mem"] = Hs[ei, ti]
                aux_l, aux_rel = aux_loss(model.mem_aux(Hs), AUXg, AMg)
                with AC():
                    logits, exl, _ = model(tb)
            logits, exl = logits.float(), exl.float()
            ce = F.cross_entropy(logits, tgt)
            m = tb["cand_mask"]
            ex = (F.binary_cross_entropy_with_logits(exl, tb["cand_exec"], reduction="none") * m).sum() / m.sum()
            if train:
                opt.zero_grad(set_to_none=True)
                (ce + 0.5 * ex + a.aux_w * aux_l).backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
            k = len(sel)
            tot["ce"] += ce.item() * k; tot["ex"] += ex.item() * k; tot["aux"] += float(aux_rel.item()) * k
            tot["acc"] += (logits.argmax(-1) == tgt).float().sum().item(); tot["n"] += k
        return {k: v / tot["n"] for k, v in tot.items() if k != "n"}

    for ep in range(a.epochs):
        np.random.shuffle(tr_eps)
        model.train(); tr = run(tr_eps, True)
        model.eval(); va = run(va_eps, False)
        print(f"epoch {ep}: train ce {tr['ce']:.3f} acc {tr['acc']:.3f} | val ce {va['ce']:.3f} acc {va['acc']:.3f} exec_bce {va['ex']:.3f} aux_rel {va['aux']:.3f}", flush=True)
        torch.save(dict(model=model.state_dict(), cfg=model.cfg), os.path.join(a.out, "bc.pt"))
    print("saved", os.path.join(a.out, "bc.pt"))


if __name__ == "__main__":
    main()
