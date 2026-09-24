# -*- coding: utf-8 -*-
"""沙盒观测编码：局面 → **窗口 token 组** + 下标形态候选 + 网格。

    三块输出（布局的唯一来源是 `rl/vocab.py` 与 `rl/features.py`）
    ──────────────────────────────────────────────────────────
      · `grid  (C,H,W)`       候选按目标格 gather 空间特征（`model.PolicyNet`）
      · `win   {g,a}`         ★ **窗口**：`g` 全局摘要 / `a` 军队 token
      · `cand  {...}`         ★ **下标形态**：`type_idx` / 落点 / `army_idx` / 内容 / 标记

    ★★★ 两条"设计不变量"（用户 2026-09-24 两次指出，都踩过）
    ────────────────────────────────────────────────────────
    ① **相对坐标，不是绝对**（「对于每个模型，都使用相对坐标，而不是绝对」）
       绝对坐标下模型学到的是"甲永远在 (1,1)、乙永远在 (6,6)" ⇒ **换个地图就废**；
       相对坐标可泛化，且**两国共用同一套视角**（对称 ⇒ 甲的经验对乙也成立）。
    ② **观测框 = 视野的外接矩形，与地图大小无关**（「你不能整网格大小，必须是
       **地图大小无关**的设计，**和以前一样**」）
       8×8 与 40×40 用**同一套编码**，模型也学不到"地图多大"。
       ★ 归一尺度（`pos_scale`）取**地图边长** `sandbox.size`，**不再写死 8.0**
         （§11「`POS_SCALE=8`、几处 `/8.0` 写死 ⇒ 参数化」）。

    ★★ 两条纪律（错了不报错、只在训练里慢慢烂掉）
    ────────────────────────────────────────────
    1. **迷雾**：只编码**看得见**的（`world.visible_to`）—— 那是 v9 当年堵掉的越权之一。
    2. ★ **市政厅是公开的例外**（`_public_buildings`）：视野内的他国地**只公开城堡与市政厅**
       （"看不见就打不着"）⇒ **厅进了视野就能看到、不需要 `spy`/换图**。

    ★ 规则表数值走**第二路**（`features`，现算）：军队 token 尾部挂兵种数值、
      候选挂"执行军兵种数值 ⊕ 目标格地形数值"、全局 token 挂骰子/撤退/续战常数。
"""
from __future__ import annotations

import numpy as np

from . import features as F
from . import vocab as V
from .sandbox import PLAYERS


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
    """骑兵进这一格的**移动代价**（用户点名的"移动属性（对应骑兵）"）。"""
    from game import unit_move_cost
    try:
        return int(unit_move_cost({"type": "骑"}, world.tile_terrain(x, y)))
    except Exception:                              # noqa: BLE001
        return 1


def _owner_class(world, name: str, x: int, y: int, by_cell: dict | None = None) -> int:
    """归属类别下标（`vocab.OWN_*`，**六类**）—— 逐条对齐引擎 `_mv_wall` / `_atk_target_ok`：

    | 类别 | 引擎怎么判 | 能不能 mv | 能不能 atk |
    |---|---|---|---|
    | `SELF` | 我的地 | ✅ | ✅ |
    | `ALLY` | `allied_between` | ✅ | ❌ |
    | `RIVAL` | `war_between` | ❌（"用 atk 夺格"） | ✅ |
    | `NEUTRAL_NATION` | 有主、非盟非敌 | ❌（"先结盟或先宣战"） | ❌ |
    | `UNOWNED` | 无主、无驻军 | ✅ | ✅（走进去就占） |
    | `BARBARIAN` | 无主、有野人驻守 | ❌（"有敌军驻守"） | ✅ |

    ★ 用户 2026-09-24 晚：「主干候选有**中立国家**和盟友吗」—— 前一版把 `NEUTRAL_NATION`
      并进了 `RIVAL`（只判"不是我的、不是盟友的"），而这两类的**可行动作完全不同**：
      敌国能打，中立国**既不能走也不能打**（得先宣战/结盟）。混成一类就分不出。
    """
    o = world.owned_by(x, y)
    if o == name:
        return V.OWN_SELF
    if o is not None:
        try:
            if world.allied_between(name, o):
                return V.OWN_ALLY
            if world.war_between(name, o):
                return V.OWN_RIVAL
        except Exception:                      # noqa: BLE001
            return V.OWN_RIVAL                 # 查不动就保守当敌国（宁可不敢走）
        return V.OWN_NEUTRAL_NATION            # ★ 有主但非盟非敌：mv/atk 都不行
    for a in (by_cell.get((x, y), world.armies) if by_cell is not None else world.armies):
        if a["owner"] == "野人" and a.get("hp", 0) > 0 and (a["x"], a["y"]) == (x, y):
            return V.OWN_BARBARIAN
    return V.OWN_UNOWNED                       # 无主、无驻军


# ============================================================ 观测框（★与地图大小无关）
def frame_of(sb, me: str, mask=None) -> tuple[int, int, int, int]:
    """我方视角的**观测框** `(x0, y0, H, W)` = **视野的外接矩形**。

    用户 2026-09-24：「必须是**地图大小无关**的设计，**和以前一样**」。

    ⇒ **没有固定半径**：框跟着视野走（这也是为什么大地图上模型只看自己周围那一圈）。
    极端情况（一支军都没有、视野空）⇒ 退回核心周围 1 格，保证框非空。
    """
    mask = _vision(sb.world, me) if mask is None else mask
    if not mask:
        hx, hy = _home_cell(sb, me)
        return hx, hy, 1, 1
    xs = [c[0] for c in mask]
    ys = [c[1] for c in mask]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return x0, y0, y1 - y0 + 1, x1 - x0 + 1


def encode_grid(sb, me: str, mask=None) -> np.ndarray:
    """局面 → `(GRID_CHANNELS, H, W)`，**H/W 逐帧可变**（= 视野外接框，见 `frame_of`）。"""
    w = sb.world
    mask = _vision(w, me) if mask is None else mask
    x0, y0, h, ww = frame_of(sb, me, mask)
    foe = _other(me)
    g = np.zeros((V.GRID_CHANNELS, h, ww), dtype=np.float32)
    # ★★ **先建"格 → 军队"索引**（一次 O(军) 遍历），别在网格循环里扫全表：
    #   16×16 上有 256 个野人（每格一个）⇒ 一格扫 256 次（实测 2 局 600s 跑不完）。
    by_cell: dict = {}
    for a in w.armies:
        if a.get("hp", 0) > 0:
            by_cell.setdefault((a["x"], a["y"]), []).append(a)
    for i in range(h):
        for j in range(ww):
            x, y = x0 + j, y0 + i
            if not (0 <= x < sb.size and 0 <= y < sb.size):
                continue                                  # 框超出地图 ⇒ 保持全 0
            t = w.tiles.get((x, y))
            visible = (x, y) in mask
            # ★★ 地形**扁平化为两个军事属性**：只值"好不好守"与"走得快不快"。
            owner_here = w.owned_by(x, y)
            g[V.GRID_DEFENSE, i, j] = _defense_of(w, x, y, owner_here) / 100.0
            g[V.GRID_MOVE, i, j] = _move_cost_of(w, x, y) / 2.0
            g[V.GRID_OWNER0 + _owner_class(w, me, x, y, by_cell), i, j] = 1.0
            g[V.GRID_VISIBLE, i, j] = 1.0 if visible else 0.0
            if not visible:
                continue                     # ★ 看不清的格：军队与厅一概不写
            mine = foehp = 0.0
            for a in by_cell.get((x, y), ()):        # ★ 查索引，不扫全表
                if a["owner"] == me:
                    mine += a["hp"]
                elif a["owner"] == foe:
                    foehp += a["hp"]
            g[V.GRID_MY_HP, i, j] = min(1.0, mine / 100.0)
            g[V.GRID_FOE_HP, i, j] = min(1.0, foehp / 100.0)
            if t is not None and t["buildings"].get("市政厅", 0) > 0:
                # ★ 三档：我的 / **盟友的** / 对手的 —— 盟友的厅原来被漏掉了
                if t["owner"] == me:
                    g[V.GRID_HALL_MINE, i, j] = 1.0
                elif t["owner"] == foe:
                    g[V.GRID_HALL_RIVAL, i, j] = 1.0
                elif _owner_class(w, me, x, y, by_cell) == V.OWN_ALLY:
                    g[V.GRID_HALL_ALLY, i, j] = 1.0
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


# ============================================================ ★ 窗口
def encode_window(sb, me: str, mask=None) -> tuple[dict, dict, list[dict]]:
    """→ `(win, win_mask, armies)`：窗口 token 组 + 掩码 + 军队清单（候选要按行号引用）。

    `win["g"]`（1 条，恒亮）= `encode_glob`(14) ⊕ `features.glob_rule_vector()`(11)
      ★ 规则表常量要进**价值头**那条路（价值走窗口池化）—— 引擎改了数值，
      "同一局面值多少"本来就该跟着变。
    `win["a"]`（n 条）= 军队 token：`vocab` 的 10 列 ⊕ `features.unit_vector(kind)` 4 列
      ★ 那 4 列就是用户要的「**各个单位的血量和各个单位的战斗力**」，且是**现算**的。
    """
    w = sb.world
    foe = _other(me)
    mask = _vision(w, me) if mask is None else mask
    ps = float(sb.size)                       # ★ 归一尺度 = 地图边长（参数化，不写死）
    hx, hy = _home_cell(sb, me)

    # 顺序：我方在前、按 id 升序（**稳定** —— 候选的 `army_idx` 按下标引用它）
    armies = []
    for a in sorted(w.armies, key=lambda a: (a["owner"] != me, a["id"])):
        if a.get("hp", 0) <= 0 or a["owner"] not in (me, foe):
            continue
        if a["owner"] == foe and (a["x"], a["y"]) not in mask:
            continue                          # ★ 看不见的敌军不进 token
        armies.append(a)

    rows = []
    for a in armies:
        row = [0.0] * (V.A_WIDTH_RAW + F.F_U)
        row[V.A_OWNER0 + (0 if a["owner"] == me else 1)] = 1.0
        kind = a.get("type", "步")
        if kind in V.UNIT:
            row[V.A_UNIT0 + V.UNIT.index(kind)] = 1.0
        # ★ 相对家的偏移 ÷ **地图边长**（不是绝对坐标）
        row[V.A_X] = (a["x"] - hx) / ps
        row[V.A_Y] = (a["y"] - hy) / ps
        row[V.A_HP] = a.get("hp", 0) / 100.0
        row[V.A_MOVED] = 1.0 if a.get("moved_turn") == w.turn else 0.0
        row[V.A_ENGAGED] = 1.0 if a.get("engaged") else 0.0
        row[V.A_WIDTH_RAW:] = F.unit_vector(kind)     # ★ 兵种数值（现算）
        rows.append(row)

    g_row = np.concatenate([encode_glob(sb, me), F.glob_rule_vector()])
    win = {"g": g_row[None, :].astype(np.float32),
           "a": np.array(rows, dtype=np.float32) if rows
                else np.zeros((0, V.A_WIDTH_RAW + F.F_U), np.float32)}
    win_mask = {"g": np.ones(1, bool),
                "a": np.ones(len(rows), bool)}
    return win, win_mask, armies


# ============================================================ ★ 下标形态候选
def candidate_batch(sb, me: str, acts=None, mask=None, by_cell=None,
                    armies=None) -> dict:
    """候选动作 → **下标形态**（`model.PolicyNet` 的那几个字段）。

    ★ 这里**不再平铺成一行标量**（旧版 12 列）。理由（§11）：平铺标量下
      "候选之间互相比较"只能靠模型自己从数字里悟；换成下标形态之后，
      落点/执行军/类型各走各的嵌入，`cross2` 才有东西可比。

    字段：
      · `type_idx [K]`      0=hold · 1=move · 2=attack（`vocab.KIND`）
      · `pos_dx/dy [K]`     ★ 目标格**相对我家核心**的偏移**已 ÷ 地图边长**（float）
        `has_pos [K]`       有没有落点（沙盒恒 1 —— 每个动作都挂在某格上；留着是给
                            将来"无落点动作"（买/卖/end）用的结构位）
      · `tile_xy [K,2]`     目标格在**观测框内**的坐标（网格 gather 用；`-1` = 框外）
      · `army_idx [K]`      执行军在上面 `armies` 里的**行号**；越界 = 无
      · `cand_content [K,F_CAND]`  规则表：执行军兵种数值 ⊕ 目标格地形数值
      · `cand_marks [K,CAND_MARKS]` 归属/厅/可见/距离（见 `vocab.CAND_*`）
    """
    w = sb.world
    foe = _other(me) if me else None
    hx, hy = _home_cell(sb, me)
    ps = float(sb.size)
    mask = ((_vision(w, me) if mask is None else mask) if me else set())
    acts = sb.legal() if acts is None else acts
    if by_cell is None:                     # ★ 格→军索引（`_owner_class` 查它，不扫全表）
        by_cell = {}
        for _a in w.armies:
            if _a.get("hp", 0) > 0:
                by_cell.setdefault((_a["x"], _a["y"]), []).append(_a)
    if armies is None:
        armies = encode_window(sb, me, mask)[2]
    row_of = {a["id"]: i for i, a in enumerate(armies)}
    by_id = {a["id"]: a for a in armies}

    x0, y0, fh, fw = frame_of(sb, me, mask)      # ★ 框算**一次**（原来每候选建一次视野）
    n = len(acts)
    out = {
        "type_idx": np.zeros(n, np.int64),
        "pos_dx": np.zeros(n, np.float32),
        "pos_dy": np.zeros(n, np.float32),
        "has_pos": np.zeros(n, np.float32),
        "tile_xy": np.full((n, 2), -1, np.int64),
        "army_idx": np.full(n, len(armies), np.int64),
        "cand_content": np.zeros((n, F.F_CAND), np.float32),
        "cand_marks": np.zeros((n, V.CAND_MARKS), np.float32),
    }
    for i, (aid, kind, x, y) in enumerate(acts):
        out["type_idx"][i] = V.KIND_INDEX.get(kind, 0)
        a = by_id.get(aid)
        if a is not None:
            out["army_idx"][i] = row_of.get(aid, len(armies))
        if x is None or y is None:
            continue                                   # 理论上沙盒没有"无落点"的动作
        out["has_pos"][i] = 1.0
        out["pos_dx"][i] = (x - hx) / ps               # ★ 在这里归一，模型不再除
        out["pos_dy"][i] = (y - hy) / ps
        fi, fj = int(y) - y0, int(x) - x0
        if 0 <= fi < fh and 0 <= fj < fw:
            out["tile_xy"][i] = (fi, fj)
        cls = _owner_class(w, me, x, y, by_cell)
        m = out["cand_marks"][i]
        # ★ 归属 one-hot **六类**（与网格同一套 `vocab.OWN_*`）
        m[V.CAND_OWN0 + cls] = 1.0
        # ★ 市政厅归属 one-hot（同一套六类）—— 单列一组，让"这格的厅是谁的"直接可读：
        #   打谁能亡国、谁亡了我就危险，是国祚层的核心判断，不指望它从两个 one-hot 里凑。
        t = w.tiles.get((x, y))
        if t is not None and t["buildings"].get("市政厅", 0) > 0:
            hall_owner = t["owner"]
            if hall_owner == me:
                m[V.CAND_HALL0 + V.OWN_SELF] = 1.0
            elif hall_owner is not None:
                hc = _owner_class(w, me, x, y, by_cell)   # 复用同一判定（盟友/敌国/中立国）
                m[V.CAND_HALL0 + hc] = 1.0
        m[V.CAND_VISIBLE] = 1.0 if (x, y) in mask else 0.0
        m[V.CAND_DIST] = max(abs(x - hx), abs(y - hy)) / ps
        out["cand_content"][i] = F.cand_content(
            a.get("type", "步") if a else "步", w.tile_terrain(x, y))
    return out


# ============================================================ 一次拿全
def obs_of(sb, me: str, acts=None) -> dict:
    """沙盒局面 → 网络要的一整套（**`legal()` 与视野各只算一次**）。"""
    acts = sb.legal() if acts is None else acts
    mask = vision_of(sb, me)
    win, win_mask, armies = encode_window(sb, me, mask)
    return {
        "grid": encode_grid(sb, me, mask),
        "win": win,
        "win_mask": win_mask,
        "cand": candidate_batch(sb, me, acts, mask, armies=armies),
        "mask": np.ones(len(acts), bool),
        "n_armies": len(armies),                 # 供 collate 记录（不做张量）
    }


# ============================================================ 小工具
def vision_of(sb, me: str):
    """当前视野 —— `obs_of` 里**算一次**、再传给下面各编码函数用。

    ★ 为什么单拎出来：`vision_mask` 是 O(地块数) 的，而各编码函数原先各自重建一遍
      （`candidate_xy` 甚至**每候选**一遍）—— 实测一局 vision_mask 被调上万次。
    """
    return _vision(sb.world, me)


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