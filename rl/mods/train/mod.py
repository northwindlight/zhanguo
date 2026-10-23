# -*- coding: utf-8 -*-
"""RL 训练能力 `rl:train` —— **只提供能力对象，不实现任何引擎规则**。

    为什么是这个文件，而不是旧 `rl/engine.py`
    ────────────────────────────────────────
    旧 `rl/engine.py`（1648 行）是一层"把新引擎包成旧 `world.*`"的门面：
      · 自己记回合号、地块表、军队表、建筑表、联盟表（5 张影子状态表）；
      · 自己复刻了摊分、野人、除名、新兵、外交表决等**引擎规则**；
      · 自己把引擎的 `Unit` 转成"旧口径 dict"。
    ⇒ 引擎改了规则，RL 侧**不会跟着改**，而且**不报错**（只是算错）。

    这里改成引擎的原生写法：
      · 能力清单写在 `mod.toml`（`[Capabilities]` / `[Interfaces]`），
        加载时逐个校验方法存在，缺一个直接 `ModError`（**不静默**）；
      · 服务对象由 `objects(world)` 提供，**不缓存任何引擎状态**；
      · 规则只通过**声明过的**接口调用（`runtime.object_call`）。

    三条红线（review §8）
    ────────────────────
    ① 不复刻引擎规则：伤害怎么摊、野人怎么合成、亡国怎么判定、兵能不能动 ——
       一律问 native；RL 侧只做**决策**（打不打、打谁），不做**结算**。
    ② 不记影子状态：回合号读 `native:government.period`，地块问 `native:territory`，
       建筑问 `native:buildings`。
    ③ 不吞引擎拒绝：引擎拒了就抛；`except: pass` 一律不写。
"""
from __future__ import annotations

from engine.runtime import GameMod

# ★ 引擎的原生建筑内容 ID（`mods/native/buildings/types.py`）。
# ★ 引擎的"野人"归属标记（`Military._hostile` 里就是这么判的）。
BARBARIAN = "野人"

TOWN_HALL = "native:town-hall"
CASTLE = "native:castle"
BARRACKS = "native:barracks"


class TrainMod(GameMod):
    """RL 训练能力。**不提供 mixin**（不给世界加方法）——能力走 `objects()`。"""

    def mixins(self):
        return ()

    def configure(self, declaration, path):
        super().configure(declaration, path)
        sandbox = declaration.get('Sandbox', {})
        if set(sandbox) != {'Stock', 'Supply'}:
            raise ValueError('Sandbox 必须声明 Stock / Supply')
        for key in ('Stock', 'Supply'):
            if type(sandbox[key]) is not int or sandbox[key] < 0:
                raise ValueError(f'Sandbox.{key} 必须是非负整数')

    def objects(self, world):
        call = lambda cap, method, *a, **k: self.runtime.object_call(world, cap, method, *a, **k)
        return {
            'rl:sandbox': SandboxService(call, world, self.declaration['Sandbox']),
            'rl:truth': TruthService(call),
            'rl:odds': OddsService(call),
        }

    # ------------------------------------------------------------ 状态存档
    def save_state(self, world):
        return {'rl_sandbox': dict(getattr(world, 'rl_sandbox', {}))}

    def load_state(self, world, state):
        if not isinstance(state, dict) or set(state) != {'rl_sandbox'}:
            raise ValueError('Invalid rl:train save')
        world.rl_sandbox = dict(state['rl_sandbox'])


class SandboxService:
    """**摆局**：把世界摆成一个可训练的沙盒局面。

    ★ 全部走 native **已声明**的方法；**不缓存任何引擎状态**。
      这里写下的每一个事实，引擎自己都记得（`territory` / `buildings` / `nations` / `units`）。
    """

    def __init__(self, call, world, settings):
        self.call = call
        self.world = world
        self.settings = settings

    # ------------------------------------------------------------ 地图与国家
    def reset(self, size: int = 8, seed: int = 0) -> None:
        """把地图重置成一张空地图（`size` × `size`，`seed` 决定地形）。"""
        self.call('native:territory', 'configure', int(size), int(seed))

    def place_nation(self, nation_id: str, name: str, sites: dict,
                     core: str | None = None, terrain: str | None = None) -> None:
        """建国家并铺地。

        `sites` = `{site_id: (x, y)}` —— **摆局参数**，由剧本（`ScenarioMod`）给。
        `core`  = 核心格（`site_id`）：它是**该国的核心**，并可强制地形
                  （旧引擎 `_place_crosses` 的口径："中心格必然是平原"）。
        ★ 这里**不造十字、不造建筑**：那是**剧本的事**，不是能力的事。
        ★ 顺序有讲究：**先建国家，再铺地** —— native 的 `territory.create`
          要求 owner 是**在册的活着的国家**（`Unknown living owner`）。
        """
        amounts = {key: int(self.settings['Stock'])
                   for key in ('native:gold', 'native:food', 'native:wood',
                               'native:ore', 'native:oil', 'native:equipment')}
        amounts['native:supply'] = int(self.settings['Supply'])
        self.call('native:nations', 'create', nation_id, name, amounts)
        for site_id, (x, y) in sites.items():
            is_core = site_id == core
            self.call('native:territory', 'create', site_id, nation_id, int(x), int(y),
                      terrain=terrain if is_core else None,
                      core=nation_id if is_core else None)

    def seed_building(self, site_id: str, kind: str, count: int = 1) -> None:
        """在一处站点上种下建筑（**native 的初始化口**，不是玩家建造流程）。"""
        self.call('native:buildings', 'seed', site_id, kind, count=int(count))

    def enroll_succession(self, owner: str) -> None:
        """把该国**登记进 native 的继承序列**（`native:succession.enroll`）。

        ★★ 2026-10-23 修（review P1-2）：**亡国必须走引擎的正规流程**。
          引擎的 `native:succession` 才是"国祚断绝 ⇒ 亡国"的**唯一**出处：
          它在结算时检查"在册国还有没有**带首都角色**的站点"，
          没有 ⇒ 迁移站点、`diplomacy.eliminate`、`army.remove_nation`、
          `nations.extinguish`（**引擎自己动手**，不是 RL 侧改状态）。
          ★ 以前 RL 线**从来没登记过**（存档里的 `enrolled` 恒为空）⇒
            引擎那一整套亡国逻辑**整段没跑过** ⇒
            "夺走最后一座市政厅"之后**引擎那边还是老样子**
            （国家仍在册、军队还在、战争关系还在），
            而 RL 侧的 `alive()` 早说它死了 ⇒ **两边分叉**。
          ⇒ 现在：摆局时给每个国家登记一次（**有首都之后**再登记 ——
            `enroll` 的语义是"从此开始按国祚判生死"，没首都就登记 = 立刻判死）。
        """
        self.call('native:succession', 'enroll', owner)

    def owner_of(self, x: int, y: int):
        """这一格归谁（`None` = 无主）。**转调 native**，不自己查表。"""
        return self.call('native:territory', 'owner', int(x), int(y))

    def create_site(self, site_id: str, owner: str, x: int, y: int,
                    terrain=None, core=None) -> None:
        """物化一格并归 `owner`（native 的 `territory.create`）。"""
        self.call('native:territory', 'create', site_id, owner, int(x), int(y),
                  terrain=terrain, core=core)

    def owned_cells(self, owner: str) -> tuple:
        """该国拥有的**格坐标**（native 的 `owned`）。"""
        return tuple(tuple(fact.tile.position)
                     for fact in self.call('native:territory', 'owned', owner))

    def claim(self, owner: str, x: int, y: int) -> None:
        """占一格当国土。

        ★ 走 native 的两条**正规**入口：
          · 格**已物化**且无主 → `territory.claim`（占下并把核心设成占领者）；
          · 格**未物化**        → `territory.create_claim`（建新地块并设核心）。
        ★ **不再有"先撤野人、再物化"那套影子逻辑**：
          native 的野人是**现场合成**的（`Military.visible_units`），
          格一旦物化，合成条件就不成立 ⇒ 野人自然消失 ✔
          （旧门面得手动改 `world.units.guard_once` 私有字段才能达到同样效果，
            而那个字段的语义**恰好相反** —— 加进去是"这格**不**再合成野人"）。
        """
        fact = self.call('native:territory', 'cell', int(x), int(y))
        if fact.tile.id:
            self.call('native:territory', 'claim', fact.tile.id, owner)
        else:
            site = f"{owner}/land-{int(x)}-{int(y)}"
            self.call('native:territory', 'create_claim', site, owner, int(x), int(y))

    # ------------------------------------------------------------ 军队
    def spawn(self, owner: str, kind: str, x: int, y: int, count: int = 1):
        """**开局摆兵** —— 走 native 的 `seed`（＝"初始化/导入"，`born_turn` 为空）。

        ★ 与 `recruit` 的区别就是 native 的区别：
          · `seed`    ＝ 初始化/导入 ⇒ 这支军**本回合可以动**（`born_turn` 为 `None`）；
          · `recruit` ＝ 回合内征召 ⇒ **本回合不能动**（`born_turn = 当前期`）。
        ⇒ 沙盒**不再有第三种"造兵"方式**（旧门面 `military.seed` 之后自己改
          `moved_turn`/`born_turn` 的那套影子逻辑已删除）。
        """
        return self.call('native:army', 'seed', owner, kind, int(x), int(y), count=int(count))

    def recruit_quote(self, owner: str, site: str, kind: str, count: int = 1) -> dict:
        """问引擎"这一格这一期还能征几支"（**native 的额度**）。

        ★ 征召额度是**引擎的硬约束**（每兵营每期 1 支；民兵还有全国编制上限），
          RL 侧**先问额度再征**，而不是自己算一个上限再让引擎拒。
        """
        return self.call('native:army', 'recruit_quote', owner, site, kind, int(count))

    def recruit(self, owner: str, site: str, kind: str, count: int = 1):
        """**回合内补员** —— 走 native 的 `recruit`（征召）。

        ★ native 的新兵规则由 `recruit` 自己保证：`Units.spawn(..., turn=period, newborn=True)`
          ⇒ `born_turn = 当前期` ⇒ `Unit.can_act(period)` 为假 ⇒ **本回合不能动**。
          这条规则 native 自己写得很清楚（`domain_objects.py`：「新兵本回合不能撤退」）。
        ⇒ RL 侧**不再复刻**它，也不绕过它。
        """
        return self.call('native:army', 'recruit', owner, site, kind, count=int(count))

    def purge_armies(self, owner: str) -> None:
        """解散某国的全部军队（**真删**，不是从临时表里 pop 掉）。"""
        for unit in self.call('native:army', 'visible_units', owner):
            if unit.owner == owner and unit.alive:
                self.call('native:army', 'disband', owner, unit.id)

    # ------------------------------------------------------------ 外交
    def declare_wars(self, pairs) -> None:
        """★ **批量宣战**（一次结算把所有宣战都走完）。

        ★ 为什么批量：native 的宣战要"提交 → 结算（生成表决）→ 代投 → 再结算（落地）"
          ⇒ **一对一次结算**。N 国两两宣战 = `2 × N(N-1)/2` 次结算 ⇒ 建一局的
          开销随国家数**平方**涨（实测 3 国建局 0.17s、5 国更贵）。
          ⇒ 把所有宣战**先提交**，**一次结算**生成全部表决，**一次结算**落地
          ⇒ 恒定 **2 次结算**（与国家数无关）。
        ★ 时序仍由引擎定：表决在结算时生成、截止期 = 生成时的期号 + 1
          ⇒ 第一次结算后正好到截止期 ⇒ 代投 → 第二次结算落地 ✔
        """
        pairs = [(a, b) for a, b in pairs if a != b]
        if not pairs:
            return
        # ★★★ 2026-10-23 修（review P1-2）：**跳过"停战期内"的那几对**。
        #   引擎的 `Diplomacy.eliminate`（**国家亡国时**）会：
        #     · **清空全部战争**（`self._wars = []`）；
        #     · 给**所有幸存者**两两停战，直到 `期号 + FallTruce`（native 默认 10 期）。
        #   ⇒ 引擎的设计是"亡国 ⇒ 战争结束 ⇒ 幸存者冷静一段，之后**可以**再宣战"。
        #   ★ 但**停战期内宣战会被引擎拒**（`Active truce blocks war`）⇒
        #     这里**先问引擎**（`active_truce`）再决定要不要宣 ⇒
        #     **不吞异常、也不绕过引擎的停战规则**。
        #   ⇒ 一对都不需要宣（都在打 / 都在停战）⇒ **一次结算都不做**
        #     （否则每回合白推进 2 个期号）。
        todo = []
        for a, b in pairs:
            if self.call('native:diplomacy', 'war_between', a, b):
                continue                        # ★ 已经在打了 ⇒ 幂等
            if self.call('native:diplomacy', 'active_truce', a, b):
                continue                        # ★ 停战期没过 ⇒ 现在不能宣
            todo.append((a, b))
        if not todo:
            return
        for a, b in todo:
            self.call('native:diplomacy', 'submit', a, 'declare_war', target=b)
        self.resolve()                          # ① 一次结算：生成全部表决
        for a, b in todo:
            self._approve_votes(a)
        self.resolve()                          # ② 一次结算：全部落地

    def declare_war(self, a: str, b: str) -> None:
        """宣战（走 native 的 `diplomacy`）。

        ★ native 的规则：联盟成员宣战要走**联盟表决**
          （`Diplomacy._apply_declare_war` → `_route` → `_vote`，表决在**结算**时生成，
          截止期 = 生成时的期号 + 1）⇒ 这里的时序是 native 定的，不是我们定的：
            ① 提交宣战 → ② 结算一次（表决生成，期号推进到截止期）
            → ③ 代投赞成 → ④ 再结算一次（计票 → 通过 → 战争落地）。
        ★ **不吞异常**：投不到票就抛（那是真错误，不是可以忽略的分支）。
        """
        if self.call('native:diplomacy', 'war_between', a, b):
            return                              # ★ 已经打起来了 ⇒ 幂等（不是错）
        self.call('native:diplomacy', 'submit', a, 'declare_war', target=b)
        self.resolve()
        self._approve_votes(a)
        self.resolve()

    def set_alliance(self, members: list, name: str) -> None:
        """结盟（走 native 的 `bloc_found` + 各成员 `accept_proposal`）。

        ★ native 的建盟是**整批受理**：全部被邀方都接受，联盟才成立
          （`Diplomacy._apply_accept_proposal`）⇒ 一次提交、一次结算就够。
        ★ **结算后校验成员**（review P1-4）："accept 就成功"是假的 ——
          代投之后**必须回读**联盟成员表确认，不确认就是没成。
        ★ **不吞异常**：提案没被接受就抛。
        """
        members = list(members)
        if len(members) < 2:
            raise ValueError('结盟至少要两个国家')
        bloc = self.bloc_id(members)
        self.call('native:diplomacy', 'submit', members[0], 'bloc_found',
                  bloc=bloc, name=name, tos=members[1:])
        self.resolve()
        for member in members[1:]:
            offer = self._find_offer(member, bloc)
            self.call('native:diplomacy', 'submit', member, 'accept_proposal', offer=offer)
        self.resolve()
        # ★ 回读校验：联盟必须真的成了，且成员齐全。
        alliance = self.call('native:diplomacy', 'alliance', members[0])
        if alliance is None or not set(members) <= set(alliance.members):
            raise ValueError(f'结盟未生效：{name}（成员：{members}）')
        if alliance.id != bloc:
            raise ValueError(f'结盟实体不符：{alliance.id} ≠ {bloc}')

    @staticmethod
    def bloc_id(members: list) -> str:
        """联盟 id —— **由成员推出**，不靠自增计数器。

        ★ 为什么不用计数器：计数器是**进程状态**（换一局就错位、存档也带不走）。
          这里用成员内容 ID 的后缀拼出稳定 id ⇒ 同一组成员永远是同一个 id。
        ★ 必须是**合法内容 ID**（`engine/identifiers.py`：命名空间 + 全 ASCII 小写，
          后缀里不能有 `:`）⇒ 取后缀（`rl:n0` → `n0`）。
        """
        return "rl:bloc-" + "-".join(sorted(str(m).split(":", 1)[-1] for m in members))

    def _find_offer(self, nation_id: str, bloc_id: str) -> str:
        """找该国收到的、属于 `bloc_id` 的建盟提案 id。

        ★ **不用 `offers[0]`**（review P1-4）：提案列表里可能有别的国家的，
          取第一个就是**猜** —— 猜错了会"接受了一个不相干的提案"而且**不报错**。
          native 的建盟提案是 `action == 'found'`，联盟 id 在 `arguments` 里。
        """
        snapshot = self.call('native:diplomacy', 'snapshot', nation_id)
        for offer in snapshot.offers:
            if offer.action != 'found':
                continue
            if dict(offer.arguments).get('bloc') == bloc_id:
                return offer.id
        raise ValueError(f'{nation_id} 没有收到 {bloc_id} 的建盟提案')

    def _approve_votes(self, actor: str) -> None:
        """代投赞成：把 `actor` 所在联盟的待表决都投成赞成。

        ★ native 的表决是**按联盟**的，只有联盟成员看得见（`Diplomacy.snapshot`
          的 `votes` 字段按"投票者在联盟里"过滤）⇒ 用成员视角读，用成员身份投。
        ★ 只给**该联盟的成员**投 ⇒ 不是"逐个 try 过去"，失败就是真错误，直接抛。
        """
        snapshot = self.call('native:diplomacy', 'snapshot', actor)
        alliances = {a.id: a for a in snapshot.alliances}
        for vote in snapshot.votes:
            bloc = alliances.get(vote.alliance)
            if bloc is None:
                raise ValueError(f'表决 {vote.id} 的联盟 {vote.alliance} 不存在')
            for member in bloc.members:
                self.call('native:diplomacy', 'vote', member, vote.id, True)

    # ------------------------------------------------------------ 结算
    def resolve(self) -> None:
        """结算一个回合（native 的世界结算：战斗、占地、外交、补给、推进期数）。"""
        self.world.resolve_turn()
        self.world.phase = "行动"


class TruthService:
    """**训练专用真值** —— 与玩家公开 API 分开，显式声明在 `[Capabilities]`。

    ★ 为什么单独一个能力：这些读法**越过了玩家的信息边界**（比如"这格的真实防御%"），
      训练时可以用（当作标签/监督信号），但**不能**混进玩家面。
    ★ 每一项都**转调 native 的规则**，不自己算：
      · 防御% → `native:army.defense_pct`（native 的公式）
      · 守军 → `native:army.visible_units`（native 自己的野人合成口径）
    """

    def __init__(self, call):
        self.call = call

    def defense_pct(self, x: int, y: int, owner: str | None) -> int:
        """这一格的总防御% = 地形 + 城堡（**native 的公式**）。"""
        terrain = self.call('native:territory', 'terrain', int(x), int(y))
        castle = 0
        if owner is not None:
            fact = self.call('native:territory', 'cell', int(x), int(y))
            if fact.owner == owner and fact.tile.id:
                castle = (self.call('native:buildings', 'count', fact.tile.id, CASTLE)
                          * self.call('native:buildings', 'effects', fact.tile.id, CASTLE)
                          .get('defense_per_level', 0))
        return self.call('native:army', 'defense_pct', terrain.defense, castle)

    def defenders(self, x: int, y: int, owner: str) -> tuple:
        """这一格上、与 `owner` 敌对的守军（**native 看到的**）。

        ★ 直接用 `visible_units`：native 自己就在那里合成野人
          （"未物化且未驻过军的可见格 ⇒ 现场合成一支满血步兵"），
          口径**完全按引擎**，RL 侧不再自己造野人（review P1-6）。
        """
        out = []
        for unit in self.call('native:army', 'visible_units', owner):
            if (unit.x, unit.y) != (int(x), int(y)) or unit.owner == owner:
                continue
            if not unit.alive:
                continue
            # ★ 敌对口径照 **native 的 `_hostile`**：野人对所有人都是敌对的
            #   （`war_between` 只查战争表，**不含野人** ⇒ 只用它会把野人漏掉，
            #     而野人正是"扩张成本"的主体）。
            if unit.owner != BARBARIAN and not self.call(
                    'native:diplomacy', 'war_between', owner, unit.owner):
                continue
            out.append(unit)
        return tuple(out)


class OddsService:
    """**战斗概率** —— 直接消费 experimental 的能力，不再 fork 一份实现。

    ★ 旧 `rl/combat_probs.py`（836 行）+ `rl/_combat_fast.c`（999 行）是
      `mods/experimental/combat_research/` 的**同源复制**
      （那份 README 明写"来源为 `origin/feat/rl-tactics` 的 `rl/combat_probs.py`"），
      ⇒ 引擎的摊分规则改一次要改两处，而且**改漏了不报错**。
      本文件已删除；这里只做转发。
    """

    def __init__(self, call):
        self.call = call

    def assess(self, actor: str, unit_ids, x: int, y: int) -> dict:
        """评估"我这支队打这一格"的胜算（**转调 native 侧的能力**）。"""
        return self.call('research:combat-probabilities', 'assess', actor,
                          list(unit_ids), int(x), int(y))
