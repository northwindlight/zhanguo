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
from rl.ppo import collate


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
    """把规则 AI 的动作对到候选清单的下标；数量档对不上就退而求其次（同类别）。"""
    if spec is None:
        return None
    kind, sub, tile, army, amount = spec
    fallback = None
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
        if fallback is None:
            fallback = i
    return fallback


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
    elif which == "v9":
        import expand_rule_v9 as m
        m.HORIZON = horizon if horizon > 0 else turns + 20
        fn = m.expand_rule_turn_v9
    else:
        from expand_rule_v6 import expand_rule_turn_v6 as fn
    return fn


def collect_episode(env: ZhanguoEnv, turns: int, seed: int, teacher_fn=None,
                    student=None, endturn_cap: int = 1):
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
                demos_d.append((obs, i, env.world.spend_total(env.agent)))
            idx, _lp, _v = _act(student, obs)
            obs, _r, done, _info = env.step(obs.cand["actions"][idx])
            if done:
                break
        end = env.world.spend_total(env.agent)
        return ([(o, i, (end - sp) * env.reward_scale) for o, i, sp in demos_d],
                end, miss_d)

    demos: list[tuple] = []
    miss = 0
    pending: dict = {}

    def on_action(tool, args):
        # 只抓状态，先不入库——规则 AI 会尝试注定失败的动作（资源不够的建造），
        # 那些动作没有对应的合法候选，混进数据集只会教坏策略。
        pending["obs"] = env._obs()           # 动作执行**之前**的状态
        pending["spec"] = to_action(tool, args)

    def on_result(tool, args, ok):
        nonlocal miss
        if not ok:
            return
        i = match(pending["obs"].cand["actions"], pending["spec"])
        if i is None:
            miss += 1
        else:
            demos.append((pending["obs"], i, env.world.spend_total(env.agent)))

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
            demos.append((o_end, j, env.world.spend_total(env.agent)))
        env.world.resolve_turn()
        if t + 1 < turns:
            env.world.begin_turn()
    # 剩余回报 G_t =（局末累计消费 − 此刻累计消费）× reward_scale。
    # γ=1 时它就是 PPO 里 V(s) 该逼近的目标——BC 顺手把 critic 也热身了。
    end = env.world.spend_total(env.agent)
    return ([(o, i, (end - sp) * env.reward_scale) for o, i, sp in demos],
            end, miss)


def pack(chunk, n_tiles):
    """(obs, 候选下标, 剩余回报) 列表 → (模型输入, 下标, 回报)。"""
    steps = [{"grid": o.grid, "glob": o.glob, "cand": o.cand, "act": i,
              "logp": 0.0, "val": g, "rew": 0.0, "done": False} for o, i, g in chunk]
    return (collate(steps, n_tiles),
            np.array([i for _o, i, _g in chunk], dtype=np.int64),
            np.array([g for _o, _i, g in chunk], dtype=np.float32))


def hit_rate(model, samples, n_tiles, *, skip_end_turn: bool = False) -> float:
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
        for s in range(0, len(samples), 256):
            chunk = samples[s:s + 256]
            (grid, glob, cand, mask), acts, _g = pack(chunk, n_tiles)
            pred = model(grid, glob, cand, mask)[0].argmax(-1).numpy()
            hit += int((pred == acts).sum())
            tot += len(acts)
    return hit / max(1, tot)


def main() -> None:
    ap = argparse.ArgumentParser(description="行为克隆冷启动（学规则 AI 的模式）")
    ap.add_argument("--teacher", default="v6", choices=("v6", "v3", "v9"),
                    help="老师：v9=当前基线（= v8 + 视野门控，推荐）/ "
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
    ap.add_argument("--minibatch", type=int, default=256)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
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
    ap.add_argument("--out", default="rl/runs/bc/last.pt")
    args = ap.parse_args()

    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)

    _ms = tuple(int(x) for x in args.map_sizes.split(",") if x.strip()) if args.map_sizes else None
    env = ZhanguoEnv(map_size=args.map_size, map_sizes=_ms, max_turns=args.turns,
                     max_actions_per_turn=args.max_actions)
    # 先 reset 一次拿到 obs 维度
    env.reset(0)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=(max(_ms) ** 2 if _ms else args.map_size ** 2))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    # ★纠正模式：**权重留下**，不从零学（用户 2026-09-11：「权重留下，只是纠正」）。
    # 换老师（比如 v9 加了视野门控之后）时，旧权重是有价值的起点 —— 从零重跑一遍 BC
    # 要几十局、且会把已经学会的部分再学一次；直接拿来当学生做 DAgger 纠正更省。
    if args.init:
        _ck = torch.load(args.init, map_location="cpu", weights_only=False)
        model.load_state_dict(_ck["model"])
        print(f"★纠正模式：从 {args.init} 起步（iter {_ck.get('iter')}）——"
              f"不从头 BC，只做纠正", flush=True)

    teacher_fn = get_teacher(args.teacher, args.turns, args.horizon)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def save(path: Path) -> None:
        torch.save({"model": model.state_dict(), "iter": 0,
                    "args": {"source": "behavior_clone", "episodes": args.episodes,
                             "turns": args.turns, "map_size": args.map_size,
                             "max_actions": args.max_actions, "steps": args.steps,
                             "grad_steps": grad_steps}}, path)
    _hz = ""
    if args.teacher == "v9":
        import expand_rule_v9 as _v9
        _hz = f"  视野(HORIZON)={_v9.HORIZON}（= 每局 {args.turns} + 20）"
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
    for ep in range(args.episodes):
        # 后半程用 **DAgger**：让学生自己跑，再让老师在**学生走到的状态**上打标签。
        # 这是治「分布漂移」的标准药——只学老师的轨迹，学生一旦偏离就没标签了。
        use_student = (ep >= dagger_from) and len(buffer) > 0
        demos, spend, miss = collect_episode(
            env, args.turns, seed=args.seed + ep, teacher_fn=teacher_fn,
            student=model if use_student else None, endturn_cap=args.endturn_cap)
        if not demos:
            print(f"第 {ep} 局没采到样本，跳过")
            continue
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
            for _o, _i, _g in buffer:
                kc[KIND_INDEX[_o.cand["actions"][_i].kind]] += 1
            freq = kc / max(1.0, kc.sum())
            raw = (freq + 1e-9) ** (-args.kind_power)
            w_kind = torch.as_tensor(raw / float((raw * freq).sum()), dtype=torch.float32)
        for _ in range(args.steps):
            chunk = [buffer[rng.randrange(len(buffer))] for _ in range(args.minibatch)]
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
                    [w_kind[KIND_INDEX[_o.cand["actions"][_i].kind]] for _o, _i, _g in chunk],
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
        tr_hit = hit_rate(model, buffer[-600:], model.n_tiles)
        tr_true = hit_rate(model, buffer[-600:], model.n_tiles, skip_end_turn=True)
        hit = hit_rate(model, val, model.n_tiles)
        hit_true = hit_rate(model, val, model.n_tiles, skip_end_turn=True)
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
