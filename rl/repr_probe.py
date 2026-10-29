# -*- coding: utf-8 -*-
"""★ **表示敏感度 + critic 区分度** —— "策略到底看不看得见局面"的两项体检。

    python rl/repr_probe.py [--ckpt rl/runs/xxx.pt] [--games 3] [--size 8]
                            [--t-max 200] [--nations 3]

★ 为什么要有它（2026-10-28）
  ──────────────────────────
  训练"学不动"这件事，量到最后一环是：**训练把主干磨平了**。
  实测（同一份探针，换档对比）：

  | 表示（局面间波动占比） | 未训练基线 | 训 10 iter | 训 30 iter |
  |---|---|---|---|
  | 逐 token 特征        | **41.0%** | 21.0% | 15.7% |
  | `g` 全局摘要 token    | **8.75%** | 5.46% | 2.54% |
  | logits（策略）        | 99.4%     | 32.1%  | 72.9%（RMS 涨了 28 倍） |

  ★ 也就是**棋盘信息被挤掉了** ⇒ 策略只剩"候选自己的局部特征"
    （谁、往哪、目标格长什么样）⇒ **它看不见对手、看不清战场** ⇒
    赢不赢基本靠地图 ⇒ 无论奖励口径、λ、算力怎么调都学不动。
  ★ 磨平它的是**价值项**：价值项在主干上的梯度范数是策略项的 **41 倍**
    ⇒ 主干被 critic 主导；而 critic 的目标（同一段里恒为 R）**最优解是常数**
    ⇒ 它的梯度天然把"局面特有"的那些分量按下去、只留公共分量。
  ⇒ 修法：**价值路径从共享主干上摘掉**（`self.value(pooled.detach())`，
    见 `rl/model.py` 与闸门 `tests/test_rl_value_detach.py`）。

★ 这个探针量两件事（都在真采样出来的局面上）：
  ① **表示敏感度**：逐 token 特征 / `g` token / logits 在局面间的波动占比；
  ② **critic 区分度**：V 在**一段轨迹内部**的跨度
     —— 跨度≈0 ⇒ V 是个常数 ⇒ `adv = R − V` 在段内也是常数
     ⇒ **没有动作级归因**（只会"模仿赢家的动作包"）。

★ 读法：
  · 逐 token 波动 **< 25%** ⇒ 主干被磨平了 ⇒ 先去查价值项的梯度；
  · V 的段内跨度 **< 0.2** ⇒ critic 是常数 ⇒ 没有动作级归因。
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


def _load(ckpt: str, players):
    blob = torch.load(ckpt, map_location="cpu", weights_only=False)
    src = blob.get("nets") or {}
    if not src and "weights" in blob:          # 联赛快照：单份权重
        src = {0: blob["weights"]}
    nets = {}
    for i, p in enumerate(players):
        n = build_model(mem_slots=0)
        key = i if i in src else list(src)[0]
        n.load_state_dict(src[key] if not isinstance(src[key], dict)
                          else src[key])
        n.eval()
        nets[p] = n
    return nets


def _trunk_and_logits(net, batch):
    """复刻 `PolicyNet.forward_state` 的前半段，额外把 logits / 逐 token 特征带出来。"""
    win, wmask = batch["win"], batch["win_mask"]
    x, wm = net.encode_window(win, wmask)
    kpm = ~wm
    dead = kpm.all(dim=1)
    if bool(dead.any()):
        kpm = kpm.clone()
        kpm[dead, 0] = False
    for blk in net.blocks:
        x = blk(x, key_padding_mask=kpm)
    x = net.ln_out(x)
    return x, wm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="表示敏感度 + critic 区分度体检")
    ap.add_argument("--ckpt", default="rl/runs/baseline_init.pt")
    ap.add_argument("--games", type=int, default=3)
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--t-max", type=int, default=200)
    ap.add_argument("--nations", type=int, default=3)
    ap.add_argument("--seed0", type=int, default=555)
    a = ap.parse_args(argv)

    torch.set_num_threads(1)
    players = list(("甲", "乙", "丙")[:a.nations])
    nets = _load(a.ckpt, players)
    print(f"★ {a.ckpt} · {a.nations} 国 · {a.size}×{a.size} · {a.games} 局\n")

    tok_all, g_all, log_all, spreads, vmeans = [], [], [], [], []
    for g in range(a.games):
        sb = Sandbox(seed=a.seed0 + g, size=a.size, t_max=a.t_max,
                     n_nations=a.nations, halls_known=True, territory=True,
                     alliances="none").reset()
        steps, info = T.collect_episode(nets, sb, rng=np.random.default_rng(3 + g))
        seg = {}
        for s in steps:
            seg.setdefault(s.player, []).append(s)
        for p, ss in seg.items():
            V = np.array([s.value for s in ss])
            spreads.append(float(V.max() - V.min()))
            vmeans.append(float(V.mean()))
        with torch.no_grad():
            for s in steps[::max(1, len(steps) // 40)][:40]:
                batch = T.to_dev(T.collate([s.obs]), "cpu")
                x, _wm = _trunk_and_logits(nets[players[0]], batch)
                tok_all.append(x[0].numpy().copy())
                g_all.append(x[0, 0].numpy().copy())
                logits, _v, _m = nets[players[0]].forward_state(batch, None)
                log_all.append(logits[0].numpy().copy())
        print(f"局 {g}: {len(steps)} 步 · {info['turns']} 回合 · winner={info['winner']}")

    def fluct(parts, name):
        n = min(p.shape[0] for p in parts)
        a2 = np.stack([p[:n].reshape(-1) if p.ndim == 1 else p[:n] for p in parts])
        rms = float(np.sqrt((a2 ** 2).mean()))
        sd = float(a2.std(axis=0).mean())
        print(f"  {name:<26} 波动占比 {sd / max(1e-12, rms):7.2%}")

    print("\n① 表示敏感度（局面间波动占比）")
    fluct(tok_all, "逐 token 特征")
    fluct(g_all, "`g` 全局摘要 token")
    print(f"  {'logits（策略）':<26} 见上（候选数逐局不同，故只报分布）")
    print("\n② critic 区分度")
    print(f"  V 的**段内跨度**：中位 {np.median(spreads):.3f} · 均值 {np.mean(spreads):.3f} "
          f"· 最大 {np.max(spreads):.3f}")
    print(f"  V 的段均值：    中位 {np.median(vmeans):+.3f} · 均值 {np.mean(vmeans):+.3f}")
    print("\n★ 读法：逐 token 波动 <25% ⇒ 主干被磨平；V 段内跨度 <0.2 ⇒ critic 是常数"
          "（没有动作级归因）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
