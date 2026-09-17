# -*- coding: utf-8 -*-
"""经济缩放 S：把规则表里的**全部"量"字段**整体 ×S（金与物一起放）。

用户 2026-09-14 口述：「**产 1 木抖 10% 还是 1** —— 先加缩放，再谈抖动」。
用户 2026-09-17：「我打算把**抖动改成缩放**……数值都在 `balance.py` 了，很好做」。

## 为什么现在好做（与 2026-09-14 那版的区别）

那版要对着 `game.*` 与 `mp.*` **两个命名空间**逐字段 `setattr` —— 因为当时数值散在各模块、
标量更是好几处副本。现在 `balance.py` 是**唯一入口**，`game.py`/`mp.py` 都是
`from balance import ...` **转口同一个对象**（已实测 `is` 为真）：

| | 容器（dict/list） | 标量（int/float） |
|---|---|---|
| 转口方式 | 同一对象 ⇒ **就地改 `balance` 一处，全引擎都看见** | **值拷贝** ⇒ 改 `balance.X` **不会**动 `mp.X` |
| 本模块做法 | `_write` 就地改 `balance.*` | 显式白名单 + 逐个回写 `balance` 与转口方 |

⇒ **容器不用再管**（这版省掉了旧版一半代码），**标量仍必须点名**。

## 缩放面（与 `rl/jitter.py` 的字段清单对齐：凡"量"都进，凡"计数/百分比/拓扑"都不进）

- `MARKET`（金/单位）、`MARKET_DEPTH`（价弹性的量纲，×S 后同比例）
- `BUILDINGS[*]`：`cost`（含城堡逐级表）/ `wood` / `energy_out` / **`energy`**（工厂/兵营/市政厅
  维持耗电）/ 字典量 `outputs` / `inputs` / `fuel` / `army_cost` / `effects.gold_base` /
  `effects.gold_per_slot`
- `UNIT_TYPES[*]`：`supply`（单位/回合）/ `recruit`（字典量）
- `START_RES`（开局全部资源，含黄金 1500）
- 标量：`SPY_COST` / `DIPLO_COST` / `DIPLO_CENTER_MIN_COST`
- **`rl.env.AMOUNTS`**（候选"量档位"）—— 它住在 `rl/env.py`、且是 tuple（改不了），必须**重绑**

**不进**：`hp`/`atk`/`speed`、`max_level`/`limit`/`min_slots`/`cap_resource`/`recruit_cap`/
`militia_cap`、一切**百分比**（`build_penalty`/`defense`/`RETRETE_DEF_COVER`/`PRICE_*`）、
`MAX_SLOTS`、`ARMY_*` 伤害。

## 三个静默坑（2026-09-14 实测换来的，一个都不能省）

1. **`energy` 漏网** —— `energy_out ×10` 而维持耗电不 ×10 ⇒ **电网凭空充足** ⇒ 行为爆炸
   （领土 158→25）。所以 `_B_FIELDS` 里必须有它。
2. ★★**jitter 基线反噬** —— `jitter.apply(seed, 0)`（=restore）会把**它自己的快照**写回规则表；
   若那份快照是缩放**前**的真值，则每次 `env.reset()` 都会把 ×S **悄悄抹掉**
   （"看着生效、其实没生效"）。⇒ 缩放写完**必须让 jitter 重抓基线**（`_resync_jitter_baseline`）。
3. **`AMOUNTS` 也是量** —— 候选量档（1..16）不放大 ⇒ **相对流量小 10 倍**（`len` 不变，
   特征侧无感，只在行为上显形）。⇒ 上面那行"必须重绑"。

## 通用守卫（同事 2026-09-14 提的，本模块实现成 `coverage_report()`）

**缩放前后列一张全部经济量对照表，逐项打 ×S 对勾** —— 漏项**当场可见**，
而不是靠"跑起来发现领土 158→25"。`coverage_report()` 把 `balance` 里**每一个模块级标量**
列出来并标"缩放/不缩放"，让**漏项无处可藏**。

## 不变式

- `S=1` 是**显式 no-op**（restore 到真值）；默认路径**逐位不变**。
- `apply(S)` **幂等**：任何时刻都从 `_TRUE` 派生，不在上一次结果上再乘。
- 与 jitter **不能同时生效**（组合语义未定义）—— 撞上就 raise，不许悄悄叠加。
"""
from __future__ import annotations

import copy

# —— 缩放面白名单（字段级，见文件头）——
_CONTAINERS = ("MARKET", "MARKET_DEPTH", "BUILDINGS", "UNIT_TYPES", "START_RES")
_B_FIELDS = ("cost", "wood", "energy_out", "energy")          # ← energy 那个坑就在这行
_B_DICTS = ("outputs", "inputs", "fuel", "army_cost")
_B_EFFECTS = ("gold_base", "gold_per_slot")
_U_FIELDS = ("supply",)
_U_DICTS = ("recruit",)
# 标量：`from balance import X` 是**值拷贝** ⇒ 必须回写到每个转口方
_SCALARS = (
    ("SPY_COST", ("mp",)),
    ("DIPLO_COST", ("mp",)),
    ("DIPLO_CENTER_MIN_COST", ("game", "mp")),
)

_TRUE: dict | None = None      # 真值快照（首次 apply 时抓）
_S: int = 1


def _mods():
    """延迟 import：`rl.env` 会 import 本模块，模块顶层 import 它就成了环。"""
    import balance
    import game
    import mp
    import rl.env as E
    return {"balance": balance, "game": game, "mp": mp, "env": E}


def _snapshot() -> None:
    global _TRUE
    if _TRUE is not None:
        return
    import rl.jitter as J
    if J._REC is not None:
        raise RuntimeError(
            "jitter 抖动正生效（_REC 非空）：此时抓的「真值」是抖过的。先 restore 再 scale。")
    M = _mods()
    B = M["balance"]
    _TRUE = {name: copy.deepcopy(getattr(B, name)) for name in _CONTAINERS}
    _TRUE["AMOUNTS"] = tuple(M["env"].AMOUNTS)
    _TRUE["scalars"] = {}
    for name, holders in _SCALARS:
        _TRUE["scalars"][name] = (getattr(B, name, None),
                                  {h: getattr(M[h], name, None) for h in holders})


def _write(S: int) -> None:
    """从 `_TRUE` 派生出 ×S 的表，**就地**写进 `balance`（容器与 game/mp 共享同一对象）。"""
    B = _mods()["balance"]
    T = _TRUE
    for k, v in T["MARKET"].items():
        B.MARKET[k] = v * S
    for k, v in T["MARKET_DEPTH"].items():
        B.MARKET_DEPTH[k] = v * S
    for b, tb in T["BUILDINGS"].items():
        cur = B.BUILDINGS[b]
        for f in _B_FIELDS:
            if f in tb:
                v = tb[f]
                cur[f] = [x * S for x in v] if isinstance(v, list) else v * S
        for f in _B_DICTS:
            if f in tb:
                cur[f] = {k: v * S for k, v in tb[f].items()}
        if "effects" in tb:
            eff = dict(tb["effects"])
            for f in _B_EFFECTS:
                if f in eff:
                    eff[f] = eff[f] * S
            cur["effects"] = eff
    for t, tu in T["UNIT_TYPES"].items():
        cur = B.UNIT_TYPES[t]
        for f in _U_FIELDS:
            if f in tu:
                cur[f] = tu[f] * S
        for f in _U_DICTS:
            if f in tu:
                cur[f] = {k: v * S for k, v in tu[f].items()}
    for k, v in T["START_RES"].items():
        B.START_RES[k] = v * S
    # ★坑 3：候选量档也是量，且住在 rl/env.py（tuple ⇒ 只能重绑模块全局）
    M = _mods()
    M["env"].AMOUNTS = tuple(v * S for v in T["AMOUNTS"])
    # ★标量：值拷贝 ⇒ 逐个回写。balance 也要写（它是权威），转口方跟着写。
    for name, holders in _SCALARS:
        bval, hvals = T["scalars"][name]
        if bval is not None:
            setattr(M["balance"], name, bval * S)
        for h, hv in hvals.items():
            if hv is not None and hasattr(M[h], name):
                setattr(M[h], name, hv * S)


def _resync_jitter_baseline() -> None:
    """★坑 2 的堵法：缩放写完，让 jitter 的基线**重抓成当前（已缩放）表**。

    否则 `jitter.apply(ms, 0)`（=restore）会把**缩放前**的快照写回，
    每次 `env.reset()` 都把 ×S 悄悄抹掉。
    """
    import rl.jitter as J
    if J._REC is not None:
        raise RuntimeError("jitter 正在生效：缩放×抖动的组合未定义，禁止悄悄叠加")
    if J._TRUE is not None:
        J._TRUE = None
        J._ensure_snapshot()


def apply(S) -> dict:
    """把经济量放大到 ×S（幂等：任何时刻都从真值派生）。`S=1` ⇒ 恢复真值。"""
    global _S
    S = int(S)
    if S < 1:
        raise ValueError(f"S 必须是正整数，得到 {S}")
    # ★**真 no-op**：S=1 且从未缩放过 ⇒ 什么都不做。
    #   若在这里仍去 `_snapshot()` + `_resync_jitter_baseline()`，就会**动到 jitter 的基线** ——
    #   默认路径（S=1）本该逐位不变，而且 jitter 正生效时那步会 raise
    #   （实测：`rules_jitter>0` 的 env 第二次 reset 必炸，68 个测试挂在这）。
    if S == 1 and _TRUE is None:
        return {"scale": 1, "touched": "noop（从未缩放过）"}
    _snapshot()
    if S != _S:
        _write(S)
        _S = S
    _resync_jitter_baseline()
    return {"scale": S, "touched": "MARKET/MARKET_DEPTH/BUILDINGS/UNIT_TYPES/START_RES/scalars/AMOUNTS"}


def restore() -> None:
    apply(1)


def current() -> int:
    return _S


def coverage_report() -> dict:
    """★把 `balance` 里**每一个模块级标量**列出来并标"缩放 / 不缩放"。

    这是防"漏一个字段 ⇒ 比例失衡 ⇒ 行为爆炸"的那道**可见**守卫（文件头「通用守卫」）。
    返回 `{"scaled": {...}, "not_scaled": {...}}`；两个集合的并集 = 全部标量。
    用法（写测试或人眼过一遍）：`print(scale.coverage_report())`。
    """
    import balance as B
    scaled_names = {n for n, _ in _SCALARS}
    scaled, not_scaled = {}, {}
    for n in dir(B):
        if n.startswith("_"):
            continue
        v = getattr(B, n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            (scaled if n in scaled_names else not_scaled)[n] = v
    return {"scaled": scaled, "not_scaled": not_scaled,
            "containers": {n: type(getattr(B, n)).__name__ for n in _CONTAINERS}}
