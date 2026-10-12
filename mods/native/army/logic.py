from __future__ import annotations
from ..core import *
from .rules import *
from ..localization import resource_name

def military_receipt(method):
    """回执报告实际位置和额度变化，失败也不能让调用者靠猜。"""
    from functools import wraps
    @wraps(method)
    def apply(self, name, ids, *args, **kwargs):
        requested = ids if isinstance(ids, list) else [ids]
        armies = [a for a in self.armies if a['owner'] == name and a['id'] in requested]
        before = {a['id']: (a['x'], a['y'], a.get('moved_turn')) for a in armies}
        ok, message = method(self, name, ids, *args, **kwargs)
        if method.__name__ == 'move' and any(
                (a['x'], a['y'], a.get('moved_turn')) != before[a['id']] for a in armies):
            self.state_version += 1
        details = []
        for a in armies:
            previous = before[a['id']]
            changed = (a['x'], a['y']) != previous[:2]
            consumed = previous[2] != self.turn and a.get('moved_turn') == self.turn
            details.append(f"军队{a['id']} 位置{'已改变' if changed else '未改变'}→({a['x']+1},{a['y']+1})；"
                           f"本次额度{'已消耗' if consumed else '未消耗'}；"
                           f"当前额度{'已用' if a.get('moved_turn') == self.turn else '可用'}")
        status = '失败' if not ok else ('已提交撤离' if method.__name__ == 'retreat' else '已执行')
        return ok, message + f"\n命令状态：{status}；" + (' / '.join(details) or '无有效我方军队，位置和额度未改变')
    return apply

class ArmyLogic:

    def _resource_balance(self, owner, resource_id):
        return self.runtime.object_call(self, resource_id, 'balance', owner)

    def _consume_resource(self, owner, resource_id, amount):
        return self.runtime.object_call(self, resource_id, 'consume', owner, amount)

    def _resource_value(self, resource_id, amount):
        return self.runtime.object_call(self, resource_id, 'value', amount,
                                        self.market_price(resource_id))

    @property
    def armies(self): return self.units
    @armies.setter
    def armies(self, values): self.units.replace(values)
    @property
    def nation_code(self): return self.units.nation_code
    @nation_code.setter
    def nation_code(self,value):
        self.units._write()
        self.units.nation_code=value
    @property
    def next_army_seq(self): return self.units.next_army_seq
    @next_army_seq.setter
    def next_army_seq(self,value):
        self.units._write()
        self.units.next_army_seq=value
    @property
    def guard_once(self): return self.units.guard_once
    @guard_once.setter
    def guard_once(self,value):
        self.units._write()
        self.units.guard_once=value
    @property
    def _next_code(self): return self.units.next_code
    @_next_code.setter
    def _next_code(self,value):
        self.units._write()
        self.units.next_code=value
    @property
    def _engage_seq(self): return self.units.engage_seq
    @_engage_seq.setter
    def _engage_seq(self,value):
        self.units._write()
        self.units.engage_seq=value
    def _mv_wall(self, name: str, x: int, y: int) -> str | None:
        """mv 走一步进这格的"墙"：`None` = 可通行。

        口径与旧版**目标格**判定逐条对齐（只是现在每步都要过这一关）：
        野地 / 自家 / 盟国可走；敌国与中立领土一律不可（要进占只能 atk）；
        野地上有与你交战的敌军驻守也不可（得先 atk）。
        """
        owner = self.owned_by(x, y)
        if owner is not None and owner != name and not self.allied_between(name, owner):
            # ★ 话术要"教学式"：光说"不行"会让模型换个姿势再撞（实测赵 连撞 5 次 battle 自家地，
            #   2026-09-20）。这里直接给三条出路：夺格用 atk / 靠近改走无主或自家地 / 查军队面板。
            return (f"({x + 1},{y + 1}) 是「{owner}」的领土，**mv 进不去**（mv 只走自己地/盟国地/"
                    f"无主地）。要夺这格：用 atk（那几支军会突入交战）；只是想靠近：改走相邻的"
                    f"无主地或自家地；先看清各军在哪、能不能动：query panel=army"
                    if self.war_between(name, owner)
                    else f"中立不可入境：({x + 1},{y + 1}) 是「{owner}」的领土——mv 只走自己地/"
                         f"盟国地/无主地；要进它的地，先结盟或先宣战（宣战后用 atk 夺格）")
        if owner is None and any(d["owner"] != name and d["owner"] != "野人"
                                 and (d["x"], d["y"]) == (x, y)
                                 and self.war_between(name, d["owner"])
                                 for d in self.troops):    # 谓词本就排除野人 ⇒ 只扫国家军队
            return f"({x + 1},{y + 1}) 有敌军驻守，不能 mv 过去；进攻请用 atk（会交战）"
        return None


    def _reachable(self, name: str, a: dict, *, for_attack: bool = False
                   ) -> dict[tuple[int, int], int]:
        """该军本回合**走得到**的格子 → 累计代价（一致代价搜索，代价是 1/2 的小整数）。

        规则（除了"逐格走"本身，其余与旧口径一致）：

        - 每步消耗 = `max(出发格代价, 目标格代价)`（用户 2026-09-15：**对称**——
          进森林/山地减速，**待在森林/山地里的那一回合也一样慢**，不存在
          "从山上冲出来跑更快"），总预算 `unit_speed(a)`
          ⇒ 骑兵平地 2 格；涉森林/山地一步吃满 ⇒ **只能 1 格、且不可能穿过山地**；
        - **中间格**必须可通行（`_mv_wall` 为 None）：野地/自家/盟国；
        - `for_attack=True` 时**终点**额外允许敌国领土与驻军格（那是要打的），
          但中途仍必须可通行 —— 所以"隔着一座山打到纵深"不再可能；
        - 起点以代价 0 计入（原地 atk 用得上；mv 到自己格照旧是"挪了个寂寞"）。

        返回 `{格子: 代价}`（不含预算之外的格子）。

        ★ 2026-09-16 换实现，**逐值等价**（同一套判定式、同样的松弛条件与"可攻终点只记不扩"）：
          预算只有 1~2（`unit_speed`）⇒ 用**桶队列**替掉堆（省掉每回合几万次 heapq 调用），
          邻居改用按边长预建的**整数下标表**（`_adjacency`，纯几何）替掉"每步现建列表"。
          这是 v11plus 每支军每回合都要走的路径（`best_step` 与 `marchable` 的前提）。
        """
        size = self.size
        adj = _adjacency(size)
        budget = unit_speed(a)
        start = a["x"] * size + a["y"]
        best = bytearray([255]) * (size * size)      # 索引 = x * size + y；255 = 还没到过
        best[start] = 0
        buckets: list = [[] for _ in range(budget + 1)]
        buckets[0].append(start)
        # ★ 碰过的格记一份**下标名单**：收尾只把这几格还原成坐标建 dict。
        #   （别去枚举整张 `best`：那是 O(全图)，实测比旧版还慢一倍。）
        touched: list = [start]
        terrain = self.tile_terrain
        wall = self._mv_wall
        move = a.definition['move_cost']
        for d in range(budget + 1):
            b = buckets[d]
            while b:
                i = b.pop()
                if best[i] != d:                     # 陈旧条目（后来有更近的路）
                    continue
                x, y = divmod(i, size)
                here = move.get(terrain(x, y), 1)
                for j in adj[i]:
                    jx, jy = divmod(j, size)
                    cj = move.get(terrain(jx, jy), 1)
                    ncost = d + (here if here > cj else cj)
                    if ncost > budget or ncost >= best[j]:
                        continue
                    why = wall(name, jx, jy)
                    if why:
                        if for_attack and self._atk_target_ok(name, jx, jy):
                            best[j] = ncost          # 可攻：算**终点**，但不从它继续扩
                            touched.append(j)
                        continue                     # 走不进去 ⇒ 更不能穿过
                    best[j] = ncost
                    touched.append(j)
                    buckets[ncost].append(j)
        return {(i // size, i % size): best[i] for i in touched}


    def recruit_cost(self, name: str, kind: str) -> dict[str, int]:
        """该国征召一支 `kind` 实际要花的原料（**含政体特价**）。

        引擎（`recruit`）与 AI 文案（`玩家接口` 的征召工具描述）都走这一份口径 ——
        曾经两边各写一遍数字，改一处就漂一处。
        """
        return dict(self.polity_rule(name, "recruit", {}).get(kind)
                    or self.units.definition(kind)["recruit"])


    def _atk_target_ok(self, name: str, x: int, y: int) -> bool:
        """这一格能不能当 atk 的**终点**（敌国领土 / 野地驻军）= "有东西可打/可占"。
        与 `attack` 里那套墙判定的"可攻"侧一致（不含"不抢别人的战斗"那条，那条在 attack 里）。"""
        owner = self.owned_by(x, y)
        if owner is not None:
            return owner != name and not self.allied_between(name, owner) \
                and self.war_between(name, owner)
        return any(d["owner"] != name and (d["x"], d["y"]) == (x, y) for d in self.armies)


    def _unreachable_msg(self, a: dict, x: int, y: int, *, for_attack: bool) -> str:
        """走不到目标格时的说法：能教人（平地几格、崎岖几格），但**不替玩家开图**
        ——地形只在视野内才点名（迷雾限眼不限手：盲推撞墙如实报"墙在视野外"）。"""
        label = unit_label(unit_kind(a), definition=a.definition)
        budget = unit_speed(a)
        ter = self.tile_terrain(x, y)
        here = self.tile_terrain(a["x"], a["y"])
        # ★ 一步的代价 = **max(出发格, 目标格)** —— 与 `_reachable` 的桶队列同口径
        #   （本文件 `ncost = d + (here if here > cj else cj)`）。
        #   曾经只取**目标格** ⇒ 站在崎岖地上时：① 报出的代价偏小；② "一步就吃满"
        #   那句该响不响（骑兵站山地→平原：真实 2≥2 吃满，旧算法算出 1<2 不报）。
        cost = max(unit_move_cost(a, here), unit_move_cost(a, ter))
        acted = "冲不进去" if for_attack else "走不到"
        seen = self.visible_to(a["owner"], x, y)
        if seen:
            # ★ `budget > 1`：步兵移动力天然只有 1，"吃满"对它是恒真的废话
            #   （任何地形都报同一句）⇒ 只在**多格兵种**上才点破地形吃满。
            tip = (f"（{label}移动力 {budget}；自{here}进{ter}这一步要 {cost}"
                   f"{'，吃满 ⇒ 本回合只走得了这 1 格' if budget > 1 and cost >= budget else ''}）")
        else:
            tip = f"（{label}移动力 {budget}；沿路地形/敌情不明——盲推撞墙如实报告，但额度照烧）"
        return f"{a['name']} {acted} ({x + 1},{y + 1}){tip}"


    def _ensure_guardians(self):
        """把**全图**无主地块都放上野人守卫（开局一次；读档时对旧档做同样的一次性补齐）。

        野人是地图的静态属性，不是"谁看见了才存在"——同一 seed 的野人布局永远相同，
        也不会因为你占了一块地就让边界外凭空冒出野人来。已死过守卫的格子（guard_once）
        不再补：杀了就是永久清空。"""
        for x in range(self.size):
            for y in range(self.size):
                if (x, y) in self.tiles or (x, y) in self.guard_once:
                    continue
                self._spawn_guardian(x, y)


    def _spawn_guardian(self, x: int, y: int):
        self.units.spawn("野人",'native:infantry',x,y)
        self.guard_once.add((x, y))  # 出生过就算数：这格野人死了不再有


    def _drop_guardians(self, x: int, y: int):
        self.armies = [a for a in self.armies
                       if not (a["owner"] == "野人" and (a["x"], a["y"]) == (x, y))]


    def recruit(self, name: str, x: int, y: int, n: int = 1, kind: str = 'native:infantry') -> tuple[bool, str]:
        t = self.tiles.get((x, y))
        if kind not in self.units.type_ids:
            return False, f"未知兵种：{kind}（可选：{'、'.join(self.units.type_ids)}）"
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        if t is None or t["owner"] != name:
            return False, "只能在自己有兵营/军屯的地块征兵"
        if kind == 'native:militia':
            # 民兵走军屯征召：军屯不耗电，不受全国电网停摆影响。
            # 双限额（2026-10-10 起每座军屯 2 支，用户「军屯可以生产和驻守两队民兵」）：
            #   ① 每座军屯每回合 `militia_cap` 支；② **全国民兵总数 ≤ 全国军屯总数×militia_cap**。
            # ★ 三处（这里两处 + `_supply_need` 的免补给覆盖）**都读同一个 effects 值**，
            #   不许谁再写死一个 1 或 2——数值只有一个权威（`balance.BUILDINGS["军屯"]`）。
            _mcap = building_effect("军屯", "militia_cap")          # 每座**能驻**几支
            _mturn = building_effect("军屯", "militia_per_turn")     # 每座**每回合能征**几支
            if t["buildings"]["军屯"] <= 0:
                return False, (f"该地块没有军屯（民兵只能在军屯征召：50金+5粮/支，"
                               f"每军屯每回合{_mturn}支）")
            tile_cap = t["buildings"]["军屯"] * _mturn - t.get("militia_recruited_this_turn", 0)
            if tile_cap <= 0:
                return False, f"本回合该地块民兵征召产能已用完（每军屯 {_mturn} 支/回合）"
            quota = self.nation_building_count(name, "军屯") * _mcap
            alive = sum(1 for a in self.troops if a["owner"] == name and unit_kind(a) == 'native:militia')
            cap = min(tile_cap, quota - alive)
            if cap <= 0:
                return False, (f"民兵总数已达军屯编制上限（{alive}/{quota} 支）："
                               f"军屯即民兵编制（每座可驻 {_mcap} 支）——想扩编先建军屯，"
                               f"阵亡或遣散（disband）后方可补员")
        else:
            if self.grid_short.get(name):
                return False, "全国电网不足，高级建筑（含兵营）停摆，无法征兵"
            if t["buildings"]["兵营"] <= 0:
                return False, "该地块没有兵营"
            cap = t["buildings"]["兵营"] * building_effect("兵营", "recruit_cap") - t["recruited_this_turn"]
            if cap <= 0:
                return False, "本回合征召产能已用完（每兵营 1 支/回合）"
        n = min(n, cap)
        cost = self.recruit_cost(name, kind)
        n = int(min(n, min(self._resource_balance(name, f) // amt for f, amt in cost.items())))
        if n <= 0:
            return False, "战略储备不足（每支耗 " + "、".join(f"{resource_name(f)}x{a}" for f, a in cost.items()) + "）"
        # Resolve all resource capabilities and valuation before spending.
        spent = sum(self._resource_value(f, amt*n) for f, amt in cost.items())
        for f, amt in cost.items():
            self._consume_resource(name, f, amt * n)
            if f in self.flow_out:
                self.flow_out[f] += amt * n   # 世界流量：征兵吃粮吃装备（黄金是货币，不计）
        self._spend(name)["recruit"] += spent
        for i in range(n):
            self.units.spawn(name,kind,x,y,turn=self.turn,newborn=True)
        if kind == 'native:militia':
            t["militia_recruited_this_turn"] = t.get("militia_recruited_this_turn", 0) + n
        else:
            t["recruited_this_turn"] += n
        return True, f"征召 {n} 支{unit_label(kind, definition=self.units.definition(kind))} @{t['name']}"


    def _assign_code(self, name):
        self.units.assign_code(name)


    def _new_army(self, owner):
        return self.units.allocate(owner)


    def _next_engage_seq(self):
        return self.units.next_engagement()


    def _claim_winner(self, alive: dict[str, list[dict]], *, attackers: set[str] | None = None) -> str | None:
        """该格（野地或敌国）的归属候选：进攻方中「无活敌」者，按入场序号取最早 atk 的
        ——**索取者优先**；索取者已阵亡则顺位给最早入场的同盟者。无候选返回 None。
        attackers=本场战斗的进攻方集合（战斗结算用；此时 engaged 标记可能已被清扫）。
        attackers=None 时不再筛进攻方，只看「在场且与格上他人无交战」——供「守军尽撤」用。"""
        claim = []
        for F, fs in alive.items():
            if F == "野人":
                continue
            if attackers is not None and F not in attackers:
                continue
            # ★ 已下令撤退的军队**不索取地块**：人都要走了，把地判给一支正在脱离的军队
            #   （甚至全 faction 皆撤 → 格归一个驻军为零的国家）不合理；留下的人才配拿。
            stay = [a for a in fs if not a.get("retreat_to")]
            if not stay:
                continue
            if any(G in alive and G != F and self.war_between(F, G) for G in alive):
                continue
            claim.append(F)
        if not claim:
            return None
        return min(claim, key=lambda F: min((a.get("engage_seq", 10 ** 9) for a in alive[F]
                                             if not a.get("retreat_to")),
                                            default=10 ** 9))


    @property
    def troops(self):
        return self.units.troops


    @property
    def guardians(self):
        return self.units.guardians


    def _army_split(self):
        self.units._index()


    def _army(self, name: str, aid: int) -> dict | None:
        return self.units.by_id(aid,name)


    def nation_armies(self, name: str) -> list[dict]:
        return list(self.units.of(name))


    def _defs_at(self, name: str, x: int, y: int) -> list[dict]:
        """这一格上、对 `name` 而言算**敌人**的军队（引擎口径的唯一出处）。

        ★ 2026-09-16：不再全表扫——**野人从不移动**，所以按格查表（`guardians`）；
          国家军队那张表小（几十支），直接扫 `troops`。逐条等价，唯一要守的是**顺序**：
          原写法按 `self.armies` 的列表序产出，而野人恒为该列表的**前缀**
          （`_ensure_guardians` 在建局时一次铺完，此后只会被撤走/阵亡，不会插到国家军队之后），
          所以"先野人、后国家军队（各按列表序）"与原序**逐位相同** ——
          `tests/test_troops.py` 用一份朴素实现逐格对照钉住它。
        """
        owner = self.owned_by(x, y)
        out = []
        if owner is None:                       # 野人只守无主格（已物化的地块上没有野人）
            g = self.guardians.get((x, y))
            if g is not None:
                out.append(g)
        for a in self.troops:
            if (a["x"], a["y"]) != (x, y) or a["owner"] == name:
                continue
            if self.war_between(name, a["owner"]):
                out.append(a)
        return out


    def _blind_cost(self, name: str, armies: list[dict], x: int, y: int) -> None:
        """向**视野外**的目标格下令、撞上目标格上的规则墙（中立领土/敌境空格/
        敌军驻守/第三方打野）→ 参令军队本回合移动额度**照样烧掉**。
        报错本身如实返回——那就是斥候带回的情报，侦察付了钱就要拿得到货；
        要付的是钱：对看不见的地方滥发命令，每一发都值一支军队一回合的腿。
        视野内撞墙不额外罚（你看得见，试错是正常决策，报错免费）。"""
        if self.visible_to(name, x, y):
            return
        for a in armies:
            a["moved_turn"] = self.turn


    @military_receipt
    def move(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        self.runtime.require_tool(self, name, 'move')
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        if a.get("engaged"):
            return False, f"{a['name']} 交战中，先 retreat 撤出"
        # 交战地中的军队（含防守方）不能直接 mv 撤离——撤出走 retreat（回合末随战斗结算后脱离）
        if any(d["owner"] != a["owner"] and d["owner"] != "野人" and d.get("engaged")
               and (d["x"], d["y"]) == (a["x"], a["y"]) and self.war_between(a["owner"], d["owner"])
               for d in self.troops):        # 谓词排除野人 ⇒ 只扫国家军队
            return False, f"{a['name']} 所在格正在交战，不能直接 mv 撤离；撤出请用 retreat（回合末随战斗结算后脱离）"
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        if not a.can_act(self.turn):
            # 教学式话术：说清"它这回合不能再动"且**下一步该做什么**（别让它反复试同一支军）
            return False, (f"{a['name']} 本回合已移动过（每军每回合只挪一次），这回合不能再动；"
                           f"换一支还能动的军，或等结算后下回合——名单见 query panel=army 的「可行动」")
        # ★ 目标格本身的墙先判（与旧口径一致：视野外撞墙 → 报错照给、**额度照烧**，侦察要付钱）。
        #   然后才是"走不到"（地形代价/中途被挡）——那一条**不烧额度**（旧版的射程不够也不罚）。
        why = self._mv_wall(name, x, y)
        if why:
            self._blind_cost(name, [a], x, y)
            # 城堡公开（2026-09-19）：这里之所以只在**调用点**补、不写进 `_mv_wall`，
            # 是因为 `_mv_wall` 在 `_reachable` 的逐格 BFS 热路径上——往里加
            # `visible_to`（O(全表)）会把寻路拖垮。
            _cl = self.visible_buildings(name, x, y).get("城堡", 0)
            return False, why + (f"（该格城L{_cl}）" if _cl else "")
        if (x, y) not in self._reachable(name, a):
            return False, self._unreachable_msg(a, x, y, for_attack=False)
        # mv 只挪位置，不占地——占地走 atk
        a.move_to(x,y,self.turn)
        # ★ 落点是**无主地**时把话说死（用户 2026-10-07，实盘秦）：
        #   `move` 的描述里写着「野地可直接走进/穿过」，`attack` 的描述里写着「格上无任何
        #   军队则直接进驻占领」——**两句长得像、后果相反**。秦 把前者读成了"走进就是占下"，
        #   自称「本回合实证 mv 直接进驻即可」，两回合铺了 30 多次 mv、**国土一块没多**，
        #   小结里却写「魏 48 块无主地已收大部分」。
        #   而回执原样是「军队8 移防 (4,8)」——中性，发十六条也读不出问题。
        #   按项目惯例（教学式回执，见 `_mv_wall` 的 docstring：光说"不行"会让它换个姿势
        #   再撞），在**它形成错误信念的那一刻**把话说死。只加在无主地：自家/盟国格不存在
        #   这个误解，不必白占 token。
        if self.owned_by(x, y) is None:
            return True, (f"军队{a['id']} 移防 ({x+1},{y+1})"
                          "（**未占地**：mv 只挪位置，这一格**还是无主地**；"
                          "要占它得用 atk）")
        return True, f"军队{a['id']} 移防 ({x+1},{y+1})"


    @military_receipt
    def attack(self, name: str, aids: list[int], x: int, y: int) -> tuple[bool, str]:
        """atk = 一次『进军占地』：派军队进目标格——
        有守军(野人/敌国军)就交战（打赢后按索取顺序占地）；格上**无任何军队**才直接进驻占领（空城/无主空地），
        他国领土上有非敌军队（中立/第三方）则不能进驻；**野地例外**——和平驻守的第三方不参战、不占地，
        清场后直接进驻（驻守者回合末被遣返）。野地上有「非敌非盟」的一方正在打野时不能插足。
        多势力同时开战各打各的敌人。mv 只挪位置不占地；占地一律走 atk，没有特例。"""
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        targets = [a for a in self.armies if a["owner"] == name and a["id"] in aids]
        if not aids or len(set(aids)) != len(aids) or len(targets) != len(aids):
            return False, "军队编号缺失、重复或不属于你；位置未改变，额度未消耗"
        if not targets:
            return False, f"未找到我方军队 {aids}"
        # ★ 目标格上的"墙"与 mv 同规：视野外撞墙 → 报错如实，但突入军队本回合额度照烧。
        owner = self.owned_by(x, y)
        why = None
        if owner is not None and (owner == name or self.allied_between(name, owner)):
            # ★ 教学式（2026-09-20 实测赵 连续 5 次 atk 自家地）：说清"这格本来就是你的"，
            #   给出正确动作，免得它换个编队继续撞。
            here = [a["name"] for a in self.troops
                    if (a["x"], a["y"]) == (x, y) and a["owner"] == name]
            tile = self.tiles.get((x, y)) or {}
            tname = tile.get("name", "?")
            why = (f"({x+1},{y+1})「{tname}」已经是{('你' if owner == name else '你盟国')}的地，"
                   f"**不用进攻**"
                   + (f"——{('、'.join(here))} 已经在格上，算已进驻" if here else "")
                   + "。要让军队过去：**用 mv**（自家/盟国/无主地都能进）；"
                     "想看各军在哪、谁还能动：query panel=army")
        elif owner is not None and not self.war_between(name, owner):
            why = "中立不可攻击他国领土"
        else:
            # 野地上别人正在打野（交战方与你既非敌也非盟）→ 不能插足抢地：
            # 这是「不抢别人的战斗」——中立打野时你不能 atk；盟友/敌人在打野则可以参战
            # （都按索取顺序占地）。想旁观仍可 mv 过去（不参战）。
            busy = [a for a in self.troops
                    if (a["x"], a["y"]) == (x, y) and a.get("engaged") and a["owner"] not in ("野人", name)]
            if owner is None and busy and not any(self.war_between(name, a["owner"]) or self.allied_between(name, a["owner"])
                                                  for a in busy):
                other = busy[0]["owner"]
                why = (f"({x+1},{y+1}) 有 {other}军正在打野（与你非交战也非盟友），"
                       f"不能插足抢地——等这场战斗打完再 atk（想插手就先向 {other} 宣战）；"
                       f"旁观可以 mv 过去（不参战）")
        if why:
            self._blind_cost(name, targets, x, y)   # 视野外撞墙：报错照给，突入军队的移动额度照烧
            return False, why
        defs = self._defs_at(name, x, y)
        if not defs and owner is not None and any(a["owner"] != name and a["hp"] > 0
            and (a["x"], a["y"]) == (x, y) for a in self.armies):
            return False, "目标有非敌驻军；位置未改变，额度未消耗"

        for a in targets:
            if a.get("born_turn") == self.turn:
                return False, "新兵当回合不能行动；位置未改变，额度未消耗"
            if a.get("engaged") and (a["x"], a["y"]) != (x, y):
                return False, (f"{a['name']} 正在交战中，不能离开战场改攻他处；"
                               f"想脱战先 retreat 军队id 目标格（会挨守军一击）")
            # ★ atk 也**逐格**走：隔着山/隔着别人的地界就冲不进去（旧版是 5×5 直取）
            if (a["x"], a["y"]) != (x, y) and (x, y) not in self._reachable(name, a, for_attack=True):
                return False, self._unreachable_msg(a, x, y, for_attack=True)
            if (a["x"], a["y"]) != (x, y) and a.get("moved_turn") == self.turn:
                return False, (f"{a['name']} 本回合已移动过（每军每回合只动一次），不能再冲；"
                               f"换一支「可行动」的军，或等结算——名单见 query panel=army")
        for a in targets:
            if (a["x"], a["y"]) != (x, y):
                a.move_to(x,y,self.turn)
        ids = "、".join(f"{a['name']}" for a in targets)
        if defs:
            for a in targets:
                a["engaged"] = True
                a["engage_seq"] = self._next_engage_seq()  # 野地索取顺序：谁先 atk 谁号小
            who = "野人" if defs[0]["owner"] == "野人" else f"{defs[0]['owner']}军"
            _cl = self.visible_buildings(name, x, y).get("城堡", 0)
            _fort = f"（该格城L{_cl}，守方吃它的减伤）" if _cl else ""
            # ★ 打**视野外**的格是合法的（这里只查规则墙与可达性，没有视野闸），而引擎
            #   照收那格地形/城堡的减伤 —— 但按视野门禁这里报不出城堡。所以必须**明说
            #   "你在盲战"**：盲战的代价是真的（该吃多少减伤吃多少），缺的只是知情。
            #   不说的话，AI 会以为自己这一拳没吃到任何加成，正好把账算反。
            _blind = "" if self.visible_to(name, x, y) else \
                "（**盲战**：该格不在你视野内，守军构成与减伤均不可知，结算照常进行）"
            return True, (f"{ids} 冲入 ({x+1},{y+1}) 与{who}交战{_fort}{_blind}，"
                          "已进入交战、尚未占领；之后每回合结算一轮；可 retreat 撤出")
        # 格上还有其他军队但不是你的敌人（中立/盟友，和平驻守）：
        #  · 他国领土 → 不能进驻（旧规：中立地不进门）；
        #  · 野地（无主）→ 和平驻守不产生任何权利、也不构成障碍：atk 只打野人/敌人，
        #    清场后直接进驻占地，驻守的第三方被挤走（回合末自动遣返）。
        squatters = sorted({a["owner"] for a in self.armies
                            if a["owner"] != name and a["hp"] > 0 and (a["x"], a["y"]) == (x, y)})
        if squatters and owner is not None:
            return False, (f"({x+1},{y+1}) 有他国军队但并非你的敌人（中立/第三方），"
                           f"不能直接进驻；只能攻击敌人或占领无任何守军的空地")
        # 格上无守军/敌人 → atk 进驻即占
        _ok, cmsg = self._conquer(x, y, name, "进驻占领", log_it=False)
        nm2 = self.tiles[(x, y)]["name"]
        self.log(f"{name} {ids} 进驻 ({x+1},{y+1})，{cmsg}", phase="领土", nation=name, x=x, y=y,
                 parties=[owner] if owner and owner != name else None,   # 同上：失主必看
                 lost_by=owner if owner and owner != name else None)
        note = (f"（{'、'.join(squatters)}军未参战，回合末自动遣返）" if squatters else "")
        return True, f"{ids} 进驻 ({x+1},{y+1})，敌人为 0，{cmsg}{note}"


    def _retreat_legal(self, name: str, x: int, y: int) -> bool:
        """撤退合法点：无人荒地 / 己方领土 / 同盟领土（中立与敌国格都不行）。
        ★ 无人荒地上有敌（交战中）军队驻守 → 也不行——与 mv 的"野地有敌军→只能 atk"同口径，
        否则能以"撤退"名义免费将军队空投进敌脚（同格不交战，下回合被围歼）。
        己方/同盟格上有混战敌军仍放行（那是增援，mv 同样允许）。"""
        o = self.owned_by(x, y)
        if o == name:
            return True
        if o is not None:
            return self.allied_between(name, o)
        return not any(a["owner"] != name and a["owner"] != "野人"
                       and (a["x"], a["y"]) == (x, y) and self.war_between(name, a["owner"])
                       for a in self.troops)


    @military_receipt
    def retreat(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        """撤出：与 mv/atk 同一个『每回合一次移动』额度。
        交战中的军队（含防守方守军）都能用；撤退固定只能退相邻 1 格（3×3，所有人，不按兵种速度）；
        目标限 己方/同盟/无人荒地（中立与敌国格都不行）；四周没有合法撤退点则不能撤退。
        撤退不立刻结算：军队留在战场参与本回合末的战斗结算（伤害全场分摊；防御方撤退减伤
        RETREAT_DEF_COVER%），结算后自动脱离到目标格——避免『每撤一支各吃一次全额』的灾难。"""
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        # ★ 攻守角色一律读唯一口径 `battle_sides`（从前这里与 `_resolve_battles` 各写一遍，
        #   一处排除 hp≤0、一处不排除，会静默漂开）。
        sides = self.battle_sides(a["x"], a["y"])
        # 交战判定：①**本军自己**挂着 engaged（逐军语义——故意不用 "name ∈ attacker"，
        #   那会新开能力：同格我军 X 军 engaged、Y 军没 engaged 时，今天 Y 撤不了，换掉就能撤）；
        #   ②本军所在格上我方是**挨打方**（有人打我）。
        in_battle = bool(a.get("engaged")) or bool(sides and name in sides["defending"])
        if not in_battle:
            return False, f"{a['name']} 未在交战中，无需撤退"
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        if (a["x"], a["y"]) == (x, y):
            return False, "撤出需选一个不同的格"
        if max(abs(a["x"] - x), abs(a["y"] - y)) > RETREAT_RANGE:
            return False, "撤退固定只能退相邻 1 格（3×3），超出范围"
        # 没有合法撤退点则不能撤退（目标限 己方/同盟/无人荒地）
        if not any(self._retreat_legal(name, nx, ny)
                   for nx, ny in self.neighbors(a["x"], a["y"])):
            return False, "四周没有合法撤退点（己方/同盟/无人荒地），无法撤退"
        if a.get("moved_turn") == self.turn:
            return False, f"{a['name']} 本回合已移动/进攻过，移动额度用尽，撤不出（下回合再撤）"
        if not self._retreat_legal(name, x, y):
            return False, "不能撤到敌国或中立国的格子；只能撤向 己方/同盟/无人荒地"
        # 撤退不立刻结算：标记 retreat_to 留在原地，本回合结束时随战斗结算走正常战斗机制
        # （敌方伤害全场分摊，撤退者在场照常吃自己那份；防御方撤退减伤 RETREAT_DEF_COVER%），
        # 结算后自动脱离到目标格（见 resolve_turn 撤退落地）。
        # 防御方减伤：我方在该格「未参战」（不是进攻方）、或本身就是格主 → 守方，撤退减伤
        # RETREAT_DEF_COVER%（谁挨打谁是守方，野地和平驻军同理）；主动进攻方撤退是全额。
        # ★ 资格读 `battle_sides` 的 `soak_elig`——与结算真正用的减伤**同一个来源**。
        defends = bool(sides and sides["soak_elig"].get(name))
        cover = RETREAT_DEF_COVER if defends else 100
        a["retreat_to"] = [x, y]
        a["retreat_cover"] = cover
        # `retreat_role` 仅供**展示**（日志/面板/摘要）——结算只认 `retreat_cover`。
        # 不 derive-from-cover：`RETREAT_DEF_COVER` 是可调数值，若被调成 100，
        # 「cover<100 ⇒ 防御」当场反标签；布尔语义免疫调参。
        a["retreat_role"] = "守" if defends else "攻"
        a["moved_turn"] = self.turn
        return True, (f"{a['name']} 已提交撤离至 ({x+1},{y+1})（{retreat_note(defends, cover)}）："
                      f"本回合结束时随战斗结算（全场分摊）后自动脱离；结算期间仍在战场")


    def disband(self, name: str, aids: list[int]) -> tuple[bool, str]:
        """遣散：把本国军队**就地解散**（解甲归田）——军队从表里消失，此后再不吃补给、不计军费。

        ★ 三条口径（用户 2026-10-09 拍板）：

          · **不返还**——人、装备、粮一律不退。这不是抠门，是**评分口径**逼出来的：
            排名看「总消费 = 建造 + 征兵 + 军费」，而 `_spend` 只有 `+=`、`_mval` 对负数
            直接返回 0 ⇒ 引擎里**没有任何负向记账入口**。一旦返还装备，就凭空多出一条
            「征兵（记一笔消费）→ 遣散（拿回装备）→ 卖装备（回本）」的零成本闭环，
            把黄金直接换成消费分。不返还则无新增刷分路径。
          · **任意位置**——境内/境外/野地都行。它没有目标格，所以**不用** `_check`
            （坐标校验）/`_reachable`（可达性）/`_blind_cost`（对视野外滥发命令的罚）——
            那三个都以坐标为输入，套用会得出错误语义。孤军深陷敌境时的止损出口，
            代价是整支军队当场没了。
          · **交战中不得遣散**（含被攻击的守方）——这是**硬闸，不是凑口径**：
            `battle_sides` 返回 None ⟺ 该格没有活着的进攻方，所以遣散 `engaged` 军会让
            **整场战斗凭空消失**（比 retreat 更强的免费脱战）；且「弃城即陷」只遍历本回合的
            `war_lines`，遣散最后一个守方会让格子当回合**不被改旗**——即「遣散守军」比
            「守军阵亡」更保地。判据逐字复用 `retreat` 的唯一口径（攻方按逐军 `engaged`、
            守方按逐国 `defending`，那处刻意的不对称别"顺手统一"）。

        ★ 不做的事（与「阵亡/饿毙/亡国清场」那三处删除一致，见 `troops` 文档）：
          不回退 `next_army_seq`（番号口径是**阵亡不回收**，回收会与 AI 已引用的 `#n` 撞号）、
          不记 `_spend`/`flow_out` 流水、不动 `engaged`/`retreat_to`/`moved_turn`。
          亡国只认市政厅 ⇒ 遣散光军队**不亡国**。
        """
        if not aids:
            return False, "没给军队编号。想看自己有哪些军：query panel=army"
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        # `self.troops` 天然不含野人（野人走 `guardians`）⇒ 野人番号进不了 targets，
        # 「野人不会被遣散」不需要额外判一次。
        targets = [a for a in self.troops if a["owner"] == name and a["id"] in aids]
        if not targets:
            return False, (f"未找到我方军队 {aids}（番号各国独立从 1 编号、且**阵亡不回收**，"
                           f"以 query panel=army 为准）")
        # 先全量校验、再统一动手（同 attack 的两段式）：免得"一半遣散了、一半被拒"的半截状态。
        for a in targets:
            sides = self.battle_sides(a["x"], a["y"])
            if a.get("engaged") or (sides and name in sides["defending"]):
                return False, (f"{a['name']} 正深陷交战（{a['x']+1},{a['y']+1}），不能就地遣散——"
                               f"交战中不许处置军队（遣散会让这场战斗凭空消失）。"
                               f"先用 retreat 撤出战场，下回合再遣散")
        for a in targets:
            self.armies.remove(a)   # ★ 删 self.armies：`self.troops` 是派生**缓存列表**（见其文档）
            self.log(f"⚑ 遣散 {a['name']}（{a['x']+1},{a['y']+1}）：解甲归田，"
                     f"人甲不返还，本回合起不再吃补给、不计军费", phase="内政", nation=name)
        names = "、".join(a["name"] for a in targets)
        return True, (f"已遣散 {len(targets)} 支：{names}——**不返还**任何粮/装备/黄金；"
                      f"本回合起即不再吃补给、不计军费（民兵的编制名额也当场释放）。"
                      f"遣散不亡国，但军队没了就是没了，番号不回收")


    def _die(self):
        d = self.rng.randint(min(COMBAT_DIE_MOD), max(COMBAT_DIE_MOD))
        return d, COMBAT_DIE_MOD[d]


    @staticmethod
    def _combat_power(atk_total: int, def_pct: int) -> int:
        """atk_total = 该方各军兵种攻击之和（步/骑 50、民兵 25，见 unit_atk）。"""
        return max(1, atk_total * (100 - def_pct) // 100)


    @staticmethod
    def _round_damage(power: int, mod: int) -> int:
        return max(1, power * (100 + mod) // 100)


    @staticmethod
    def _spread(dmg: int, units: list[dict]):
        """把伤害**均摊**到这一方的各军头上；**有军被打死时，多出来的那部分转嫁给还活着的**
        ——不浪费在尸体上（用户 2026-10-11：「打架伤害虽然均摊，但是有人提前死了，
        例如造成 100，对面两个兵，一个 20 一个 90，应该结算为 0、10」）。

        旧写法 `hp -= per` 会让 20 兵吃 50（多吞 30）⇒ 90 兵只掉到 40，伤害**凭空少了三成**；
        现在按 20 兵吃掉 20、剩下的 80 全砸在 90 兵头上 ⇒ 结算 0、10（总血 110→10）。

        做法＝一轮轮均摊：谁这一轮会被打死就先结算它（只扣到 0），它吃不下的留到下一轮，
        由**还活着的**重新均摊。没有军被打死时一轮就分完，与旧行为逐字相同。
        """
        left = int(dmg)
        rest = list(units)
        while left > 0 and rest:
            per, rem = divmod(left, len(rest))
            alive: list[dict] = []
            for i, u in enumerate(rest):
                take = per + (1 if i < rem else 0)
                if take >= u["hp"]:
                    left -= max(0, u["hp"])
                    u["hp"] = 0
                else:
                    u["hp"] -= take
                    left -= take
                    alive.append(u)
            rest = alive


    @staticmethod
    def faction_atk(units: list[dict]) -> int:
        """该方**本回合输出基数** = Σ 各军兵种攻击（`unit_atk`，**与 hp 无关**）。

        撤退中的军输出 −`RETREAT_ATK_PENALTY`%（撤离途中无心恋战），下限 1。

        ★ 这是**基数**，不含骰子修正——掷完骰子那一步是 `_round_damage`。
          面板报"总战力"时必须按它报，并把标签写成「输出基数」，
          否则 AI 会把基数读成"这一轮会打出的伤害"（那还要乘骰子 ±25%）。

        ★ 结算与面板的**唯一出处**：别在面板里重抄这个 `sum`。
        """
        return sum(unit_atk(a) if not a.get("retreat_to")
                   else max(1, unit_atk(a) * (100 - RETREAT_ATK_PENALTY) // 100)
                   for a in units)


    def defense_breakdown(self, x: int, y: int, def_owner: str | None) -> tuple[int, int, int]:
        """该格对 `def_owner` 的防御**三分量**：`(地形%, 城堡%, 合计%)`。

        合计 = 两者**相乘**叠加（`100 - ((100-td)*(100-cd))//100`），不是相加——
        所以「沙漠 −10% × 城堡 L2 +20%」的合计是 12% 而不是 10%，可以小于任一分量。
        负的地形防御（沙漠 −10%）**如实返回**，别夹到 0：那是"守方反而多挨打"。

        城堡分量只在**该格归属就是防御方**时才计（`t["owner"] == def_owner`）——
        非格主的守军（同格第三方守军）只吃地形。

        ★ 结算与面板的**唯一出处**：`_defense_pct` 就是它的 `[2]`；面板要摊开三分量时读它，
        不许自己重算（那会让"面板报的减伤"与"结算真吃的减伤"悄悄漂开）。
        """
        t = self.tiles.get((x, y))
        terrain = t["terrain"] if t else self.tile_terrain(x, y)
        castle = t["buildings"]["城堡"] if (t and t["owner"] == def_owner) else 0
        td = TERRAIN_STATS[terrain]["defense"]
        cd = castle * building_effect("城堡", "defense_per_level")
        return td, cd, 100 - ((100 - td) * (100 - cd)) // 100


    def _defense_pct(self, x: int, y: int, def_owner: str | None) -> int:
        """地块总防御% = 地形与城堡**相乘**叠加。见 `defense_breakdown`（本函数＝它的 `[2]`）。"""
        return self.defense_breakdown(x, y, def_owner)[2]


    @staticmethod
    def _modtxt(mods: dict[str, int]) -> str:
        """每方骰修正的简短文本：如「甲+5% 乙-15%」。"""
        return " ".join(f"{F}{m:+d}%" for F, m in sorted(mods.items())) or "—"


    def battle_sides(self, x: int, y: int) -> dict | None:
        """(x,y) 这一格**当前这一仗**的完整切分 —— 攻守/减伤/输出的**唯一出处**。

        返回 `None` ⟺ 该格没有待结算的战斗（无活军，或没有任何**活着的**进攻方）。

        非 None 时给：
          owner       格主（None = 无主野地）
          forces      {势力: [该格活军…]}   列表序 = `self.armies` 序；含野人、含在野第三方
          attacker    {势力}                有 engaged 军的势力（野人永不在内）
          defending   {势力}                挨打方 = ⋃ 各攻方的敌人
          role        {势力: "攻"|"守"|"旁观"}   旁观 = 在场但既不攻也不挨打
          enemies     {势力: [与之交战的在场势力…]}
          soak_elig   {势力: bool}          **结算减伤资格**：F ∉ attacker 或 F == owner
          soak_pct    {势力: int}           结算**真正用的**数（不符合资格者恒 0）
          soak_parts  {势力: (地形%, 城堡%, 合计%)}  与资格无关的引擎真值
          atk_base    {势力: int}           本回合输出基数（Σ `unit_atk`，撤退军 −80%）

        ★ `forces` 里装的是**军队对象本身**（不是拷贝）——`_spread` 要就地改 hp。

        ★ 这里是「谁是进攻方」的**唯一**判定处，`_resolve_battles` 与 `retreat()` 都读它。
          从前这两处各写一遍（一处排除 `hp <= 0`、一处不排除），对"格上还有没有活着的
          进攻军"可能给出相反答案，而症状是**静默**的：结算按攻方算、撤退却按守方给
          50% 减伤。统一到 `hp > 0`——0 血军是尸体，既不计入输出、也不参与分摊，
          更不该替本方要来"防御撤退"的减伤。
        """
        owner = self.owned_by(x, y)
        forces: dict[str, list[dict]] = {}
        for a in self.armies:
            if (a["x"], a["y"]) != (x, y) or a["hp"] <= 0:
                continue
            if a["owner"] == "野人" and owner is not None:
                continue                      # 野人只守无主格
            forces.setdefault(a["owner"], []).append(a)
        attacker = {F for F in forces if F != "野人"
                    and any(a.get("engaged") for a in forces[F])}
        if not forces or not attacker:
            return None

        def _enemies(F: str) -> list[str]:
            """敌人关系：非野人 = 格上其他交战方 +（自己是进攻方时）无主格野人；野人 = 只打进攻方。"""
            if F == "野人":
                return [G for G in attacker if G != "野人"]
            en = [G for G in forces if G != F and G != "野人" and self.war_between(F, G)]
            if F in attacker and "野人" in forces:
                en.append("野人")
            return en

        enemies = {F: _enemies(F) for F in forces}
        defending = {G for F in attacker for G in enemies[F]}
        # 减伤：未参战的驻军（含野人）挨打时吃本格地形，格主永远算守方
        # （攻击方不吃加成——谁挨打谁是守方）。
        soak_parts = {F: self.defense_breakdown(x, y, F) for F in forces}
        soak_elig = {F: (F not in attacker or F == owner) for F in forces}
        return {
            "x": x, "y": y, "owner": owner,
            "forces": forces, "attacker": attacker, "defending": defending,
            "role": {F: ("攻" if F in attacker else "守" if F in defending else "旁观")
                     for F in forces},
            "enemies": enemies, "soak_elig": soak_elig,
            "soak_pct": {F: (soak_parts[F][2] if soak_elig[F] else 0) for F in forces},
            "soak_parts": soak_parts,
            "atk_base": {F: self.faction_atk(forces[F]) for F in forces},
        }


    def _resolve_battles(self) -> list[str]:
        """每格每回合的多势力交战结算：**进攻方不纯联合**——每方独立只打自己的敌人（互相宣战才互打），
        每方掷自己的骰；野人只守无主格、只打"进攻方"（不打扰和平停驻者）；地形/城堡减伤只给格主/野人；
        同格多方同时出手再统一结算阵亡；占地 = 唯一幸存且野人已清的势力。"""
        lines: list[tuple[int, int, str]] = []  # (x, y, 战报行)——带坐标才进得了事件视野
        engaged = [a for a in self.armies if a.get("engaged") and a["owner"] != "野人"]
        for (x, y) in sorted({(a["x"], a["y"]) for a in engaged}):
            # 格子标记带上**城堡等级**（2026-09-19：城堡公开——凡是报"哪格在打"的地方都带上它，
            # 否则守方靠城减伤，攻方却看不出为什么打不动）。tag 被本格所有战报行复用，
            # 所以这一处改动就让每条战报都带上。
            _t_castle = self.tiles.get((x, y))
            _cl = _t_castle["buildings"].get("城堡", 0) if _t_castle else 0
            tag = f"({x+1},{y+1}){self.ter_char(x, y)}" + (f" 城L{_cl}" if _cl else "")
            # ★ 攻守/减伤/输出全部来自唯一口径 `battle_sides`（见其 docstring）
            sides = self.battle_sides(x, y)
            if sides is None:
                continue
            owner = sides["owner"]
            forces = sides["forces"]
            attacker = sides["attacker"]
            enemies = sides["enemies"]
            soak = sides["soak_pct"]
            # 每方掷自己的骰，同时出手（先算全部伤害再统一施加，允许同归于尽）
            dmg: dict[str, int] = {F: 0 for F in forces}
            mods: dict[str, int] = {}
            for F in forces:
                en = enemies[F]
                if not en:
                    continue
                _d, mod = self._die()
                mods[F] = mod
                atk = self.faction_atk(forces[F])   # 撤退中的军 −80% 的输出折扣在这一处
                power = self._round_damage(self._combat_power(atk, 0), mod)
                share = power / len(en)  # 均分给各敌人（腹背受敌则兵力分散）
                for G in en:
                    dmg[G] += max(1, round(share * (100 - soak[G]) / 100))
            for F in forces:
                if not dmg[F]:
                    continue
                units = forces[F]
                ws = [a.get("retreat_cover", 100) for a in units]
                if all(w == 100 for w in ws):
                    self._spread(dmg[F], units)
                else:
                    # 有人带撤退减伤：先按常规全场分摊算出每人该吃多少，再按 cover% 打折——
                    # 防御方撤退 cover=50 就只吃一半（单支守军撤退也真的减半），
                    # 少掉的那部分不再转嫁给同格友军。
                    per, rem = divmod(dmg[F], len(units))
                    for i, (a, w) in enumerate(zip(units, ws)):
                        share = per + (1 if i < rem else 0)
                        a.take_damage(share * w // 100)
            for a in list(self.armies):
                if (a["x"], a["y"]) == (x, y) and a["hp"] <= 0:
                    self.armies.remove(a)
            alive = {}
            for F, fs in forces.items():
                fs2 = [a for a in fs if a["hp"] > 0]
                if fs2:
                    alive[F] = fs2
            survivors = [F for F in alive if F != "野人"]
            # 清 engaged：该方在格上已无活敌人 → 脱离战斗
            for F in alive:
                if F == "野人":
                    continue
                if not enemies[F] or not any(G in alive for G in enemies[F]):
                    for a in alive[F]:
                        a.disengage()
            # 占地 / 战报：归属候选 = 进攻方中「无活敌」者，按索取顺序取最早 atk 的（索取者优先；
            # 索取者阵亡则顺位最早入场的同盟者）。野地与敌国同规。
            win = self._claim_winner(alive, attackers=attacker)
            desc_alive = "；".join(f"{F} 余{len(alive[F])}支[{alive[F][0]['hp']}hp]" for F in survivors)
            if owner is None:
                # ---- 野地（无主）----
                # 野人清空 + 有归属候选 → 占地；和平驻守的第三方不占地也不参战（回合末被遣返）。
                if "野人" in alive:
                    if win is not None:
                        fs = alive[win]
                        lines.append((x, y, f"⚔ {win} 仍与野人交战 @{tag}"
                                     f"（余{len(fs)}支[{fs[0]['hp']}hp]，守军未清，占不得）"))
                    else:
                        lines.append((x, y, f"⚔ 攻方全灭 @{tag}，野人仍在（无主地守军未清）"))
                elif win is not None:
                    fs = alive[win]
                    info = f"{win} 余{len(fs)}支[{fs[0]['hp']}hp]"
                    _ok, msg = self._conquer(x, y, win, "进驻")
                    others = [F for F in survivors if F != win and F in attacker]
                    joint = (f"（共占，按索取顺序归 {win}；同场 {'、'.join(others)}）" if others else "")
                    sq = sorted(F for F in survivors if F not in attacker)
                    squeeze = (f"（{'、'.join(sq)}军未参战，回合末自动遣返）" if sq else "")
                    lines.append((x, y, f"⚔ 全歼守军 @{tag}，{msg}（{info}）{joint}{squeeze}"))
                elif any(F in attacker for F in survivors):
                    lines.append((x, y, f"⚔ 多方混战 @{tag}（骰 {self._modtxt(mods)}）：{desc_alive}（战局未定）"))
                elif survivors:
                    lines.append((x, y, f"⚔ 野人已清 @{tag}，但场上只剩未参战的驻军，野地维持无主"))
                else:
                    lines.append((x, y, f"⚔ 同归于尽 @{tag}——此地成无主空地，可直接占领"))
            else:
                # ---- 他国领土：同一套索取顺序（多国共同围攻时，第一个 atk 者优先接管）----
                if win is not None and win != owner:
                    fs = alive[win]
                    info = f"{win} 余{len(fs)}支[{fs[0]['hp']}hp]"
                    ok, msg = self._conquer(x, y, win, "攻陷")
                    lines.append((x, y, f"⚔ 全歼守军 @{tag}，{msg}（{info}）"))
                elif owner in alive:
                    fs = alive[owner]
                    info = f"{owner} 余{len(fs)}支[{fs[0]['hp']}hp]"
                    if any(F in attacker for F in survivors):
                        lines.append((x, y, f"⚔ 守军坚守 @{tag}：{info}，攻方未退"
                                     f"（骰 {self._modtxt(mods)}）"))
                    else:
                        dead = sum(len(v) for F, v in forces.items() if F != owner)
                        lines.append((x, y, f"⚔ 守军坚守 @{tag}：攻方{dead}支全灭，{info}"
                                     f"（骰 {self._modtxt(mods)}）"))
                elif survivors:
                    lines.append((x, y, f"⚔ 多方混战 @{tag}（骰 {self._modtxt(mods)}）：{desc_alive}（战局未定）"))
                else:
                    lines.append((x, y, f"⚔ 同归于尽 @{tag}——城仍在敌手"))
        return lines


    def _conquer(self, x: int, y: int, by: str, how: str, *, log_it: bool = True) -> tuple[bool, str]:
        self._check(x, y)
        old = self.owned_by(x, y)
        self.guard_once.discard((x, y))
        self._drop_guardians(x, y)
        t = self.tiles.get((x, y))
        if t is None:
            t = self._new_tile(x, y, by)
            self.tiles[(x, y)] = t
            msg = f"{by} {how}拓疆「{t['name']}」({x+1},{y+1}){t['terrain']}"
        elif old is None:
            # ★ **无主故土**（前朝废墟，用户 2026-09-21 口径「剩余领土变成空地，建筑保留，
            #   但是无主」）：格子在、名字在、建筑也在，只是没有主人。
            #   ⇒ **复用这一格，不许 `_new_tile` 重造**——重造会把地名/建筑/资源全洗掉，
            #   等于"建筑保留"这条口径在这个入口上被静默吃掉（`old is None` 曾经只可能
            #   意味着"从没物化过"，现在多了一种）。
            #   无主 ⇔ 没有失主：没有 `lost_by`，也没有 `_return_core`（旧核心主张随亡国作废）。
            # ★★ 但**核心要写**（2026-10-07 修）：这一支原本只改 owner、不写 `core`，于是
            #   同样的"占无主地"在**两条路上给出两种结果**——上面 `t is None` 那条走
            #   `_new_tile`（里面写着 `"core": owner`）⇒ 有核心；这条 ⇒ `core` 留着 `None`。
            #   而 `tiles` 是**惰性物化**的（面板/地图/视野查一次就建）⇒ **谁看过那格**
            #   会改变它将来有没有 core —— 同 seed 两局可能对不上，且静默。
            #   口径对齐开局（「每格 core = owner」）与 `_new_tile`：**无主 ⇒ 没有活着的
            #   失主 ⇒ 核心归占领者**。用途上是必要的：core 唯一的消费者是 `_return_core`
            #   （同联盟同战线的盟友占了你的核心地就归还）；没 core 的地永远不归还。
            #   ⚠ 与"不调 `_return_core`"不冲突：那说的是**没有失主可还**，不是"不记归属"。
            #   ⚠ 占**活着的**他国的地仍不动 core（下面那支）——留给战后 `_snapshot_cores`
            #     按实占重算，那是现有设计。
            t["owner"] = by
            t["core"] = by
            msg = f"{by} {how}「{t['name']}」({x+1},{y+1}){t['terrain']}（无主之地）"
            _loot = " ".join(f"{b}×{n}" for b, n in t["buildings"].items() if n)
            if _loot:
                # 无主的建筑不涉机密（无主＝没有国家需要护着的内政底细）⇒ 可以照报
                msg += f"，得 {_loot}"
        elif old != by:
            t["owner"] = by
            # ★ **地名在前、被动语态**（用户 2026-09-20：「应该显示最近消息，地名(坐标)被x攻占」）：
            #   一条纪事同时喂养三方（攻方、失主、视野内的第三方），所以取**事件视角**——
            #   "「绥湾」(12,20) 被 秦 攻陷" 谁读都对；原先写"秦 攻占「绥湾」"是攻方视角，
            #   失主从自己的近讯里**认不出是自己丢了地**（用户报的「丢地不报消息」）。
            #   末尾点名原属国：失主据此一眼确认"这是我的地"。
            #   ★ 动词只认"攻陷"这一种**真打下来**的说法，其余（`进驻`/`进驻占领`/
            #   `守军尽撤`）一律中性"占领"——`how` 不全是动词，直接套被动会读成
            #   "被秦守军尽撤"；那些是**原因**，同一回合的动作纪事里就写着。
            # 城堡**公开**（2026-09-19）：占领后建筑原样保留，所以要报出缴获了什么要塞。
            # ★ 这条日志是**视野广播**的（同格谁看得见谁就收到），所以只能带公开信息——
            #   城堡可以；兵营/工厂/农田那些若报出去，等于向第三者泄露被占国的内政底细。
            # ★ 市政厅（2026-09-21 起）与城堡同级公开：它是**国祚**（拔光即亡国），
            #   攻城方必须看得出"这一刀砍在国祚上"——否则"我为什么把它打死了"无从知晓
            #   （亡国广播只说结果，不说这一格的价值）。兵营/工厂等内政底细仍照旧瞒着。
            _cl = t["buildings"].get("城堡", 0)
            _th = t["buildings"].get("市政厅", 0)
            _pub = "，".join(x for x in (f"原属 {old}", f"城L{_cl}" if _cl else "",
                                         f"市政厅×{_th}" if _th else "") if x)
            msg = f"「{t['name']}」({x+1},{y+1}) 被 {by} " \
                  f"{'攻陷' if how == '攻陷' else '占领'}（{_pub}）"
            extra = self._return_core(x, y, by)  # 同战线盟友核心领土 → 自动归还
            if extra:
                t = self.tiles[(x, y)]
                msg += extra
        else:
            return False, "已是自己领土"
        if log_it:
            # ★ `parties=[old]`：失主**必看**——按视野快照它可能已经看不到这格了
            #   （那块地是飞地、或失去它之后四周再无自家地），于是"丢了地"这件事
            #   在它的近讯里彻底不存在（用户 2026-09-20 报的第二个洞的极端情形）。
            self.log(msg, phase="领土", nation=by, x=x, y=y,
                     parties=[old] if old and old != by else None,
                     lost_by=old if old and old != by else None)
        # ★ 亡国**不在这里当场判**（2026-10-09 用户拍板「统一到回合末」）：这里只把地易主，
        #   亡国与"全天下强制休战"统一由 `resolve_turn` 收尾的 `_settle_deaths()` 结算。
        #   旧口径是在命令阶段当场亡国——空厅被 `atk` 进驻的那一刻就除名、全世界战线立刻冻结、
        #   余土立刻变无主。后果是**同一个回合里世界被半路改写**：AI 是工具循环、一条条下令的，
        #   它刚拔掉一座空厅，同回合计划好的下一刀就变成"中立不可攻击他国领土"了
        #   （实测复现：秦拔楚最后一座空厅 ⇒ 秦↔齐 的战线也当场消失 ⇒ 秦接着打齐被拒）。
        #   而且"空厅当场 / 有守军等回合末"是不该有的不对称：同一件事落在两个时刻。
        return True, msg


    def has_townhall(self, name: str) -> bool:
        """本国是否还有**已落成**的市政厅 —— 这是**国祚**（用户 2026-09-21 口径）。
        在建的不算：`pending` 里的那笔还没盖起来（同"产出只认已落成"的口径）。"""
        return any(t["owner"] == name and t["buildings"].get("市政厅", 0) > 0
                   for t in self.tiles.values())


    def _settle_deaths(self) -> list[str]:
        """**回合末统一**判定亡国：厅尽失的国家在此除名（含全世界强制休战、余土变无主）。

        ★ 时序口径（用户 2026-10-09 拍板「统一到回合末」，回答"全球强制和平的结算插在
          回合末还是回合中"）：`_conquer` 只负责地易主，**不**当场判亡国——旧的当场判定会让
          同一个回合里世界被半路改写（AI 是工具循环、逐条下令，它刚拔掉一座空厅，
          同回合计划好的下一刀就"中立不可攻击他国领土"了），而且造成"空厅当场 /
          有守军等回合末"的不对称。**两条路现在都在这里收口**：
          空厅由命令阶段的 `atk` 进驻、有守军的由 `_resolve_battles` 在回合末拔掉。
        返回本回合灭掉的国家名（按 `order` 序；供日志与测试用）。
        """
        dead = [n for n in self.order if n in self.nations and not self.has_townhall(n)]
        for n in dead:
            self._eliminate_if_dead(n)
        return dead


    def _eliminate_if_dead(self, name: str) -> bool:
        """**亡国条件＝一座市政厅都不剩**（用户 2026-09-21 改版；旧口径是"领土尽失"）。

        ★ 现在**只由 `_settle_deaths()` 在回合末调用**（2026-10-09 用户拍板「统一到回合末」）——
          命令阶段拔掉最后一座厅只会让地易主，亡国本身等回合末统一判。别在别处直接调它。

        领土丢光自然也没有市政厅（市政厅盖在地上），所以旧口径是这一条的特例；
        但反过来不成立：**还握着大片土地、市政厅却被拔光的国家照样亡国**——
        这时它的领土**不归任何人**：变成**无主空地**，地上的建筑（含市政厅/城堡/兵营）
        **原样留在原地**，等着谁进驻谁得（`_conquer` 的 `old is None` 分支复用该格）。
        """
        if name not in self.nations:
            return False
        if self.has_townhall(name):
            return False
        # ★ **战斗也要当场停**（用户 2026-10-07：「正在发生的战争必须强制停下」「包括战斗」）：
        #   战线马上要没了，场上不再有"进攻方"这个身份。不清 `engaged` 的话，滞留在原敌方
        #   格上的军队下一回合会被 `_resolve_battles` 判成"唯一幸存者"（没敌人了）⇒
        #   `_conquer("攻陷")` —— **白占一格、顺手清掉守军**（实测复现过：3 秦军 vs 2 韩军
        #   打了一回合，第三国亡国停战，再结算一回合 ⇒ 地归秦、守军消失）。
        #   口径与议和一致（`_do_accept_peace` 那句「各方军队解除交战」）；之后它们是
        #   **非法滞留**，由 `_withdraw_illegal` 按兵种速度遣返回国。
        #   ⚠ **必须跑在"余土变无主"之前**：那一步一执行，亡国者的地块 `owner` 就成了 None，
        #     站在上面的军队会被下面那条"只清国有地"的过滤器漏掉，带着 `engaged` 留在无主地
        #     上——洞还是那个洞（顺序反了就是同一个 bug 换个地方复发，有用例钉着）。
        #   ⚠ 只清**站在国有地上**的：打野人的仗不是"战争"（野人只守无主格），
        #     全天下休战不该把清野地一并掐掉。
        for a in self.armies:
            if a["owner"] != "野人" and self.owned_by(a["x"], a["y"]) is not None:
                a.disengage()
        # 亡国 ⇒ 余土变无主之地（建筑留存）；核心主张随之作废（没有国了，谈何核心）
        orphaned = [t for t in self.tiles.values() if t["owner"] == name]
        for t in orphaned:
            t["owner"] = None
            t["core"] = None
        # ★ 亡国**是公开事实**（`broadcast`）：原先写成 `log(nation=name)`——只有那个
        #   已经不存在、再也不会查询的国家"看得见"，等于谁都不知道（连灭它的那家也不知道，
        #   若它正好在视野外）。同族问题的极端形态（用户 2026-09-20「丢地不报消息」）。
        #   归无主的余土要报出**块数**：那是全世界都能去捡的废墟（有建筑就值钱）。
        tail = (f"余 {len(orphaned)} 块领地沦为无主之地（建筑留在原地，先到先得）"
                if orphaned else "")
        self.broadcast(f"☠ {name} 亡国：市政厅尽失，国祚断绝！{tail}", phase="灭国")
        del self.nations[name]
        # 军队解散
        self.armies = [a for a in self.armies if a["owner"] != name]
        # 关系与外交清场
        # ★★ 2026-10-07 口径（用户拍板）：**「全天下强制休战」字面就是停战**——
        #   一方亡国 ⇒ **现有战线一律当场终止**（不只是与亡国者有关的那几条），
        #   然后给全体现存国家两两压 `FALL_TRUCE_TURNS` 回合（下面的 truce 块）。
        #   语义与 `offer_peace` 完全对齐：**战争结束 + 期限内不得再宣战**。
        #   旧口径只终止与亡国者相关的战线、其余照打，结果是自相矛盾的一幕：
        #   全世界两两"休战至第 N 回合"的同时，别的战线**照打不误**
        #   （实盘 T157 魏/周同日亡国 ⇒ 全天下休战至 167，而韩↔燕 照打、连一次宣战都不用）
        #   ——**"防连环征服"的立意被绕过**：灭国者反而拿到一段"没人能宣战它"的接收窗口，
        #   正在流血的一方却一分钟都没停。
        ended_wars = list(self.wars)
        self.wars = []
        # 因亡国而终结的战争：余方实际持有重算为核心领土
        for w in ended_wars:
            self._snapshot_cores([w["atk"], w["def"]] + list(w["followers"])
                                 + list(w.get("atk_followers", [])))
        others = [w for w in ended_wars if name not in (w["atk"], w["def"])]
        if others:
            names = "、".join(f"{w['atk']}↔{w['def']}" for w in others[:3])
            more = f" 等 {len(others)} 条" if len(others) > 3 else ""
            self.proclaim(f"🕊 **全天下强制休战 {FALL_TRUCE_TURNS} 回合**：{name} 亡国，"
                          f"与它无关的战线也一并终止（{names}{more}）"
                          "——期满各自是中立，要再打，得重新宣战")
        # 联盟清场：盟主亡 → 顺位继承（最早加入的剩余成员）；成员全亡 → 解散
        for bloc in list(self.blocs):
            if name in bloc["members"]:
                was_chief = self.bloc_chief(bloc) == name
                bloc["members"].remove(name)
                if bloc["members"]:
                    if was_chief:
                        bloc["chief"] = bloc["members"][0]
                        self.proclaim(f"👑 {name} 亡国，盟主之位由 {bloc['chief']} "
                                      f"继承（联盟「{bloc['name']}」）")
                else:
                    self.blocs.remove(bloc)
                    self.proclaim(f"💔 联盟「{bloc['name']}」因成员凋零而解散")
        for _v in [v for v in self.votes if self.bloc_by_name(v["bloc"]) is None]:
            self._cancel_vote(_v, "联盟因成员凋零而解散")   # ★ 别静默清票：盟外申请人要收到
        self.truce = {p: u for p, u in self.truce.items() if ent_nation(name) not in p}
        # ★★ 强制和平 ⇒ **在途的外交事项一律当场作废**（用户 2026-10-11 报的洞：
        #   「强制和平后，还没有敲定的和平条约没有自动作废，可能覆盖风险」）。
        #   这一刀落下时**所有战线已经终止**：在途的求和提议已经无仗可停，留着只会在落地
        #   那一刻把下面的全天下强制休战覆盖成它自己的短休战（防雪球的地基被一纸短约抹掉）；
        #   宣战/议和投票同理——它们的前提（有战线）也没了。见 `_void_pending_treaties`。
        self._void_pending_treaties(f"{name} 亡国、全天下强制休战", all_kinds=True)
        # 一方灭亡 → 强制全天下休战 10 回合（防连环征服滚雪球；已有更长休战则保留）
        until_fall = self.turn + FALL_TRUCE_TURNS
        ents = self.entities()
        for i in range(len(ents)):                      # 实体 ↔ 实体（联盟也在内）
            for j in range(i + 1, len(ents)):
                self.set_truce(ents[i], ents[j], until_fall)
        alive = self.alive()
        for i in range(len(alive)):                     # 国家 ↔ 国家（成员退盟后由它兜底）
            for j in range(i + 1, len(alive)):
                self.set_truce(alive[i], alive[j], until_fall)
        gone = self.drop_pacts_of(ent_nation(name))
        if gone:
            self.proclaim(f"💔 {name} 亡国，其条约随之作废（{'、'.join(gone)}）")
        self.mailbox.pop(name, None)
        # ★ 亡国**不清记忆**（2026-10-08 用户：「结算厅要结算所有国家」）——亡国之君要凭
        #   自己的记忆走进结算厅；删掉就只剩一屋子失忆的君主，整局战绩没有下文。
        #   留下的三样都是**记忆**（长期记忆 / 每回合小结 / 阶段块总结），逐字增长已停止；
        #   清掉的是**在局状态**：`turn_memory` 是"活着接着打"用的逐字缓冲区（也是存档里
        #   最大的一块，单国 ~1.4MB），死了不再需要；`maps`/`plans`/`econ_intel`/`ledger`
        #   同理。`polity` 留（那是身份：匈奴君主的 system prompt 要靠它）。
        self.turn_memory.pop(name, None)
        self.maps.pop(name, None)
        self.gift_pending = [g for g in self.gift_pending if g["from"] != name and g["to"] != name]
        self.map_pending = [m for m in self.map_pending if m["from"] != name and m["to"] != name]
        self.spy_pending = [s for s in self.spy_pending if s["from"] != name and s["to"] != name]
        self.econ_intel.pop(name, None)
        self.ledger.pop(name, None)          # 账本是本期暂态；已出的 econ_reports 留作历史
        self.plans.pop(name, None)
        self.extra_prompt.pop(name, None)     # 在局注入的临时提示；`polity` 留（身份）
        self.mail_pending = [m for m in self.mail_pending if m["to"] != name and m["from"] != name]
        self.peace_offers = [p for p in self.peace_offers if p["a"] != name and p["b"] != name]
        self.proposals = [p for p in self.proposals
                          if p.get("a") != name and p.get("b") != name
                          and p.get("A") != ent_nation(name) and p.get("B") != ent_nation(name)]
        return True


    def _stage_supply(self, ctx):
        # 3) 军队补给 + 回复（**先算补给、再打架**——用户 2026-10-11 钉死的回合末顺序：
        #    补给了才有血打；饿着肚子打完再回血，等于让『断粮扣血』和『这一拳』互相插队）
        famine = {}
        for n in self.alive():
            ps = self.nation_armies(n)
            # ★ 需求/缺口/每军扣血一律走 `supply_shortfall`——面板要报"缺粮会流多少血"
            #   时读同一个函数，不许自己重抄 `max(1, 35*short//need)`（那又是一处静默漂移源）。
            need, short, per = self.supply_shortfall(n)
            paid = min(need, self._resource_balance(n, 'native:supply'))
            self._consume_resource(n, 'native:supply', paid)
            self.flow_out['native:supply'] += paid   # 世界流量：军队吃补给
            self._ledger(n)["supply_eaten"] += paid
            self._spend(n)["supply"] += self._resource_value('native:supply', paid)   # 总消费：军费
            if short:
                # 缺口按比例分摊：**吃补给的**每军扣 ARMY_STARVE_DAMAGE×缺口/需求（交战中也照扣），
                # 至少 1。★ 2026-10-10 用户：「应该是**依赖补给的军队**一起扣」——原先这里遍历
                # `ps`（全军），于是**驻在自家军屯、这一回合根本没吃补给的民兵也跟着挨饿**：
                # 豁免明明让它不占需求额度，却仍在挨饿名单里，前后自相矛盾。
                # 名单改从 `_supply_fed` 取——与需求同一份判定。
                dead = []
                for a in self._supply_fed(n, ps):
                    a.take_damage(per)
                    if a["hp"] <= 0:
                        dead.append(a)
                for a in dead:
                    if a in self.armies:
                        self.armies.remove(a)
                famine[n] = (short, per, len(dead))
                ps = self.nation_armies(n)
            battle_tiles = {(a["x"], a["y"]) for a in self.armies if a.get("engaged")}
            # 回血的两个前提、一个代价（用户 2026-09-26「回血要补给，现在回血变成满足补给的
            # 条件下，自动扣 1 装备回血」）：① 本国本回合没断供 ② 不在交战格；
            # 代价 = 每支真吃到回血的军自动扣 `HEAL_EQUIP_COST` 件装备。满血的不扣（它没回血），
            # 装备见底就停（付不起就不回血）。迭代序用 `self.troops` 的稳定序，同 seed 可复现。
            heal_equip = 0
            for a in list(self.troops):          # 回血只给本国军队（野人不在 `n` 的账上）
                if a["owner"] != n:
                    continue
                if short or (a["x"], a["y"]) in battle_tiles:
                    continue  # 断粮或所在格正在交战（含防御方守军）→ 不回血
                if a["hp"] >= unit_max_hp(a):
                    continue  # 满血：没得回，也就不该扣装备
                # ★ 民兵除外（用户 2026-09-26「民兵除外」）：它**征召就不吃装备**（50金+5粮），
                #   回血自然也不该吃 ⇒ 免费。用 continue 而不是 break：付不起只卡住正规军，
                #   别把排在后面的民兵一起连坐（装备只减不增，非民兵之间 break≡continue）。
                if unit_kind(a) != 'native:militia':
                    if self._resource_balance(n, 'native:equipment') < HEAL_EQUIP_COST:
                        continue
                    self._consume_resource(n, 'native:equipment', HEAL_EQUIP_COST)
                    self.flow_out['native:equipment'] += HEAL_EQUIP_COST   # 世界流量：回血吃装备
                    self._ledger(n)["heal_value"] += self._resource_value('native:equipment', HEAL_EQUIP_COST)
                    heal_equip += HEAL_EQUIP_COST
                a.heal(ARMY_HEAL_PER_TURN)
            if heal_equip:
                self.log(f"🩹 {n} 军队回血：正规军吃装备 {heal_equip} 件（民兵免费）",
                         phase="内政", nation=n)
        for n, (short, per, dead) in famine.items():
            self.log(f"⚠ {n} 补给断粮（缺 {short}，吃补给的每军 -{per}HP）："
                     f"{dead} 支军队饿毙", phase="内政", nation=n)

        ctx["famine"] = famine


    def _stage_battle(self, ctx):
        # 4) 战争结算
        war_lines = self._resolve_battles()
        for wx, wy, ln in war_lines:
            self.log(ln, phase="战报", x=wx, y=wy)  # 带坐标 → 视野内（含瞭望塔圈）才可见
        flat_lines = [ln for _, _, ln in war_lines]

        ctx["war_lines"] = war_lines
        ctx["flat_lines"] = flat_lines


    def _stage_retreats(self, ctx):
        # 4.5) 撤退落地：撤退军队已随本轮战斗结算（全场分摊），此刻脱离到目标格
        for a in [a for a in self.armies if a.get("retreat_to")]:
            tx, ty = a["retreat_to"]
            # ★ **先抢存再 pop**：角色/减伤档要在下面三行日志里用，而 pop 与日志之间
            #   隔着 `hp<=0` 判断和三层分支——本函数曾经就是"先 pop 后写日志"才把
            #   攻/防角色丢掉的（谁再往里插一个分支就会再犯一次）。数据跟着军走，
            #   与 pop 的位置无关。
            role, cover = a.get("retreat_role"), a.get("retreat_cover", 100)
            note = retreat_note(role != "攻", cover)
            a.pop("retreat_to", None)
            a.pop("retreat_cover", None)
            a.pop("retreat_role", None)
            if a["hp"] <= 0:
                continue  # 结算中阵亡，撤不成了（战报已记）
            if self._retreat_legal(a["owner"], tx, ty):
                a["x"], a["y"] = tx, ty
                a.disengage()
                self.log(f"{a['name']} 撤到 ({tx+1},{ty+1})，脱离交战（{note}）", phase="战报",
                         nation=a["owner"], x=tx, y=ty)
            else:
                alts = [(nx, ny) for nx, ny in self.neighbors(a["x"], a["y"])
                        if (nx, ny) != (a["x"], a["y"]) and self._retreat_legal(a["owner"], nx, ny)]
                if alts:
                    a["x"], a["y"] = alts[0]
                    a.disengage()
                    self.log(f"{a['name']} 撤退目标格战局生变，改撤 ({alts[0][0]+1},{alts[0][1]+1})（{note}）",
                             phase="战报", nation=a["owner"], x=alts[0][0], y=alts[0][1])
                else:
                    self.log(f"{a['name']} 撤退目标格已不合法且四周无可退点，原地留守（{note}）",
                             phase="战报", nation=a["owner"], x=a["x"], y=a["y"])


    def _stage_disengage(self, ctx):
        # 4.9) 脱离清扫：撤退军已落地离开原格，格上留守者不该再背「交战中」
        cleared = self._clear_disengaged()
        if cleared:
            self.log(f" {cleared} 支军队解除交战（敌军已撤离/覆灭）", phase="战报")


    def _stage_withdraw(self, ctx):
        # 5) 非法滞留 → 自动遣返（断盟/退盟/停战后必须撤出）
        self._withdraw_illegal()


    def _supply_fed(self, n: str, ps: list[dict]) -> list[dict]:
        """这批军里**这一回合真要吃补给**的那些（同序）。

        **唯一判定处**——需求（`_supply_need`）与断粮扣血（`resolve_turn` 第 4 步）
        都从这里取名单，**不许各写一遍**（曾经扣血走的是"全军"、需求走的是"扣掉免供的"，
        于是免供的军屯民兵也跟着挨饿）。

        豁免只有一条：**民兵驻在自家军屯格**，每座军屯覆盖本格 `militia_cap` 支
        （2026-10-10 起 2）；同格超出覆盖数的、离格的、军屯被夺的，照常吃。
        """
        free: dict[tuple[int, int], int] = {}
        out: list[dict] = []
        for a in ps:
            if unit_kind(a) == 'native:militia':
                t = self.tiles.get((a["x"], a["y"]))
                if t is not None and t["owner"] == n:
                    cap = t["buildings"].get("军屯", 0) * building_effect("军屯", "militia_cap")
                    used = free.get((a["x"], a["y"]), 0)
                    if used < cap:
                        free[(a["x"], a["y"])] = used + 1
                        continue
            out.append(a)
        return out


    def _supply_need(self, n: str, ps: list[dict]) -> int:
        """全军每回合补给需求（单位数）：步1/骑2/民1，**只算真要吃的那些**
        （免供的军屯民兵不计——判定见 `_supply_fed`）。"""
        return sum(unit_supply(a) for a in self._supply_fed(n, ps))
        return need


    @staticmethod
    def starve_per_army(need: int, short: int) -> int:
        """缺口 → **每支军**扣多少血：按缺口比例分摊（满额 `ARMY_STARVE_DAMAGE`），至少 1；
        无缺口为 0。★ 公式只此一处，结算与面板都走它。"""
        return max(1, ARMY_STARVE_DAMAGE * short // need) if (short and need) else 0


    def supply_shortfall(self, n: str) -> tuple[int, int, int]:
        """该国本回合的补给账 `(需求, 缺口, 每军扣血)` —— **只看不扣**（供面板/摘要）。

        ★ 三条容易漏的引擎事实，报给 AI 时都要带上：
          · **交战中也照扣**——断粮的军不会因为"正在打仗"就免于流血；
          · 扣血的是**要吃补给的军**（2026-10-10 用户口径：免供——即驻自家军屯的民兵——
            **不在此列**，它们这一回合根本没吃你的补给）；不分在不在交战格；
          · 断粮**或**所在格在交战 ⇒ 那格**本回合不回血**（见 `resolve_turn` 第 4 步）。
        """
        need = self._supply_need(n, self.nation_armies(n))
        short = max(0, need - self._resource_balance(n, 'native:supply'))
        return need, short, self.starve_per_army(need, short)


    def _clear_disengaged(self) -> int:
        """脱离战斗清扫：格上已无活敌军的 engaged 军队就地解除交战。
        覆盖两处死角——敌军撤退落地离开原格（留守者傻等下回合结算才解锁）、
        以及任何路径留下的空交战标记。返回解除数。"""
        cleared = 0
        for a in self.armies:
            if not a.get("engaged"):
                continue
            foes = []
            for d in self.armies:
                if d is a or (d["x"], d["y"]) != (a["x"], a["y"]):
                    continue
                if d["owner"] == "野人":
                    if a["owner"] != "野人":
                        foes.append(d)  # 我方与野人交战（野人只接战进攻方）
                    continue
                if d["owner"] != a["owner"] and self.war_between(a["owner"], d["owner"]):
                    foes.append(d)
            if a["owner"] == "野人":
                # 野人只与「交战中的进攻方」为敌，和平停驻者不算
                foes = [d for d in self.armies
                        if d is not a and (d["x"], d["y"]) == (a["x"], a["y"])
                        and d["owner"] != "野人" and d.get("engaged")]
            if not foes:
                a.disengage()
                cleared += 1
        return cleared


    def _withdraw_illegal(self):
        """断盟/停战后身处他国中立领土的军队，每回合按兵种速度朝最近的合法地(本国/盟国)撤
        （步兵 1 格=3×3、骑兵 2 格=5×5，一步步走）。"""
        for n in list(self.alive()):
            legal_tiles = self.own_tiles(n)
            for m in self.alive():
                if m != n and self.allied_between(n, m):
                    legal_tiles += self.own_tiles(m)
            if not legal_tiles:
                continue
            for a in list(self.troops):              # 撤军只可能是国家军队（野人不 retreat）
                if a["owner"] != n or a.get("engaged"):
                    continue
                owner = self.owned_by(a["x"], a["y"])
                if owner is None or owner == n or self.allied_between(n, owner) or self.war_between(n, owner):
                    continue  # 合法
                # 非法：按兵种速度（骑兵 2 格/步兵 1 格）朝最近的合法地一步步走
                tx, ty = min(legal_tiles, key=lambda p: max(abs(p[0] - a["x"]), abs(p[1] - a["y"])))
                moved = False
                for _ in range(unit_speed(a)):
                    best, bd = None, 10 ** 9
                    for nx, ny in self.neighbors(a["x"], a["y"]):
                        if not self._enterable_step(n, nx, ny, forced=True):
                            continue
                        dd = max(abs(nx - tx), abs(ny - ty))
                        if dd < bd or (dd == bd and (nx, ny) < (best or (9 ** 9, 0))):
                            best, bd = (nx, ny), dd
                    if best is None:
                        break
                    a["x"], a["y"] = best
                    moved = True
                if moved:
                    self.log(f"🚶 {n} 军队{a['id']} 自敌境「遣返」撤向合法地（{a['x']+1},{a['y']+1}）", phase="事件", nation=n, x=a["x"], y=a["y"])


    def _enterable_step(self, n: str, x: int, y: int, forced: bool = False) -> bool:
        """军队能否落步到 (x,y)。forced=True 供强制遣返用：军队已非法滞留他国（断盟/停战后），
        只允许落在合法地会把它永远困死，故遣返途中允许踩过中立地，一路走回本国/盟国。"""
        if forced:
            return True
        o = self.owned_by(x, y)
        return o is None or o == n or self.allied_between(n, o) or self.war_between(n, o)
