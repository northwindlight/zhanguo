"""PPO（整局回报）：多进程造局 + GPU 批量推理 + 同步更新。

回报 = 终局 r_i = S_i / max_j S_j（plan.md §2.2），γ=1，每个决策点同一个回报；
优势 = R − V_old，批内标准化。损失 = PPO 裁剪 + value MSE（梯度缩放过的价值头）+ exec 辅助 BCE + 熵正则。
`python3 -m rl.train --workers 16 --games 48 --size 13 --max-turns 200 --minutes 30`
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import time
from multiprocessing.connection import wait

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--games", type=int, default=48, help="每次更新用的对局数")
    ap.add_argument("--size", type=int, default=13)
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--d", type=int, default=192)
    ap.add_argument("--layers", type=int, default=4)
    ap.add_argument("--arch", default="full", choices=["full", "xattn"], help="xattn：编码器只跑局面，候选用交叉注意力读局面")
    ap.add_argument("--cand-layers", type=int, default=1)
    ap.add_argument("--cand-self", action="store_true", help="xattn 候选块内先做候选间自注意力")
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--mb", type=int, default=128)
    ap.add_argument("--sub", type=float, default=0.3, help="每个 epoch 随机取这个比例的决策点训练")
    ap.add_argument("--clip", type=float, default=0.1)
    ap.add_argument("--ent", type=float, default=0.0, help="熵奖励（默认 0：熵含不可执行候选，会把概率推向无效动作）")
    ap.add_argument("--invalid-pen", type=float, default=0.05, help="无效动作（不可执行）的局部优势惩罚")
    ap.add_argument("--aux-w", type=float, default=0.0, help="记忆辅助损失（预测下一回合将离开视野的敌军）权重")
    ap.add_argument("--kl-ref", type=float, default=0.05, help="对 BC 参考策略的 KL 约束系数")
    ap.add_argument("--target-kl", type=float, default=0.03, help="每次更新内 KL 超过它就停止本次后续 minibatch")
    ap.add_argument("--exec-w", type=float, default=0.5)
    ap.add_argument("--exec-prior", type=float, default=0.5, help="软先验权重（不是掩码）：logits += w·log σ(exec_head)")
    ap.add_argument("--weights", type=str, default="0.25,0.3,0.25,0.2", help="对手池抽样权重 rule,w1_25_2,w1_40_3,w2")
    ap.add_argument("--out", type=str, default="runs/r0")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", type=str, default="cuda")
    ap.add_argument("--init", type=str, default="", help="从 BC/旧检查点初始化")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    weights = [float(x) for x in a.weights.split(",")]

    # ---- 先起 worker（fork），再碰 CUDA ----
    ctx = mp.get_context("fork")
    conns, procs = [], []
    from . import actors
    for w in range(a.workers):
        pc, cc = ctx.Pipe(duplex=True)
        p = ctx.Process(target=actors.worker_main, args=(cc, w, a.size, a.max_turns, a.seed * 100_000_007 + 17, weights), daemon=True)
        p.start()
        conns.append(pc)
        procs.append(p)

    import torch
    import torch.nn.functional as F
    from .model import Policy, aux_loss
    from .obs import F_DIM, M_AUX, collate
    dev = a.device
    AC = lambda: torch.autocast(device_type=("cuda" if dev == "cuda" else "cpu"), dtype=torch.bfloat16, enabled=(dev == "cuda"))
    torch.manual_seed(a.seed)
    model = Policy(a.d, a.layers, exec_prior_weight=a.exec_prior, arch=a.arch, cand_layers=a.cand_layers, cand_self=a.cand_self).to(dev)
    ref = None
    if a.init:
        model.load_state_dict(torch.load(a.init, map_location=dev)["model"])
        print("init from", a.init, flush=True)
        if a.kl_ref > 0:
            import copy
            ref = copy.deepcopy(model).eval()
            for p_ in ref.parameters():
                p_.requires_grad_(False)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.0)
    print("params(M)", sum(p.numel() for p in model.parameters()) / 1e6, flush=True)

    def to_dev(b):
        out = {}
        for k, v in b.items():
            t = torch.from_numpy(v)
            if t.dtype == torch.float16:
                t = t.float()
            out[k] = t.to(dev, non_blocking=True)
        return out

    buf = {w: dict(decs=[], xs=[], auxs=[]) for w in range(a.workers)}    # 进行中的对局
    hw = {w: torch.zeros(model.mem_h, device=dev) for w in range(a.workers)}   # 各 worker 的 GRU 隐状态
    done_games = []                                  # 已结束：(决策列表, 回报)
    stats = dict(decisions=0, games=0, t0=time.time(), last=time.time(), dec_window=0)
    log = open(os.path.join(a.out, "log.jsonl"), "a")
    t_end = time.time() + a.minutes * 60
    upd = 0

    def infer(reqs):
        batch = collate([r[2] for r in reqs])
        tb = to_dev(batch)
        with torch.no_grad():
            nt = [i for i, r in enumerate(reqs) if r[3]["xt"] is not None]
            if nt:                                            # 回合开头：GRU 吃一步回合摘要（fp32，与训练时整局序列等价）
                x = torch.from_numpy(np.stack([reqs[i][3]["xt"] for i in nt])).to(dev)
                h0 = torch.stack([hw[reqs[i][1]] for i in nt])
                h1 = model.mem_step(x, h0)
                for j, i in enumerate(nt):
                    hw[reqs[i][1]] = h1[j]
            tb["mem"] = torch.stack([hw[r[1]] for r in reqs])
        with torch.no_grad(), AC():
            logits, ex, value = model(tb)
        logits = logits.float()
        probs = torch.softmax(logits, -1)
        act = torch.multinomial(probs, 1).squeeze(-1)
        logp = torch.log(probs.gather(1, act[:, None]).squeeze(1) + 1e-12)
        return act.cpu().numpy(), logp.cpu().numpy(), value.float().cpu().numpy()

    def compact(o):
        return {k: (v.astype(np.float16) if v.dtype == np.float32 else v) for k, v in o.items()}

    def update(games):
        nonlocal upd
        model.train()
        items, ep_of, E = [], [], len(games)
        Tmax = max(len(g[2]) for g in games)
        X = np.zeros((E, Tmax, F_DIM), np.float32)
        AUX = np.zeros((E, Tmax, M_AUX), np.float32)
        AMK = np.zeros((E, Tmax), bool)
        for e_, (decs, R, xs, auxs) in enumerate(games):
            X[e_, : len(xs)] = np.stack(xs)
            for k, v in enumerate(auxs):
                if v is not None:
                    AUX[e_, k], AMK[e_, k] = v, True
            for d in decs:
                items.append((d, R))
                ep_of.append(e_)
        Xg, AUXg, AMKg = (torch.from_numpy(z).to(dev) for z in (X, AUX, AMK))
        ep_arr = np.array(ep_of)
        ti_arr = np.array([d["ti"] for d, _ in items])
        n = len(items)
        adv_all = np.array([R - d["v"] - (a.invalid_pen if d["obs"]["cand_exec"][d["a"]] < 0.5 else 0.0)
                            for d, R in items], np.float32)
        adv_all = (adv_all - adv_all.mean()) / (adv_all.std() + 1e-6)
        ret_all = np.array([R for _, R in items], np.float32)
        stat = dict(pl=0., vl=0., el=0., ent=0., kl=0., cf=0., exec_acc=0., klref=0., aux=0., nb=0)
        for ep in range(a.epochs):
            idx = np.random.permutation(n)[: max(a.mb, int(n * a.sub))]
            for i in range(0, len(idx), a.mb):
                sel = idx[i: i + a.mb]
                batch = collate([items[j][0]["obs"] for j in sel])
                tb = to_dev(batch)
                Hs = model.mem_seq(Xg)                                  # [E,T,H] 对整局序列精确 BPTT
                tb["mem"] = Hs[torch.from_numpy(ep_arr[sel]).to(dev), torch.from_numpy(ti_arr[sel]).to(dev)]
                aux_pred = model.mem_aux(Hs)
                aux_l, aux_rel = aux_loss(aux_pred, AUXg, AMKg)
                act = torch.tensor([items[j][0]["a"] for j in sel], device=dev)
                lp_old = torch.tensor([items[j][0]["lp"] for j in sel], device=dev)
                adv = torch.from_numpy(adv_all[sel]).to(dev)
                ret = torch.from_numpy(ret_all[sel]).to(dev)
                with AC():
                    logits, exl, val = model(tb)
                logits, exl, val = logits.float(), exl.float(), val.float()
                logp_all = F.log_softmax(logits, -1)
                lp = logp_all.gather(1, act[:, None]).squeeze(1)
                ratio = torch.exp(lp - lp_old)
                pl = -torch.min(ratio * adv, ratio.clamp(1 - a.clip, 1 + a.clip) * adv).mean()
                vl = F.mse_loss(val, ret)
                m = tb["cand_mask"]
                ex_tgt = tb["cand_exec"]
                el = (F.binary_cross_entropy_with_logits(exl, ex_tgt, reduction="none") * m).sum() / m.sum()
                p = logp_all.exp()
                ent = -(p * logp_all.masked_fill(~m, 0)).sum(-1).mean()
                klr = torch.zeros((), device=dev)
                if ref is not None:
                    with torch.no_grad():
                        tb_ref = dict(tb)
                        tb_ref["mem"] = ref.mem_seq(Xg)[torch.from_numpy(ep_arr[sel]).to(dev), torch.from_numpy(ti_arr[sel]).to(dev)]
                    with torch.no_grad(), AC():
                        rl_, _, _ = ref(tb_ref)
                    ref_logp = F.log_softmax(rl_.float(), -1)
                    klr = (p * (logp_all - ref_logp).masked_fill(~m, 0)).sum(-1).mean()
                loss = pl + 0.5 * vl + a.exec_w * el - a.ent * ent + a.kl_ref * klr + a.aux_w * aux_l
                opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                stat["pl"] += pl.item(); stat["vl"] += vl.item(); stat["el"] += el.item(); stat["ent"] += ent.item()
                stat["kl"] += (lp_old - lp).mean().item(); stat["cf"] += ((ratio - 1).abs() > a.clip).float().mean().item()
                stat["exec_acc"] += (((exl > 0).float() == ex_tgt).float() * m).sum().item() / m.sum().item()
                stat["nb"] += 1
                stat["klref"] = stat.get("klref", 0.) + klr.item()
                stat["aux"] = stat.get("aux", 0.) + float(aux_rel.item())
                if (lp_old - lp).mean().item() > a.target_kl:      # KL 超过目标：停止本次更新的后续 minibatch
                    break
        model.eval()
        upd += 1
        if dev == "cuda":
            torch.cuda.empty_cache()
        return {k: (v / max(1, stat["nb"]) if k != "nb" else v) for k, v in stat.items()}

    model.eval()
    pending = {}
    last_ckpt = time.time()
    while time.time() < t_end:
        ready = wait(conns, timeout=0.01)
        reqs = []
        for c in ready:
            try:
                msg = c.recv()
            except EOFError:
                continue
            if msg[0] == "req":
                reqs.append((c, msg[1], msg[2], msg[3]))
            else:
                summ = msg[2]
                b_ = buf[msg[1]]
                buf[msg[1]] = dict(decs=[], xs=[], auxs=[])
                hw[msg[1]] = torch.zeros(model.mem_h, device=dev)
                done_games.append((b_["decs"], summ["reward"], summ, b_["xs"], b_["auxs"]))
                stats["games"] += 1
        if reqs:
            acts, lps, vals = infer(reqs)
            for (c, wid, o, meta), ai, lp, v in zip(reqs, acts, lps, vals):
                b_ = buf[wid]
                if meta["xt"] is not None:
                    b_["xs"].append(meta["xt"]); b_["auxs"].append(None)
                    if meta["aux_prev"] is not None:
                        b_["auxs"][-2] = meta["aux_prev"]
                b_["decs"].append(dict(obs=compact(o), a=int(ai), lp=float(lp), v=float(v), ti=len(b_["xs"]) - 1))
                c.send(int(ai))
            stats["decisions"] += len(reqs)
            stats["dec_window"] += len(reqs)
        if len(done_games) >= a.games:
            games, done_games = done_games[: a.games], done_games[a.games:]
            summ = [g[2] for g in games]
            tu = time.time()
            st = update([(g[0], g[1], g[3], g[4]) for g in games])
            now = time.time()
            rec = dict(upd=upd, minutes=(now - stats["t0"]) / 60, games=stats["games"], decisions=stats["decisions"],
                       dec_per_s=stats["dec_window"] / max(1e-6, now - stats["last"] - (now - tu)), update_s=now - tu,
                       reward=float(np.mean([s["reward"] for s in summ])),
                       win=float(np.mean([s["winner"] == s["seat"] for s in summ])),
                       lose=float(np.mean([(s["winner"] is not None) and s["winner"] != s["seat"] for s in summ])),
                       turns=float(np.mean([s["turns"] for s in summ])),
                       invalid=float(sum(s["invalid"] for s in summ) / max(1, sum(s["steps"] for s in summ))),
                       by_opp={o: float(np.mean([s["reward"] for s in summ if s["opp"] == o] or [np.nan])) for o in ("rule", "w1_25_2", "w1_40_3", "w2")},
                       **st)
            log.write(json.dumps(rec, ensure_ascii=False) + "\n"); log.flush()
            print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items() if k != "by_opp"}, ensure_ascii=False), flush=True)
            stats["last"], stats["dec_window"] = now, 0
        if time.time() - last_ckpt > 600:
            torch.save(dict(model=model.state_dict(), cfg=model.cfg, upd=upd), os.path.join(a.out, "ckpt.pt"))
            last_ckpt = time.time()
    torch.save(dict(model=model.state_dict(), cfg=model.cfg, upd=upd), os.path.join(a.out, "ckpt.pt"))
    for c in conns:
        try:
            c.send(None)
        except Exception:
            pass
    print("done", stats["games"], "games", stats["decisions"], "decisions", flush=True)


if __name__ == "__main__":
    main()
