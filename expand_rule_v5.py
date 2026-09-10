# -*- coding: utf-8 -*-
"""扩张流规则 AI（v3）—— 以「占满全图」为唯一目标，500 回合尺度。

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
# v5 改动：军队上限 80。扫过 40/80/140 三档（seed0-2 平均）：
#   40 → 93,797（军费被砍太多）
#   80 → 118,037（最优：复利引擎活着 + 军队够大）
#  140 → 105,169（钱全被军队吃掉，市政厅退回 0~14 座，复利引擎饿死）
# 即：军费和复利不是二选一，是配比问题——用金矿/市政厅的现金流去供养军队。
ARMY_CAP = 80


def expand_rule_turn_v5(world, name: str, rng: random.Random | None = None,
                     max_actions: int = 40) -> list:
    if rng is None:
        rng = random.Random(0)
    acts: list[tuple[str, dict, bool, str]] = []

    def do(tool, args, fn, *a, **k) -> bool:
        if len(acts) >= max_actions:
            return False
        try:
            ok, msg = fn(*a, **k)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
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

    # ⚠️ 补给和装备**绝不卖**：
    #   军费 = 军队吃掉的补给 × 市价（mp.py:1145），只有被吃掉才计入消费；
    #   卖掉补给 = 把最大的消费渠道倒进市场，账面一分不涨。
    #   同理装备是征兵原料，卖了还得高价买回。
    #   真正该清的是**中间投入的盈余**（粮/矿/木/油）——它们换来的钱能立刻变成建筑。
    must_keep = {
        "粮食": sup_n * 2 + planned_rec * 10 + 10,   # 补给厂投料(留2轮) + 征兵
        "矿石": (sup_n + eqp_n) * 2 + 10,            # 补给厂 + 装备厂（留2轮）
        "石油": eqp_n * 2 + 4,
        "木头": 90,                                  # 建造用（市价 2 金，随时可补）
    }
    for g_, keep in must_keep.items():
        have = int(R().get(g_, 0))
        if have > keep:
            # 分档出清：一次卖太多会把市价打崩，反而少收钱
            surplus = have - keep
            sell(g_, surplus) if surplus <= 40 else sell(g_, surplus // 2)

    # ---------------------------------------------------------------- 1. 木材（建造硬门槛，2金/个）
    if R()["木头"] < 70 and R()["黄金"] >= 300:
        buy("木头", 70 - int(R()["木头"]), reserve=200)

    # ---------------------------------------------------------------- 2. 主城 → 兵营
    # 兵营 min_slots=3（含在建）→ 最快第 4 回合出。主城选施工惩罚最低的地块。
    # ⚠️ 兵营是**征兵吞吐量的唯一瓶颈**：每座每回合只能征 1 兵。
    # 实测踩坑：主城一旦有 1 座兵营就不再建 → 兵营永远=1 → 1兵/回合 →
    # 补给厂产出的补给没人吃，终局积压 1.6 万单位（8.4 万金）没转成军费。
    # 兵营无 limit，同一地块可叠多座；也可在多个地块各建。
    want_barr = min(10, max(3, ARMY_CAP // 4))      # v4：不再要 120 座
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

        # 兵营（补给链成型后最优先，见下方说明）
        if army_room > 0 and cnt("补给厂") >= 6 and cnt("兵营") < want_barr \
                and sum(bb.values()) >= 3 and afford("兵营"):
            build(p, "兵营"); continue
        # v4：**收入建筑优先**。黄金矿场 20 回合回本，是复利引擎；
        # v3 把它排在采集之后，钱被 45~70 金的便宜建筑分光，终局一座没有。
        if used(p) >= 6 and eff("市政厅") == 0 and afford("市政厅"):
            build(p, "市政厅"); continue            # 满格地块 25 金/回合
        if res.get("黄金", 0) > eff("黄金矿场") and afford("黄金矿场"):
            build(p, "黄金矿场"); continue          # 10 金/回合/座，通胀免疫
        # 采集铺满（产能 = 消费的上游）
        if res.get("耕地", 0) > eff("农场") and afford("农场"):
            build(p, "农场"); continue
        if res.get("矿石", 0) > eff("矿场") and afford("矿场"):
            build(p, "矿场"); continue
        if res.get("木头", 0) > eff("林场") and afford("林场"):
            build(p, "林场"); continue
        if res.get("石油", 0) > eff("石油厂") and afford("石油厂"):
            build(p, "石油厂"); continue
        # 电力：留足缓冲。电网一停摆，补给厂停产 → 军队断粮 → 3 回合全灭
        # （实测 seed7 扩张到 55 格后军队崩到 1 支，就是电厂没跟上）
        if power < need_pw + 2 and afford("木材能源厂"):
            build(p, "木材能源厂"); continue
        # 兵营：征兵吞吐瓶颈（每座每回合 1 兵）。补给链成型后提到**采集之前**——
        # 否则便宜的农场(50)/矿场(70)会先把钱分光，兵营(350)永远造不起
        # （实测终局兵营=1、军=77、可养=152，一半产能空转）。
        # 门槛：已有若干补给厂，否则早期砸兵营会饿死补给链（实测退回 5 格）。
        if army_room > 0 and cnt("补给厂") >= 6 and cnt("兵营") < want_barr \
                and sum(bb.values()) >= 3 and afford("兵营"):
            build(p, "兵营"); continue
        # 补给厂：决定军队规模。原料可外购，所以只要有电就堆（不再卡自产粮/矿）
        if cnt("补给厂") < min(200, ARMY_CAP // 2 + 4) and power > need_pw \
                and afford("补给厂"):   # v4：按军队规模定产
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
    cap = min(supply_cap(), ARMY_CAP)      # v4：军队封顶
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
