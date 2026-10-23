# -*- coding: utf-8 -*-
"""战斗概率 —— **只转发引擎的能力**（`research:combat-probabilities`）。

★★ 2026-10-22 重写（review §2.4 / §8）
  ────────────────────────────────────────────
  旧 `rl/combat_probs.py`（836 行）+ `rl/_combat_fast.c`（999 行）是
  `mods/experimental/combat_research/` 的**同源复制** —— 那份 README 明写
  「来源为 `origin/feat/rl-tactics` 的 `rl/combat_probs.py` / `rl/_combat_fast.c`」。

  ⇒ 引擎的摊分/减伤/撤退规则改一次要改**两处**，而**改漏了不报错**。

  现在：**实现只有一份**（引擎的），本文件只做两件事：
    ① 把引擎能力的返回**翻译**成编码层要读的形状（`Odds` / `FrameOdds`）；
    ② 补上引擎能力**不覆盖**的那部分：
       · **增援**（引擎能力只算"手上的兵"；增援够不够得着是**寻路**，属 RL 侧决策），
       · **撤退生还率**（引擎能力是"打赢这一格"，不含"撤了能不能活"）。

  ★ 引擎能力**不覆盖**的格子（该格对所有参战方都不可见）⇒ 按**保守估**
    （"一支满血野人"）—— 与 `rl/teacher/combat.py` 的未知目标同一条口径。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from . import rules
from .tokens import BARBARIAN

# ★ 引擎的"野人"归属标记（native 的 `visible_units` 就用它）。
BARBARIAN_OWNER = BARBARIAN


@dataclass
class Odds:
    """一格的推演结果（按势力）。`p_hold[F]` = F 是**唯一**幸存者（= 引擎里占地的那家）。"""

    p_win: dict = field(default_factory=dict)     # 我方活、敌全灭
    p_lose: dict = field(default_factory=dict)    # 我方全灭、敌还活
    p_draw: float = 0.0                            # ★ 同归于尽（全场无人）
    p_hold: dict = field(default_factory=dict)     # 唯一幸存 ⇒ 占地
    e_rounds: float = 0.0
    e_loss: dict = field(default_factory=dict)     # 期望掉血
    p_rounds: list = field(default_factory=list)   # ★ P(打满 k 轮才定局)，下标 = 轮数
    n_states: int = 0
    truncated: float = 0.0                         # 未收敛的质量

    def round_bins(self, edges: tuple | None = None) -> list[float]:
        """★ P(轮数 ≤ edge) 的**累积**分档 —— "多久打完"喂网络用这个（定长、单调）。"""
        if edges is None:
            from . import scoring as S
            edges = S.ROUND_BIN_EDGES
        cum = []
        running = 0.0
        idx = 0
        for e in edges:
            while idx < len(self.p_rounds):
                running += self.p_rounds[idx]
                idx += 1
            cum.append(running)
        return cum


@dataclass
class FrameOdds:
    """**一整帧**的战斗明细 —— 编码层要的全部战斗概率，**一帧算一次**。

    ★ 只对**生成它的那一帧**成立：战斗每打一轮 hp 就变 ⇒ 跨帧复用就是错的。
    ★ **没有**跨帧缓存，也不许有。
    """

    cells: dict = field(default_factory=dict)
    reinf: dict = field(default_factory=dict)
    retreat: dict = field(default_factory=dict)
    n_retreat: int = 0

    def at(self, cell):
        return self.cells.get(cell)


# ---------------------------------------------------------------- 引擎能力的翻译
def env_name(ident: str) -> str:
    """引擎的**内容 ID** → **短名**（`rl:n0` → `甲`；非 RL 国家原样返回）。"""
    from . import nations as N
    return N.name(ident)


def _odds_from_native(result: dict, factions) -> Odds:
    """把引擎能力的返回翻成 `Odds`。

    ★ 引擎那边（`mods/experimental/combat_research/mod.py`）给的是：
        `{'research':True,'known':bool,'winnable':bool,'probabilities':{…}}`
      其中 `probabilities` = `p_win` / `p_lose` / `p_draw` / `rounds` /
      `expected_rounds_partial` / `expected_losses_partial` / `unresolved` / `n_states`。
    """
    odds = result.get("probabilities") or {}
    p_win = dict(odds.get("p_win") or {})
    p_lose = dict(odds.get("p_lose") or {})
    p_draw = float(odds.get("p_draw") or 0.0)
    rounds = dict(odds.get("rounds") or {})
    max_round = max(rounds) if rounds else 0
    p_rounds = [float(rounds.get(k, 0.0)) for k in range(max_round + 1)]
    # ★ `p_hold` = "这一家占住了这块地"：引擎那边的 `p_win[F]` 是
    #   "F 活着且 F 的敌人全灭" ⇒ 两国时与"唯一幸存"**逐字相等**；
    #   多方时它是"我活着且我的敌人都死了"（比"唯一幸存"宽）——
    #   ★ 引擎能力**不暴露**完整的幸存者分布（`terminal` 在它内部），
    #     所以多方情形只能到这个精度（**如实记在这里**，不假装更准）。
    # ★★ 归属键换成**短名**（RL 侧的词汇）—— 引擎给的是**内容 ID**（`rl:n0`），
    #   编码层按短名取（`o.p_win.get(side)`）⇒ 不换的话**那几列特征永远是 0**，
    #   而且**不报错**（`.get` 取不到就返回默认值）。
    names = {}
    for key in set(p_win) | set(p_lose) | set(odds.get("expected_losses_partial") or {}):
        names[key] = env_name(key)
    p_win = {names[k]: v for k, v in p_win.items()}
    p_lose = {names[k]: v for k, v in p_lose.items()}
    e_loss = {names[k]: v for k, v in (odds.get("expected_losses_partial") or {}).items()}
    return Odds(
        p_win=p_win,
        p_lose=p_lose,
        p_draw=p_draw,
        p_hold=dict(p_win),
        e_rounds=float(odds.get("expected_rounds_partial") or 0.0),
        e_loss=e_loss,
        p_rounds=p_rounds,
        n_states=int(odds.get("n_states") or 0),
        truncated=float(odds.get("unresolved") or 0.0),
    )


def _conservative(factions) -> Odds:
    """看不见的一格 ⇒ **保守估**（"一支满血野人"，同 `teacher/combat.py` 的未知目标）。"""
    g_hp, g_atk = rules.unit_max_hp({"type": "native:infantry"}), rules.unit_atk({"type": "native:infantry"})
    return Odds(p_win={}, p_lose={}, p_draw=0.0, p_hold={},
                e_rounds=1.0, e_loss={}, p_rounds=[0.0, 1.0], n_states=0, truncated=0.0)


# ---------------------------------------------------------------- 交战格
def engaged_cells(env) -> list:
    """当前**正在交战**的格（引擎结算遍历的那个集合）。"""
    return sorted({(a.x, a.y) for a in env.armies()
                   if a.engaged and env.owner_of(a) != BARBARIAN_OWNER})


def _factions_at(env, cell) -> dict:
    """这一格上**各方**的军队 `{主人: [军, …]}`。"""
    out: dict = {}
    for unit in env.armies():
        if (unit.x, unit.y) != cell or not unit.alive:
            continue
        out.setdefault(env.owner_of(unit), []).append(unit)
    return out


def _actor_of(env, cell, factions) -> str | None:
    """当这一格的**提问方**。

    ★ 两个硬条件（缺一不可）：
      ① **看得见**这一格 —— 引擎的能力要求"提问方看得见目标格"，
        看不见它就答"不知道"（`known=False`）⇒ 提问方必须**看得见**；
      ② **野人不能当提问方** —— 引擎能力要"提问者自己的部队"。
    ★ 取**名字排序后的第一个**合格者 ⇒ 同一格每次问的人**相同**（数字不漂）。
    """
    for owner, units in sorted(factions.items()):
        if owner == BARBARIAN_OWNER or not units:
            continue
        if env.visible(owner, cell[0], cell[1]):
            return owner
    return None


# ---------------------------------------------------------------- 增援
def _reinforcements(env, cell, owner: str, allow) -> list:
    """这一方**够得着**这一格的增援（引擎的可达集 + 视野过滤）。

    ★ 这是**寻路**（RL 侧的决策），不是引擎规则 ⇒ 走 `env.reachable`（引擎的可达集）。
    ★ 增援**只算看得见的**（`allow` ＝ 视野）：看不见的敌军增援不算
      （否则就是隔雾点名）。
    """
    out = []
    for unit in env.armies():
        if not unit.alive or env.owner_of(unit) != owner:
            continue
        if (unit.x, unit.y) == cell:
            continue
        if allow is not None and (unit.x, unit.y) not in allow:
            continue
        reach = env.reachable(owner, unit, for_attack=True)
        if cell in reach:
            out.append(unit)
    return out


# ---------------------------------------------------------------- 撤退生还率
def retreat_odds(env, x: int, y: int, army) -> float:
    """**这支军撤了之后活下来的概率**（一轮结算后它还在）。

    ★ 撤退的口径**全部取自引擎**：
      · 守方档 `retreat_cover = 50`（`rules.retreat_def_cover()` 读引擎的表）、
        进攻方档 `100`；
      · 撤退军输出 −80%（`rules.retreat_atk_penalty()`，也是引擎的表）——
        引擎的 `faction_power` **自己**按 `retreat_to` 打这个折扣 ✔；
      · 伤害分摊走 `native:army.spread_damage` ✔。
    ⇒ RL 侧**不重写**任何一条。
    """
    factions = _factions_at(env, (x, y))
    if not factions:
        return 1.0
    side = env.owner_of(army)
    if side not in factions:
        return 1.0
    # 撤退的减伤档位由引擎决定：我方在该格"未参战"、或本身就是格主 ⇒ 守方档
    attacking = any(env.owner_of(u) == side and u.engaged for u in factions[side])
    owner_here = env.owner(x, y)
    cover = rules.retreat_def_cover() if (not attacking or owner_here == side) else 100

    atk = [deepcopy(u) for u in factions[side]]
    defs = []
    for other, units in sorted(factions.items()):
        if other == side:
            continue
        defs.extend(deepcopy(u) for u in units)
    # ★ 撤退方可能是**观测行**（dict）而不是引擎 `Unit` ⇒ 两种都吃
    if isinstance(army, dict):
        army = next((u for u in atk if u.get("gid") == army.get("gid")), None)
        if army is None:
            return 1.0
    if not atk or not defs:
        return 1.0
    # 标出"哪一支在撤"：引擎按 `retreat_to` 判它输出打折、按 `retreat_cover` 判它承伤。
    target = None
    for i, u in enumerate(atk):
        if u.gid == army.gid:
            target = i
            break
    if target is None:
        return 1.0
    atk[target].retreat_to = (x, y)
    atk[target].retreat_cover = cover
    for u in defs:
        if u.retreat_cover is None:
            u.retreat_cover = 100
    a_pow = env.call("native:army", "faction_power", atk)
    d_pow = env.call("native:army", "faction_power", defs)
    env.call("native:army", "spread_damage", d_pow, atk)
    env.call("native:army", "spread_damage", a_pow, defs)
    return 1.0 if atk[target].alive else 0.0


# ---------------------------------------------------------------- 入口
def actor_of(env, x: int, y: int) -> str | None:
    """这一格的**提问方**（谁来问引擎"这一格打成什么样"）。

    ★ 引擎的评估是**从提问方视角**算的（它按提问方的视野拼战场）⇒
      提问方**必须定死**（取国家名排序后的第一个），否则同一格每次问的人不同、
      数字会漂。
    ★ 野人**不能**当提问方（引擎能力要"提问者自己的部队"）。
    """
    return _actor_of(env, (x, y), _factions_at(env, (x, y)))


def ids_of(env, x: int, y: int, actor: str) -> list:
    """提问方在**这一格**上的部队 id（引擎据此把它们当作投入目标格的进攻方）。"""
    return [u.id for u in _factions_at(env, (x, y)).get(actor, ())]


def cell_odds(env, x: int, y: int, *, with_reinf: bool = False):
    """一格 → `Odds`（没在打 ⇒ `None`）。这是**给编码层用的入口**。"""
    factions = _factions_at(env, (x, y))
    if not factions:
        return None
    actor = _actor_of(env, (x, y), factions)
    if actor is None:
        return _conservative(factions)
    ids = [u.id for u in factions[actor]]
    if not ids:
        return _conservative(factions)
    try:
        result = env.assess(actor, ids, x, y)
    except Exception:                                # noqa: BLE001 引擎拒了 ⇒ 保守估
        return _conservative(factions)
    if not result.get("known"):
        return _conservative(factions)
    return _odds_from_native(result, factions)


def snapshot(env, *, with_reinf: bool = False) -> dict:
    """★ **本回合**所有交战格的概率 —— **每回合重算，绝不跨回合缓存**。"""
    out: dict = {}
    for cell in engaged_cells(env):
        o = cell_odds(env, cell[0], cell[1], with_reinf=with_reinf)
        if o is not None:
            out[cell] = o
    return out


def frame_odds(env, me: str, mask, *, with_reinf: bool = True,
               retreat: bool = True) -> FrameOdds:
    """★ **观测路径的唯一入口** —— 这一帧的全部战斗概率。

    `mask` 是**必填的第三个位置参数**（不是关键字、更不给默认值）：
    这个函数要按**视野**过滤增援 ⇒ 给个默认值就等于留一条
    "忘了传 ⇒ 静默偷看"的路（本线最忌）。忘传直接 `TypeError`，这是有意的。
    """
    out = FrameOdds()
    for cell in engaged_cells(env):
        o = cell_odds(env, cell[0], cell[1])
        if o is None:
            continue
        out.cells[cell] = o
        if with_reinf:
            # ★ 增援：把"够得着这一格"的自家部队**并进提问方的部队**再问一次
            #   （引擎能力只算"手上的兵"；增援够不够得着是**寻路**，属 RL 侧决策）。
            factions = _factions_at(env, cell)
            actor = _actor_of(env, cell, factions)
            if actor is not None:
                extra = _reinforcements(env, cell, actor, mask)
                if extra:
                    ids = [u.id for u in factions[actor]] + [u.id for u in extra]
                    try:
                        result = env.assess(actor, ids, cell[0], cell[1])
                    except Exception:                # noqa: BLE001
                        result = None
                    out.reinf[cell] = (o if result is None or not result.get("known")
                                       else _odds_from_native(result, factions))
                else:
                    out.reinf[cell] = o
            else:
                out.reinf[cell] = o
        if retreat:
            # ★ 算**这一格上我方的每一支**（不只是 `engaged` 的那些）——
            #   native 的 `engaged` 标的是"**主动进攻**的那方"，**守军不算**；
            #   但守军同样在打、同样要撤 ⇒ 撤退风险要算它的。
            for unit in factions.get(me, ()):
                # ★ 按 **`gid`** 记（全局唯一）—— 不能用 `id(对象)`：
                #   编码层拿到的是**窗口行**（另一批对象）⇒ `id()` 永远对不上
                #   ⇒ 那一列**恒为 1.0**（"撤退必活"）——**而且不报错**。
                out.retreat[unit.gid] = retreat_odds(env, cell[0], cell[1], unit)
                out.n_retreat += 1
    return out
