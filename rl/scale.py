# -*- coding: utf-8 -*-
"""经济缩放 S：把规则表按**单位换算**整体放大，让抖动终于抖得动。

用户 2026-09-14：「**产 1 木抖 10% 还是 1** —— 先加缩放，再谈抖动」。
用户 2026-09-18：「大缩放下，抖动才有意义」「小尺度抖不动」「按这个改，且产出之类的都要抖动」。

## 一、为什么必须分「次数」（2026-09-18 实测定的，别再改回去）

一次缩放就是**换单位**：物理单位 ×a、金单位 ×b ⇒ **单价必须 ×(b/a)**。
只有这样 `物值 = (a·q)·(b/a·p) = b·(q·p)` 才与「金 ×b」**同度**，
整个游戏才是自身的等比放大，老师/引擎/模型的行为才逐条不变。

（踩过：单价**不**跟着 a、b 一起调时，`物值` 与 `金` **次数不同** ⇒ **游戏本身变了** ——
实测 S=10 时老师第 4 回合不去建兵营、第 5 回合不征兵；引擎 `build_econ` 的
`capex = 造价 + 木×价` 也混了次数 ⇒ ROI 排序随 S 漂。）

**要抖动有分辨率**，就得让原来小的数变大：

| 要抖的 | 现值 | 目标 | 条件 |
|---|---|---|---|
| 产量 / 木耗 / 征兵料 / 能源（`outputs` 等） | 1~6 | ×S | **a = S** |
| **单价**（`MARKET`） | 2~8 | ×S | **b/a = S** |

⇒ 两样都要 ⇒ 唯一解 **`a = S`、`b = S²`**：

```
物量 ×S        单价 ×S        金 ×S²
```

验算：`物值 = (S·q)(S·p) = S²·qp` 与 `金 = S²·G` 同度 ✓；而产量 1→10、单价 2→20，
**±10% 抖动终于落在整数上** ✓✓（这就是"大缩放下抖动才有意义"）。

⇒ **连带好处：老师与引擎一行都不用改就自动齐次**：

```
_budget = 金(S²) + 余货(S·q)×价(S·p) = S²·(G + Σqp)      ← 全 S² ✓
_need   = 造价(S²) + 木(S·q)×价(S·p) = S²·(C + w·p)      ← 全 S² ✓
build_econ: capex 与 per_turn 同度 ⇒ payback 与 S 无关 ✓
```

## 二、字段分类（**每个字段的次数**，漏一个就比例失衡且不报错）

- **次数 2（金）**：`BUILDINGS[*].cost`（含城堡逐级表）/ `army_cost` /
  `effects.gold_base` / `effects.gold_per_slot`；`START_RES["黄金"]`；
  标量 `SPY_COST` / `DIPLO_COST` / `DIPLO_CENTER_MIN_COST` / `LETTER_*`（四个）
- **次数 1（物）**：`BUILDINGS[*].wood` / `energy` / `energy_out` /
  `outputs` / `inputs` / `fuel`；`UNIT_TYPES[*].supply` / `recruit`；`START_RES` 金以外项
- **次数 1（单价/深度）**：`MARKET`（金/单位）· `MARKET_DEPTH`（单位）
- **`rl.env.AMOUNTS`**（候选量档）：住处不在 balance ⇒ 单独重绑

**不进**：`hp`/`atk`/`speed`、`max_level`/`limit`/`min_slots`/`cap_resource`/`recruit_cap`/
`militia_cap`、一切**百分比**（`build_penalty`/`defense`/`PRICE_*`/`MARKET_SPREAD`）、
`MAX_SLOTS`、`ARMY_*` 伤害 —— 它们是**计数 / 比例 / 拓扑**，与单位无关。

## 三、两个静默坑（2026-09-14 实测换来的）

1. **`energy` 漏网** ⇒ `energy_out ×S` 而维持耗电不 ×S ⇒ **电网凭空充足**、行为爆炸。
2. ★★**jitter 基线反噬** ⇒ `jitter.apply(ms, 0)`（=restore）会把**它自己的快照**写回；
   若那份快照是缩放**前**的 ⇒ 每次 `env.reset()` 都把缩放**悄悄抹掉**。
   ⇒ 缩放写完**必须让 jitter 重抓基线**（`_resync_jitter_baseline`）。

## 四、守卫

`coverage_report()` 逐字段标**次数**（标量 + `BUILDINGS`/`UNIT_TYPES` 的每个字段）——
漏项**当场可见**，不靠"跑起来发现领土 158→25"。（同事 2026-09-14 提的那道守卫，
2026-09-18 升级成带次数。）

## 五、不变式

- `S=1` 是**真 no-op**（不抓快照、不碰 jitter）；默认路径逐位不变。
- `apply(S)` **幂等**（任何时刻从真值派生，不在上次结果上再乘）。
- **奖励/回报/惩罚一律以 S=1 的消费为单位**（`ZhanguoEnv.spend_units` 做 ÷S²），
  否则 S 会偷偷把学习率 ×S²、把 `--invalid-penalty` 相对削弱 S² 倍。
- 与 jitter **不能同时生效**（组合语义未定义）—— 撞上就 raise。
"""
from __future__ import annotations

import copy

# ---- 字段次数表（物=1、金=2；单价/深度见 _PRICE_DEG / _DEPTH_DEG）----
_B_DEG = {                       # BUILDINGS[*] 的字段（effects 里的键也在这查）
    "cost": 2, "army_cost": 2, "gold_base": 2, "gold_per_slot": 2,
    "wood": 1, "energy": 1, "energy_out": 1,
    "outputs": 1, "inputs": 1, "fuel": 1,
}
_U_DEG = {"supply": 1, "recruit": 1}          # UNIT_TYPES[*]
_START_DEG = {"黄金": 2}                       # START_RES：只有黄金是金，其余是物
_PRICE_DEG = 1                                 # MARKET：单价 ×S（见文件头 §一）
_DEPTH_DEG = 1                                 # MARKET_DEPTH：单位数
# 标量（`from balance import X` 是**值拷贝** ⇒ 必须回写到每个转口方）
_SCALARS = (
    ("SPY_COST", 2, ("mp",)),
    ("DIPLO_COST", 2, ("mp",)),
    ("DIPLO_CENTER_MIN_COST", 2, ("game", "mp")),
    ("LETTER_COST", 2, ("mp",)),
    ("LETTER_COST_ALLY", 2, ("mp",)),
    ("LETTER_CENTER_DISCOUNT", 2, ("mp",)),
    ("LETTER_COST_MIN", 2, ("mp",)),
)
_CONTAINERS = ("MARKET", "MARKET_DEPTH", "BUILDINGS", "UNIT_TYPES", "START_RES")

_TRUE: dict | None = None
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
    for name, _deg, holders in _SCALARS:
        _TRUE["scalars"][name] = (getattr(B, name, None),
                                  {h: getattr(M[h], name, None) for h in holders})


def _scaled(v, deg: int, S: int):
    """按次数放大：次数 1 ⇒ ×S，次数 2 ⇒ ×S²（金）。"""
    if deg == 1:
        return v * S
    if deg == 2:
        return v * S * S
    raise ValueError(f"次数只能是 1 或 2，得到 {deg}")


def _write(S: int) -> None:
    """从 `_TRUE` 派生 ×(S^次数) 的表，**就地**写进 `balance`（容器与 game/mp 共享同一对象）。"""
    B = _mods()["balance"]
    T = _TRUE
    for k, v in T["MARKET"].items():
        B.MARKET[k] = _scaled(v, _PRICE_DEG, S)
    for k, v in T["MARKET_DEPTH"].items():
        B.MARKET_DEPTH[k] = _scaled(v, _DEPTH_DEG, S)
    for b, tb in T["BUILDINGS"].items():
        cur = B.BUILDINGS[b]
        # ★`effects` **单独一支**：它的次数写在 `_B_DEG` 的**键**上（gold_base/gold_per_slot），
        #   而 `_B_DEG` 的**字段**里没有 "effects" 这一项 —— 所以它**不能**混在下面那个
        #   `for f, deg in _B_DEG` 循环里（那样这个分支永远不会被执行）。
        #   实测栽过：市政厅 `gold_per_slot` 没放大，而 `coverage_report()` 还报它"次数 2"
        #   ⇒ **守卫谎报**，比没有守卫更危险。
        if "effects" in tb:
            cur["effects"] = {k: (_scaled(v, _B_DEG[k], S) if k in _B_DEG else v)
                              for k, v in tb["effects"].items()}
        for f, deg in _B_DEG.items():
            if f not in tb or f in ("gold_base", "gold_per_slot"):
                continue                 # 后两个是 effects 的**内键**，不是字段
            src = tb[f]
            if isinstance(src, dict):    # outputs/inputs/fuel/army_cost：整只同次数
                cur[f] = {k: _scaled(v, deg, S) for k, v in src.items()}
            elif isinstance(src, list):  # 城堡逐级造价表
                cur[f] = [_scaled(x, deg, S) for x in src]
            else:
                cur[f] = _scaled(src, deg, S)
        # ⚠ 上面也**不能**写成"跳过 dict 字段"：`outputs`/`inputs`/`fuel` 都是 dict，
        #   跳过它们 = **产出/投料不缩放**（实测：装备产出 2→2 应为 2→20）。
        #   两处都是"漏一个字段 ⇒ 比例失衡"那类坑，而且**不报错**。
    for t, tu in T["UNIT_TYPES"].items():
        cur = B.UNIT_TYPES[t]
        for f, deg in _U_DEG.items():
            if f in tu:
                src = tu[f]
                cur[f] = ({k: _scaled(v, deg, S) for k, v in src.items()}
                          if isinstance(src, dict) else _scaled(src, deg, S))
    for k, v in T["START_RES"].items():
        B.START_RES[k] = _scaled(v, _START_DEG.get(k, 1), S)
    # ★候选量档：住在 rl/env.py、是 tuple ⇒ 只能重绑模块全局（物 ⇒ 次数 1）
    M = _mods()
    M["env"].AMOUNTS = tuple(v * S for v in T["AMOUNTS"])
    # ★标量：值拷贝 ⇒ balance 与每个转口方都要回写
    for name, deg, holders in _SCALARS:
        bval, hvals = T["scalars"][name]
        if bval is not None:
            setattr(M["balance"], name, _scaled(bval, deg, S))
        for h, hv in hvals.items():
            if hv is not None and hasattr(M[h], name):
                setattr(M[h], name, _scaled(hv, deg, S))


def _resync_jitter_baseline() -> None:
    """★坑 2 的堵法：缩放写完，让 jitter 的基线**重抓成当前（已缩放）表**。

    否则 `jitter.apply(ms, 0)`（=restore）会把**缩放前**的快照写回，
    每次 `env.reset()` 都把缩放悄悄抹掉。
    """
    import rl.jitter as J
    if J._REC is not None:
        raise RuntimeError("jitter 正在生效：缩放×抖动的组合未定义，禁止悄悄叠加")
    if J._TRUE is not None:
        J._TRUE = None
        J._ensure_snapshot()


def apply(S) -> dict:
    """把规则表按单位换算放大（物 ×S、价 ×S、金 ×S²）。`S=1` ⇒ 恢复真值。"""
    global _S
    S = int(S)
    if S < 1:
        raise ValueError(f"S 必须是正整数，得到 {S}")
    # ★真 no-op：S=1 且从未缩放过 ⇒ 什么都不做（不抓快照、不碰 jitter）。
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
    """★**逐字段标次数**的守卫。

    把 `balance` 里每个模块级标量、`BUILDINGS`/`UNIT_TYPES` 的每个字段都列出来并标次数
    （1=物/价、2=金、"不缩放"）。漏一个字段 ⇒ 比例失衡 ⇒ 行为爆炸，而且**不会报错**
    —— 这张表让它**当场可见**。
    """
    import balance as B
    names = {n: d for n, d, _h in _SCALARS}
    scalars = {}
    for n in dir(B):
        if n.startswith("_"):
            continue
        v = getattr(B, n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            scalars[n] = names.get(n, "不缩放")
    bfields = {}
    for _b, tb in B.BUILDINGS.items():
        for f in tb:
            if f in _B_DEG:
                bfields[f] = _B_DEG[f]
            elif f == "effects":
                for k in tb[f]:
                    bfields[f"effects.{k}"] = _B_DEG.get(k, "不缩放")
    return {
        "scalars": scalars,
        "building_fields": {k: bfields[k] for k in sorted(bfields)},
        "unit_fields": dict(_U_DEG),
        "containers": {n: ({"MARKET": _PRICE_DEG, "MARKET_DEPTH": _DEPTH_DEG}.get(n, 1))
                       for n in _CONTAINERS},
    }
