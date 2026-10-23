# -*- coding: utf-8 -*-
"""RL 环境客户端 `Env` —— RL 线与引擎之间**唯一**的接缝。

★ 它**不是**旧 `rl/engine.py` 那种门面
  ──────────────────────────────────
  旧门面（1648 行）做的是"把引擎包成旧 `world.*`"：
    · 自己记回合号、地块表、军队表、建筑表、联盟表；
    · 自己复刻野人合成、伤害摊分、亡国除名、新兵、外交表决等**引擎规则**；
    · 自己把引擎的 `Unit` 转成"旧口径 dict"；
    · 引擎抛的异常一律 `except: pass` 吞掉。
  ⇒ 引擎改了规则，RL 侧不会跟着改，而且**不报错**。

  这里只做三件事：
    ① **转发**：把调用送到**声明过的**能力接口（`runtime.object_call`）；
    ② **投影**：把引擎对象**现算**成 RL 要读的形状（`TileRow`），**不缓存**；
    ③ **换词**：RL 用中文短名（甲/乙），引擎用内容 ID（`rl:n0`）——
       换词是**纯函数**（`rl/nations.py`），不是状态。

★ 一次调用都**不许**自己复刻引擎规则
  ─────────────────────────────────
  合法性、伤害、野人、亡国、新兵 —— 全部问 native。
  本文件里的 `reachable` / `defense_pct` / `barbarian_cells` 等**只是转发**，
  没有一行是在"重算"引擎已经算过的东西。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.runtime import DomainRejection
from engine.state import unit_context

from . import nations as N

# ★ RL 训练能力的 Mod ID（`rl/mods/train/mod.toml` 的 `[Mod] Id`）。
#   有了它，`unit_context` 才能让 `runtime.object_call` **按声明校验**：
#   调用了没在 `[Interfaces]` 里声明的方法 ⇒ 直接 `ModError`（不静默）。
TRAIN_MOD = "rl:train"

# ★ RL 线的发行版清单（`rl/release.toml`）。
RELEASE = "rl/release.toml"

# ★ 引擎的"野人"归属标记（`mods/native/army` 里 野人 的 owner 字面量）。
BARBARIAN = "野人"

# ★ 建筑内容 ID（`mods/native/buildings/types.py`）。
TOWN_HALL = "native:town-hall"
CASTLE = "native:castle"
BARRACKS = "native:barracks"


@dataclass(frozen=True)
class TileRow:
    """一格的**读模型** —— 每次从引擎**现算**，**不缓存**。

    ★ 为什么需要它：引擎的 `TileFact` 里**没有建筑**（建筑在另一个域），
      而 RL 的观测要"这一格上有几座厅/城堡"。
      这里把两个域的**当前值**合起来读一次 —— 读完就扔，不留表。
    """

    position: tuple
    owner: str | None
    terrain: object            # 引擎的 `Terrain`（.id / .defense / .build_penalty / …）
    core: str | None
    tile_id: str
    name: str = ""             # 引擎的 `Tile.name`（地名）
    buildings: dict = field(default_factory=dict)   # {建筑 kind: 数量}


class Env:
    """RL 环境的**能力客户端**。

    ★ 状态归属：
      · 引擎状态 → 引擎自己（`territory` / `buildings` / `units` / `diplomacy` / …）；
      · RL 状态   → `rl/sandbox.py` 的 `Sandbox`（回合序、待办、记忆账本）；
      · 本类**什么都不存** ⇒ 没有影子状态可漂移。
    """

    def __init__(self, runtime, world):
        self.rt = runtime
        self.world = world

    # ------------------------------------------------------------ 构造
    #: ★ Runtime **按清单路径缓存**（加载一次很贵：它会解析全部 mod.toml）。
    #:   缓存的是**运行时**，不是**世界** ⇒ 每次 `load()` 给的都是**全新世界**。
    _runtimes: dict = {}

    @classmethod
    def load(cls, manifest: str = RELEASE) -> "Env":
        """按发行版清单加载一份**全新的世界**。

        ★ `check_interfaces` 在这里**显式跑一次**：
          它逐个校验 `[Interfaces]` 里声明的方法**真的存在**于能力对象上，
          缺一个直接 `ModError`（"改名 ⇒ 加载即报，不静默"）。
        """
        from engine.runtime import Runtime
        key = str(manifest)
        runtime = cls._runtimes.get(key)
        if runtime is None:
            runtime = Runtime.load(manifest)
            cls._runtimes[key] = runtime
        world = runtime.world_class()()
        runtime.check_interfaces(world)
        return cls(runtime, world)

    def clone(self) -> "Env":
        """试演副本：**引擎状态**按 mod 存档复制（`save_mod_state` / `load_mod_state`）。

        ★ 用引擎自己的存档机制，而不是 `deepcopy(world)` ——
          后者会把 `Owned` 包装对象连同内部引用一起复制（引擎明确禁止）。
        """
        world = self.rt.world_class()()
        self.rt.load_mod_state(world, self.rt.save_mod_state(self.world))
        world.phase = self.world.phase
        return Env(self.rt, world)

    # ------------------------------------------------------------ 调用
    def call(self, capability: str, method: str, *args, **kwargs):
        """调一个**声明过的**能力方法。

        ★ `unit_context(TRAIN_MOD)` 是这套机制的**开关**：
          `runtime.object_call` 会检查"调用方（`rl:train`）是否声明了这个能力与方法"，
          没声明 ⇒ `ModError`。
          ⇒ RL 侧**调错方法名/调错能力**会在**调用当场**炸，而不是静默算错。
        """
        with unit_context(TRAIN_MOD):
            return self.rt.object_call(self.world, capability, method, *args, **kwargs)

    # ------------------------------------------------------------ 世界
    def resolve(self) -> None:
        """结算一个回合（native 的 `settle`），然后把阶段拨回"行动"。

        ★ 与 `engine/session.py:397` 同款：`resolve_turn()` 之后引擎把阶段置成"已结算"，
          行动前必须拨回"行动"，否则 `diplomacy.submit` 会拒收（"Submit only during action phase"）。
        """
        self.world.resolve_turn()
        self.world.phase = "行动"

    def period(self) -> int:
        """当前回合号 = `native:government.period()`（**引擎的期号**，不自记）。"""
        return self.call("native:government", "period")

    def size(self) -> int:
        """地图边长。"""
        return self.call("native:territory", "dimensions")[0]

    # ------------------------------------------------------------ 国家
    # ★ **词汇边界**：Env 对外说**中文短名**（甲/乙），对引擎说**内容 ID**（`rl:n0`）。
    #   换词是**纯函数**（`rl/nations.py`），不是状态。

    def id_of(self, name: str) -> str:
        """短名 → 引擎内容 ID（`甲` → `rl:n0`）。

        ★ 不是 RL 国家（引擎自带的归属，如"野人"）**原样返回** ——
          那是引擎自己的词，不该被我们的词表改写。
        """
        try:
            return N.ident(name)
        except KeyError:
            return name

    def name_of(self, ident: str) -> str:
        """引擎内容 ID → 短名（`rl:n0` → `甲`）。"""
        return N.name(ident)

    def nations(self) -> tuple:
        """**在册**国家（活着的，短名）。亡国的由 `native:succession` 裁决后移出。"""
        return tuple(self.name_of(i) for i in self.call("native:nation-inspection", "ids"))

    def nation(self, name: str):
        """国家对象（引擎的 `Nation`：`.id` / `.name` / `.alive` / …）。"""
        return self.call("native:nation-inspection", "get", self.id_of(name))

    def display(self, name: str) -> str:
        """国家的中文名（引擎 `Nation.name` 里存的就是"甲/乙"）。"""
        return self.nation(name).name

    def is_alive(self, name: str) -> bool:
        """这个国家还在不在（引擎说了算：查在册名单）。"""
        return self.id_of(name) in self.call("native:nation-inspection", "ids")

    # ------------------------------------------------------------ 军队
    def armies(self) -> tuple:
        """全表军队（**真值**）：引擎的 `Unit` 对象，**不是**重塑的 dict。

        ★ 身份用 `u.gid`（全局唯一），**不用** `u.id`（本国序号）——
          后者在多国时**会撞**（甲的第 1 支与乙的第 1 支都是 `id=1`）。
        """
        return self.call("native:army", "snapshot")

    def owner_of(self, unit) -> str:
        """这支军的主人（**短名**；引擎自带的归属如"野人"原样返回）。

        ★ 吃**两种形状**：
          · 引擎的 `Unit`（`unit.owner` 是**内容 ID**）⇒ 换算成短名；
          · **观测行**（`{"owner": 短名}`，`encode.window_armies` 产的）⇒ 原样返回。
        """
        if isinstance(unit, dict):
            return unit.get("owner")
        return self.name_of(unit.owner)

    def owns(self, unit, name: str) -> bool:
        """这支军是谁的？

        ★ 原生 `Unit.owner` 是**内容 ID**（引擎的词汇），而 RL 侧说短名 ⇒
          归属判断**统一走这里**，别在别处写 `unit.owner == name`
          （那会永远为假，而且**不报错**）。
        """
        return unit.owner == self.id_of(name)

    def visible_armies(self, owner: str) -> tuple:
        """`owner` **看得见**的军队（引擎的 `visible_units`）。

        ★ 含引擎**现场合成**的野人（无主且未驻过军的可见格）——
          野人的口径**完全按引擎**，RL 侧不再自己造（review P1-6）。
        """
        return self.call("native:army", "visible_units", self.id_of(owner))

    def unit(self, owner: str, aid: int):
        """按**本国序号**找一支军（`None` = 没有）。

        ★ `aid` 是**本国序号**（引擎的 `Unit.id`）—— 跨实体唯一要用 `Unit.gid`。
        """
        ident = self.id_of(owner)
        for unit in self.armies():
            if unit.owner == ident and unit.id == aid:
                return unit
        return None

    def reachable(self, owner: str, unit, for_attack: bool = False) -> dict:
        """该军本回合**走得到**的格 → 累计代价（**引擎的可达集**）。

        ★ 直接转达 `native:army.reachable`（引擎自己算路径与代价）。
          RL 侧**不重算**可达性（重算必然和引擎漂移，而且漂了不报错）。
        ★ 含起点（代价 0）—— 引擎的返回里本来就有。
        """
        pairs = self.call("native:army", "reachable", self.id_of(owner), unit.id,
                          attack=for_attack)
        return {(int(x), int(y)): int(cost) for (x, y), cost in pairs}

    # ------------------------------------------------------------ 地块
    def tile(self, x: int, y: int) -> TileRow:
        """一格的读模型（**现算**）。归属是**短名**（Env 的词汇）。"""
        fact = self.call("native:territory", "cell", int(x), int(y))
        return TileRow(
            position=(int(x), int(y)),
            owner=self.name_of(fact.owner) if fact.owner else None,
            terrain=self.call("native:territory", "terrain", int(x), int(y)),
            core=self.name_of(fact.tile.core) if fact.tile.core else None,
            tile_id=fact.tile.id,
            name=fact.tile.name or "",
            buildings=self.buildings_at(fact.tile.id) if fact.tile.id else {},
        )

    def tiles(self) -> tuple:
        """全图读模型（**现算**，不缓存）。

        ★ 走 `territory.cell` 逐格读 ⇒ **未物化的格也在**（引擎现场合成一个
          `id=''` 的事实）—— 与旧口径"全图一张表"一致，但**不建影子表**。
        ★ 建筑只取**一次** `buildings.snapshot`（逐格取会白翻 N 次快照）。
        """
        size = self.size()
        counts = self._building_counts()
        out = []
        for x in range(size):
            for y in range(size):
                fact = self.call("native:territory", "cell", x, y)
                site = fact.tile.id
                out.append(TileRow(
                    position=(x, y),
                    owner=self.name_of(fact.owner) if fact.owner else None,
                    terrain=self.call("native:territory", "terrain", x, y),
                    core=self.name_of(fact.tile.core) if fact.tile.core else None,
                    tile_id=site,
                    name=fact.tile.name or "",
                    buildings=counts.get(site, {})))
        return tuple(out)

    def tile_at(self, cell) -> TileRow:
        """单格读模型（**现算**）。"""
        return self.tile(int(cell[0]), int(cell[1]))

    def _building_counts(self) -> dict:
        """`{站点 id: {建筑 kind: 数量}}`（**现算**一次）。"""
        out: dict = {}
        for item in self.call("native:buildings", "snapshot").instances:
            out.setdefault(item.site, {})
            out[item.site][item.kind] = out[item.site].get(item.kind, 0) + 1
        return out

    def owner(self, x: int, y: int):
        """这一格归谁（**短名**；`None` = 无主）。"""
        ident = self.call("native:territory", "owner", int(x), int(y))
        return self.name_of(ident) if ident else None

    def terrain(self, x: int, y: int):
        """这一格的地形（引擎的 `Terrain`）。"""
        return self.call("native:territory", "terrain", int(x), int(y))

    def neighbors(self, x: int, y: int) -> tuple:
        """相邻格（引擎的 8 邻域口径）。"""
        return self.call("native:territory", "neighbors", int(x), int(y))

    def owned(self, owner: str) -> tuple:
        """该国拥有的地块（**读模型**序列，归属是短名）。"""
        return tuple(self.tile(x, y) for (x, y) in
                     ((f.tile.position for f in self.call("native:territory", "owned", self.id_of(owner)))))

    # ------------------------------------------------------------ 建筑
    def buildings_at(self, site_id: str) -> dict:
        """一处站点上的建筑 → `{kind: 数量}`（**现算**）。"""
        if not site_id:
            return {}
        snapshot = self.call("native:buildings", "snapshot")
        counts: dict = {}
        for item in snapshot.instances:
            if item.site == site_id:
                counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts

    def has_townhall(self, owner: str) -> bool:
        """该国还有没有**已落成的市政厅**（国祚）。

        ★ 判据用引擎的 `buildings.has_role(site,'capital')` —— 引擎自己的口径
          （`native:succession` 判亡国用的是同一条）。
        """
        return self.core_of(owner) is not None

    def core_of(self, owner: str):
        """该国的**市政厅格**（= 核心 = 国祚）；`None` ⇒ 已亡。"""
        for fact in self.call("native:territory", "owned", self.id_of(owner)):
            if fact.tile.id and self.call("native:buildings", "has_role", fact.tile.id, "capital"):
                return tuple(fact.tile.position)
        return None

    # ------------------------------------------------------------ 视野
    def visible(self, owner: str, x: int, y: int) -> bool:
        """这一格对 `owner` 可见吗（引擎的 `native:vision`）。"""
        return bool(self.call("native:vision", "visible", self.id_of(owner), int(x), int(y)))

    def vision_mask(self, owner: str) -> set:
        """`owner` 的可见格集合（**现算**）。

        ★ 与 `native:vision.tiles(owner)` 同源；这里补上**自己的格**
          （引擎的视野是"周围 3×3"，自己的格当然看得见）。
        """
        ident = self.id_of(owner)
        mask = {tuple(t.position) for t in self.call("native:vision", "tiles", ident)}
        for unit in self.armies():
            if unit.owner == ident and unit.alive:
                mask.add((unit.x, unit.y))
        return mask

    # ------------------------------------------------------------ 外交
    def war_between(self, a: str, b: str) -> bool:
        return bool(self.call("native:diplomacy", "war_between", self.id_of(a), self.id_of(b)))

    def allied(self, a: str, b: str) -> bool:
        return bool(self.call("native:diplomacy", "allied", self.id_of(a), self.id_of(b)))

    def entity(self, nation: str) -> str:
        """国家所属的**外交实体**（`nation/xxx` 或 `bloc/xxx`）。"""
        return self.call("native:diplomacy", "entity", self.id_of(nation))

    def entity_members(self, entity: str) -> tuple:
        """实体里的国家名单（**短名**）。"""
        return tuple(self.name_of(n) for n in self.call("native:diplomacy", "members", entity))

    def alliance_of(self, nation: str):
        """国家所属的联盟（`None` = 没结盟）。"""
        return self.call("native:diplomacy", "alliance", self.id_of(nation))

    # ------------------------------------------------------------ 真值 / 派生
    def defense_pct(self, x: int, y: int, owner):
        """这一格的总防御% —— **转调 `rl:truth`**（native 的公式）。"""
        return int(self.call("rl:truth", "defense_pct", int(x), int(y),
                             self.id_of(owner) if owner else None))

    def defenders(self, x: int, y: int, owner: str) -> tuple:
        """这一格上与 `owner` 敌对的守军（native 看到的，含合成野人）。"""
        return self.call("rl:truth", "defenders", int(x), int(y), self.id_of(owner))

    def barbarian_cells(self, observer: str | None = None) -> set:
        """哪些格**有野人驻守**。

        ★ 口径 = **引擎的野人**，分两种视角：
          · `observer` 给了 ⇒ **该方看得见的**野人（观测层用这个）；
          · 不给 ⇒ **全局真值**（打分层用这个：真野人 + 引擎会现场合成的那些格）。
        ⇒ 直接问 `visible_units`（引擎自己合成），**不自己造野人**（review P1-6）。
        """
        out = set()
        for unit in self.call("native:army", "snapshot"):
            if unit.owner == BARBARIAN and unit.alive:
                out.add((unit.x, unit.y))
        for nation in (self.nations() if observer is None else (observer,)):
            for unit in self.visible_armies(nation):
                if unit.owner == BARBARIAN:
                    out.add((unit.x, unit.y))
        return out

    def frontier_of(self, owner: str) -> set:
        """某国领地的**外缘格**（相邻的無主/敌格）。

        ★ 用引擎的 `owned` + `neighbors` + `owner` **现算**（不建表）。
        """
        out = set()
        for fact in self.call("native:territory", "owned", self.id_of(owner)):
            x, y = fact.tile.position
            for nx, ny in self.neighbors(x, y):
                if self.owner(nx, ny) != owner:
                    out.add((int(nx), int(ny)))
        return out

    # ------------------------------------------------------------ 动作
    def move(self, owner: str, aid: int, x: int, y: int) -> tuple:
        """移动一支军 → `(ok, msg)`。

        ★ **拒绝**与**错误**要分清（review §8 的红线 ③）：
          · 引擎**拒绝**这个动作（`DomainRejection`，带理由）⇒ 翻成 `(False, 理由)`，
            这是**正常结果**（模型要从这句理由里学到东西）；
          · 其它异常（TypeError、属性错误……）⇒ **照常抛出** ——
            那是**真错误**，旧门面的 `except Exception: return False, str(exc)`
            把它伪装成"引擎拒了"，**静默**掉了 ⇒ review P1-3 要求删掉。
        """
        try:
            receipt = self.call("native:army", "move", self.id_of(owner), int(aid),
                                int(x), int(y))
        except DomainRejection as exc:
            return False, str(exc)
        return _ok(receipt)

    def attack(self, owner: str, aids, x: int, y: int) -> tuple:
        """进攻一格 → `(ok, msg)`。"""
        try:
            receipt = self.call("native:army", "attack", self.id_of(owner),
                                [int(a) for a in aids], int(x), int(y))
        except DomainRejection as exc:
            return False, str(exc)
        return _ok(receipt)

    def retreat(self, owner: str, aids, x: int, y: int) -> tuple:
        """撤退 → `(ok, msg)`（native 的 `retreat` 是"提交"，结算后才真撤）。"""
        try:
            receipt = self.call("native:army", "retreat", self.id_of(owner),
                                [int(a) for a in aids], int(x), int(y))
        except DomainRejection as exc:
            return False, str(exc)
        return _ok(receipt)

    def disband(self, owner: str, aid: int) -> tuple:
        try:
            receipt = self.call("native:army", "disband", self.id_of(owner), int(aid))
        except DomainRejection as exc:
            return False, str(exc)
        return _ok(receipt)

    # ------------------------------------------------------------ 摆局
    def reset_map(self, size: int, seed: int) -> None:
        self.call("rl:sandbox", "reset", int(size), int(seed))

    def place_nation(self, ident: str, name: str, sites: dict) -> None:
        self.call("rl:sandbox", "place_nation", ident, name, sites)

    def place_nation_short(self, name: str, sites: dict, core=None, terrain=None) -> None:
        """按**短名**建国家并铺地（`name` = 甲/乙 ⇒ 内容 ID 由 `rl/nations.py` 推）。"""
        self.call("rl:sandbox", "place_nation", self.id_of(name), name, sites, core, terrain)

    def seed_building(self, site_id: str, kind: str, count: int = 1) -> None:
        self.call("rl:sandbox", "seed_building", site_id, kind, int(count))

    def spawn(self, owner: str, kind: str, x: int, y: int, count: int = 1):
        """**开局摆兵**（native 的 `seed`：初始化/导入 ⇒ 本回合可动）。"""
        return self.call("rl:sandbox", "spawn", self.id_of(owner), kind, int(x), int(y), int(count))

    def recruit(self, owner: str, site: str, kind: str, count: int = 1):
        """**回合内补员**（native 的 `recruit`：征召 ⇒ **本回合不能动**）。"""
        return self.call("rl:sandbox", "recruit", self.id_of(owner), site, kind, int(count))

    def recruit_quote(self, owner: str, site: str, kind: str, count: int = 1) -> dict:
        """问引擎"还能征几支"（**native 的额度**：每兵营每期 1 支；民兵另有全国编制）。"""
        return self.call("rl:sandbox", "recruit_quote", self.id_of(owner), site, kind, int(count))

    def purge_armies(self, owner: str) -> None:
        self.call("rl:sandbox", "purge_armies", self.id_of(owner))

    def declare_war(self, a: str, b: str) -> None:
        self.call("rl:sandbox", "declare_war", self.id_of(a), self.id_of(b))

    def declare_wars(self, pairs) -> None:
        """★ **批量宣战**（恒定 2 次结算，与国家数无关）。"""
        self.call("rl:sandbox", "declare_wars",
                  [(self.id_of(a), self.id_of(b)) for a, b in pairs])

    def set_alliance(self, members, name: str) -> None:
        self.call("rl:sandbox", "set_alliance", [self.id_of(m) for m in members], name)

    def setup(self, size: int, seed: int, nations, territory_target: int = 0,
              armies: int = 0, kind: str = "native:infantry",
              random_starts: bool = True, starts: dict | None = None) -> dict:
        """摆一局（**转调剧本能力** `rl:scenario`）。

        ★ 摆局的**口径**归剧本管（十字开局、强制平原核心、扩地、兵营/城堡/市政厅），
          这里只做转发 ⇒ 剧本与沙盒**不会各摆各的**。
        """
        return self.call("rl:scenario", "setup", int(size), int(seed),
                         [self.id_of(n) for n in nations],
                         int(territory_target), int(armies), kind, bool(random_starts),
                         {self.id_of(k): tuple(v) for k, v in (starts or {}).items()})

    # ------------------------------------------------------------ 战斗概率
    def assess(self, owner: str, aids, x: int, y: int) -> dict:
        """战斗胜算（**转调 `research:combat-probabilities`**，不再 fork 一份）。"""
        return self.call("rl:odds", "assess", self.id_of(owner),
                          [int(a) for a in aids], int(x), int(y))


def _ok(receipt) -> tuple:
    """把引擎的回执翻成 `(ok, msg)`。

    ★ 成功的口径 = `effective` / `submitted`（引擎自己的状态词）。
      旧门面写的是 `status not in ("rejected","failed","")` —— 那会把
      **`not_executed`**（没执行）当成成功 ⇒ 废动作被当成有效动作。
    """
    status = getattr(receipt, "status", "")
    message = getattr(receipt, "message", "") or ""
    return status in ("effective", "submitted"), message
