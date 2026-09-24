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

from . import combat_probs as CB
from . import features as F
from . import scoring as S
from . import vocab as V
from .sandbox import PLAYERS

# ★★ 军队 token 的行内布局（**唯一出处** —— 别在别处手算这两个数）：
#     [0, A_WIDTH_RAW)      局面列（归属/兵种 one-hot/位置/hp/moved/engaged）
#     [A_WIDTH_RAW, ARMY_TAIL0)   规则表数值（`features.unit_vector`，F_U 列）
#     [ARMY_TAIL0, ARMY_WIDTH)    ★ 战斗明细尾段（`vocab.A_EXTRA` 列）
#   ⚠ `vocab.A_CB_*` 是**尾段内**的下标，**不是**整行的下标 —— 直接拿来索引整行
#     会**读错列且不报错**（我写测试时就踩了：读到的其实是 `A_OWNER0` 的 one-hot）。
ARMY_TAIL0 = V.A_WIDTH_RAW + F.F_U
ARMY_WIDTH = ARMY_TAIL0 + V.A_EXTRA


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


# ============================================================ ★★ 战斗明细的取数口
def combat_of(sb, me: str, mask, armies=None) -> CB.FrameOdds:
    """★ **一帧算一次**的战斗明细（`combat_probs.frame_odds` 的薄包装）。

    ★ 只在 `obs_of` 里调**一次**，返回值往下传（网格与军队 token 共用那一份）——
      别在每个编码函数里各算一遍：那是**每帧重算**（正确），但是白算几遍（`assess`
      的 DP 不便宜，`reachable_reinforcements` 还带寻路）。

    ★ `mask` 是**引擎视野**（`vision_mask`），必传（见 `frame_odds` 的注释：
      它是增援过滤的**唯一**依据，给默认值就等于留一条静默偷看的路）。
    """
    return CB.frame_odds(sb.world, me, mask, with_reinf=True, retreat=True)


def _mine_engaged_here(by_cell, me: str, x: int, y: int) -> bool:
    """这一格上有没有**我的**军（= "仗打在我身上"）。

    ★ 这是"**接触即看见**"判据（`scoring.CB_CONTACT_VISION`）的**唯一**依据 ——
      它**不扩视野本身**，只决定"这一格能不能写概率"。见 `scoring.py` 那段实测。
    """
    return any(a["owner"] == me for a in by_cell.get((x, y), ()))


def _combat_tail(frame: CB.FrameOdds | None, cell, side: str,
                 retreat: float | None) -> list[float]:
    """该格那场仗、从 `side` 那一方看 ⇒ `A_EXTRA` 列（见 `vocab` 的 5' 段）。

    · `frame` 为 `None` 或该格**没在打** ⇒ 只有撤退那一列是 `1.0`（没在打，撤了当然活）。
    · `retreat is None` ⇒ **读不到这一格的战况**（口径不准许）⇒ **整段全 0**，
      连撤退那列也 0（`A_CB_ACTIVE=0` 就是"这一段没有内容"的标记）。

    ★ **逐列按 `vocab.A_*` 下标填**，不按顺序 append —— 顺序错了不报错，只会静默错位。
    """
    row = [0.0] * V.A_EXTRA
    if retreat is None:
        return row
    row[V.A_RETREAT] = retreat
    o = None if frame is None else frame.cells.get(cell)
    if o is None:
        return row
    row[V.A_CB_ACTIVE] = 1.0
    row[V.A_CB_PWIN] = o.p_win.get(side, 0.0)
    row[V.A_CB_PLOSE] = o.p_lose.get(side, 0.0)
    row[V.A_CB_PDRAW] = o.p_draw
    row[V.A_CB_PHOLD] = o.p_hold.get(side, 0.0)
    for k, v in enumerate(o.round_bins()):          # ★ 边界读先验表（`scoring`）
        row[V.A_CB_R0 + k] = v
    r = frame.reinf.get(cell) or o                  # 没有增援 ⇒ 与现状同一个对象
    row[V.A_CB_PWIN_REINF] = r.p_win.get(side, 0.0)
    #   ★ **不夹到 1**（原来写 `min(1.0, …)`）：夹了就把"丢一支军"和"丢三支军"
    #     抹成同一个数 —— 那正是 `REWARD_TANH_SCALE` 那条坑的翻版（调了等于没调）。
    #     尺度含义见 `scoring.PROB_*_SCALE`：1.0 = 一支满血兵 / 打十轮。
    row[V.A_CB_EROUNDS] = o.e_rounds / S.PROB_ROUND_SCALE
    row[V.A_CB_ELOSS] = o.e_loss.get(side, 0.0) / S.PROB_LOSS_SCALE
    return row


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


def encode_grid(sb, me: str, mask=None, halls_known: bool | None = None,
                frame: CB.FrameOdds | None = None) -> np.ndarray:
    """局面 → `(GRID_CHANNELS, H, W)`，**H/W 逐帧可变**（= 视野外接框，见 `frame_of`）。

    `frame` = `combat_of` 算好的那一帧战斗明细（**同一帧只算一次**，`obs_of` 传下来）。
    ★ 网格里的战斗通道**只在"这一格放得进框"时才有位置** —— 交战格大多在框外
      （实测 16×16 只有 0~7% 在视野里）⇒ **真正扛事的是军队 token 那一段**
      （`_combat_tail`，与视野无关）。网格这份是"**看得见的那几场仗**"，
      给候选直接 gather 用（候选要去的那格正好是战场时，一眼读到）。
    """
    w = sb.world
    mask = _vision(w, me) if mask is None else mask
    # ★ "已派间谍"模式：他国的厅**位置**已知 ⇒ 厅通道不看视野。
    #   ⚠ 军队的可见性**永远**走 `mask`（下面那段没动）—— "知道厅在哪" ≠ "看得见守军"。
    halls_known = sb.halls_known if halls_known is None else halls_known
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
            # ★★ 战斗明细 —— **每帧重算**（`frame` 由 `obs_of` 现算传下来，不是缓存）。
            #    取数条件：这一格在打，**且**（看得见 **或** 仗打在我身上）。
            #    后者 = "**接触即看见**"（`scoring.CB_CONTACT_VISION` + 实测理由）。
            #    ⚠ 看不见又没我的军 ⇒ **一格都不写**（概率是从 `world.armies` 真值算的，
            #      写下去就是把迷雾里的兵力透给模型 —— 本线最忌的偷看）。
            if frame is not None:
                oc = frame.cells.get((x, y))
                if oc is not None and (visible or (
                        S.CB_CONTACT_VISION and _mine_engaged_here(by_cell, me, x, y))):
                    g[V.GRID_CB_ACTIVE, i, j] = 1.0
                    g[V.GRID_CB_PWIN, i, j] = oc.p_win.get(me, 0.0)
                    g[V.GRID_CB_PLOSE, i, j] = oc.p_lose.get(me, 0.0)
                    g[V.GRID_CB_PDRAW, i, j] = oc.p_draw
                    g[V.GRID_CB_PHOLD, i, j] = oc.p_hold.get(me, 0.0)
                    for k, v in enumerate(oc.round_bins()):
                        g[V.GRID_CB_R0 + k, i, j] = v
                    ro = frame.reinf.get((x, y)) or oc
                    g[V.GRID_CB_PWIN_REINF, i, j] = ro.p_win.get(me, 0.0)
                    g[V.GRID_CB_EROUNDS, i, j] = oc.e_rounds / S.PROB_ROUND_SCALE
                    g[V.GRID_CB_ELOSS, i, j] = (
                        oc.e_loss.get(me, 0.0) / S.PROB_LOSS_SCALE)   # ★ 不夹，见 `_combat_tail`
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
            if (t is not None and t["buildings"].get("市政厅", 0) > 0
                    and (visible or halls_known)):
                # ★ 三档：我的 / **盟友的** / 对手的 —— 盟友的厅原来被漏掉了
                if t["owner"] == me:
                    g[V.GRID_HALL_MINE, i, j] = 1.0
                elif t["owner"] == foe:
                    g[V.GRID_HALL_RIVAL, i, j] = 1.0
                elif _owner_class(w, me, x, y, by_cell) == V.OWN_ALLY:
                    g[V.GRID_HALL_ALLY, i, j] = 1.0
    return g


# ============================================================ 全局标量
def encode_glob(sb, me: str, mask=None) -> np.ndarray:
    """局面 → `(GLOB_SIZE,)` 标量（归一到 0~1）。**不含任何绝对坐标。**"""
    w = sb.world
    foe = _other(me)
    mine, his = _armies(w, me), _armies(w, foe)
    cap_m, cap_f = sb.cap_of(me), sb.cap_of(foe)
    n2 = float(sb.size * sb.size)
    hx, hy = _home_cell(sb, me)
    ps = float(sb.size)
    mask = _vision(w, me) if mask is None else mask
    # ★ 已知的厅（三个类各一条"最近的那座"，相对**我家核心**）—— 见 `vocab.GLOB` 的注释。
    #   我/盟友的厅全知；对手的厅按 `sb.halls_known`（间谍模式）或视野。
    hall_vals = {}
    for tag, who, known in (("my", (me,), False), ("ally", _allies_of(sb, me), True),
                            ("foe", (foe,), False)):
        cells = [c for n in who for c in
                 _hall_cells_of(w, n, mask, sb.halls_known or known)]
        if cells:
            cx, cy = min(cells, key=lambda c: max(abs(c[0] - hx), abs(c[1] - hy)))
            hall_vals[f"{tag}_hall_dx"] = (cx - hx) / ps
            hall_vals[f"{tag}_hall_dy"] = (cy - hy) / ps
            hall_vals[f"{tag}_hall_d"] = max(abs(cx - hx), abs(cy - hy)) / ps
        else:
            hall_vals[f"{tag}_hall_dx"] = hall_vals[f"{tag}_hall_dy"] = 0.0
            hall_vals[f"{tag}_hall_d"] = 0.0
        hall_vals[f"{tag}_halls"] = min(1.0, len(cells) / 4.0)
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
        **hall_vals,
    }
    return np.array([vals[k] for k in V.GLOB], dtype=np.float32)


# ============================================================ ★ 窗口
def window_armies(sb, me: str, mask=None) -> list[dict]:
    """窗口里**该有哪些军**（顺序 = 军队 token 的行序）。

    ★ 单拎出来是为了让调用方能**先拿到军单**、再算战斗明细（`combat_of` 要按军取
      撤退概率）—— 否则 `obs_of` 得把选军那段抄两遍（抄两遍就会**慢慢不一致**）。
    """
    w = sb.world
    foe = _other(me)
    mask = _vision(w, me) if mask is None else mask
    out = []
    # 顺序：我方在前、按 id 升序（**稳定** —— 候选的 `army_idx` 按下标引用它）
    for a in sorted(w.armies, key=lambda a: (a["owner"] != me, a["id"])):
        if a.get("hp", 0) <= 0 or a["owner"] not in (me, foe):
            continue
        if a["owner"] == foe and (a["x"], a["y"]) not in mask:
            continue                          # ★ 看不见的敌军不进 token
        out.append(a)
    return out


def encode_window(sb, me: str, mask=None, *, frame: CB.FrameOdds | None = None,
                  armies: list[dict] | None = None) -> tuple[dict, dict, list[dict]]:
    """→ `(win, win_mask, armies)`：窗口 token 组 + 掩码 + 军队清单（候选要按行号引用）。

    `win["g"]`（1 条，恒亮）= `encode_glob`(14) ⊕ `features.glob_rule_vector()`(11)
      ★ 规则表常量要进**价值头**那条路（价值走窗口池化）—— 引擎改了数值，
      "同一局面值多少"本来就该跟着变。
    `win["a"]`（n 条）= 军队 token：`vocab` 的 10 列 ⊕ `features.unit_vector(kind)` 4 列
      ⊕ **`vocab.A_EXTRA` 13 列**（★ 这支军身处的那场仗，从它自己那一方看 ——
      见 `vocab` 的 5' 段；**与视野无关**，大地图上也是活的）
      ★ 中间那 4 列就是用户要的「**各个单位的血量和各个单位的战斗力**」，且是**现算**的。

    `frame` = `combat_of` 算好的那一帧（**同一帧只算一次**）；不传 ⇒ 自己算一次
    （单测里方便，正式路径由 `obs_of` 传下来）。
    """
    w = sb.world
    mask = _vision(w, me) if mask is None else mask
    ps = float(sb.size)                       # ★ 归一尺度 = 地图边长（参数化，不写死）
    hx, hy = _home_cell(sb, me)
    if armies is None:
        armies = window_armies(sb, me, mask)
    if frame is None:
        frame = combat_of(sb, me, mask, armies)
    wfull = ARMY_WIDTH

    rows = []
    for a in armies:
        row = [0.0] * wfull
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
        row[V.A_WIDTH_RAW:ARMY_TAIL0] = F.unit_vector(kind)        # ★ 兵种数值（现算）
        # ★★ 战斗明细：**按这支军自己那一方**填（甲看到的"甲的概率"就是乙看到的
        #    "乙的概率" ⇒ 两国共用一套编码，自对弈不失衡）。
        #    ★ 取数条件与网格**同一条**：看得见这一格，**或**"仗打在我身上"
        #      （接触；`scoring.CB_CONTACT_VISION`）。
        #      · 我的军：`a["owner"] == me` ⇒ 走接触档（我军恒在 token 里，与视野无关）
        #      · 敌的军：进 token 的前提就是 `(x,y) in mask`（`window_armies`）
        #        ⇒ 它天然是"看得见"那一档
        #      ⇒ 关掉 `CB_CONTACT_VISION` 时**两边一起关**，这个开关才是真的口径开关。
        cell = (a["x"], a["y"])
        allowed = (cell in mask) or (S.CB_CONTACT_VISION and a["owner"] == me)
        row[ARMY_TAIL0:] = _combat_tail(
            frame, cell, a["owner"],
            frame.retreat.get(id(a), 1.0) if allowed else None)
        rows.append(row)

    g_row = np.concatenate([encode_glob(sb, me, mask), F.glob_rule_vector()])
    arr = np.array(rows, dtype=np.float32) if rows else np.zeros((0, wfull), np.float32)
    # ★★ 行宽断言：**list 的切片赋值会静默改变长度**（`row[18:] = [14 个数]`
    #    在 28 长的 list 上会把它撑到 32，**不报错**）⇒ 少了这条，尾段整体错位
    #    也只是"模型少读到几列"，训练照跑。宁可在这里炸。
    assert arr.shape[1] == wfull, f"军队 token 行宽 {arr.shape[1]} ≠ 约定的 {wfull}"
    win = {"g": g_row[None, :].astype(np.float32), "a": arr}
    win_mask = {"g": np.ones(1, bool),
                "a": np.ones(len(rows), bool)}
    return win, win_mask, armies


# ============================================================ ★ 下标形态候选
def candidate_batch(sb, me: str, acts=None, mask=None, by_cell=None,
                    armies=None, halls_known: bool | None = None) -> dict:
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
    halls_known = sb.halls_known if halls_known is None else halls_known
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
        # ★ 陷阱候选也要走 `halls_known`：间谍模式下"我知道那格是敌厅"，
        #   哪怕它此刻不在视野里 —— 这正是"明知"要买到的东西。
        if t is not None and t["buildings"].get("市政厅", 0) > 0 \
                and ((x, y) in mask or halls_known):
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
    """沙盒局面 → 网络要的一整套（**`legal()` / 视野 / 战斗明细各只算一次**）。

    ★ 顺序是定死的：**视野 → 军单 → 战斗明细 → （窗口、网格）**。
      战斗明细要在窗口之前算（军队 token 要用它），而它自己要用军单（撤退概率按军取）
      ⇒ 先 `window_armies` 拿军单，`combat_of` 算一次，再分别喂给窗口与网格。
      ★★ `combat_of` **一帧只调一次**：它内部是精确 DP + 寻路，
        在两个编码函数里各算一遍 = 白烧一遍（结果逐位相同，纯粹浪费）。
    """
    acts = sb.legal() if acts is None else acts
    mask = vision_of(sb, me)
    armies = window_armies(sb, me, mask)
    frame = combat_of(sb, me, mask, armies)
    win, win_mask, _ = encode_window(sb, me, mask, frame=frame, armies=armies)
    return {
        "grid": encode_grid(sb, me, mask, frame=frame),
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


def _hall_cells_of(world, name: str | None, mask, halls_known: bool) -> list:
    """该国**已落成**的厅格（`halls_known` ⇒ 不看掩码；见 `vocab.GLOB` 的注释）。"""
    if not name:
        return []
    out = []
    for cell, t in world.tiles.items():
        if t["owner"] != name or t["buildings"].get("市政厅", 0) <= 0:
            continue
        if halls_known or cell in mask:
            out.append(cell)
    return out


def _allies_of(sb, me: str | None) -> tuple:
    if not me:
        return ()
    try:
        return tuple(n for n in sb.world.nations
                     if n != me and sb.world.allied_between(me, n))
    except Exception:                              # noqa: BLE001
        return ()


def _armies(world, name: str) -> list[dict]:
    return [a for a in world.armies if a["owner"] == name and a.get("hp", 0) > 0]


def _home_cell(sb, name: str | None) -> tuple[int, int]:
    c = sb.core_of(name) if name else None
    return c if c else (0, 0)


def _hp_frac(armies: list[dict], cap: int) -> float:
    return min(1.0, sum(a.get("hp", 0) for a in armies) / max(1.0, 100.0 * max(1, cap)))