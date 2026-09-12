# -*- coding: utf-8 -*-
"""行为克隆冷启动：先跟规则 AI（扩张流 v6）学会「模式」，再交给 PPO 微调。

为什么需要它：从零 RL 时，所有配置都卡在同一处——**探索不出「复利链」**
（建产能 → 出兵 → 占地 → 再建产能）。这条链要几十回合才回本，而策略在学会它
之前就已经塌进「少做事」的局部最优（ent → 0.1，局末消费掉到 1 千）。

BC 把最难的那一步用现成的规则 AI 直接灌进去：`expand_rule_v6` 会建产能、会卖余量
换现金、会征兵、会扩张——**正是策略缺的那个"会做事"的先验**。

做法：拿规则 AI 跑局，在它**每次动手前**抓一帧观测（那一刻的状态就是该动作的输入），
把它的动作映射成 RL 候选清单里的下标，监督训练（交叉熵）。

    python3 -m rl.bc --episodes 30 --out rl/runs/bc/last.pt
    # 之后照常 PPO，从这份权重起步：
    python3 -m rl.train ... --resume rl/runs/bc/last.pt
"""
from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from rl.env import ACT_SAFETY, KIND_INDEX, KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.transformer import WindowTransformer
from rl.ppo import collate, collate_cand, collate_window
from rl.tokenize import GROUPS, tokenize


def _parts(s):
    """样本 → `(obs, 下标, 回报)`。**窗口版样本是 4 元组，窗口在末位** ——
    统一从这里取前三项，别在各个循环里直接解包（加了窗口之后解包会当场炸，
    但如果哪天窗口挪了位置，只有这里会漏改）。"""
    return s[0], s[1], s[2]


def _win_of(s):
    """样本里的窗口；没有窗口（没开 `--window`）就是 None。"""
    return s[3] if len(s) > 3 else None


def _mkwin(env: ZhanguoEnv, obs, on: bool):
    """按需造窗口。`on=False` 直接返回 None —— **别无条件调用**：
    `tokenize` 是每步一次的，关掉窗口的跑法不该为它付钱。"""
    return tokenize(env, obs) if on else None


def to_action(tool: str, args: dict):
    """规则 AI 的 (tool, args) → (kind, sub, tile, army, amount)。坐标转 0-based。"""
    try:
        if tool == "build":
            x, y = map(int, str(args["tile"]).split())
            return ("build", str(args["building"]), (x - 1, y - 1), 0, 1)
        if tool == "recruit":
            x, y = map(int, str(args["tile"]).split())
            kind = args.get("kind") or args.get("unit") or "步"   # v6 用 unit，旧版用 kind
            return ("recruit", str(kind), (x - 1, y - 1), 0, int(args.get("n", 1)))
        if tool == "move":
            return ("move", "", (int(args["x"]) - 1, int(args["y"]) - 1),
                    int(args["army_id"]), 1)
        if tool == "attack":
            ids = args.get("army_ids") or [args.get("army_id")]
            return ("attack", "", (int(args["x"]) - 1, int(args["y"]) - 1), int(ids[0]), 1)
        if tool in ("buy", "sell"):
            return (tool, str(args["good"]), None, 0, int(args.get("qty", 1)))
    except (KeyError, TypeError, ValueError):
        return None
    return None


def match(actions, spec):
    """把规则 AI 的动作对到候选清单的下标；数量档对不上时取**最接近**的那一档。

    为什么不是"取同类别第一个"：老师常一次卖 20~49 个，而 `AMOUNTS` 的上限是 **16**
    ⇒ 兜底取第一个 = **amount 1**，标签变成「有 35 个余量 → 只卖 1 个」。
    实测这类失配占样本的 **2.3%**（sell/buy 上；move/attack/build/recruit 全部精确）。
    改成按 |amount 差| 取最近档，最坏也落在 16 而不是 1。
    """
    if spec is None:
        return None
    kind, sub, tile, army, amount = spec
    best, best_gap = None, None
    for i, a in enumerate(actions):
        if a.kind != kind:
            continue
        if sub and a.sub != sub:
            continue
        if tile is not None and a.tile != tile:
            continue
        if army and a.army != army:
            continue
        if a.amount == amount:
            return i
        gap = abs(a.amount - amount)
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    return best


def get_teacher(which: str, turns: int = 500, horizon: int = -1):
    """老师：v9=**当前基线**（= v8 + 视野门控）/ v6=旧基线 / v3=第一版扩张流。

    旧的 rule_ai（稳经济不扩张，终局 ~7k）已退休——它既不会扩张，也早就不当
    无 key 代打了（那个角色给了 v6），留着当基线只会误导。

    ★v9 的 `HORIZON` 是 **ROI 回收期窗口**（`left = HORIZON - turn`，回本超过 `left`
    的楼不入选），口径是 **视野 = 每局实际回合 + 20**（用户 2026-09-11：
    「视野按交接视野+20」）。BC/DAgger 的局只有几十回合，不该按 500 回合规划 ——
    那样老师会选一堆局末才回本的楼，学生跟着学一堆没用的。
    """
    if which == "v3":
        from expand_rule_ai import expand_rule_turn as fn
    elif which == "v10":
        # ★ v10 = v9 + 抗抖（引擎数值现读，不再写死）+ 去掉"绕山地"（改看打不打得赢）。
        #   训练期开 `--rules-jitter` 时**必须用 v10 当老师**：v9 会把抖过的表当成真值。
        #   ★ HORIZON 必须与 v9 同口径设（`turns + 20`）—— 漏过这一行：v10 用默认 200，
        #     于是 70 回合的局里它按 200 回合规划，扩张明显变少（实测领地 28→19、
        #     进攻 24→17）。见 `tests/test_rule_v10.py::TestTeacherHorizon`。
        import expand_rule_v10 as m
        m.HORIZON = horizon if horizon > 0 else turns + 20
        fn = m.expand_rule_turn_v10
    elif which == "v9":
        import expand_rule_v9 as m
        m.HORIZON = horizon if horizon > 0 else turns + 20
        fn = m.expand_rule_turn_v9
    else:
        from expand_rule_v6 import expand_rule_turn_v6 as fn
    return fn


def episode_is_degenerate(tiles: int, seen: list[int], *, turns: int | None = None,
                          student_driven: bool = False,
                          ratio: float = 0.3, floor: int = 8,
                          min_turns: int = 40) -> bool:
    """这一局是不是「老师根本没启动起来」？（抖动过大的地图会这样）

    实测（20 张图 × 80 回合，`expand_rule_v10`）：抖动 ±20% 时健康局最少 12 格，
    ±35% 起出现退化局（领地停在开局的 5 格、0~1 次进攻），±50% 时 4/20 张图退化。
    ⇒ **退化局 ≤7 格，健康局 ≥12 格**，中间是干净的间隔。

    为什么不用"老师这一局的消费"当判据：**分不开** —— 退化局照样烧 3k~4k
    （消费记账是建造+征兵+军费，卡住的帝国也在建东西、养兵，只是不扩张）。

    阈值**相对化**（见过的中位数的 `ratio`，且不低于 `floor`）：绝对阈值会随回合数、
    地图尺寸、老师版本漂，而"这一局比别的局差一大截"是稳定的信号。

    ★`student_driven=True`（DAgger 局）**一律不判**：那时领地反映的是**学生**的表现，
    而学生不会扩张正是我们要打标签的东西 —— 丢掉它等于把**纠正信号**一起丢掉。
    （踩过：`--dagger-from 21` 一开，后半程 21 局全被判退化丢弃，那半炉一个样本没进缓冲；
    而且丢弃局不做梯度步，整炉只跑了 3.6 小时就"完成"。）

    `turns`：**回合数不够时这个判据不成立** —— 扩张本来就晚（老师首次进攻中位第 21 回合），
    短回合的跑法（冒烟/调试）本来就不扩张。所以 `turns < min_turns` 一律不判退化。
    （踩过：拿 `--turns 12` 冒烟，5 格被当成退化局，那一局样本全丢。）
    `ratio=0.3` 而不是 0.4：±20% 实测里有两局只有 12 格、但有 8~10 次进攻 —— 那是
    **健康但慢**的地图，不该误杀（中位 31 时 0.3×31≈9.3，12 格过关；退化局 ≤7 格照样被抓）。
    """
    if student_driven:                  # ★DAgger 局：领地是学生的成绩，不是判据
        return False
    if turns is not None and turns < min_turns:
        return False
    if tiles <= 5:                      # 开局就是 5 格（十字）→ 一格没打下来
        return True
    if len(seen) < 3:                   # 样本太少时只用绝对下限
        return tiles < floor
    med = sorted(seen)[len(seen) // 2]
    return tiles < max(floor, ratio * med)


def collect_episode(env: ZhanguoEnv, turns: int, seed: int, teacher_fn=None,
                    student=None, endturn_cap: int = 1, with_window: bool = False):
    """跑一局，采 (观测, 候选下标)。返回 (样本, 该局消费, 未匹配数)。

    `student=None`：**老师自己走**（纯 BC，只覆盖老师的轨迹）。
    `student=模型`：**学生走、老师打标签**（DAgger，覆盖学生实际会走到的状态）。
    """
    teacher_fn = teacher_fn or get_teacher("v6")
    env.reset(seed)

    # ---------------- DAgger：学生走、老师打标签 ----------------
    # 每个回合**开头**问一次老师，拿到它这一回合的**整个动作序列**（在世界的副本上问，
    # 不污染真实局面）。回合边界恰好是学生偏航最明显的地方（比如它整局没建东西，
    # 回合 100 的局面和老师见过的完全不同），把那些状态补上标签就治住了主要漂移。
    # （不是逐步问：逐步问要把老师拆成"每步重规划"，而规则 AI 的语义是整回合一次规划。）
    #
    # ★ 标签不能固定取序列第 1 个：实测 v6 每回合的第一步 **100% 是 sell**（先卖余量换
    #   现金），固定取它等于每回合都教学生"卖"——而学生本来就在过度卖，DAgger 于是
    #   不但治不了漂移，还会加重它。
    #   正确做法是**按相位对齐**：学生这一回合走到第 k 步，就取老师序列的第 k 个动作；
    #   **k 超出老师的回合长度 → 老师早该停手了，正确答案是 `end_turn`**。
    #   最后这条直接对治空转：老师的回合长度就是"这回合该干多少活"的天然标尺。
    if student is not None:
        import copy
        from rl.ppo import act as _act
        demos_d: list[tuple] = []
        miss_d = 0
        obs = env.reset(seed)
        rng = random.Random(seed)
        last_turn = -1
        seq: list[tuple] = []
        n_end = 0                # 本回合已发过几条 end_turn 标签
        while True:
            t = env.world.turn
            if t != last_turn:
                last_turn = t
                n_end = 0
                # ★**每回合都问老师，不节流**（2026-09-11 去掉 `dagger_every`）。
                #   实测一次 deepcopy + v8 整回合只要 **7.4 ms**，而一次梯度步约 1100 ms
                #   —— 一局 70 回合全问 = 0.52 秒，占整局（≈289 s）的 **0.18%**。
                #   那个节流省不到千分之二，却制造了两个 bug：
                #     ① 早年：被跳过的回合 `seq` 停在 `[]`，`k < len(seq)` 即 `k < 0`
                #        恒假 → **整回合每一步都贴 end_turn**（投毒）；
                #     ② 改成"跳过就不发标签"后：那一回合**完全没监督** → 学生把它
                #        磨到 512 步的安全上限，**空转反而被放任**（用户当场指出）。
                #   省这点时间不值得留一个能出两种错的开关。
                w2 = copy.deepcopy(env.world)
                seq = []
                teacher_fn(w2, env.agent, rng, max_actions=10 ** 9,
                           on_action=lambda tool, args: seq.append((tool, args)))
            # 标签 = 老师回合序列的第 k 个动作；**k 超出老师的回合长度 → end_turn**。
            k = env.turn_actions
            if k < len(seq):
                i = match(obs.cand["actions"], to_action(*seq[k]))
            elif n_end < endturn_cap:
                # ★ end_turn 标签**每回合封顶**（默认 1 条）。学生（未训练时）一回合
                #   走 67~71 步、老师只走 5~6 步，于是 **~90% 的 DAgger 标签是 end_turn**
                #   （实测 62 vs 5）—— 第 2 条之后全是同一教训的重复，边际信息近乎零，
                #   却把梯度预算吃光（旧 bc_full 日志「训练命中 92.5% / 真命中 16.7%」
                #   就是它撑的）。老师**真正的停手状态只有一个**：走完自己那 n 步的
                #   那一刻（k == len(seq)），留那一条就够。实测占比 92.3% → 18.6%。
                i = next((j for j, a in enumerate(obs.cand["actions"])
                          if a.kind == "end_turn"), None)
                n_end += 1
            else:
                i = None         # 停手标签已发过 → 这步不入库（**不算 miss**，是刻意跳过）
            if i is None:
                if k < len(seq):         # 只有"老师动作对不上候选"才算 miss
                    miss_d += 1
            else:
                demos_d.append((obs, i, env.world.spend_total(env.agent),
                                _mkwin(env, obs, with_window)))
            idx, _lp, _v = _act(student, obs, win=_mkwin(env, obs, with_window))
            obs, _r, done, _info = env.step(obs.cand["actions"][idx])
            if done:
                break
        end = env.world.spend_total(env.agent)
        return ([(o, i, (end - sp) * env.reward_scale, w) for o, i, sp, w in demos_d],
                end, miss_d)

    demos: list[tuple] = []
    miss = 0
    pending: dict = {}

    def on_action(tool, args):
        # 只抓状态，先不入库——规则 AI 会尝试注定失败的动作（资源不够的建造），
        # 那些动作没有对应的合法候选，混进数据集只会教坏策略。
        pending["obs"] = env._obs()           # 动作执行**之前**的状态
        # ★窗口必须**在这里**一起抓，不能等到 `on_result` 里再调 `tokenize`。
        #   `on_result` 跑在动作**执行之后**（这是它的定义），那时 `world` 已经变了：
        #   实测一次 recruit 就会让 `nation_armies` 从 0 变 1，而 `pending["obs"]` 里
        #   还是 0 —— A 组和候选于是指的是**两个世界**，`army_idx` 直接指错 token。
        #   不是理论上可能，是第一次跑 20 回合就撞上的真 bug。
        pending["win"] = _mkwin(env, pending["obs"], with_window)
        pending["spec"] = to_action(tool, args)

    def on_result(tool, args, ok):
        nonlocal miss
        if not ok:
            return
        i = match(pending["obs"].cand["actions"], pending["spec"])
        if i is None:
            miss += 1
        else:
            demos.append((pending["obs"], i, env.world.spend_total(env.agent),
                          pending["win"]))

    rng = random.Random(seed)
    for t in range(turns):
        # 老师**不限额**：实测它每回合最多 15 个动作（中位 8），所以 64 从来没卡住过，
        # 但那是"碰巧没卡住"。规则 AI 想动多少动多少，限额不该由我们来定。
        teacher_fn(env.world, env.agent, rng, max_actions=10 ** 9,
                   on_action=on_action, on_result=on_result)
        # **「何时停手」也要教**：规则 AI 从不发 end_turn 动作（各版 expand_rule_*
        # 里一次都没出现），干完活就直接返回。所以只采它做过的动作的话，数据集里
        # 根本没有 end_turn 这个示范——模型永远学不会停手，每回合一路磨到
        # MAX_ACTIONS 安全上限（实测 512 步/回合），评估和训练都被拖死。
        # 老师停手处的局面，正确答案就是 end_turn，补一条标签。
        o_end = env._obs()
        j = next((k for k, a in enumerate(o_end.cand["actions"])
                  if a.kind == "end_turn"), None)
        if j is not None:
            demos.append((o_end, j, env.world.spend_total(env.agent),
                          _mkwin(env, o_end, with_window)))
        env.world.resolve_turn()
        if t + 1 < turns:
            env.world.begin_turn()
    # 剩余回报 G_t =（局末累计消费 − 此刻累计消费）× reward_scale。
    # γ=1 时它就是 PPO 里 V(s) 该逼近的目标——BC 顺手把 critic 也热身了。
    end = env.world.spend_total(env.agent)
    return ([(o, i, (end - sp) * env.reward_scale, w) for o, i, sp, w in demos],
            end, miss)


def pack(chunk, n_tiles):
    """(obs, 候选下标, 剩余回报[, 窗口]) 列表 → (模型输入, 下标, 回报)。

    开了 `--window` 用 `pack_win`：那个多返回一份窗口批。分开两个函数而不是
    加个开关返回变长元组 —— 变长返回值的调用点迟早会解错。
    """
    steps = [{"grid": s[0].grid, "glob": s[0].glob, "cand": s[0].cand, "act": s[1],
              "logp": 0.0, "val": s[2], "rew": 0.0, "done": False} for s in chunk]
    return (collate(steps, n_tiles),
            np.array([s[1] for s in chunk], dtype=np.int64),
            np.array([s[2] for s in chunk], dtype=np.float32))


def pack_win(chunk, n_tiles):
    """同 `pack`，另外拼出窗口批 → `(模型输入, 窗口批, 下标, 回报)`。"""
    steps = [{"grid": s[0].grid, "glob": s[0].glob, "cand": s[0].cand, "act": s[1],
              "logp": 0.0, "val": s[2], "rew": 0.0, "done": False} for s in chunk]
    return (collate(steps, n_tiles),
            collate_window([_win_of(s) for s in chunk]),
            np.array([s[1] for s in chunk], dtype=np.int64),
            np.array([s[2] for s in chunk], dtype=np.float32))


def pack_tf(chunk):
    """P4 主干：**不拼网格**（CNN 退场）→ `(候选, 候选掩码, 窗口批, 下标, 回报)`。

    **不收 `n_tiles`**：那是给 CNN 的扁平地块下标用的，P4 里没有"地块特征表"这东西，
    收了也只能传个没用的数 —— 别为了"三个 pack 签名一致"留一个假参数。
    """
    steps = [{"grid": s[0].grid, "glob": s[0].glob, "cand": s[0].cand, "act": s[1],
              "logp": 0.0, "val": s[2], "rew": 0.0, "done": False} for s in chunk]
    cand, cmask = collate_cand(steps)
    return (cand, cmask, collate_window([_win_of(s) for s in chunk]),
            np.array([s[1] for s in chunk], dtype=np.int64),
            np.array([s[2] for s in chunk], dtype=np.float32))


def hit_rate(model, samples, n_tiles: int = 0, *, skip_end_turn: bool = False,
             net: str = "mlp", mb: int = 256) -> float:
    """在给定样本上量命中率（老师动作是否被选中）。分批，别一次塞爆内存。

    `skip_end_turn=True`：只算老师的**真实动作**，把每回合补的那条 end_turn 示范剔掉。
    必须分开看——end_turn 每回合一条、又最容易学，混在一起会把命中率抬得虚高
    （实测「训练 20%」里约四分之三来自 end_turn，真实动作只有 ~5%）。
    """
    if skip_end_turn:
        samples = [s for s in samples
                   if s[0].cand["actions"][s[1]].kind != "end_turn"]
    if not samples:
        return float("nan")
    hit = tot = 0
    with torch.no_grad():
        # ★批大小跟着训练批走，别再写死 256：`tf` 主干下 256 是**内存尖峰**
        #   （窗口激活 ∝ batch，见 `--minibatch` 的说明），而这个函数只是量命中率，
        #   不值得为它冒换页的险。实测 v9+tf 一局里它独占 18 s，接到 32 之后只剩几秒。
        for s in range(0, len(samples), mb):
            chunk = samples[s:s + mb]
            if net == "tf":
                cand, cmask, wb, acts, _g = pack_tf(chunk)
                pred = model(wb, cand, cmask)[0].argmax(-1).numpy()
            elif net == "pool":
                (grid, glob, cand, mask), wb, acts, _g = pack_win(chunk, n_tiles)
                pred = model(grid, glob, cand, mask, win=wb)[0].argmax(-1).numpy()
            else:
                (grid, glob, cand, mask), acts, _g = pack(chunk, n_tiles)
                pred = model(grid, glob, cand, mask)[0].argmax(-1).numpy()
            hit += int((pred == acts).sum())
            tot += len(acts)
    return hit / max(1, tot)


def main() -> None:
    ap = argparse.ArgumentParser(description="行为克隆冷启动（学规则 AI 的模式）")
    # ★默认曾是 v6，而帮助里写着"推荐 v9" —— 两者打架的代价：重写命令行时漏了这个
    #   参数，跑出来的样本数（299 = v6）和 v9 的 437 对不上，我花了一整轮去追一个
    #   **根本不存在的"不确定性"**（三次直跑 437/消费 5001 一模一样，世界生成是确定的）。
    #   默认值就该是当前基线，别让默认值和文档互相矛盾。
    ap.add_argument("--teacher", default="v9", choices=("v10", "v9", "v6", "v3"),
                    help="老师：v10=抗抖版（引擎数值现读 + 不绕山地；**配 --rules-jitter 时用它**）/ "
                         "v9=**旧基线**（= v8 + 视野门控，默认）/ "
                         "v6=旧基线（T500 2384k 但 T300 只有 485k）/ v3=第一版（82k）")
    ap.add_argument("--horizon", type=int, default=-1,
                    help="v9 老师的 ROI 回收期窗口（视野）；默认 = 每局回合 + 20")
    ap.add_argument("--episodes", type=int, default=30, help="跑多少局老师 AI 采样本")
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--map-sizes", default="",
                    help="★逗号分隔的地图边长候选（如 '16,24,32'）：给了就**每局重采样一个**。"
                         "RL 是通用的、地图由玩家选，而智能体**不知道地图多大**（迷雾挡着、"
                         "从未探索过边界）—— 只练单一尺寸，换尺寸必然 OOD。")
    # 每回合动作数的安全上界（不是游戏规则）。观测里那一维按固定 ACT_REF=64
    # 归一化，所以这个值改大改小**不再影响观测**，三个脚本之间也不用对齐。
    ap.add_argument("--max-actions", type=int, default=ACT_SAFETY)
    # 关键：训的是**整个回放缓冲**，不是本局。只训本局有两个死穴——
    # ① 灾难性遗忘：每局换了地图就把上一局学的冲掉；② 梯度步数被样本数绑死，
    # 24 局 × 4 epoch × 7 批 = 672 步，克隆一个规则 AI 差了两个数量级。
    ap.add_argument("--steps", type=int, default=250, help="每局采完后做多少梯度步")
    ap.add_argument("--endturn-cap", type=int, default=1,
                    help="DAgger 里每回合最多发几条 end_turn 标签（默认 1 = 只留老师"
                         "真正的停手状态）。学生未训练时一回合走 67~71 步、老师只走 5~6 步，"
                         "不封顶的话 ~90%% 的标签是 end_turn，梯度预算全被同一个教训吃掉。")
    ap.add_argument("--dagger-from", type=int, default=-1,
                    help="从第几局（0 起）开始 DAgger；默认 episodes//2。"
                         "给个很大的数=全程纯 BC（短程验证用，信号干净不被 DAgger 搅）")
    ap.add_argument("--val-every", type=int, default=6,
                    help="每几局抽 1 局整局留出当验证集；短程跑要调小，否则一直是 nan")
    ap.add_argument("--buffer", type=int, default=40000, help="回放缓冲上限（滚动窗口）")
    ap.add_argument("--vf-coef", type=float, default=0.5,
                    help="value 损失的权重——BC 顺手把 critic 也热身，PPO 接手时 V 不是随机数。"
                         "注意 loss_v 已按回报方差归一化，别拿它跟旧日志的 vf 直接比")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--minibatch", type=int, default=0,
                    help="0 = 自动：mlp/pool 用 256，tf 用 32。"
                         "★批大小**同时决定激活内存**（∝ batch × token × d_model）："
                         "tf 在 batch=256 时每个中间张量 63 MB、4 层合计 ~3.8 GB，"
                         "实测把 ECS（3.7 GB）直接压进 swap，一步要几分钟 —— 看着像算力不够，"
                         "其实是换页。改这个之前先看 `free -m` 的 Swap 行。")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=0, help="torch CPU 线程数；**0 = 自动 = 物理核数**（ECS 1 / Pi 5 4）。SMT 的第二个逻辑核对向量计算收益为零，写死 4 在 ECS 上等于打开超订（实测慢 3.4×）")
    ap.add_argument("--ckpt-every", type=int, default=4,
                    help="每几局存一个 ep<N>.pt（0=不存）——跑几小时的东西，"
                         "得能中途量分，不然只能干等")
    ap.add_argument("--kind-power", type=float, default=0.0,
                    help="★类别重加权指数：0=关（原行为）。0.5=按 1/sqrt(频率) 加权，"
                         "使期望权重=1（loss 量纲不变）。针对实测病：sell 占 75%% 的标签把"
                         "**类型排序焊死**（探针：sell 最好候选 +5.59 / attack −0.41，而每类"
                         "跨局面 σ 只有 1.0~1.6 → attack 要翻盘得等 ≈6σ，永远不发生；"
                         "argmax 95.7%% 落在 sell、attack 0%%）。加训练量只会把 sell 焊得更死。")
    ap.add_argument("--init", default="",
                    help="★从已有权重起步（**纠正模式**）：不从头 BC，直接拿它当学生。"
                         "配 --dagger-from 0 就是**纯纠正**（老师不变、只把学生拉回老师）；"
                         "配 --dagger-from 0 --episodes N 跑 N 局。")
    ap.add_argument("--net", default="mlp", choices=("mlp", "pool", "tf"),
                    help="主干：mlp=现有点积头（默认，无窗口）/ "
                         "pool=P3（窗口掩码池化接现有点积头；只换策略 query 的来源）/ "
                         "tf=P4（WindowTransformer：窗口 self-attn + 候选 cross-attn，"
                         "CNN 退场）。三条路同 seed 同起点跑，差异才归因得清")
    ap.add_argument("--d-model", type=int, default=192, help="P4 主干宽度")
    ap.add_argument("--n-layer", type=int, default=4)
    ap.add_argument("--n-head", type=int, default=4)
    ap.add_argument("--rules-jitter", type=float, default=0.0,
                    help="训练期域随机化的幅度（0=关，见 rl/jitter.py 与 TOKEN_DESIGN §10.4）。"
                         "开了就每局按 seed 换一套规则表 —— 学「给定数值怎么打」而不是背下标")
    ap.add_argument("--out", default="rl/runs/bc/last.pt")
    args = ap.parse_args()

    from rl.hw import set_threads
    n_threads = set_threads(args.threads)     # 0 = 自动 = 物理核（ECS 1 / Pi 5 4）
    torch.manual_seed(args.seed)

    # ★DAgger 半程的学生前向是 **batch=1 的逐步前向**，与 `rl/train.py` 的采样同病：
    #   多线程的同步开销远大于收益（train.py 实测 4 线程 34.8ms/步 vs 单线程 7.7ms/步）。
    #   所以采样期间切单线程，梯度步（大 batch）再切回来 —— 照抄 train.py 的既有做法。
    #   在 ECS 上 n_threads 本来就是 1，这层是白给；在 Pi 上是 2~4×。
    def set_collect_threads():
        torch.set_num_threads(1)

    def set_train_threads():
        torch.set_num_threads(n_threads)

    # ★批大小按主干定：它**同时是激活内存的系数**（∝ batch × token × d_model）。
    #   tf 的窗口是 321 token × d192，batch=256 时光窗口那一路的中间张量就有 ~3.8 GB，
    #   把 ECS（3.7 GB）压进 swap —— 表现是"一步几分钟"，很容易误判成算力不够。
    if args.minibatch <= 0:
        args.minibatch = 32 if args.net == "tf" else 256
        print(f"批次自动 = {args.minibatch}（net={args.net}）", flush=True)

    _ms = tuple(int(x) for x in args.map_sizes.split(",") if x.strip()) if args.map_sizes else None
    env = ZhanguoEnv(map_size=args.map_size, map_sizes=_ms, max_turns=args.turns,
                     max_actions_per_turn=args.max_actions,
                     rules_jitter=getattr(args, "rules_jitter", 0.0))
    # 先 reset 一次拿到 obs 维度
    env.reset(0)
    # 开了 --window 就先造一帧窗口，拿它的**实际宽度**建编码器 ——
    # 宽度是 tokenize 的模块常量，但 G 组的宽度含 `len(rivals)`（外交预留），
    # 与其在这边重算一遍（迟早会不一致），不如问窗口自己要。
    use_win = args.net in ("pool", "tf")
    _w = tokenize(env, env._obs()) if use_win else None
    _ww = ({g: _w.feats[g].shape[1] for g in GROUPS} if _w else None)
    if args.net == "tf":
        model = WindowTransformer(_ww, d_model=args.d_model, n_layer=args.n_layer,
                                  n_head=args.n_head)
        model.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
        # 激活内存的粗估（∝ batch × token × d_model），跑之前先亮出来：
        # ECS 只有 3.7 GB，这个数一旦接近它就会换页，而**换页的表现是"慢"，不是"崩"** ——
        # 实测 batch=256 时一步几分钟，看着像算力不够。
        # 三项：主干激活 ∝ b·T·d·层数；**自注意力掩码** ∝ b·H·T²；
        # **候选侧掩码** ∝ b·H·K·T（K 取 512 作上界估）。第二三项是大头 ——
        # 带 `key_padding_mask` 时 torch 会物化整个 (B,H,T,T)，batch=256 时单个就是 422 MB。
        _b, _T, _H = args.minibatch, _w.total, args.n_head
        _act_mb = (_b * _T * args.d_model * 4 * args.n_layer * 4
                   + _b * _H * _T * _T * 4 * args.n_layer
                   + _b * _H * 512 * _T * 4) / 2 ** 20
        print(f"★P4 主干（WindowTransformer）：d_model={args.d_model} "
              f"{args.n_layer} 层 {args.n_head} 头，{model.n_params():,} 参数；"
              f"窗口 {_w.total} token / 亮 {_w.live}；**无 CNN**（候选只带相对落点去 attend）；"
              f"batch={args.minibatch} 激活粗估 ≈{_act_mb:,.0f} MB"
              f"（ECS 可用 3.3 GB，超了就换页——换页看着像慢，不像崩）", flush=True)
    else:
        model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                          sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                          n_tiles=(max(_ms) ** 2 if _ms else args.map_size ** 2),
                          win_widths=_ww)
        if _ww:
            print(f"★窗口模式（P3）：策略 query 来自 token 窗口 "
                  f"共 {_w.total} token / 亮 {_w.live}；候选侧与价值头照旧", flush=True)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    # ★纠正模式：**权重留下**，不从零学（用户 2026-09-11：「权重留下，只是纠正」）。
    # 换老师（比如 v9 加了视野门控之后）时，旧权重是有价值的起点 —— 从零重跑一遍 BC
    # 要几十局、且会把已经学会的部分再学一次；直接拿来当学生做 DAgger 纠正更省。
    if args.init:
        _ck = torch.load(args.init, map_location="cpu", weights_only=False)
        _was = (_ck.get("args") or {}).get("net", "mlp")
        if _was != args.net:
            # 不是错，但**必须说出来**：窗口版多一组 win_enc.*，strict 加载会整个失败；
            # 放宽成 strict=False 又会静默把编码器留成随机初始化。
            print(f"⚠ 起步权重的路线与本次不同（权重 net={_was}，本次={args.net}）"
                  f"—— 用 strict=False 加载，win_enc 会是随机初始化。"
                  f"要「纠正」就保持同一条路；要「换路」就当它是从零训编码器。", flush=True)
        _miss = model.load_state_dict(_ck["model"], strict=False)
        if _miss.unexpected_keys or _miss.missing_keys:
            print(f"  未加载 {len(_miss.unexpected_keys)} 项 / 缺 {len(_miss.missing_keys)} 项："
                  f"{list(_miss.missing_keys)[:4]}…", flush=True)
        print(f"★纠正模式：从 {args.init} 起步（iter {_ck.get('iter')}）——"
              f"不从头 BC，只做纠正", flush=True)

    teacher_fn = get_teacher(args.teacher, args.turns, args.horizon)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def save(path: Path) -> None:
        torch.save({"model": model.state_dict(), "iter": 0,
                    "args": {"source": "behavior_clone", "episodes": args.episodes,
                             # ★记下这份权重是**哪条路**训出来的。三条路的 state_dict
                             #   键不同（pool 多一组 win_enc.*，tf 整个换掉），不记的话
                             #   `--init` 到不匹配的模型上会静默少加载/全不加载。
                             "net": args.net, "d_model": args.d_model,
                             "n_layer": args.n_layer, "n_head": args.n_head,
                             "turns": args.turns, "map_size": args.map_size,
                             "max_actions": args.max_actions, "steps": args.steps,
                             # ★训练期的**规则表抖动幅度**：不记的话，拿这份权重
                             #   评估时没人知道它是在"哪一族数值"上练的（§10.4 纪律 2）。
                             #   0 = 真值；>0 = 每局按 seed 换表（`rl/jitter.py`）。
                             "rules_jitter": getattr(args, "rules_jitter", 0.0),
                             "grad_steps": grad_steps}}, path)
    # ★把老师的 ROI 窗口打进日志：**口径要能自证** —— 漏设 HORIZON 那次
    #   （v10 用默认 200 跑了 6 局）就是因为日志里没有这一项，事后才发现。
    _hz = ""
    if args.teacher in ("v9", "v10"):
        _m = __import__(f"expand_rule_{args.teacher}")
        _hz = f"  视野(HORIZON)={_m.HORIZON}（口径 = 每局 {args.turns} + 20）"
        if _m.HORIZON != args.turns + 20 and (args.horizon or 0) <= 0:
            _hz += "  ★与口径不符！"
    print(f"老师 = {args.teacher}（{teacher_fn.__module__}）{_hz}"
          f"  每局 {args.turns} 回合 × {args.episodes} 局"
          f"  每局 {args.steps} 梯度步  缓冲 {args.buffer}")
    rng = random.Random(args.seed ^ 0xBEEF)
    buffer: list = []
    val: list = []                 # 验证集：只用来量命中率，**永不参与训练**
    t0 = time.time()
    total_steps = 0
    grad_steps = 0
    dagger_from = args.dagger_from if args.dagger_from >= 0 else args.episodes // 2
    degenerate = 0                 # 被判为退化局的局数（丢弃样本，不参与训练）
    _seen_tiles: list[int] = []    # 见过的健康局领地数（判据的相对基准）
    for ep in range(args.episodes):
        # 后半程用 **DAgger**：让学生自己跑，再让老师在**学生走到的状态**上打标签。
        # 这是治「分布漂移」的标准药——只学老师的轨迹，学生一旦偏离就没标签了。
        use_student = (ep >= dagger_from) and len(buffer) > 0
        if use_student:
            set_collect_threads()      # batch=1 逐步前向 → 单线程
        demos, spend, miss = collect_episode(
            env, args.turns, seed=args.seed + ep, teacher_fn=teacher_fn,
            student=model if use_student else None, endturn_cap=args.endturn_cap,
            with_window=use_win)
        set_train_threads()            # 梯度步是大 batch → 切回来
        if not demos:
            print(f"第 {ep} 局没采到样本，跳过")
            continue
        # ★退化局守卫（2026-09-12）：抖动过大的地图上老师可能**整局启动不起来**
        #   （领地停在开局 5 格、0 次进攻）—— 那种局的样本几乎全是 end_turn，
        #   收进缓冲等于**教学生"别动"**。判据见 `episode_is_degenerate`。
        _tiles = len(env.world.own_tiles(env.agent)) if env.world is not None else 0
        if episode_is_degenerate(_tiles, _seen_tiles, turns=args.turns,
                                 student_driven=use_student):
            # ★报**从 1 开始**的局号：与进度行「局 N/42」同一口径。
            #   写 0 基的 `ep` 会让日志读起来像"第 10 局被丢"而实际是第 11 局（踩过）。
            print(f"⚠ 第 {ep + 1} 局老师没启动起来（领地 {_tiles}，见过的中位 "
                  f"{sorted(_seen_tiles)[len(_seen_tiles)//2] if _seen_tiles else '-'}）"
                  f"—— 判为退化局，**丢弃这一局的样本**（抖动过大的地图会这样）")
            degenerate += 1
            set_train_threads()
            continue
        _seen_tiles.append(_tiles)
        total_steps += len(demos)
        # 验证集 = **整局留出**（每 --val-every 局抽 1 局）。先前是从每局里切 10%，
        # 那些样本和训练集**共用同一张地图**——只能测出"对见过的地图过拟合"，
        # 而真正要测的是**换一张新地图还灵不灵**。整局留出才测得到跨地图泛化。
        if ep % args.val_every == args.val_every - 1:
            val.extend(demos)
        else:
            buffer.extend(demos)
        if len(buffer) > args.buffer:
            buffer = buffer[-args.buffer:]
        if len(val) > 2000:
            val = val[-2000:]

        losses = []
        vlosses = []
        # ★类别重加权（--kind-power）：BC 的模仿损失在「永远 sell」这个解上是很舒服的
        #   局部最优 —— 实测 3 局快照的类型偏好：sell 最好候选 +5.59、attack −0.41，
        #   而每类跨局面的 σ 只有 1.0~1.6 → attack 要翻盘得等一次 ≈6σ，永远不发生
        #   （argmax 95.7% 落在 sell、attack 0%）。**加训练量只会把 sell 焊得更死。**
        #   按 1/频率^p 给每类样本加权，把稀有但关键的 attack/move/recruit 顶上来。
        #   每局算一次（O(缓冲)，不在梯度步里算）；权重按「期望=1」归一，loss 量纲不变。
        w_kind = None
        if args.kind_power > 0 and buffer:
            kc = np.zeros(len(KINDS), dtype=np.float64)
            for _s in buffer:
                _o, _i, _g = _parts(_s)
                kc[KIND_INDEX[_o.cand["actions"][_i].kind]] += 1
            freq = kc / max(1.0, kc.sum())
            raw = (freq + 1e-9) ** (-args.kind_power)
            w_kind = torch.as_tensor(raw / float((raw * freq).sum()), dtype=torch.float32)
        for _ in range(args.steps):
            chunk = [buffer[rng.randrange(len(buffer))] for _ in range(args.minibatch)]
            if args.net == "tf":
                cand, cmask, wb, acts, rets = pack_tf(chunk)
                logits, v = model(wb, cand, cmask)
            elif args.net == "pool":
                (grid, glob, cand, mask), wb, acts, rets = pack_win(chunk, model.n_tiles)
                logits, v = model(grid, glob, cand, mask, win=wb)
            else:
                (grid, glob, cand, mask), acts, rets = pack(chunk, model.n_tiles)
                logits, v = model(grid, glob, cand, mask)
            logp = F.log_softmax(logits, dim=-1)
            a_t = torch.as_tensor(acts)
            _lp = logp.gather(1, a_t.unsqueeze(1)).squeeze(1)
            if w_kind is not None:
                # ⚠️ `acts` 是**候选下标**（`logp.gather` 用的就是它），不是类别下标 ——
                # 要先经 `obs.cand["actions"][i].kind` 映射回类别，不能直接 w_kind[acts]
                # （踩过：直接索引报 "index 65 is out of bounds for dimension 0 with size 8"）。
                w_s = torch.as_tensor(
                    [w_kind[KIND_INDEX[_parts(_s)[0].cand["actions"][_parts(_s)[1]].kind]]
                     for _s in chunk],
                    dtype=torch.float32)
                _lp = _lp * w_s
            loss_pi = -_lp.mean()
            # **value 头也要练**：只练策略的话 BC 出来 V 是随机的，PPO 接手时
            # critic 从零开始，而 γ=1/λ=1 下优势完全依赖 V——偏置会直接毁掉
            # 训练信号。数据现成：G_t =（局末消费 − 此刻消费）× reward_scale。
            # **值要归一化**：回报的量纲是「金 × reward_scale」≈ 0~1500，MSE 天然是
            # 10^5 量级。不归一化有两个坑：① 0.5·loss_v ≈ 500 而 loss_pi ≈ 3，梯度
            # 配比 170:1；② 下面那句全局 clip_grad_norm_(0.5) 被价值撑满，策略那点
            # 梯度会被**一起缩掉**。除以回报方差拉回 O(1)：critic 没标定时使劲学，
            # 标定好了自动让位（这就是"顺手热身"该有的样子）。
            rt = torch.as_tensor(rets)
            loss_v = F.mse_loss(v, rt) / max(1.0, float(rt.var()))
            loss = loss_pi + args.vf_coef * loss_v
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            opt.step()
            losses.append(float(loss_pi.detach()))
            vlosses.append(float(loss_v.detach()))
            grad_steps += 1

        # 训练集命中率也量：**只看验证集看不出过拟合**。两个一起看才有意义——
        # 训练一路涨、验证不涨或掉 = 过拟合，这时该早停挑检查点而不是继续跑。
        _kw = {"net": args.net, "mb": args.minibatch}
        # 不传 `model.n_tiles`：那是给 CNN 的扁平地块下标，P4 的主干根本没有 ——
        # `collate` 里这个参数早就废弃了（空位下标按张量实时算）。
        tr_hit = hit_rate(model, buffer[-600:], **_kw)
        tr_true = hit_rate(model, buffer[-600:], skip_end_turn=True, **_kw)
        hit = hit_rate(model, val, **_kw)
        hit_true = hit_rate(model, val, skip_end_turn=True, **_kw)
        # 这个消费数**两种模式含义不同**：纯 BC 局是老师的水平（~15 万），
        # DAgger 局是**学生自己走**打出来的（可能接近 0）——标错会误判成"老师崩了"。
        who = "学生" if use_student else "老师"
        print(f"局 {ep + 1}/{args.episodes}  样本 {len(demos)}(缓冲 {len(buffer)})  "
              f"{who}消费 {spend:,.0f}  未匹配 {miss}  loss {np.mean(losses):.3f}  "
              f"vf {np.mean(vlosses):.3f}  "
              f"命中 训练{tr_hit:.1%}/验证{hit:.1%}  "
              f"真命中(扣end_turn) 训练{tr_true:.1%}/验证{hit_true:.1%}  "
              f"梯度步 {grad_steps}  "
              f"累计 {time.time() - t0:.0f}s",
              flush=True)
        # 中途存点：只在跑完才存的话，想提前量一次分就得干等几小时。
        if args.ckpt_every and (ep + 1) % args.ckpt_every == 0:
            save(out.parent / f"ep{ep + 1}.pt")

    save(out)
    print(f"\nBC 完成：{total_steps} 个样本 · {grad_steps} 梯度步 → {out}")


if __name__ == "__main__":
    main()
