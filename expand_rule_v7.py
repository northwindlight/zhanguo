# -*- coding: utf-8 -*-
"""⚠️⚠️ **耻辱柱 —— 已废弃，勿用** ⚠️⚠️

    这个文件是**反面教材**，保留仅供对照。规则 AI 请用 `expand_rule_v8.py`。

    ■ 死因（**结构病，不是参数病**）：同一批资源上叠了**四套互不知道的账**
        ① 0.5 清仓       卖到 `must_keep`
        ② 0.6 刚性支出   买到"缺口"，`reserve=0`（有多少钱花多少）
        ③ 1/4 市场调剂   买到 `must_keep`，`reserve=200`
        ④ 建造留钱       `RESERVE=350` / `barr_fund=350`
      ②与④**直接对立**：②用 reserve=0 把现金买光，④要攒 350 买兵营 —— 谁在代码里
      靠前谁赢（②在 0.6、④在第 3 节），所以**兵营永远攒不到**。这是逻辑必然，不是
      seed 特例。而且"征兵数"在三处各算一遍（`cap_now` / `_army_cap` / 第 5 节 `cap`），
      三个数在数值上互不相关 → 装备必然"这节卖光、那节买回"。
      **改一处就顶塌另一处** —— 2026-09-11 那一天为"锁定量"来回改了四遍，每遍都把
      别的分数带下去（55.2k → 43.8k → 50.9k → 34.5k → 37.4k），全是这个结构病的症状。

    ■ 结论：v7 不该靠调参救。v8 把四套账合成**一套**（`need(物资)` 一个数，
      清仓卖到它、市场买到它），并把决策改成**串行判定、条件先判**。

    ■ 教训（写给下一个接手的人）：
      · 同一批资源上**只能有一处口径**。出现第二个"留多少/买多少"的地方就是病。
      · 改规则 AI 要先**通读逻辑**，不要一版版试参数 —— 试参数会在四套账之间
        互相抵消，看起来"怎么调都没用"。
      · 每改一版先 commit，别攒工作区（那天就是因为没有存档点，一错就全丢）。

────────────────────────── 以下为 v7 原文，已废弃 ──────────────────────────

扩张流规则 AI · v6（用户 2026-09-10 第二版）

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

from game import BUILDINGS, TERRAIN_STATS, ARMY_MAX_HP, MAX_SLOTS
from mp import best_build

# 各地形一轮击杀所需兵力（含 1 支余量，防骰子修正）
# 建筑 → 它要的本地资源（按 ROI 挑采集建筑时用）
_RES_OF = {"农场": "耕地", "矿场": "矿石", "林场": "木头",
           "石油厂": "石油", "黄金矿场": "黄金"}

TROOPS_FOR = {"沙漠": 2, "平原": 2, "森林": 2, "丘陵": 2, "山地": 3}
# ↑ **除山地外一律 2 支**（用户 2026-09-11：v6 的森林 3 / 丘陵 3 太保守）。
#   算一下就明白：野人 100 血，2 支各 50 攻 = 100 伤害；森林减伤 10% → 90，
#   差 10 血没打死 —— **能打，只是慢一回合**（下回合补一刀就死）。
#   v6 把门槛抬到 3 支，于是兵不到 3 支时一步不动，实测在"四周全是森林丘陵"
#   的开局图上直接卡死：138 次 move、**0 次 attack**、永远 5 块地。
# ↑ 山地从 v6 的 4 改成 3：**用户 2026-09-11 的口径是「两两成组、山地三三成组」**。
#   （v6 的文档按伤害数学推的是 4 支一轮击杀；3 支可能打不死、要挨一轮反击。
#     若实测发现山地战损明显变大，把这里改回 4 即可。）

HORIZON = 200         # 评估基准回合数（用户 2026-09-11：**以后都按 200 回合算，
                      #   不做长期 ROI**）。200 回合下开荒晚了很吃亏 —— 这是
                      #   多人局的真实约束，早占产能比抠回本更重要。
ARMY_MIN = 2          # 出兵里程碑：满 2 支才交给 v6 的扩张逻辑
MIL_SHARE_MIN = 0.15  # ★ 军费闸门（用户 2026-09-11）：**出兵、涨兵的条件都是 15%**
                      #   —— 军费占收入低于它 = 军队太小还养得起 → 补征；到它就停。
MIL_SHARE_MAX = 0.60  # 保留：上限（高于它 → 停止扩军，专心复利）


def expand_rule_turn_v7(world, name: str, rng: random.Random | None = None,
                     max_actions: int = 40, on_action=None, on_result=None) -> list:
    """v7 = **v6 的骨架 + 三处改动**（2026-09-11 用户定的方案）。

    基准是 v6（清仓 → 铺产能 → 扩张 → 打野人），只改：

    ① **出兵提前**：v6 的 `supply_cap()` 要补给产能跟上才肯征，前 40~60 回合一支不出。
       v7 在**满 2 支兵之前直接交给 `expand_rule_open`**（实测 30/30、首支兵中位第 7
       回合，最迟 15 回合以内）。
    ② **军费占比落在 20%~60% 的带子里**：低于 20% 说明军队太小（扩张与收入都会被拖住），
       补征；高于 60% 停止扩军、专心复利。口径按用户定的 (a)：**只算军费（补给），
       不算一次性的征兵费**。
    ③ **军事教条**：
       - **从不单兵作战**（`TROOPS_FOR` 最少 2 支，v6 已有）
       - **山地 3 支**（v6 原为 4，按用户口径改小）
       - **只满血扩张**：血不满的兵不参战、不推进
       - **绕山地**：行军不踩山地格（简单版：邻格是山地就不选它）
    ④ **建筑目标驱动**：沿用 v6 的建造优先级队列（金矿权重最大），按目标缺口整笔买料。
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

    # ---- ① 出兵提前：**不交棒、不换模块**，只把 v6 的征兵闸门换掉 ----
    # 用户 2026-09-11 拍板：「不交棒，直接 v7 扩展」。
    # 先前那版是「前期调 expand_rule_open、达标后交给 v6」，结果两套经济逻辑在接缝上
    # 打架（补给厂专款 / 清仓清单 / 买粮次序各一套），补给厂 40 回合建不起来、部队饿死。
    # v6 本身该有的机器（清仓、按格建、兵营专款）一样不少，出兵晚**只因一个闸门**
    # `supply_cap()`；把闸门换成下面的 20%~60% 带子，它自己就会早出兵。
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
        # ⚠️ 必须是 `>=` 而不是 `>`：清仓要卖"全部余量"时 qty 恰好等于 have，
        #    用严格大于会让**要卖光的物资一个都卖不掉**（引擎都调不到，日志里看不见）。
        #    实测：粮食从 20 一路堆到 201（折金 305），现金常年 2~5 ——
        #    自由现金流 10~16/回合是真的，但全卡在这一行上。
        return (int(qty) > 0 and int(R().get(good, 0)) >= int(qty)
                and do("sell", {"good": good, "qty": int(qty)},
                       world.sell, name, good, int(qty)))

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
    # 兵营编制缺口 / 想要的兵营数 —— **兵营的入选条件**（它不看回本，见选目标处），
    # 提到这里算，是因为选目标时就要用（兵营要能进目标，电厂才看得见它）。
    want_barr = min(120, max(2, supply_cap() // 2 + 2))
    army_room = max(0, supply_cap() - army_n - barr_n)

    # 清仓清单。
    # ⚠️ 之前的写法「补给和装备绝不卖」是**错的**：
    #    卖补给本身确实不计消费，但换来的钱去建造 → 计入 build（实测 +1221）。
    #    让它在仓库发霉才是真的 0。所以补给必须纳入清仓，只留军队口粮。
    # ★ v7 改动：清仓清单**只留本回合真要用掉的量**（用户口径：不买多，也不买少）。
    # v6 的旧清单是固定缓冲，最大一笔是「木头 90」≈ **180 金**，注释还写着"随时可补"
    # —— 于是现金被换成木头堆在仓库里，而建造恰恰因为没现金而没发生。
    # 实测（seed0，120 回合）：**期末现金 1~5 金、库存折金 185 金、只建了 14 座**，
    # 自由现金流 9.7/回合全被囤住了 —— 这就是"70% 的钱没体现"。
    # ---- 0.4 **选一个目标，攒它的料**（用户 2026-09-11 的口径）----
    # 先前留的是拍脑袋的固定值（先 90、后 45）—— **没有任何建筑要 90 木**，最贵的
    # 市政厅也只要 40。正确做法：**先选定这一座要建什么，清仓时只留它要的料**，
    # 其余全卖成现金去攒那一座；攒够就建，建完再选下一个。
    from mp import build_econ
    from spend_rules import army_upkeep_units      # 补给只留本回合真要吃的量
    # 电力账（**必须在选目标之前算**：目标要不要换成电厂，全看这个余量）
    power = cnt("木材能源厂") * 2 + cnt("石油能源厂") * 8
    need_pw = cnt("补给厂") + cnt("装备厂") + cnt("兵营") + cnt("市政厅")

    # ---- 电力闸门（**不变量**）------------------------------------------------
    # **任何用电建筑落地前，账上必须先有电。**
    #   账 = (发电 + 本回合已下单的电厂×2) − (已建成用电 + 本回合已下单的用电)
    # 为什么能这样一笔一笔记：在建建筑**一回合落地、落地当回合不产出**（mp.py:1310），
    # 所以本回合下单的电厂和用电建筑**同时**在下回合生效，正好互相抵消。
    #
    # ⚠️ 这才是这一版真正的修法。先前"先造电厂"挂在 `target` 上，可**真正决定建什么的是
    #    每格的 `_cand` 候选表** —— 补给厂在表里、靠回本就能赢，赢的时候**没有任何地方
    #    问过电**；target 那天若是兵营/农场，电厂那条根本不触发。实测形状：
    #    每建一座补给厂就缺电 3~4 回合，一直等到 target 轮到用电建筑才补电厂
    #    （seed15：T172 建补给厂 → 缺电 T172~T175 → T175 才建电厂；T184、T193 同形）。
    #    → 条件必须挂在**落地那一刻**，不是挂在"目标"上。
    _gen_add = 0      # 本回合已下单的电厂发电量
    _dem_add = 0      # 本回合已下单的用电建筑耗电量

    def _pw_ok(bn: str) -> bool:
        """账上还有电给 `bn` 吗？（不用电的建筑恒 True）"""
        e = BUILDINGS[bn].get("energy", 0)
        return e == 0 or (power + _gen_add - need_pw - _dem_add) >= e

    def _pw_try_plant(p, reserve: int) -> bool:
        """电不够 → 先在**本格**把电厂顶上（用户口径：兵营和补给厂要先造电厂）。"""
        nonlocal _gen_add
        if not afford("木材能源厂", reserve):
            return False
        if build(p, "木材能源厂"):
            _gen_add += BUILDINGS["木材能源厂"]["energy_out"]
            return True
        return False

    def _pw_build(p, bn: str, reserve: int = 0) -> bool:
        """建一座 `bn`，**带电力闸门**：电不够就先建电厂；电厂也建不起就不建。

        返回 True = 本格已被占用（调用方 continue）。
        """
        nonlocal _dem_add
        if not _pw_ok(bn):
            _pw_try_plant(p, reserve)      # 顶不上电厂也不能硬建用电建筑
            return True
        if build(p, bn):
            _dem_add += BUILDINGS[bn].get("energy", 0)
        return True
    # ★ **目标必须能落地**：先前只按 `cnt(bn) < 200` 过滤，best_build 就挑回本最快的
    #   黄金矿场（215 金）—— 可手里可能根本没有「黄金」格，永远建不了，
    #   于是金攒着不用、别的也不建（全刚需保留量那版 13.9k 就是死在这）。
    #   正确的候选 = **自有格上资源没建满、且本回合没下过单**的那些。
    _placeable = []
    for _p in own:
        _t = world.tiles[_p]
        if _t.get("built_this_turn"):
            continue
        _slots = sum(_t["buildings"].values()) + sum(_t.get("pending", {}).values())
        if _slots >= MAX_SLOTS:
            continue
        for _bn in ("黄金矿场", "矿场", "林场", "农场", "石油厂"):
            _r = _RES_OF[_bn]
            if _t["resources"].get(_r, 0) > _t["buildings"][_bn] + _t.get("pending", {}).get(_bn, 0):
                _placeable.append((_bn, _p))          # **带地块**：按该格实际造价算
        # 补给厂：不挑资源，有空位就能落（用户：补给厂也按 ROI 算）
        # **装备厂已删**（用户 2026-09-11）：引擎给它的产出按"买价×2/回合"折价，
        # 可装备是**存量**（只在征兵时一次性耗 5/支），回本被虚高 → 它赢了 ROI 榜、
        # 吃掉早期现金（实测 T10 就建了一座 228 金的装备厂，把金矿拖到 T60）。
        _placeable.append(("补给厂", _p))
        # ★ **兵营接到 ROI 里**（用户 2026-09-11），但**条件不同**：kind=barracks 时
        #   `build_econ` 给 per=0、payback=None，靠回本**永远选不中** —— 于是由
        #   **编制缺口**（`want_barr` / `army_room`）替它把门（见下面 target 处）。
        #   接进 ROI 的意义：它从此**会出现在目标里**，电厂那条"目标是用电建筑"才看得见它
        #   —— 先前兵营是专用分支建的，不经 best_build，所以缺电 42 回合也没人管。
        _placeable.append(("兵营", _p))
        # 电厂同理：kind=energy 不产货、payback=None，入选条件**不是回本，是电**。
        _placeable.append(("木材能源厂", _p))
    # **每回合按当前市价重评**，且**只接受回本能落在剩余回合内的**
    # （用户口径：一局才 50 回合，回本 40 回合的农场在第 30 回合建就是纯亏；
    #   而市价是会崩的 —— 实测木头 2.00→0.82，按名义价算的回本全是假的）
    _left = max(1, HORIZON - world.turn)
    target_item, target_pb = best_build(world, _placeable, max_payback=_left)
    target = target_item[0] if target_item else None
    # ★ **条件不同的那两个候选**（回本选不中，按各自的条件替它们把门）：
    #   ① 兵营 —— 条件是**编制缺口**（`want_barr`），不是回本
    #   ② 电厂 —— 条件是**电**，不是回本（用户口径：兵营和补给厂要先造电厂）
    #   ⚠️ 别用 `army_room > 0` 当判据：`army_room = supply_cap() − army_n − barr_n`，
    #      而 `supply_cap() = 补给厂×2` —— **没有补给厂时 army_room 恒为 0**，
    #      第一座兵营恰恰就是在这之前建的（第 2 节那条不查 army_room 的分支），
    #      用 army_room 当门 → 永远不为真 → target 永远不是兵营 → 电厂那条又看不见它。
    #   ⚠️ 判据用「主城凑够 min_slots（该上兵营了）」而不是"还有编制缺口"：
    #      从第 1 回合就把目标定成兵营 → 电厂 T3 就落地（120 金 + 15 木 + 1 木/回合
    #      燃料，此时一点收入都没有）→ 实测 50.5k **掉到** 29.7k，seed15 直接 0 兵。
    #      "先造电厂"是**顺序**上的先，不是第一天就造 —— 等到真要上兵营那一刻再先它一步。
    _capital = min(own, key=lambda q: (TERRAIN_STATS[terr(q)]["build_penalty"],
                                       -sum(world.tiles[q]["resources"].values())))
    _slots_cap = sum(world.tiles[_capital]["buildings"].values()) \
        + sum(world.tiles[_capital].get("pending", {}).values())
    if barr_n < want_barr and (_slots_cap >= 3 or army_room > 0):
        target = "兵营"
    # 「一旦目标是用电的建筑，那么当电力没有大于 1 的[余量]时，电厂替换掉」——
    # 现在这条的**作用只剩一个：清仓时为电厂留料**（15 木，+ 已在场的电厂燃料）。
    # 真正"缺电就先造电厂"由 `_pw_build` 的闸门在**落地那一刻**保证（见上面电力闸门段），
    # 因为决定建什么的是每格的候选表，不是 target —— 挂在 target 上会漏
    # （实测：补给厂靠回本在候选表里赢了就建，target 那天若是兵营/农场，电厂那条不动，
    #  于是每建一座补给厂就缺电 3~4 回合）。
    #   ⚠️ 口径是「**缺位**」（缺口，`power < need_pw`），不是「余量 ≤ 1」：
    #      按余量算的话，兵营+补给厂 = 2 用电、1 座电厂正好发 2 电 → 余量 = 0 ≤ 1
    #      → **还要再来一座** → 实测 T6 就落 2 座电厂（240 金 + 30 木）。
    _plant_need = 0
    if target is not None and BUILDINGS[target].get("energy") and power < need_pw:
        _plant_need = 1
        target = "木材能源厂"                      # 顶掉：清仓会为它留 15 木
    # ★ **锁定库存（按目标）** —— 唯一口径：第 0.5 节清仓**卖到它**，第 1/4 节市场**买到它**。
    #   两处只要有一个数不同，就会"同回合先卖后买"空转（实测木头 buy48/sell54、
    #   装备 buy15/sell15）—— 所以下面所有数都从这里取，别在别处再写一遍。
    #
    #   每一个数都是**这一回合这套计划真要花掉的量**，不是拍脑袋的固定线：
    #     · 木头 = 目标建筑的木 + 电厂燃料（电厂每座 1 木/回合）
    #     · 粮食/矿石/石油 = 工厂投料（补给厂 1粮1矿、装备厂 1矿1石油，每座每回合）
    #     · 装备 = 本回合真要征的那几支（5/支）
    #     · 补给 = 本回合军队真要吃的（唯一一处用户明确要求"不用缓冲"的）
    must_keep = {
        "粮食": sup_n + planned_rec * 10,        # 补给厂投料 + 本回合征兵
        "矿石": sup_n + eqp_n,                   # 补给厂 + 装备厂投料
        "石油": eqp_n,                           # 装备厂投料
        "木头": (BUILDINGS[target]["wood"] if target else 0)
                + cnt("木材能源厂") * 2,          # 目标建筑 + 电厂燃料
        "装备": planned_rec * 5,                 # 本回合征兵
        # ★ 补给是**唯一例外**：只留本回合军队真要吃的量（用户 2026-09-11：「不用缓冲」）。
        #   原先这里是 `army_n * 2 + 5`，而第 4 节还有一条 `army_n * 12` 的补货线 ——
        #   两条对着干，实测 seed0 T11~T20 **每回合都是"卖 4 买 5"**（回合初 11 → 卖到 7
        #   → 再买回 12），白烧 10% 买卖价差，还把 60 金（1 支兵吃 1 个/回合）锁在仓库里。
        "补给": army_upkeep_units(world, name),
    }
    # 每回合每品最多卖 30：**市价冲击是持久的，分批也回不来**。
    # 实测：卖 10~20 个单价 4.5~4.6；卖 150+ 直接砸到地板 2.85（亏 43%），
    # 且连卖 25 回合也回不到原价。所以宁可慢慢出，也别倾销。
    SELL_CAP = 10 ** 9          # 不限流：宁可砸价也要现金流（见下方实测注释）
    # ★ 遍历**所有物资**，不只遍历 keep 字典里有的那几个。
    #   踩坑：`rigid_expenditure` 的 keep 只装"需要 > 自产"的项（负的会被丢掉），
    #   于是**自产过剩的物资根本不在字典里 → 永远不参与清仓 → 一直堆着**。
    #   实测（seed0）：粮食从 31 涨到 201（折金 305），而现金常年 2~5 ——
    #   自由现金流 10~16/回合是真的，但全变成卖不掉的存货，
    #   这就是"钱没体现"。字典里没有的，keep 记 0，一律卖到只剩 0。
    for g_ in ("粮食", "木头", "矿石", "石油", "装备", "补给"):
        have = int(R().get(g_, 0))
        surplus = have - must_keep.get(g_, 0)
        if surplus > 0:
            sell(g_, min(int(surplus), SELL_CAP))

    # ---------------------------------------------------------------- 0.6 刚性支出（**必须先付**）
    # 三条判据见 `spend_rules.py`（用户 2026-09-11 定死，**口径写在那边**）：
    #   ③ supply_ok          —— 回合前判断：军队必须满补给，缺就立刻补（不问价、不设余额门槛）
    #   ① rigid_expenditure  —— 算出**本回合**的账单；buy_exact **缺一个买一个**
    #   ② gate_ok            —— 刚性支出/收入 ≤ 60%（第 5 节征兵闸门用它）
    # **先付清，剩下的钱才轮到建造。**
    from spend_rules import supply_ok, rigid_expenditure, buy_exact

    _ok_sup, _gap_sup = supply_ok(world, name)
    if not _ok_sup and _gap_sup:
        buy("补给", _gap_sup, reserve=0)             # ③ 必须支出

    # ★ **本回合打算养到几支兵** —— 军费闸门，两处共用同一个数
    #   （用户 2026-09-11：**出军队的条件是 15%，涨军队的条件也是 15%**）：
    #   `军费占比 = 军队口粮折金 / 总收入`，低于 15% = 军队太小还养得起 → 放行。
    #   ⚠️ 口径必须是**军费**而不是 `gate_ok` 的刚性支出：那个账单含**一次性征兵原料**
    #      （装备 8 金 × 5 = 40），一征就顶破 60% → 闸门恒假 → 军队卡死。
    cap = _army_cap = supply_cap()
    try:
        from spend_rules import income_of, army_upkeep_units
        _inc = income_of(world, name)
        _mil = army_upkeep_units(world, name) * world.prices.get("补给", 5)
        _mshare = (_mil / _inc) if _inc > 0.5 else 1.0
        _army_cap = max(cap, army_n + 2) if _mshare < MIL_SHARE_MIN else army_n
    except Exception:                               # noqa: BLE001
        pass
    # 征兵原料**必须在这里提前买**（`rigid_expenditure` 只在本回合真要征时才把它记账）。
    # 装备 8 金/个 × 5 = 40 金 —— 等第 5 节再买就晚了：那时钱已被建造花光，
    # `buy(..., reserve=40)` 要手上 ≥80 金才买得动，买不动就 `break`，
    # 而 `q ≤ 0` 时连 `do()` 都不调 → **日志里一条都不留**（实测 seed0 T95~T110：
    # 金 0~47 锯齿、装恒为 0、`征兵0`，军队永远 2 支）。
    _plan_rec = max(0, min(cnt("兵营"), _army_cap - army_n))
    buy_exact(rigid_expenditure(world, name, recruit=_plan_rec),
              lambda g, q: buy(g, q, reserve=0))     # ① 缺一个买一个，不买多不买少

    # ---------------------------------------------------------------- 1. 木材（建造硬门槛，2金/个）
    # 买到**目标库存**（`must_keep["木头"]`）为止 —— 与第 0.5 节清仓**同一个数**。
    # ⚠️ 这里原来是"补到 70"，而清仓留的是"目标建筑的木 + 电厂燃料"（≈22）——
    #    两个数不一致 → 同回合先卖后买，实测 seed3 每回合 `buy木头48 + sell木头54`，
    #    白烧买卖价差还把现金锁在存货里（金恒卡在 299，兵营 350 永远攒不齐 → 全程冻结）。
    #    现在**只有一处口径**：`must_keep`，清仓卖到它、市场买到它。
    # ⚠️ `reserve=200` **必须留**：改成 0 会让买入"有多少钱买多少料"，把建楼的钱
    #    全吃光 —— 实测金长期只剩 4~45，AI 停建、seed15 归零（34.5k vs 50.9k）。
    #    买入是**调剂**不是**刚性支出**：刚性那部分在第 0.6 节，那里才用 reserve=0。
    if R()["木头"] < must_keep["木头"]:
        buy("木头", must_keep["木头"] - int(R()["木头"]), reserve=200)

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
                _pw_build(capital, "兵营")       # 带电力闸门：缺电先上电厂
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
    # （power / need_pw / want_barr / army_room 已在选目标时算过，这里不再重复）
    # 兵营专款：还想造兵营时，其余建筑必须留下 350 金，否则永远轮不到兵营
    # （实测：一回合 92 个动作把钱花光，终局金=79，兵营 350 永远凑不齐）
    RESERVE = 350 if (army_room > 0 and cnt("兵营") < want_barr) else 0

    # 兵营基金：没兵营就没兵，没兵就永远卡在 5 格（实测 seed0/1/2 都死在这）。
    # 所以第一座兵营落地前，除主城凑位外暂停一切建设，把钱留够 350。
    barr_fund = 350 if cnt("兵营") == 0 else 0
    # **军队也走这条路径**（用户 2026-09-11：和兵营一个逻辑）——
    # 候选 = "在本格征一支兵"，同样**不看回本、看特殊判定**（军费占比 < 15%，即 `_army_cap`）。
    # 为什么必须挪进循环：先前征兵是**建造循环跑完之后**才做的第 5 节，
    # 那时钱已经被建造花光 —— 装备 8 金/个 × 5 = 40 金买不动，
    # `buy(..., reserve=40)` 要手上 ≥80 金，买不动就 `break`，而 `q ≤ 0` 时连 `do()`
    # 都不调 → **日志里一条都不留**（实测 seed0 T95~T110：金 0~47 锯齿、装恒为 0、
    # `征兵0`，军队永远停在 2 支）。兵营当年就是同一个病，治法就是给它留下钱。
    def _recruit_here(p) -> bool:
        nonlocal army_n
        t = world.tiles[p]
        if army_n >= _army_cap:
            return False
        if t["buildings"].get("兵营", 0) <= t.get("recruited_this_turn", 0):
            return False
        if world.grid_short.get(name):
            return False
        if R()["粮食"] < 10 or R()["装备"] < 5:
            return False                            # 原料在第 0.6 节已按刚需买好
        if do("recruit", {"tile": f"{p[0]+1} {p[1]+1}", "n": 1, "unit": "步"},
              world.recruit, name, p[0], p[1], 1, "步"):
            army_n += 1
            return True
        return False

    for p in own:
        if len(acts) >= max_actions - 8:      # 留动作给征兵与进攻
            break
        t = world.tiles[p]
        if t.get("built_this_turn"):
            continue
        # （电厂不再有专门的旁路分支：它由 `_pw_build` 的闸门**按需**顶上去 ——
        #   哪一格要建用电建筑、账上又没电，就在那一格先建电厂。一个决策点，一处口径。）
        # 征兵也是候选之一（和兵营同一个逻辑）：特殊判定 `_army_cap`（军费 < 15%）。
        if _recruit_here(p):
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
            _pw_build(p, "兵营", RESERVE); continue
        # 采集类：**按游戏层的 ROI 挑**，不再手写顺序。
        # v6 原来的顺序是 农场→矿场→林场→石油厂→**黄金矿场**，而 ROI 算出来
        # 金矿 21 回合最快、矿场 27、林场 38、农场 40 —— 等于"最赚的最后建"，
        # 这正是用户说的「金矿权重最大」一直没落实的地方。
        _cand = [(bn, p) for bn in ("黄金矿场", "矿场", "林场", "农场", "石油厂")
                 if res.get(_RES_OF[bn], 0) > eff(bn) and afford(bn, RESERVE)]
        # 补给厂 / 装备厂也进候选（用户 2026-09-11）—— 它们的回本由 ROI 说话，
        # 该建就建、不该建就不建，别写死。要电/要料都在 build_econ 里算进去了。
        if afford("补给厂", RESERVE):          # 装备厂已删（ROI 口径虚高，见上）
            _cand.append(("补给厂", p))
        # 兵营也接到这个候选表里（用户：**兵营在 ROI 里走特殊判定**）——
        # 它的判定不是回本（上面那个分支已经按编制缺口把它挑走了），
        # 放进候选表是为了它**和别的建筑走同一条路径**，不再有旁路。
        if afford("兵营", RESERVE):
            _cand.append(("兵营", p))
        # 电厂也进同一个候选表（用户口径：**接到 ROI 中，但条件不同**）。
        # 它 kind=energy、不产货 → build_econ 给 payback=None，**靠回本永远选不中**；
        # 于是它的入选改用"电"—— 条件不同，路径相同（条件落在下面 `_pw_build` 的闸门上）。
        if afford("木材能源厂", RESERVE):
            _cand.append(("木材能源厂", p))
        _pick, _pb = best_build(world, _cand, max_payback=max(1, HORIZON - world.turn))
        if _pick:
            # **落地这一刻过电力闸门**：候选择优只看回本，但"能不能再上一座用电建筑"
            # 是另一条条件 —— 电不够就先在**本格**顶电厂（电厂的入选条件），
            # 而不是像先前那样建完了才在 3~4 回合后补电。
            _pw_build(p, _pick[0], RESERVE); continue
        # 兵营：征兵吞吐瓶颈（每座每回合 1 兵）
        if army_room > 0 and cnt("补给厂") >= 6 and cnt("兵营") < want_barr \
                and sum(bb.values()) >= 3 and afford("兵营", RESERVE):
            _pw_build(p, "兵营", RESERVE); continue
        # 补给厂：决定军队规模。原料可外购，所以只要有电就堆
        if cnt("补给厂") < 200 and power > need_pw and afford("补给厂", RESERVE):
            _pw_build(p, "补给厂", RESERVE); continue
        if cnt("装备厂") < 4 and cnt("石油厂") > 0 and afford("装备厂"):
            _pw_build(p, "装备厂", 0); continue

    # ---------------------------------------------------------------- 4. 市场调剂
    # ★ **整节已删**（用户 2026-09-11：只留本回合刚需）。
    #   这里原是 v6 的三条**固定囤货线**（粮食补到 sup_n+20、矿石补到 sup_n+eqp_n+10、
    #   装备补到 20，且都要金 ≥ 300 才动手）—— 它们和第 0.5 节清仓**对着干**：
    #   清仓把装备卖到 `planned_rec*5+5`(=5)、粮食卖到刚需，第 4 节转头买回 20 / sup_n+20。
    #   实测 seed3 每回合：`buy木头48+sell木头54`、`buy装备15+sell装备15`（**120 金买了立刻
    #   全卖回去**）、`buy粮食9+sell粮食16` —— 白烧买卖价差，并把现金锁死在存货里，
    #   金恒卡在 299 → 兵营 350 永远攒不齐 → **seed3 全程冻结（0 兵/5 地）**。
    #   ★ 这一节的功能**全都已被第 0.6 节覆盖**，不是删了就少东西：
    #     · 工厂投料（含补给厂 1粮1矿、装备厂 1矿1石油）→ `rigid_expenditure` 的 ②
    #     · 能源厂燃料 → 同上的 ③
    #     · 征兵原料 → 同上的 ④（按 `_plan_rec` 算）
    #     · 口粮 → `supply_ok` + `buy_exact`（上一轮已从 `army_n*12` 缓冲改过来）
    #   所以这里**不该再有第二套账面**。
    # ★ 但**不能整节删掉**（实测过：删了 43.8k → 32.7k，seed15 直接塌成 0 兵/5地）——
    #   这些"买"本身是值钱的（外购原料开补给厂是正收益：1粮+1矿=6 金 → 2补给=10 金）。
    #   病根只是它和清仓**各用各的数**。治法 = **统一到 `must_keep` 一处**：
    #   清仓卖到 `must_keep`，市场买到 `must_keep` —— 同一个数就不可能有空转。
    sup_n = cnt("补给厂")
    if R()["粮食"] < must_keep["粮食"]:
        buy("粮食", must_keep["粮食"] - int(R()["粮食"]), reserve=200)
    if R()["矿石"] < must_keep["矿石"]:
        buy("矿石", must_keep["矿石"] - int(R()["矿石"]), reserve=200)
    if R()["装备"] < must_keep["装备"]:
        buy("装备", must_keep["装备"] - int(R()["装备"]), reserve=200)
    # （清仓已移到第 0.5 节，在建房之前执行——卖完立刻拿钱去建，不留隔夜钱）

    # ---------------------------------------------------------------- 5. 征兵（规模按军费带子）
    cap = _army_cap          # 军费闸门算出来的（口径见第 0.6 节，两处共用同一个数）

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
            # **只满血参战**（v7 教条）：血不满的兵先回血（25/回合），不推进也不打。
            # 半血去啃野人 = 白白送掉，而回血是免费的。
            near = [a for a in armies if a["id"] not in used_ids
                    and not a.get("engaged") and a["hp"] >= ARMY_MAX_HP
                    and max(abs(a["x"] - tx), abs(a["y"] - ty)) <= 1]
            # 不在旁边的，先移动过去
            if len(near) < need:
                movers = [a for a in armies if a["id"] not in used_ids
                          and not a.get("engaged") and a["hp"] >= ARMY_MAX_HP
                          and a.get("moved_turn") != world.turn]
                for a in movers[:need - len(near)]:
                    # **绕山地**（简单版）：邻格是山地就不选它 —— 山地行军亏、战斗也亏
                    # （守方 +50% 减伤），宁可多走一步平地。
                    cands = [q for q in world.neighbors(a["x"], a["y"])
                             if world.owned_by(*q) is None
                             and world.tile_terrain(*q) != "山地"]
                    if not cands:
                        continue
                    cur = max(abs(a["x"] - tx), abs(a["y"] - ty))
                    step = min(cands, key=lambda q: max(abs(q[0] - tx), abs(q[1] - ty)))
                    if max(abs(step[0] - tx), abs(step[1] - ty)) < cur:
                        do("move", {"army_id": a["id"], "x": step[0] + 1, "y": step[1] + 1},
                           world.move, name, a["id"], step[0], step[1])
                        used_ids.add(a["id"])
                near = [a for a in armies if a["id"] not in used_ids
                        and not a.get("engaged") and a["hp"] >= ARMY_MAX_HP
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
                _pw_build(p, "市政厅")       # 市政厅也吃电（energy=1），同样过闸门
                break
            if afford("城堡"):
                build(p, "城堡")
                break
    return acts
