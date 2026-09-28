# -*- coding: utf-8 -*-
"""**策略梯度里到底有没有一致的方向** —— 劈半对拍。

    python rl/grad_snr_probe.py <ckpt> [局数] [size_min] [size_max]

★ 为什么这么测：设 `g = ∇θ L_policy`。PPO 一步的位移 ≈ `-lr·g`。
  若 `g` 是**零均值噪声**，两半数据各自算出来的 `g₁`、`g₂` **互不相关**
  （`cos ≈ 0`），参数就只做随机游走 —— **挪不动**，策略永远停在均匀。
  若 `g` 有**一致方向**，`cos(g₁,g₂)` 会明显为正 ⇒ 更新会累积。
  ★ 实测佐证：iter 190→455（**265 个 iter**、≈13000 次 Adam 更新）参数范数只挪了
    **~3%**、单权最大变化 **0.006**。若方向一致，`13000 × 3e-4 = 3.9` 是上限
    ⇒ 0.006 说明**符号基本在抵消**。

★★ 口径：**用生产路径本身**（`T.ppo_update`）在**两份克隆**上各跑一遍，
  比的是**参数的位移**（≈ `-lr·g`），不是自己另写一份损失 —— 免得测的不是同一个东西。
"""
import copy
import math
import sys

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import train as T                          # noqa: E402
from rl.model import build_model                    # noqa: E402
from rl.sandbox import Sandbox                      # noqa: E402

CKPT = sys.argv[1] if len(sys.argv) > 1 else "rl/runs/par_mem/mem1v1.pt"
NG = int(sys.argv[2]) if len(sys.argv) > 2 else 6
SMIN = int(sys.argv[3]) if len(sys.argv) > 3 else 10
SMAX = int(sys.argv[4]) if len(sys.argv) > 4 else 14
torch.set_num_threads(1)

sb0 = Sandbox(seed=1, size=SMIN, t_max=100, n_nations=2).reset()
base = build_model(mem_slots=8)
ran = T._load_ckpt(CKPT, {0: base}, mem_slots=8, log=lambda *a: None)
base.eval()
print(f"载入 {CKPT}（{ran} iter） · 打 {NG} 局收集 buffer\n")

# ---- 收一个 buffer（只取**甲**那一方的步，和生产里 `buf[成员]` 同形）----
buf = []
for g in range(NG):
    size = SMIN + (g % (SMAX - SMIN + 1))
    sb = Sandbox(seed=9100 + g, size=size, t_max=100, n_nations=2,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    nets = {p: copy.deepcopy(base) for p in sb.players}
    for n in nets.values():
        n.eval()
    steps, _ = T.collect_episode(nets, sb, rng=np.random.default_rng(g))
    buf += [s for s in steps if s.player == sb.players[0]]
print(f"buffer {len(buf)} 步（一半 {len(buf)//2}）\n")

if len(buf) < 200:
    print("★ 步数太少，量不动")
    sys.exit(1)


def run_half(steps):
    net = copy.deepcopy(base)
    net.train()
    before = [p.detach().clone() for p in net.parameters()]
    T.ppo_update(net, steps, epochs=2, lr=3e-4)
    return [p.detach() - b for p, b in zip(net.parameters(), before)]


half = len(buf) // 2
d1 = run_half(buf[:half])
d2 = run_half(buf[half:])
d_all = run_half(buf)

# ★ 逐参数的余弦 / 范数比 —— **必须逐张量算再合并**，不能把所有参数拉平成一个
#   向量（参数量级差几个数量级，拉平后会被最大的那几张主导）。
def stats(dx, dy):
    num = den1 = den2 = 0.0
    for a, b in zip(dx, dy):
        num += float((a * b).sum())
        den1 += float((a * a).sum())
        den2 += float((b * b).sum())
    return num / (math.sqrt(den1 * den2) + 1e-30), math.sqrt(den1), math.sqrt(den2)


cos12, n1, n2 = stats(d1, d2)
print(f"{'':<26}{'‖Δθ‖':>12}")
print(f"  前半更新 Δθ₁            {n1:>12.6f}")
print(f"  后半更新 Δθ₂            {n2:>12.6f}")
print(f"  全量更新 Δθ_all         {stats(d_all, d_all)[1]:>12.6f}")
print(f"\n  ★★ **cos(Δθ₁, Δθ₂) = {cos12:>+.4f}**")

# 理论：若两半独立、信号占比为 f，则 cos ≈ f（信噪比的直接读数）
print(f"\n  读法：cos ≈ **{cos12:.3f}**")
if cos12 < 0.05:
    print("   ⇒ **两半的方向基本无关 ⇒ 这个梯度是噪声**：更新只在随机游走，"
          "\n     策略永远挪不动。**加大数据量（batch）是唯一出路**，"
          "\n     或者说明优势里压根没有可学的方向。")
elif cos12 < 0.3:
    print("   ⇒ 方向弱相关：有信号但很小 ⇒ 每次更新大部分是噪声，需要**大得多的 batch**。")
else:
    print("   ⇒ 方向明显一致 ⇒ 更新应该能累积。那「策略不动」就另有原因"
          "（学习率/熵项/数值尺度），不是梯度没信号。")

# 参照：同一个 loss 面上的两次不同 minibatch 顺序（同一半数据）= 应该高度相关
d1b = run_half(buf[:half])
cos_same, _, _ = stats(d1, d1b)
print(f"\n  （对照：**同一半数据**重跑一次的 cos = {cos_same:+.4f} "
      f"—— 它≈1 才说明测量本身是稳的）")
