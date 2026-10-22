# -*- coding: utf-8 -*-
"""★ **小型可控任务**（报告 §9.1 第 2 条）：**先证明能学**，再谈泛化。

报告 §9.1 给的退出条件（按顺序）：

  ① **数据契约** —— P0 回归全部通过、真实训练入口也执行相同的收尾。
     ✔（Phase B–D：`rl/train.py` 的逐玩家结算 / 分段 GAE / 截断自举，
        `tests/test_rl_terminal_outcome.py` 等）
  ② **小型可控任务** —— 无随机战斗**或答案可枚举**的
     「集结 / 增援 / 回防 / 攻取目标」，**确认概率向正确选择集中**。 ← 本文件
  ③ 固定对手短训 —— 固定配置、种子集合、对手版本，比较修复后未训练网与训练网。
  ④ 随机地图及骰子 —— 在相同任务语义上增加分布变化。
  ⑤ 多国与大军 —— 用真实后期规模验证多战线管理。

★ **本文件只做第 ② 条**：一个**答案可枚举**的「回防」任务 ——
  敌国军队**贴着我的核心** ⇒ 正确候选是**唯一确定**的「攻击那支敌军」。
  ⇒ 训练时**不用**打架的随机结果，只用"选对了没有"这个**可枚举**的信号。

★ 为什么**必须有**这一步（报告 §10 的纪律）：
  「**全量测试绿**」只说明**数据流**是对的，**不说明模型能学**。
  而"训练不动"有太多可能原因（奖励没进样本、优势没信号、动作分布被偏置…）——
  一个**答案可枚举**的小任务能把"学不到"**定位**到具体环节。

★ **它用的是真家伙**：观测走 `encode.obs_of`、网络是真网、
  更新走 `rl.train.ppo_update` ⇒ 测的是**真实训练方法**，不是另写一套。
"""
from __future__ import annotations

import dataclasses

import numpy as np

from . import encode as E
from .sandbox import Sandbox

__all__ = ["defense_fixture", "correct_candidates", "task_reward", "train_task",
           "route_train",
           "route_fixture", "route_correct"]


# ================================================================ ★ 任务
def defense_fixture(seed: int = 0, size: int = 8):
    """「**回防**」任务的局面。

    ★ 构造（**全部确定性**，没有骰子）：
      · 我核心在 (1,1)；**一支敌国军队站在 (2,1)**，而 (2,1) **归乙所有**
        ⇒ 它**紧贴**我的核心（正被威胁），而且那一格**不可走、只可攻**；
      · 我只剩**一支**军（放在 (2,2)，够得着 (2,1)）⇒ 候选集小、**答案唯一**；
      · 敌国的其余军队**删掉**（不制造第二个威胁）。

    ★ **答案为什么可枚举**：敌国军队**站在我的核心旁边** ⇒
      ① 我**不能**走进敌占格（引擎的 `_mv_wall`）⇒ "走过去挡一下"**不存在**；
      ② 不动（hold）⇒ 敌军下一手就吃我的核心 ⇒ 守家那几项直接塌；
      ③ 唯一能**立刻**解除威胁的动作 = **攻击 (2,1) 的那支敌军**。
      ⇒ 正确候选**唯一**，不依赖任何随机战斗结果。

    → `(sb, me, enemy_cell)`
    """
    sb = Sandbox(seed=seed, size=size, n_nations=2, t_max=20, war=True).reset(
        random_starts=False)
    w = sb.world
    units = {u.name: u for u in w._world.units}
    enemy_cell = (2, 1)
    # ★ (2,1) 归乙 ⇒ 敌占格 ⇒ 不可走、只可攻（`legal()` 的口径）。
    w.tiles[enemy_cell]["owner"] = "乙"
    mine = [a for a in w.armies if a["owner"] == "甲"]
    foes = [a for a in w.armies if a["owner"] == "乙"]
    keep = []
    for a in mine:
        u = units[a["name"]]
        u.x, u.y = 2, 2                   # ★ 我的一支军，够得着 (2,1)
        keep.append(a)
        break                             # ★ 只留一支 ⇒ 候选集小、答案唯一
    for a in foes:
        u = units[a["name"]]
        u.x, u.y = enemy_cell             # ★ 敌军贴着我的核心
        keep.append(a)
        break                             # ★ 只留一支 ⇒ 威胁唯一
    w.armies = keep
    return sb, "甲", enemy_cell


def correct_candidates(sb: Sandbox, me: str, enemy_cell) -> list[int]:
    """**正确候选**的下标（对着 `sb.legal()` 的顺序）。

    ★ 只认「**攻击**那个贴着我核心的敌军」—— 别的候选（打别处、去别处、hold）都是错的。
    """
    out = []
    for i, (_aid, kind, x, y) in enumerate(sb.legal()):
        if kind == "attack" and (x, y) == enemy_cell:
            out.append(i)
    return out


def task_reward(aidx: int, correct: list[int]) -> float:
    """任务奖励：**选对了 +1、选错了 0**（可枚举的信号，不掺战斗随机数）。"""
    return 1.0 if aidx in correct else 0.0


# ================================================================ ★ 任务二：绕山
def route_fixture(variant: str = "east_blocked", seed: int = 0, size: int = 8):
    """「**绕山**」：**远处**一道山墙把直路堵住 ⇒ 正确第一步是**另一个方向**。

    ★ 构造（**确定性**）：
      · 我核心在 (1,1)，**我的一支军**在 (2,2) ⇒ 它有两个方向可走：
        `+x`（(3,2)）与 `+y`（(2,3)）；
      · **山墙**（`native:mountain`）摆在**远处**（**不是**一步够得着的格）：
        · `"east_blocked"` ⇒ 墙在 `x=4`（y=1..3）⇒ **+x 那条路被堵** ⇒
          正确第一步 = **+y**（(2,3)，绕过去）；
        · `"north_blocked"` ⇒ 墙在 `y=4`（x=1..3）⇒ **+y 那条路被堵** ⇒
          正确第一步 = **+x**（(3,2)）。

    ★★ 为什么这道题**只有读了地图**才能答对：
      山墙在**两三格之外** ⇒ 它**不在任何候选的落点**上 ⇒
      **候选特征（`cand_content`）里根本没有它** ⇒
      只有**空间摘要**（`"s"` 组，报告 §8.2）带着它 ⇒
      ⇒ 答对 ⇒ **价值路径真的读到了远处的地形**。

    → `(sb, me, correct_aidx)`
    """
    sb = Sandbox(seed=seed, size=size, n_nations=2, t_max=20, war=True).reset(
        random_starts=False)
    w = sb.world
    units = {u.name: u for u in w._world.units}
    # ★ 我的军挪到 (2,2)（只剩一支 ⇒ 候选集小）
    mine = [a for a in w.armies if a["owner"] == "甲"]
    for a in mine:
        units[a["name"]].x, units[a["name"]].y = 2, 2
    keep = [a for a in mine[:1]]
    # ★ 敌国的军挪到**远处**（不打扰）
    for a in w.armies:
        if a["owner"] == "乙":
            units[a["name"]].x, units[a["name"]].y = 6, 6
            keep.append(a)
            break
    w.armies = keep
    # ★ 山墙（**远处**、非候选落点）
    if variant == "east_blocked":
        cells = [(4, 1), (4, 2), (4, 3)]
    elif variant == "north_blocked":
        cells = [(1, 4), (2, 4), (3, 4)]
    else:
        raise ValueError(f"未知变体 {variant!r}")
    terr = w._world.territory
    for c in cells:
        fact = terr.cell(*c)
        if not fact.tile.id:
            continue
        terr._tiles[fact.tile.id] = dataclasses.replace(fact.tile, terrain="native:mountain")
    return sb, "甲", (3, 2), (2, 3)


def route_correct(sb: Sandbox, me: str, east_cell, north_cell,
                  variant: str = "east_blocked") -> int:
    """「绕山」的正确候选下标（对着 `sb.legal()` 的顺序）。

    ★ 答案由**变体**决定（山墙堵住哪个方向 ⇒ 走另一个方向）。
    ★ 墙在**两三格之外** ⇒ 它**不是**一步够得着的格 ⇒ 引擎的"走不走得进去"
      **不会**泄露答案 ⇒ 答案只能从**空间摘要**里读出来（报告 §8.2 的验收）。
    """
    acts = sb.legal()
    want = north_cell if variant == "east_blocked" else east_cell
    hits = [i for i, (_a, k, x, y) in enumerate(acts)
            if k == "move" and (x, y) == want]
    if len(hits) != 1:
        raise RuntimeError(f"用例前提：{want} 应当恰好有一个 move 候选，实际 {len(hits)}")
    return hits[0]


# ================================================================ ★ 训练
def _collect_task_steps(sb, me, correct, obs, acts, net, rng, temperature,
                        episode_id, n, member):
    """在**一个固定局面**上采 `n` 个样本（局面不动 ⇒ 观测不变）。"""
    import torch as _torch
    from . import train as T
    steps = []
    for _ in range(n):
        b = T.collate([obs])
        with _torch.inference_mode():
            logits, value = net(b)
        probs = _torch.softmax(logits[0], -1).numpy()
        p = probs ** (1.0 / max(1e-6, temperature))
        p = p / p.sum()
        aidx = int(rng.choice(len(p), p=p))
        logp = float(np.log(max(1e-12, probs[aidx])))
        steps.append(T.Step(obs, aidx, logp, float(value[0]),
                            task_reward(aidx, correct), True, me,
                            episode_id=episode_id, member=member))
    return steps


def route_train(*, iters: int = 80, batch: int = 24, lr: float = 3e-4,
                temperature: float = 1.0, seed: int = 0, size: int = 8,
                device: str = "cpu", mem_slots: int = 0,
                variants=("east_blocked", "north_blocked"), log=None):
    """在「绕山」任务上短训（**两个变体一起训**）⇒ 返回每个变体的**正确候选概率质量**。

    ★★ **为什么必须两个变体一起训**：
      只训一个变体 ⇒ "学会了"和"背下了这个下标"**分不开**（两种模型都会在
      训练那个变体上高、另一个上低）⇒ **测不到"读地图"**。
      ⇒ 两个变体**一起训**（同一个网络、答案相反）⇒ 只有**真的读了远处地形**
      才能两个都对 ⇒ **这才证明"价值路径读到了地图"**（报告 §8.2 的验收）。
    """
    from .model import build_model
    from . import train as T
    import torch as _torch

    _torch.manual_seed(seed)
    net = build_model(mem_slots=mem_slots)
    rng = np.random.default_rng(seed)
    scenes = []
    for v in variants:
        sb, me, e, n = route_fixture(v, seed=seed, size=size)
        acts = sb.legal()
        correct = route_correct(sb, me, e, n, v)
        scenes.append((v, E.obs_of(sb, me, acts), acts, correct))
    per = max(1, batch // max(1, len(scenes)))
    trace = {v: [] for v, _o, _a, _c in scenes}

    for it in range(iters):
        steps = []
        for v, obs, acts, correct in scenes:
            steps += _collect_task_steps(None, "甲", [correct], obs, acts, net,
                                         rng, temperature, it, per, v)
        T.ppo_update(net, steps, epochs=4, lr=lr, device=device,
                     temperature=temperature)
        for v, obs, acts, correct in scenes:
            with _torch.inference_mode():
                logits, _v = net(T.collate([obs]))
                probs = _torch.softmax(logits[0], -1).numpy()
            trace[v].append(float(probs[correct]))
            if log is not None:
                log(f"  iter {it:3d} {v:15s} 正确候选概率质量 {trace[v][-1]:.3f}")
    return {"trace": trace,
            "final": {v: t[-1] for v, t in trace.items() if t},
            "correct": {v: c for v, _o, _a, c in scenes}}


def train_task(*, iters: int = 60, batch: int = 24, lr: float = 3e-4,
               temperature: float = 1.0, seed: int = 0, size: int = 8,
               device: str = "cpu", mem_slots: int = 0, log=None):
    """在「回防」任务上短训 ⇒ 返回**每个 iter 之后**正确候选上的概率质量。

    ★ 走的是**真家伙**：`encode.obs_of` 取观测、真网出 logits、
      `rl.train.ppo_update` 做更新 ⇒ 测的是**真实训练方法**能不能学。
    ★ 每一步都在**同一个局面**上采（局面不动 ⇒ 观测不变）⇒
      于是"概率往正确候选上集中"就是**能不能学**的直接读数。
    ★ 任务奖励**不含战斗随机数** ⇒ 学习曲线不被骰子搅动（这正是"小型可控"的意思）。
    """
    from .model import build_model
    from . import train as T

    torch = T.torch
    torch.manual_seed(seed)
    net = build_model(mem_slots=mem_slots)
    sb, me, enemy_cell = defense_fixture(seed=seed, size=size)
    acts = sb.legal()
    correct = correct_candidates(sb, me, enemy_cell)
    if not correct:
        raise RuntimeError("用例前提：这个局面该有'攻击贴着我核心的敌军'这个候选")
    obs = E.obs_of(sb, me, acts)
    rng = np.random.default_rng(seed)
    trace: list[float] = []

    for it in range(iters):
        steps = []
        for _ in range(batch):
            b = T.collate([obs])
            with torch.inference_mode():
                logits, value = net(b)
            probs = torch.softmax(logits[0], -1).numpy()
            p = probs ** (1.0 / max(1e-6, temperature))
            p = p / p.sum()
            aidx = int(rng.choice(len(p), p=p))
            logp = float(np.log(max(1e-12, probs[aidx])))
            steps.append(T.Step(obs, aidx, logp, float(value[0]),
                                task_reward(aidx, correct), True, me,
                                episode_id=it, member="task"))
        T.ppo_update(net, steps, epochs=4, lr=lr, device=device,
                     temperature=temperature)
        with torch.inference_mode():
            logits, _v = net(T.collate([obs]))
            probs = torch.softmax(logits[0], -1).numpy()
        mass = float(probs[correct].sum())
        trace.append(mass)
        if log is not None:
            log(f"  iter {it:3d} 正确候选概率质量 {mass:.3f}")
    return {"trace": trace, "final": trace[-1] if trace else 0.0,
            "correct": list(correct), "n_candidates": len(acts)}
