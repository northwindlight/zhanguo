# -*- coding: utf-8 -*-
"""训练期**域随机化**：就地抖动 `game.*` 的规则表（`TOKEN_DESIGN.md` §10.4）。

为什么要有
----------
只把数值喂进输入还不够：训练分布里只有**一个**价格点时，模型学到的就是"这一个点的
答案"，仍然把数值当常数。要真正"改数值不必重炼"，必须让它在训练里**见过一整族数值**，
学的才是「给定这组数值该怎么打」（§10.2 的那条推理）。

用户举的例子：石油能源厂 240 → 180。抖的就是这类经济量。

三条纪律（都是踩过或差点踩的）
------------------------------
1. **只改 dict/list **里**的值，绝不替换容器**。`mp.py` 是 `from game import BUILDINGS`
   —— 拿到的是同一个 dict 对象，就地改立即生效；写 `game.BUILDINGS = {...}` 只改
   本模块看到的引用，**引擎毫无察觉**（值拷贝）。也正因为是就地改，**引擎文件一行不用改**，
   保住了「mp.py/game.py/mp_ai.py/mp_run.py 与 main 逐字相同」这条契约。
2. **每次都从真值出发**（首次调用时快照），不在上一次抖动结果上再抖 ——
   否则连跑几十局就是随机游走，分布漂到没人认得的地方，而且不可复现。
3. **默认关**，评估/看海/对拍一律真值。开了才抖，`restore()` 还原。
   评估期抖的话，"这局为什么输了"里永远藏着一个看不见的随机规则表。

随机什么、不随机什么：见 `TOKEN_DESIGN.md` §10.4 的字段表。**不碰**可行性拓扑类字段
（`cap_resource`/`limit`/`min_slots`/`max_level` —— 它们决定"哪儿能建"，一抖就换了游戏），
也**不碰** `UNIT_TYPES[*].supply`（`spend_rules.py` 有一份硬编码副本，抖了会让
引擎结算与老师的账对不上）。
"""
from __future__ import annotations

import copy
import math
import random

import game

# 抖动幅度的**符号学**：`amount=0.2` = 每个量按对数均匀在 [×0.8, ×1.2] 内取。
# 为什么对数均匀：价格/造价的**相对**变化才有意义（240→288 与 1→1.2 同类），
# 线性均匀会让小量的绝对变化被放大、大量的被压缩。
_SALT = 0x5A17E1  # seed 派生用的盐，别改（改了 = 所有跑过的随机化不可复现）

# 逐字段策略（与 §10.4 的表一一对应）
#   抖：cost / wood / energy_out、outputs / inputs / fuel、MARKET 全表、unit hp / atk
#   不抖：cap_resource / min_slots / limit / max_level（可行性拓扑）、unit.supply（副本问题）
#   ⚠ 未列入：`energy`（工厂耗电）—— §10.4 的表没写它，本版按表执行；要抖就加进
#     `_BUILDING_SCALAR`（它是 1~2 的小整数，得走 `_jitter_small_int`）。
_BUILDING_SCALAR = ("cost", "wood", "energy_out")   # cost 可能是**逐级表**（城堡）→ 逐项抖
_BUILDING_SMALL_INT = ("outputs", "inputs", "fuel")   # 小整数产出/投料
_UNIT_SCALAR = ("hp", "atk")
_MARKET_ALL = True                          # 基准价全抖（含黄金：它是"1 单位黄金 = 10 金"）

_TRUE: dict | None = None                   # 真值快照（第一次 apply 时抓）
_REC: dict | None = None                    # 当前生效的抖动记录（restore 后为 None）


def _ensure_snapshot() -> None:
    global _TRUE
    if _TRUE is None:
        _TRUE = {
            "buildings": copy.deepcopy(game.BUILDINGS),
            "units": copy.deepcopy(game.UNIT_TYPES),
            "market": dict(game.MARKET),
        }


def _factor(rng: random.Random, amount: float) -> float:
    """对数均匀因子：`exp(U(ln(1-a), ln(1+a)))`，两端等概率。"""
    lo, hi = math.log(max(1e-6, 1.0 - amount)), math.log(1.0 + amount)
    return math.exp(rng.uniform(lo, hi))


def _jitter_scalar(v, rng: random.Random, amount: float):
    """数值（可能是逐级表）→ 抖动后的同形状值。整数保持整数。"""
    if isinstance(v, (list, tuple)):
        return [_jitter_scalar(x, rng, amount) for x in v]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    out = v * _factor(rng, amount)
    return int(max(1, round(out))) if isinstance(v, int) else float(out)


def _jitter_small_int(v, rng: random.Random, amount: float):
    """小整数（产出/投料多是 1~2）→ **必须保持整数**，所以不能照抄乘法。

    先按对数均匀乘再取整；若取整后没变化（1×1.2→1 这种），再按 `amount` 的概率
    **走一格**（±1，下限 0）—— 否则 `1` 永远是 `1`，这类字段等于没随机化。
    为什么不干脆允许小数产出：那会让 `res` 里的库存变成浮点，而引擎里到处都是
    `//`、`<= 0`、`int()` 的整数口味假设 —— 那是另一件事，不在本版。
    """
    if not isinstance(v, int) or isinstance(v, bool):
        return v
    new = max(0, int(round(v * _factor(rng, amount))))
    if new == v and rng.random() < amount:
        step = 1 if (v == 0 or rng.random() < 0.5) else -1
        new = max(0, v + step)
    return new


def apply(seed: int, amount: float) -> dict:
    """按 `seed` 抖一套规则表并**就地写进 `game.*`**；返回记录（写进 run 目录用）。

    `amount <= 0` 时等价于 `restore()`（默认关就是走这条）。
    """
    _ensure_snapshot()
    if amount <= 0:
        restore()
        return {}
    rng = random.Random(int(seed) ^ _SALT)
    assert _TRUE is not None

    b_rec: dict[str, dict] = {}
    for name, true_b in _TRUE["buildings"].items():
        cur, rec = {}, {}
        for f, v in true_b.items():
            if f in _BUILDING_SCALAR:
                cur[f] = _jitter_scalar(v, rng, amount)
                rec[f] = cur[f]
            elif f in _BUILDING_SMALL_INT and isinstance(v, dict):
                cur[f] = {k: _jitter_small_int(x, rng, amount) for k, x in v.items()}
                rec[f] = cur[f]
            else:
                cur[f] = copy.deepcopy(v)      # 可行性拓扑类字段：原样（见模块 docstring）
        game.BUILDINGS[name].update(cur)       # ★就地改：mp.py 与 env 看到同一份
        b_rec[name] = rec

    u_rec: dict[str, dict] = {}
    for name, true_u in _TRUE["units"].items():
        cur, rec = {}, {}
        for f, v in true_u.items():
            if f in _UNIT_SCALAR:
                cur[f] = _jitter_scalar(v, rng, amount)
                rec[f] = cur[f]
            else:
                cur[f] = copy.deepcopy(v)
        game.UNIT_TYPES[name].update(cur)
        u_rec[name] = rec

    m_rec: dict[str, int] = {}
    for good, base in _TRUE["market"].items():
        new = max(1, int(round(base * _factor(rng, amount)))) if _MARKET_ALL else base
        game.MARKET[good] = new
        m_rec[good] = new

    global _REC
    _REC = {"seed": int(seed), "amount": float(amount),
            "buildings": b_rec, "units": u_rec, "market": m_rec}
    return _REC


def restore() -> None:
    """把规则表还原成真值（就地写回；评估/看海/对拍前必须调）。"""
    global _REC
    if _TRUE is None:
        _REC = None
        return
    for name, true_b in _TRUE["buildings"].items():
        game.BUILDINGS[name].clear()
        game.BUILDINGS[name].update(copy.deepcopy(true_b))
    for name, true_u in _TRUE["units"].items():
        game.UNIT_TYPES[name].clear()
        game.UNIT_TYPES[name].update(copy.deepcopy(true_u))
    game.MARKET.clear()
    game.MARKET.update(_TRUE["market"])
    _REC = None


def current() -> dict | None:
    """当前生效的抖动记录（None = 真值）。"""
    return _REC


def is_on() -> bool:
    return _REC is not None
