# -*- coding: utf-8 -*-
"""沙盒观测编码：局面 → (网格, 全局, 军队 token) + 候选集。

    布局的唯一来源是 `rl/vocab.py`（网格 14 通道 / 全局 14 标量 / 军队 token 10 列）——
    本文件只负责**按那个布局把沙盒的局面填进去**，不自己定维度。

    ★★★ **一律用相对坐标**（用户 2026-09-24：「对于每个模型，都使用**相对坐标**，
    而不是绝对」）
    ────────────────────────────────────────────────────────────────────────
    绝对坐标下模型学到的是"甲永远在 (1,1)、乙永远在 (6,6)" ⇒ **换个地图就废**；
    相对坐标下学到的是"敌人在那个方向、离我多远" ⇒ **可泛化**，而且**两国共用同一套
    视角**（两国对称 ⇒ 甲的经验对乙也成立）。
    旧线就是这么做的：网格是「**可见区外接框**」、落点用「**相对家的偏移**」
    （`rl/transformer.py` 原话："不是扁平下标 —— 扁平下标绑死网格形状"）。

    具体：
      · **网格**：以**我方核心**为中心取 `S×S` 的框（`S = 2·radius+1`），越界处全 0。
      · **军队 token**：位置是 `(x−hx)/POS_SCALE`、`(y−hy)/POS_SCALE`。
      · **候选坐标**（`candidate_xy`）：**框内**坐标，供网络 gather 空间特征。

    ★★ 两条纪律（错了不报错、只在训练里慢慢烂掉）
    ────────────────────────────────────────────
    1. **迷雾**：只编码**看得见**的（`world.visible_to`）。看不见的敌军不进网格、不进 token
       —— 那是 v9 当年堵掉的"四处越权偷看"之一。
    2. ★ **市政厅是公开的例外**（`_public_buildings`）：视野内的他国地**只公开城堡与市政厅**
       （引擎原话"看不见就打不着"）⇒ **厅进了视野就能看到、不需要 `spy`/换图**。
       这正是"攻取国祚"能瞄准的前提；**别把这条当成越权**。

    ★ 候选集**已经在 `sandbox.legal()` 里屏蔽/试探过**（走不到的位置、已动过的军、
      交战中的军；**无视野的邻格给 move+attack 两条路**）⇒ 这里拿到的是干净候选。
"""
from __future__ import annotations

import numpy as np

from . import vocab as V
from .sandbox import END, PLAYERS

RADIUS = 8          # 相对框半径（8×8 时 S = 17，覆盖全图 + 余量；大地图时按需调小）


# ============================================================ 谁占这一格
def _owner_class(world, name: str, x: int, y: int) -> int:
    """归属通道下标（`vocab.OWNER_CHANNELS`）：0=自己 1=对手 2=无主 3=野人驻守。"""
    o = world.owned_by(x, y)
    if o == name:
        return 0
    if o is not None:
        return 1
    for a in world.armies:
        if a["owner"] == "野人" and a.get("hp", 0) > 0 and (a["x"], a["y"]) == (x, y):
            return 3
    return 2


# ============================================================ 相对框
def frame_of(sb, me: str, radius: int = RADIUS) -> tuple[int, int, int]:
    """我方视角的**相对框**：`(hx, hy, S)` —— 原点是我方核心，`S = 2·radius+1`。"""
    hx, hy = _home_cell(sb, me)
    return hx, hy, 2 * int(radius) + 1


def to_frame(sb, me: str, x: int, y: int, radius: int = RADIUS) -> tuple[int, int]:
    """地图绝对坐标 → **框内坐标**（越界返回 `(-1,-1)`，由调用方决定怎么处理）。"""
    hx, hy, s = frame_of(sb, me, radius)
    i, j = x - hx + radius, y - hy + radius
    if 0 <= i < s and 0 <= j < s:
        return int(i), int(j)
    return -1, -1


def encode_grid(sb, me: str, radius: int = RADIUS) -> np.ndarray:
    """局面 → `(GRID_CHANNELS, S, S)`，**以我方核心为中心**（★相对坐标，见文件头）。"""
    w = sb.world
    hx, hy, s = frame_of(sb, me, radius)
    foe = _other(me)
    mask = _vision(w, me)
    g = np.zeros((V.GRID_CHANNELS, s, s), dtype=np.float32)
    for i in range(s):
        for j in range(s):
            x, y = hx - radius + i, hy - radius + j      # 地图绝对坐标
            if not (0 <= x < sb.size and 0 <= y < sb.size):
                continue                                  # 越界 ⇒ 保持全 0（框外）
            t = w.tiles.get((x, y))
            visible = (x, y) in mask
            terr = w.tile_terrain(x, y)
            if terr in V.TERRAIN:
                g[V.GRID_TERRAIN0 + V.TERRAIN.index(terr), i, j] = 1.0
            g[V.GRID_OWNER0 + _owner_class(w, me, x, y), i, j] = 1.0
            g[V.GRID_VISIBLE, i, j] = 1.0 if visible else 0.0
            if not visible:
                continue                     # ★ 看不清的格：军队与厅一概不写
            mine = foehp = 0.0
            for a in w.armies:
                if a.get("hp", 0) <= 0 or (a["x"], a["y"]) != (x, y):
                    continue
                if a["owner"] == me:
                    mine += a["hp"]
                elif a["owner"] == foe:
                    foehp += a["hp"]
            g[V.GRID_MY_HP, i, j] = min(1.0, mine / 100.0)
            g[V.GRID_FOE_HP, i, j] = min(1.0, foehp / 100.0)
            if t is not None and t["buildings"].get("市政厅", 0) > 0:
                if t["owner"] == me:
                    g[V.GRID_MY_HALL, i, j] = 1.0
                elif t["owner"] == foe:
                    g[V.GRID_FOE_HALL, i, j] = 1.0
    return g


# ============================================================ 全局标量
def encode_glob(sb, me: str) -> np.ndarray:
    """局面 → `(GLOB_SIZE,)` 标量（全部归一到 0~1）。**不含任何绝对坐标。**"""
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


# ============================================================ 军队 token
def encode_armies(sb, me: str) -> np.ndarray:
    """军队 token `(k, A_WIDTH)`：我的全部 + **看得见的**敌方。**位置是相对家的偏移。**"""
    w = sb.world
    foe = _other(me)
    mask = _vision(w, me)
    hx, hy = _home_cell(sb, me)
    rows = []
    for a in sorted(w.armies, key=lambda a: (a["owner"] != me, a["id"])):
        if a.get("hp", 0) <= 0 or a["owner"] not in (me, foe):
            continue
        if a["owner"] == foe and (a["x"], a["y"]) not in mask:
            continue                          # ★ 看不见的敌军不进 token
        rows.append(_army_row(sb, a, me, hx, hy))
    return np.array(rows, dtype=np.float32) if rows else np.zeros((0, V.A_WIDTH), np.float32)


def _army_row(sb, a: dict, me: str, hx: int, hy: int) -> list[float]:
    row = [0.0] * V.A_WIDTH
    row[V.A_OWNER0 + (0 if a["owner"] == me else 1)] = 1.0
    kind = a.get("type", "步")
    if kind in V.UNIT:
        row[V.A_UNIT0 + V.UNIT.index(kind)] = 1.0
    # ★ 相对家的偏移（不是绝对坐标）—— 旧线 `POS_SCALE` 同口径："相对家的绝对尺度"
    row[V.A_X] = (a["x"] - hx) / V.POS_SCALE
    row[V.A_Y] = (a["y"] - hy) / V.POS_SCALE
    row[V.A_HP] = a.get("hp", 0) / 100.0
    row[V.A_MOVED] = 1.0 if a.get("moved_turn") == sb.world.turn else 0.0
    row[V.A_ENGAGED] = 1.0 if a.get("engaged") else 0.0
    return row


# ============================================================ 候选集
CAND_WIDTH = 12


def candidate_features(sb, radius: int = RADIUS) -> np.ndarray:
    """`sandbox.legal()` 的每个候选 → 一行特征 `(K, CAND_WIDTH)`。

    列（冻结）：0..2 kind one-hot(move/attack/end) · 3..5 目标格归属 one-hot
    （自己/对手/无主）· 6 ★目标格是不是**对手的市政厅** · 7 **到我家核心的距离**/S
    · 8 执行军的 hp/100 · 9 该军本回合是否已动（哨兵，恒 0）· 10 目标格是否在视野内
    · 11 end_turn 标记。

    ★ 列 7 用的是**相对**距离（到我家核心），不是绝对坐标。
    """
    w = sb.world
    me = sb.current_player()
    foe = _other(me) if me else None
    hx, hy, s = frame_of(sb, me, radius) if me else (0, 0, 2 * radius + 1)
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
            row[7] = max(abs(x - hx), abs(y - hy)) / float(s)     # ★ 相对距离
            a = by_id.get(aid)
            row[8] = (a.get("hp", 0) / 100.0) if a else 0.0
            row[9] = 1.0 if (a and a.get("moved_turn") == w.turn) else 0.0
            row[10] = 1.0 if (x, y) in mask else 0.0
        out.append(row)
    return np.array(out, dtype=np.float32)


def candidate_xy(sb, radius: int = RADIUS) -> np.ndarray:
    """每个候选的**框内坐标** `(K, 2)`（整数索引）—— 网络拿它去卷积特征图里 gather。

    ★ **框内**（相对我方核心），不是地图绝对坐标 —— 与网格同坐标系，
      这样"候选看到的那一格"和"网格里的那一格"严格对应（见文件头的相对坐标口径）。
    `end_turn` 没有目标格 ⇒ `(-1,-1)`，网络侧夹到 0。
    """
    me = sb.current_player()
    out = []
    for aid, kind, x, y in sb.legal():
        if aid == END or me is None:
            out.append((-1, -1))
        else:
            out.append(to_frame(sb, me, int(x), int(y), radius))
    return np.array(out, dtype=np.int64)


# ============================================================ 小工具
def _other(name: str | None) -> str:
    return next((n for n in PLAYERS if n != name), PLAYERS[1])


def _vision(world, name: str) -> set:
    from ruleai.v11plus import pathfind
    return pathfind.vision_mask(world, name)


def _armies(world, name: str) -> list[dict]:
    return [a for a in world.armies if a["owner"] == name and a.get("hp", 0) > 0]


def _home_cell(sb, name: str | None) -> tuple[int, int]:
    c = sb.core_of(name) if name else None
    return c if c else (0, 0)


def _hp_frac(armies: list[dict], cap: int) -> float:
    return min(1.0, sum(a.get("hp", 0) for a in armies) / max(1.0, 100.0 * max(1, cap)))