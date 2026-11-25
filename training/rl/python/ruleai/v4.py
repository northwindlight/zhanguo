# -*- coding: utf-8 -*-
"""扩张流规则 AI · v4 —— 相对 v3 的改动只有一条主线：**复利优先**。

v3 实测的病（seed0 / 500 回合 / 消费 64,745）：
  * 建了 92 座补给厂，只有一半产物被军队吃掉，**终局囤 2 万单位补给**（≈10 万金）
    ——「库存 = 未实现的消费」，它自己囤了最大的一笔。
  * **收入类建筑 0 座**（黄金矿场 / 市政厅），收入只靠卖原料，没有复利引擎。
  * **兵营 1 座** → 每回合至多征 1 兵 → 军队被自己的征兵吞吐卡住，
    而补给产能 184 在空转；钱每回合被便宜的农场/补给厂分光，350 金的兵营永远排不上。

v4 的优先级（每回合，从上到下）：
  1. **市政厅**（5 + 本地建筑位×1 金/回合）—— 满格地块 25 金/回合，20 回合回本，
     是全局最强的复利引擎；**有位置就建**，不等余钱。
  2. **黄金矿场**（+10 金/回合）—— 20 回合回本，本地有金位就建。
  3. **补给链按军队需求定规模**（不再堆 200 座）：军队目标 ~24 支，
     补给厂 ≈ 目标/2 + 2，多一座都不建。
  4. **兵营**：保持 6~10 座（征兵吞吐 = 每座 1 兵/回合），这是把黄金兑现成军队的闸门。
  5. 采集建筑（农场/矿场/林场/石油厂）补齐链条，**多余原料全部卖掉换现金**
     —— 钱立刻变成建筑，不留隔夜。
  6. 军队维持 24 支左右够扩张即可（军费是纯消耗，不是复利）。
  7. **每回合把黄金花光**：建造本身就是消费，也是产能，双重收益。

用法与 v3 相同：
    from expand_rule_v4 import expand_rule_turn_v4
    expand_rule_turn_v4(world, "秦", rng, max_actions=24)
"""
from __future__ import annotations

import random

from game import BUILDINGS, TERRAIN_STATS

TROOPS_FOR = {"沙漠": 2, "平原": 2, "森林": 3, "丘陵": 3, "山地": 4}
ARMY_TARGET = 24          # 维持多少支就够扩张（军费是纯消耗，不追求越多越好）


def expand_rule_turn_v4(world, name: str, rng: random.Random | None = None,
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

    def own():
        return sorted(p for p, t in world.tiles.items() if t["owner"] == name)

    def cnt(bn):
        return sum(t["buildings"].get(bn, 0)
                   for t in world.tiles.values() if t["owner"] == name)

    def eff(t, bn):
        return t["buildings"].get(bn, 0) + t.get("pending", {}).get(bn, 0)

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
        qty = int(qty)
        return qty > 0 and int(R().get(good, 0)) > qty and do(
            "sell", {"good": good, "qty": qty}, world.sell, name, good, qty)

    tiles = own()
    if not tiles:
        return acts

    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    army_n = len(armies)
    sup_n, eqp_n, barr_n = cnt("补给厂"), cnt("装备厂"), cnt("兵营")
    power = cnt("木材能源厂") * 2 + cnt("石油能源厂") * 8
    need_pw = sup_n + eqp_n + barr_n + cnt("市政厅") + cnt("军屯")
    grid_short = bool(world.grid_short.get(name))

    # ---------------------------------------------------------------- 0. 清仓换现金
    # 补给/装备**绝不卖**（补给被军队吃掉才计入军费，卖掉等于把最大消费渠道倒掉；
    # 装备是征兵原料）。该清的是中间投入的盈余：粮/矿/木/油。
    keep = {"粮食": sup_n * 2 + 30, "矿石": sup_n + eqp_n + 20,
            "石油": eqp_n * 2 + 6, "木头": 120}
    for g, k in keep.items():
        have = int(R().get(g, 0))
        if have > k:
            surplus = have - k
            sell(g, surplus if surplus <= 40 else surplus // 2)

    # ---------------------------------------------------------------- 0.9 攒出第一座兵营
    # v3 注释里的血泪教训：没兵营就没兵，没兵就永远卡在 5 格。
    # 而兵营 350 金，只要放开了建别的，钱每回合都会被 45~70 金的便宜建筑分光。
    # 所以这里立一条硬规矩：**兵营落地前，只放最便宜的农场/林场，且必须留够 350 金**。
    if barr_n == 0:
        for p in tiles:
            if len(acts) >= max_actions - 4:
                break
            t = world.tiles[p]
            if t.get("built_this_turn") or used(p) >= 3:
                continue
            if t["resources"].get("耕地", 0) > eff(t, "农场") and afford("农场", 350):
                build(p, "农场")
                continue
            if t["resources"].get("木头", 0) > eff(t, "林场") and afford("林场", 350):
                build(p, "林场")
                continue
        # 主城凑够 3 个位子（含在建）就立刻下兵营
        for p in sorted(tiles, key=lambda q: (TERRAIN_STATS[terr(q)]["build_penalty"],
                                              -used(q))):
            if len(acts) >= max_actions - 2:
                break
            t = world.tiles[p]
            if t.get("built_this_turn") or used(p) < 3:
                continue
            if afford("兵营"):
                build(p, "兵营")
                break
        return acts          # 兵营落地前不做别的（复利引擎、补给厂都往后排）

    # ---------------------------------------------------------------- 1. 复利引擎优先
    # 每回合先把「回本最快、且能持续产金」的两样建满：市政厅 / 黄金矿场。
    # 它们不产兵、不产料，只产**现金**，而现金 = 更多建筑 = 更多消费 + 更多产能。
    for p in tiles:
        if len(acts) >= max_actions - 10:
            break
        t = world.tiles[p]
        if t.get("built_this_turn"):
            continue
        # 市政厅：需本地建筑位 ≥6、每地块限 1。满格(20 位) = 25 金/回合。
        if used(p) >= 6 and eff(t, "市政厅") == 0 and afford("市政厅"):
            build(p, "市政厅")
            continue
        # 黄金矿场：本地金位有限（≤2），20 回合回本
        if t["resources"].get("黄金", 0) > eff(t, "黄金矿场") and afford("黄金矿场"):
            build(p, "黄金矿场")
            continue

    # ---------------------------------------------------------------- 2. 兵营：把金兑现成军队
    # 征兵吞吐 = 兵营数（每座 1 兵/回合）。v3 只建 1 座 → 军队永远补不满、
    # 补给产能空转。这里保持 6~10 座，价格 350 金，排在采集之前。
    want_barr = min(10, max(3, ARMY_TARGET // 3))
    if barr_n < want_barr and not grid_short:
        for p in sorted(tiles, key=lambda q: (TERRAIN_STATS[terr(q)]["build_penalty"],
                                              -used(q))):
            if len(acts) >= max_actions - 10:
                break
            t = world.tiles[p]
            if t.get("built_this_turn") or used(p) < 3:
                continue
            if afford("兵营"):
                build(p, "兵营")
                barr_n += 1
                break

    # ---------------------------------------------------------------- 3. 逐地块建造
    # 顺序：凑兵营位(3) → 采集 → 电 → 补给厂(按目标规模) → 装备厂
    sup_target = max(2, ARMY_TARGET // 2 + 2)
    for p in tiles:
        if len(acts) >= max_actions - 10:
            break
        t = world.tiles[p]
        if t.get("built_this_turn"):
            continue
        # 攒兵营：本地 3 个位子，用最便宜的农场/林场凑
        if barr_n < want_barr and used(p) < 3:
            if t["resources"].get("耕地", 0) > eff(t, "农场") and afford("农场"):
                build(p, "农场")
                continue
            if t["resources"].get("木头", 0) > eff(t, "林场") and afford("林场"):
                build(p, "林场")
                continue
        # 采集：产能的上游，也是可卖的现金来源
        if t["resources"].get("耕地", 0) > eff(t, "农场") and afford("农场"):
            build(p, "农场")
            continue
        if t["resources"].get("矿石", 0) > eff(t, "矿场") and afford("矿场"):
            build(p, "矿场")
            continue
        if t["resources"].get("木头", 0) > eff(t, "林场") and afford("林场"):
            build(p, "林场")
            continue
        if t["resources"].get("石油", 0) > eff(t, "石油厂") and afford("石油厂"):
            build(p, "石油厂")
            continue
        # 电力：留 2 点缓冲，电网一停摆补给厂停产、军队断粮三回合全灭
        if power < need_pw + 2 and afford("木材能源厂"):
            build(p, "木材能源厂")
            power += 2
            need_pw += 1
            continue
        # 补给厂：**按军队规模定产**（v3 堆了 92 座，一半烂在仓里）
        if sup_n < sup_target and power > need_pw and afford("补给厂"):
            build(p, "补给厂")
            sup_n += 1
            need_pw += 1
            continue
        if eqp_n < 3 and cnt("石油厂") > 0 and afford("装备厂"):
            build(p, "装备厂")
            eqp_n += 1
            need_pw += 1
            continue
        # 还有余钱：继建筑位（也是消费）
        if used(p) < 20 and afford("城堡"):
            build(p, "城堡")
            continue

    # ---------------------------------------------------------------- 4. 市场补缺
    if R()["粮食"] < sup_n + 30 and R()["黄金"] >= 400:
        buy("粮食", sup_n + 30 - int(R()["粮食"]), reserve=200)
    if R()["矿石"] < sup_n + 30 and R()["黄金"] >= 400:
        buy("矿石", sup_n + 30 - int(R()["矿石"]), reserve=200)
    if R()["装备"] < 30 and R()["黄金"] >= 400:
        buy("装备", 30 - int(R()["装备"]), reserve=200)

    # ---------------------------------------------------------------- 5. 征兵（够扩张即可）
    if army_n < ARMY_TARGET and not grid_short:
        for p in tiles:
            if len(acts) >= max_actions - 6 or army_n >= ARMY_TARGET:
                break
            t = world.tiles[p]
            if t["buildings"].get("兵营", 0) <= t.get("recruited_this_turn", 0):
                continue
            if R()["粮食"] < 10 or R()["装备"] < 5:
                break
            if do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "n": 1, "unit": "步"},
                  world.recruit, name, p[0], p[1], 1, "步"):
                army_n += 1

    # ---------------------------------------------------------------- 6. 扩张（按地形成本）
    armies = [a for a in world.armies if a["owner"] == name and a["hp"] > 0]
    if armies:
        def tinfo(p):
            try:
                t = world._new_tile(*p, name)
                return t["terrain"], sum(t["resources"].values())
            except Exception:
                return "平原", 0

        def dist_to(p):
            return min(max(abs(a["x"] - p[0]), abs(a["y"] - p[1])) for a in armies)

        targets = []
        for a in armies:
            for nb in world.neighbors(a["x"], a["y"]):
                if world.owned_by(*nb) is None and nb not in targets:
                    targets.append(nb)
        if not targets:
            gs = [(g["x"], g["y"]) for g in world.armies
                  if g["owner"] == "野人" and g["hp"] > 0]
            targets = sorted(gs, key=dist_to)[:12]
        targets.sort(key=lambda p: (TROOPS_FOR.get(tinfo(p)[0], 3) / max(tinfo(p)[1], 1),
                                    -tinfo(p)[1], dist_to(p)))
        used_ids = set()
        for (tx, ty) in targets[:8]:
            need = TROOPS_FOR.get(tinfo((tx, ty))[0], 3)
            near = [a for a in armies if a["id"] not in used_ids and not a.get("engaged")
                    and max(abs(a["x"] - tx), abs(a["y"] - ty)) <= 1]
            if len(near) >= need:
                near.sort(key=lambda a: -a["hp"])
                grp = near[:need]
                used_ids.update(a["id"] for a in grp)
                do("attack", {"army_ids": [a["id"] for a in grp], "x": tx + 1, "y": ty + 1},
                   world.attack, name, [a["id"] for a in grp], tx, ty)
    return acts
