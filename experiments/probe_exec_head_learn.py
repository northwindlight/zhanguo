# -*- coding: utf-8 -*-
"""**可执行性头到底学不学得会** —— 第三条路的第一道闸（用户 2026-09-18：「验证」）。

## 问的是什么

`--exec-warmup` 会**冻结主干**、只训 `exec_head.*`。而 `exec_head` 是
`nn.Linear(d_model + d_cand, 1)`、输入是主干给的 `[h, q0]` ⇒ **warmup 能训出什么，
完全取决于「引擎的 `ok` 这个标签，在冻结表征里线性可分吗」**：

| 结果 | 含义 |
|---|---|
| 留出 AUC **明显高于 0.5**（比如 >0.8） | 信息**已经**在表征里 ⇒ 只要训头 + 在选择时用它，就能压掉撞墙。**第三条路通** |
| 留出 AUC ≈ 0.5 | 表征里没有可解码的"可执行性" ⇒ warmup 训不出好头，得**联合训主干**，而那有"两块把策略打回开局"的前科 ⇒ **这条路要重新设计** |

## 为什么用"缓存特征 + 训最后一层"而不是跑训练

主干冻结 ⇒ `[h, q0]` 是**常量特征**。挂一个 forward hook 把头部的输入抓下来一次，
之后训头就是**在固定特征上的逻辑回归**，几百步瞬间跑完，不必反复走主干。
而且它训的就是**真的那个 `exec_head` 模块本身**（不是仿制品）⇒ 结论直接可迁移。

## 口径

标签只有**被选中的那个候选**有（部分标签，和训练时一模一样）。
所以报的是"**对策略实际会选的动作**，头能不能预测它会被拒"—— 也正是它要被用到的场合。
基率（被拒占比）一并报出来，否则 AUC 不好读。

用法：
    python experiments/probe_exec_head_learn.py <ckpt> [<ckpt> ...] [--seed 900000] [--det]
    ZHANGUO_THREADS=1 ...   # ECS 是 1 物理核
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv  # noqa: E402
from rl.hw import set_threads  # noqa: E402
from rl.ppo import Rollout, act, forward_batch  # noqa: E402
from rl.tokenize import GROUPS, tokenize  # noqa: E402
from rl.transformer import WindowTransformer  # noqa: E402


def _opt(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


# ★位置参数只认**路径**：带逗号的（`--sweep 0,0.5,1` 的值）不是 ckpt。
#   之前漏了这一条 ⇒ 跑完扫描后又拿 "0,0.5,1,2,4" 当路径去 load，末尾白炸一次。
CKPTS = [a for a in sys.argv[1:]
         if not a.startswith("--") and not a.lstrip("-").isdigit() and "," not in a]
SEED = _opt("--seed", 900000)
TURNS = _opt("--turns", 200)
DET = "--det" in sys.argv
# β 扫描（第三条路的**效果**）：训练完头之后，同一张图上把 β 从 0 扫上去。
SWEEP = ([float(x) for x in sys.argv[sys.argv.index("--sweep") + 1].split(",")]
         if "--sweep" in sys.argv else [])
set_threads(int(os.environ.get("ZHANGUO_THREADS", "4")))

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=ACT_SAFETY)
env.reset(0)
_w = tokenize(env, env._obs())


def load(p: str):
    m = WindowTransformer({g: _w.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(p, map_location="cpu", weights_only=False)
    miss, _ = m.load_state_dict(ck["model"], strict=False)
    m.eval()
    print(f"  载入 {p}（缺失 {len(miss)} 项）", flush=True)
    return m


def collect(model, seed: int, beta: float = 0.0) -> Rollout:
    """跑一局，把每步的 `(win, 选中候选, ok)` 收进 Rollout（和训练同一条路）。

    `beta>0` 时**按第三条路的样子采样**（`use_exec` 与 `exec_beta` 同源）——
    于是这把尺量的就是"真开起来会怎样"，而不是另造一份推理逻辑。
    """
    torch.manual_seed(0x5EED)
    r = Rollout(lam=1.0, normalize=False)
    obs = env.reset(seed)
    while True:
        w = tokenize(env, obs)
        i, lp, v = act(model, obs, deterministic=DET, win=w,
                       use_exec=beta > 0, exec_beta=beta)
        keep = obs
        obs, rew, done, info = env.step(obs.cand["actions"][i])
        r.add(keep, i, lp, v, rew, done, win=w, ok=info["ok"], turn=info["turn"])
        if done:
            break
    return r


def episode_stats(model, seed: int, beta: float) -> dict:
    """跑一局，报第三条路最关心的三个数：撞墙率 / 消费 / 地。"""
    r = collect(model, seed, beta)
    n = len(r.steps)
    rej = sum(1 for s in r.steps if not s["ok"])
    s = env.summary()
    return {"steps": n, "rej": rej, "rate": rej / max(n, 1),
            "spend": s["spend_total"], "tiles": s["tiles"]}


def head_inputs(model, rollout) -> tuple[torch.Tensor, torch.Tensor]:
    """抓 `exec_head` 的输入（`[h, q0]`），只留**被选中那一行** —— 标签只有它有。"""
    got: list[torch.Tensor] = []
    hk = model.exec_head.register_forward_hook(lambda mod, inp, out: got.append(inp[0].detach()))
    xs, ys = [], []
    try:
        steps = rollout.steps
        with torch.no_grad():
            for s in range(0, len(steps), 64):
                mb = steps[s:s + 64]
                wins = [x["win"] for x in mb]
                forward_batch(model, mb, wins, return_exec=True)
                feats = got[-1]                      # [B, K, d]
                acts = torch.as_tensor([x["act"] for x in mb], dtype=torch.long)
                xs.append(feats[torch.arange(len(mb)), acts])
                ys.append(torch.as_tensor([float(x["ok"]) for x in mb]))
    finally:
        hk.remove()
    return torch.cat(xs), torch.cat(ys)


def auc(score: torch.Tensor, y: torch.Tensor) -> float:
    """秩和法 AUC（并列取平均秩），不引 sklearn。"""
    s = score.detach().double().numpy()
    yy = y.numpy().astype(bool)
    n1, n0 = int(yy.sum()), int((~yy).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = s.argsort()
    ranks = np.empty(len(s), float)
    ranks[order] = np.arange(1, len(s) + 1)
    # 并列 → 平均秩（否则 AUC 会被并列序号的高低偏置）
    for v in np.unique(s):
        m = s == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    return (ranks[yy].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def train_head(model, X: torch.Tensor, y: torch.Tensor, test: torch.Tensor,
               steps: int = 600, lr: float = 1e-2) -> dict:
    """在**缓存特征**上训 `model.exec_head` 本身（主干冻结 = warmup 的真实条件）。"""
    a0 = auc(model.exec_head(X[test]).squeeze(-1), y[test])
    opt = torch.optim.Adam(model.exec_head.parameters(), lr=lr)
    lossf = torch.nn.BCEWithLogitsLoss()
    tr = ~test
    for _ in range(steps):
        opt.zero_grad()
        loss = lossf(model.exec_head(X[tr]).squeeze(-1), y[tr])
        loss.backward()
        opt.step()
    a1 = auc(model.exec_head(X[test]).squeeze(-1), y[test])
    return {"auc_before": a0, "auc_after": a1}


t0 = time.time()
print(f"种子 {SEED}，各 {TURNS} 回合（{'贪心' if DET else '采样'}臂）\n", flush=True)
print(f"{'ckpt':<22}{'样本':>7}{'被拒基率':>10}{'AUC(训练前)':>13}{'AUC(训练后)':>13}", flush=True)

for p in CKPTS:
    m = load(p)
    r = collect(m, SEED)
    X, y = head_inputs(m, r)
    g = torch.Generator().manual_seed(0)
    test = torch.zeros(len(y), dtype=torch.bool)
    test[torch.randperm(len(y), generator=g)[: max(1, len(y) // 5)]] = True
    res = train_head(m, X, y, test)
    print(f"{Path(p).stem:<22}{len(y):>7}{float(1 - y.mean()):>10.1%}"
          f"{res['auc_before']:>13.3f}{res['auc_after']:>13.3f}", flush=True)
    if SWEEP:
        print(f"    β 扫描（同一张图 {SEED}）：", flush=True)
        print(f"    {'β':>6}{'被拒率':>9}{'消费':>10}{'地':>7}", flush=True)
        for b in SWEEP:
            st = episode_stats(m, SEED, b)
            print(f"    {b:>6.2f}{st['rate']:>8.1%}{st['spend']:>10,.0f}{st['tiles']:>7}",
                  flush=True)

print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)
