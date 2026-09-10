# -*- coding: utf-8 -*-
"""扩张流规则 AI · v6（用户 2026-09-10 第二版）

相对 v3 的改动：① 补给/装备纳入清仓（卖钱去建造才计入 build，囤着是 0）
② 兵营专款 RESERVE=350（否则钱被即时花光、兵营永远凑不齐）③ 兵营提到采集之前。

原版 v3 文档：扩张流规则 AI（v3）—— 以「占满全图」为唯一目标，500 回合尺度。

前两版为什么不行（实测）：
  * 稳经济版：`supply_cap()` 依赖补给厂，而补给厂建不起（reserve 死锁）→ cap=0
    → 永远不造兵 → 永远卡在 5 格。
  * 爆兵版：造了兵但养不活，征兵 9.7k 换回终局 0.5 支军队。

本版的核心：**军队规模 = 补给产能，扩张速度 = 军队规模 / 每格兵力成本**

    补给链：农场(1粮) + 矿场(1矿) + 电厂(2电) + 补给厂(175金)
            → 1 补给厂 耗 1粮1矿1电 产 2 补给 → 养 2 支步军
    军费：1 补给/回合/支 × 市价 5 金 → 每支兵每回合烧 5 金（这是消费的大头）

战斗（实测引擎规则，别凭直觉）：
  * 野人 100HP、攻击 50（按步兵算），**地形减伤只给守方（野人）**：
        沙漠 -10% / 平原 0% / 森林 10% / 丘陵 25% / 山地 50%
  * 所以一轮击杀所需兵力：沙漠·平原 2 支；森林·丘陵 3 支；**山地 4 支**
  * 我方在进攻不吃减伤，野人反击 50 伤害均摊 → 2 支各 25HP、4 支各 12.5HP
  * 回血 25 HP/回合（不断粮、不交战）→ **2 支兵打完一轮，1 回合就回满**
  * 野人死了不重生（guard_once）→ 打下来的地永久属于你

所以最优打法：**优先啃低防御地形**，用最小兵力换最大占地速度。

用法：
    w = World(size=16, seed=0, nations=["秦"])
    w.begin_turn()
    while ...:
        expand_rule_turn(w, "秦")
        w.resolve_turn()
        w.begin_turn()
"""
from __future__ import annotations

import random

from game import BUILDINGS, TERRAIN_STATS, ARMY_MAX_HP

# 各地形一轮击杀所需兵力（含 1 支余量，防骰子修正）
TROOPS_FOR = {"沙漠": 2, "平原": 2, "森林": 3, "丘陵": 3, "山地": 4}


def expand_rule_turn_v6(world, name: str, rng: random.Random | None = None,
                     max_actions: int = 40, on_action=None, on_result=None) -> list:
    """行为克隆采样钩子（与 v3/v4/v5 各版规则 AI 同签名）：

    - on_action(tool, args)：**执行之前**回调（此刻的世界状态就是该动作的输入）
    - on_result(tool, args, ok)：**执行之后**回调，调用方应只在 ok=True 时入库
      （规则 AI 会尝试注定失败的动作，那些没有对应的合法候选）
    """
    if rng is None:
        rng = random.Random(0)
    acts: list[tuple[str, dict, bool, str]] = []

    def do(tool, args, fn, *a, **k) -> bool:
        if len(acts) >= max_actions:
            return False
        if on_action is not None:
            on_action(tool, args)
        try:
            ok, msg = fn(*a, **k)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        if on_result is not None:
            on_result(tool, args, bool(ok))
        acts.append((tool, args, bool(ok), str(msg)))
        return bool(ok)

    def R():
        return world.nations[name].res

    def tiles():
        return sorted((x, y) for (x, y), t in world.tiles.items() if t["owner"] == name)

    def cnt(bn):
        return sum(t["buildings"].get(bn, 0)
                   for t in world.tiles.values() if t["owner"] == name)

    def used(p):
        t = world.tiles[p]
        return sum(t["buildings"].values()) + sum(t.get("pending", {}).values())

    def terr(p):
        t = world.tiles.get(p)
        return t["terrain"] if t else world.tile_terrain(*p)

    def cost_of(bn):
        c = BUILDINGS[bn]["cost"]
        return sum(c) if isinstance(c, list) else c

    def afford(bn, reserve=0):
        cur = R()
        return (cur["黄金"] - reserve >= cost_of(bn)
                and cur["木头"] >= BUILDINGS[bn]["wood"])

    def build(p, bn):
        return do("build", {"tile": f"{p[0]+1} {p[1]+1}", "building": bn},
                  world.build, name, p[0], p[1], bn)

    def buy(good, qty, reserve=100):
        px = max(1, int(world.prices.get(good, 2)))
        q = min(int(qty), max(0, (int(R()["黄金"]) - reserve) // px))
        return q > 0 and do("buy", {"good": good, "qty": q}, world.buy, name, good, q)

    def sell(good, qty):
        return int(R().get(good, 0)) > qty and do(
            "sell", {"good": good, "qty": int(qty)}, world.sell, name, good, int(qty))

    own = tiles()
    if not own:
        return acts

    # ---------------------------------------------------------------- 0. 资源盘点
    def supply_cap():
        """可养军队数 = 补给产量（每支步军 1 补给/回合）。

        ⚠️ 踩坑：原来写 min(补给厂×2, 剩余电力)，但「剩余电力」是个存量差，
        恒等于几，导致 cap 被误算成个位数 —— 实际却养着 77 支军。
        电力是否够由 `world.grid_short` 单独判断（缺电会停摆），
        只要不停摆，军队上限就是补给产量本身。
        """
        return cnt("补给厂") * 2

    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    army_n = len(armies)

    # ---------------------------------------------------------------- 0.5 清仓（核心）
    # 库存 = 未实现的消费。实测终局有 3.7 万金躺在库存+国库里闲置（占总消费 69%），
    # 复利因此停滞。所以每回合**先清仓**：只留产业链本回合必须吃掉的量，其余全卖，
    # 拿钱立刻转成建筑/军队 —— 货币周转越快，复利越猛。
    sup_n, eqp_n = cnt("补给厂"), cnt("装备厂")
    barr_n = cnt("兵营")
    cap_now = max(0, min(sup_n * 2, max(0, (cnt("木材能源厂") * 2 + cnt("石油能源厂") * 8)
                                        - (eqp_n + barr_n + cnt("市政厅")))))
    planned_rec = max(0, min(cap_now - army_n, barr_n))   # 本回合计划征兵数

    # 清仓清单。
    # ⚠️ 之前的写法「补给和装备绝不卖」是**错的**：
    #    卖补给本身确实不计消费，但换来的钱去建造 → 计入 build（实测 +1221）。
    #    让它在仓库发霉才是真的 0。所以补给必须纳入清仓，只留军队口粮。
    must_keep = {
        "粮食": sup_n * 2 + planned_rec * 10 + 10,   # 补给厂投料(留2轮) + 征兵
        "矿石": (sup_n + eqp_n) * 2 + 10,            # 补给厂 + 装备厂（留2轮）
        "石油": eqp_n * 2 + 4,
        "木头": 90,                                  # 建造用（市价 2 金，随时可补）
        "补给": army_n * 2 + 5,                      # 军队两回合口粮，多余全卖
        "装备": planned_rec * 5 + 5,                 # 征兵原料，留够本回合用
    }
    # 每回合每品最多卖 30：**市价冲击是持久的，分批也回不来**。
    # 实测：卖 10~20 个单价 4.5~4.6；卖 150+ 直接砸到地板 2.85（亏 43%），
    # 且连卖 25 回合也回不到原价。所以宁可慢慢出，也别倾销。
    SELL_CAP = 10 ** 9          # 不限流：宁可砸价也要现金流（见下方实测注释）
    for g_, keep in must_keep.items():
        have = int(R().get(g_, 0))
        surplus = have - keep
        if surplus > 0:
            sell(g_, min(int(surplus), SELL_CAP))

    # ---------------------------------------------------------------- 1. 木材（建造硬门槛，2金/个）
    if R()["木头"] < 70 and R()["黄金"] >= 300:
        buy("木头", 70 - int(R()["木头"]), reserve=200)

    # ---------------------------------------------------------------- 2. 主城 → 兵营
    # 兵营 min_slots=3（含在建）→ 最快第 4 回合出。主城选施工惩罚最低的地块。
    # ⚠️ 兵营是**征兵吞吐量的唯一瓶颈**：每座每回合只能征 1 兵。
    # 实测踩坑：主城一旦有 1 座兵营就不再建 → 兵营永远=1 → 1兵/回合 →
    # 补给厂产出的补给没人吃，终局积压 1.6 万单位（8.4 万金）没转成军费。
    # 兵营无 limit，同一地块可叠多座；也可在多个地块各建。
    want_barr = min(120, max(2, supply_cap() // 2 + 2))
    capital = min(own, key=lambda p: (TERRAIN_STATS[terr(p)]["build_penalty"],
                                      -sum(world.tiles[p]["resources"].values())))
    # 优先在主城叠兵营（主城已凑够 min_slots，且施工惩罚最低）
    if cnt("兵营") < want_barr and not world.tiles[capital].get("built_this_turn"):
        if used(capital) >= 3:
            if afford("兵营"):
                build(capital, "兵营")
        elif used(capital) < 3:
            # 凑 min_slots 用最便宜的：农场 50 < 木材能源厂/瞭望塔 120。
            # 实测踩坑：连盖 3 座瞭望塔 = 360 金白花，国库直接见底。
            tc = world.tiles[capital]
            fillers = []
            if tc["resources"].get("耕地", 0) > tc["buildings"].get("农场", 0):
                fillers.append("农场")
            if tc["resources"].get("矿石", 0) > tc["buildings"].get("矿场", 0):
                fillers.append("矿场")
            if tc["resources"].get("木头", 0) > tc["buildings"].get("林场", 0):
                fillers.append("林场")
            fillers += ["木材能源厂", "补给厂", "瞭望塔"]
            for f in fillers:
                if afford(f):
                    build(capital, f)
                    break

    # ---------------------------------------------------------------- 3. 逐地块建造（每格 1 座/回合）
    power = cnt("木材能源厂") * 2 + cnt("石油能源厂") * 8
    need_pw = cnt("补给厂") + cnt("装备厂") + cnt("兵营") + cnt("市政厅")
    # 军队离供给上限的差距：越大越该优先造兵营（把补给产能兑现成军费）
    army_room = max(0, supply_cap() - army_n - cnt("兵营"))
    # 兵营专款：还想造兵营时，其余建筑必须留下 350 金，否则永远轮不到兵营
    # （实测：一回合 92 个动作把钱花光，终局金=79，兵营 350 永远凑不齐）
    RESERVE = 350 if (army_room > 0 and cnt("兵营") < want_barr) else 0

    # 兵营基金：没兵营就没兵，没兵就永远卡在 5 格（实测 seed0/1/2 都死在这）。
    # 所以第一座兵营落地前，除主城凑位外暂停一切建设，把钱留够 350。
    barr_fund = 350 if cnt("兵营") == 0 else 0
    for p in own:
        if len(acts) >= max_actions - 8:      # 留动作给征兵与进攻
            break
        t = world.tiles[p]
        if t.get("built_this_turn"):
            continue
        if barr_fund and p != capital:
            # 攒钱期：只放**收入类**建筑（农场/矿场/林场/金矿），并豁免专款。
            # ⚠️ 踩坑：原来连农场也要留够 350 才建 → 收入不涨、永远攒不到 350 →
            # 死锁（实测 seed4 卡在 5 格 60 回合只做「卖粮食」一件事）。
            # 收入建筑几回合就回本，它们恰恰是攒出 350 的唯一途径。
            res = t["resources"]
            bb, pend = t["buildings"], t.get("pending", {})

            def eff0(bn):
                return bb.get(bn, 0) + pend.get(bn, 0)

            # 专款 350 必须留够，否则兵营永远造不起（实测这才是主因）
            # 只放最便宜的农场/林场（50/45 金）；矿场(70)/金矿(200)也放进来的话
            # 钱会被吃掉，实测 seed0 从 9.1 万掉到 3.9 千。
            if res.get("耕地", 0) > eff0("农场") and afford("农场", barr_fund):
                build(p, "农场")
            elif res.get("木头", 0) > eff0("林场") and afford("林场", barr_fund):
                build(p, "林场")
            continue
        res = t["resources"]
        bb, pend = t["buildings"], t.get("pending", {})

        def eff(bn):
            return bb.get(bn, 0) + pend.get(bn, 0)

        # 兵营 —— 位够的地块上**最先**造。
        # 放采集之后的话，钱(350)和动作都被农场(50)/矿场(70)吃掉，
        # 补给厂只要 175 反而建得起来 → 107 座补给厂只养 19 支军的荒唐局面。
        # ⚠️ 更隐蔽的坑：一回合有 92 个动作，钱总是被即时花光（实测终局金=79，
        #    兵营 350 永远凑不齐）。所以**其余建筑必须为兵营留 350 专款**。
        if army_room > 0 and cnt("兵营") < want_barr and sum(bb.values()) >= 3 \
                and afford("兵营", RESERVE):
            build(p, "兵营"); continue
        # 采集铺满（产能 = 消费的上游）—— 需为兵营保留专款
        if res.get("耕地", 0) > eff("农场") and afford("农场", RESERVE):
            build(p, "农场"); continue
        if res.get("矿石", 0) > eff("矿场") and afford("矿场", RESERVE):
            build(p, "矿场"); continue
        if res.get("木头", 0) > eff("林场") and afford("林场", RESERVE):
            build(p, "林场"); continue
        if res.get("石油", 0) > eff("石油厂") and afford("石油厂", RESERVE):
            build(p, "石油厂"); continue
        if res.get("黄金", 0) > eff("黄金矿场") and afford("黄金矿场", RESERVE):
            build(p, "黄金矿场"); continue          # 10 金/回合/座，通胀免疫
        # 电力：留足缓冲。电网一停摆，补给厂停产 → 军队断粮 → 3 回合全灭
        if power < need_pw + 2 and afford("木材能源厂", RESERVE):
            build(p, "木材能源厂"); continue
        # 兵营：征兵吞吐瓶颈（每座每回合 1 兵）
        if army_room > 0 and cnt("补给厂") >= 6 and cnt("兵营") < want_barr \
                and sum(bb.values()) >= 3 and afford("兵营", RESERVE):
            build(p, "兵营"); continue
        # 补给厂：决定军队规模。原料可外购，所以只要有电就堆
        if cnt("补给厂") < 200 and power > need_pw and afford("补给厂", RESERVE):
            build(p, "补给厂"); continue
        if cnt("装备厂") < 4 and cnt("石油厂") > 0 and afford("装备厂"):
            build(p, "装备厂"); continue

    # ---------------------------------------------------------------- 4. 市场调剂
    # 关键：**别让自产资源位成为天花板**（实测 seed2 耕地=0、seed1 矿石=1 直接卡死）。
    # 补给厂吃 1粮+1矿 产 2补给(市价5) → 原料成本 2+4=6 金，产出 10 金，**净赚 4 金/座/回合**。
    # 所以自产不够就买——外购原料开补给厂是正收益，不是亏本买卖。
    sup_n = cnt("补给厂")
    need_food = sup_n + 20          # 补给厂消耗 + 征兵周转
    need_ore = sup_n + cnt("装备厂") + 10
    if R()["粮食"] < need_food and R()["黄金"] >= 300:
        buy("粮食", need_food - int(R()["粮食"]), reserve=200)
    if R()["矿石"] < need_ore and R()["黄金"] >= 300:
        buy("矿石", need_ore - int(R()["矿石"]), reserve=200)
    if R()["装备"] < 20 and R()["黄金"] >= 300:
        buy("装备", 20 - int(R()["装备"]))
    if R()["补给"] < army_n * 3 and R()["黄金"] >= 200:
        buy("补给", army_n * 3 - int(R()["补给"]))
    # （清仓已移到第 0.5 节，在建房之前执行——卖完立刻拿钱去建，不留隔夜钱）

    # ---------------------------------------------------------------- 5. 征兵（规模 ≤ 补给产能）
    cap = supply_cap()
    if army_n < cap:
        for p in tiles():
            if len(acts) >= max_actions - 4:
                break
            if army_n >= cap:
                break
            t = world.tiles[p]
            if t["buildings"].get("兵营", 0) <= t.get("recruited_this_turn", 0):
                continue
            if world.grid_short.get(name):
                break
            # 征兵原料**绝不让它卡住**：一兵 = 10粮+5装 = 60 金，
            # 而它每回合吃掉 1 补给 = 5 金军费，且军队是扩张的唯一手段。
            # 实测踩坑：装备=0 时征兵直接停摆，补给厂白建。
            if R()["粮食"] < 10 and buy("粮食", 10, reserve=40) is False \
                    and R()["粮食"] < 10:
                break
            if R()["装备"] < 5 and buy("装备", 5, reserve=40) is False \
                    and R()["装备"] < 5:
                break
            if R()["粮食"] < 10 or R()["装备"] < 5:
                break
            if do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "n": 1, "unit": "步"},
                  world.recruit, name, p[0], p[1], 1, "步"):
                army_n += 1

    # ---------------------------------------------------------------- 6. 扩张：按地形减伤排序
    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    if armies:
        def tile_info(p):
            try:
                t = world._new_tile(*p, name)
                return t["terrain"], sum(t["resources"].values())
            except Exception:
                return "平原", 0

        def dist_to(p):
            return min(max(abs(a["x"] - p[0]), abs(a["y"] - p[1])) for a in armies)

        # 目标 = 相邻或近处的无主格；按「每格兵力成本」排序（越便宜越先打）
        targets = []
        for a in armies:
            for nb in world.neighbors(a["x"], a["y"]):
                if world.owned_by(*nb) is None and nb not in targets:
                    targets.append(nb)
        if not targets:
            # 没接壤目标 → 找最近的野人格推进
            gs = [(g["x"], g["y"]) for g in world.armies
                  if g["owner"] == "野人" and g["hp"] > 0]
            targets = sorted(gs, key=dist_to)[:12]

        def cost_of_tile(p):
            tr, rv = tile_info(p)
            need = TROOPS_FOR.get(tr, 3)
            # 单位资源所需兵力越少越优先；同成本则资源多的优先
            return (need / max(rv, 1), -rv, dist_to(p))

        targets.sort(key=cost_of_tile)

        used_ids = set()
        for (tx, ty) in targets[:8]:
            tr, _ = tile_info((tx, ty))
            need = TROOPS_FOR.get(tr, 3)
            # 挑血最足、未参战、且已相邻的兵
            near = [a for a in armies if a["id"] not in used_ids
                    and not a.get("engaged")
                    and max(abs(a["x"] - tx), abs(a["y"] - ty)) <= 1]
            # 不在旁边的，先移动过去
            if len(near) < need:
                movers = [a for a in armies if a["id"] not in used_ids
                          and not a.get("engaged")
                          and a.get("moved_turn") != world.turn]
                for a in movers[:need - len(near)]:
                    cands = [q for q in world.neighbors(a["x"], a["y"])
                             if world.owned_by(*q) is None]
                    if not cands:
                        continue
                    cur = max(abs(a["x"] - tx), abs(a["y"] - ty))
                    step = min(cands, key=lambda q: max(abs(q[0] - tx), abs(q[1] - ty)))
                    if max(abs(step[0] - tx), abs(step[1] - ty)) < cur:
                        do("move", {"army_id": a["id"], "x": step[0] + 1, "y": step[1] + 1},
                           world.move, name, a["id"], step[0], step[1])
                        used_ids.add(a["id"])
                near = [a for a in armies if a["id"] not in used_ids
                        and not a.get("engaged")
                        and max(abs(a["x"] - tx), abs(a["y"] - ty)) <= 1]
            if len(near) >= need:
                near.sort(key=lambda a: -a["hp"])
                grp = near[:need]
                used_ids.update(a["id"] for a in grp)
                do("attack", {"army_ids": [a["id"] for a in grp], "x": tx + 1, "y": ty + 1},
                   world.attack, name, [a["id"] for a in grp], tx, ty)

    # ---------------------------------------------------------------- 7. 余钱：城堡/市政厅（纯消费）
    if R()["黄金"] >= 1500:
        for p in sorted(tiles(), key=lambda q: -used(q)):
            if len(acts) >= max_actions:
                break
            if world.tiles[p].get("built_this_turn"):
                continue
            if used(p) >= 6 and world.tiles[p]["buildings"].get("市政厅", 0) == 0 \
                    and afford("市政厅"):
                build(p, "市政厅")
                break
            if afford("城堡"):
                build(p, "城堡")
                break
    return acts
