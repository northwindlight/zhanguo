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
from . import hall_memory as HM
from . import intel as IN
from . import features as F
from . import scoring as S
from . import vocab as V
# ★ 编码层**不依赖沙盒**：`enemies_of` 转发 `evaluate.rival_nations`（唯一实现），
#   其余一切都从 `world` 上读 —— 少一条 import 就少一条循环依赖的路。

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
    return _owner_class_of(world, name, world.owned_by(x, y), x, y, by_cell)


def _owner_class_of(world, name: str, o: str | None, x: int, y: int,
                    by_cell: dict | None = None) -> int:
    """★ **显式给主人**的那一版 —— `o` = "我**认知里**的主人"（见 `hall_memory.owner_seen`）。

    ★ 拆这一版是为了**厅**：一座**记得的、看不见的**厅，认知里的主人是"最后看见时的"，
      而不是当前真值（用户 2026-09-25：「**厅的归属会变的**」）。
      军队/候选/网格归属仍然走 `_owner_class`（那必须用当前真值 —— 它们要么可见、
      要么本来就不该有归属，见 `encode_grid` 里归属只在可见时写的注释）。
    """
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


def encode_grid(sb, me: str, mask=None, known=None,
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
    # ★ 已知的厅 = **视野 ∪ 永久记忆**（`known`）。间谍模式只是**记忆的初值不同**
    #   （开局就装满），所以这里**不再分模式**。
    #   ⚠ 军队的可见性**永远**走 `mask`（下面那段没动）—— "知道厅在哪" ≠ "看得见守军"。
    known = sb.known_halls(me, mask) if known is None else known
    x0, y0, h, ww = frame_of(sb, me, mask)
    g = np.zeros((V.GRID_CHANNELS, h, ww), dtype=np.float32)
    # ★ 顺手把本帧看得见的记进番号账本（**幂等**：同一帧记两次无害），
    #   再取"按格"的读法。★ 放在这里是为了让 `encode_grid` **自给自足**
    #   —— 它可能被单独调用（测试、探针），那时不能指望 `encode_window` 先跑过。
    sb.war_mem.observe(w, me, mask, sb.turn)
    mem_ages = sb.war_mem.cell_ages(me, sb.turn)
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
            # ★★ **归属只在"看得见"时写** —— 用户 2026-09-25 问「视野内的地块归属有记忆吗」，
            #   顺着查出来的：原来这里是**无条件**写的，而网格是**视野的外接矩形**
            #   ⇒ 框内总有一圈/若干格**看不见**（视野不是矩形），那些格的归属
            #   却是**真值**。实测每帧泄漏 **10%~16% 的框内格**（16×16 上 4 格是别国地）。
            #   ★ 这正是用户让我整批删掉 `foe_tiles/foe_armies/...` 的**同一类**：
            #     「**有没有超越真玩家的内容**」—— 不侦察就知道那块地是谁的。
            #   ★ 只写可见会不会丢掉**自家**疆域？不会：实测 2400 格自家地里
            #     **0 格**不在视野内（`vision_mask` 本来就含自家/盟方地块及其八邻）。
            #   ★ 与**厅**那段不冲突：厅在 `if not visible: continue` **之上**、
            #     且走「视野 ∪ 永久记忆」—— 那是**独立的一次情报**（厅不能动/拆不掉）。
            #     归属**会**易主 ⇒ 不能靠记忆放宽，**取保守那侧**（只信当帧视野）。
            if visible:
                g[V.GRID_OWNER0 + _owner_class(w, me, x, y, by_cell), i, j] = 1.0
            g[V.GRID_VISIBLE, i, j] = 1.0 if visible else 0.0
            # ★★ **记忆**两列（与 `"k"` 组同源、同一次观测，两种读法）——
            #   "这格我上次看见有敌军（多旧）"。★ 它让候选能**直接 gather** 到
            #   "我要去的那一格，情报有多旧"，而不必先看 token。
            _age = mem_ages.get((x, y))
            if _age is not None:
                g[V.GRID_MEM_AGE, i, j] = max(0.0, 1.0 - _age / V.AGE_SCALE)
                g[V.GRID_MEM_ENEMY, i, j] = 1.0 / (1.0 + _age)
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
            # ★★ **市政厅要走"已知"（视野 ∪ 永久记忆），因此必须在 `if not visible` 之前**
            #   —— 用户 2026-09-24：「**发现厅了就应该永久标记，因为厅是拆不掉也不能
            #   移动的**」（含**盟友的厅**：「盟友发现厅应该也纳入标记」）。
            #
            #   ⚠ 这里原来把厅塞在 `if not visible: continue` **下面**，而那条判据当时写的是
            #     `visible or (x, y) in known` —— `visible` 恒真 ⇒ **`in known` 那半截是
            #     死代码**。后果是**真闪断**（实测过）：网格 = **视野外接框**（矩形），
            #     而视野不是矩形 ⇒ 框内有一圈**看不见**的格；一座**记得的**敌厅若正落在
            #     那一圈里，`GRID_HALL_RIVAL` 当帧塌 0，而同帧 `GLOB foe_hall_d` 还在
            #     ⇒ 同一件"永久事实"在观测的两半里**读数不一致**（正是要禁的那类闪断）。
            #
            #   ★ 只抬**厅**这一段：军队/hp 仍然死守 `mask`（"知道厅在哪" ≠ "看得见守军"，
            #     两件情报 —— 见 `hall_memory.py` 的文件头）。
            #   ★ 归属仍以**当前** `t["owner"]` 为准（记忆只放宽**可见性**，取保守那侧）。
            if (t is not None and t["buildings"].get("市政厅", 0) > 0
                    and (visible or (x, y) in known)):
                # ★★ **归属取"我认知里的主人"**（用户 2026-09-25：「厅的归属会变的」）：
                #   看得见 ⇒ 当前真值；看不见 ⇒ 记忆里"最后看见时的"。
                #   ⚠ 原来这里用的是 `t["owner"]`（当前真值）⇒ 一座**看不见的**记得的厅
                #     若在我不知情时易主，这几列会**静默跟着变** —— 模型白得一条情报。
                #     （★ 实测：30 个 seed 没抓到这种格 —— 敌厅一般落在我视野框**外**，
                #      所以这不是"正在流血"，而是"差一个 if"；但口径必须对。）
                _ho = HM.owner_seen(w, (x, y), mask, known)
                if _ho == me:
                    g[V.GRID_HALL_MINE, i, j] = 1.0
                else:
                    # ★ 走**六类**判定（不再是 `== foe`）⇒ 多玩家下"打谁能亡国"不再漏人；
                    #   中立国的厅既不进"我的"也不进"对手的"（它不可攻），与候选侧同口径。
                    hc = _owner_class_of(w, me, _ho, x, y, by_cell)
                    if hc == V.OWN_RIVAL:
                        g[V.GRID_HALL_RIVAL, i, j] = 1.0
                    elif hc == V.OWN_ALLY:
                        g[V.GRID_HALL_ALLY, i, j] = 1.0
            if not visible:
                continue                     # ★ 看不清的格：军队与 hp 一概不写
            mine = foehp = 0.0
            for a in by_cell.get((x, y), ()):        # ★ 查索引，不扫全表
                if a["owner"] == me:
                    mine += a["hp"]
                elif a["owner"] in w.nations:
                    # ★ 别人的军（原来写死 `== foe`）：多玩家下"敌人"是集合
                    #   ⇒ 只认一个会把另一个对手的兵力**静默漏掉**。
                    #   ⚠ 野人（`野人 ∉ w.nations`）**维持现状不计入**（与 token 同口径）。
                    foehp += a["hp"]
            g[V.GRID_MY_HP, i, j] = min(1.0, mine / 100.0)
            g[V.GRID_FOE_HP, i, j] = min(1.0, foehp / 100.0)
    return g


# ============================================================ 全局标量
def encode_glob(sb, me: str, mask=None, known=None) -> np.ndarray:
    """局面 → `(GLOB_SIZE,)` 标量（归一到 0~1）。**不含任何绝对坐标。**"""
    w = sb.world
    # ★★ **对手是一个集合**（用户 2026-09-25：多玩家 3 人起步）——
    #   `foe_*` 那几个标量一律是**所有对手之和**，"还有没有对手活着"用 `any`。
    #   ⚠ 原来的 `_other(me)` 只取"另一个" ⇒ 三国局里**第二个对手整个不进观测**
    #     （而那**不报错**，只是模型少看到一个敌人）。
    # ★ **mask 必须先归一**：下面 `enemies_of`/`_hall_cells_of`/`known_halls` 都要用它
    #   （我 2026-09-25 一度把用 mask 的代码写在归一**之前** ⇒ `mask=None` 调用会
    #    `TypeError: argument of type NoneType is not iterable`）。
    mask = _vision(w, me) if mask is None else mask
    foes = enemies_of(w, me)
    mine = _armies(w, me)
    # ★★ 那五个"对手标量"**已整批删除**（用户 2026-09-25：「**全都不要了**…这个规则是
    #   **训练的规则**，实际如何补员由 llm 决定…**根本不是该计入的规则**」）——
    #   它们既**超越真玩家**（不过 mask），又在数**沙盒的补员公式**。见 `vocab.GLOB`。
    n2 = float(sb.size * sb.size)
    hx, hy = _home_cell(sb, me)
    ps = float(sb.size)
    # ★ 已知的厅（三个类各一条"最近的那座"，相对**我家核心**）—— 见 `vocab.GLOB` 的注释。
    #   我/盟友的厅全知；对手的厅按 `known`（**视野 ∪ 永久记忆** —— 见过就永远知道）。
    hall_vals = {}
    known = sb.known_halls(me, mask) if known is None else known
    for tag, who in (("my", (me,)), ("ally", _allies_of(sb, me)), ("foe", tuple(foes))):
        cells = [c for n in who for c in _hall_cells_of(w, n, mask, known)]
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
        "my_tiles": sb.tiles_of(me) / n2,
        "my_armies": len(mine) / 8.0,
        "my_hall": 1.0 if sb.alive(me) else 0.0,
        # ★ 语义：**还有对手活着**（多玩家下"某一个对手的国祚"已无意义）。
        #   国祚存亡是**公开事件**（引擎 `_eliminate_if_dead`）⇒ 不算偷看。
        "foe_hall": 1.0 if any(sb.alive(f) for f in foes) else 0.0,
        "my_hp_frac": _hp_frac(mine, S.HP_REF_ARMIES),
        "my_moved": sum(1 for a in mine if a.get("moved_turn") != w.turn) / 8.0,
        "last_ok": 1.0 if sb.last_ok else 0.0,
        # ★★ 我**累计**的战果（见 `vocab.GLOB` 那两段；单调、与视野无关）。
        #   ⚠ **只数"敌国"**（`victims=foes`）：打野人/打盟友不算 —— 口径与打分器一致，
        #     而账本按 (凶手, 受害者) 成对记，正是为了在这里能筛。
        "my_kills": min(1.0, sb.kills.kills_by(me, victims=foes) / S.KILLS_SCALE),

        "my_dmg": min(1.0, sb.kills.dmg_by(me, victims=foes) / S.DMG_SCALE),

        # ★★ **当前回合数**（用户 2026-09-25：「llm 玩家也知道的，现在多少回合了」）。
        #   ★ 三条口径见 `vocab.GLOB` 里 `my_turn` 那段。这里只落实两条实现细节：
        #     ① 分子加的是**本局随机的 `turn_offset`** ⇒ 死记无处落脚，
        #        而局内**逐帧做差**仍读得出「又过了一回合」（相对量完整保留）；
        #     ② ★ **不夹到 1**（与上面两列 `min(1.0, …)` 相反）—— 回合数一直涨，
        #        夹了就把后半段的增量抹平，而增量是 ① 里唯一保留下来的东西。
        #     ③ ★ 分母是 `V.TURN_SCALE`（**纯尺度**），**不是 `sb.t_max`**：
        #        `t_max` 是训练为了防僵局定的地平线，进观测就等于把删掉的
        #        `turn_frac` 从后门放回来。守卫钉这条（改 `t_max` 不许动这一列）。
        "my_turn": (sb.turn + sb.turn_offset) / V.TURN_SCALE,

        **hall_vals,
        # ★★ **情报（可写观测层）** —— 见 `rl/intel.py` 与 `vocab.GLOB` 那 12 列。
        **_intel_vals(sb, me),
    }
    return np.array([vals[k] for k in V.GLOB], dtype=np.float32)


def _intel_vals(sb, me: str) -> dict:
    """**外部告知的军情** → 六类里"别人的那三类"各一份（数量 + 陈旧度）。

    ★ 口径照抄引擎的间谍（`mp.World._econ_snapshot`）：
      「粗略军情：**只有各兵种数量 —— 位置/血量/番号不外泄**」
      ⇒ 这里**只有数量**，没有位置。（位置/番号只能来自自己看见。）
    ★ 归成**定长**（盟友/对手/中立各 4 列）：国家数不许进观测形状（本线铁律）。
      同类里有多个来源国报了 ⇒ 取**最新的那一份**；★ 同回合按**国名**定序
      ⇒ 与字典插入顺序无关 ⇒ **确定性**（同一局面观测逐位相同）。
    ★ 没收到过情报 ⇒ 全 0（"我没派人去探 / 还没回来"，读得出来）。
    """
    out = {}
    for tag in IN.INTEL_CLASSES:                     # 先把 12 列铺成 0
        for u in V.UNIT:
            out[f"intel_{tag}_{u}"] = 0.0
        out[f"intel_{tag}_age"] = 0.0
    got = sb.intel.armies_of(me, sb.turn)
    if not got:
        return out
    buckets: dict = {t: [] for t in IN.INTEL_CLASSES}
    for src, rec in got.items():
        if src == me or src not in sb.world.nations:
            continue                                 # 自己不用探；野人不是国家
        # ★ 复用同一套六类判定（`o` 给了 ⇒ 只可能落到 ALLY/RIVAL/NEUTRAL）
        cls = _owner_class_of(sb.world, me, src, 0, 0)
        tag = ("ally" if cls == V.OWN_ALLY else
               "rival" if cls == V.OWN_RIVAL else "neutral")
        buckets[tag].append((int(rec["turn"]), src, rec))
    for tag, items in buckets.items():
        if not items:
            continue
        items.sort(key=lambda t: (-t[0], t[1]))      # ★ 最新优先；同回合按国名 ⇒ 确定性
        _, _, rec = items[0]
        for u in V.UNIT:
            out[f"intel_{tag}_{u}"] = min(
                1.0, rec["kinds"].get(u, 0) / S.INTEL_ARMY_SCALE)
        out[f"intel_{tag}_age"] = min(1.0, rec["age"] / V.AGE_SCALE)
    return out


# ============================================================ ★ 窗口
def window_armies(sb, me: str, mask=None) -> list[dict]:
    """窗口里**该有哪些军**（顺序 = 军队 token 的行序）。

    ★ 单拎出来是为了让调用方能**先拿到军单**、再算战斗明细（`combat_of` 要按军取
      撤退概率）—— 否则 `obs_of` 得把选军那段抄两遍（抄两遍就会**慢慢不一致**）。
    """
    w = sb.world
    mask = _vision(w, me) if mask is None else mask
    out = []
    # 顺序：我方在前、按 id 升序（**稳定** —— 候选的 `army_idx` 按下标引用它）
    for a in sorted(w.armies, key=lambda a: (a["owner"] != me, a["id"])):
        if a.get("hp", 0) <= 0:
            continue
        if a["owner"] != me:
            # ★★ 别人的军：**看得见才进**。★ 原来写的是 `owner not in (me, foe)`
            #    —— "那一个敌人"的假设 ⇒ 三国局里**另一个对手的军静默消失**
            #    （明明在视野里，却进不了 token，而**不报错**）。
            #    ⇒ 现在收**任何国家**的军（盟友/敌国/中立国），归属由六类 one-hot 区分。
            if a["owner"] not in w.nations:
                continue                      # 野人：**不进 token**（维持现状，见下）
            if (a["x"], a["y"]) not in mask:
                continue
        out.append(a)
    return out


def encode_window(sb, me: str, mask=None, *, frame: CB.FrameOdds | None = None,
                  armies: list[dict] | None = None,
                  known=None) -> tuple[dict, dict, list[dict]]:
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
    if known is None:
        known = sb.known_halls(me, mask)
    if frame is None:
        frame = combat_of(sb, me, mask, armies)
    wfull = ARMY_WIDTH

    rows = []
    by_cell_w = _by_cell(w)
    for a in armies:
        row = [0.0] * wfull
        # ★★ 归属 **六类**（与网格/候选同一套 `_owner_class`）—— 不再是"甲/乙"两位：
        #    ① **国家数不进观测形状**（多玩家落地时宽度不变，基座不用重炼）；
        #    ② 盟友的军与敌国的军**从此分得开**（引擎对这两类判定相反）。
        row[V.A_OWNER0 + _owner_class(w, me, a["x"], a["y"], by_cell_w)] = 1.0
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

    g_row = np.concatenate([encode_glob(sb, me, mask, known), F.glob_rule_vector()])
    arr = np.array(rows, dtype=np.float32) if rows else np.zeros((0, wfull), np.float32)
    # ★★ 行宽断言：**list 的切片赋值会静默改变长度**（`row[18:] = [14 个数]`
    #    在 28 长的 list 上会把它撑到 32，**不报错**）⇒ 少了这条，尾段整体错位
    #    也只是"模型少读到几列"，训练照跑。宁可在这里炸。
    assert arr.shape[1] == wfull, f"军队 token 行宽 {arr.shape[1]} ≠ 约定的 {wfull}"
    # ★★ `"k"` 组：**记忆中的敌军**（按番号）—— 与 `"m"`（将来的潜槽）**并存**，
    #   见 `vocab.TOKEN_GROUPS` 的注释。★ 一条都没有时返回**空表** ⇒ `collate`
    #   会补一个 mask 全 False 的零 token（注意力自动忽略），不会凭空多一行。
    k_rows = []
    for r in sb.known_enemies(me, mask, armies):
        row = [0.0] * V.K_WIDTH
        row[V.K_OWNER0 + _owner_class(w, me, r["x"], r["y"], by_cell_w)] = 1.0
        if r["kind"] in V.UNIT:
            row[V.K_UNIT0 + V.UNIT.index(r["kind"])] = 1.0
        row[V.K_X] = (r["x"] - hx) / ps
        row[V.K_Y] = (r["y"] - hy) / ps
        row[V.K_HP] = r["hp"] / 100.0
        row[V.K_NO] = min(1.0, r["no"] / V.NO_SCALE)          # ★ 番号
        row[V.K_AGE] = min(1.0, r["age"] / V.AGE_SCALE)       # ★ 陈旧度
        k_rows.append(row)
    karr = (np.array(k_rows, dtype=np.float32) if k_rows
            else np.zeros((0, V.K_WIDTH), np.float32))
    assert karr.shape[1] == V.K_WIDTH, f"k token 行宽 {karr.shape[1]} ≠ {V.K_WIDTH}"

    win = {"g": g_row[None, :].astype(np.float32), "a": arr, "k": karr}
    win_mask = {"g": np.ones(1, bool),
                "a": np.ones(len(rows), bool),
                "k": np.ones(len(k_rows), bool)}
    return win, win_mask, armies


# ============================================================ ★ 下标形态候选
def candidate_batch(sb, me: str, acts=None, mask=None, by_cell=None,
                    armies=None, known=None) -> dict:
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
    hx, hy = _home_cell(sb, me)
    ps = float(sb.size)
    mask = ((_vision(w, me) if mask is None else mask) if me else set())
    known = sb.known_halls(me, mask) if known is None else known
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
        # ★ 陷阱候选也要走 `known`："我知道那格是敌厅"，哪怕它此刻不在视野里
        #   —— 这正是"记忆/间谍"要买到的东西（厅拆不掉 ⇒ 见过就永远算数）。
        if t is not None and t["buildings"].get("市政厅", 0) > 0 \
                and ((x, y) in mask or (x, y) in known):
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
    known = sb.known_halls(me, mask)          # ★ 顺带把本帧看见的厅并进记忆（一次）
    armies = window_armies(sb, me, mask)
    frame = combat_of(sb, me, mask, armies)
    win, win_mask, _ = encode_window(sb, me, mask, frame=frame, armies=armies,
                                     known=known)
    return {
        "grid": encode_grid(sb, me, mask, known=known, frame=frame),
        "win": win,
        "win_mask": win_mask,
        "cand": candidate_batch(sb, me, acts, mask, armies=armies, known=known),
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
    """★ **已废**（只认"另一个"）—— 多玩家下"敌人"是一个**集合**，见 `enemies_of`。

    留着它只为让"谁还在按两国假设取敌人"立刻炸出来（现在没有调用方）。
    """
    raise AssertionError(
        "「_other」是两国假设的残骸：多玩家必须用 enemies_of(world, me)（集合），"
        "别再取「某一个」敌人 —— 那会静默漏掉另外几个对手")


def enemies_of(world, me: str | None) -> list:
    """★ **敌国集合**（除我**与我的盟友**之外的现存国家，顺序 = `world.order`）。

    用户 2026-09-25：多玩家（3 人起步）⇒「敌人」不再是一个人。
    ★ 用它替换原来的 `_other(me)`：那个只取"另一个"，三国局里会**静默漏掉一个对手**
      （它的军既进不了 token、也不算进 `foe_tiles`/`foe_armies`，而**不报错**）。

    ★★ **实现只有一份**：直接转发 `evaluate.rival_nations` —— 那个函数 2026-09-24
      就在了（"对手国 = 除我与我盟友之外的所有现存国家"），口径一模一样。
      ⚠ 千万别在这里再写一遍：**两份实现必然漂移**，而后果是
      "观测与打分器对**谁是对手**各说各话" —— 那种错不报错、只在训练里慢慢歪。
    """
    if not me:
        return []
    from .evaluate import rival_nations
    try:
        return rival_nations(world, me)
    except Exception:                          # noqa: BLE001
        return [n for n in getattr(world, "order", ())
                if n in world.nations and n != me]


def _by_cell(world) -> dict:
    """格 → 该格上的军（`_owner_class` 查野人用它；一次 O(军) 建好）。"""
    out: dict = {}
    for a in world.armies:
        if a.get("hp", 0) > 0:
            out.setdefault((a["x"], a["y"]), []).append(a)
    return out


def _vision(world, name: str) -> set:
    from ruleai.v11plus import pathfind
    return pathfind.vision_mask(world, name)


def _hall_cells_of(world, name: str | None, mask, known) -> list:
    """该国**已落成**的厅格 —— **视野 ∪ 永久记忆**（见 `vocab.GLOB` 的注释）。

    ★ `known` = `sandbox.known_halls(...)`，`{格: 最后看见时的主人}`。
      用户 2026-09-24：「**发现厅了就应该永久标记，因为厅是拆不掉也不能移动的**」
      ⇒ 不能再拿"当前视野"回答"厅在哪"（那会让已知的厅在观测里**闪断**）。
    ★ 归属以**当前** `t["owner"]` 为准（记忆只放宽可见性）—— 取保守那一侧。
    """
    # ★★ **转发 `hall_memory.cells_of_seen`**（唯一实现）—— 原来这里和
    #   `evaluate.hall_cells` **各写了一遍同样的谓词**，而两份都拿**当前真值**
    #   判归属 ⇒ 「厅永久标记」被看不见的易主无声推翻（实测见 `owner_seen` 的注释）。
    #   ★ 现在两处共用一份口径 ⇒ 不会再漂开。
    return HM.cells_of_seen(world, name, mask, known)


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


def _hp_frac(armies: list[dict], ref: float) -> float:
    """总血量 ÷ (100 × ref)。★ ref **只是个数**（见 scoring.HP_REF_ARMIES）——
    不许再拿"补员上限"当分母（那是**沙盒规则**推出来的量）。"""
    return min(1.0, sum(a.get("hp", 0) for a in armies)
               / max(1.0, 100.0 * max(1.0, ref)))