# -*- coding: utf-8 -*-
"""子环节探针：**链式任务要同时盯每一环**。

J.1 的教训（写下来过）：只看终局地数会给**假阴性** —— 它分不出「没建兵营」和
「建了兵营、征了兵、就是不出兵」。所以这个探针逐步数整条链：

    经济开局 → 建兵营（第几回合首建 / 几张图建成）→ 征兵 → move/attack → 终局领地

用法：python experiments/probe_chain.py <ckpt> [episodes] [turns]

采样策略（不是贪心）—— 那是 PPO 里的实际行为，也是"学生一局到底做了什么"的口径。
每局换一张图（`map_seed` 变），所以 `episodes` 张图独立。
"""
import sys
import collections
import torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import act
from spend_rules import army_upkeep_units, income_of

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 70

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)                      # `_terrain` 要等第一次 reset 才建出来
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"])
m.eval()

rows = []
for ep in range(EPS):
    torch.manual_seed(1000 + ep)          # 采样可复现（见 J.4：轨迹 A/B 必须重设）
    obs = env.reset(ep, map_seed=100 + ep)   # 每局一张不同的图
    c = collections.Counter()
    first_barracks = None
    # ★军费占收入 —— **在不在认真养兵扩张**的直接读数（v10 的闸门是 30%：
    #   低了补征、高了停）。领地/attack 都是**后果**，这个是**意图**的物理形态。
    #   逐回合累加取均值（军费比随回合变），局末的瞬时值另存。
    _last_turn = -1
    _mil_sum = 0.0
    _mil_n = 0
    _mil_last = 0.0
    while True:
        idx, _lp, _v = act(m, obs, win=tokenize(env, obs))
        a = obs.cand["actions"][idx]
        sub = getattr(a, "sub", None)
        # ★口径（用户 2026-09-13 澄清，别再搞错）：
        #   **扩张 = attack，且是唯一手段**（attack 自带移动）。`move` 只能走**野地**、
        #   作用是长途奔袭，而**用户不打算教它** ⇒ move 的次数**不是**扩张指标，
        #   别把它读成"动得不少"。所以这里只量 attack 的战果：**打了有没有占下地**。
        _own_before = len(env.world.own_tiles(env.agent)) if a.kind == "attack" else None
        obs, _r, done, info = env.step(a)
        # ★撞墙次数 = `--failure-trigger` **直接作用的量**。没有它就没法判断那味药
        #   有没有效（领地/attack 是间接后果，会被别的东西盖住）。
        if not info["ok"]:
            c["撞墙"] += 1
        if info["turn"] != _last_turn:          # 回合切换 → 记一次军费比
            _last_turn = info["turn"]
            _mil = army_upkeep_units(env.world, env.agent) * env.world.prices.get("补给", 5)
            _inc = income_of(env.world, env.agent)
            if _inc > 0:
                _mil_sum += _mil / _inc
                _mil_n += 1
                _mil_last = _mil / _inc
        if info["ok"]:
            c[a.kind] += 1
            if _own_before is not None and len(env.world.own_tiles(env.agent)) > _own_before:
                c["attack:占地"] += 1
            if a.kind == "build" and sub == "兵营":
                c["建兵营"] += 1
                if first_barracks is None:
                    first_barracks = info["turn"]
        if done:
            break
    tiles = len(env.world.own_tiles(env.agent))
    spend = env.world.spend_total(env.agent)          # ★目标函数本身（不是代理指标）
    mil_sh = (_mil_sum / _mil_n) if _mil_n else 0.0
    # ★总消费的**分解**（`mp.py:80 SPEND_FIELDS` = build/recruit/supply）：
    #   总消费 = 建造 + 征兵 + 军费。**钱去哪了**比钱多少更能说明问题 ——
    #   同样 60% 的消费，老师那份是养兵（扩张的燃料），学生如果是建楼堆的，
    #   那就是"原地建造"的直接证据。
    _sp = env.world.spend.get(env.agent) or {}
    _tot = max(1e-9, sum(_sp.values()))
    _b, _r, _su = (_sp.get("build", 0.0) / _tot, _sp.get("recruit", 0.0) / _tot,
                   _sp.get("supply", 0.0) / _tot)
    rows.append((ep, c["建兵营"], first_barracks, c["recruit"], c["attack"],
                 c["attack:占地"], c["move"], tiles, c["撞墙"], spend, mil_sh,
                 (_b, _r, _su)))
    print(f"  局{ep}:  兵营×{c['建兵营']}  首建 T{first_barracks}  "
          f"征兵{c['recruit']}  attack{c['attack']}(占{c['attack:占地']})  "
          f"move{c['move']}(野地行军)  撞墙{c['撞墙']}  领地{tiles}  "
          f"消费{spend:.0f}  军费比{mil_sh:.0%}  "
          f"结构[建{_b:.0%}/征{_r:.0%}/军{_su:.0%}]")

n = len(rows)
fb = sorted(r[2] for r in rows if r[2] is not None)
atk = sum(r[4] for r in rows)
got = sum(r[5] for r in rows)
print(f"\n=== {CKPT}    {n} 局 × {TURNS} 回合（采样） ===")
# ★r[2] 是「首建兵营的回合」，**可能为 None**（整局没建）—— 直接 sum 会 TypeError
#   （2026-09-13 踩过：ep500 的汇总行就这么崩了，只剩逐局数据）。
print(f"  经济开局（有买卖/建造活动）："
      f"{'✓' if all(sum(v for v in r[1:7] if v is not None) > 0 for r in rows) else '✗'}")
print(f"  建成兵营的图：{sum(1 for r in rows if r[1] > 0)}/{n}"
      f"     首次兵营中位：{fb[len(fb) // 2] if fb else '—'}"
      f"   逐图 {[r[2] for r in rows]}")
print(f"  征兵        {sum(r[3] for r in rows) / n:.1f} 次/局")
print(f"  ★attack     {atk / n:.1f} 次/局   ← **扩张的唯一手段**"
      f"；其中**占下地** {got / n:.1f}（成功率 {got / atk:.0%}）" if atk else "  attack 0")
print(f"  move        {sum(r[6] for r in rows) / n:.1f} 次/局"
      f"   ← 野地行军，**不是扩张指标**（用户不打算教这个）")
print(f"  终局领地    均值 {sum(r[7] for r in rows) / n:.1f}   逐局 {[r[7] for r in rows]}"
      f"   （开局 5 格 ⇒ 净增 {sum(r[7] for r in rows) / n - 5:.1f}）")
print(f"  ★撞墙       {sum(r[8] for r in rows) / n:.1f} 次/局"
      f"（{sum(r[8] for r in rows) / n / TURNS:.1f}/回合）"
      f"   ← **失败触发直接作用的量**，看它降不降")
# ★消费 = **目标函数本身**（支出法 GDP：建造+征兵+军费）。领地/attack 都是
#   **代理指标**（§一「判据口径：前期看领地」——前期而已）。终局消费才是终局结算
#   排名真正比的那个量。
print(f"  ★★消费     均值 {sum(r[9] for r in rows) / n:.0f}"
      f"   逐局 {[round(r[9]) for r in rows]}")
print(f"      中位 {sorted(r[9] for r in rows)[n // 2]:.0f}"
      f"   最低 {min(r[9] for r in rows):.0f}   最高 {max(r[9] for r in rows):.0f}")
# ★军费占收入 = **在不在认真养兵扩张**的直接读数。v10（规则 AI）主动把它顶在
#   **30%**（低了补征、高了停，`expand_rule_v10.py:99`）—— 所以这个数就是
#   那把尺子：**远低于 30% ⇒ 军队规模不够 ⇒ 根本没在扩张**，跟领地多少无关。
print(f"  ★★军费比   均值 {sum(r[10] for r in rows) / n:.1%}"
      f"   逐局 {[f'{r[10]:.0%}' for r in rows]}")
print(f"      （v10 的闸门 = 30%；远低于它 ⇒ 军队规模不够 ⇒ 没在扩张）")
# ★★总消费的**分解**（build/recruit/supply 三项和 = 总消费，`mp.py:80`）
_mb = sum(r[11][0] for r in rows) / n
_mr = sum(r[11][1] for r in rows) / n
_ms = sum(r[11][2] for r in rows) / n
print(f"  ★★消费结构 建造 {_mb:.0%} / 征兵 {_mr:.0%} / **军费 {_ms:.0%}**"
      f"   ← 钱去哪了：军费那一块就是「扩张的燃料」")
print(f"      （对照：老师在 100 回合时军费占收入 30%、且军费吃掉后期消费增量的绝大部分）")
