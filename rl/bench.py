# -*- coding: utf-8 -*-
"""给**一台机器**量三个数，用来决定「搬不搬」和「worker × 线程怎么摆」。

用户 2026-09-25：「**机房单核不如 ecs 的**」——
★ 这一句直接推翻了"核多就一定快"的前提：我那套"×24"的账**假设核一样快**。
  慢 2 倍剩 12×、慢 3 倍剩 8×、慢 5 倍剩 5× ⇒ **得先量单核比**。

量的三个数
──────────
① **单核标尺**：固定规模的 matmul 的 GFLOP/s —— 跟机器无关，用来算"单核比 `r`"。
   拿它在两台机器上各跑一次，`r = 本机 / ECS`。
② **collect 成本**（ms/步）：沙盒 `legal()` + 观测 + B=1 前向。
   ★ **基本单线程**（Python/numpy 占大头）⇒ 只有**加 worker** 能摊。
③ **update 成本**（秒/千步）在 `threads = 1,2,4,8` 下的曲线。
   ★ update 全是 matmul/注意力 ⇒ **多线程能铺**，但要量出**实际 scaling**
     （不假设理想线性；容器可能被超卖）。

怎么用这两个成本定摆法
──────────────────────
  设 collect 占比 `c`、update 的线程加速比 `s(T)`、单核比 `r`、物理核 `C`：
      一个 worker 用 T 线程的耗时 ∝ `c + (1-c)/s(T)`
      N 个 worker（N×T ≤ C）的总吞吐 ∝ `N / (c + (1-c)/s(T)) × r`
  ⇒ 两条约束同时看：
      · **总吞吐** ≈ `N×T`（核数铺满就行，N 与 T 怎么分差不太多）
      · ★★ **池子的多样性 ∝ N**（一条血脉一个 worker）
        ⇒ **在总吞吐相近的前提下，优先把 N 开大、T 开小**。
  ⇒ 缺省建议 `N ≈ C/2`、`T ≈ 2`，然后按实测的 `s(T)` 微调。

★ 结果写 **JSON 文件**（`/tmp/zhanguo_bench.json`），不靠 stdout ——
  `run_*.sh` 的 `| tee` 会块缓冲，配 `timeout` 被掐就一行都拿不到（踩过）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from rl.model import build_model                        # noqa: E402
from rl.sandbox import Sandbox                          # noqa: E402
from rl.train import collate, ppo_update, Step          # noqa: E402
from rl import encode                                   # noqa: E402


def matmul_gflops(n: int = 768, reps: int = 20) -> float:
    """★ 单核标尺：固定规模 matmul 的 GFLOP/s（跟机器无关，可跨机比）。"""
    torch.set_num_threads(1)
    a = torch.randn(n, n)
    b = torch.randn(n, n)
    for _ in range(3):
        a @ b                                          # 预热
    t0 = time.time()
    for _ in range(reps):
        a @ b
    dt = time.time() - t0
    return (2.0 * n ** 3 * reps) / dt / 1e9


def collect_cost(size: int, t_max: int, steps: int, seed: int = 3) -> dict:
    """② collect 的 ms/步：沙盒 + 观测 + 前向（B=1）。**基本单线程。**"""
    sb = Sandbox(seed=seed, size=size, n_nations=3, t_max=t_max,
                 halls_known=True).reset()
    net = build_model()
    rng = np.random.default_rng(0)
    t0 = time.time()
    n = 0
    with torch.no_grad():
        while n < steps and not sb.is_terminal():
            me = sb.current_player()
            if me is None:
                break
            acts = sb.legal()
            if not acts:
                break
            obs = encode.obs_of(sb, me, acts)
            logits, _ = net(collate([obs]))
            p = torch.softmax(logits[0], -1).numpy()
            p = p / max(1e-9, p.sum())
            sb.step(acts[int(rng.choice(len(p), p=p))])
            n += 1
    dt = time.time() - t0
    return {"步数": n, "ms每步": 1000 * dt / max(1, n), "秒": dt}


def update_cost(nsteps: int, threads_list, size: int = 16, t_max: int = 150) -> dict:
    """③ update 的**线程 scaling**：造一份真 obs 的 buffer，跑 `ppo_update`。

    ★ 只跑 1 个 epoch（`epochs=1`）—— 我们要的是**线程 scaling**，不是收敛质量。
    """
    sb = Sandbox(seed=11, size=size, n_nations=3, t_max=t_max,
                 halls_known=True).reset()
    rows: list = []
    rng = np.random.default_rng(0)
    while len(rows) < nsteps and not sb.is_terminal():
        me = sb.current_player()
        if me is None:
            break
        acts = sb.legal()
        if not acts:
            break
        obs = encode.obs_of(sb, me, acts)
        rows.append(Step(obs=obs, aidx=int(rng.integers(len(acts))), logp=-1.0,
                         value=0.0, reward=0.0, done=False, player=me))
        sb.step(acts[int(rng.integers(len(acts)))])
    out = {}
    for T in threads_list:
        torch.set_num_threads(T)
        net = build_model()
        t0 = time.time()
        ppo_update(net, list(rows), epochs=1, minibatch=128)
        out[str(T)] = time.time() - t0
    torch.set_num_threads(1)
    return {"步数": len(rows), "各线程数耗时秒": out,
            "秒每千步": {k: 1000 * v / max(1, len(rows)) for k, v in out.items()}}


def train_vs_infer(mb: int = 128, reps: int = 20, size: int = 16,
                   t_max: int = 150) -> dict:
    """④ ★★ **同一批数据**：只前向（推理） vs 前向+反向+优化器（训练），耗时比。

    用户 2026-09-26：「你测过**训练和推理速度差多少**吗」。

    ★★ 为什么不拿现成的两个数相除（那是**错的**）：
      · `collect` 的 ms/步 = **B=1 的前向** —— 批量 1 吃不到 BLAS 的批量收益，
        每步都按"小矩阵"最慢的那一档算；
      · `update` 的 ms/步 = 整批（minibatch=128）**一次前向+反向**，再摊到 128 步上。
      ⇒ 两者**不是同一个口径**；直接相除甚至会得出"训练比推理快"。
      ⇒ 正经量法：**同一批 obs、同一个 batch size**，只差"有没有 backward + optimizer.step"。
        这才是"训练比推理慢几倍"的标准答案（也是决定"能不能把评估铺开跑"的数）。
    """
    sb = Sandbox(seed=11, size=size, n_nations=3, t_max=t_max,
                 halls_known=True).reset()
    rows: list = []
    rng = np.random.default_rng(0)
    while len(rows) < mb and not sb.is_terminal():
        me = sb.current_player()
        if me is None:
            break
        acts = sb.legal()
        if not acts:
            break
        rows.append(Step(obs=encode.obs_of(sb, me, acts),
                         aidx=int(rng.integers(len(acts))), logp=-1.0,
                         value=0.0, reward=0.0, done=False, player=me))
        sb.step(acts[int(rng.integers(len(acts)))])
    batch = collate([s.obs for s in rows])
    net = build_model()
    net.train()
    opt = torch.optim.Adam(net.parameters(), lr=1e-4)

    def _run(train: bool) -> float:
        t0 = time.time()
        for _ in range(reps):
            if train:
                opt.zero_grad()
                out = net(batch)
                (out[0].sum() + out[1].sum()).backward()
                opt.step()
            else:
                with torch.no_grad():
                    net(batch)
        return (time.time() - t0) / reps

    inf = _run(False)
    tr = _run(True)
    return {"batch": len(rows), "推理_毫秒": 1000 * inf, "训练_毫秒": 1000 * tr,
            "训练是推理的几倍": tr / max(inf, 1e-9),
            "每样本_推理_微秒": 1e6 * inf / max(1, len(rows)),
            "每样本_训练_微秒": 1e6 * tr / max(1, len(rows))}


def main() -> None:
    ap = argparse.ArgumentParser(description="量这台机器能不能跑、怎么摆")
    ap.add_argument("--threads-list", default="1,2,4,8",
                    help="update 要试的线程数（逗号分隔）")
    ap.add_argument("--collect-steps", type=int, default=1500)
    ap.add_argument("--update-steps", type=int, default=2000)
    ap.add_argument("--size", type=int, default=16, help="采样用的地图边长（取中档）")
    ap.add_argument("--t-max", dest="t_max", type=int, default=150)
    ap.add_argument("--out", default="/tmp/zhanguo_bench.json")
    a = ap.parse_args()

    tl = [int(x) for x in a.threads_list.split(",") if x.strip()]
    res: dict = {
        "机器": os.uname().nodename,
        "物理核(nproc)": os.cpu_count(),
        "torch": torch.__version__,
        "线程环境": {k: os.environ.get(k) for k in
                     ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")},
    }
    res["单核标尺_GFLOPs"] = matmul_gflops()
    res["collect"] = collect_cost(a.size, a.t_max, a.collect_steps)
    res["update"] = update_cost(a.update_steps, tl, size=a.size, t_max=a.t_max)
    # ★★ 「训练比推理慢几倍」—— 同批数据的同口径对比（见 `train_vs_infer`）。
    #   ★ 它跑在**单线程**上（`update_cost` 收尾把线程数复位成 1）：
    #     "训练/推理的倍数"是**算法口径**（多一次反向+优化器），不该被线程数混淆。
    res["训练vs推理"] = train_vs_infer(size=a.size, t_max=a.t_max)
    # ★ 推算：把两块成本拼起来看"一个 worker 用 T 线程"的相对耗时
    c = res["collect"]["秒"] / (res["collect"]["秒"] + res["update"]["各线程数耗时秒"].get("1", 1))
    res["collect占比"] = c
    t1 = res["update"]["各线程数耗时秒"].get("1")
    if t1:
        res["线程加速比"] = {k: t1 / v for k, v in res["update"]["各线程数耗时秒"].items()}
    with open(a.out, "w") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(f"已写入 {a.out}")


if __name__ == "__main__":
    main()