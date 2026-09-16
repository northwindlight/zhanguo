# -*- coding: utf-8 -*-
"""难度评估：**打这一格要几支兵、要几轮、会掉多少血**（v11plus 的"评估难度"一步）。

v10 有同款计算的**内联线性版**（`expand_rule_v10.py:_fight_cost`），三处不足：
用**全军最大** atk/hp 估（不看实际派谁去）、按线性估多轮（不算战损衰减）、
不读守军**当前血量**。本模块改成**逐轮模拟**，并且**直接调用引擎的伤害公式**
（`World._combat_power` / `_round_damage` / `World._defense_pct`），不再重写一遍——
重写那一步正是 v9→v10 反复踩的坑（写死的值一抖动就偏）。

★ 口径与引擎 `_resolve_battles`（`mp.py:1199`）逐条对齐：

  · 骰子取**期望**（修正 0）：`_round_damage(power, 0) == power`；
  · **进攻方不吃地形**：我方输出按 `def_pct=0` 算（我在格子上，守方不在）；
  · 守方按该格总减伤算（地形 × 城堡，且**城堡只在地主名下才算**）；
  · 双方**同时出手**，一轮算完再统一施加伤害（允许同归于尽）；
  · 伤害在该方各军之间**按序分摊**（引擎 `_spread` 口径：余数给靠前的军）。

★ 迷雾：**看不见的守军不许猜具体数字** —— 按"一支满血野人"估，而且野人的攻击力
也必须**现读** `UNIT_TYPES["步"]["atk"]`，**不能写 `hp // 2`**（真值下 100/2=50 恰好
等于 atk，看着对；一抖动 hp 与 atk 各走各的，这个估值就偏了——v10 的注释里记着这笔账）。

用法：
    d = assess(world, "秦", (12, 7), my_armies, visible=True)
    if d.winnable and d.need <= len(group): ...
"""
from __future__ import annotations

from typing import NamedTuple

from game import ARMY_MAX_HP, TERRAIN_STATS, UNIT_TYPES, unit_atk, unit_max_hp

# 一格（一个目标）的难度评估结果。**NamedTuple**：可断言、可排序、可打印。
Difficulty = NamedTuple("Difficulty", [
    ("need", int),        # 最少几支能赢；打不赢 = 手里那些全上也不够（= len(units)+1）
    ("rounds", int),      # 打赢要几轮 —— ★**轮 = 回合**（每回合结算一轮，见 `_resolve_battles`）
    ("losses", int),      # 预计我方总掉血（HP 缺口）
    ("def_hp", int),      # 守方总血（看不见时 = "一支满血野人"的估；空目标 = 0）
    ("def_pct", int),     # 该格总减伤%
    ("winnable", bool),   # 手里这些兵够不够赢
    ("empty", bool),      # **看得见且一个守军都没有** ⇒ 走进去就占地（不是"未知"，是真的空）
])


def defense_pct(world, x: int, y: int, def_owner: str | None, *, visible: bool) -> int:
    """这一格的**总减伤%**（地形 × 城堡相乘，与引擎 `_defense_pct` 同式）。

    `visible=False`（看不见这格）→ 取**当下最保守**的地形减伤（`TERRAIN_STATS` 里
    defense 最大的那个），且**城堡按 0 算**（看不见就没法知道城建了几级）。
    保守 = 宁可把敌人估强一点：估弱的代价是送死，估强的代价只是晚一回合动手。
    """
    if not visible:
        return max(st.get("defense", 0) for st in TERRAIN_STATS.values())
    return world._defense_pct(x, y, def_owner)


def defenders_at(world, name: str, x: int, y: int, *, visible: bool) -> list[dict]:
    """这一格上**看得见**的守军（看不见 → 空表 ⇒ 调用方按野人兜底估）。

    直接问引擎的 `_defs_at`（它的口径就是"这一格上、对 `name` 而言算敌人的人"：
    已物化地块上与我交战的国家军队、无主野地上的野人），**先过视野闸门**再问——
    不然等于隔着迷雾点名敌军的血量和兵种。
    """
    if not visible:
        return []
    return [a for a in world._defs_at(name, x, y) if a.get("hp", 0) > 0]


def _guardian_profile() -> tuple[int, int]:
    """野人守军的 `(满血, 攻击力)` —— **现读兵种表**，不去全图找样本。

    野人是引擎 `_spawn_guardian`（`mp.py:668`）造出来的，**没有 `type` 键**
    （只有 `hp: ARMY_MAX_HP`）⇒ `game.unit_kind` 按步兵兜底 ⇒ 一个合成体
    `{"type": "步"}` 经 `unit_max_hp`/`unit_atk` 读出来的值与真野人**逐字相同**，
    而且**现读**（抖动/调平衡都跟得上）、**不扫全图**（`assess` 每个目标都要调一次，
    在 `world.armies` 里 `next(...)` 一遍就是 O(目标数 × 军队数)）。
    """
    return unit_max_hp({"type": "步"}), unit_atk({"type": "步"})


def _spread(dmg: int, units: list[list[int]]) -> None:
    """把 `dmg` 分摊给**还活着**的军（余数给靠前的）——引擎 `World._spread` 同口径。"""
    alive = [u for u in units if u[0] > 0]
    if not alive:
        return
    per, rem = divmod(dmg, len(alive))
    for i, u in enumerate(alive):
        u[0] -= per + (1 if i < rem else 0)


def simulate(world, attackers: list[tuple[int, int]], defenders: list[tuple[int, int]],
             def_pct: int, *, rounds_cap: int) -> tuple[bool, int, int]:
    """逐轮模拟 `(hp, atk)` 两方对撞 → `(赢了吗, 打了几轮, 我方总掉血)`。

    每轮：双方按**当下存活者**的攻击总和各出一击（同时出手），再统一扣血。
    **同归于尽算输**——我全灭就没人去占地了（引擎里那块地归不了一个没人站着的势力），
    所以判定顺序是"先看我方死没死、再看守方死没死"。轮数打满还没分出胜负 = 打不动 = 输。
    """
    atk_u = [[h, a] for h, a in attackers]
    def_u = [[h, a] for h, a in defenders]
    start_hp = sum(u[0] for u in atk_u)

    def _loss() -> int:
        return start_hp - sum(max(0, u[0]) for u in atk_u)

    if not atk_u:                                   # 一支都没派 = 打不了
        return False, 0, 0
    for r in range(1, rounds_cap + 1):
        a_alive = [u for u in atk_u if u[0] > 0]
        d_alive = [u for u in def_u if u[0] > 0]
        if not d_alive:                             # 守方已清空 → 赢（上一轮就打完了）
            return True, r - 1, _loss()
        if not a_alive:
            return False, r - 1, _loss()
        # ★ 伤害口径照抄引擎 `_resolve_battles`（`mp.py:1245-1248`），只是骰子取期望：
        #     我方出手 = _round_damage(_combat_power(Σatk, 0), mod=0) × 守方减伤
        #     守方出手 = _round_damage(_combat_power(Σatk, 0), mod=0) × 我方减伤(=0，进攻方不吃地形)
        #   `_combat_power(x, 0)` 里那一步**不带减伤**（减伤在乘 `(100-soak)/100` 那一步），
        #   照抄才逐字对得上（自己"合并"成 `_combat_power(atk, def_pct)` 会差在
        #   整除 vs 四舍五入、以及 max(1,·) 的位置上）。
        a_pow = world._round_damage(world._combat_power(sum(u[1] for u in a_alive), 0), 0)
        d_pow = world._round_damage(world._combat_power(sum(u[1] for u in d_alive), 0), 0)
        dmg_def = max(1, round(a_pow * (100 - def_pct) / 100))     # 守方吃地形
        _spread(d_pow, atk_u)          # 守方打我（我方减伤 0 ⇒ 原样）
        _spread(dmg_def, def_u)        # 我打守方（乘以守方减伤）
        if not any(u[0] > 0 for u in atk_u):        # 同归于尽：我全灭 ⇒ 算输
            return False, r, _loss()
        if not any(u[0] > 0 for u in def_u):
            return True, r, _loss()
    return False, rounds_cap, _loss()               # 打满轮数还没打完 = 打不动


def assess(world, name: str, cell: tuple[int, int], units: list[dict], *,
           visible: bool, need_cap: int, rounds_cap: int) -> Difficulty:
    """评估"用 `units` 里最强的 n 支去打 `cell`"：返回 `Difficulty`。

    `need` = **最少几支能赢**：按 `(-攻击力, -血, id)` 排好序后从 1 支开始逐个数着模拟，
    第一支能赢的 n 就是它；一直赢不了 → `need = len(units)+1` 且 `winnable=False`。
    排序是**确定**的（不看列表传入顺序），所以同一局面永远同一个结论。

    ★ 调用方的两种用法：
      · **排榜时**（`targeting.py`）传全军的可用军队 —— `need` 是"要凑几支"的粗估；
      · **出手前**（`v11plus/military.py`）传**实际编好的那几支** —— 复核这一队真能赢，
        不能赢就不打（这比 v10 强的地方：v10 只看排序指标，凑够 `MIN_SQUAD` 就上）。
    """
    x, y = cell
    # ★ 传给 `_defense_pct` 的必须是**守方阵营**（引擎 `_resolve_battles` 传的就是 F）：
    #   无主野地上的守军是"野人"，城堡那条判的是 `t["owner"] == def_owner`
    #   ⇒ 传 "野人" 才与引擎同判（传 `owned_by` 得到的 None 会在"无主格恰好有城堡"时
    #   多算一级城防——现实里不会发生，但口径要对齐，免得将来真发生时不声不响）。
    def_owner = world.owned_by(x, y) or "野人"
    d_pct = defense_pct(world, x, y, def_owner, visible=visible)
    live = defenders_at(world, name, x, y, visible=visible)
    if live:
        def_pairs = [(max(0, a.get("hp", 0)), unit_atk(a)) for a in live]
        def_pairs.sort(key=lambda t: (-t[1], -t[0]))       # 确定性排序（与传入顺序无关）
    elif visible:
        # ★ **看得见、又没有守军 = 真的空**（不是"不知道"）：野人开局一次铺满全图，
        #   死过不再补（`guard_once`），占地时还会连守卫一起清（`_drop_guardians`），
        #   所以无主格上的守军数**只减不增** ⇒ 看得见的空格走进去就占地，一支就够。
        return Difficulty(1, 0, 0, 0, d_pct, True, True)
    else:
        g_hp, g_atk = _guardian_profile()
        def_pairs = [(g_hp, g_atk)]                        # 看不见 ⇒ 按一支满血野人**保守**估
    def_hp = sum(h for h, _ in def_pairs)

    pool = sorted(units, key=lambda a: (-unit_atk(a), -a.get("hp", 0), a["id"]))
    pool = pool[:max(1, need_cap)]
    for n in range(1, len(pool) + 1):
        atk_pairs = [(max(0, a.get("hp", 0)), unit_atk(a)) for a in pool[:n]]
        win, rounds, losses = simulate(world, atk_pairs, def_pairs, d_pct,
                                       rounds_cap=rounds_cap)
        if win:
            return Difficulty(n, rounds, losses, def_hp, d_pct, True, False)
    # 全上也不够：如实说"打不赢"，轮数按上限记（不许把它当成"要很多兵"的普通目标）
    return Difficulty(len(pool) + 1, rounds_cap, 0, def_hp, d_pct, False, False)