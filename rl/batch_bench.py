# -*- coding: utf-8 -*-
"""前向的**批大小 scaling** —— 判断它是「算力不够」还是「启动开销吃掉」。

★★ 2026-09-26 实测（P4 那台，size16，K=45，记忆臂）：

| B | cpu ms/样本 | cuda ms/样本 |
|---|---|---|
| 1 | 5.352 | 5.595 |
| 2 | 3.271 | 2.866 |
| 4 | 2.455 | 1.447 |
| 8 | 1.975 | 0.726 |
| 16 | 1.867 | 0.377 |
| 32 | **1.557** | **0.180** |

★★ 两条判读（都反直觉，且解释了一堆旧矛盾）：
  ① **cuda 的「每批」耗时几乎不随 B 变**（B=1→32 是 5.60→5.77 ms）
     —— 这是**纯启动开销**的特征：GPU 把整批算完跟只算一个一样快。
  ② **B=1 时 cuda ≈ cpu（5.60 vs 5.35）** ⇒ **采集回路恒为 B=1**
     ⇒ **上卡一分钱不赚**。这才是「cuda 更慢」的真因
     （★ 不是"P4 算力低"，也不是"引擎太慢"——见 `rl/phase_bench.py`）。
  ③ cpu 只快 3.4×（5.35→1.56）⇒ CPU 侧更接近算力受限；GPU 侧是开销受限。

⇒ **真正的杠杆是「批量前向」，不是换设备**：把 N 个沙盒并排推进、把 N 帧拼成一个 batch。
  ★ 架构上本来就支持 —— `train.collate(rows)` 吃的就是**一串帧**，`to_dev` 负责搬设备；
    要改的只是"这些帧从哪来"（1 个环境 → N 个环境同拍推进）。
  ★★ 但**别把上限说大**：前向只占每步 48~56%（`rl/phase_bench.py`），
    其余（观测 21~29% / legal 10~12% / 打分 6~10%）**是按环境逐份算的、不会跟着批走**
    ⇒ 就算前向变成零，端到端上限也只有 **~2.3×**。剩下那 44% 是下一层的事。

用法：  python batch_bench.py [cpu|cuda] [size]
"""
import sys, time
import torch
sys.path.insert(0, ".")
from rl.sandbox import Sandbox
from rl.encode import obs_of
from rl.train import collate, to_dev
from rl.model import build_model

size, dev = 16, sys.argv[1] if len(sys.argv) > 1 else "cpu"
torch.manual_seed(0)
sb = Sandbox(seed=3, size=size, t_max=150, n_nations=3, halls_known=True,
             territory=True, alliances="random2v2").reset()
me = sb.current_player()
acts = sb.legal()
obs = obs_of(sb, me, acts)
net = build_model(mem_slots=8).to(dev).eval()
print(f"size={size} 设备={dev}  单帧候选 K={len(acts)}")
print(f"{'B':>4}{'ms/批':>10}{'ms/样本':>10}{'相对 B=1':>10}")
base = None
with torch.no_grad():
    for B in (1, 2, 4, 8, 16, 32):
        batch = to_dev(collate([obs] * B), dev)
        mem = net.mem_init(B, device=dev)
        for _ in range(5):
            _, _, mo = net.forward_state(batch, mem)
        if dev != "cpu":
            torch.cuda.synchronize()
        t = time.perf_counter()
        for _ in range(20):
            _, _, mo = net.forward_state(batch, mem)
        if dev != "cpu":
            torch.cuda.synchronize()
        ms = (time.perf_counter() - t) / 20 * 1000
        per = ms / B
        if base is None:
            base = per
        print(f"{B:>4}{ms:>10.2f}{per:>10.3f}{base / per:>9.1f}×")
