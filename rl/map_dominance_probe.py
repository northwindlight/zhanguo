# -*- coding: utf-8 -*-
"""★ **地图支配 vs 动作支配** —— 同一张图、同一个策略，多打几遍，胜负会不会翻转？

    python rl/map_dominance_probe.py [--games-per-map 4] [--maps 6] [--nations 2]
                                     [--size 8] [--t-max 200] [--ckpt ...]

★ 为什么必须量（2026-10-28）
  ──────────────────────────
  PPO 在这个游戏里"学不动"，有两种完全不同的解释，处理方式也相反：
    ① **信号不够**（梯度通路/信用分配/算力）⇒ 加样本、加旋钮就能学；
    ② **这件事本来就学不了** —— 胜负**由地图决定**，动作改不了 ⇒
       再多样本也只是拟合噪声；而且**评测也没有分辨力**：
       ★ 配对评测（同一批种子、同一张图，两臂各打一遍）里，
         如果地图决定了胜负，**两臂必然同输或同赢** ⇒ 净效果恒 ≈ 0 ⇒
         **再怎么训也测不出强弱**。
  ⇒ 这个探针量的是**天花板**：即使把 PPO 修到完美，收益上限有多大。

★ 口径：
  · 同一张图（`Sandbox(seed=固定)`）打 `games_per_map` 遍，**只有采样 rng 不同**
    （⇒ 动作不同、地图与地形完全相同）；
  · 报**每张图的胜负分布**（谁赢了几遍）—— 一致 ⇒ 地图支配；五五开 ⇒ 动作支配。

★ 读法：
  · **一致性高**（某张图 4/4 同一个人赢）⇒ 那张图是"地图局"，两边都赢 ⇒
    评测时这类图**必须分开统计**（否则分母被它吃掉）。
  · **一致性低**（2:2、3:1）⇒ 胜负由动作决定 ⇒ **有得学**，也有得测。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np
import torch

# ★ 探针必须能 import 到**主线的 `mods`**（`mods.native.*`）：脚本住在 `rl/` 下
#   ⇒ `rl/mods`（一个**正规包**）会把仓库根的 `mods`（**命名空间包**）遮掉
#   ⇒ `mods.native` import 不到（"No module named 'mods.native'"）。
#   ⇒ 把**脚本自己所在目录**从 sys.path 里剔掉，让仓库根那份 `mods` 生效。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _here]

from rl import train as T                                   # noqa: E402
from rl.model import build_model                            # noqa: E402
from rl.sandbox import Sandbox                              # noqa: E402
from rl.vocab import PLAYER_NAMES                            # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量「地图支配 vs 动作支配」")
    ap.add_argument("--ckpt", default="rl/runs/baseline_init.pt")
    ap.add_argument("--maps", type=int, default=6)
    ap.add_argument("--games-per-map", type=int, default=4)
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--t-max", type=int, default=200)
    ap.add_argument("--nations", type=int, default=2)
    ap.add_argument("--seed0", type=int, default=40000)
    a = ap.parse_args(argv)

    torch.set_num_threads(1)
    players = list(PLAYER_NAMES[:a.nations])
    nets = {}
    if a.ckpt and os.path.exists(a.ckpt):
        blob = torch.load(a.ckpt, map_location="cpu", weights_only=False)
        src = blob.get("nets") or {}
        for i, p in enumerate(players):
            n = build_model(mem_slots=0)
            key = i if i in src else (min(src) if src else None)
            if key is not None:
                n.load_state_dict(src[key])
            n.eval()
            nets[p] = n
    else:
        torch.manual_seed(20261024)
        for p in players:
            n = build_model(mem_slots=0)
            n.eval()
            nets[p] = n
    print(f"★ 政策：{a.ckpt or '随机初始化'} · {a.nations} 国 · {a.size}×{a.size} · "
          f"t_max {a.t_max}")
    print(f"★ {a.maps} 张图 × 每张打 {a.games_per_map} 遍（**只有采样 rng 不同**）\n")

    same = 0
    decided = 0
    flips = []
    for m in range(a.maps):
        seed = a.seed0 + m
        win = Counter()
        turns = []
        for rep in range(a.games_per_map):
            sb = Sandbox(seed=seed, size=a.size, t_max=a.t_max,
                         n_nations=a.nations, halls_known=True, territory=True,
                         alliances="none").reset()
            steps, info = T.collect_episode(
                nets, sb, rng=np.random.default_rng(seed * 31 + rep),
                episode_id=m * 100 + rep,
                member_of={p: f"m{i}" for i, p in enumerate(sb.players)})
            w = info.get("winner_members") or ()
            if w:
                win[tuple(sorted(w))] += 1
            else:
                win[("平",)] += 1
            turns.append(info["turns"])
        top = win.most_common()
        best = top[0][1]
        label = "+".join(top[0][0]) if len(top[0][0]) > 1 else top[0][0][0]
        print(f"图 {m}（seed={seed}）: {dict((('+'.join(k) if len(k) > 1 else k[0]), v) for k, v in win.items())}"
              f" · 最常见者 {best}/{a.games_per_map} · 平均回合 {np.mean(turns):.0f}", flush=True)
        if len(top) == 1 and best == a.games_per_map:
            same += 1
        else:
            flips.append(best)
        decided += 1
    print(f"\n★ 完全一致（同一张图每次同一个赢家）的图：**{same}/{decided}**")
    if flips:
        print(f"★ 不一致的图：最常见者占比 {flips}（占 {a.games_per_map} 遍）")
    print("★ 读法：一致率高 ⇒ **地图支配** ⇒ 配对评测在那些图上没有分辨力"
          "（两臂必然同输同赢 ⇒ 净效果恒 0）；一致率低 ⇒ **动作决定胜负** ⇒ 有得学也有得测。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
