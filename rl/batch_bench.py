# -*- coding: utf-8 -*-
"""前向的**批大小 / 线程**扫描 —— 回答「前向只能靠算力改进吗，能利用多核吗」。

★★ 结论一句话：**B=1 的前向不是算力受限，是每算子调度开销受限** ⇒
   ① 不靠算力也有大空间（批量化、减 op 开销）；② 多核**能用，但必须先批量化**。

★ 证据一（`b` 模式）：**cuda 的「每批」耗时几乎不随 B 变**（B=1→32 是 5.60→5.77 ms）。
  P4 的算术吞吐是一个核的 ~40 倍；若时间花在算术上，B=32 该慢 32 倍 —— 它只慢 1.03 倍。

★ 证据二（`b` 模式，2026-09-26，size16/K=45/记忆臂）：

| B | cpu ms/样本 | cuda ms/样本 |
|---|---|---|
| 1 | 5.352 | 5.595 |
| 2 | 3.271 | 2.866 |
| 4 | 2.455 | 1.447 |
| 8 | 1.975 | 0.726 |
| 16 | 1.867 | 0.377 |
| 32 | **1.557** | **0.180** |

  ⇒ **B=1 时 cuda ≈ cpu**，而采集回路**恒为 B=1** ⇒ **上卡一分钱不赚**
  （这才是「cuda 更慢」的真因；不是「P4 算力低」，也不是「引擎太慢」——引擎只占 1.6%）。

★★ 证据三（`sweep` 模式）：**线程 scaling 随 B 变好** ——

| B | 1 线程 ms/样本 | 2 线程 | 4 线程 | 8 线程 |
|---|---|---|---|---|
| 1 | 5.079 | 1.15× | **1.26×** | 1.03× |
| 8 | 1.933 | 1.49× | 1.80× | **1.87×** |
| 32 | 1.454 | 1.27× | 2.45× | **2.87×** |

  ⇒ ★★ **「多线程烂」只在 B=1 成立**（小算子被同步开销吃掉），**大 B 下不成立**。
    B=32 走 4 线程是**甜点**（0.593 ms/样本；8 线程只再好 17% 却多占一倍核）。
  ★ 合起来：**B=1/1线程 5.079 → B=32/4线程 0.593 = 8.6×/样本**（纯 CPU，不换设备）。
  ★★ 但**别把上限说大**：前向只占每步 48~56%（`rl/phase_bench.py`），
    其余（观测 21~29% / legal 10~12% / 打分 6~10%）是**按环境逐份算的、不跟批走**
    ⇒ 前向归零，端到端也只有 **~2.3×**。剩下那 44% 是下一层。

★★★ **GIL 到底管不管这里**（用户问「python 多线程一直很烂」）：
  · **torch 张量那部分不受 GIL 限**（C++ 里会释放 GIL）⇒ 所以线程在大 B 下真能加速；
  · **纯 Python/numpy 那部分（观测编码、legal、打分）受 GIL 限** ⇒ 线程救不了，
    只有**多进程**能救 —— 这正是"多线程烂"这个印象的**真实来源**。
  ⇒ 「利用多核」的正确形状是：**进程内批量化（吃满一个核 + 少数线程），
    进程之间靠多 worker 铺开**；而不是给一个进程开 8 个线程。
    ★ 而多 worker 现在被「在训成员 mid 并账」挡着（PLAN §12.2 第 8 条 ⑤）。

用法：
    python rl/batch_bench.py b     [cpu|cuda] [size] [threads]   # 批大小扫描（缺省）
    python rl/batch_bench.py sweep [size]                        # 批 × 线程 二维扫描
"""
import sys
import time

import torch

sys.path.insert(0, ".")
from rl.encode import obs_of
from rl.model import build_model
from rl.sandbox import Sandbox
from rl.train import collate, to_dev


def _frame(size: int):
    """造一帧真观测。★ 候选数 K 要**从沙盒的 legal() 取** ——
    `cand_content` 是 `collate` **之后**才有的键，obs 里没有（我第一版就踩了）。"""
    sb = Sandbox(seed=3, size=size, t_max=150, n_nations=3, halls_known=True,
                 territory=True, alliances="random2v2").reset()
    acts = sb.legal()
    return obs_of(sb, sb.current_player(), acts), len(acts)


def _time(net, batch, mem, dev, reps):
    with torch.no_grad():
        for _ in range(3):
            net.forward_state(batch, mem)          # 预热
        if dev != "cpu":
            torch.cuda.synchronize()
        t = time.perf_counter()
        for _ in range(reps):
            net.forward_state(batch, mem)
        if dev != "cpu":
            torch.cuda.synchronize()
    return (time.perf_counter() - t) / reps * 1000


def sweep_b(size: int, dev: str, threads: int):
    obs, K = _frame(size)
    net = build_model(mem_slots=8).to(dev).eval()
    print(f"size={size} 设备={dev} 线程={threads}  K={K}")
    print(f"{'B':>4}{'ms/批':>10}{'ms/样本':>10}{'相对 B=1':>10}")
    base = None
    for B in (1, 2, 4, 8, 16, 32):
        batch = to_dev(collate([obs] * B), dev)
        ms = _time(net, batch, net.mem_init(B, device=dev), dev, 20)
        per = ms / B
        base = per if base is None else base
        print(f"{B:>4}{ms:>10.2f}{per:>10.3f}{base / per:>9.1f}×")


def sweep_threads(size: int):
    """批 × 线程：**必须分两个维度量** —— 只看 B=1 会把结论用错地方。"""
    obs, K = _frame(size)
    net = build_model(mem_slots=8).eval()
    print(f"size={size} cpu · K={K}")
    print(f"{'B':>4}{'线程':>6}{'ms/批':>10}{'ms/样本':>10}{'vs 1线程':>10}")
    for B in (1, 8, 32):
        batch = to_dev(collate([obs] * B), "cpu")
        ref = None
        for T in (1, 2, 4, 8):
            torch.set_num_threads(T)
            ms = _time(net, batch, net.mem_init(B), "cpu", 15)
            per = ms / B
            ref = per if ref is None else ref
            print(f"{B:>4}{T:>6}{ms:>10.2f}{per:>10.3f}{ref / per:>9.2f}×")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "b"
    if mode == "sweep":
        sweep_threads(int(sys.argv[2]) if len(sys.argv) > 2 else 16)
        return
    dev = sys.argv[2] if len(sys.argv) > 2 else "cpu"
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    if threads:
        torch.set_num_threads(threads)
    sweep_b(size, dev, threads)


if __name__ == "__main__":
    main()
