# -*- coding: utf-8 -*-
"""难度评估：**打这一格要几支兵、要几轮、会掉多少血**（v11plus 的"评估难度"一步）。

★ 2026-10-22 重写（review §2.4 / §8）：**伤害与减伤全部走 native**。

  旧版这里**自己抄了一份**引擎的战斗数学：
    · `_combat_power(x, def_pct)` ＝ `max(1, x * (100 - def_pct) // 100)`
    · `_round_damage(power, mod)` ＝ `max(1, power * (100 + mod) // 100)`
    · `_spread(dmg, units)`      ＝ 均分、余数给靠前的
  三条都是**引擎规则**的复刻 ⇒ 引擎改了平衡，RL 线还按老数学算，**而且不报错**。

  现在：
    · 总减伤%   → `native:army.defense_pct`（native 的公式）
    · 守军      → `native:army.visible_units`（native 自己合成野人，口径按引擎）
    · 一方输出  → `native:army.faction_power`
    · 伤害分摊  → `native:army.spread_damage`
  ⇒ RL 侧只做**决策**（派几支去打），不做**结算**。

★ 口径与引擎 `_resolve_battles` 逐条对齐：
  · 骰子取**期望**（修正 0）；
  · **进攻方不吃地形**（我方输出按 `def_pct=0` 算）；
  · 守方按该格总减伤算（地形 × 城堡，且**城堡只在地主名下才算**）；
  · 双方**同时出手**，一轮算完再统一施加伤害（允许同归于尽）。

★ 迷雾：**看不见的守军不许猜具体数字** —— 按"一支满血野人"估。

用法：
    d = assess(env, "甲", (12, 7), my_armies, visible=True)
    if d.winnable and d.need <= len(group): ...
"""
from __future__ import annotations

from copy import deepcopy
from typing import NamedTuple

from .. import rules
from ..rules import unit_atk, unit_max_hp

# 一格（一个目标）的难度评估结果。**NamedTuple**：可断言、可排序、可打印。
Difficulty = NamedTuple("Difficulty", [
    ("need", int),        # 最少几支能赢；打不赢 = 手里那些全上也不够（= len(units)+1）
    ("rounds", int),      # 打赢要几轮 —— ★**轮 = 回合**
    ("losses", int),      # 预计我方总掉血（HP 缺口）
    ("def_hp", int),      # 守方总血（看不见时 = "一支满血野人"的估；空目标 = 0）
    ("def_pct", int),     # 该格总减伤%
    ("winnable", bool),   # 手里这些兵够不够赢
    ("empty", bool),      # **看得见且一个守军都没有** ⇒ 走进去就占地
])


def defense_pct(env, x: int, y: int, def_owner: str | None, *, visible: bool) -> int:
    """这一格的**总减伤%**（地形 × 城堡相乘，**native 的公式**）。

    `visible=False`（看不见这格）→ 取**当下最保守**的地形减伤（引擎地形表里
    defense 最大的那个），且**城堡按 0 算**（看不见就没法知道城建了几级）。
    保守 = 宁可把敌人估强一点：估弱的代价是送死，估强的代价只是晚一回合动手。
    """
    if not visible:
        return max(rules.terrain_defense(t) for t in rules.terrain_names())
    # ★ native 的公式（地形减伤 × 城堡减伤），城堡只在地主名下才算。
    return env.defense_pct(x, y, def_owner)


def defenders_at(env, name: str, x: int, y: int, *, visible: bool) -> list:
    """这一格上**看得见**的守军（看不见 → 空表 ⇒ 调用方按野人兜底估）。

    ★ 直接问 native（`rl:truth.defenders` → `native:army.visible_units`）：
      野人是引擎**现场合成**的，口径完全按引擎，RL 侧不再自己造（review P1-6）。
    """
    if not visible:
        return []
    return [u for u in env.defenders(x, y, name) if u.hp > 0]


def _guardian_profile() -> tuple[int, int]:
    """野人守军的 `(满血, 攻击力)` —— **现读兵种表**（`rl/rules.py` 读引擎的活表）。

    ★ 野人是引擎合成的**虚拟单位**，没有自己的兵种 ⇒ 按步兵兜底
      （引擎里 `visible_units` 造野人用的就是 `native:infantry` 的 hp）✔
    """
    return unit_max_hp({"type": "native:infantry"}), unit_atk({"type": "native:infantry"})


def simulate(env, attackers, defenders, def_pct: int, *, rounds_cap: int) -> tuple[bool, int, int]:
    """逐轮模拟 → `(赢了吗, 打了几轮, 我方总掉血)`。

    ★ 两方的输出与承伤**全部走 native**：
      · `native:army.faction_power`（一方输出；含撤退惩罚）
      · `native:army.spread_damage`（伤害在该方各军之间分摊）
    ⇒ RL 侧**不再有**自己的 `_combat_power` / `_round_damage` / `_spread`。

    每轮：双方按**当下存活者**的攻击总和各出一击（同时出手），再统一扣血。
    **同归于尽算输**——我全灭就没人去占地了。
    """
    # ★ 模拟要改血量 ⇒ 先**深拷**（绝不改真单位的 hp）。
    atk_u = [deepcopy(u) for u in attackers]
    def_u = [deepcopy(u) for u in defenders]
    start_hp = sum(max(0, u.hp) for u in atk_u)

    def _loss() -> int:
        return start_hp - sum(max(0, u.hp) for u in atk_u)

    if not atk_u:                                   # 一支都没派 = 打不了
        return False, 0, 0
    for r in range(1, rounds_cap + 1):
        a_alive = [u for u in atk_u if u.alive]
        d_alive = [u for u in def_u if u.alive]
        if not d_alive:                             # 守方已清空 → 赢（上一轮就打完了）
            return True, r - 1, _loss()
        if not a_alive:
            return False, r - 1, _loss()
        # ★ 我方出手（进攻方不吃地形 ⇒ 守方减伤只作用在**守方**那一步）。
        a_pow = env.call("native:army", "faction_power", a_alive)
        d_pow = env.call("native:army", "faction_power", d_alive)
        dmg_def = max(1, round(a_pow * (100 - def_pct) / 100))     # 守方吃地形
        env.call("native:army", "spread_damage", d_pow, a_alive)  # 守方打我（我方减伤 0）
        env.call("native:army", "spread_damage", dmg_def, d_alive)  # 我打守方
        if not any(u.alive for u in atk_u):         # 同归于尽：我全灭 ⇒ 算输
            return False, r, _loss()
        if not any(u.alive for u in def_u):
            return True, r, _loss()
    return False, rounds_cap, _loss()               # 打满轮数还没打完 = 打不动


def assess(env, name: str, cell: tuple[int, int], units: list, *,
           visible: bool, need_cap: int, rounds_cap: int) -> Difficulty:
    """评估"用 `units` 里最强的 n 支去打 `cell`"：返回 `Difficulty`。

    `need` = **最少几支能赢**：按 `(-攻击力, -血, id)` 排好序后从 1 支开始逐个数着模拟，
    第一支能赢的 n 就是它；一直赢不了 → `need = len(units)+1` 且 `winnable=False`。
    排序是**确定**的（不看列表传入顺序），所以同一局面永远同一个结论。
    """
    x, y = cell
    # ★ 传给减伤计算的是**守方阵营**：无主野地上的守军是"野人"，
    #   城堡那条判的是"这格的主人是不是守方" ⇒ 传"野人"才与引擎同判。
    def_owner = env.owner(x, y) or "野人"
    d_pct = defense_pct(env, x, y, def_owner, visible=visible)
    live = defenders_at(env, name, x, y, visible=visible)
    if live:
        def_pairs = [(max(0, u.hp), unit_atk(u)) for u in live]
        def_pairs.sort(key=lambda t: (-t[1], -t[0]))       # 确定性排序
        def_units = live
    elif visible:
        # ★ **看得见、又没有守军 = 真的空**（不是"不知道"）：
        #   无主格上的守军数**只减不增**（野人死过不再补，占地时连守卫一起清）
        #   ⇒ 看得见的空格走进去就占地，一支就够。
        return Difficulty(1, 0, 0, 0, d_pct, True, True)
    else:
        g_hp, g_atk = _guardian_profile()
        def_pairs = [(g_hp, g_atk)]                        # 看不见 ⇒ 按一支满血野人**保守**估
        def_units = []
    def_hp = sum(h for h, _ in def_pairs)

    pool = sorted(units, key=lambda a: (-unit_atk(a), -a.hp, a.id))
    pool = pool[:max(1, need_cap)]
    for n in range(1, len(pool) + 1):
        atk_pairs = pool[:n]
        win, rounds, losses = simulate(env, atk_pairs, def_units, d_pct,
                                       rounds_cap=rounds_cap)
        if win:
            return Difficulty(n, rounds, losses, def_hp, d_pct, True, False)
    # 全上也不够：如实说"打不赢"，轮数按上限记（不许把它当成"要很多兵"的普通目标）
    return Difficulty(len(pool) + 1, rounds_cap, 0, def_hp, d_pct, False, False)
