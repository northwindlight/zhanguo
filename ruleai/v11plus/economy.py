# -*- coding: utf-8 -*-
"""v11plus 的**经济层**：一榜、一账、一次决策（口径照抄 `expand_rule_v10.py` 的经济段）。

★ **它不该知道军事层的存在**（用户 2026-09-15：「v11 是个经济层和军事层分离的 ruleai」）：
  两层只共享**一本动作账**（`ruleai.Ledger`）与 `world`。经济层管钱、料、建造、征兵；
  军事层管编组与出手。谁也别 import 谁。

★ 与 v10 的三处**有意分歧**（都是"修 bug / 补能力"，不是偷偷调参）：

  1. **补给按真实兵种算**：v10 走 `spend_rules.army_upkeep_units`，而那里读的是
     `a.get("kind", "步")` —— 军队字典存的键是 **`type`**，于是**每支骑兵都被按 1 算**
     （真值 2）。用户 2026-09-15 定：**v10 不动，v11 算对** ⇒ 本层用 `game.unit_supply`
     并镜像引擎 `_supply_need` 的"军屯覆盖本格民兵"豁免（`_supply_units`）。
  2. **征召料按引擎同一口径现读**（`world.recruit_cost(name, "步")`，含政体特价）：
     v10 读的是 `UNIT_TYPES["步"]["recruit"]` 与 `BUILDINGS["兵营"]["army_cost"]`
     两份手抄表——今天数值恰好相同，一改平衡就漂。
  3. ~~给军事段留额度（`V11_MIL_RESERVE`）：清仓/备料也要卡这个闸。~~
     **★ 2026-09-16 已删**（用户：「不是早就让你取消任何 v11 的动作限制吗」）：
     这条闸（`econ_full()` 卡买卖 + 建造循环里的 `break`）是**为"看海默认只给 12 个动作"
     那个上限而生的** —— 上限本身 2026-09-15 就删了（`rule_ai.UNLIMITED_ACTIONS`），
     闸却留了下来。额度既然无上限，"给军事段留 6 个"就是纯粹的自我限制 ⇒ 一并拆掉。
     `balance.V11_MIL_RESERVE` **留着**（冻结版 v11 与 v12 还在 import 它），别当它没主。

★ **产兵权在这里**（用户 2026-09-15：「产兵由经济引擎决定」）：产几支、何时产、
  出生在哪个兵营格，都由本层决定；军事层只负责**用**已经存在的兵，不碰产兵。
"""
from __future__ import annotations

from game import (BUILDINGS, MAX_SLOTS, TRADEABLE, building_effect,
                   unit_kind, unit_supply)
from mp import build_econ, good_value

# ★**本层没有"规划视界"这个旋钮了**（用户 2026-09-16：「应该不设回合限制」）。
#   这段历史：原来这里是 `HORIZON = 200`，而它住在**本模块**里 —— 外面 `mod.HORIZON = n`
#   那种写法改的是包的入口模块、本层读不到（2026-09-15 实测栽过：v11/v12 全程按 200 规划
#   而 v10 被真设上了 ⇒ 500 回合那一轮两边口径差 2.6 倍）；后来改成
#   `left = world.max_turns + PLAN_EXTRA - world.turn`，拿它当 ROI 榜的「回本 ≤ left」门槛。
#   ⇒ 现在**门槛整个拆掉**：不设回合限制，只要有回本期（`payback` 存在）就进榜，
#     排在哪由 `payback` 自己决定。理由（用户）：目标函数是**总消费** ——
#     "本局之内回不回得了本"不该当成建不建的前提，建了就是消费。
#   ⚠ 作用面只有 ROI 榜那一处，与军事层无关（v10/v11 是冻结基线，各自的 PLAN_EXTRA 不动）。
BARRACKS_CAP = 4      # 兵营数上限：它的意义是"每回合能征几支"，而征兵被钱卡在 ~1 支/回合
#   ★ 2026-09-16（用户）：「为什么无脑爆兵营啊，那么多兵营何意味」——
#     原来 `want_barr = supply_cap//2 + 2`（= 补给厂数 + 2，只由编制上限推、不看钱也不看军队数）
#     ⇒ 实测 30 座兵营、产能利用率 0.7%、花掉 10,500 金（实际征兵只花 3,052）。
# ★ 木头**没有"工作库存上限"这个旋钮了**（用户 2026-09-17：「不应该找上限值，应该实际算」）。
#   历史：上游加过 `WOOD_KEEP = 60` 压需求（防囤积），但它连"本回合真要用的量"一起压，
#   每回合只买得起 60 木 ⇒ 终局 234 格可建却只动得了 2 座、钱全砸手里。
#   现在改成**把需求算准**：每地块每回合只建 1 座 ⇒ 要备的就是 `best_of` 那批（见第 4 节）。
MIL_SHARE = 0.30       # 军费占收入的上限：出兵、涨兵**同一个条件**（用户 2026-09-11）

STOCK_OUTPUT_FACTORIES = ("装备厂",)
#   产出**存量品**的工厂：装备只在**征兵时一次性消耗**，不像补给那样每回合被军队吃掉
#   ⇒ "自用替代"那套估值对它不成立（你并不是每回合都去买装备）。
STOCK_FACTORY_DISCOUNT = 0.85
#   上面这些工厂在**本层**的 ROI 手动折减：产出**改按卖价估**，再乘这个系数
#   （用户 2026-09-16：「拉低装备厂，不但按卖价估价且手动拉低 roi」）。
#   ★ 引擎 `build_econ` 那份**不动** —— 它给的是中性口径（工厂产出按买价 = 自用替代），
#     对流量品（补给）是对的；是**本层**按自己的用途重估，不是引擎算错了。
#   ⚠ 为什么必须拉低（实测 16x16 seed 900007、500 回合）：榜单改成"遍历建筑表"后
#     装备厂进来了 —— T1 按买价算 per=+5.30、payback 50 ⇒ 挤在采集楼后头被建 3 座
#     ⇒ 装备价 8.00→4.96 ⇒ 事后 per=−4.16（3 座厂每回合买料 41 金、卖成品 27 金，
#     **净亏 14/回合**）⇒ 现金被抽干 ⇒ 攒不到兵营的 350 金 ⇒ 困死开局 5 格。
#     （v10/v11 在同一张图上都正常：v11 的榜是 `_FACTORIES`，压根没有装备厂。）


def _supply_units(world, name: str) -> int:
    """本国军队本回合要吃的补给**单位数** —— 现读 `game.unit_supply`（步1/骑2/民1）。

    镜像引擎 `World._supply_need`（`mp.py:1770`）：民兵驻在**自家军屯格**免费，
    每座军屯覆盖本格 1 支；离格/军屯被夺/同格第 2 支起照常吃。

    ★ 刻意**不用** `spend_rules.army_upkeep_units`：那里读 `a.get("kind", "步")`，
      而军队字典存的键是 `type` ⇒ 每支骑兵被按 1 算（真值 2）。见文件头"分歧 1"。
    """
    free: dict = {}
    need = 0
    for a in world.nation_armies(name):
        if unit_kind(a) == "民":
            t = world.tiles.get((a["x"], a["y"]))
            if t is not None and t["owner"] == name:
                cap = t["buildings"].get("军屯", 0)
                used = free.get((a["x"], a["y"]), 0)
                if used < cap:
                    free[(a["x"], a["y"])] = used + 1
                    continue
        need += unit_supply(a)
    return need


def _can_afford(world, name: str, res: dict, unit: str) -> bool:
    """征一支 `unit` 的原料够不够 —— 现读 `world.recruit_cost`（**含政体特价**）。

    v10 读的是 `UNIT_TYPES[unit]["recruit"]`（手抄的第二份）；引擎 `recruit` 收费走的是
    `world.recruit_cost`，两边必须同一份口径，否则"备料备够了却征不出来"。
    """
    return all(res.get(g, 0) >= amt for g, amt in world.recruit_cost(name, unit).items())


def _res_of(building: str) -> str | None:
    """某采集建筑要的本地资源 —— 直接读 `cap_resource`（不再手抄一份）。"""
    return BUILDINGS.get(building, {}).get("cap_resource")


def _extractors() -> tuple[str, ...]:
    """当下所有「采集类」建筑（按 kind 现算，引擎加一种就自动带上）。"""
    return tuple(b for b, v in BUILDINGS.items()
                 if v.get("kind") in ("extract", "gold") and v.get("outputs"))


_FACTORIES = ("补给厂",)

SKIP_BUILDINGS = ("军屯",)   # v11plus 不参榜的建筑（用户 2026-09-16：「然后 v11plus 过滤军屯」）


def _roi_payback(bn: str, e: dict) -> float | None:
    """榜上用的回本期 —— **存量品工厂另算**（口径见 `STOCK_FACTORY_DISCOUNT`）。

    引擎 `e["payback"]` 对工厂是按"自用替代"（买价）估产出的：对补给（军队每回合都吃）
    成立，对装备（只在征兵时一次性消耗）高估 ⇒ 这里改按**卖价**估、再乘手动折减。
    """
    if bn not in STOCK_OUTPUT_FACTORIES:
        return e["payback"]
    d = e["detail"]
    per = (d["outputs_sell"] - d["inputs_value"] - d["energy_cost"]) * STOCK_FACTORY_DISCOUNT
    return (e["capex"] / per) if per > 0 else None


def _engine_allows(world, bn: str, p: tuple, nation_count) -> bool:
    """这一格**现在真能建** `bn` 吗 —— 逐条复现引擎 `World.build` 的前置判定
    （`mp.py` 920~940），只读不写。榜单的最后一道，摆在 ROI 搜索与密度之后
    （用户 2026-09-16：「每个建筑 roi 搜索，然后是密度搜索，然后你这个时候才能
    通过引擎规则真实过滤非法建筑，然后顺位」）。少了它，榜单会把引擎**必拒**的楼
    排在前头（工程院/市政厅回本比采集楼短），白烧动作：实测 seed 900010 一回合
    185 次 build 里 176 次被拒。
    """
    info = BUILDINGS[bn]
    t = world.tiles[p]
    b, pend = t["buildings"], (t.get("pending") or {})
    eff = {k: b.get(k, 0) + pend.get(k, 0) for k in b}      # 引擎 `_eff`：已建成 + 在建
    used = sum(eff.values())
    if used >= MAX_SLOTS:
        return False
    cr = info.get("cap_resource")
    if cr is not None:
        have = t["resources"].get(cr, 0)
        if have <= 0 or eff.get(bn, 0) >= have:
            return False
    if info.get("max_level") and eff.get(bn, 0) >= info["max_level"]:
        return False
    if info.get("min_slots") and used < info["min_slots"]:
        return False
    if info.get("limit") and eff.get(bn, 0) >= info["limit"]:
        return False
    if info.get("limit_nation") and nation_count(bn) >= info["limit_nation"]:
        return False
    return True


def run(ledger, world, name: str) -> None:
    """把一个回合的经济动作追加进 `ledger`（额度与回调都由它统一管）。"""
    acts = ledger.acts
    do = ledger.do
    # ★ 2026-09-16：这里原先还有 `max_actions = ledger.max_actions`（只为 `econ_full()` 服务）
    #   与"给军事段留额度"那条闸 —— 都随"v11 不留任何动作限制"一起拆了（见模块说明第 3 条）。

    # ================================================================ 0. 工具
    # （第 0~7 节 = `expand_rule_v10.py` 的经济段，照抄不动，只在上面那几处按 v11 口径改）
    def R():
        return world.nations[name].res

    def tiles():
        return [p for p, _t in own_pairs]

    # 本回合自家地块的 `(格, 地块对象)`：**经济段不改归属**（建/征/买卖都不动 owner），
    # 所以这一趟算一次就够；建筑数照旧**现读同一批对象**，不会陈旧。
    # ★ 2026-09-16：原先 `cnt` 每问一次就扫全图 `world.tiles.values()`（1600 格，
    #   而自家地块只有三四百）—— 一回合两万三千次 genexpr，占整局 ~9%。
    own_pairs: list = sorted((p, t) for p, t in world.tiles.items() if t["owner"] == name)

    def cnt(bn):
        return sum(t["buildings"].get(bn, 0) for _p, t in own_pairs)

    def slots(p):
        t = world.tiles[p]
        return sum(t["buildings"].values()) + sum((t.get("pending") or {}).values())

    def cost_of(bn):
        c = BUILDINGS[bn]["cost"]
        return sum(c) if isinstance(c, list) else c

    def free_at(p):
        """这一格本回合还能不能下单（引擎：每地块每回合限建 1 座）。"""
        return (not world.tiles[p].get("built_this_turn")) and slots(p) < MAX_SLOTS

    def afford(p, bn):
        cur = R()
        return (cur["黄金"] >= cost_of(bn) and cur["木头"] >= BUILDINGS[bn]["wood"]
                and free_at(p))

    def build(p, bn):
        return do("build", {"tile": f"{p[0]+1} {p[1]+1}", "building": bn},
                  world.build, name, p[0], p[1], bn)

    def buy(good, qty):
        """**市场调剂**：买多少由 `need` 定，不由余额定（不设 `reserve` 门槛）。"""
        px = max(1, int(world.prices.get(good, 2)))
        q = min(int(qty), max(0, int(R()["黄金"]) // px))
        return (q > 0 and do("buy", {"good": good, "qty": q}, world.buy, name, good, q))

    def sell(good, qty):
        # `>=` 而不是 `>`：要卖光时 qty 恰好等于 have，用严格大于会**一个都卖不掉**
        return (int(qty) > 0 and int(R().get(good, 0)) >= int(qty)
                and do("sell", {"good": good, "qty": int(qty)},
                       world.sell, name, good, int(qty)))

    own = tiles()
    if not own:
        return acts
    armies = [a for a in world.troops if a["owner"] == name and a["hp"] > 0]
    army_n = len(armies)

    # ================================================================ 1. 账面盘点
    power = (cnt("木材能源厂") * BUILDINGS["木材能源厂"]["energy_out"]
             + cnt("石油能源厂") * BUILDINGS["石油能源厂"]["energy_out"])
    need_pw = sum(cnt(bn) * BUILDINGS[bn].get("energy", 0)
                  for bn in BUILDINGS if BUILDINGS[bn].get("energy"))
    gen_add = 0
    _dem_add = 0

    from spend_rules import income_of
    supply_px = float(world.prices.get("补给", 5))

    # ================================================================ 2. 条件项
    supply_cap = cnt("补给厂") * 2               # 补给产能 = 军队编制上限
    # ★ 2026-09-16（用户）：「为什么无脑爆兵营啊，那么多兵营何意味」。
    #   原来这里由**编制上限**推：`supply_cap // 2 + 2` = 补给厂数 + 2 ⇒ 补给厂一多就无限加兵营，
    #   而它**不看军队数、也不看钱** —— 实测（40x40 seed 900000、300 回合）：兵营 30 座、
    #   全期只征了 63 支（0.2 支/回合）⇒ **产能利用率 0.7%**；兵营花了 10,500 金，是实际征兵
    #   花费（3,052）的 3.4 倍；后期 30 座营位全空着（军队 62 已超编制上限 56）。
    #   兵营的真实意味 = **每回合能征几支**，而实际征兵被钱卡在 ~1 支/回合 ⇒ 几座就够。
    #   ⇒ 封顶到 `BARRACKS_CAP`（够了就行，多的钱该去建采集楼，回本更快）。
    want_barr = max(2, min(supply_cap // 2 + 2, BARRACKS_CAP))

    inc = income_of(world, name)
    upkeep = _supply_units(world, name) * supply_px      # ★ v11：真实兵种（骑算 2）
    mil_ok = inc <= 0.5 or (upkeep / inc) < MIL_SHARE
    army_cap = max(army_n + 1, supply_cap) if mil_ok else army_n

    # ================================================================ 3. 本回合的计划
    free_tiles = [p for p in own if free_at(p)]
    room = len(free_tiles)

    roi: list[tuple[float, str, tuple]] = []
    # ★ 2026-09-16（用户）：「应该走正常的 roi 机制，其他建筑会根据真实格子效果选，
    #   有市政厅的 roi 自动高」⇒ 不再手抄"哪些建筑参榜"（原来是 `_extractors()` + 补给厂
    #   两张名单），改成**遍历建筑表**：`build_econ(world, bn, p)` 已经按**这一格的真实效果**
    #   算出每回合净收益，所以
    #     · 有回本期的（`per > 0`）自动进榜（采集类还受本格资源上限约束，见下面两行）；
    #     · 不产出的（兵营/城堡/瞭望塔：`per == 0` ⇒ 回本 None）自动落榜；
    #     · **市政厅按本格密度算产出** ⇒ 密度越高的格回本越快、ROI 自动越高（不用另写选址规则）。
    #   `sorted()` 只为可复现（`BUILDINGS` 的字典序会随插入变化）。
    for p in free_tiles:
        t = world.tiles[p]
        res, built = t["resources"], t["buildings"]
        pend = t.get("pending") or {}
        for bn in sorted(BUILDINGS):
            if bn in SKIP_BUILDINGS:
                continue
            _need = _res_of(bn)                    # 现读 cap_resource（不再手抄一份）
            if _need is not None and res.get(_need, 0) <= built.get(bn, 0) + pend.get(bn, 0):
                continue
            e = build_econ(world, bn, p)           # 传地块：按该格实际造价与效果算回本
            pb = _roi_payback(bn, e)               # ★ 存量品工厂在本层重估（见常量注释）
            if pb:                                 # ★ 不设回合限制（用户 2026-09-16）
                roi.append((pb, bn, p))
    # ★ 2026-09-16（用户）：「还是按 roi 建，只是全国扫地，高级建筑会扫到很多 roi 相同的地，
    #   然后按密度最高的建」⇒ 排序键加一条**密度降序**当平手判据：
    #   不挑地的那几族（补给厂/装备厂/电厂/市政厅）在很多格上算出**同一个回本**
    #   （同造价、同每回合收益 ⇒ 回本只差地形施工惩罚），平手时就往**建筑位最多**的格上建。
    roi.sort(key=lambda x: (x[0], -slots(x[2]), x[2]))
    # ③ 引擎规则过滤非法建筑 —— **必须摆在 ①② 之后**（先算收益，再判合法性）
    roi = [(pb, bn, p) for pb, bn, p in roi if _engine_allows(world, bn, p, cnt)]
    plant_order = sorted(free_tiles, key=lambda q: (-slots(q), q))     # 电厂站址：同一条规则

    n_barr = min(max(0, want_barr - cnt("兵营")),
                 sum(1 for p in free_tiles if slots(p) >= 3)) if cnt("兵营") < want_barr else 0
    e_out = BUILDINGS["木材能源厂"]["energy_out"]
    n_plant = 0
    if power < need_pw:
        n_plant = min(max(0, room - n_barr), -(-(need_pw - power) // e_out))
    n_recruit = 0
    if mil_ok and army_n < army_cap:
        _slots_rec = sum(max(0, world.tiles[p]["buildings"].get("兵营", 0)
                             - world.tiles[p].get("recruited_this_turn", 0)) for p in own)
        n_recruit = min(army_cap - army_n, _slots_rec)
    # ★ 本回合**实际会建的那批**：每地块每回合只建 1 座 ⇒ 就是**每格榜上那一个**
    #   （`roi` 升序 ⇒ 每格第一次出现即该格最优）。木头需求（第 4 节）与建造（第 ④ 段）
    #   共用这一份，免得两处各说各话。
    best_of: dict = {}
    for _pb, _bn, _p in roi:
        best_of.setdefault(_p, _bn)

    # ================================================================ 4. 记账（**唯一口径**）
    need: dict[str, int] = {g: 0 for g in TRADEABLE}

    def want(g, v):
        if v > 0:
            need[g] = need.get(g, 0) + int(v)

    want("补给", _supply_units(world, name))                     # 刚需①军队口粮 ★v11 口径
    for bn in ("补给厂", "装备厂"):                                # 刚需②在产工厂投料
        for g, per in (BUILDINGS[bn].get("inputs") or {}).items():
            want(g, cnt(bn) * per)
    for bn in ("木材能源厂", "石油能源厂"):                        # 刚需③在产电厂燃料
        for g, per in (BUILDINGS[bn].get("fuel") or {}).items():
            want(g, cnt(bn) * per)
    for _ in range(n_plant):
        want("木头", BUILDINGS["木材能源厂"]["wood"])
    for _ in range(n_barr):
        want("木头", BUILDINGS["兵营"]["wood"])

    # ★ 钱与木是**联合分配**（用户 2026-09-17：「钱和木的关系是动态规划，不是简单的计算」）：
    #   按全局回本顺序走一遍 `best_of`（每格榜上那一个），用 `_budget` = 现金 + 余货折价
    #   **一座一座地扣** —— 扣得起的才进"本回合真会建"这一批，木头需求就是**这一批**的木头。
    #   · 只按 `best_of` 走（不是全局榜的前 N 项）：每地块每回合只建 1 座，前 N 项会挤在
    #     少数格上，把需求灌成几千 ⇒ 买回一堆用不掉的木（实测 seed 900007 木囤到 2633、
    #     现金被换成木头、兵营的 350 金攒不出 ⇒ 卡在开局 5 格到 T150）。
    #   · 用 `_budget` 而不是"缺多少买多少"：买木的钱与建造的钱是**同一笔**，
    #     不联合分配就会两头落空。
    #   ⇒ 需求算准了，也就不必再有 `WOOD_KEEP` 那种拍脑袋的上限：买多少 = 用多少。
    _budget = int(R()["黄金"])
    for _g in TRADEABLE:
        if _g == "木头":
            continue
        _sur = int(R().get(_g, 0)) - need.get(_g, 0)
        if _sur > 0:
            _budget += int(_sur * 0.9 * max(1, int(world.prices.get(_g, 2))))
    _wx = max(1, int(world.prices.get("木头", 2)))
    for _bn in best_of.values():
        _need_gold = int(cost_of(_bn)) + int(BUILDINGS[_bn].get("wood", 0)) * _wx
        if _need_gold > _budget:
            continue                     # 付不起就跳过（不中断：后面可能有更便宜的）
        _budget -= _need_gold
        want("木头", BUILDINGS[_bn].get("wood", 0))
    for _ in range(n_recruit):                                    # 计划征兵料
        for g, per in world.recruit_cost(name, "步").items():     # ★ v11：与引擎同一份口径
            want(g, per)

    # ★ 木头**不在这里压需求**（用户 2026-09-16：「备木要保留，但是不能囤积木头」
    #   「缺木头会买，买不设上限就行」）：`need` 就是"本回合真要用的量" ——
    #   第 5 步清仓把超出它的卖掉（不囤积），第 6 步按缺口买（缺就买、不设上限）。

    # ================================================================ 5. 清仓
    for g in TRADEABLE:
        surplus = int(R().get(g, 0)) - need.get(g, 0)
        if surplus > 0:
            sell(g, surplus)

    # ================================================================ 6. 备料
    for g in TRADEABLE:
        gap = need.get(g, 0) - int(R().get(g, 0))
        if gap > 0:
            buy(g, gap)

    # ================================================================ 7. 花钱（**串行判定**）
    def cash_crop(bn: str) -> bool:
        """它的产出能不能卖成钱？只有能变现的才被允许在攒钱期动那笔钱。"""
        if bn in _extractors():            # ★现算（v9 是一份写死的名单）
            return True
        return any(g in TRADEABLE for g in (BUILDINGS[bn].get("outputs") or {}))

    _top_bn = None
    if n_plant:
        _top_bn = "木材能源厂"
    elif n_barr:
        _top_bn = "兵营"
    elif roi:
        _top_bn = roi[0][1]
    save = max(0, cost_of(_top_bn) - int(R()["黄金"])) if _top_bn else 0

    def spend_ok(bn: str) -> bool:
        if save <= 0 or cash_crop(bn):
            return True
        return int(R()["黄金"]) - save >= cost_of(bn)

    def place(p, bn) -> bool:
        """下单一座楼，**带电厂替换**：这一座是**用电建筑**而账上没电 → 换成电厂。

        ★ 2026-09-16（用户）：「改成电厂替换逻辑，落在电厂后，市政厅前，市政厅大概在 10 座
          建筑左右建，那么工程院省下市政厅就省了一笔钱」—— 用**同一套替换写法**加一条：
          **建市政厅前先把工程院建上**（顺序 = 电厂 → 工程院 → 市政厅）。
          理由：厅 500 金，−25% 就是 **125 金**（工程院自己才 300 金）⇒ 这一笔就抵掉它四成
          造价，而这一格**之后的楼**继续吃折扣 ⇒ 工程院不再是"要不要建"，而是"盖厅的步骤之一"。
        """
        nonlocal gen_add, _dem_add
        if BUILDINGS[bn].get("energy", 0) and (power + gen_add) < need_pw + _dem_add + 1:
            if afford(p, "木材能源厂") and spend_ok("木材能源厂"):
                if build(p, "木材能源厂"):
                    gen_add += BUILDINGS["木材能源厂"]["energy_out"]
            return True                              # 本格已占用（建了，或电厂也建不起）
        # ② 工程院前置（同款替换，见上）：只在建市政厅那一座时触发
        if bn == "市政厅" and not world.tiles[p]["buildings"].get("工程院") \
                and afford(p, "工程院") and spend_ok("工程院") \
                and (sum(world.tiles[p]["buildings"].values())
                     + sum((world.tiles[p].get("pending") or {}).values())) < MAX_SLOTS:
            if build(p, "工程院"):
                return True                          # 本格本回合已下单 ⇒ 厅顺延到下一回合
        if not afford(p, bn) or not spend_ok(bn):
            return False
        if build(p, bn):
            _dem_add += BUILDINGS[bn].get("energy", 0)
            return True
        return False

    # ★ 2026-09-16：逐格循环里那句 `cnt("兵营")` 原先**每格重扫一遍自家地块**
    #   （实测一回合六十来次 × 三四百格）。这里提成计数器：兵营在**本循环里**只增不减
    #   （全文件只有这一处建兵营），所以"建一座减一"与原式逐值等价。
    barr_left = want_barr - cnt("兵营")           # 本回合还差几座兵营

    # ---- ① 征兵：**独立循环**，只认"有兵营、本回合还没用过"的格 ----
    #   ★ 用户 2026-09-17：「征兵走的独立条件，按兵营建，和当地格子应该毫无关系，
    #     除非你满格过滤过滤掉了征兵」。原先把这段塞在下面的 `for p in free_tiles:` 里，
    #     而 `free_tiles` 只含"还建得了"的格（`slots < MAX_SLOTS`）—— 兵营格一旦建满
    #     20 座就掉出这个列表，**征兵跟着被跳过**，军队永远长不大：
    #     实测 seed 900010，兵营 4 座、装备粮食都够、`army_cap` = 282，
    #     军队却死死卡在 36 支，军费因此少 17 万（而建造量与基线其实是持平的）。
    if mil_ok and army_n < army_cap and not world.grid_short.get(name):
        for p in sorted(own):
            if army_n >= army_cap:
                break
            t = world.tiles[p]
            if t["buildings"].get("兵营", 0) <= t.get("recruited_this_turn", 0):
                continue
            if not _can_afford(world, name, R(), "步"):            # ★ v11：现读 recruit_cost
                continue
            if do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "n": 1, "unit": "步"},
                  world.recruit, name, p[0], p[1], 1, "步"):
                army_n += 1

    for p in free_tiles:
        if not free_at(p):
            continue
        t = world.tiles[p]

        # ---- ② 兵营（条件项）----
        if barr_left > 0 and slots(p) >= 3 and afford(p, "兵营") \
                and spend_ok("兵营"):
            had = t["buildings"].get("兵营", 0)     # `place` 也有一条"改建成电厂"的支路：
            if place(p, "兵营"):                    # 那条返回 True 却没建兵营 ⇒ 回读牌面判定
                if t["buildings"].get("兵营", 0) > had:
                    barr_left -= 1
                continue

        # ---- ③ 电厂（条件项）：补已成事实的缺口 ----
        #   ★ 2026-09-16（用户）：「任何电厂……也不挑地，应该密度堆积」⇒ 站址取密度最高的格
        #   （同 ROI 那条规则；池子是全图可建格，所以不会卡住建不出来）。
        if (power + gen_add) < need_pw + _dem_add:
            #   ⚠ `spend_ok` 只吃**建筑名**（`save` 是闭包里的），站址 `q` 归 `afford` 管 ——
            #     `bcc7eb1` 把站址换成 `plant_order` 时把 `q` 带进了第二个调用，成了
            #     `spend_ok(q, "木材能源厂")` ⇒ TypeError，**老师的电厂补缺这条腿整条崩**
            #     （2026-09-17 ECS 那炉 150 回合 BC 跑到第 2 局就死在这行）。
            _site = next((q for q in plant_order
                          if free_at(q) and afford(q, "木材能源厂") and spend_ok("木材能源厂")), None)
            if _site is not None and build(_site, "木材能源厂"):
                gen_add += BUILDINGS["木材能源厂"]["energy_out"]
                continue

        # ---- ④ ROI 项：**不在这里建** —— 见循环之后那一段（按回本期顺序）----

    # ---- ④ ROI 项（选项）：**按回本期顺序**花这笔钱 ----
    #   原先这里是"逐格按坐标序"发钱：排在前面的**难地**先动工（森林/丘陵/山地，施工惩罚
    #   +15~50%），回本更快的**平地**反而等不到钱 —— 实测 t=200（seed 900000）：v11plus 建了
    #   70 座在惩罚地上，而"有资源、回本达标"的平地还空着 53 格。
    #   用户 2026-09-16：「有难地你完全可以不建」⇒ 改成按 `roi`（**回本期升序**）逐格建：
    #   钱先落在回本最快的地上，难地只有在便宜地建完、钱还剩时才轮得到。
    #   （⚠ 别改成"只建 `planned` 那几条"：那张表是按预算裁过的、且被 ①②③ 花掉的钱会失效，
    #     实测那样建筑会从 219 掉到 72 —— 逐格尝试、失败即跳过才是原语义。）
    #   `best_of`（每格榜上那一个）在第 3 节就算好了 —— 木头需求与这里共用同一份。
    for _p, _bn in best_of.items():
        if not free_at(_p):
            continue
        place(_p, _bn)
