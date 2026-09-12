# -*- coding: utf-8 -*-
"""战国 RL 训练入口：PPO 训一个"只认总消费"的君主。

目标函数 = **终局总消费**（world.spend_total：建造 + 征兵 + 军费，按当时市价折金）。
奖励 = 每步总消费增量（Σ 增量 ≡ 终局总消费，密集但不改目标）。

一局跑满 500 回合 ≈ 9k 步——经济要滚复利，短局看不出名堂。长局按
`--rollout-steps` 分块做 PPO（块边界用 value 自举），只是把长局切成可训练的小段，
**不改目标函数**。

    python3 -m rl.train --map-size 16 --turns 500 --iterations 100
    python3 -m rl.train --eval-only --ckpt rl/runs/single16/last.pt --episodes 3

产物：rl/runs/<run>/ 下 model.pt / last.pt / log.csv / config.json
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import threading
import time
from pathlib import Path

import numpy as np
import torch

from rl.bc import get_teacher, set_horizon
from rl.device import pick_device
from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import PPO, Rollout, act, value_of


def teacher_baseline(world, agent, turns, teacher_fn):
    """在**世界副本**上跑老师 `turns` 回合，返回它的 `spend_total`（该图的"标准答案"）。

    ★**只取前 100 回合**（用户 2026-09-13）：图难度是**开局条件**的差异，而复利是
    **指数增长** —— 好图/差图跑到 200 回合，绝对差在拉大但**相对差在缩小**
    （8000/3000 = 2.67 → 25000/10000 = 2.5）⇒ **越往后基准越区分不出图难度**。
    前 100 回合才是难度信号最纯的窗口。

    固定 rng（同一张图必得同一个基准，可复现）。跑在**副本**上，不碰 env.world。
    """
    rng = random.Random(0xB4BE)
    for t in range(turns):
        teacher_fn(world, agent, rng, max_actions=10 ** 9, on_action=lambda *a: None)
        world.resolve_turn()
        if t + 1 < turns:
            world.begin_turn()
    return world.spend_total(agent)


def map_sizes_of(args):
    """`--map-sizes` 的解析（逗号分隔）。给了就**每局按种子重采样一个尺寸**。

    ★为什么评估必须多图（用户口径 + `~/消费总量评测指标论证.md` §7.7）：
    跨图 σ ≈ 均值 **45%** —— 单张图的数字里，"能力"和"开局抽签"分不开。
    固定 seed 的配对比较能压住跨图噪声（对所有被测模型相同），
    但**得多图取中位**，不能只报一张图。
    """
    return (tuple(int(x) for x in args.map_sizes.split(",") if x.strip())
            if getattr(args, "map_sizes", "") else None)


def build_env(args, *, jitter: float | None = None) -> ZhanguoEnv:
    """`jitter=None` → 用 `args.rules_jitter`（训练采样期）；显式传 0.0 → 评估用真值。

    ★评估**必须**真值：评估期抖的话，"这局为什么输了"里永远藏着一个看不见的随机规则表。
    """
    return ZhanguoEnv(map_size=args.map_size, map_sizes=map_sizes_of(args),
                      seed=args.seed, agent=args.agent,
                      max_turns=args.turns, max_actions_per_turn=args.max_actions,
                      reward_scale=args.reward_scale,
                      rules_jitter=(args.rules_jitter if jitter is None else jitter),
                      invalid_penalty=getattr(args, "invalid_penalty", 0.0))


def build_model(env: ZhanguoEnv, args):
    """`--net` 选主干。P4 的 `WindowTransformer` 需要窗口，所以调用方要先造一帧看宽度。"""
    from rl.tokenize import GROUPS, tokenize
    # `tokenize` 要用 `env._obs()` 的外接框与锚点，而那两个只有 reset 之后才有 ——
    # 换主干前先开一局。无副作用（后面采样时会用自己的 seed 重新 reset）。
    if getattr(env, "world", None) is None:
        env.reset(args.seed)
    if args.net == "tf":
        from rl.transformer import WindowTransformer
        w = tokenize(env, env._obs())
        m = WindowTransformer({g: w.feats[g].shape[1] for g in GROUPS},
                              d_model=args.d_model, n_layer=args.n_layer,
                              n_head=args.n_head)
        m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
        return m
    if args.net == "pool":
        from rl.tokenize import tokenize as _t
        w = _t(env, env._obs())
        return PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                         sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                         n_tiles=env.map_size ** 2,
                         win_widths={g: w.feats[g].shape[1] for g in GROUPS})
    return PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                     sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                     n_tiles=(max(map_sizes_of(args)) if map_sizes_of(args)
                              else env.map_size) ** 2)


def _win(env, obs, on: bool):
    """按需造窗口（P4/P3 要，点积头不要）。"""
    if not on:
        return None
    from rl.tokenize import tokenize
    return tokenize(env, obs)


def play_episode(env: ZhanguoEnv, model, seed: int, deterministic: bool = False,
                 use_win: bool = False) -> dict:
    """跑完整一局（不训练），用于评估。"""
    obs = env.reset(seed)
    total = 0.0
    while True:
        idx, _lp, _v = act(model, obs, deterministic=deterministic,
                           win=_win(env, obs, use_win))
        obs, r, done, _info = env.step(obs.cand["actions"][idx])
        total += r
        if done:
            break
    s = env.summary()
    s["ep_return"] = total
    s["seed"] = seed
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="战国 RL 训练（PPO，目标=总消费）")
    ap.add_argument("--net", default="mlp", choices=("mlp", "pool", "tf"),
                    help="主干：mlp=现有点积头（默认）/ pool=P3（窗口池化接点积头）/ "
                         "tf=P4（WindowTransformer，需窗口）")
    ap.add_argument("--d-model", type=int, default=192)
    ap.add_argument("--n-layer", type=int, default=4)
    ap.add_argument("--n-head", type=int, default=4)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--map-sizes", default="",
                    help="★逗号分隔的地图边长候选（如 '16,24,32'）：给了就每局重采样一个。"
                         "RL 是通用的、地图由玩家选，而智能体**不知道地图多大**；"
                         "只练单一尺寸必然 OOD。评估也多图取中位（跨图 σ≈45%）")
    ap.add_argument("--turns", type=int, default=500, help="每局回合上限（经济滚复利，短局没意义）")
    ap.add_argument("--agent", default="秦")
    ap.add_argument("--max-actions", type=int, default=ACT_SAFETY,
                    help="每回合动作上限。游戏给 LLM 玩家的是 24，但单国 RL 在后期"
                         "（几十块地）24 手明显不够用")
    ap.add_argument("--reward-scale", type=float, default=0.01)
    ap.add_argument("--invalid-penalty", type=float, default=0.0,
                    help="无效动作（被引擎拒）的**虚空**惩罚，单位与消费同口径："
                         "10 = 每次被拒扣掉「10 消费」的等价 reward。**不动游戏内逻辑**"
                         "（spend_total / 计分板一个字节都不变，只改 reward）。默认 0。"
                         "为什么需要：撞墙的唯一代价是烧一格回合预算，而预算是 512、"
                         "学生每回合只用 7.2 步（利用率 1.4%）⇒ 实际零代价 ⇒ 对策略是"
                         "一张「零成本的试错期权」（不产生消费、也不结束回合，"
                         "「再试一次」不要钱）。BC/DAgger 阶段靠交叉熵压着（撞墙动作"
                         "不是老师标签、不入库），**PPO 上来这个约束就没了** ⇒ 这个开关"
                         "是给 PPO 准备的。标定别拍 100：学生一局消费 4747/100 回合 ≈ 47/回合、"
                         "撞墙 2.4 次/回合，10 ⇒ -24/回合（约占一半），100 ⇒ -240/回合（净变负）。")
    ap.add_argument("--map-pool", default="",
                    help="★从**筛过的地图池**里取训练图（`experiments/screen_maps.py` 产出的 "
                         "JSON）。动机（用户 2026-09-13）：全池图难度极端比 **5.4×**"
                         "（老师 2612 ~ 14068），PPO 会把「抽到差图」读成「打法错」。"
                         "筛出中间 50%% 之后难度区间窄到 **1.6×**。")
    ap.add_argument("--teacher-baseline", action="store_true",
                    help="★用**老师在该图上的基准**当 advantage 的基线（用户 2026-09-13）。"
                         "动机：图间 σ ≈ 均值的 45%%，同一套打法在好图 20000、差图 3000 —— "
                         "PPO 只看绝对回报，会把「这局抽到差图」读成「我这个打法错了」，"
                         "于是去修正一个本来正确的行为。改成相对该图标准的**差值**之后，"
                         "图难度被减掉了。**目标函数一个字节都没动**（排名/结算比的还是"
                         "总消费），改的是 baseline。")
    ap.add_argument("--baseline-turns", type=int, default=100,
                    help="老师基准取前几回合（默认 100）。★别取满 200：图难度是**开局"
                         "条件**的差异，而复利是指数增长 —— 好图/差图跑到 200 回合，"
                         "绝对差在拉大但**相对差在缩小**（8000/3000=2.67 → 25000/10000=2.5）"
                         "⇒ 越往后基准越区分不出图难度。前 100 回合是难度信号最纯的窗口。")
    ap.add_argument("--device", default="auto",
                    help="auto = 有 CUDA 用 cuda，否则 cpu；也可显式 cuda/cuda:1/cpu。"
                         "显式写了 cuda 而没卡时**报错而不是静默退回 CPU**（静默退回的"
                         "代价是「这炉怎么这么慢」，8 小时的跑法里发现得太晚）。"
                         "★搬运点只有两处，且**都已写在 `ppo.py` 里**（`forward_batch` "
                         "与 `PPO.update` 的 `model_device`）⇒ 这里只管把模型 `.to(dev)`。")
    ap.add_argument("--iterations", type=int, default=100, help="PPO 更新块数（每块 rollout-steps 步）")
    ap.add_argument("--rollout-steps", type=int, default=2048,
                    help="仅当 --rollout-episodes 0 时生效：每次更新采多少步")
    ap.add_argument("--rollout-episodes", type=int, default=1,
                    help="每次更新采满几局（默认 1 = 整局一更新；0 = 退回固定步数）")
    ap.add_argument("--rollout-cap", type=int, default=40000,
                    help="按局收集时的步数硬上限（防止某局无限长）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--minibatch", type=int, default=0,
                    help="0 = 自动：mlp/pool 512，tf 32。★它同时是**激活内存**的系数"
                         "（∝ batch × token × d_model）—— tf 在 512 上会把 ECS 压进 swap，"
                         "而换页的表现是「慢」不是「崩」，极易误判成算力不够")
    ap.add_argument("--ent-coef", type=float, default=0.01)
    ap.add_argument("--ent-final", type=float, default=None,
                    help="熵系数退火终点：从 --ent-coef 线性降到它（不设=不退火）。"
                         "熵高时 argmax 无意义（分布太平），收尾退火才能收出一个"
                         "好的确定性策略")
    ap.add_argument("--adv-norm", choices=("minibatch", "global"), default="minibatch",
                    help="优势归一化范围。minibatch=CleanRL 默认；global=整块一次，"
                         "保留「整局好/坏」的信息（策略双峰骑墙时用这个）")
    # 奖励归一化默认**关**：奖励本身就是「消费 × reward_scale」，是有真实含义的量，
    # V 在真实单位下才可解释、可体检（rl/critic.py 直接拿 V 和"剩余消费"比）。
    # 而且 BC 热身时 V 学的就是真实回报，PPO 再叠一层缩放会让两边尺度对不上。
    # 尺度问题交给优势归一化（--adv-norm global）就够了。
    ap.add_argument("--norm-reward", action="store_true",
                    help="打开运行 RMS 奖励归一化（默认关，见上）")
    ap.add_argument("--lam", type=float, default=1.0,
                    help="GAE λ。γ=1、λ=1 时 GAE 退化为蒙特卡洛优势："
                         "A_t = 整局剩余消费 − V(s_t)，与目标函数完全同构。"
                         "λ<1 时 TD 残差只往回传 1/(1-λ) 步（0.99 → 100 步 ≈ 7 回合），"
                         "够不着生产链几十回合的回本周期，会纵容近视解。")
    ap.add_argument("--threads", type=int, default=0, help="torch CPU 线程数；**0 = 自动 = 物理核数**（ECS 1 / Pi 5 4）。SMT 的第二个逻辑核对向量计算收益为零，写死 4 在 ECS 上等于打开超订（实测慢 3.4×）")
    ap.add_argument("--out", default="rl/runs/single16")
    ap.add_argument("--resume", default="", help="从 checkpoint 续训")
    ap.add_argument("--ckpt-every", type=int, default=50,
                    help="每多少块另存一份带编号的 checkpoint（ckpt_<iter>.pt）。"
                         "策略的「好时段」可能转瞬即逝（高熵期能打 5~8 万、一旦变尖锐就塌），"
                         "只留 last.pt 会把好权重覆盖掉——这个坑我踩过一次")
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--eval-dir", default="",
                    help="批量回评目录下所有 *.pt 并按消费排序（挑最好的 checkpoint 用）")
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--eval-episodes", type=int, default=2)
    ap.add_argument("--rules-jitter", type=float, default=0.0,
                    help="训练采样期的规则表抖动幅度（0=关；评估恒用真值）")
    args = ap.parse_args()

    from rl.hw import set_threads
    _n_threads = set_threads(args.threads)    # 0 = 自动 = 物理核（ECS 1 / Pi 5 4）
    _use_win = args.net in ("pool", "tf")
    if args.minibatch <= 0:
        args.minibatch = 32 if args.net == "tf" else 512
    # 采样是 batch=1 的逐步前向：多线程的同步开销远大于收益（实测 4 线程 34.8ms/步
    # vs 单线程 7.7ms/步）。所以采样期间切单线程，PPO 更新（大 batch）再切回来。
    def set_collect_threads():
        torch.set_num_threads(1)

    def set_train_threads():
        torch.set_num_threads(_n_threads)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    env = build_env(args)
    model = build_model(env, args)
    # ★搬到设备**必须在建优化器之前**（`PPO(...)` 里建 Adam）—— 动量张量跟着参数走，
    #   先建后搬会留下一份 CPU 动量（不报错，只是白算）。同 `bc.py:638` 的注释。
    #   ★PPO 这条路径原来从没在 GPU 上跑过（train.py 里一处设备处理都没有），但两个
    #   搬运点其实**都已经在 `ppo.py` 里写好了**：`forward_batch`（rollout 的 act
    #   与 eval 都走它）与 `PPO.update` 里的 `model_device(model)` ⇒ **只要把模型
    #   搬上去**，采样/评估/更新全自动跟随。
    #   ⚠ 别用 `device.move_to(model, dev)`：那个函数只认 tensor/dict/list/tuple，
    #   `nn.Module` 会走最后的 `return x` **原样返回**（静默不搬）。
    _dev = pick_device(args.device)
    model = model.to(_dev)
    print(f"设备：{_dev}", flush=True)
    ppo = PPO(model, lr=args.lr, epochs=args.epochs, minibatch=args.minibatch,
              ent_coef=args.ent_coef, adv_norm=args.adv_norm)
    start_iter = 0
    ck = None
    if args.resume:
        ck = torch.load(args.resume, map_location="cpu", weights_only=False)
        model.load_state_dict(ck["model"])
        start_iter = int(ck.get("iter", 0))
        # 优化器状态必须一起恢复：否则每次重启都是全新 Adam，
        # 第一步更新幅度异常大，会把策略踹坏（服务管理器自动重启时尤其致命）。
        if ck.get("opt"):
            try:
                ppo.opt.load_state_dict(ck["opt"])
                print("（含优化器状态）")
            except Exception as e:
                print(f"（优化器状态载入失败，忽略：{type(e).__name__}: {e}）")
        print(f"续训自 {args.resume}（第 {start_iter} 块）")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(vars(args), ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    # 评估必须用**独立 env**：play_episode 会把环境跑到 done，
    # 借用训练 env 的话下一步采样就会撞 "env 未 reset 或已结束"。
    eval_env = build_env(args, jitter=0.0)   # ★评估一律真值

    def evaluate(n: int) -> dict:
        """贪心 + 采样两套评估。

        必须都看：策略熵高时「argmax」未必代表策略的真实本领——实测出现过
        贪心掉进「建最贵的建筑→资源耗光→躺平」的近视陷阱，而采样仍有 4~6 万消费。
        """
        g = [play_episode(eval_env, model, seed=900_000 + i, deterministic=True,
                          use_win=_use_win)
             for i in range(n)]
        s = [play_episode(eval_env, model, seed=800_000 + i, deterministic=False,
                          use_win=_use_win)
             for i in range(n)]
        return {"eval_spend": float(np.mean([x["spend_total"] for x in g])),
                "eval_tiles": float(np.mean([x["tiles"] for x in g])),
                "eval_armies": float(np.mean([x["armies"] for x in g])),
                "eval_s_spend": float(np.mean([x["spend_total"] for x in s])),
                "eval_s_tiles": float(np.mean([x["tiles"] for x in s]))}

    if args.eval_dir:
        # 批量回评：策略的「好时段」可能很短，事后挑权重比只看末态靠谱
        rows = []
        for p in sorted(Path(args.eval_dir).glob("*.pt")):
            ck = torch.load(p, map_location="cpu", weights_only=False)
            model.load_state_dict(ck["model"])
            r = evaluate(max(1, args.eval_episodes))
            rows.append((p.name, int(ck.get("iter", -1)), r))
            print(f"{p.name:<18} iter={ck.get('iter'):>5}  "
                  f"贪心 {r['eval_spend']:>9,.0f}  采样 {r['eval_s_spend']:>9,.0f}  "
                  f"地 {r['eval_s_tiles']:.1f}", flush=True)
        if rows:
            best = max(rows, key=lambda x: x[2]["eval_s_spend"])
            print(f"\n按采样消费最优：{best[0]}（iter {best[1]}，{best[2]['eval_s_spend']:,.0f}）")
        return

    if args.eval_only:
        print("评估：", evaluate(args.eval_episodes))
        return

    writer = None
    csv_fh = None
    t0 = time.time()
    # 训练用的地图种子逐局递增；**必须跟着 checkpoint 走**，否则每次重启都从头数，
    # 会重复练前面那批地图（评估种子是固定的 900000+/800000+，与训练图不重叠）。
    seed = int(ck["seed"]) if (ck and ck.get("seed") is not None) else args.seed
    if ck and ck.get("seed") is not None:
        print(f"（训练图种子接着数：{seed}）")
    # ★老师基准（`--teacher-baseline`）：每局在**世界副本**上并行跑一次老师，
    #   拿到"这张图的标准答案"，再把学生前 `--baseline-turns` 回合的 reward
    #   逐笔减去 `基准×scale/回合数` ⇒ Σ 前段 reward = 学生前段 − 老师前段。
    #   跑在**独立线程**里：老师一局约 8 s、学生约 48 s，本来就能藏进去；
    #   ⚠ 但两者都会抢 GIL（老师是纯 Python，学生大头也在 CPU 侧的 tokenize/env.step），
    #   实际能藏多少**要实测**。
    _teacher_fn = None
    if args.teacher_baseline:
        _teacher_fn = get_teacher("v10", turns=args.baseline_turns)
        set_horizon(_teacher_fn, args.baseline_turns)

    # ★地图池：从筛过的 seed 列表里顺序取，而不是 seed += 1 一路数下去。
    #   池子里的图难度已被压到 1.6×（全池是 5.4×）⇒ PPO 不会再把图难度当打法问题。
    _pool, _pool_i = None, 0
    if args.map_pool:
        _pool = json.loads(Path(args.map_pool).read_text(encoding="utf-8"))["pool"]
        _pool_i = int(ck["pool_i"]) if (ck and ck.get("pool_i") is not None) else 0
        print(f"★地图池：{len(_pool)} 张（难度已筛）  从第 {_pool_i} 张接着数")

    def _take_seed():
        """取下一张训练图的 seed（有池子用池子，否则沿用 seed += 1）。"""
        nonlocal _pool_i
        if _pool is None:
            return None
        s = _pool[_pool_i % len(_pool)]
        _pool_i += 1
        return s

    obs = env.reset(seed)
    rollout = Rollout(lam=args.lam, normalize=args.norm_reward)

    # ---- 老师基准的两个动作（定义在 rollout 之后才能闭包到它）
    _bl = {"th": None, "base": {}, "pend": []}

    def _spawn_baseline():
        """起老师基准线程（跑在 env.world 的**副本**上，不碰学生的世界）。"""
        if _teacher_fn is None:
            return
        w0 = copy.deepcopy(env.world)      # 主线程拷，避免跟学生抢 world
        _bl["base"], _bl["pend"] = {}, []
        _bl["th"] = threading.Thread(
            target=lambda: _bl["base"].__setitem__(
                "v", teacher_baseline(w0, env.agent, args.baseline_turns, _teacher_fn)),
            daemon=True)
        _bl["th"].start()

    def _settle_baseline():
        """局末收账：把本局**前 baseline_turns 回合**的每笔 reward 减去 基准/回合数。

        ⇒ Σ 前段 reward = (学生前段消费 − 老师前段消费) × scale —— 正是"相对该图标准
        的表现"。**必须在开下一局之前调**（下一局会重置 `_bl["pend"]`）。

        ★`rollout.steps[i]["rew"]` 可以直接改：`Rollout._scale()` 在 `normalize=False`
        （默认，我们没开 `--norm-reward`）时**原样返回 reward**，所以存的就是原值。
        """
        th = _bl["th"]
        if th is None:
            return
        th.join()
        shift = (_bl["base"].get("v", 0.0) * args.reward_scale
                 / max(1, args.baseline_turns))
        for i in _bl["pend"]:
            rollout.steps[i]["rew"] -= shift
        _bl["th"], _bl["pend"] = None, []

    _spawn_baseline()
    if ck and ck.get("norm"):
        rollout.load_state(ck["norm"])
        print("（含回报归一化状态）")
    ep_ret, ep_steps, ep_done = 0.0, 0, False
    eps: list[dict] = []          # 已完成的局
    total_steps = 0

    for it in range(start_iter + 1, start_iter + args.iterations + 1):
        # ---- 采样
        # 默认「采满 --rollout-episodes 局」：一局约 1.2 万步，而固定 2048 步的话
        # **每次更新只看得到 1/6 局**——λ=1 的信用传播被卡在窗口里，而「这局打得好不好」
        # 要等一万多步后才揭晓。采满整局，优势才等于真正的整局蒙特卡洛优势。
        set_collect_threads()
        done_this = 0
        while True:
            if ep_done:                     # 刚结束一局：先收基准的账，再记账、开下一局
                _settle_baseline()
                s = env.summary()
                s["ep_return"] = ep_ret
                s["ep_steps"] = ep_steps
                eps.append(s)
                done_this += 1
                _s = _take_seed()           # ★有地图池就从池里取（难度已筛）
                if _s is None:
                    seed += 1
                    _s = seed
                obs = env.reset(_s)         # 必须在 break 之前 reset：
                ep_ret, ep_steps, ep_done = 0.0, 0, False   # 否则下一轮迭代会空转
                _spawn_baseline()           # 起本局的老师基准线程（跟本局并行）
                if (args.rollout_episodes and done_this >= args.rollout_episodes) \
                        or len(rollout) >= args.rollout_cap:
                    break
            elif not args.rollout_episodes and len(rollout) >= args.rollout_steps:
                break                   # 旧的固定步数模式
            elif len(rollout) >= args.rollout_cap:
                break
            _w = _win(env, obs, _use_win)
            idx, logp, val = act(model, obs, win=_w)
            keep = obs
            obs, r, done, info = env.step(obs.cand["actions"][idx])
            # ★窗口与 obs 必须**同一瞬间**取，一起入缓冲。分开取会让更新侧重算的 logp
            #   对应的其实是另一个状态（`bc.py` 上踩过：一次 recruit 就让 A 组与候选
            #   指向两个世界），而这里表现成 ratio 恒偏、策略学歪且不报错。
            rollout.add(keep, idx, logp, val, r, done, win=_w)
            if _bl["th"] is not None and info["turn"] < args.baseline_turns:
                _bl["pend"].append(len(rollout.steps) - 1)   # 这笔属于基准覆盖的前段
            ep_ret += r
            ep_steps += 1
            ep_done = done
        total_steps += len(rollout)

        # ---- 熵系数退火（可选）：让分布逐步收拢成可交付的确定性策略
        if args.ent_final is not None:
            prog = (it - start_iter) / max(1, args.iterations)
            ppo.ent_coef = args.ent_coef + (args.ent_final - args.ent_coef) * prog

        # ---- 更新（块边界自举；局末则 0）
        set_train_threads()
        last_v = 0.0 if ep_done else value_of(model, obs, win=_win(env, obs, _use_win))
        stats = ppo.update(rollout, last_value=last_v)
        rollout.clear()

        recent = eps[-3:]
        # 注意：row 的键必须在**每一块**都齐全——csv.DictWriter 的列头取自第一块，
        # 评估列若只在评估块才出现，writerow 会抛 "dict contains fields not in fieldnames"
        # （曾因此每 50 块崩一次、被服务管理器反复重启）。
        row = {
            "eval_spend": float("nan"), "eval_tiles": float("nan"),
            "eval_armies": float("nan"),
            "eval_s_spend": float("nan"), "eval_s_tiles": float("nan"),
            "iter": it, "env_steps": total_steps, "secs": round(time.time() - t0, 1),
            "episodes": len(eps),
            "last_spend": float(recent[-1]["spend_total"]) if recent else float("nan"),
            "mean_spend": float(np.mean([s["spend_total"] for s in recent])) if recent else float("nan"),
            "last_tiles": float(recent[-1]["tiles"]) if recent else float("nan"),
            "last_armies": float(recent[-1]["armies"]) if recent else float("nan"),
            **{k: round(v, 4) for k, v in stats.items()},
        }
        if args.eval_every and it % args.eval_every == 0:
            row.update(evaluate(args.eval_episodes))
        print(" | ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                         for k, v in row.items()), flush=True)
        if writer is None:
            csv_fh = open(out / "log.csv", "w", newline="", encoding="utf-8")
            writer = csv.DictWriter(csv_fh, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)
        csv_fh.flush()                      # 每块落盘：别让监控去猜缓冲区
        # 状态文件：不依赖 stdout（服务/nssm 下 stdout 未必接到文件）
        (out / "status.txt").write_text(
            f"iter={it} steps={it * args.rollout_steps} secs={row['secs']} "
            f"episodes={len(eps)} last_spend={row['last_spend']:.0f} "
            f"mean_spend={row['mean_spend']:.0f} tiles={row['last_tiles']:.0f} "
            f"armies={row['last_armies']:.0f} ent={row['ent']:.3f} "
            + (f"eval_spend={row['eval_spend']:.0f} eval_tiles={row['eval_tiles']:.0f}"
               if row["eval_spend"] == row["eval_spend"] else "eval_spend=-"),
            encoding="utf-8")
        blob = {"model": model.state_dict(), "opt": ppo.opt.state_dict(),
                "norm": rollout.state(), "seed": seed, "iter": it, "args": vars(args),
                "pool_i": _pool_i}       # ★地图池进度：续训时要接着数，否则重复练前几张图
        torch.save(blob, out / "last.pt")
        torch.save(blob, out / "model.pt")
        if args.ckpt_every and it % args.ckpt_every == 0:
            torch.save(blob, out / f"ckpt_{it}.pt")
    print(f"训练结束，用时 {time.time() - t0:.0f}s，产物在 {out}/")


if __name__ == "__main__":
    main()
