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

import numpy as np

from . import encode as E
from .sandbox import Sandbox

__all__ = ["defense_fixture", "correct_candidates", "task_reward", "train_task"]


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


# ================================================================ ★ 训练
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
