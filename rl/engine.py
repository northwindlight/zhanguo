# -*- coding: utf-8 -*-
"""**沙盒世界适配层**：把 `main` 的新引擎包成 RL 线熟悉的 `world.*` 门面。

    为什么需要这一层
    ────────────────
    `feat/rl-tactics` 是在**旧引擎**（`mp.py` 单体 + `game.py` + `balance.py`）上写的。
    2026-09/10 的主线重构把那两个文件**整体删掉**，换成了：

      · `engine/`      —— 通用引擎（`Runtime` / `Session` / 状态所有权 / 阶段结算）
      · `mods/native/` —— **真正的战国游戏**（领土 / 军队 / 视野 / 外交 / 建筑 / 国祚）
      · `ruleai/`      —— 规则 AI 客户端（拿的是 `PlayerSession`，不再是 `world`）

    本模块**只做一件事**：用新引擎造一个世界，再把**旧引擎那套 `world.*` 门面**
    重新架上去 —— 让 `rl/sandbox.py`、`rl/encode.py`、`rl/evaluate.py`、
    `rl/military.py` 这些**算法层**一行都不用改就能接着跑。

    ★ **这不是"复刻旧引擎"**：门面底下跑的**就是主线那套真规则**
      （同一份 `UNIT_TYPES` / `MOVE_COST` / `COMBAT_DIE_MOD`、同一套
      战斗结算与占地规则）。门面只是**改名**：新引擎讲"内容 ID"
      （`native:infantry` / `native:plain`），RL 线讲"中文短名"
      （`步` / `平原`）—— 短名是 RL 线的**观测词表**（`rl/vocab.py`），
      换掉它等于把已经训出来的网络的输入维度全改了。⇒ 门面在**边界上**做翻译。

    ★ 门面上的**语义差异**都记在对应方法的 docstring 里；**凡是"看起来一样但
      其实不一样"的地方都标了 ⚠** —— 那些是旧线与主线本来就不同的地方，
      不是本层的 bug。
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------- 路径与加载
ROOT = Path(__file__).resolve().parent.parent

# ★ 沙盒用的引擎发行版：**只要主线那套原生模块**（`mods/native`）——
#   领土 / 军队 / 视野 / 外交 / 建筑 / 国祚 / 政府 / 国家 / 资源 / 地图生成。
#   不含 `mods/experimental`（研究用的概率推演，由规则 AI 私有注入，不进沙盒）。
_MODS_TOML = ROOT / "mods.toml"

# ★★ **运行时缓存**：新引擎的 `Runtime.load` 要**解析全部发行版清单**并建好
#   所有 Mod 对象（实测 **123 ms/次**）。沙盒每局都要建一个世界 ⇒
#   **每建一局就 load 一次**的话，RL 训练（几千局）会**慢到不能跑**。
#   ⇒ 这里**只 load 一次**，之后每个世界用 `runtime.world_class()()` 造。
#
# ★ **为什么安全**：`Runtime` 本身**不含世界状态**（状态归 `state_owners`，
#   那是**每个世界自己的**）；实测同一 Runtime 造两个世界，
#   互不影响（`w1.territory.configure(8,…)` 不会串到 `w2`）。
_RUNTIME = None


def _runtime():
    """取（并缓存）主线发行版的 `Runtime`。"""
    global _RUNTIME
    if _RUNTIME is None:
        from engine.runtime import Runtime
        _RUNTIME = Runtime.load(_MODS_TOML)
    return _RUNTIME

# ★ 沙盒的**国家标识**：新引擎要求 owner 是**内容 ID**（`engine/identifiers.py`
#   的正则：`[a-z][a-z0-9_.-]*:[a-z][a-z0-9_./-]*` —— **必须全 ASCII 小写**），
#   而 RL 线的国家名是"甲/乙/丙"（见 `rl/vocab.py:PLAYER_NAMES`）—— 中文不能进 ID。
#   ⇒ 引擎侧用 `rl:n0` / `rl:n1` 这类**序号 ID**，门面维护
#   **双向映射**（短名 ↔ ID），让 RL 代码看到的仍然是"甲"。
#   ★ 为什么用序号而不是短名的转写：短名可读但要编解码（"甲"→"jia"），
#     序号 ID 零歧义、零编解码，且**跨局稳定**（同一个短名永远是同一个 ID）。
_NATION_PREFIX = "rl:"


class _NationRegistry:
    """短名 ↔ 内容 ID 的双向映射（门面内部用）。"""

    def __init__(self):
        self._to_id: dict[str, str] = {}
        self._to_name: dict[str, str] = {}

    def ident(self, name: str) -> str:
        ident = self._to_id.get(name)
        if ident is None:
            ident = f"{_NATION_PREFIX}n{len(self._to_id)}"
            self._to_id[name] = ident
            self._to_name[ident] = name
        return ident

    def name(self, ident: str) -> str:
        return self._to_name.get(ident, ident)

    def known(self, ident: str) -> bool:
        return ident in self._to_name


# ★ 进程级注册表：一个沙盒世界里建过的国家，跨 `SandboxWorld` 实例复用同一 ID ——
#   引擎的存档/结算按 ID 认国家，ID 必须稳定。
_REGISTRY = _NationRegistry()


# ---------------------------------------------------------------- 词表翻译
# ★★ **中文短名 ↔ 内容 ID**：RL 线的观测词表（`rl/vocab.py`）用短名
#   （兵种"步/骑/民"、地形"平原/森林/丘陵/山地/沙漠"），新引擎全程用内容 ID。
#   两边的对应关系**是主线重构时定的**（见 `mods/native/army/parameters.py`
#   与 `mods/native/territory/mod.py`），这里只做**一一对应**，不新增规则。
#   ★ 翻译表住在 `rl/tokens.py`（`rl/rules.py` 也要用 ⇒ 不能塞这里，否则成环）。
from . import rules
from .tokens import (BUILDING_NAME, BUILDINGS, CASTLE, CROSS, KIND_OF,
                     KIND_OF_NAME, TERRAIN_OF, TOWN_HALL, UNIT_KINDS)


def _kind_of(unit: Any) -> str:
    """单位 → **中文短名**（`步`/`骑`/`民`）。未知兵种回落到"步"。"""
    kind = unit.kind if hasattr(unit, "kind") else unit.get("kind", "native:infantry")
    return KIND_OF.get(kind, "步")


def _terrain_of(terrain_id: str) -> str:
    return TERRAIN_OF.get(terrain_id, terrain_id)


class _BlocList(list):
    """★ `world.blocs` 的**活视图** —— 读走引擎，写（`append`）也走引擎。

    旧引擎：`w.blocs` 是真列表，`w.blocs.append({...})` 直接建联盟。
    新引擎：联盟在 `diplomacy` 域（`bloc_found` + 各成员 `accept_proposal`）。
    ⇒ `append` 翻译成**建盟流程**（门面 `set_alliance`），读则是引擎现拼。
    """

    def __init__(self, world: "SandboxWorld"):
        super().__init__()
        self._world = world

    def __iter__(self):
        return iter(self._world._bloc_snapshot())

    def __len__(self):
        return len(self._world._bloc_snapshot())

    def __getitem__(self, index):
        return self._world._bloc_snapshot()[index]

    def __contains__(self, item):
        return any(b.get("name") == (item.get("name") if isinstance(item, dict) else item)
                   for b in self._world._bloc_snapshot())

    def append(self, bloc: dict):
        """★ 建盟（旧口径：直接往列表里塞）。"""
        members = list(bloc.get("members", []))
        name = bloc.get("name", "联盟")
        ok, msg = self._world.set_alliance(members, name)
        if not ok:
            raise ValueError(f"建盟失败：{msg}")

    def extend(self, blocs):
        for bloc in blocs:
            self.append(bloc)

    def __repr__(self):
        return repr(self._world._bloc_snapshot())


class _UnitDict(dict):
    """★ 一支军的**旧口径 dict**（**写穿透**到引擎里那支军）。

    旧引擎的 `world.armies` 就是**真值表**（军队就是 dict），
    RL 线与测试会**就地改**它（`a["hp"] = 5`）。
    ⇒ 这里改字段会**同步改到引擎的 `Unit`**：
      · `hp` / `x` / `y` / `moved_turn` / `engaged` ⇒ 直接写引擎；
      · `id` / `gid` / `owner` / `type` ⇒ 引擎**明文不许改**
        （"已登记部队的编号、归属与兵种不能直接改写"）⇒ 写本地、并在
        `_refresh` 时被引擎真值**覆盖**（避免"以为改了、其实没改"）。
    """

    # ★ 这些字段能写穿透到引擎（其余是"身份"，改不了）。
    _WRITABLE = ("hp", "x", "y", "moved_turn", "engaged")

    def __init__(self, world: "SandboxWorld", unit):
        super().__init__()
        self._world = world
        self._unit = unit
        self._refresh()

    def _refresh(self) -> None:
        """把引擎里的**当下真值**刷进这个 dict（身份不变、字段更新）。"""
        unit = self._unit
        dict.__setitem__(self, "id", unit.id)
        dict.__setitem__(self, "gid", unit.gid)
        dict.__setitem__(self, "owner",
                         self._world._name(unit.owner) if unit.owner != "野人" else "野人")
        dict.__setitem__(self, "type", _kind_of(unit))
        dict.__setitem__(self, "name", unit.name)
        dict.__setitem__(self, "hp", unit.hp)
        dict.__setitem__(self, "x", unit.x)
        dict.__setitem__(self, "y", unit.y)
        dict.__setitem__(self, "moved_turn", unit.moved_turn)
        dict.__setitem__(self, "engaged", unit.engaged)

    def __setitem__(self, key, value):
        if key in self._WRITABLE:
            try:
                self._unit[key] = value
            except Exception:
                pass                      # 引擎拒了就只改本地（别让观测崩掉）
        else:
            dict.__setitem__(self, key, value)
        self._refresh()

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value


class _ArmyList(list):
    """★ `world.armies` 的**活视图** —— 读走引擎，写也走引擎。

    旧引擎：`w.armies` 是真列表，`w.armies[:] = [...]` 能**删军队**
    （RL 线的测试与 `combat_noise_probe` 都靠这个改世界）。
    新引擎：军队在 `units` 域里，删除要走 `military.disband_many`。
    ⇒ 这里的**写**翻译成"**解散列表里没出现的那些军队**"，
      而不是只改字典（否则"我方军队没了"其实还在引擎里 ⇒ 分叉）。
    """

    def __init__(self, world: "SandboxWorld"):
        super().__init__()
        self._world = world

    # -- 读 --
    def __iter__(self):
        return iter(self._world._army_snapshot())

    def __len__(self):
        return len(self._world._army_snapshot())

    def __getitem__(self, index):
        return self._world._army_snapshot()[index]

    def __contains__(self, item):
        return item in self._world._army_snapshot()

    # -- 写 --
    def __setitem__(self, index, value):
        if isinstance(index, slice):
            self._sync(value)
            return
        items = self._world._army_snapshot()
        items[index] = value
        self._sync(items)

    def __delitem__(self, index):
        items = self._world._army_snapshot()
        del items[index]
        self._sync(items)

    def append(self, item):
        """★ 造一支**真**军队（引擎里 `military.seed` 出来）。

        ⚠ 旧引擎的 `w.armies.append({...})` 是直接往列表里塞一条 dict；
          新引擎的军队在 `units` 域里 ⇒ 这里**真去引擎建**，
          再把调用方给的 dict 里的额外字段（`hp` / `engaged` 等）覆盖上去。
        ★ `engaged`（"正在交战"）尤其要带过去：RL 的战斗概率层
          用它认"谁是进攻方"（`combat_probs.build` 的入口条件）。

        ⚠⚠ **`military.seed` 返回的是 `deepcopy`**（不是活对象）！
          ⇒ 想改字段（`hp` / `engaged`）**必须回表里找那支军**，
          否则改的是副本、世界上的真军没动（而且**不报错**）。
        """
        kind = item.get("type", "步")
        x, y = int(item.get("x", 0)), int(item.get("y", 0))
        owner = item.get("owner", "野人")
        ident = self._world._id_of(owner)
        native = self._world.native
        # ★★ **别重复造**：调用方常常是「先 `_new_army` 造一支（拿 gid），
        #   再 `armies.append({...})` 补细节」—— 旧引擎里 `_new_army` **只造不返**、
        #   `append` 才是"塞进表里"，两步**只造一支**。
        #   新引擎的 `seed` 每次都**真造一支** ⇒ 不去重就会**造出两支**。
        #   ⇒ 带了 `gid` 且引擎里**已经有这支军** ⇒ 复用，不再造。
        gid = item.get("gid")
        live = native.units.by_id(gid) if gid else None
        if live is None:
            seeded = native.military.seed(ident, UNIT_KINDS.get(kind, kind), x, y, count=1)
            live = native.units.by_id(seeded[0].gid)
        if live is None:
            return
        # ★ 位置/兵种也按调用方给的来（复用时引擎里那支可能位置不对）。
        try:
            if (live.x, live.y) != (x, y):
                live.x, live.y = x, y
        except Exception:
            pass
        if "hp" in item and item["hp"] != live.hp:
            try:
                live.hp = int(item["hp"])
            except Exception:
                pass
        if item.get("engaged"):
            try:
                live.engaged = True
            except Exception:
                pass
        if "moved_turn" in item:
            try:
                live.moved_turn = int(item["moved_turn"])
            except Exception:
                pass
        # ★ 队名：引擎会自己生成（`rl:n1·步7军`），但调用方可能给了**自己的**名字
        #   （测试里 `"乙rein"` 这种）⇒ 按调用方的来（引擎允许改 `name`）。
        if item.get("name"):
            try:
                live["name"] = item["name"]
            except Exception:
                pass
        # ★ **返回引擎里那支军的 dict**（门面按 gid 缓存 ⇒ 身份稳定）。
        #   调用方若要"我刚放下的那支军"，**必须用返回值**，不能用自己造的那个
        #   （自己造的那个是**另一个对象** ⇒ 按身份（`is`）认军队就会认错）。
        return self._world._as_dict(live)

    def extend(self, items):
        for item in items:
            self.append(item)

    def clear(self) -> None:
        """★ **清空军队**（旧口径：`w.armies.clear()`）—— **真删引擎里的**。

        ⚠ 不能只清本地列表：那样引擎里的军还在 ⇒ 读到的还是它们
          （"我清空了军队，怎么还有"）—— 那种错**不报错**。
        """
        self._sync([])

    def _sync(self, keep: list) -> None:
        """★ 让引擎里的军队与 `keep` 一致（**多出来的解散掉**）。"""
        keep_gids = {a.get("gid") for a in keep if a.get("gid") is not None}
        by_owner: dict[str, list[int]] = {}
        native = self._world.native
        for unit in native.units:
            if unit.gid in keep_gids:
                continue
            by_owner.setdefault(unit.owner, []).append(unit.id)
        for owner, ids in by_owner.items():
            try:
                native.military.disband_many(owner, ids)
            except Exception:
                pass

    def __repr__(self):
        return repr(self._world._army_snapshot())


class _TileDict(dict):
    """★ 一格地的**旧口径 dict**（`tile["owner"]` / `["buildings"]` / …）—— **写穿透**。

    旧引擎的 `world.tiles[(x, y)]` 就是**真值表**：RL 线（与测试）会**就地改**
    它（`t["owner"] = "甲"` = "这格归甲了"）。
    ⇒ 这里改 `owner` 会**真的**改到引擎（`territory.transfer`），
      改 `buildings` 会走 `_BuildingCounts`（见那里的"拆建筑"说明）。
    ★ 改 `core` / `terrain` / `name` **不写穿透**（引擎没有"改核心/改地形"的公开口）
      ⇒ 改了只改本地，下次刷新被引擎真值覆盖（不会"以为改了、其实没改"）。
    """

    def __init__(self, world: "SandboxWorld", fact, counts):
        super().__init__()
        self._world = world
        self._fact = fact
        self._counts = counts
        self._refresh()

    def _refresh(self) -> None:
        fact = self._fact
        dict.__setitem__(self, "owner",
                         self._world._name(fact.owner) if fact.owner else None)
        dict.__setitem__(self, "terrain", _terrain_of(fact.tile.terrain))
        dict.__setitem__(self, "buildings", self._counts)
        dict.__setitem__(self, "core", self._world._core_owner(fact.tile.position,
                                                                fact.owner))
        dict.__setitem__(self, "name", fact.tile.name)
        dict.__setitem__(self, "id", fact.tile.id)

    def __setitem__(self, key, value):
        if key == "owner":
            # ★ 换归属 = **真的**转给那国（引擎的 `territory.transfer`
            #   会连建筑一起转过去）。
            ident = self._world._id(value) if value else None
            try:
                self._world.native.territory.transfer(self._fact.tile.id, ident)
            except Exception:
                pass
            self._world._core_owners[tuple(self._fact.tile.position)] = value
            self._world._invalidate_tiles()   # ★ 归属变了 ⇒ 快照失效
            return
        dict.__setitem__(self, key, value)

    def get(self, key, default=None):
        return dict.get(self, key, default)


class _BuildingCounts(dict):
    """★ 一格上的建筑**计数表**（旧口径：`tile["buildings"]`）—— **写穿透**。

    旧引擎的 `tile["buildings"]` 是**真字典**：`{"市政厅": 1, "城堡": 0, …}`，
    RL 线（与测试）会**就地改**它（`t["buildings"]["市政厅"] = 0` = "拔掉这座厅"）。
    ⇒ 这里改计数会**同步到引擎**：
      · 改成 0 ⇒ **拆掉**该格所有这一类建筑（`buildings` 域里删实例）；
      · 改成 N>0 ⇒ 拆到 N 座为止（不够就补种）。
    ★ 引擎侧的建筑是**实例列表**，这里是**计数** ⇒ 两边靠 `seed`/`instances` 对账。
    """

    def __init__(self, world: "SandboxWorld", site_id: str):
        super().__init__()
        self._world = world
        self._site = site_id
        self._refresh()

    def _refresh(self) -> None:
        """按引擎的**当下真值**刷新这张表（**再套上门面的"被拆掉"覆盖**）。"""
        buildings = self._world.native.buildings
        for name in BUILDINGS:
            dict.__setitem__(self, name, 0)
        for inst in buildings.instances(self._site):
            if inst.remaining:
                continue
            name = BUILDING_NAME.get(inst.kind, inst.kind)
            dict.__setitem__(self, name, dict.get(self, name, 0) + 1)
        # ★ 套覆盖（"门面这边被拆掉了几座"）。
        for kind, want in (self._world._building_override.get(self._site) or {}).items():
            name = BUILDING_NAME.get(kind, kind)
            dict.__setitem__(self, name, min(dict.get(self, name, 0), want))

    def __setitem__(self, key, value):
        want = int(value)
        buildings = self._world.native.buildings
        kind = KIND_OF_NAME.get(key, key)
        have = buildings.count(self._site, kind)
        if want > have:
            for _ in range(want - have):
                buildings.seed(self._site, kind, count=1)
            self._refresh()
            return
        if want < have:
            # ★★ **新引擎没有"拆建筑"的公开口**（`buildings` 域只能建、不能拆）
            #   ⇒ 门面**自己记一张"被拆掉多少"的覆盖表**，
            #   并让 `has_townhall` / 建筑计数**都按覆盖后的数算**。
            #   ★ 为什么需要这个：旧引擎的 `tile["buildings"]` 是**真字典**，
            #     RL 线（与测试）会**就地改**它（`t["buildings"]["市政厅"] = 0`
            #     = "拔掉这座厅"）⇒ 那个动作**必须真的生效**，
            #     否则"拔了厅却还判有厅" ⇒ 亡国判据失灵、而**不报错**。
            #   ⇒ 覆盖表是**门面自己的账**，与引擎的实例列表对账：
            #     引擎那边该建筑没了（被别的机制拆掉）⇒ 覆盖自动失效。
            self._world._building_override.setdefault(self._site, {})[kind] = want
        self._refresh()

    def __getitem__(self, key):
        return dict.__getitem__(self, key)

    def get(self, key, default=0):
        return dict.get(self, key, default)


class _NationMap(dict):
    """★ `world.nations` 的**活映射** —— 读走引擎，**删也走引擎**。

    旧引擎的 `world.nations` 是真字典：`nations.pop("甲")` = **那国没了**。
    ⇒ 这里 `pop` / `__delitem__` 会**真的把那国从引擎里除名**
      （`military.remove_nation` + 从在册名单里划掉），
      否则"模拟被灭国"只是从临时字典里划掉了 ⇒ 世界里那国还在。
    """

    def __init__(self, world: "SandboxWorld"):
        super().__init__()
        self._world = world

    # -- 读 --
    def __iter__(self):
        return iter(self._world._nation_snapshot())

    def __len__(self):
        return len(self._world._nation_snapshot())

    def __getitem__(self, key):
        return self._world._nation_snapshot()[key]

    def __contains__(self, key):
        return key in self._world._nation_snapshot()

    def get(self, key, default=None):
        return self._world._nation_snapshot().get(key, default)

    def keys(self):
        return self._world._nation_snapshot().keys()

    def values(self):
        return self._world._nation_snapshot().values()

    def items(self):
        return self._world._nation_snapshot().items()

    # -- 写（除名） --
    def __delitem__(self, key):
        self._world._remove_nation(key)

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        view = self._world._nation_snapshot()[key]
        self._world._remove_nation(key)
        return view

    def __repr__(self):
        return repr(self._world._nation_snapshot())


class _NationView:
    """★ 一个国家的**兼容视图**（旧口径：`world.nations[name]`）。

    旧引擎的 `Nation` 有 `res`（资源字典）；新引擎的资源在 `native:resources` 域。
    ⇒ 这里把 `res` 做成**活的**：读 = 问引擎，写 = 改引擎余额。
    """

    def __init__(self, world: "SandboxWorld", ident: str):
        self._world = world
        self._ident = ident

    @property
    def id(self) -> str:
        return self._ident

    @property
    def name(self) -> str:
        return self._world._name(self._ident)

    @property
    def res(self) -> dict:
        """★ **活的资源表**（中文短名 → 数量）。写它会**改到引擎余额**。"""
        return _ResourceView(self._world, self._ident)

    def __repr__(self):
        return f"<_NationView {self.name}>"


class _ResourceView(dict):
    """★ 资源表（中文短名 ↔ 内容 ID），**读写都落到引擎**。

    ⚠ 资源名是**中文**（`粮食`/`黄金`/…），引擎里是内容 ID
      （`native:food`/`native:gold`/…）⇒ 每次读写都翻译一次。
    """

    _NAMES = {
        "粮食": "native:food", "木头": "native:wood", "矿石": "native:ore",
        "石油": "native:oil", "装备": "native:equipment", "黄金": "native:gold",
        "补给": "native:supply",
    }
    _IDS = {v: k for k, v in _NAMES.items()}

    def __init__(self, world: "SandboxWorld", ident: str):
        super().__init__()
        self._world = world
        self._ident = ident

    def _balance(self, resource_id: str) -> int:
        try:
            return int(self._world._call("native:resources", "balance",
                                         self._ident, resource_id))
        except Exception:
            return 0

    def __getitem__(self, key):
        return self._balance(self._NAMES.get(key, key))

    def __setitem__(self, key, value):
        resource_id = self._NAMES.get(key, key)
        current = self._balance(resource_id)
        delta = int(value) - current
        if delta:
            self._world._call("native:resources", "exchange", self._ident,
                              {resource_id: delta}, {})

    def get(self, key, default=0):
        try:
            return self[key]
        except Exception:
            return default

    def __contains__(self, key):
        return key in self._NAMES or key in self._IDS

    def __iter__(self):
        return iter(self._NAMES)

    def keys(self):
        return list(self._NAMES)

    def items(self):
        return [(name, self[name]) for name in self._NAMES]


class _TileMap:
    """★ `world.tiles` 的**活视图** —— 读走引擎，写也走引擎。

    旧引擎的 `world.tiles` 是一张**真的** `{(x,y): dict}` 表：
    读 `t["owner"]` / `t["buildings"]`，写 `tiles[cell] = 新地块`。
    新引擎没有这张表 ⇒ 这里用 `MutableMapping` 把两种访问都接回引擎：

      · 读 → 每次现拼快照（`world._tile_snapshot()`）；
      · 写 → **真去引擎建那格**（`territory.create`），而不是只改字典 ——
        只改字典会让"门面说有这块地、引擎说没有"**分叉**，
        而那种错**不报错**（读到的都是门面那张表）。

    ⚠ 写只支持"**新建一格**"（旧线 `_claim` 的用法）：
      赋一个已存在的格会抛错 —— 归属/建筑的改动要走引擎自己的接口
      （`transfer` / `buildings.seed`），不该从这张表改。
    """

    def __init__(self, world: "SandboxWorld"):
        self._world = world

    # -- MutableMapping 协议 --
    def __getitem__(self, key):
        return self._world._tile_snapshot()[key]

    def __setitem__(self, key, value):
        cell = tuple(key)
        x, y = cell
        owner = value.get("owner")
        if owner is None:
            raise ValueError(f"地块 {cell} 的 owner 不能为空（门面只支持'建新格'）")
        # ★ 旧线的写法是「先 `_new_tile` 建好，再 `tiles[cell] = 那张 dict`」——
        #   也就是说**建格**发生在 `_new_tile` 里，赋值只是**存回去**。
        #   ⇒ 这里先问引擎"这格是不是已经物化了"：是 ⇒ 只存（不重复建）；
        #     否 ⇒ 真建（`territory.create`）。
        #   否则就会撞 `Duplicate tile identity or position`。
        if not self._world.has_cell(x, y):
            self._world._create_tile(x, y, owner)
        # ★★ **建筑也要跟着建**：旧线 `tiles[cell] = {"buildings": {"市政厅": 1}}`
        #   是一格"**带厅的地**"；门面只建空地的话，那座厅就没了
        #   （"每国两座厅"这类场景直接算错，而且**不报错**）。
        for name, count in (value.get("buildings") or {}).items():
            if count:
                self._world._seed_building(x, y, name, int(count))

    def __delitem__(self, key):
        raise TypeError("门面不支持删格（引擎没有这个操作）")

    def __iter__(self):
        return iter(self._world._tile_snapshot())

    def __len__(self):
        return len(self._world._tile_snapshot())

    def __contains__(self, key):
        return key in self._world._tile_snapshot()

    def get(self, key, default=None):
        return self._world._tile_snapshot().get(key, default)

    def items(self):
        return self._world._tile_snapshot().items()

    def values(self):
        return self._world._tile_snapshot().values()

    def keys(self):
        return self._world._tile_snapshot().keys()

    def __repr__(self):
        return f"<_TileMap {len(self)} 格>"


class SandboxWorld:
    """★ **沙盒用的 `world` 门面** —— 语义与旧 `mp.World` 对齐，底座是新引擎。

    只暴露 RL 线用到的那部分 `world.*`；**算法层照旧写**。
    """

    def __init__(self, size: int = 8, seed: int | None = None, *,
                 nations: list[str] | None = None,
                 starts: dict[str, tuple[int, int]] | None = None,
                 max_turns: int = 200):
        self.size = int(size)
        self.seed = int(seed if seed is not None else random.randrange(1 << 31))
        self.rng = random.Random(self.seed)
        self.max_turns = int(max_turns)
        self._turn = 0
        self._order: list[str] = []
        self._bloc_seq = 0
        # ★ 每格的"首任 owner"（旧口径 `tile["core"]`）—— 见 `_core_owner`。
        self._core_owners: dict[tuple[int, int], str | None] = {}
        # ★ 每支军的**稳定 dict 身份**（按 gid 缓存）—— 见 `_as_dict`。
        self._unit_dicts: dict[int, dict] = {}
        # ★ 地块快照缓存（见 `_tile_snapshot`）。
        self._tile_cache: dict[tuple[int, int], dict] | None = None
        # ★ "被拆掉的建筑"覆盖表（`{站点: {建筑种类: 剩余座数}}`）——
        #   新引擎**没有拆建筑的口**，而旧线的 `tile["buildings"]` 是**真字典**
        #   （`t["buildings"]["市政厅"] = 0` = "拔掉这座厅"）⇒ 门面自己记这笔账。
        self._building_override: dict[str, dict[str, int]] = {}
        # ★ 资源给足：沙盒**不走经济层**（补员自己算），但引擎的补给阶段
        #   每回合收军粮，断粮会扣 HP —— 资源给 0 ⇒ 军队是"饿死"的不是战死的。
        self._resources = {
            "native:gold": 100000, "native:food": 100000, "native:wood": 100000,
            "native:ore": 100000, "native:oil": 100000,
            "native:equipment": 100000, "native:supply": 1000000,
        }
        self._runtime = _runtime()          # ★ 复用（见 `_runtime` 的说明）
        self._world = self._runtime.world_class()()
        self._world.territory.configure(self.size, self.seed)
        self._nations: list[str] = []
        for name in (nations or []):
            self._add_nation(name)
        self._order = list(self._nations)
        self._starts = dict(starts or {})
        if not self._starts:
            # ★★ **没给 `starts` ⇒ 环状开局**（旧引擎的 `_place_ring`）。
            #   旧引擎的 `World(nations=[…])` 不给 `starts` 时，会按
            #   `HUNS_RING_RATIO`（边长 × 0.31）把各国摆成一个**环**。
            #   ⇒ 不复刻这条，RL 线的"三国局/四国局"（测试与训练都这么建）
            #   就会**一个核心都没有** ⇒ 没有国土、没有厅、分数恒为 0（而且**不报错**）。
            self._starts = self._ring_starts()
        # ★ 开局**十字**：中心格 + 上下左右（归属本国），中心额外送一座市政厅。
        #   旧引擎的 `World(nations=…, starts=…)` 就是这么干的（`_place_crosses`），
        #   新引擎没有这条自动规则 ⇒ 门面补上，RL 线的算法层就不用管。
        for name, center in self._starts.items():
            self.place_cross(name, center)

    def _ring_starts(self) -> dict:
        """★ **环状开局**（旧引擎 `_place_ring` 的口径）。

        半径 = 地图边长 × `HUNS_RING_RATIO`（旧 `balance.py` 的 0.31），
        各国按**均分角度**摆在环上（第一个在正上方）。
        """
        import math
        ratio = 0.31                      # ★ 旧 `balance.HUNS_RING_RATIO`
        n = max(1, len(self._nations))
        c = self.size / 2
        r = self.size * ratio
        out = {}
        for i, name in enumerate(self._nations):
            ang = -math.pi / 2 + i * 2 * math.pi / n
            out[name] = (int(round(c + r * math.cos(ang))),
                         int(round(c + r * math.sin(ang))))
        return out

    # ------------------------------------------------------------ 世界装配
    @staticmethod
    def _id(name: str) -> str:
        """国家短名 → 引擎内容 ID。"""
        return _REGISTRY.ident(name)

    @staticmethod
    def _id_of(name: str) -> str:
        """国家短名 → 引擎内容 ID（`_id` 的别名，给外部模块用）。"""
        return _REGISTRY.ident(name)

    @staticmethod
    def _name(ident: str) -> str:
        """引擎内容 ID → 国家短名（野人等非注册实体原样返回）。"""
        return _REGISTRY.name(ident)

    # ------------------------------------------------------------ 世界装配
    def __deepcopy__(self, memo):
        """★ **深拷贝**（旧口径：`copy.deepcopy(world)`）。

        ★★ **新引擎的世界不能直接 `deepcopy`**（里面挂着 `Runtime`、状态所有者、
          上下文变量 ⇒ 拷贝要么炸、要么拷出一份"看着像、其实不联动"的东西）。
        ⇒ 这里走引擎自己的**存档/读档**（`save_mod_state` / `load_mod_state`）：
        **逐个 Mod 存状态 → 新世界 → 逐个 Mod 读回来** ⇒ 副本与真身**状态一致**
        （而且**互不影响**）—— 这正是试演（MCTS 真实试演）要的。

        ★ 门面自己的那些"表"（`_unit_dicts` 缓存、`_core_owners`、`_building_override`）
          也得**各自独立** ⇒ 一并重置（缓存按 gid 重建即可）。
        """
        clone = SandboxWorld.__new__(SandboxWorld)
        memo[id(self)] = clone
        clone.size = self.size
        clone.seed = self.seed
        clone.rng = random.Random(self.seed)
        clone.max_turns = self.max_turns
        clone._turn = self._turn
        clone._order = list(self._order)
        clone._bloc_seq = self._bloc_seq
        clone._nations = list(self._nations)
        clone._starts = dict(self._starts)
        clone._core_owners = dict(self._core_owners)
        clone._building_override = {k: dict(v) for k, v in self._building_override.items()}
        clone._tile_cache = None
        clone._unit_dicts = {}
        # ★ 世界本体：同一个 `Runtime`（**无世界状态**，可安全共用）⇒ 新世界 + 读档。
        clone._runtime = self._runtime
        clone._world = self._runtime.world_class()()
        states = self._runtime.save_mod_state(self._world)
        self._runtime.load_mod_state(clone._world, states)
        clone._world.phase = self._world.phase
        return clone

    def _add_nation(self, name: str) -> str:
        ident = self._id(name)
        self._world.nations.create(ident, name, dict(self._resources))
        self._nations.append(name)
        return ident

    def _call(self, capability: str, method: str, *args, **kwargs):
        return self._runtime.object_call(self._world, capability, method, *args, **kwargs)

    # ------------------------------------------------------------ 开局布置
    def place_cross(self, name: str, center: tuple[int, int]) -> list[tuple[int, int]]:
        """★ **十字开局**：中心格 + 上下左右，各物化一格（归属 `name`）。

        ⚠ **中心格额外送一座市政厅**（国祚）—— 旧引擎的 `_place_crosses` 就是
        这么做的（"开局默认有个市政厅，在国家核心"），新引擎**没有**这条自动规则
        ⇒ 门面补上（沙盒的胜负判据就是"对方的厅还在不在"）。

        ⚠ **中心格是平原**：旧引擎把核心格的地形**强制成平原**
        （"中心格必然是平原"）。这里照做 —— 否则核心可能落在山地，
        守方天然减伤 50%，开局就不公平。
        """
        ident = self._id(name)
        placed: list[tuple[int, int]] = []
        for dx, dy in CROSS:
            x, y = center[0] + dx, center[1] + dy
            if not (0 <= x < self.size and 0 <= y < self.size):
                continue
            if self._world.territory.owner(x, y) is not None:
                continue
            site = f"{ident}/land-{x}-{y}"
            terrain = "native:plain" if (dx, dy) == (0, 0) else None
            self._world.territory.create(site, ident, x, y, terrain=terrain,
                                        core=ident if (dx, dy) == (0, 0) else None)
            # ★ 旧口径的"首任 owner"记一份（`tile["core"]`）。
            self._core_owners[(x, y)] = name
            if (dx, dy) == (0, 0):
                self._world.buildings.seed(site, TOWN_HALL)
            placed.append((x, y))
        self._invalidate_tiles()      # ★ 建了十字 ⇒ 快照失效
        return placed

    def seed_barbarian(self, x: int, y: int) -> None:
        """在一格**放一支野人**（守无主地 = 扩张成本）。

        ⚠ 新引擎的野人是**虚拟的**（`military.visible_units` 对无主格现场合成），
        所以这里**只是把这一格标成"已驻军"**（`guard_once`）——
        引擎的"可攻终点"判定要求格上有驻军（否则军队出不了自家十字）。
        """
        self._world.units.guard_once.add((x, y))

    # ------------------------------------------------------------ 基础读
    @property
    def native(self):
        """★ 底层的新引擎世界（门面之外要用原生能力时走这里）。"""
        return self._world

    @property
    def runtime(self):
        return self._runtime

    @property
    def turn(self) -> int:
        """当前回合号。★ 引擎的回合号是 `world.government.period()`。"""
        return self._turn

    @property
    def order(self) -> list[str]:
        """行动顺序（**短名**）。"""
        return list(self._order)

    @property
    def nations(self) -> dict:
        """★ **按国家名索引**的国家表（旧口径：`world.nations`）。

        ⚠ 旧引擎的 `world.nations` 是 **`{国名: Nation}` 字典**（不是列表）——
          RL 线两处都靠这个形状：`n in world.nations`（在册检查）与
          `world.nations[name].res[...]`（资源）。
          ⇒ 这里返回**字典**：`{短名: _NationView}`。

        ★ 亡国即除名（与旧引擎一致：亡国后 `name in self.nations` 为假）。
        ★ **可写**：`nations.pop("甲")` 会**真的把那国从引擎里除名**
          （旧引擎的 `nations` 是真字典，`pop` 就是除名）——
          "模拟被灭国"这类操作（测试与剧本都用）**必须真删**，
          否则只是"从临时字典里 pop 掉了"，世界里那国还在
          （⇒ 后面全错，而且**不报错**）。
        """
        return _NationMap(self)

    def _nation_snapshot(self) -> dict:
        """当前在册国家的**只读快照**（内部用）。"""
        out = {}
        for name in self._nations:
            ident = self._id(name)
            if ident not in self._alive_ids():
                continue
            out[name] = _NationView(self, ident)
        return out

    @property
    def blocs(self) -> list[dict]:
        """★ **联盟列表**（旧口径：`world.blocs`）—— **可写**。

        ⚠ 旧引擎的 `blocs` 是**真列表**（`w.blocs.append({...})` 建联盟），
          新引擎的联盟在 `diplomacy` 域里（走 `bloc_found` / `accept_proposal`）。
        ⇒ 这里给一个**兼容列表**：读 = 从引擎现拼；写（`append`）= 走引擎的
          建盟流程（`set_alliance`）。这样 RL 线的"开局直接指定联盟"那条
          口径（用户 2026-09-24：「开局直接指定，而不是让模型发起和接受」）
          仍然成立。
        """
        return _BlocList(self)

    def _bloc_snapshot(self) -> list[dict]:
        """当前联盟的**只读快照**（内部用）。"""
        out: list[dict] = []
        seen: set[str] = set()
        for ident in self._alive_ids():
            alliance = self._world.diplomacy.alliance(ident)
            if alliance is None or alliance.id in seen:
                continue
            seen.add(alliance.id)
            out.append({"name": alliance.id,
                        "chief": self._name(alliance.chief),
                        "members": [self._name(m) for m in alliance.members]})
        return out

    def _alive_ids(self) -> list[str]:
        try:
            return list(self._world.nations.ids())
        except Exception:
            return []

    # ------------------------------------------------------------ 地块
    def tile_terrain(self, x: int, y: int) -> str:
        """这一格的地形（**中文短名**）。"""
        return _terrain_of(self._world.territory.terrain(x, y).id)

    def owned_by(self, x: int, y: int) -> str | None:
        """这一格属于谁（**短名**；无主 = `None`）。"""
        owner = self._world.territory.owner(x, y)
        return self._name(owner) if owner else None

    def has_cell(self, x: int, y: int) -> bool:
        """这一格**是否已物化**（进了 `territory` 的表）。

        ⚠ 与 `owned_by` 不同：**无主**不等于**未物化** ——
      一格可以被物化但无主（被夺走后）。旧引擎的 `world.tiles` 只收**已物化**的格，
        所以"这格在不在表里"要问这个，而不是问归属。
        """
        return any(f.tile.position == (x, y) for f in self._world.territory.snapshot())

    def own_tiles(self, name: str) -> list[tuple[int, int]]:
        """某国拥有的全部地块坐标。"""
        return [f.tile.position for f in self._world.territory.owned(self._id(name))]

    @property
    def tiles(self) -> dict[tuple[int, int], dict]:
        """★ **旧口径的 `world.tiles`** —— `(x,y) -> {owner, terrain, buildings, core}`。

        ⚠ **这是门面里唯一一处"造旧形状"的地方**：新引擎把"地块"拆成了
        `territory`（归属/地形/核心）与 `buildings`（建筑实例）两个域，
        没有 `(x,y) -> dict` 这张表。RL 线（`encode.py` / `evaluate.py` /
        `military.py`）大量按 `t["owner"]` / `t["buildings"]` 读，
        ⇒ 这里**按需拼**出一张表（每次取属性重建，不缓存 ——
        缓存会与引擎状态脱节，而 RL 线的编码要求"每次观测都是当下的真值"）。

        ★ **可写**：旧线的 `sandbox._claim` 是 `self.world.tiles[cell] = …`
          （"物化一格"）。这里用 `_TileMap` 包一层：**赋值 = 真去引擎建那格**
          （`territory.create` + 建筑按需补），而不是只改字典 ——
          否则字典与引擎会**分叉**（读到"我有这块地"，引擎说没有）。
        """
        return _TileMap(self)

    def _core_owner(self, cell: tuple[int, int], owner: str | None) -> str | None:
        """★ 这一格的**核心领土**归属（旧口径：`tile["core"]`）。

        ⚠ **口径**：旧引擎的 `core` = **该格的"首任 owner"**
          （`_new_tile` 里写死 `"core": owner`；战争结束时 `_snapshot_cores`
          按参与者实占**重算**一遍）。
        ⚠ **与新引擎的 `Tile.core` 不同**：新引擎的 `Tile.core` 是
          "**这个站点是哪国的核心站点**"（开局十字的中心格才有），
          普通占来的地**没有** core。
        ⇒ 门面自己记一张"首任 owner"表（`_core_owners`），
          建格时写入，**与引擎的 `Tile.core` 无关**。
        """
        key = tuple(cell)
        if key not in self._core_owners:
            self._core_owners[key] = self._name(owner) if owner else None
        return self._core_owners[key]

    def _tile_snapshot(self) -> dict[tuple[int, int], dict]:
        """当前所有地块的**快照**（内部用）。

        ★★ **带缓存**（2026-10）：旧引擎的 `world.tiles` 是**一张真表**（读 O(1)），
          而门面每次访问都要**重建**（遍历所有地块 + 问建筑域）——
          实测 `obs_of` 一次要访问 **98 次** ⇒ **每步 67 ms**，训练直接跑不动。
          ⇒ 这里**缓存**快照，只在**状态真的变了**时重建。
        ★ 失效点**全在门面的写路径上**（`_invalidate_tiles`）：建格 / 转归属 /
          种建筑 / 结算（战斗会占地）—— RL 线对世界的写**全走门面**，
          所以这三处 + 结算就是**完备**的失效集合。
        """
        if self._tile_cache is None:
            self._tile_cache = self._build_tile_snapshot()
        return self._tile_cache

    def _invalidate_tiles(self) -> None:
        """★ 地块快照**失效**（状态变了 ⇒ 下次访问重建）。"""
        self._tile_cache = None

    def _build_tile_snapshot(self) -> dict[tuple[int, int], dict]:
        """当前所有地块的**只读快照**（内部用；门面每次现拼）。"""
        out: dict[tuple[int, int], dict] = {}
        buildings = self._world.buildings
        for fact in self._world.territory.snapshot():
            pos = fact.tile.position
            # ★ 旧口径：每个地块的 `buildings` **预置全部键**（没建就是 0）——
            #   新引擎是实例列表 ⇒ 这里补成"全键零值 + 实有的"。
            # ★ **写穿透**：改 `t["buildings"]["市政厅"] = 0`（"拔掉这座厅"）
            #   必须**真的**改到引擎（否则只是改了张快照 ⇒ 世界没变，且**不报错**）。
            counts = _BuildingCounts(self, fact.tile.id)
            out[pos] = _TileDict(self, fact, counts)
        return out

    def frontier_of(self, name: str) -> set[tuple[int, int]]:
        """某国领地的**外缘格**（相邻的無主/敌格）。"""
        out: set[tuple[int, int]] = set()
        owner = self._id(name)
        for fact in self._world.territory.owned(owner):
            x, y = fact.tile.position
            for nx, ny in self._world.territory.neighbors(x, y):
                cell_owner = self._world.territory.owner(nx, ny)
                if cell_owner != owner:
                    out.add((nx, ny))
        return out

    def has_townhall(self, name: str) -> bool:
        """★ 该国是否还有**已落成的市政厅**（国祚判定）。

        ⚠ **与旧引擎的差别**：新引擎的国祚由 `native:succession` 在结算阶段
        统一裁决（`settle()` 里那条 `has_role(site.id,'capital')`），
        门面这里做的是**同样的判定**，但**只读不裁决** ——
        沙盒要的是"现在还有没有厅"（用来判胜负），不是"谁被清除了"。
        """
        owner = self._id(name)
        for fact in self._world.territory.owned(owner):
            if not self._world.buildings.has_role(fact.tile.id, "capital"):
                continue
            # ★ 门面这边"被拆掉"的厅（见 `_BuildingCounts.__setitem__`）不算数。
            override = (self._building_override.get(fact.tile.id) or {}).get(TOWN_HALL)
            if override is not None and override <= 0:
                continue
            return True
        return False

    # ------------------------------------------------------------ 军队
    def armies_of(self, name: str) -> list[dict]:
        """某国的军队（旧口径：dict 列表）。"""
        return [self._as_dict(u) for u in self._world.units.of(self._id(name))]

    @property
    def armies(self) -> list[dict]:
        """★ **全部军队**（旧口径：dict 列表）—— **可写**。

        ⚠ 旧引擎的 `world.armies` 是**真列表**（`w.armies[:] = [...]` 能删军队），
          RL 线的测试与 `combat_noise_probe` 都靠这个改世界。
        ⇒ 这里给一个**活列表**：读 = 从引擎现拼；**写（`[:] =` / `=`）= 真删引擎里
          对应的军队**（`units.remove` / `disband`），不是只改字典 ——
          否则字典与引擎会**分叉**（"我方军队没了"其实还在）。
        """
        return _ArmyList(self)

    @armies.setter
    def armies(self, value) -> None:
        """★ 整表替换（旧口径：`w.armies = [...]`）—— 语义 = "删掉表外的军队"。"""
        _ArmyList(self)._sync(list(value))

    def _army_snapshot(self) -> list[dict]:
        """当前全部军队的**只读快照**（内部用）。"""
        return [self._as_dict(u) for u in self._world.units]

    @property
    def troops(self) -> list[dict]:
        """★ **非野人**的军队（旧口径：`world.troops`）。

        ★ 为什么要单列：寻路要判"这一格是不是**墙**"（有与我交战的他国驻军）。
          野人驻军**不是墙**（行军不打野人）⇒ 判墙时必须先把野人剔掉 ——
          旧引擎那张表叫 `troops`，新引擎的 `Units.troops` 是同一个口径。
        """
        return [self._as_dict(u) for u in self._world.units.troops]

    def nation_armies(self, name: str) -> list[dict]:
        """某国的军队（旧口径：`world.nation_armies`）。"""
        return self.armies_of(name)

    def neighbors(self, x: int, y: int) -> list[tuple[int, int]]:
        """八邻格（**顺序与引擎一致**：`dx` 外层、`dy` 内层）。"""
        return list(self._world.territory.neighbors(x, y))

    def bloc_of(self, name: str) -> dict | None:
        """该国所属的联盟（旧口径：`{'name','chief','members'}`）；无则 `None`。"""
        alliance = self._world.diplomacy.alliance(self._id(name))
        if alliance is None:
            return None
        return {"name": alliance.id,
                "chief": self._name(alliance.chief),
                "members": [self._name(m) for m in alliance.members]}

    def _as_dict(self, unit) -> dict:
        """★ 新 `Unit` → 旧口径的 dict（**写穿透**到引擎）。

        ⚠ **`id` 的口径变了**：旧引擎的 `id` 是**本国序号**（1,2,3…），
        新引擎的 `Unit.id` 也是本国序号，而 `Unit.gid` 是全局唯一号。
        ⇒ 门面把 **`id` 映射成本国序号**（与旧引擎一致），
        另加一个 `gid` 字段（RL 线用不上，但存档/排查时有用）。

        ★★ **同一支军 ⇒ 同一个 dict 对象**（**按 gid 缓存**）。
          为什么：RL 线的编码层（`encode.py` 的 `window_armies`）把军队 dict
          直接传给观测构造，而测试与调用方**按对象身份（`is`）**认军队
          （`row_of_token` 找"我那支军"）⇒ 每次访问都造新 dict 的话，
          "我那支军"就**认不出来了**（`StopIteration` 那种错）。

        ★★ **写穿透**（`_UnitDict`）：旧引擎的 `world.armies` 就是**真值表**，
          RL 线（与测试）会**就地改**军队 dict（`a["hp"] = 5` ——
          "敌残血 ⇒ 赢面该上升"那条测试就是这么做的）。
          ⇒ 这里的 dict 改字段会**同步改到引擎里的那支军**，
          不是只改本地副本（否则"改了血量、概率没变"——那种错**不报错**）。
        """
        gid = unit.gid
        cached = self._unit_dicts.get(gid)
        if cached is None:
            cached = _UnitDict(self, unit)
            self._unit_dicts[gid] = cached
        cached._refresh()
        return cached

    def _find_unit(self, name: str, aid: int):
        """按（国家短名, 本国序号）找单位。★ 新引擎的 `by_id` 参数序是 `(aid, nation)`。"""
        owner = self._id(name)
        for unit in self._world.units.of(owner):
            if unit.id == aid:
                return unit
        return None

    def _army(self, name: str, aid: int) -> dict | None:
        """★ 找一支军（旧口径：`world._army(name, aid)` → dict 或 `None`）。

        ⚠ 旧引擎的 `troops` **不含野人**（野人不是"国家军队"），
          而新引擎**没有野人实体** ⇒ 这里按国家找即可。
        """
        unit = self._find_unit(name, aid)
        return self._as_dict(unit) if unit is not None else None

    def _reachable(self, name: str, a: dict, *, for_attack: bool = False
                   ) -> dict[tuple[int, int], int]:
        """该军本回合走得到的格 → 累计代价。

        ⚠ **口径对齐**：旧引擎的 `_reachable` 返回 `{格: 代价}`（含起点，代价 0），
        且"可攻终点只记不扩"。新引擎的 `military.reachable` 返回
        `((x,y), 代价)` 元组序列 —— 这里转成旧形状，
        并**把起点补上**（旧口径起点代价恒为 0）。
        """
        unit = self._find_unit(name, a["id"])
        if unit is None:
            return {(a["x"], a["y"]): 0}
        pairs = self._world.military.reachable(self._id(name), unit.id,
                                              attack=for_attack)
        out = {(x, y): int(cost) for (x, y), cost in pairs}
        out.setdefault((unit.x, unit.y), 0)
        return out

    def move(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        """移动一支军。返回 `(ok, msg)`（旧口径）。"""
        owner = self._id(name)
        try:
            receipt = self._world.military.move(owner, aid, x, y)
        except Exception as exc:
            return False, str(exc)
        status = getattr(receipt, "status", "")
        message = getattr(receipt, "message", "") or ""
        ok = status not in ("rejected", "failed", "")
        return ok, message

    def attack(self, name: str, aids: list[int], x: int, y: int) -> tuple[bool, str]:
        """进攻一格。返回 `(ok, msg)`（旧口径）。"""
        owner = self._id(name)
        try:
            receipt = self._world.military.attack(owner, list(aids), x, y)
        except Exception as exc:
            return False, str(exc)
        status = getattr(receipt, "status", "")
        message = getattr(receipt, "message", "") or ""
        ok = status not in ("rejected", "failed", "")
        return ok, message

    def _new_army(self, owner: str, kind: str = "步", x: int | None = None,
                  y: int | None = None) -> tuple[int, int]:
        """新建一支军。返回 `(gid, seq)`（旧口径）。

        ⚠ **位置缺省 = 该国的核心格**（市政厅所在格）—— 旧引擎的 `_new_army`
          就是这么做的（沙盒的 `spawn` 只给国名，军队就落在核心上）。
          新引擎的 `military.seed` **必须给坐标** ⇒ 这里补上"找核心"这一步。

        ⚠ **只造军队，不返回它的 dict**：调用方若还要 dict，走
          `world.armies` 取（门面按 gid 缓存 ⇒ 身份稳定）。
          ★ 旧引擎的 `_new_army` 同样**只造不返**（返的是 `armies.append` 的结果），
          两边口径一致 ⇒ 调用方不会"造两支军"。

        ⚠ **野人**：旧引擎的野人是**真实军队**（占格、挡路、要打），
          新引擎的野人是**按需合成的虚拟单位**（`military.visible_units` 里
          对无主格现场造一个）—— 所以**不用真的建野人**，
          引擎会自己在"看得见的无主格"上补出来。
        """
        ident = self._id(owner)
        kind_id = UNIT_KINDS.get(kind, kind)
        # ★ 这国还没**建**出来？⇒ **当场建**（旧引擎的 `_new_army` 不查在册，
        #   给个名字就能造军）—— 新引擎要求"活着的国家"才准造 ⇒ 这里补建。
        if ident not in self._alive_ids() and owner != "野人":
            self._add_nation(owner)
        if x is None or y is None:
            core = self.core_of(owner)
            if core is None:
                core = (0, 0)
            x, y = core
        units = self._world.military.seed(ident, kind_id, x, y, count=1)
        unit = units[0]
        return unit.gid, unit.id

    def core_of(self, name: str) -> tuple[int, int] | None:
        """该国的**核心格**（有已落成市政厅的那格）。"""
        owner = self._id(name)
        for fact in self._world.territory.owned(owner):
            if self._world.buildings.has_role(fact.tile.id, "capital"):
                return fact.tile.position
        return None

    def _drop_guardians(self, x: int, y: int):
        """撤走该格的野人守卫（占格时用）。

        ⚠ **新引擎没有"真野人"可撤**（野人是虚拟合成的）——
        引擎判定"这格能不能攻"用的是 `_pass`/`_hostile`，
        **不要求格上真有驻军**。⇒ 这里保留调用点（算法层不用改），
        实现是**空操作**。
        """
        return None

    def _new_tile(self, x: int, y: int, owner: str) -> dict:
        """物化一格（归属给 `owner`）。"""
        ident = self._id(owner)
        site = f"{ident}/land-{x}-{y}"
        self._world.territory.create(site, ident, x, y, core=ident)
        self._invalidate_tiles()          # ★ 建了新格 ⇒ 快照失效
        return self._tile_snapshot().get((x, y), {})

    def _remove_nation(self, name: str) -> None:
        """★ **除名**（旧口径：`world.nations.pop(name)`）。

        ⇒ 军队全撤（`military.remove_nation`）+ 从在册名单里划掉
          （它**不再在册** ⇒ `n in world.nations` 为假 ⇒ 观测里不再是"对手国"）。
        ★ 领土**不回收**（旧引擎的"亡国余土成无主废墟"由结算阶段处理，
          这里只做"除名"这一件事，语义与旧线一致）。
        """
        ident = self._id(name)
        try:
            self._world.military.remove_nation(ident)
        except Exception:
            pass
        if name in self._nations:
            self._nations.remove(name)
        if name in self._order:
            self._order.remove(name)
        self._invalidate_tiles()

    def _seed_building(self, x: int, y: int, name: str, count: int) -> None:
        """★ 在一格上**种下**建筑（旧口径：`tile["buildings"]["市政厅"] = 1`）。

        ★ 新引擎的建筑是**实例**（`buildings.seed(site, kind, count)`），
          一格一座 ⇒ 这里按座数逐个种。
        ⚠ 座数上限：引擎的站点容量（`SITE_CAPACITY`）有限 ⇒ 种不下就**少种**
          （不抛错 —— 观测层不该因为"多一座厅"就崩）。
        """
        kind = KIND_OF_NAME.get(name, name)
        fact = self._world.territory.cell(x, y)
        site = fact.tile.id
        for _ in range(max(0, int(count))):
            try:
                self._world.buildings.seed(site, kind, count=1)
            except Exception:
                break
        self._invalidate_tiles()          # ★ 建筑变了 ⇒ 快照失效

    def _create_tile(self, x: int, y: int, owner: str) -> dict:
        """★ **物化一格**（`_TileMap` 的写入口）。

        ⚠ 与 `_new_tile` 的差别：这里**不设核心**（`core=None`）——
        旧引擎的 `_new_tile` 建的是"普通地块"（核心只有开局十字的中心格有），
        所以占来的地**不是核心**。
        """
        ident = self._id(owner)
        site = f"{ident}/land-{x}-{y}"
        self._world.territory.create(site, ident, x, y)
        # ★ 旧口径的"首任 owner"记一份（`tile["core"]`）。
        self._core_owners[(x, y)] = owner
        self._invalidate_tiles()          # ★ 地块变了 ⇒ 快照失效
        return self._tile_snapshot().get((x, y), {})

    # ------------------------------------------------------------ 外交
    def war_between(self, a: str, b: str) -> bool:
        """★ 这两国是否在交战。

        ⚠ **口径**：新引擎的战争记在**实体**上（`bloc/rl:bloc0` vs `nation/rl:n2`），
          但 `war_between` 比的是**成员国家**（`War.attackers` / `War.defenders`
          装的是国家 id）。
        ⇒ 这里**两边都比**：先按国家 id 比（覆盖"甲 vs 丙"这种直接交战），
          再按实体比（覆盖"甲（联盟首领）对丙宣战 ⇒ 整个联盟 vs 丙"）。
          —— 只比国家 id 会漏掉后者（联盟里的乙其实也在打）。
        """
        ident_a, ident_b = self._id(a), self._id(b)
        if ident_a == ident_b:
            return False
        if self._world.diplomacy.war_between(ident_a, ident_b):
            return True
        return self._world.diplomacy.war_between(self._entity(ident_a),
                                                 self._entity(ident_b))

    def _entity(self, ident: str) -> str:
        """国家 → 它的**外交实体**（联盟成员 ⇒ 联盟；独立 ⇒ 国家自己）。"""
        try:
            return self._world.diplomacy.entity(ident)
        except Exception:
            return ident

    def allied_between(self, a: str, b: str) -> bool:
        return self._world.diplomacy.allied(self._id(a), self._id(b))

    def entity_of(self, name: str) -> str:
        """外交实体（在盟 → 其联盟；否则 → 独立国家）。"""
        return self._world.diplomacy.entity(self._id(name))

    def entity_members(self, ent: str) -> list[str]:
        """实体成员（短名）。"""
        return [self._name(m) for m in self._world.diplomacy.members(ent)]

    def declare_war(self, a: str, b: str) -> tuple[bool, str]:
        """宣战。返回 `(ok, msg)`（旧口径）。

        ⚠ **与旧引擎的规则差异（★ 真差异，不是适配器的锅）**：
          新引擎里，**在联盟中的国家宣战要走联盟表决**
          （`_route` → `_vote`：发起方是联盟成员且未获批 ⇒ 生成一张表决票，
          下一期结算时按票数 `yes > no` 决定）。
          旧引擎没有这层 —— `w.declare_war` 直接生效。
        ⇒ 门面把"表决"也**代投**掉：发起宣战后，**联盟全体成员投赞成**，
          让沙盒的"开局直接指定战争关系"这条口径在新引擎下依然成立。
          （RL 沙盒要的是**可复现的训练局面**，不是外交博弈。）

        ⚠ **另一条新规则**：**正在交战的实体不能创盟**
          （`正在战争中，不能创盟`）。所以沙盒的建盟必须**排在宣战之前**
          —— 门面在 `set_alliance` 里也代投掉接受。
        """
        ident_a, ident_b = self._id(a), self._id(b)
        if self.war_between(a, b):
            return False, "你们已经在交战"
        try:
            self._world.diplomacy.submit(ident_a, "declare_war", target=ident_b)
        except Exception as exc:
            return False, str(exc)
        # ★ **结算一次**：让 `relations` 阶段把这条宣战**变成一张表决**
        #   （在联盟里就是表决；不在联盟里就是直接生效）。
        self.resolve_turn()
        # ★ 代投：联盟成员投赞成（见 docstring 的规则差异说明）。
        #   表决若已在上面那次结算里生效（不在联盟里），这里就是空转。
        self._auto_approve_votes()
        return True, "已宣战"

    def _auto_approve_votes(self) -> None:
        """★ **代投赞成**：把当前所有待表决的联盟表决都投成赞成。

        ★ 只在**沙盒开局**用（`declare_war` / `set_alliance` 内部）——
          沙盒要的是"**开局直接指定**战争/联盟关系"（用户 2026-09-24 的口径），
          不是让模型去打外交投票。
        ⇒ 表决存在的唯一理由是"让联盟成员有机会反对"；在沙盒里没人反对。

        ⚠ **时序**（★ 这里踩过一次坑，别改回去）：
          表决是在**结算的 `relations` 阶段**才生成的，而它的**截止期是"下一个期"**
          （`deadline = period + 1`）。
          ⇒ 流程必须是：
             ① 结算一次 ⇒ 生成表决（此时 `period == deadline - 1`）；
             ② **在 `period == deadline` 那一期**把票投掉
                （`vote()` 会拒"不在截止期"的投票）；
             ③ 再结算一次 ⇒ 表决计票 → 通过 → 动作生效（战争/结盟落地）。
          ★ 而"把期数推到截止期"**不能靠多结算几次**：结算会顺带跑
            `relations` 阶段，而那里 `_void_pending` 会清掉失效表决 ——
            多结算一次，表决就被**作废**了（症状：`approved` 空、战争没来）。
          ⇒ 正确做法：**不额外结算**，直接投票（此时期号已经对上了）。
        """
        for _ in range(6):        # 一次结算可能带出新的表决，循环几次
            votes = tuple(getattr(self._world.diplomacy, "_votes", ()))
            if not votes:
                return
            for vote in votes:
                # ★ `diplomacy.alliance(…)` 收的是**国家 id**（返回该国所属的联盟），
                #   而 `vote.alliance` 是**联盟 id** ⇒ 这里直接查 `_alliances` 表。
                bloc = self._world.diplomacy._alliances.get(vote.alliance)
                if bloc is None:
                    continue
                for member in bloc.members:
                    try:
                        self._world.diplomacy.vote(member, vote.id, True)
                    except Exception:
                        pass
            # ★ 投票后结算一次：计票 → 通过 → 动作生效（战争/结盟落地）。
            self.resolve_turn()

    def set_alliance(self, members: list[str], name: str) -> tuple[bool, str]:
        """结盟（旧口径：直接给成员名单）。

        ⚠ 新引擎的建盟是**三步**：`bloc_found`（发起，要 `bloc` ID 与 `name`）
          → 各成员 `accept_proposal` → 才真的成盟。⇒ 门面把这三步包起来。
        ⚠ **费用**：建盟要 `found_fee`（默认 100 金），而沙盒资源给得够
          （`SandboxWorld.__init__` 每国 10 万金）⇒ 不会因缺钱失败。
        ⚠ **顺序**：新引擎不许"**交战中的实体创盟**" ⇒ 沙盒里
          **必须先结盟、后宣战**（`declare_war` 里代投表决来成盟）。
        """
        idents = [self._id(m) for m in members]
        if len(idents) < 2:
            return False, "结盟至少要两个国家"
        # ★ bloc ID 必须是**内容 ID**（全 ASCII 小写，见 `engine/identifiers.py`），
        #   而联盟名是中文 ⇒ ID 用**序号**，名字走 `name` 参数。
        bloc_id = f"rl:bloc{self._bloc_seq}"
        self._bloc_seq += 1
        try:
            self._world.diplomacy.submit(idents[0], "bloc_found",
                                         bloc=bloc_id, name=name, tos=idents[1:])
        except Exception as exc:
            return False, str(exc)
        self.resolve_turn()
        for ident in idents[1:]:
            try:
                offer = self._world.diplomacy.snapshot(ident).offers[0]
                self._world.diplomacy.submit(ident, "accept_proposal", offer=offer.id)
            except Exception as exc:
                return False, f"结盟提案未被接受：{exc}"
        self._auto_approve_votes()
        self.resolve_turn()
        return True, "已结盟"

    # ------------------------------------------------------------ 视野
    def visible_to(self, name: str, x: int, y: int) -> bool:
        """该国是否看得见这一格（**含盟国视野**）。"""
        return bool(self._call("native:vision", "visible", self._id(name), x, y))

    def _defense_pct(self, x: int, y: int, def_owner: str | None) -> int:
        """地块总防御% = 地形与城堡**相乘**叠加（旧口径）。"""
        terrain = self._world.territory.terrain(x, y)
        castle = 0
        if def_owner:
            owner = self._id(def_owner)
            fact = self._world.territory.cell(x, y)
            if fact.owner == owner and fact.tile.id:
                castle = (self._world.buildings.count(fact.tile.id, CASTLE)
                          * self._world.buildings.effects(fact.tile.id, CASTLE)
                          .get("defense_per_level", 0))
        return self._world.military.defense_pct(terrain.defense, castle)

    # ------------------------------------------------------------ 战斗内算
    def _defs_at(self, name: str, x: int, y: int) -> list[dict]:
        """这一格上、对 `name` 而言算**敌人**的军队（旧口径）。

        ⚠ **野人是虚拟的**：新引擎没有"真野人军队"，野人由
          `military.visible_units` 对无主格**现场合成**。⇒ 这里照旧口径
          合成一个"满血步兵"的野人条目（无主格才有），国家军队则取实际单位。
        """
        owner = self._id(name)
        out: list[dict] = []
        cell_owner = self._world.territory.owner(x, y)
        if cell_owner is None:
            # 无主格 ⇒ 野人守军（引擎的野人没有兵种键 ⇒ 按步兵兜底）。
            out.append({"id": 0, "owner": "野人", "type": "步",
                        "hp": rules.unit_max_hp(), "x": x, "y": y,
                        "moved_turn": -1, "engaged": False})
        for unit in self._world.units:
            if (unit.x, unit.y) != (x, y) or unit.owner == owner:
                continue
            if unit.owner == "野人":
                continue
            if self._world.diplomacy.war_between(owner, unit.owner):
                out.append(self._as_dict(unit))
        return out

    @staticmethod
    def _combat_power(atk_total: int, def_pct: int) -> int:
        """该方输出（`atk_total` 乘守方减伤）。★ 与引擎同式。"""
        return max(1, atk_total * (100 - def_pct) // 100)

    @staticmethod
    def _round_damage(power: int, mod: int) -> int:
        """骰修正后的伤害。★ 与引擎同式。"""
        return max(1, power * (100 + mod) // 100)

    def _atk_target_ok(self, name: str, x: int, y: int) -> bool:
        """这一格能不能当 atk 的**终点**（敌国领土 / 有敌军驻守的野地）。"""
        owner = self._world.territory.owner(x, y)
        ident = self._id(name)
        if owner is not None:
            return (owner != ident
                    and not self._world.diplomacy.allied(ident, owner)
                    and self._world.diplomacy.war_between(ident, owner))
        return any(u.owner != ident and (u.x, u.y) == (x, y)
                   for u in self._world.units if u.owner != "野人")

    def _mv_wall(self, name: str, x: int, y: int) -> str | None:
        """mv 走一步进这格的"墙"：`None` = 可通行。

        口径：野地 / 自家 / 盟国可走；敌国与中立领土一律不可（要进占只能 atk）；
        野地上有与你交战的敌军驻守也不可（得先 atk）。
        """
        owner = self._world.territory.owner(x, y)
        ident = self._id(name)
        if owner is not None and owner != ident \
                and not self._world.diplomacy.allied(ident, owner):
            return (f"({x + 1},{y + 1}) 是「{self._name(owner)}」的领土，**mv 进不去**"
                    f"（mv 只走自己地/盟国地/无主地）")
        if owner is None and any(u.owner not in (ident, "野人")
                                 and (u.x, u.y) == (x, y)
                                 and self._world.diplomacy.war_between(ident, u.owner)
                                 for u in self._world.units):
            return f"({x + 1},{y + 1}) 有敌军驻守，不能 mv 过去；进攻请用 atk（会交战）"
        return None

    # ------------------------------------------------------------ 回合
    def _resolve_battles(self) -> list[str]:
        """★ **结算所有交战格**（旧口径：`world._resolve_battles()`）。

        ⚠ 新引擎没有这个"世界级"入口 —— 战斗是**结算阶段**的一个 hook
          （`native:army.battle`，见 `mods/native/army/mod.py` 的阶段表）。
        ⇒ 门面把它**单独拎出来**调（只结算战斗，不跑经济/外交/补给），
        这样"对拍台"（`rl/combat_noise_probe.py`）能**只重打战斗**，
        不会把经济层的随机数也搅进来。
        """
        return list(self._world.military.battle())

    def resolve_turn(self) -> dict:
        """★ **结算一个回合**（新引擎的 `runtime.settle`）。

        ⚠ **口径**：新引擎的结算**包含经济**（采集/市场/银行/国库），
        旧引擎的 `resolve_turn` 也包含 —— 两边一致。
        沙盒"不管经济"指的是**不靠经济补兵**（补员自己算），
        不是"结算里没有经济"。
        """
        result = self._world.resolve_turn()
        self._world.phase = "行动"
        self._invalidate_tiles()      # ★ 战斗会占地 ⇒ 快照失效
        return result if isinstance(result, dict) else {}

    def begin_turn(self) -> int:
        """★ **开下一回合**（旧口径：`world.turn += 1`）。

        ⚠ **新引擎没有 `begin_turn`**：回合号由 `native:government.advance`
        在结算的 `cycle.end` 阶段推进（`world.government.period()`）。
        ⇒ 门面**自己记**回合号，在结算后 +1 —— 这样 RL 线看到的
        `world.turn` 仍是"本回合是第几回合"。
        """
        self._turn += 1
        return self._turn
