# -*- coding: utf-8 -*-
"""失败触发 DAgger（failure-triggered query）的可行性探针。

在**学生自己的轨迹**上跑一局，把每一个"被引擎拒绝"的决策点抓出来，回答三问。

P1 ★老师在这些状态上重规划，前 3 步是什么？
   判据是**有效修正率 R**（前 3 步内出现"合法、非 end_turn、建设性"的动作），
   **不是首动作的 kind** —— 老师的首动作固定是"卖余量变现"，只看它会把
   "先卖钱再买装备"误判成"无价值"（专家 2026-09-12 指出，采纳）。
   顺带记一条硬证据：学生被拒的那个动作，**是不是就是 DAgger 在这状态上发的标签**？
   撞墙时世界没变（`_apply` 返回 False ⇒ 动作没生效），所以**撞墙点的状态 == 上一步
   的状态**，只差 `last_reject` 一维。相同 ⇒ 老师的标签在这局面上执行不了
   ⇒ **相位对齐失效**的铁证。

P2 学生撞墙之后**下一步**干什么？同类 / 换类 / end_turn。
   这判的不是价值，是**反向风险**：若它撞完还选同类，说明它没有别的想做，
   教它"别撞"会把它推向 end_turn（比现在更不扩张）。

P3 撞墙推进回合步数 k，使 end_turn 标签提前多少？
   **只量化标签污染，别拿它当"不扩张"的解释** —— 实测学生 9 步/回合 >
   老师 5~6 步，它并没有提前停手（2026-09-12 自纠，曾推错过）。

用法：python experiments/probe_failure_trigger.py <ckpt> [seed]
"""
import sys, copy, random, collections, torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act
import rl.bc as bc

CKPT = sys.argv[1]
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 0
TURNS = 40
torch.manual_seed(SEED)

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)                 # ★必须：`_terrain` 要等第一次 reset 才建出来，空 env 取 obs 会越界
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"])
m.eval()
teacher = bc.get_teacher("v10", turns=TURNS)

# "建设性" = 能推进「装备 → 征兵 → 出兵」这条链的动作。
# 学生撞墙的原因是"装备不足"，所以救它的答案必须落在这条链上。
GOOD_BUY = {"装备"}
GOOD_BUILD = {"装备厂", "兵营"}
GOOD_KIND = {"recruit", "move", "attack"}
# "动手" = 真正在版图上做事的动作（区别于纯市场操作 sell/buy）。
DOING = {"build", "recruit", "move", "attack"}


def is_constructive(kind, sub):
    if kind == "buy":
        return sub in GOOD_BUY
    if kind == "build":
        return sub in GOOD_BUILD
    return kind in GOOD_KIND


def kk(kind, sub):
    return f"{kind}:{sub}" if kind in ("build", "buy", "sell", "recruit") else kind


def teacher_seq(world, rng_):
    """在 `world` 的副本上跑老师整回合，返回动作序列（元素是 to_action 的 5 元组）。"""
    s = []
    teacher(world, env.agent, rng_, max_actions=10 ** 9,
            on_action=lambda tool, args: s.append(bc.to_action(tool, args)))
    return s


obs = env.reset(SEED)
rng = random.Random(SEED)                    # 跟 collect_episode 同口径
rng_probe = random.Random(SEED ^ 0xFA11)     # 探针专用，不污染主 rng

stat = collections.Counter()
rows = []
turn_rej = 0
turn_eff = 0
last_turn = -1
seq = []
prev = None
prev_rejected = False

while True:
    t = env.world.turn
    if t != last_turn:
        if last_turn >= 0:                   # 收上一回合的账（P3）
            stat["P3:回合数"] += 1
            stat["P3:有撞墙的回合"] += (turn_rej > 0)
            stat["P3:撞墙步数"] += turn_rej
            stat["P3:有效步数"] += turn_eff
            stat["P3:老师步数"] += len(seq)
        last_turn = t
        turn_rej = turn_eff = 0
        seq = teacher_seq(copy.deepcopy(env.world), rng)

    k = env.turn_actions
    label = seq[k] if k < len(seq) else None

    idx, _lp, _v = act(m, obs, win=tokenize(env, obs))
    a = obs.cand["actions"][idx]
    ak, asub = a.kind, getattr(a, "sub", None)

    obs2, _r, done, info = env.step(a)
    ok = info["ok"]

    if not ok:
        stat["P1:撞墙总数"] += 1
        turn_rej += 1
        # ---- P1：老师在这个状态上重规划 ----
        # 每个撞墙点**重置**探针 rng：答案的差异纯由局面决定，不掺老师的随机性。
        rng_probe.seed(SEED ^ 0xFA11 ^ stat["P1:撞墙总数"])
        full = teacher_seq(copy.deepcopy(env.world), rng_probe)
        head = full[:3]
        stat["P1:老师无动作"] += (len(full) == 0)
        stat["P1:老师步数合计"] += len(full)
        if head:
            stat["P1:首步是end_turn"] += (head[0][0] == "end_turn")
            if any(h[0] != "end_turn" and is_constructive(h[0], h[1]) for h in head):
                stat["P1:有效修正_窄_前3步"] += 1
        # ★判据改宽：老师开局固定"先卖余量、再买木头"，前 3 步全是市场操作，
        #   窄定义必然全灭（第一版 R=0% 就是这么来的，别误读成"机制无用"）。
        #   真正的判据是**整条回合计划里有没有"动手"的动作**。
        if any(h[0] in DOING for h in full):
            stat["P1:全序列含动手"] += 1
        for h in full:
            stat["P1:直方图:" + h[0]] += 1
        if label is not None:
            stat["P1:该状态有标签"] += 1
            if (label[0], label[1]) == (ak, asub):
                stat["P1:标签==被拒动作"] += 1
        rows.append((t, k, kk(ak, asub),
                     [f"{h[0]}:{h[1]}" for h in head],
                     label is not None and (label[0], label[1]) == (ak, asub)))
    else:
        turn_eff += 1

    # ---- P2：上一步撞墙了 → 这一步选了什么 ----
    if prev_rejected:
        stat["P2:样本"] += 1
        if (ak, asub) == prev:
            stat["P2:仍选同类"] += 1
        elif ak == "end_turn":
            stat["P2:改end_turn"] += 1
        else:
            stat["P2:换别的"] += 1
            if is_constructive(ak, asub):
                stat["P2:换建设性"] += 1

    prev = (ak, asub)
    prev_rejected = not ok

    if done:
        break
    obs = obs2

# ------------------------------------------------------------------ 报告
n = stat["P1:撞墙总数"]
print(f"检查点 {CKPT}   seed {SEED}   一局 {stat['P3:回合数']} 回合")
print(f"撞墙 {n} 次；回合有效步数 {stat['P3:有效步数']}、老师步数 {stat['P3:老师步数']}"
      f" → 撞墙占预算 {stat['P3:撞墙步数'] / max(1, stat['P3:有效步数'] + stat['P3:撞墙步数']):.0%}")

print("\n--- P1 老师在撞墙状态上重规划 ---")
if n:
    print(f"  ★全序列含「动手」（build/recruit/move/attack）："
          f"{stat['P1:全序列含动手']}/{n} = {stat['P1:全序列含动手'] / n:.0%}"
          f"   ← 判据 R（宽）")
    print(f"  窄判据（前3步含 买装备/造装备厂·兵营/征兵/出兵）："
          f"{stat['P1:有效修正_窄_前3步']}/{n} = "
          f"{stat['P1:有效修正_窄_前3步'] / n:.0%}"
          f"   ← 老师开局先卖余量，前3步全是市场操作，此栏必然低")
    print(f"  老师计划平均 {stat['P1:老师步数合计'] / n:.1f} 步；"
          f"首步就是 end_turn（反向风险）：{stat['P1:首步是end_turn']}/{n} = "
          f"{stat['P1:首步是end_turn'] / n:.0%}")
    hist = {k.split(":")[-1]: v for k, v in stat.items()
            if k.startswith("P1:直方图:")}
    print(f"  老师整条计划的动作直方图：{hist}")
    d = stat["P1:该状态有标签"]
    if d:
        print(f"  ★被拒动作 == 该状态的 DAgger 标签：{stat['P1:标签==被拒动作']}/{d} = "
              f"{stat['P1:标签==被拒动作'] / d:.0%}   ← 高 = 相位对齐失效的铁证")
    else:
        print("  （撞墙点上没有 DAgger 标签：k 已越过 len(seq)）")

print("\n--- P2 撞墙之后下一步 ---")
s = stat["P2:样本"]
if s:
    print(f"  仍选同类 {stat['P2:仍选同类']}/{s} = {stat['P2:仍选同类'] / s:.0%}"
          f"   改 end_turn {stat['P2:改end_turn']}/{s} = {stat['P2:改end_turn'] / s:.0%}"
          f"   换别的 {stat['P2:换别的']}/{s}（其中建设性 {stat['P2:换建设性']}）")

print("\n--- P3 标签错位（只量化，不下结论）---")
print(f"  有撞墙的回合 {stat['P3:有撞墙的回合']}/{stat['P3:回合数']}；"
      f"撞墙步数 {stat['P3:撞墙步数']} —— 这些步会让 k 提前越过 len(seq)，"
      f"使 end_turn 标签提前发出")

print("\n--- 前 12 个撞墙点明细 ---")
for r in rows[:12]:
    print(f"  T{r[0]:>3} k={r[1]:>2} 被拒={r[2]:<14} "
          f"老师前3步={r[3]}  标签同={r[4]}")
