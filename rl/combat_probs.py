# -*- coding: utf-8 -*-
"""战斗概率推演 —— `World._resolve_battles`（`mp.py:1818`）的**概率版**。

    为什么要有它
    ────────────
    `military.assess` 把骰子**取期望** ⇒ 只能给"大概打得过"，给不出**概率**。
    用户要的正是概率（2026-09-24）：「看战斗能不能赢（**输赢和同归的概率**是什么），
    mv 和 atk 的增援会改变什么，**撤退保住军队的概率**是多少」。

    ★ 能**精确算**，不必蒙特卡洛
    ─────────────────────────────
    引擎口径（`mp.py:1856-1872`，逐条核过）：

      · **每方各掷一个 1d6**（`_die` → `COMBAT_DIE_MOD = {1:-25,…,6:+25}`）
      · **同时出手**：先把所有伤害算完再统一施加 ⇒ **允许同归于尽**
      · 伤害 `share = power / len(enemies)` **均分给各敌人**（腹背受敌则兵力分散）
      · 按对方减伤 `soak` 打折，再 `max(1, round(...))`
      · `_spread` 把总伤害**尽量摊平**到各单位（前 `rem` 支各多 1 点）

    两方 ⇒ 每轮 **6×6 = 36 种组合**；三方 ⇒ 216。**枚举得动**，
    而每轮 hp 严格下降 ⇒ 状态图是 **DAG** ⇒ 记忆化 DP 可**精确**求出
    P(赢/输/同归)、期望轮数、期望掉血。

    ★ 一个关键简化（不是近似，是引擎事实）
    ─────────────────────────────────────
    `unit_atk(a)` **与 hp 无关**（`game.py:100`，只查兵种表）⇒ 一方本轮的**输出**
    只取决于"还有哪些兵种活着"，与它们剩多少血无关。于是递归状态只需
    **存活单位的 (兵种, hp, 是否撤退中) 有序元组**，输出侧是它的纯函数。

    三问（对应用户原话）
    ────────────────────
      ① `assess`   —— 现状：P(赢)/P(输)/P(同归) + 期望掉血 + 期望轮数
      ② `assess(..., reinforce=…)` —— ★ **可及增援**：不是凭空加兵，而是
         "**当前状态下够得着**的军"。**攻守角色不同**（用户 2026-09-24：
         「**进攻方的增援是 atk，防御方的增援是 mv**」）：
           攻方 = `_reachable(..., for_attack=True)` 含该格（冲进敌地开打）
           守方 = `_reachable(...)` 含该格 —— ★ **只在自家/盟国地成立**
                  （无主地上有交战敌军 ⇒ `_mv_wall` 拒 mv、只认 atk）
      ③ `retreat_odds` —— 撤退**保住这支军**的概率（`retreat_cover`：
         防御方 50% 减伤、进攻方 100%（即全额）；撤退军本轮输出 −80%）

    ★ 用法：**当特征**（用户：「当特征」）—— 精确计算不该让网络去学。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from balance import COMBAT_DIE_MOD, RETREAT_ATK_PENALTY, RETREAT_DEF_COVER
from game import unit_atk, unit_kind

# ★★ 本模块里的**尺度与上限**全部读先验表（用户 2026-09-24：「搜索引擎是不是也是
#   硬编码的，**改成从 `balance` 抽**」）—— 引擎口径（骰子/撤退/兵种攻击）本来就已经
#   读 `balance`/`game`；会调的是尺度与上限，它们现在在 `rl/scoring.py`。
#   ★ 一律 `S.名字`（**不要** `from .scoring import ...`：那会在导入时把值绑死，
#     之后改表**不生效且不报错**）。
from . import scoring as S

DIE_FACES = tuple(sorted(COMBAT_DIE_MOD))          # (1,2,3,4,5,6)
BARBARIAN = "野人"


# ================================================================ 静态局面
@dataclass
class Battle:
    """一格上的战斗**静态**部分（势力分组 / 敌我关系 / 减伤）—— 逐行对齐 `_resolve_battles`。

    只建一次，DP 全程复用（`order` 固定，`state` 按它的下标索引）。
    """
    x: int
    y: int
    owner: str | None
    order: tuple[str, ...]                    # 势力顺序（稳定）
    attacker: frozenset[str]                  # 进攻方（有 `engaged` 军的那些）
    enemies: dict[str, tuple[str, ...]]       # 每方的敌人（含野人特则）
    soak: dict[str, int]                      # 减伤%（地形/城堡，**只给守方**）
    init: dict[str, tuple]                    # 每方初始 `(兵种, hp, 撤退中, 减伤)`，顺序 = 引擎顺序
    # ---- 下面两条是 `order` 的**下标形态**（热循环里用；dict 查名字太慢）----
    enemy_idx: tuple = ()                     # 每方的敌人下标
    soak_list: tuple = ()                     # 每方减伤%（按 order）
    # ★ 签名 → `{伤害向量: 概率质量}` 的缓存（**挂实例上**，见 `_agg_damage`）
    agg_cache: dict = field(default_factory=dict)


def build(world, x: int, y: int, *, extra: dict[str, list[dict]] | None = None
          ) -> Battle | None:
    """把引擎当前局面**照抄**成一张 `Battle`；无战斗（或没进攻方）⇒ `None`。

    `extra` = 追加的增援军（`{势力: [军队 dict, …]}`），用来算"加增援后会怎样"。
    """
    owner = world.owned_by(x, y)
    forces: dict[str, list[dict]] = {}
    for a in world.armies:
        if (a["x"], a["y"]) != (x, y) or a["hp"] <= 0:
            continue
        if a["owner"] == BARBARIAN and owner is not None:
            continue                            # 野人只守无主格
        forces.setdefault(a["owner"], []).append(a)
    for F, ms in (extra or {}).items():
        forces.setdefault(F, []).extend(ms)
    attacker = frozenset(F for F in forces if F != BARBARIAN
                         and any(a.get("engaged") for a in forces[F]))
    if not forces or not attacker:
        return None

    def _enemies(F: str) -> tuple[str, ...]:
        if F == BARBARIAN:
            return tuple(G for G in attacker if G != BARBARIAN)
        en = [G for G in forces if G != F and G != BARBARIAN
              and world.war_between(F, G)]
        if F in attacker and BARBARIAN in forces:
            en.append(BARBARIAN)
        return tuple(en)

    # 地形/城堡减伤给「守方」：未参战的驻军（含野人）挨打时吃本地地形，格主永远算守方；
    # 交战中的进攻方不吃加成（谁挨打谁是守方）。—— 与引擎逐字同式
    soak = {F: (world._defense_pct(x, y, F) if (F not in attacker or F == owner) else 0)
            for F in forces}
    order = tuple(forces)
    enemies = {F: _enemies(F) for F in order}
    idx = {F: i for i, F in enumerate(order)}
    return Battle(
        x=x, y=y, owner=owner, order=order, attacker=attacker, enemies=enemies,
        soak=soak,
        init={F: tuple((unit_kind(a), int(a["hp"]),
                        bool(a.get("retreat_to")),
                        int(a.get("retreat_cover", 100)))
                       for a in forces[F]) for F in order},
        enemy_idx=tuple(tuple(idx[G] for G in enemies[F]) for F in order),
        soak_list=tuple(soak[F] for F in order))


# ================================================================ 一轮推进
# ★ 性能：`_damage` 的输入里**只有兵种和撤退标记**影响输出（`unit_atk` 与 hp 无关）；
#   而 36 种骰子组合里有大量组合**算出同一份伤害向量**。所以：
#     ① 输出表按 (存活兵种, 撤退标记) 签名缓存 —— 同一签名不同 hp 的状态共用；
#     ② 一轮里先把 36 种组合**归并成"伤害向量 → 概率质量"**，只对不同的伤害向量推进。
#   实测 3v3 从 177 ms/帧 降到 ~20 ms/帧（见提交记录）。
_POWER_CACHE: dict[tuple, tuple[float, ...]] = {}


def _atk_sig(state_i: tuple) -> tuple:
    """一方"输出侧"的签名：只有兵种和撤退标记（hp 不影响输出）。"""
    return tuple((u[0], u[2]) for u in state_i)


def _power_by_die(b: Battle, sig: tuple) -> tuple[float, ...]:
    """该签名下、掷出 1..6 时**给每个敌人的伤害份额**（`share`，未打减伤）。

    `share = power / len(enemies)` —— ★ 均分（腹背受敌则兵力分散）。
    敌人个数在战斗中会变（一方被打光就少一个），所以份额表按签名缓存是不够的，
    这里**只按签名**缓存 atk 总和，敌人个数在外面除。
    """
    key = sig
    if key not in _POWER_CACHE:
        atk = 0
        for kind, retreat in sig:
            base = unit_atk({"type": kind})
            # 撤退中的军队输出 −80%（撤离途中无心恋战），**按军**计入攻击总和
            atk += base if not retreat else max(1, base * (100 - RETREAT_ATK_PENALTY) // 100)
        # _combat_power(atk, 0) = max(1, atk)；再 _round_damage(·, mod) = max(1, p*(100+mod)//100)
        _POWER_CACHE[key] = tuple(
            max(1, max(1, atk) * (100 + COMBAT_DIE_MOD[d]) // 100) for d in DIE_FACES)
    return _POWER_CACHE[key]


def _agg_damage(b: Battle, sig: tuple) -> dict[tuple[int, ...], float]:
    """★ "**存活兵种签名**" → `{伤害向量: 概率质量}`。

    这是本模块最关键的一处性能设计：伤害**只看兵种和撤退标记**（与 hp 无关），
    而 DP 里有几百个 hp 状态、签名却只有十几个 ⇒ 缓存签名级的结果，
    `_damage` 的调用量掉一个数量级（实测 3v3：5652 次 → 360 次）。
    """
    # ★ 缓存挂在 `Battle` **自己身上**（不是模块级按 `id(b)` 索引）—— `id()` 在对象
    #   回收后会被复用，模块级缓存会**静默拿错格的表**（soak/敌人关系不同）。这条踩过。
    hit = b.agg_cache.get(sig)
    if hit is not None:
        return hit
    n = len(sig)
    live = [bool(s) for s in sig]
    agg: dict[tuple[int, ...], float] = {}
    p_die = 1.0 / (len(DIE_FACES) ** n)
    for combo in _die_combos(n):
        dmg = [0] * n
        for i in range(n):
            if not live[i]:
                continue
            en = [j for j in b.enemy_idx[i] if live[j]]
            if not en:
                continue                        # 没敌人 ⇒ 不掷骰、不出手（引擎同）
            share = _power_by_die(b, sig[i])[combo[i] - 1] / len(en)
            for j in en:
                dmg[j] += max(1, round(share * (100 - b.soak_list[j]) / 100))
        dv = tuple(dmg)
        agg[dv] = agg.get(dv, 0.0) + p_die
    b.agg_cache[sig] = agg
    return agg


def _damage(b: Battle, state: tuple, dies: tuple[int, ...]) -> tuple[int, ...]:
    """一轮的伤害向量（按 `b.order` 的下标）—— `mp.py:1859-1875` 的逐行搬运。"""
    n = len(b.order)
    live = [bool(state[i]) for i in range(n)]
    dmg = [0] * n
    for i, F in enumerate(b.order):
        if not live[i]:
            continue
        en = [j for j in b.enemy_idx[i] if live[j]]   # ★ `enemy_idx` 里是**下标**，别再按名字查
        if not en:
            continue                            # 没敌人 ⇒ 不掷骰、不出手（引擎同）
        share = _power_by_die(b, _atk_sig(state[i]))[dies[i] - 1] / len(en)
        for j in en:
            dmg[j] += max(1, round(share * (100 - b.soak_list[j]) / 100))
    return tuple(dmg)


def _apply(b: Battle, state: tuple, dmg: tuple[int, ...]) -> tuple:
    """把伤害摊到各单位 —— `_spread` + 撤退减伤那半边（`mp.py:1876-1888`）。

    ★ **不丢阵亡者**（hp ≤ 0 原样留着）：调用方有时要按**下标**找某一支军
      （撤退保命），丢了下标就错位。丢弃交给 `_drop`。
    """
    out = []
    for i, units in enumerate(state):
        d = dmg[i]
        if not units or not d:
            out.append(units)
            continue
        flat = all(u[3] == 100 for u in units)   # 全员无撤退减伤 ⇒ 引擎走 `_spread`
        per, rem = divmod(d, len(units))
        keep = []
        for k, u in enumerate(units):
            share = per + (1 if k < rem else 0)
            keep.append((u[0], u[1] - (share if flat else share * u[3] // 100), u[2], u[3]))
        out.append(tuple(keep))
    return tuple(out)


def _drop(state: tuple) -> tuple:
    """移除阵亡者（引擎每轮结算后 `self.armies.remove(a)`）。"""
    return tuple(tuple(u for u in units if u[1] > 0) for units in state)


def _absorbed(b: Battle, state: tuple) -> bool:
    """还有没有任何一方**有活着的敌人**；没有 ⇒ 战斗定局（引擎下回合不再交战）。"""
    for i, units in enumerate(state):
        if units and any(state[j] for j in b.enemy_idx[i]):
            return False
    return True


def _survivors(b: Battle, state: tuple) -> frozenset[str]:
    return frozenset(F for i, F in enumerate(b.order) if state[i])


# ================================================================ DP（精确）
@dataclass
class Odds:
    """一格的推演结果（按势力）。`p_hold[F]` = F 是**唯一**幸存者（= 引擎里占地的那家）。"""
    p_win: dict[str, float] = field(default_factory=dict)     # 我方活、敌全灭
    p_lose: dict[str, float] = field(default_factory=dict)    # 我方全灭、敌还活
    p_draw: float = 0.0                                       # ★ 同归于尽（全场无人）
    p_hold: dict[str, float] = field(default_factory=dict)    # 唯一幸存 ⇒ 占地
    e_rounds: float = 0.0
    e_loss: dict[str, float] = field(default_factory=dict)    # 期望掉血（含阵亡者的余血）
    p_rounds: list[float] = field(default_factory=list)       # ★ P(打满 k 轮才定局)，下标 = 轮数
    n_states: int = 0
    truncated: float = 0.0                                    # 未收敛的质量（正常应恒 0）

    def round_bins(self, edges: tuple[int, ...] | None = None) -> list[float]:
        """★ P(轮数 ≤ edge) 的**累积**分档 —— "多久打完"喂网络用这个（定长、单调）。

        用户 2026-09-24：「还要报告**多少概率打几个回合**」——要的是**轮数的分布**，
        不是只有期望（期望会把"1 轮速胜"和"8 轮惨胜"抹成同一个数）。
        """
        edges = S.ROUND_BIN_EDGES if edges is None else edges
        cum = []
        for e in edges:
            cum.append(sum(p for k, p in enumerate(self.p_rounds) if k <= e))
        return cum

    def as_vec(self, me: str) -> list[float]:
        """喂网络的定长向量（`vocab.GRID_COMBAT` 那几条通道）。"""
        return [self.p_win.get(me, 0.0), self.p_lose.get(me, 0.0), self.p_draw,
                self.p_hold.get(me, 0.0),
                min(1.0, self.e_rounds / S.PROB_ROUND_SCALE),
                min(1.0, self.e_loss.get(me, 0.0) / S.PROB_LOSS_SCALE)]

    def report(self, sides: tuple[str, ...] | None = None) -> str:
        """人读的一行（终端/日志用）。`sides` 为 `None` ⇒ 全部势力。"""
        who = sides or tuple(self.p_win)
        head = " | ".join(
            f"{F}: 赢{self.p_win.get(F, 0):.0%} 输{self.p_lose.get(F, 0):.0%}"
            f" 占{self.p_hold.get(F, 0):.0%} 掉{self.e_loss.get(F, 0):.0f}hp"
            for F in who)
        # ★ 边界只从 `round_bins` 来（它读先验表）—— 原来这里又写了一遍 (1,2,3,5,8)，
        #   改一处漏一处
        rounds = " ".join(f"≤{e}:{p:.0%}" for e, p in
                          zip(S.ROUND_BIN_EDGES, self.round_bins()))
        return (f"{head} | 同归{self.p_draw:.0%} | 期望{self.e_rounds:.1f}轮"
                f" | 轮数 {rounds}")


def assess(b: Battle, *, max_states: int | None = None,
           max_rounds: int | None = None,
           retreat_units: frozenset[int] | None = None) -> Odds:
    """精确 DP。`retreat_units` = 这些**下标**的军本轮带撤退标记（撤退保命用）。

    ★ 吸收性有保证：只要一方还有活敌人，它每轮必吃 `≥1` 伤害（`max(1, …)`）
      ⇒ hp 严格下降 ⇒ 状态图无环。`max_states/max_rounds` 只是防呆上限，
      真被顶到会在 `truncated` 里报出来（**不允许静默**）。
    """
    # ★ 上限**每次调用时**从先验表读 —— 写进签名会在**导入时**绑死，改表不生效（不报错）
    max_states = S.ASSESS_MAX_STATES if max_states is None else max_states
    max_rounds = S.ASSESS_MAX_ROUNDS if max_rounds is None else max_rounds
    state0 = tuple(b.init[F] for F in b.order)
    if retreat_units:
        state0 = tuple(tuple((k, h, (idx in retreat_units) or r, c) for idx, (k, h, r, c) in enumerate(units))
                       for units in state0)
    memo: dict[tuple, tuple[dict[frozenset, float], dict[int, float], float,
                           dict[str, float]]] = {}

    def rec(state: tuple, depth: int):
        """→ (结局分布, **轮数分布**, 期望轮数, 期望掉血)。"""
        if _absorbed(b, state):
            return ({_survivors(b, state): 1.0}, {0: 1.0}, 0.0,
                    [0.0] * len(b.order))
        if state in memo:
            return memo[state]
        if depth >= max_rounds or len(memo) >= max_states:
            # ★ 兜底：把剩余质量压进"未定"，并让调用方看得见（不静默当成功）
            return {}, {}, 0.0, [0.0] * len(b.order)
        dist: dict[frozenset, float] = {}
        rh: dict[int, float] = {}                # ★ 轮数分布（原始计数，最后归一）
        er = 0.0
        ehp = [0.0] * len(b.order)
        n = len(b.order)
        # ★ 伤害只看"存活兵种签名"（与 hp 无关），而 hp 状态多、签名少
        #   ⇒ 整张 `{伤害向量: 概率质量}` 按签名缓存（`_agg_damage`）。
        #   同一签名下 36 种骰子还可能**算出同一份伤害** ⇒ 归并也是精确的（概率相加）。
        agg = _agg_damage(b, tuple(_atk_sig(s) for s in state))
        pre_hp = [sum(u[1] for u in s) for s in state]
        for dv, p in agg.items():
            # `_apply` + `_drop` **就地内联**（这是全模块最热的两行；
            # 每次 DP 要跑几千遍，函数调用开销本身就有分量）。口径同 `_apply`/`_drop`。
            nxt = []
            lost_all = []
            for i in range(n):
                units = state[i]
                d = dv[i]
                if not units or not d:
                    nxt.append(units)
                    lost_all.append(0)
                    continue
                flat = all(u[3] == 100 for u in units)
                per, rem = divmod(d, len(units))
                keep = []
                lost = 0
                for k, u in enumerate(units):
                    sh = per + (1 if k < rem else 0)
                    nh = u[1] - (sh if flat else sh * u[3] // 100)
                    lost += u[1] - nh          # ★ 含阵亡者"消失的血"（用 nh，不夹到 0）
                    if nh > 0:
                        keep.append((u[0], nh, u[2], u[3]))
                nxt.append(tuple(keep))
                lost_all.append(lost)
            sub, srhist, sr, sl = rec(tuple(nxt), depth + 1)   # 返回 (分布, 轮数分布, 期望轮数, 掉血)
            for k, v in sub.items():
                dist[k] = dist.get(k, 0.0) + v * p
            for k, v in srhist.items():          # 本轮已打 ⇒ 各支路轮数 +1
                rh[k + 1] = rh.get(k + 1, 0.0) + v * p
            er += p * (1.0 + sr)
            for i in range(n):
                ehp[i] += p * (lost_all[i] + sl[i])
        memo[state] = (dist, rh, er, ehp)
        return memo[state]

    dist, rh, er, ehp = rec(state0, 0)
    total = sum(dist.values())
    o = Odds(e_rounds=er, n_states=len(memo), truncated=max(0.0, 1.0 - total))
    for i, F in enumerate(b.order):
        # 赢 = 我活着、**所有敌人**都死了（可能是多方混战里活下来的两家之一 ⇒ 不算赢）
        o.p_win[F] = sum(p for s, p in dist.items()
                         if F in s and not (set(b.enemies[F]) & s))
        # 输 = 我全灭、且我至少还有一个敌人活着
        o.p_lose[F] = sum(p for s, p in dist.items()
                          if F not in s and (set(b.enemies[F]) & s))
        o.p_hold[F] = sum(p for s, p in dist.items() if s == {F})
        o.e_loss[F] = ehp[i] / total if total else 0.0
    o.p_draw = dist.get(frozenset(), 0.0)
    if total:
        for k in list(o.p_win):
            o.p_win[k] /= total
            o.p_lose[k] /= total
            o.p_hold[k] /= total
        o.p_draw /= total
        o.e_rounds = er / total
    # ★ 轮数分布：规整成"下标 = 轮数"的定长表（0..max_rounds），且**归一化到 dist 的总质量**
    #   （`_absorbed` 的根节点轮数为 0 ⇒ 表里恒有 p_rounds[0]，但那种局面调用方不会拿到）
    o.p_rounds = [0.0] * (max_rounds + 1)
    for k, v in rh.items():
        if 0 <= k <= max_rounds:
            o.p_rounds[k] = v / total if total else 0.0
    return o


_DIE_CACHE: dict[int, tuple] = {}


def _die_combos(n: int) -> tuple:
    """n 方的骰子组合（`6^n` 条，每条等概率）。缓存住 —— 每步都算会白烧 CPU。"""
    if n not in _DIE_CACHE:
        from itertools import product
        _DIE_CACHE[n] = tuple(product(DIE_FACES, repeat=n))
    return _DIE_CACHE[n]


# ================================================================ ② 可及增援
def reachable_reinforcements(world, b: Battle, side: str, *,
                             exclude_on_cell: bool = True) -> list[dict]:
    """★ "**够得着**这格的军"—— 分角色，因为攻守的入场动作不同。

    用户 2026-09-24：「注意**进攻方的增援是 atk，防御方的增援是 mv**，有点区别」

    · `side` 是**进攻方** ⇒ 用 `_reachable(..., for_attack=True)`：终点额外允许
      敌国领土与驻军格（那是要打的），中途仍必须可通行。
    · `side` 是**守方** ⇒ 用 `_reachable(...)`：★ **只在自家/盟国地成立**——
      无主地上有交战敌军时 `_mv_wall` 拒 mv（只认 atk），所以野地防守**没有增援**，
      这本身就是要喂给模型的信息（"这格救不了"）。
    """
    if side not in b.order:
        return []
    atk_side = side in b.attacker
    out = []
    # ★ 这里拿的是 **`World`**（不是 `Sandbox`）—— 别调 `world.armies_of`，那是沙盒的方法，
    #   引擎的 `World` 只有 `armies`（踩过：测试直接报 `'World' object has no attribute`）。
    for a in world.armies:
        if a["owner"] != side or a["hp"] <= 0:
            continue
        if a.get("moved_turn") == world.turn:
            continue                            # 本回合额度已用 ⇒ 来不了
        if exclude_on_cell and (a["x"], a["y"]) == (b.x, b.y):
            continue                            # 已在场上，不算"增援"
        reach = world._reachable(side, a, for_attack=atk_side)
        if (b.x, b.y) in reach:
            out.append(a)
    return out


def assess_with_reinforcements(world, b: Battle, *, max_states: int | None = None) -> Odds:
    """把**双方各自够得着的增援**都算进去后的概率（用户：「mv 和 atk 的增援会改变什么」）。"""
    extra = {F: reachable_reinforcements(world, b, F) for F in b.order}
    extra = {F: ms for F, ms in extra.items() if ms}
    if not extra:
        return assess(b, max_states=max_states)
    b2 = build(world, b.x, b.y, extra=extra)
    return assess(b2 or b, max_states=max_states)


# ================================================================ ③ 撤退保命
def retreat_odds(world, x: int, y: int, army: dict) -> float:
    """**这支军撤了之后活下来的概率**。

    撤退不立刻结算（`mp.py:1765-1780`）：它留在战场照常吃本轮伤害，只是
    · 防御方 ⇒ `retreat_cover = RETREAT_DEF_COVER(50)`（只受一半）
    · 进攻方 ⇒ cover 100（全额，撤退没有免费午餐）
    · 自己本轮输出 −80%
    结算后自动脱离。⇒ P(保命) = **一轮结算后它还在**的概率（枚举 6^n 组合即得）。
    """
    b = build(world, x, y)
    if b is None:
        return 1.0                              # 没在交战 ⇒ 撤了当然活
    side = army["owner"]
    if side not in b.order:
        return 1.0
    # 找出这支军在 state 里的下标（在 `side` 的**活军列表**里的位置 —— 与 `build` 同序；
    # ★ `_apply` 不丢死者 ⇒ 下标全程稳定，这正是它不丢的原因）。
    ids = [id(a) for a in world.armies
           if (a["x"], a["y"]) == (x, y) and a["hp"] > 0 and a["owner"] == side]
    try:
        k = ids.index(id(army))
    except ValueError:
        return 1.0
    # 撤退的减伤档位由引擎决定：我方在该格"未参战"、或本身就是格主 ⇒ 守方档
    attacking = any(m["owner"] == side and m.get("engaged")
                    and (m["x"], m["y"]) == (x, y) for m in world.armies)
    cover = RETREAT_DEF_COVER if (not attacking or b.owner == side) else 100

    i = b.order.index(side)
    state = tuple(b.init[F] for F in b.order)
    units = list(state[i])
    units[k] = (units[k][0], units[k][1], True, cover)   # 标记撤退：输出 −80% + 减伤
    state = state[:i] + (tuple(units),) + state[i + 1:]

    survive = 0.0
    n = len(b.order)
    p_die = 1.0 / (len(DIE_FACES) ** n)
    for combo in _die_combos(n):
        raw = _apply(b, state, _damage(b, state, combo))
        if raw[i][k][1] > 0:                    # ★ 按稳定下标取 ⇒ 不会被前面的死者挤位
            survive += p_die
    return survive


# ================================================================ 便捷入口
def cell_odds(world, x: int, y: int, *, with_reinf: bool = False) -> Odds | None:
    """一格 → `Odds`（没在打 ⇒ `None`）。这是**给编码层用的入口**。"""
    b = build(world, x, y)
    if b is None:
        return None
    return assess_with_reinforcements(world, b) if with_reinf else assess(b)


def engaged_cells(world) -> list[tuple[int, int]]:
    """当前**正在交战**的格（= `_resolve_battles` 遍历的那个集合）。"""
    return sorted({(a["x"], a["y"]) for a in world.armies
                   if a.get("engaged") and a["owner"] != BARBARIAN})


def snapshot(world, *, with_reinf: bool = False, max_states: int | None = None
             ) -> dict[tuple[int, int], Odds]:
    """★ **本回合**所有交战格的概率 —— **每回合重算，绝不跨回合缓存**。

    用户 2026-09-24：「**每个回合都要按照当前状态重算概率**」。

    这条不是性能建议、是**正确性**要求：战斗每打一轮 hp 就变一次，减伤、存活兵种、
    "够得着谁"全跟着变 ⇒ 上一回合的概率**这一回合就是错的**。
    别把 `Odds` 挂在 `Battle` 或编码器上复用 —— 它只对**生成它的那一帧**成立。

    ★ 这里也**不缓存**（`assess` 内部的 `memo` 只在单次调用内活）：调用方每帧调一次，
      拿到的是那一帧的答案。要在同一帧内多处用，就**自己拿着返回值传下去**
      （`encode.py` 就是这么做的：`snapshot()` 一次，逐格取）。
    """
    out: dict[tuple[int, int], Odds] = {}
    for c in engaged_cells(world):
        b = build(world, *c)
        if b is None:
            continue
        out[c] = (assess_with_reinforcements(world, b, max_states=max_states)
                  if with_reinf else assess(b, max_states=max_states))
    return out