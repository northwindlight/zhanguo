# -*- coding: utf-8 -*-
"""**打分器**：给一个局面打分（用户 2026-09-24：「你先写搜索逻辑和打分器」）。

    它是 MCTS 的**叶子评估**、PPO 的**势函数**（奖励取其差分），也是将来价值网络的老师。

    ★★★ 铁律：**只对可见视野打分**（用户 2026-09-24）
    ────────────────────────────────────────────────
    「你怎么知道敌军离你多远，**这不是暴露信息了吗**，**打分只对可见视野打分**」。

    旧版（第一稿）用了全图：`min_dist(world, enemy, mc)`（敌军到我核的距离）、
    `len(armies(world, enemy))`（敌军总数）、`tiles(world, enemy)`（敌方国土）——
    那是**打分器作弊**：它会奖励"朝一个我根本看不见的敌人走过去"，而那个信号在真实
    对局里**不存在**。模型照着它学，学到的是**上帝视角策略**，一进有迷雾的对局就废。

    这与旧线记过的"**四处越权偷看**"（`ruleai/v9.py`）是同一类 ——
    v9 当年就是靠 `visible_to` 门控把那些堵掉的（`zhanguo_ruleai_v9`）。

    ⇒ 本文件所有涉及**敌方**的量（军队、国土、核心、血量）**一律过 `mask`**；
      **自己**的量全知（那是应该的）。
      `mask=None` ⇒ 全知模式，**只该用于无迷雾的诊断/对照，别进训练**。

    打分构成（量纲：1 分 ≈ 1 格国土；权重都可调）
    ─────────────────────────────────────────────
      · **国祚**：自己没了 = `-INF`，对手没了 = `+INF`（亡国是**公开事件**，不算偷看）
      · **国土差 / 兵力差 / 血量差**（敌方那半边过 mask）
      · **逼近**：我军离敌核越近越好、敌军离我核越近越糟（**两边都得看得见才算**）
      · ★ **安全 / 防御**（用户：「怎么不可能回防，5 支军队赖着主城不动压根输不了，
        是目前的打分模型**没有奖励防御，没有安全扣分机制**」）
      · ★★ **盟友**（用户 2026-09-24）：「评分系统中应该加入**盟友的评分**，盟友评分
        **除了地皮分以外**，应该和自己的评分机制一致，但是**分数只有 50%**」
        ⇒ `score = 自己 + 0.5 × 盟友(不含国土差)`。盟友那一份走**同一个 `_one`**，
        所以将来改 `_one` 的构成，盟友自动跟着改（这正是"机制一致"的意思）。
"""
from __future__ import annotations

# ★★ **打分器的所有数字都在 `rl/scoring.py`**（用户 2026-09-24：「别硬编码，打分标准是
#   先验的，可能要经常调，学 `balance.py` 抽出来」）—— 本文件只剩下**怎么算**。
#
#   ★ 一律写成 `W_TILE` 这种**模块属性访问**，**不要** `from .scoring import W_TILE`：
#     后者会在导入时把值绑死在 `evaluate` 的命名空间里，之后改 `scoring.W_TILE` 对打分
#     **毫无影响**，而那种错**不报错**（引擎那边吃过同一个亏）。
from . import scoring as S

INF = S.INF


def score(world, me: str, enemy: str, mask=None, allies=None) -> float:
    """从 `me` 视角打分（正 = 我占优）= **自己 + 0.5 × 盟友（不含国土差）**。

    ★ **敌方的一切都过 `mask`**（见文件头）。`mask` = `pathfind.vision_mask(world, me)`；
      `None` ⇒ 全知（只给诊断用）。

    ★ 盟友那一份**用的还是同一张 `mask`** —— 这不是省事，是**正确**：
      `vision_mask` 建的时候就把**联盟成员的地块**算进去了（`o not in members` 就跳过）
      ⇒ 那张 mask 本来就是**整个联盟的**视野，拿它评估盟友既不多看、也不少看。
    """
    t = terminal(world, me, enemy)
    if t is not None:
        return t                          # 已定局 ⇒ 直接用终局分（**与 `terminal` 同一套口径**）
    allies = allies_of(world, me) if allies is None else list(allies)
    s = _one(world, me, enemy, mask, with_tiles=True)
    # ★★ 国祚那一项**只数我这边**（用户 2026-09-24 纠正）：
    #   「**分数是针对于自己而言，得厅加分，丢厅扣分，和对面几个厅有半毛钱关系？**」
    #
    #   ⚠ 我上一版写的是 `+ W_HALL × (我的厅 + 0.5×盟友的厅 **− 对手的厅**)`，两处错：
    #     ① **重复计**：引擎实测「打下一座对手的厅 ⇒ 那格的 `owner` 变成我、建筑保留」
    #        （`甲厅 1→2`）⇒ 拿下这件事**已经**体现在"我的厅 +1"里了，再从对手那侧扣一次
    #        等于同一件事记两遍。
    #     ② **迷雾悖论**：`halls_of(对手, mask)` 只数看得见的 ⇒ **侦察到对手的厅反而
    #        当场扣 500**（势函数是差分），"去找厅"在奖励上变成负的 —— 而找厅恰恰是
    #        本任务要模型学会的事。不 mask 又是偷看。**根因就是"分数里不该有对手的厅"。**
    #   ⇒ 现在这一项**不需要 mask**：我的厅全知、盟友的（同盟共享视野）也全知，
    #     对手的厅**根本不进分数** ⇒ 迷雾悖论从根上消失，侦察不再有负收益。
    #
    #   ★ 对手的厅数在**别处**仍然有用且合法：`_one` 里的逼近/威胁项要"我离最近的
    #     敌厅多远"（那是**距离**、是差分，侦察到更近的厅是**加分**的）。
    #   ★ 放在 `score` 里**只加一次**（不放进 `_one`）—— 放进 `_one` 会让同一座厅
    #     同时给我和盟友各记一笔（盟友那份又是 0.5）。
    s += S.W_HALL * (halls_of(world, me)
                     + S.ALLY_SHARE * sum(halls_of(world, al)
                                          for al in allies if al != enemy))
    for al in allies:
        if al == me or al == enemy:
            continue
        if not world.has_townhall(al):
            s += S.ALLY_SHARE * S.ALLY_DEAD   # 见 `ALLY_DEAD` 的注释
            continue
        s += S.ALLY_SHARE * _one(world, al, enemy, mask, with_tiles=False)
    return s


def _one(world, me: str, enemy: str, mask, *, with_tiles: bool) -> float:
    """单国评分（打分构成见文件头）。`with_tiles=False` ⇒ **不算国土差**（盟友那一份用）。

    ★ 这里**不再**对"`enemy` 已亡"返回 `+INF` —— 那件事是不是胜局由 `score` 判
      （要看**全部**对手）。`enemy` 亡时它那几项自然塌成 0（没有军队/国土/核心），
      剩下的就是"我自己这一摊"，是有限的、有意义的数。
    """
    if not world.has_townhall(me):
        return -S.INF

    s = 0.0
    if with_tiles:
        # ★ 盟友那一份**去掉这一项**（用户：「盟友评分**除了地皮分以外**」）——
        #   理由也自洽：盟友的地**可 mv 不可 atk**，本来就不是我能夺取的目标，
        #   给盟友的地记分等于奖励一件我做不到的事。
        s += S.W_TILE * (tiles(world, me) - tiles(world, enemy, mask))
    s += S.W_ARMY * len(armies(world, me))               # 我方：全知
    s -= S.W_KILL * len(armies(world, enemy, mask))      # ★ 敌方：**只数看得见的**
    s += S.W_HP * (hp_total(world, me) - hp_total(world, enemy, mask))

    # ★★ 逼近 / 威胁 / 守家：**对每一座厅都生效**（用户 2026-09-24：「打分器的**距离
    #   市政厅**的厅，应该**对每个市政厅都生效**」）—— 一国有两座厅时，不能只盯其中一座。
    my_halls = hall_cells(world, me)                   # 我的厅：全知
    foe_halls = hall_cells(world, enemy, mask)         # ★ 敌的厅：看不见 ⇒ 空的
    if my_halls and foe_halls:
        # 我离**最近的敌厅**（挑最好打的那座）；敌离**我最危险的那座厅**
        my_d = min(min_dist(world, me, h) for h in foe_halls)
        foe_d = min(min_dist(world, enemy, h, mask) for h in my_halls)
        s += S.W_NEAR * (-my_d + foe_d)

        # ★★ 安全 / 防御 —— **只在真有威胁时生效**：
        #   没威胁时守家**不加分**，否则模型会永远缩在核心格不动（另一个极端）。
        #   有威胁时：被逼近扣分 + **守家的军按逼近强度加分** ⇒ "赖在主城"第一次有了收益，
        #   "回来拦"也有了收益 —— 攻守这才对称。
        #   ★ 判定用**最近的那座敌厅/最近的那支敌军**，所以"保住任何一座"都算数。
        if foe_d <= S.THREAT_R:
            intensity = (S.THREAT_R - foe_d + 1) / float(S.THREAT_R)
            s -= S.W_THREAT * intensity
            guards = sum(1 for a in armies(world, me)
                         if min(max(abs(a["x"] - h[0]), abs(a["y"] - h[1]))
                                for h in my_halls) <= S.GUARD_R)
            s += S.W_GUARD * intensity * guards
    return s


def allies_of(world, me: str) -> list[str]:
    """与 `me` 同盟的**其他国家**（同属一个外交实体）。没联盟 ⇒ `[]`。

    ★ 联盟是**开局指定的场景条件**（用户 2026-09-24：「是否中立和联盟和模型无关，
      开局直接指定」）⇒ 这里只**读**关系，不产生任何外交动作。
    """
    return [n for n in world.nations
            if n != me and n in world.order and world.allied_between(me, n)]


def rival_nations(world, me: str, allies=None) -> list[str]:
    """**对手国** = 除我与我盟友之外的所有现存国家（顺序 = `world.order`）。

    ★ 只看"还在不在册"（`world.nations`），**不看有没有厅** —— 那是 `dead_rivals` 的事。
    """
    mine = {me, *(allies_of(world, me) if allies is None else allies)}
    return [n for n in world.order if n in world.nations and n not in mine]


def dead_rivals(world, me: str, allies=None) -> list[str]:
    """**已被灭的对手国**（市政厅尽失）。

    ★ 只看"这个国家还在不在"，**不区分是谁灭的** —— 亡国是**公开事件**
      （引擎 `_eliminate_if_dead`），而"是谁打下来的"不是公开信息，要靠额外记账；
      而且在联盟体系里"少一个敌人"本身就是共同收益。⇒ 只数结果，不追究功劳。
    """
    return [n for n in rival_nations(world, me, allies) if not world.has_townhall(n)]


def terminal(world, me: str, enemy: str | None = None) -> float | None:
    """终局分（**只剩一个外交实体**）。没结束 ⇒ `None`。

    ★★ 胜者是**实体**，不是单个国家 —— 用户 2026-09-24：「**应该是联盟胜利或者
      单国胜利**」。⇒ 判据是"场上还剩几个**外交实体**（`entity_of`）"，不是"某一国的
      厅还在不在"：
        · 还剩 ≥2 个实体 ⇒ 未定局
        · 还剩 1 个     ⇒ 它赢；**我的实体**是它 ⇒ `+INF`，否则 `-INF`
        · 一个都不剩     ⇒ 同归于尽 ⇒ `0.0`
      ★ 我**战死但我的联盟赢了** ⇒ 也算我赢（`+S.INF`）—— 那正是"联盟胜利"的含义。

    **亡国是公开事件，不需要视野**（`has_townhall` 是公开的）。
    `enemy` 参数保留只为向后兼容，**不再参与判定**（多国时"某个对手"不是判据）。
    """
    alive = [n for n in world.order if n in world.nations and world.has_townhall(n)]
    if not alive:
        return 0.0                                   # 同归于尽
    ents = {world.entity_of(n) for n in alive}
    if len(ents) == 1:                               # ★ 只剩一个实体 ⇒ 它赢了
        return +S.INF if _my_entity(world, me) in ents else -S.INF
    if world.has_townhall(me) or _my_entity(world, me) in ents:
        return None                                  # 我还有戏（我活着，或我的盟还活着）
    return -S.INF


def _my_entity(world, me: str) -> str | None:
    """我的外交实体标签；我已从 `nations` 里消失 ⇒ `None`（尽力而为，不抛）。"""
    try:
        return world.entity_of(me) if me in world.nations else None
    except Exception:                                # noqa: BLE001
        return None


def hall_cells(world, name: str, mask=None) -> list:
    """该国**所有已落成**的市政厅格（排序稳定）。

    ★ 用户 2026-09-24：「打分器的**距离市政厅**的厅，应该**对每个市政厅都生效**」
      ⇒ 逼近/威胁/守家那三项都走这个函数，**不是只取第一座厅**（`core_of` 只回一座，
      一国有两座厅时另一座等于不存在）。
    """
    return [cell for cell, t in sorted(world.tiles.items())
            if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0
            and _vis(cell, mask)]


def halls_of(world, name: str, mask=None) -> int:
    """该国**已落成**的市政厅**座数**（= 国祚，也是补员产能）。

    ★ 数**对手**的厅时必须传 `mask` —— 厅只在**视野内**公开（引擎 `_public_buildings`：
      "看不见就打不着"）。自己与盟友的厅走全知（`vision_mask` 本来就把盟友的地算进去了）。
    """
    return len(hall_cells(world, name, mask))


# ---------------------------------------------------------------- 小工具（★都可过 mask）
def _vis(cell, mask) -> bool:
    return mask is None or cell in mask


def tiles(world, name: str, mask=None) -> int:
    """国土数。★ 数**敌方**时必须传 `mask`（视野外看不见谁占了哪）。"""
    return sum(1 for cell, t in world.tiles.items()
               if t["owner"] == name and _vis(cell, mask))


def armies(world, name: str, mask=None) -> list[dict]:
    """军队。★ 数**敌方**时必须传 `mask`（看不见的敌军不算 —— 这是本轮修的那个漏洞）。"""
    return [a for a in world.armies
            if a["owner"] == name and a.get("hp", 0) > 0 and _vis((a["x"], a["y"]), mask)]


def hp_total(world, name: str, mask=None) -> int:
    return sum(a.get("hp", 0) for a in armies(world, name, mask))


def core_of(world, name: str, mask=None):
    """该国的市政厅格 = 核心 = 国祚。★ 看不见 ⇒ `None`（**厅也要在视野内才知道在哪**，
    引擎 `_public_buildings` 原话："看不见就打不着"）。"""
    for cell, t in sorted(world.tiles.items()):
        if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0 and _vis(cell, mask):
            return cell
    return None


def min_dist(world, name: str, cell, mask=None) -> int:
    """`name` 的军队到 `cell` 的**最近切比雪夫距离**（没有军 ⇒ 返回 99）。

    ★ 传 `mask` 时只算**看得见**的军 —— 打分器要算"敌军离我核多远"就必须走这条，
      否则就是隔着迷雾点名敌军位置（用户 2026-09-24 抓到的那个漏洞）。
    """
    pool = armies(world, name, mask)
    if not pool:
        return 99
    return min(max(abs(a["x"] - cell[0]), abs(a["y"] - cell[1])) for a in pool)