# -*- coding: utf-8 -*-
"""沙盒观测编码：局面 → (网格, 全局, 军队 token) + 候选集特征。

    布局的唯一来源是 `rl/vocab.py`（网格 14 通道 / 全局 14 标量 / 军队 token 10 列）——
    本文件只负责**按那个布局把沙盒的局面填进去**，不自己定维度。

    ★★ 两条纪律（错了不报错，只在训练里慢慢烂掉，所以写在这儿）
    ─────────────────────────────────────────────────────────
    1. **迷雾**：只编码**看得见**的东西（`world.visible_to`）。看不见的敌军不进网格、
       不进 token —— 那是 v9 当年堵掉的"四处越权偷看"之一（`ruleai/v9.py` 记着）。
    2. ★ **市政厅是公开的例外**（`_public_buildings`）：视野内的他国地**只公开城堡与
       市政厅**（引擎原话"看不见就打不着"）⇒ **厅只要进了视野就能看到，不需要 `spy` /
       换图**（那是兵营/工厂/农田那些"内政底细"才需要的）。这正是"攻取国祚"能瞄准的
       前提；**别把这条当成越权**。
       实测印证（2026-09-24）：甲开局视野 15 格、乙核心 (6,6) 在视野外
       ⇒ `GRID_FOE_HALL` 全 0 —— 正确没写。

    ★ 候选集**已经在 `sandbox.legal()` 里屏蔽过**（走不到的位置 / 已动过的军 / 交战中的军
      都不给）⇒ 本文件的 `candidate_features()` 拿到的就是干净候选，不必再过滤一遍
      （这也是 `exec_head` 不需要了的原因，见 `rl/PLAN.md` §3.3）。
"""
from __future__ import annotations

import numpy as np

from . import vocab as V
from .sandbox import END, PLAYERS


# ============================================================ 谁占这一格
def _owner_class(world, name: str, x: int, y: int) -> int:
    """归属通道下标（`vocab.OWNER_CHANNELS`）：0=自己 1=对手 2=无主 3=野人驻守。"""
    o = world.owned_by(x, y)
    if o == name:
        return 0
    if o is not None:
        return 1
    # 无主：看看格上有没有野人（8×8 上野人铺满、每格一支）
    for a in world.armies:
        if a["owner"] == "野人" and a.get("hp", 0) > 0 and (a["x"], a["y"]) == (x, y):
            return 3
    return 2


def encode_grid(sb, me: str) -> np.ndarray:
    """局面 → `(GRID_CHANNELS, size, size)` 的 float32 网格。"""
    w = sb.world
    n = sb.size
    g = np.zeros((V.GRID_CHANNELS, n, n), dtype=np.float32)
    foe = _other(me)
    mask = _vision(w, me)
    for x in range(n):
        for y in range(n):
            t = w.tiles.get((x, y))
            visible = (x, y) in mask
            # ---- 地形（未物化的格也读得到地形：地形是地图属性）----
            terr = w.tile_terrain(x, y)
            if terr in V.TERRAIN:
                g[V.GRID_TERRAIN0 + V.TERRAIN.index(terr), x, y] = 1.0
            # ---- 归属 ----
            g[V.GRID_OWNER0 + _owner_class(w, me, x, y), x, y] = 1.0
            # ---- 视野 ----
            g[V.GRID_VISIBLE, x, y] = 1.0 if visible else 0.0
            if not visible:
                continue                      # ★ 看不清的格：军队与厅一概不写
            # ---- 军队（只写看得见的）----
            mine = foehp = 0.0
            for a in w.armies:
                if a.get("hp", 0) <= 0 or (a["x"], a["y"]) != (x, y):
                    continue
                if a["owner"] == me:
                    mine += a["hp"]
                elif a["owner"] == foe:
                    foehp += a["hp"]
            g[V.GRID_MY_HP, x, y] = min(1.0, mine / 100.0)
            g[V.GRID_FOE_HP, x, y] = min(1.0, foehp / 100.0)
            # ---- 市政厅（★公开：这条不受迷雾限制，但仍只在"看得见"时写坐标）----
            if t is not None and t["buildings"].get("市政厅", 0) > 0:
                if t["owner"] == me:
                    g[V.GRID_MY_HALL, x, y] = 1.0
                elif t["owner"] == foe:
                    g[V.GRID_FOE_HALL, x, y] = 1.0
    return g


def encode_glob(sb, me: str) -> np.ndarray:
    """局面 → `(GLOB_SIZE,)` 标量（全部归一到 0~1）。"""
    w = sb.world
    foe = _other(me)
    mine, his = _armies(w, me), _armies(w, foe)
    cap_m, cap_f = sb.cap_of(me), sb.cap_of(foe)
    n2 = float(sb.size * sb.size)
    vals = {
        "turn_frac": sb.turn / max(1, sb.t_max),
        "my_tiles": sb.tiles_of(me) / n2,
        "foe_tiles": sb.tiles_of(foe) / n2,
        "my_armies": len(mine) / 8.0,
        "foe_armies": len(his) / 8.0,
        "my_cap": cap_m / 8.0,
        "foe_cap": cap_f / 8.0,
        "my_hall": 1.0 if sb.alive(me) else 0.0,
        "foe_hall": 1.0 if sb.alive(foe) else 0.0,
        "my_hp_frac": _hp_frac(mine, cap_m),
        "foe_hp_frac": _hp_frac(his, cap_f),
        "my_moved": sum(1 for a in mine if a.get("moved_turn") != w.turn) / 8.0,
        "foe_moved": sum(1 for a in his if a.get("moved_turn") != w.turn) / 8.0,
        "last_ok": 1.0 if sb.last_ok else 0.0,
    }
    return np.array([vals[k] for k in V.GLOB], dtype=np.float32)


def encode_armies(sb, me: str) -> np.ndarray:
    """军队 token：`(k, A_WIDTH)`（我的全部 + **看得见**的敌方）。没军时 `k=0`。"""
    w = sb.world
    foe = _other(me)
    mask = _vision(w, me)
    rows = []
    for a in sorted(w.armies, key=lambda a: (a["owner"] != me, a["id"])):
        if a.get("hp", 0) <= 0 or a["owner"] not in (me, foe):
            continue
        if a["owner"] == foe and (a["x"], a["y"]) not in mask:
            continue                          # ★ 看不见的敌军不进 token
        rows.append(_army_row(sb, a, me))
    return np.array(rows, dtype=np.float32) if rows else np.zeros((0, V.A_WIDTH), np.float32)


def _army_row(sb, a: dict, me: str) -> list[float]:
    row = [0.0] * V.A_WIDTH
    row[V.A_OWNER0 + (0 if a["owner"] == me else 1)] = 1.0
    kind = a.get("type", "步")
    if kind in V.UNIT:
        row[V.A_UNIT0 + V.UNIT.index(kind)] = 1.0
    hx, hy = _home(sb, me)
    row[V.A_X] = (a["x"] - hx) / V.POS_SCALE
    row[V.A_Y] = (a["y"] - hy) / V.POS_SCALE
    row[V.A_HP] = a.get("hp", 0) / 100.0
    row[V.A_MOVED] = 1.0 if a.get("moved_turn") == sb.world.turn else 0.0
    row[V.A_ENGAGED] = 1.0 if a.get("engaged") else 0.0
    return row


# ============================================================ 候选集
CAND_WIDTH = 12          # 候选特征列数（见 `candidate_features` 的注释）


def candidate_features(sb) -> np.ndarray:
    """把 `sandbox.legal()` 的每个候选编成一行特征 → `(K, CAND_WIDTH)`。

    列（顺序冻结）：
        0..2   kind one-hot（move / attack / end_turn）
        3..5   目标格归属 one-hot（自己 / 对手 / 无主）—— `end_turn` 全 0
        6      ★ 目标格是不是**对手的市政厅**（国祚）
        7      目标格离我方核心的切比雪夫距离 / 8
        8      执行这支军的 hp / 100（`end_turn` = 0）
        9      这支军本回合是否已动（恒 0 —— 屏蔽过了，留着当哨兵列）
        10     目标格是否在视野内
        11     `end_turn` 标记（哨兵）
    """
    w = sb.world
    me = sb.current_player()
    foe = _other(me) if me else None
    hx, hy = _home(sb, me) if me else (0, 0)
    mask = _vision(w, me) if me else set()
    by_id = {a["id"]: a for a in sb.armies_of(me)} if me else {}
    out = []
    for aid, kind, x, y in sb.legal():
        row = [0.0] * CAND_WIDTH
        if aid == END:
            row[2] = 1.0
            row[11] = 1.0
        else:
            row[0 if kind == "move" else 1] = 1.0
            cls = _owner_class(w, me, x, y)
            row[3 + min(cls, 2)] = 1.0
            t = w.tiles.get((x, y))
            row[6] = 1.0 if (t is not None and t["owner"] == foe
                             and t["buildings"].get("市政厅", 0) > 0) else 0.0
            row[7] = max(abs(x - hx), abs(y - hy)) / float(sb.size)
            a = by_id.get(aid)
            row[8] = (a.get("hp", 0) / 100.0) if a else 0.0
            row[9] = 1.0 if (a and a.get("moved_turn") == w.turn) else 0.0
            row[10] = 1.0 if (x, y) in mask else 0.0
        out.append(row)
    return np.array(out, dtype=np.float32)


# ============================================================ 小工具
def _other(name: str | None) -> str:
    return next((n for n in PLAYERS if n != name), PLAYERS[1])


def _vision(world, name: str) -> set:
    from ruleai.v11plus import pathfind
    return pathfind.vision_mask(world, name)


def _armies(world, name: str) -> list[dict]:
    return [a for a in world.armies if a["owner"] == name and a.get("hp", 0) > 0]


def _home(sb, name: str | None) -> tuple[int, int]:
    c = sb.core_of(name) if name else None
    return c if c else (0, 0)


def _hp_frac(armies: list[dict], cap: int) -> float:
    return min(1.0, sum(a.get("hp", 0) for a in armies) / max(1.0, 100.0 * max(1, cap)))