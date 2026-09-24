# -*- coding: utf-8 -*-
"""沙盒观测编码：局面 → (网格, 全局, 军队 token) + 候选集。

    布局的唯一来源是 `rl/vocab.py`（网格 14 通道 / 全局 14 标量 / 军队 token 10 列）。

    ★★★ 两条"设计不变量"（用户 2026-09-24 两次指出，都踩过）
    ────────────────────────────────────────────────────────
    ① **相对坐标，不是绝对**（「对于每个模型，都使用相对坐标，而不是绝对」）
       绝对坐标下模型学到的是"甲永远在 (1,1)、乙永远在 (6,6)" ⇒ **换个地图就废**；
       相对坐标可泛化，且**两国共用同一套视角**（对称 ⇒ 甲的经验对乙也成立）。
    ② **观测框 = 视野的外接矩形，与地图大小无关**（「你不能整网格大小，必须是
       **地图大小无关**的设计，**和以前一样**」）
       旧线口径：网格是「**可见区外接框**」，**逐帧可变**（边界跟着视野走）。
       8×8 与 40×40 用**同一套编码**，模型也学不到"地图多大"。
       ⚠ 我中间写过固定半径 `RADIUS=8`（钉死 17×17）—— 那不随视野变，是错的。
       ⇒ 由此 `encode_grid` 的 H/W **逐帧可变**，批处理时由 `train.collate` 补零对齐。

       ★ 副作用（**这正是要的**）：大地图上视野只覆盖一小片 ⇒ 框就一小片，
         模型看到的永远是"我周围这一圈"，**不需要**知道 40×40 的全貌。

    ★★ 两条纪律（错了不报错、只在训练里慢慢烂掉）
    ────────────────────────────────────────────
    1. **迷雾**：只编码**看得见**的（`world.visible_to`）—— 那是 v9 当年堵掉的越权之一。
    2. ★ **市政厅是公开的例外**（`_public_buildings`）：视野内的他国地**只公开城堡与市政厅**
       （"看不见就打不着"）⇒ **厅进了视野就能看到、不需要 `spy`/换图**。

    ★ 候选集**已经在 `sandbox.legal()` 里屏蔽/试探过**（走不到的位置、已动过的军、
      交战中的军；**无视野的邻格给 move+attack 两条路**）⇒ 这里拿到的是干净候选。
"""
from __future__ import annotations

import numpy as np

from . import vocab as V
from .sandbox import END, PLAYERS


# ============================================================ 谁占这一格
def _defense_of(world, x: int, y: int, owner) -> int:
    """本格**总减伤%** = 引擎的 `_defense_pct`（地形 × 城堡**已合并**在引擎里）。

    ★ 城堡不单列（用户：「**也不应该看城堡**」）—— 它本来就是防御的一部分。
    """
    try:
        return int(world._defense_pct(x, y, owner or "野人"))
    except Exception:                              # noqa: BLE001  地图边界等
        return 0


def _move_cost_of(world, x: int, y: int) -> int:
    """骑兵进这一格的**移动代价**（用户点名的"移动属性（对应骑兵）"）。

    取 `game.unit_move_cost({"type": "骑"}, 地形)`；骑兵 `speed=2` ⇒ 森林/山地这类
    高代价地形对它的相对影响最大（步兵 speed=1 反正只能走一格，分辨不出差别）。
    """
    from game import unit_move_cost
    try:
        return int(unit_move_cost({"type": "骑"}, world.tile_terrain(x, y)))
    except Exception:                              # noqa: BLE001
        return 1


def _owner_class(world, name: str, x: int, y: int) -> int:
    """归属通道下标（`vocab.OWNER_CHANNELS`）：

    `0=self · 1=ally · 2=rival · 3=neutral · 4=barbarian`

    ★ **盟友单列**（用户 2026-09-24：「还有盟友和中立」）：引擎对这两类的判定正好**相反**
      —— 盟友的地**可 mv 不可 atk**（`_mv_wall` 放行、`attack` 拒），
      敌国的地**可 atk 不可 mv**。混成一类，网络就分不出"这一格该不该打"。
    """
    o = world.owned_by(x, y)
    if o == name:
        return 0
    if o is not None:
        try:
            if world.allied_between(name, o):
                return 1                       # ★ 盟友
        except Exception:                      # noqa: BLE001
            pass
        return 2                               # 对手（含中立国——它不可 atk，但 mv 也不可）
    for a in world.armies:
        if a["owner"] == "野人" and a.get("hp", 0) > 0 and (a["x"], a["y"]) == (x, y):
            return 4
    return 3                                   # 无主


# ============================================================ 观测框（★与地图大小无关）
def frame_of(sb, me: str) -> tuple[int, int, int, int]:
    """我方视角的**观测框** `(x0, y0, H, W)` = **视野的外接矩形**。

    旧线口径（`rl/transformer.py` / 旧 `model.py` 原话）：「观测网格是『**可见区外接框**』，
    尺寸逐帧可变（地图尺寸也逐局可变），写死会在换尺寸时静默取错格」。
    用户 2026-09-24：「必须是**地图大小无关**的设计，**和以前一样**」。

    ⇒ **没有固定半径**：框跟着视野走（这也是为什么大地图上模型只看自己周围那一圈）。
    极端情况（一支军都没有、视野空）⇒ 退回核心周围 1 格，保证框非空。
    """
    mask = _vision(sb.world, me)
    if not mask:
        hx, hy = _home_cell(sb, me)
        return hx, hy, 1, 1
    xs = [c[0] for c in mask]
    ys = [c[1] for c in mask]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return x0, y0, y1 - y0 + 1, x1 - x0 + 1


def to_frame(sb, me: str, x: int, y: int) -> tuple[int, int]:
    """地图绝对坐标 → **框内坐标**；不在框里 ⇒ `(-1,-1)`（调用方决定怎么办）。"""
    x0, y0, h, w = frame_of(sb, me)
    i, j = y - y0, x - x0
    if 0 <= i < h and 0 <= j < w:
        return int(i), int(j)
    return -1, -1


def encode_grid(sb, me: str) -> np.ndarray:
    """局面 → `(GRID_CHANNELS, H, W)`，**H/W 逐帧可变**（= 视野外接框，见 `frame_of`）。"""
    w = sb.world
    x0, y0, h, ww = frame_of(sb, me)
    foe = _other(me)
    mask = _vision(w, me)
    g = np.zeros((V.GRID_CHANNELS, h, ww), dtype=np.float32)
    for i in range(h):
        for j in range(ww):
            x, y = x0 + j, y0 + i
            if not (0 <= x < sb.size and 0 <= y < sb.size):
                continue                                  # 框超出地图 ⇒ 保持全 0
            t = w.tiles.get((x, y))
            visible = (x, y) in mask
            # ★★ 地形**扁平化为两个军事属性**（用户 2026-09-24：「看不见地形特征，
            #   也不应该看城堡，扁平化为**防御属性**和**移动属性**（对应骑兵）…
            #   地形…**对于军事价值很低**」）—— 军事上只值"好不好守"与"走得快不快"。
            owner_here = w.owned_by(x, y)
            g[V.GRID_DEFENSE, i, j] = _defense_of(w, x, y, owner_here) / 100.0
            g[V.GRID_MOVE, i, j] = _move_cost_of(w, x, y) / 2.0
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
    """局面 → `(GLOB_SIZE,)` 标量（归一到 0~1）。**不含任何绝对坐标。**"""
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
    """军队 token `(k, A_WIDTH)`：我的全部 + **看得见的**敌方。位置 = **相对家的偏移**。"""
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


def candidate_features(sb, acts=None) -> np.ndarray:
    """候选动作 → 一行特征 `(K, CAND_WIDTH)`。

    ★ `acts` 可外部传入（`sandbox.legal()` 的结果）—— **别在这里再调一次**：
      实测（cProfile，2026-09-24）`legal()` 每支军要跑**两次 `_reachable`**（Dijkstra），
      而 `obs_of` 原来同时调 `candidate_features` 与 `candidate_xy` ⇒ **一军一步 4 次搜索**。
      统一由调用方算一次传进来。

    列（冻结）：0..2 kind one-hot(move/attack/end) · 3..5 目标格归属 one-hot
    （自己/对手/无主）· 6 ★目标格是不是**对手的市政厅** · 7 **到我家核心的距离**/**地图边长**
    · 8 执行军的 hp/100 · 9 该军本回合是否已动（哨兵，恒 0）· 10 目标格是否在视野内
    · 11 end_turn 标记。

    ★ 列 7 是**相对我的**距离（不是绝对坐标），且除以**地图边长**（不是框尺寸）——
      这样"走一格"的分量在不同地图上一致。
    """
    w = sb.world
    me = sb.current_player()
    foe = _other(me) if me else None
    hx, hy = _home_cell(sb, me)
    mask = _vision(w, me) if me else set()
    by_id = {a["id"]: a for a in sb.armies_of(me)} if me else {}
    out = []
    for aid, kind, x, y in (sb.legal() if acts is None else acts):
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
            row[7] = max(abs(x - hx), abs(y - hy)) / float(sb.size)   # ★ 相对距离 / 地图边长
            a = by_id.get(aid)
            row[8] = (a.get("hp", 0) / 100.0) if a else 0.0
            row[9] = 1.0 if (a and a.get("moved_turn") == w.turn) else 0.0
            row[10] = 1.0 if (x, y) in mask else 0.0
        out.append(row)
    return np.array(out, dtype=np.float32)


def candidate_xy(sb, acts=None) -> np.ndarray:
    """每个候选的**框内坐标** `(K, 2)`（整数索引）—— 网络拿它去卷积特征图里 gather。

    与 `encode_grid` **同一坐标系**（视野外接框），所以"候选看到的那一格"与"网格里的
    那一格"严格对应；框外的候选（视野外的试探格、`end_turn`）⇒ `(-1,-1)`，网络侧夹到 0。
    """
    me = sb.current_player()
    out = []
    for aid, kind, x, y in (sb.legal() if acts is None else acts):
        if aid == END or me is None:
            out.append((-1, -1))
        else:
            out.append(to_frame(sb, me, int(x), int(y)))
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