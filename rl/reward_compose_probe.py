# -*- coding: utf-8 -*-
"""**势函数差分里到底哪一项在驱动学习** —— 用户 2026-09-29 的诊断：「他们不愿意打野，
喜欢冲野地撞对面的地的位置」。

    python -m rl.reward_compose_probe <ckpt> [局数] [size] [nations]

★ 为什么这么测：训练里每一步的奖励是 `tanh(Δscore / REWARD_TANH_SCALE)`，
  而 `score` 是**一堆加权项之和**（国土/兵力/击杀/血/逼近/威胁/守家/厅）。
  用户怀疑"冲野地占位"是被奖励**奖励出来**的 —— 那就该能从"哪一项贡献了最多的
  `Σ|Δ|`"上看出来。

★★ 口径（关键）：**同一条轨迹上比各项** —— 因为**奖励不参与采样**
  （策略的动作只看观测，不看奖励）⇒ 把权重换成"只留某一项"**不会改变轨迹**，
  于是各变体是在**同一串状态**上算的，可以直接比。
  ★ 反过来说：**这个探针测的是"奖励的形状"，不是"策略的行为"**。
    策略行为要看动作分布（那是另一件事）。

★ 只读：不改任何权重文件，跑完把 `scoring` 的常量还原。
"""
from __future__ import annotations

import sys

import numpy as np
import torch

from . import scoring as S
from . import train as T
from .eval_fixed import load_pool
from .sandbox import Sandbox

# 参与比较的项（`evaluate._one` + `score` 里用到的那些权重）
TERMS = ("W_TILE", "W_ARMY", "W_KILL", "W_HP", "W_NEAR", "W_THREAT", "W_GUARD", "W_HALL")


def main() -> None:
    ckpt = sys.argv[1] if len(sys.argv) > 1 else "rl/runs/par_2p/l1_only.pt"
    ng = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    k = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    torch.set_num_threads(1)

    pool = load_pool(ckpt, 1, log=lambda *a: None)
    net = pool[0]
    net.eval()
    orig = {t: getattr(S, t) for t in TERMS}
    print(f"载入 {ckpt} · {ng} 局 · size {size} · {k} 国")
    print(f"权重：{' '.join(f'{t}={orig[t]:g}' for t in TERMS)}\n")

    # 累加：每项单独打开时的 Σ|Δ|；以及"全部打开"时的 Σ|Δ|
    acc = {t: 0.0 for t in TERMS}
    acc["__all__"] = 0.0
    steps = 0

    def val(sb, me):
        """在当前状态上，逐项单独打开算 score（返回 dict）。"""
        out = {}
        for only in list(TERMS) + ["__all__"]:
            for t in TERMS:
                setattr(S, t, orig[t] if (only in (t, "__all__")) else 0.0)
            out[only] = T._score(sb, me)
        for t in TERMS:                       # ★ 还原（别把权重留在半路上）
            setattr(S, t, orig[t])
        return out

    rng = np.random.default_rng(0)
    for g in range(ng):
        sb = Sandbox(seed=8000 + g, size=size, t_max=100, n_nations=k,
                     halls_known=True, territory=True).reset()
        prev: dict = {}
        n = 0
        while not sb.is_terminal() and n < 4000:
            n += 1
            sb._auto_advance()
            if sb.is_terminal():
                break
            me = sb.current_player()
            if me is None:
                break
            acts = sb.legal()
            if not acts:
                sb.pending.pop(0)
                continue
            cur = val(sb, me)
            if prev:
                for t in cur:
                    acc[t] += abs(cur[t] - prev[t])
            prev = cur
            steps += 1
            # ★ 采样（用户 09-18：「判据只看采样臂」）—— 而且奖励不参与采样 ⇒ 轨迹固定
            obs = T.collate([T.encode.obs_of(sb, me, acts)])
            with torch.inference_mode():
                lg, _ = net(obs)
            p = torch.softmax(lg[0], -1).numpy()
            sb.step(acts[int(rng.choice(len(p), p=p))])

    tot = acc["__all__"]
    print(f"{steps} 个决策点（{ng} 局）\n")
    print(f"{'项':>10}{'Σ|Δ|':>14}{'占比':>9}")
    print("-" * 34)
    for t in sorted(TERMS, key=lambda x: -acc[x]):
        print(f"{t:>10}{acc[t]:>14.1f}{acc[t] / max(tot, 1e-9) * 100:>8.1f}%")
    print(f"{'全部':>10}{tot:>14.1f}")
    print("\n★ 读法：某一项占比高 ⇒ 势函数差分**主要由它驱动**（策略会去刷它）。")
    print("  各项之和 > 全部 是正常的（单独打开时 tanh 的分母不变、绝对值相加不是线性的）。")


if __name__ == "__main__":
    main()
