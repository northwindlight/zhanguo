# -*- coding: utf-8 -*-
"""★★ **GAE-λ 的信用视界** —— 终局结果到底能回传到多远一步。

    python rl/gae_credit_probe.py [--ckpt rl/runs/baseline_init.pt] [--games 8]
                                 [--size 8] [--t-max 200] [--nations 3]

★ 为什么要有它（2026-10-28 实测出来的）
  ──────────────────────────────────────
  上一轮把"策略学不动"归因成"**信号被 critic 吃掉**"（原始优势 std 只有 0.051，
  理想应 ≈1）⇒ 于是打算动**模型结构**（策略与价值别共用主干）。
  ★ **那个归因是错的**：真正掐死信号的是 **GAE 的 λ**（平滑系数），不是 critic。
    GAE-λ 的递推 `adv_t = δ_t + γ·λ·adv_{t+1}` 意味着**终局奖励在第 t 步的权重是
    `λ^m`**（m = 从 t 到终局的步数）⇒ **信用视界 ≈ 1/(1−λ) 步**。
    而这一局**每方中位 265 步**（最长 780）⇒ λ=0.95 的视界只有 **20 步** ⇒
    **92.7% 的决策拿到的终局权重是 6e-9 量级**（= 没有信号）。

  ★ 为什么"看起来像 critic 吃掉了"：λ 小的时候 `adv` 里剩下的是
    **critic 的局部价值差**（≈ 噪声），量级 0.05~0.17 ⇒ 再被标准化除以 0.17 ⇒
    **把噪声放大 6 倍** ⇒ 表现就是"策略在原地随机游走"。
    ⇒ 那个 0.051 **不是**"critic 解释掉了 95%"，而是"**终局结果压根没传过来**"。

★ 本探针量三件事（**全部来自真实对局样本**，不是从日志里量化过的数）：
  ① 每段轨迹的长度分布（m 的量级）；
  ② 各 λ 下 **终局权重 λ^m** 的分布（解析量，闭式）；
  ③ 各 λ 下 **`adv` 与该段终局结果 R 的相关**（= 优势里还剩多少胜负信号）。

★ 读法（实测，8 局 / 24 段 / 中位段长 265）：
    λ     std(adv)   corr(adv,R)     R²     中位 λ^m      带信号步占比
    0.95    0.174        0.345      11.9%   6.3e-09          7.3%
    0.99    0.369        0.696      48.4%   2.5e-02         32.4%
    1.00    0.945        0.988      97.7%   1.0e+00        100.0%
  ⇒ **λ=1.0 时 std(adv)≈0.945**，正是"理想 ≈1"那个数 ⇒ 口径自洽。
"""
from __future__ import annotations

import argparse
import os
import sys

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

LAMS = (0.5, 0.8, 0.9, 0.95, 0.99, 1.0)


def _load(ckpt: str | None, players: list[str], log=print):
    """按座位建网；给了档就载入它的第 0..k 份网，否则用**固定种子**的随机初始化。"""
    nets = {}
    if ckpt and os.path.exists(ckpt):
        blob = torch.load(ckpt, map_location="cpu", weights_only=False)
        src = blob.get("nets") or {}
        for i, p in enumerate(players):
            n = build_model(mem_slots=0)
            key = i if i in src else (min(src) if src else None)
            if key is not None:
                n.load_state_dict(src[key])
            n.eval()
            nets[p] = n
        log(f"★ 载入 {ckpt}（{len(src)} 份网）")
    else:
        torch.manual_seed(20261024)
        for p in players:
            n = build_model(mem_slots=0)
            n.eval()
            nets[p] = n
        log(f"★ 没给档 ⇒ 用**固定种子**的随机初始化（种子 20261024）")
    return nets


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量 GAE-λ 的信用视界")
    ap.add_argument("--ckpt", default="rl/runs/baseline_init.pt")
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--t-max", type=int, default=200)
    ap.add_argument("--nations", type=int, default=3)
    ap.add_argument("--seed0", type=int, default=7000)
    a = ap.parse_args(argv)

    torch.set_num_threads(1)
    players = list(PLAYER_NAMES[:a.nations])
    nets = _load(a.ckpt, players)

    all_steps = []
    for g in range(a.games):
        sb = Sandbox(seed=a.seed0 + g, size=a.size, t_max=a.t_max,
                     n_nations=a.nations, halls_known=True, territory=True,
                     alliances="none").reset()
        steps, info = T.collect_episode(
            nets, sb, rng=np.random.default_rng(1000 + g), episode_id=g,
            member_of={p: f"m{i}" for i, p in enumerate(sb.players)})
        all_steps.append(steps)
        print(f"局 {g}: {len(steps)} 步 · {info['turns']} 回合 · "
              f"truncated={info['truncated']} · winner={info['winner']}", flush=True)

    steps = [s for ss in all_steps for s in ss]
    seg_outcome: dict[tuple, float] = {}
    seg_len: dict[tuple, int] = {}
    seg_last: dict[tuple, int] = {}
    for i, s in enumerate(steps):
        k = T._segment_key(s)
        seg_len[k] = seg_len.get(k, 0) + 1
        seg_last[k] = i
        if s.outcome is not None:
            seg_outcome[k] = float(s.outcome)

    lens = np.array(list(seg_len.values()))
    print(f"\n合计 {len(steps)} 步 / {a.games} 局")
    print(f"段数 {len(seg_len)} · 段长 中位 {np.median(lens):.0f} · 均值 {lens.mean():.0f} "
          f"· 最小 {lens.min()} · 最大 {lens.max()}")
    vals = np.array([seg_outcome[k] for k in seg_outcome])
    if len(vals):
        print(f"终局结果：赢 {(vals > 0).sum()} · 输 {(vals < 0).sum()} · "
              f"平 {(vals == 0).sum()} · 取值 {sorted(set(vals.tolist()))}")

    print(f"\n{'λ':>6} {'std(adv)':>10} {'corr(adv,R)':>12} {'R²':>7} "
          f"{'中位 λ^m':>11} {'m中位':>7} {'带信号步':>9}")
    for lam in LAMS:
        adv, _ret = T.gae_segments(steps, gamma=1.0, lam=lam)
        R = np.array([seg_outcome.get(T._segment_key(s), np.nan) for s in steps])
        m = np.array([seg_last[T._segment_key(s)] - i for i, s in enumerate(steps)])
        ok = ~np.isnan(R)
        if ok.sum() > 2 and np.std(adv[ok]) > 1e-9 and np.std(R[ok]) > 1e-9:
            corr = float(np.corrcoef(adv[ok], R[ok])[0, 1])
        else:
            corr = float("nan")
        lev = lam ** m
        sig = float(((lev * np.abs(R)) >= 0.1).sum()) / max(1, int(ok.sum()))
        print(f"{lam:>6.2f} {np.std(adv):>10.4f} {corr:>12.4f} {corr ** 2:>7.4f} "
              f"{np.median(lev):>11.3e} {np.median(m):>7.0f} {sig:>9.1%}")
    print("\n★ 判读：λ 的信用视界 ≈ 1/(1−λ) 步；这一局每方中位 "
          f"{np.median(lens):.0f} 步 ⇒ 要让**整局**的结果都传回去，λ 必须≈1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
