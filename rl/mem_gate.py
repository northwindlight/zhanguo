# -*- coding: utf-8 -*-
"""★ **潜槽的两道闸**（用户 2026-09-25 的隐空间设计里点名要的）。

    跑法（**训完之后**、**别在炉子里跑**）：
        .venv/bin/python -m rl.mem_gate --ckpt rl/runs/ppo_mem.pt
        .venv/bin/python -m rl.mem_gate --ckpt ... --prob        # 只跑线性解码探针

为什么要有它
────────────
"加了隐空间"这件事**最容易变成装饰品**：结构接好了、loss 照降、日志一切正常，
而槽里其实什么都没有（写门没开 / 梯度没进去 / 被主干忽略）。
⇒ 两道**能证伪**的闸：

  **闸 A（行为·置零/打乱）**：同一批**固定种子**的局，跑两遍 ——
    一遍**带着**槽，一遍把槽**置零 / 打乱**。
    · 训完之后**分数必须掉**（掉不动 ⇒ 槽里没东西、或没人读它）；
    · ★ **轮换对照**：打乱**比置零更糟或相当**才对 ——
      如果"置零"比"打乱"更伤，说明模型依赖的是**槽的平均值**（一个常数偏置），
      而不是**信息**，那记忆是假的。
  ★ 还有一条**必须同时看**的基线：**同权重、关掉记忆**（`forward` 走初值槽）的分数。
    「带槽」比「不带槽」好，才说明记忆有用；否则只是"槽占了几行 K/V"的副作用。

  **闸 B（表征·线性解码）**：从槽**线性**回归"此刻**看不见但账本里有**的敌军"
    （`war_memory` 的幽灵：番号/位置/血量/年龄）。R² 高 ⇒ 槽里**线性可读地**
    编码了记忆内容；R² ≈ 0 ⇒ 槽是**哑的**（哪怕策略分数没掉）。
    ★ 线性可解码是**充分不必要**的判据（主干可以非线性编码），
      所以 R² 低**不能单独判死**；但 R² 高是**很强的**"记忆真的在里面"的证据。

★ 为什么用**固定种子**：闸门要比的是"同一个局面下，槽不同会怎样"。
  换种子就换了局面 ⇒ 分数差里混进地图噪声，什么都测不出来。
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import torch

from . import encode as E
from . import scoring as S
from . import train as T
from . import vocab as V
from .model import build_model
from .sandbox import Sandbox


def _nets_from_ckpt(path: str, mem_slots: int, log=print):
    blob = torch.load(path, map_location="cpu", weights_only=False)
    fp = (blob.get("meta") or {}).get("fingerprint") or {}
    if fp and int(fp.get("mem_slots", -1)) != int(mem_slots):
        raise SystemExit(f"★ {path} 的 `mem_slots`={fp.get('mem_slots')} 与 "
                         f"--mem-slots {mem_slots} 不一致（同 `train._load_ckpt` 的纪律）")
    got = blob.get("nets")
    if not isinstance(got, dict) or not got:
        raise SystemExit(f"★ {path} 里没有 `nets`")
    nets = {}
    for i, sd in got.items():
        n = build_model(mem_slots=mem_slots)
        n.load_state_dict(sd)
        n.eval()
        nets[int(i)] = n
    log(f"★ 读入 {len(nets)} 份权重（mem_slots={mem_slots}）")
    return nets


def _episodes(seeds, size, n_nations, t_max, halls_known):
    """**固定种子**的一批局（闸门要比的就是"同局面、槽不同"）。"""
    out = []
    for sd in seeds:
        sb = Sandbox(seed=sd, size=size, n_nations=n_nations, t_max=t_max,
                     halls_known=halls_known).reset()
        out.append(sb)
    return out


@torch.no_grad()
def _rollout(nets, sb, mode: str, rng, max_steps: int = 4000) -> float:
    """跑完一局，返回**终局势函数**（`Φ(s_T)`）——闸门比的就是它。

    ★★ `max_steps` = **必给的兜底**：我第一版没写它 ⇒ 探针在**这里死循环了 15 分钟
      没有输出**（`while not sb.is_terminal()` 一旦"步进不推进局面"就永远转）。
      `train.collect_episode` 从来都有 `max_steps` 兜底，**探针也必须**有 ——
      闸门卡住和"闸门说记忆没用"是两件完全不同的事，而**卡住时什么都看不出来**。
    

    `mode`：`on`（带槽）/ `off`（不带槽，走初值）/ `zero`（槽恒置零）/
            `shuffle`（每一步把槽**打乱**：行序打乱 + 加一个跨局交换）。
    ★ `off` 是**基线臂**：它就是"马尔可夫"的那条路（见 `PolicyNet.forward` 的说明）。
    """
    mem: dict = {}
    n = 0
    while not sb.is_terminal() and n < int(max_steps):
        n += 1
        me = sb.current_player()
        if me is None:
            break
        acts = sb.legal()
        if not acts:
            break
        batch = T.collate([E.obs_of(sb, me, acts)])
        net = nets[sb.players.index(me) % len(nets)]
        if mode == "off":
            logits, _ = net(batch)
        else:
            m = mem.get(me)
            if mode == "zero":
                m = torch.zeros_like(net.mem_init(1)) if m is None else torch.zeros_like(m)
            elif mode == "shuffle" and m is not None:
                m = m[:, torch.randperm(m.shape[1]), :]
            logits, _, m2 = net.forward_state(batch, m)
            mem[me] = torch.zeros_like(m2) if mode == "zero" else m2
        probs = torch.softmax(logits[0], -1).numpy()
        aidx = int(rng.choice(len(probs), p=probs / probs.sum()))
        sb.step(acts[aidx])
    return float(T._score(sb, sb.players[0])), bool(n >= int(max_steps))


def gate_a(nets, seeds, modes, a_max_steps=600, **kw) -> dict:
    """闸 A：固定种子下，四种槽状态各跑一遍，比分。"""
    res = {}
    for mode in modes:
        vals = []
        for i, sd in enumerate(seeds):
            sb = Sandbox(seed=sd, size=kw["size"], n_nations=kw["n_nations"],
                         t_max=kw["t_max"], halls_known=kw["halls_known"]).reset()
            vals.append(_rollout(nets, sb, mode, np.random.default_rng(1000 + i),
                                 max_steps=a_max_steps))
        res[mode] = float(np.mean(vals))
        # ★★ 如实报告"有几局**根本没打完**（撞了步数上限）"：未训练的随机策略**赢不了**
        #   ⇒ 局局跑到上限，此时那个分数是"**上限处的 Φ**"，不是终局分。
        #   不报的话，读的人会把它当成"终局得分"来比 —— 那正是"工具骗人"的形状。
        res[mode + "_capped"] = int(sum(1 for v in vals if v[1]))
    return res


@torch.no_grad()
def gate_b(nets, seeds, a_max_steps=600, **kw) -> dict:
    """闸 B：从槽**线性**解码"账本里有、但此刻看不见"的敌军信息。"""
    X, Y = [], []
    for i, sd in enumerate(seeds):
        sb = Sandbox(seed=sd, size=kw["size"], n_nations=kw["n_nations"],
                     t_max=kw["t_max"], halls_known=kw["halls_known"]).reset()
        mem: dict = {}
        n = 0
        while not sb.is_terminal() and n < a.max_steps:   # ★ 同 `_rollout`：兜底必给
            n += 1
            me = sb.current_player()
            if me is None:
                break
            acts = sb.legal()
            if not acts:
                break
            batch = T.collate([E.obs_of(sb, me, acts)])
            net = nets[sb.players.index(me) % len(nets)]
            logits, _, m = net.forward_state(batch, mem.get(me))
            mem[me] = m
            # ★ 标签 = **账本里的幽灵**汇总（只有"看不见、但我记得"的才在里面）
            ghosts = sb.known_enemies(me)
            hx, hy = E._home_cell(sb, me)
            ps = float(sb.size)
            Y.append([len(ghosts) / 4.0,
                      sum(g["hp"] for g in ghosts) / 400.0,
                      sum((g["x"] - hx) / ps for g in ghosts) / 4.0,
                      sum((g["y"] - hy) / ps for g in ghosts) / 4.0,
                      max((g["age"] for g in ghosts), default=0) / V.AGE_SCALE])
            X.append(m.reshape(-1).numpy())
            probs = torch.softmax(logits[0], -1).numpy()
            sb.step(acts[int(np.random.default_rng(2000 + i).choice(
                len(probs), p=probs / probs.sum()))])
    if len(X) < 20:
        return {"n": len(X), "note": "样本太少，探针不可信"}
    X = np.asarray(X, np.float64)
    Y = np.asarray(Y, np.float64)
    X = np.c_[X, np.ones(len(X))]                       # 截距
    # 岭回归（闭式）—— 槽的维度高于样本数时普通最小二乘会过拟合
    lam = 1e-2 * np.trace(X.T @ X) / X.shape[1]
    W = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ Y)
    pred = X @ W
    ss_res = ((Y - pred) ** 2).sum(0)
    ss_tot = ((Y - Y.mean(0)) ** 2).sum(0) + 1e-12
    return {"n": len(X), "r2": (1 - ss_res / ss_tot).tolist(),
            "cols": ["幽灵数", "总血", "位置和x", "位置和y", "最旧年龄"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="潜槽的两道闸（训完之后跑）")
    ap.add_argument("--ckpt", required=True, help="训练存档（含 `nets`）")
    ap.add_argument("--mem-slots", dest="mem_slots", type=int, default=V.M_SLOTS)
    ap.add_argument("--episodes", type=int, default=6, help="固定种子的局数")
    ap.add_argument("--max-steps", dest="max_steps", type=int, default=600,
                    help="单局步数上限（兜底，**必给**：没它探针会死循环）")
    ap.add_argument("--size", type=int, default=14)
    ap.add_argument("--nations", type=int, default=3)
    ap.add_argument("--t-max", dest="t_max", type=int, default=150)
    ap.add_argument("--no-halls-known", dest="halls_known", action="store_false")
    ap.add_argument("--prob", action="store_true", help="只跑闸 B（线性解码）")
    a = ap.parse_args(argv)

    nets = _nets_from_ckpt(a.ckpt, a.mem_slots)
    seeds = [7000 + i for i in range(a.episodes)]       # ★ **固定种子**
    kw = dict(size=a.size, n_nations=a.nations, t_max=a.t_max,
              halls_known=a.halls_known)
    if not a.prob:
        res = gate_a(nets, seeds, ("on", "off", "zero", "shuffle"),
                     a_max_steps=a.max_steps, **kw)
        print(f"★ 闸 A（固定种子 {len(seeds)} 局，平均 Φ；越大越好）")
        for k in ("on", "off", "zero", "shuffle"):
            cap = res.get(k + "_capped", 0)
            print(f"   槽={k:8s} {res[k]:12.2f}"
                  + (f"   ⚠ 其中 {cap}/{len(seeds)} 局**撞了步数上限**"
                     f"（分数是上限处的 Φ，不是终局分）" if cap else ""))
        d_off = res["on"] - res["off"]
        print(f"   · 带槽 − 不带槽 = {d_off:+.2f}"
              f"{'  ⇒ 记忆有增益' if d_off > 0 else '  ⇒ ★ 记忆没带来增益'}")
        print(f"   · 置零 vs 打乱 = {res['zero']:+.2f} vs {res['shuffle']:+.2f}"
              f"{'  ⇒ 正常（信息在槽里，不是常数偏置）' if res['shuffle'] <= res['zero'] + 1e-6 else '  ⇒ ★ 可疑：模型依赖的是槽的平均值'}")
    b = gate_b(nets, seeds, a_max_steps=a.max_steps, **kw)
    print("★ 闸 B（线性解码）：从槽回归「账本里有、此刻看不见」的敌军")
    if "r2" not in b:
        print("   ", b)
    else:
        print(f"    n={b['n']}")
        for c, r in zip(b["cols"], b["r2"]):
            print(f"    {c:8s} R²={r:+.3f}")
        print("   ★ R² 高 = 槽里线性可读地编码了记忆；≈0 = 槽是哑的"
              "（线性可解码是**充分不必要**，低不能单独判死）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
