# -*- coding: utf-8 -*-
"""★ **固定种子评测** —— 回答「有没有学到策略」的**干净判据**。

    为什么要单独做这个（训练日志里的"平均回合"不够用）
    ────────────────────────────────────────────────
    训练日志的 `平均回合` 是**每 iter 现抽的随机地图 + 随机国家数 + 随机先手**，
    逐 iter 比它等于在噪声里找趋势（实测：2 个 iter 就能从 34.8 跳到 24.5，
    而那时网根本没学过东西）。

    ⇒ 这里把**地图随机那一层去掉**：同一组固定种子、同样先手，
      拿**训过的网**和**未训练的网**各打一遍，比：平均回合 / 胜场分布 / 策略熵。
      ★ **这才是"学会了没有"的判据**；日志里的 `ent` 只是便宜的代理。

    ★ `ent` 的参照系：**均匀分布的熵 = `log K`**（K = 候选数）。
      不量出它，`ent=3.2` 是个没意义的数。实测 8×8/3 国 `log K` 均值 ≈ **3.16**
      ⇒ `ent` 明显低于 3.16 才叫"策略在收紧"。

    跑法
    ────
      # 未训练基线
      python -m rl.eval_fixed --seeds 24 --size 8 --nations 3 --t-max 500
      # 训过的（同一个种子组，直接对比）
      python -m rl.eval_fixed --seeds 24 --size 8 --nations 3 --t-max 500 \
          --ckpt rl/runs/ppo/base_0925_small.pt
      # ECS 上：./rl/run_ecs.sh eval_fixed --ckpt ... --seeds 24

    ★ 两种取动作方式都给：`--sample`（按策略采样，训练同分布）与默认的**贪心**。
      用户 2026-09-18 定过调：「判据只看**采样臂**（贪心要退火，没做之前不看）」
      ⇒ **报数时以 `--sample` 那条为准**，贪心那条只作参考。
"""
from __future__ import annotations

import argparse

import numpy as np
import torch

from . import encode, scoring as S
from . import vocab as V
from .model import build_model
from .sandbox import Sandbox, n_nations_for
from .train import collate


def load_pool(path: str | None, n_slots: int, *, log=print) -> dict:
    """从 ckpt 读回整池权重；`None` ⇒ **未训练的网**（基线臂）。"""
    nets = {i: build_model() for i in range(n_slots)}
    if not path:
        log(f"★ 基线臂：**未训练**的 {n_slots} 份网（全新初始化）")
        return nets
    blob = torch.load(path, map_location="cpu", weights_only=False)
    meta = blob.get("meta") or {}
    fp = meta.get("fingerprint") or {}
    # ★★ 形状指纹对不上 ⇒ **直接拒**（旧线铁律：ckpt 会被新代码加载就必须重炼）
    now = {"grid_channels": int(V.GRID_CHANNELS), "glob_size": int(V.GLOB_SIZE)}
    for k, v in now.items():
        if k in fp and fp[k] != v:
            raise SystemExit(
                f"★ ckpt 的形状指纹对不上：{k} 存的是 {fp[k]}，现在是 {v}"
                f" ⇒ 这个 ckpt 是**旧代码**训的，必须重炼（别硬加载）")
    got = blob["nets"]
    for i in range(n_slots):
        if i in got:
            nets[i].load_state_dict(got[i])
    log(f"★ 从 {path} 读回 {len(got)} 份权重（iter={meta.get('iters')}，"
        f"指纹 {fp}）")
    return nets


@torch.no_grad()
def play(nets: dict, sb: Sandbox, *, greedy: bool,
         rng: np.random.Generator) -> dict:
    """一局到底。返回 `{"turns", "winner_members", "ent", "n", "ok"}`（一局一句）。"""
    # ★★ 逐步记 **`ent` 与 `log K` 两个**（K = 当时的候选数）——
    #   只报"整局平均 ent"是错的：局一长、民兵一多 K 就涨，"均匀熵"跟着涨
    #   ⇒ 拿它对比一个固定数（如 3.16）是**拿苹果比橘子**（实测踩过：未训练的网
    #   拖到 100 回合时 ent 报到 4.585，而那时 log K 也 ≈4.6，其实**仍是均匀**）。
    #   ⇒ 唯一可比的量是**逐步的 `ent / log K`**（1.0 = 纯均匀）。
    ents, logks, n = [], [], 0
    while not sb.is_terminal():
        me = sb.current_player()
        if me is None:
            break
        acts = sb.legal()
        if not acts:                       # ★ 保险（`_auto_advance` 本该已推进）
            break
        obs = encode.obs_of(sb, me, acts)
        net = nets[sb.players.index(me) % len(nets)]
        logits, _ = net(collate([obs]))
        p = torch.softmax(logits[0], -1).numpy()
        aidx = int(np.argmax(p)) if greedy else int(rng.choice(len(p), p=p))
        ents.append(float(-(p * np.log(np.maximum(p, 1e-12))).sum()))
        logks.append(float(np.log(len(p))))
        sb.step(acts[aidx])
        n += 1
    return {"turns": sb.turn, "winner_members": tuple(sb.winner_members()),
            "ent": float(np.mean(ents)) if ents else 0.0,
            "logk": float(np.mean(logks)) if logks else 0.0,
            "ratio": (float(np.mean([e / k for e, k in zip(ents, logks)]))
                      if ents else 0.0),
            "n": n, "players": tuple(sb.players)}


def main() -> None:
    ap = argparse.ArgumentParser(description="固定种子评测（学会了没有）")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=24, help="固定种子个数（同一组种子两臂共用）")
    ap.add_argument("--seed0", type=int, default=1000, help="种子起点（两臂必须一致）")
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--nations", type=int, default=None, help="缺省按 `n_nations_for(size)`")
    ap.add_argument("--t-max", dest="t_max", type=int, default=500)
    ap.add_argument("--pool", type=int, default=5)
    ap.add_argument("--ckpt", type=str, default=None, help="不给 ⇒ 未训练基线臂")
    ap.add_argument("--sample", action="store_true",
                    help="★ 按策略**采样**取动作（用户 09-18：**判据只看采样臂**）")
    ap.add_argument("--halls-known", dest="halls_known", action="store_true", default=True)
    ap.add_argument("--no-halls-known", dest="halls_known", action="store_false")
    a = ap.parse_args()
    if a.threads:
        torch.set_num_threads(a.threads)

    k = a.nations or n_nations_for(a.size)
    nets = load_pool(a.ckpt, a.pool)
    rng = np.random.default_rng(0)                 # ★ 固定种子、**不随臂变**
    rows = []
    for s in range(a.seed0, a.seed0 + a.seeds):
        sb = Sandbox(seed=s, size=a.size, n_nations=k, t_max=a.t_max,
                     halls_known=a.halls_known).reset()
        rows.append(play(nets, sb, greedy=not a.sample, rng=rng))
    turns = np.array([r["turns"] for r in rows], float)
    ents = np.array([r["ent"] for r in rows], float)
    players = V.PLAYER_NAMES[:k]
    wins = {p: sum(1 for r in rows if p in r["winner_members"]) for p in players}
    draws = sum(1 for r in rows if not r["winner_members"])
    print(f"── {'采样臂' if a.sample else '贪心臂'} · {a.size}×{a.size} / {k} 国 · "
          f"{a.seeds} 个固定种子 ──")
    print(f"  平均回合 {turns.mean():6.1f}（中位 {np.median(turns):.0f}，"
          f"打到上限判平 {int((turns >= a.t_max).sum())} 局）")
    print(f"  胜场 {wins}   平局 {draws}")
    ratio = np.array([r["ratio"] for r in rows], float)
    logk = np.array([r["logk"] for r in rows], float)
    print(f"  策略集中度 **ent/logK = {ratio.mean():.3f}**（1.0=纯均匀，越低越紧）"
          f"   [原始 ent {ents.mean():.3f} / logK {logk.mean():.3f}]")


if __name__ == "__main__":
    main()