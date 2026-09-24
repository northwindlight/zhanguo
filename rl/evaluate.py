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
      · **逼近**：★ **只有我这一半** —— 我军离敌厅越近越好（敌军往我这边推进记在**它自己**的分里）
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


def score(world, me: str, enemy=None, *, mask=None, allies=None,
          known=None, kills=None, dmg=None) -> float:
    """从 `me` 视角打分（正 = 我占优）= **自己 + 0.5 × 盟友（不含国土差）**。

    ★ **敌方的一切都过 `mask`**（见文件头）。`mask` = `pathfind.vision_mask(world, me)`；
      `None` ⇒ 全知（只给诊断用）。

    ★★ **`mask` 及它后面的一律是"仅限关键字"** —— 这是**防偷看**的一道结构闸，不是风格：
      `enemy` 现在是**可选**的，若 `mask` 还能当第 3 个位置参数传，那么
      `score(w, me, mask)`（想省掉 enemy 的写法）会被**静默**解释成 `enemy=mask`
      而 `mask` 保持 `None` ⇒ **全知打分**（打分器能点名视野外的敌军/国土）。
      2026-09-25 我自己写测试时就踩了：那条测试"通过"了，但它测的其实是全知口径。
      ⇒ 改成仅限关键字后，写错的调用**当场 `TypeError`**，不会静默降级成偷看。

    ★ 盟友那一份**用的还是同一张 `mask`** —— 这不是省事，是**正确**：
      `vision_mask` 建的时候就把**联盟成员的地块**算进去了（`o not in members` 就跳过）
      ⇒ 那张 mask 本来就是**整个联盟的**视野，拿它评估盟友既不多看、也不少看。

    ★★ `enemy`（用户 2026-09-25：多玩家 3 人起步）—— 可以是**一个名字、一串名字、
      或 `None`**。`None` ⇒ **所有对手**（`rival_nations`）。
      ⚠ 原来这里是"**那一个**敌人"的假设：三国局里被漏掉的那个对手，
        它的军队/国土/厅**一概不进分数**，而**不报错** —— 模型会以为天下只有两个人。
      ⇒ 现在 `foe_*` 的每一项都是**对手集合上的聚合**（国土/兵力/血量求和，
        厅取并集，距离取最近）。二国局下与原来**逐位相同**（集合里就一个人）。
    """
    t = terminal(world, me, enemy)
    if t is not None:
        return t                          # 已定局 ⇒ 直接用终局分（**与 `terminal` 同一套口径**）
    allies = allies_of(world, me) if allies is None else list(allies)
    foes = _as_list(enemy) or rival_nations(world, me, allies)
    s = _one(world, me, foes, mask, with_tiles=True, known=known,
             kills=kills, dmg=dmg)
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
    #   ★★ **盟友的厅也走同一本账**（`mask` ∪ `known`）—— 用户 2026-09-24：
    #      「顺便**盟友发现厅应该也纳入标记**」。原来这里是 `halls_of(world, al)`
    #      （**没传 mask/known = 全知**），而 `encode_glob` 的 ally 段走的是 `known`
    #      ⇒ 同一件事在**观测**和**打分器**里两套口径（实测：同一局面下无 mask→1 座、
    #      空 mask→0 座）。两半读数不一致就是"不报错的错"，一律归到一本账上。
    #   ★ 结构上二者**当前等价**（`vision_mask` 把联盟成员的**地块**也算进视野
    #      ⇒ 盟友的厅本来就看得见），所以这里**不改数值**；改的是**机制**：
    #      将来联盟/视野口径一改，打分器跟着走，不会静默说谎。
    s += S.W_HALL * (halls_of(world, me)
                     + S.ALLY_SHARE * sum(halls_of(world, al, mask, known)
                                          for al in allies if al not in foes))
    for al in allies:
        if al == me or al in foes:
            continue
        if not world.has_townhall(al):
            s += S.ALLY_SHARE * S.ALLY_DEAD   # 见 `ALLY_DEAD` 的注释
            continue
        # ★ 盟友那一份传**同一串对手**：盟与我是**同一外交实体** ⇒ 对手集合本就相同
        #   （`rival_nations` 按实体算），所以这不是近似，是同一个集合。
        s += S.ALLY_SHARE * _one(world, al, foes, mask, with_tiles=False,
                                 known=known, kills=kills, dmg=dmg)
    return s


def _sum_pairs(table, me: str, foes) -> int:
    """从 `{(凶手, 受害者): 累计}` 里取"**我打敌国**"那一份的和。

    ★ 为什么按对记、并在这里筛：账本不该猜敌我关系（会变），而打分器**知道**
      当前的 `foes`（盟友/中立/野人都在 `foes` 之外）⇒ 口径只有一处。
    """
    if not table:
        return 0
    fs = set(foes)
    return sum(n for (k, v), n in table.items() if k == me and v in fs)


def _as_list(enemy) -> list[str]:
    """`enemy` 归一成**名字列表**：`None` ⇒ `[]`（由调用方决定"那就是全部对手"）；
    字符串 ⇒ 单元素；**列表/元组** ⇒ 原样。

    ★★ **只收 `str`/`list`/`tuple`，其余一律 `TypeError`** —— 这是**防偷看的结构闸**，
      不是类型洁癖。要挡的是这个错：`score(world, me, mask)`（想省掉 `enemy`）
      会被读成 `enemy=mask`，而 `mask` 保持 `None` ⇒ **全知打分**（打分器能点名
      视野外的敌军/国土），而它**不报错**、只是悄悄不再迷雾受限。
      ★ 为什么"仅限关键字"不够：`*` 只挡第 **4** 个位置参数，而危险的那个调用
        **只有 3 个**（`world, me, mask`）⇒ 照样静默通过（我 2026-09-25 实测过）。
      ★ 为什么判 `frozenset`/`set`/`dict`：`mask` 的实际类型就是 `frozenset`
        （`pathfind.vision_mask` 的返回），而"敌人"**永远**是国名字符串
        ⇒ 收到集合就一定是把 mask 传错了位置。
    """
    if enemy is None:
        return []
    if isinstance(enemy, str):
        return [enemy]
    if isinstance(enemy, (list, tuple)):
        return list(enemy)
    raise TypeError(
        f"`enemy` 只收 国名字符串 / 名字列表 / None，收到 {type(enemy).__name__}："
        f"{enemy!r} —— ★ 多半是**把 `mask` 传到了 `enemy` 的位置**"
        f"（`score(world, me, mask)` 会被读成 `enemy=mask`，而 `mask` 仍是 None"
        f" ⇒ **全知打分 = 偷看**）。要传视野请写 `mask=`。")


def _one(world, me: str, foes, mask, *, with_tiles: bool,
         known=None, kills=None, dmg=None) -> float:
    """单国评分（打分构成见文件头）。`with_tiles=False` ⇒ **不算国土差**（盟友那一份用）。

    ★★ `foes` = **对手集合**（见 `score` 的 docstring）。集合里每一项都参与聚合，
      二国局下与"那一个敌人"逐位相同。

    ★ 这里**不再**对"对手已亡"返回 `+INF` —— 那件事是不是胜局由 `score` 判
      （要看**全部**对手）。对手亡时它那几项自然塌成 0（没有军队/国土/核心），
      剩下的就是"我自己这一摊"，是有限的、有意义的数。
    """
    if not world.has_townhall(me):
        return -S.INF

    s = 0.0
    if with_tiles:
        # ★ 盟友那一份**去掉这一项**（用户：「盟友评分**除了地皮分以外**」）——
        #   理由也自洽：盟友的地**可 mv 不可 atk**，本来就不是我能夺取的目标，
        #   给盟友的地记分等于奖励一件我做不到的事。
        # ★★ **只算我自己的国土** —— 用户 2026-09-25（这条口径覆盖除威胁系统以外的**全部**项）：
        #   「你记账到底是怎么写的，**每个打分器只对自己国家负责**就行了，例如
        #    **甲打了一块地，甲自己的计分器加分，乙的扣分**，**完全不需要什么视野地图**」。
        #
        #   ⚠ 原来写的是 `W_TILE × (我的国土 − **看得见的**敌国国土)`，两个错：
        #     ① **重复计**（就是他在厅那里骂过的那个毛病）：甲打下一格 ⇒ 我的国土 +1
        #        **并且** 敌国国土 −1 ⇒ 甲**一次拿两分**；而"乙扣分"本该是**乙自己那本账**
        #        的事（乙的 `tiles(乙)` 少一格，乙的分自然少）⇒ 同一件事记两遍。
        #     ② **迷雾悖论**：那半过 mask ⇒ **侦察到敌国领土反而扣分**、丢视野反而涨分。
        #   ⇒ 现在**一行、无 mask、无重复**：`W_TILE × 我的国土`。
        #   ★ 拿下敌国地块依然加分（我的国土 +1），"越多地分越高、丢地扣小分"逐字兑现。
        s += S.W_TILE * tiles(world, me)
    # ★★ **击杀 = 累计事件计数**（用户 2026-09-25：「会不会太复杂了，**只计算我军
    #   杀掉的敌军来加分**就行了」）—— 替换掉原来那一项「**看得见的**敌国军队数」。
    #   那一项有两个病（与厅那条一模一样）：
    #     ① **迷雾悖论**：只数看得见的 ⇒ **侦察到敌军反而当场扣分**、丢视野反而涨分，
    #        把"该去侦察"教成负收益；
    #     ② **闪断**：同一件事实一帧读得到、一帧读成 0 ⇒ 势函数差分变噪声。
    #   ⇒ 改成**只增不减、与视野无关**的计数（`sandbox.KillLedger`，按结算瞬间
    #     同格共处归因）。`kills` = `{国: 累计击杀}`；`None` ⇒ 当成 0
    #     （**没接账本**的调用方得 0 而不是"看不见就偷看"）。
    s += S.W_KILL * _sum_pairs(kills, me, foes)
    # ★★ **打掉的血也改成累计事件**（用户 2026-09-25：「`W_HP × (我的血 − 看得见的
    #   敌方血)` **也要改，和击杀一样**」）—— 两项同源、同一个账本。
    #   ★ **我的血**那半**不变**：我自己的血量本来就是全知（没有迷雾问题）。
    #   ⚠ `foe_armies`（**看得见的**敌国军队数）那一项还在 `W_ARMY`/`W_TILE` 里
    #     没有对应物 —— 见下面 `foe_tiles` 的注释（那是**同一个病**的最后一处）。
    s += S.W_HP * hp_total(world, me)                    # 我的血：全知，不变
    s += S.W_HP * _sum_pairs(dmg, me, foes)              # 打掉敌军的血：累计、不进迷雾
    s += S.W_ARMY * len(armies(world, me))               # 我方：全知

    # ★★ 逼近 / 威胁 / 守家：**对每一座厅都生效**（用户 2026-09-24：「打分器的**距离
    #   市政厅**的厅，应该**对每个市政厅都生效**」）—— 一国有两座厅时，不能只盯其中一座。
    my_halls = hall_cells(world, me)                   # 我的厅：全知
    # ★ 敌的厅 = **所有对手的厅的并集**（视野 ∪ **记忆**）——
    #   多玩家下"最近的敌厅"要跨全部对手取，不能只看一个。
    foe_halls = foe_hall_cells(world, foes, mask, known)
    # ★★ **"逼近"和"守家"要分开判**（2026-09-24 拆开，`--no-halls-known` 逼出来的）：
    #   · **逼近**（`W_NEAR`）★ 2026-09-25 起**只算我自己那一半**（"我的军离敌厅多近"）
    #     —— 敌厅的位置来自**永久记忆账本**（不是当前视野）；对手往我这边推进
    #     在**对手自己的账**里记（对手的 `−my_d`）。
    #   · **威胁**（`W_THREAT`）只看**我的厅** + **敌军在哪**
    #     （`foe_d` 走的是 `min_dist(world, enemy, …)` = **敌军**到我的厅的距离，
    #     **不是**敌厅到我的厅）⇒ ★ **跟知不知道敌厅毫无关系**。
    #   ⚠ 原来两块塞在同一个 `if my_halls and foe_halls:` 里 ⇒ 在"自己找厅"那一版
    #     里**敌厅没找到之前，守家一分没有** —— 用户加这两项就是为了让"回防"有收益
    #     （「5 支军队赖着主城不动压根输不了，是打分模型**没有奖励防御**」），
    #     而那个版本要跑到找到厅为止才生效 ⇒ **防御梯度整局缺席**。
    if my_halls and foe_halls:
        # 我离**最近的敌厅**（挑最好打的那座）
        my_d = min(min_dist(world, me, h) for h in foe_halls)
        # ★★ **只留我自己的那一半**（用户 2026-09-25：「每个打分器**只对自己国家负责**」）
        #   —— 原来还有 `+ foe_d`（**敌**军离我的厅多远）：那是**对手自己那本账**的事
        #   （对手的 `−my_d` 里已经记过一遍），放进我的分里就是**同一件事记两遍**。
        s += S.W_NEAR * (-my_d)

    # ★★ 安全 / 防御 —— ★ **只扣分**（用户 2026-09-25：「其实**扣分就行了**」）。
    #   ★★ **这是全套打分里唯一允许"读敌军位置"的地方，而且是用户明说正确的**：
    #      「再说这里**本来就是暴露出的敌人越多防御越有价值，没暴露的也无法虚空防守**」
    #      ⇒ 这一项的迷雾门控**不是毛病，是正确**：看不见的敌人本来就防不了，
    #        "虚空防守"没有意义。⇒ 它**不**适用"完全不需要视野"那条口径（那条覆盖
    #        其它所有项），keep as-is。
    #   ⚠ 原来还按威胁强度给"守家的军"**加分**（`W_GUARD`）—— **已停用**：
    #     那会把"缩在核心不动"变成无条件收益（另一个极端），而**威胁本身已经提供
    #     了回防的压力**（不走过去处理就一直扣分）。
    #   ★ 判定用**最近的那支敌军**（到我最危险的那座厅），所以"保住任何一座"都算数。
    if my_halls:
        foe_d = min(foe_min_dist(world, foes, h, mask) for h in my_halls)
        if foe_d <= S.THREAT_R:
            intensity = (S.THREAT_R - foe_d + 1) / float(S.THREAT_R)
            s -= S.W_THREAT * intensity
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


def hall_cells(world, name: str, mask=None, known=None) -> list:
    """该国**所有已落成**的市政厅格（排序稳定）。

    ★ 用户 2026-09-24：「打分器的**距离市政厅**的厅，应该**对每个市政厅都生效**」
      ⇒ 逼近/威胁/守家那三项都走这个函数，**不是只取第一座厅**（`core_of` 只回一座，
      一国有两座厅时另一座等于不存在）。

    ★★ `known` = **已知的厅** `{格: 最后看见时的主人}`（`sandbox.known_halls`）——
      用户 2026-09-24：「**发现厅了就应该永久标记，因为厅是拆不掉也不能移动的**」。
      ★ 为什么不能用"当前视野"代替它：厅是**永久事实**，而视野是**逐帧**的
        ⇒ 敌厅一离开视野，逼近/守家那几项**当帧塌成 0**，势函数差分变噪声。
        详见 `rl/hall_memory.py`。
      ★ 归属仍以**当前** `t["owner"]` 为准（记忆只放宽**可见性**）⇒ 取**保守**那一侧。

    ★ 这**只**放行"厅在哪"，**不**放行厅上的守军 —— 军队的可见性永远走真正的
      `mask`（`armies()` / `min_dist()`），那是两件情报。
    ★ `mask=None` ⇒ **不过滤**（自己的厅、盟友的厅走这条：`vision_mask` 本来
      就把自家与盟方的地算进去了）。
    """
    out = []
    for cell, t in sorted(world.tiles.items()):
        if t["owner"] != name or t["buildings"].get("市政厅", 0) <= 0:
            continue
        if mask is None or cell in mask or (known is not None and known.get(cell) == name):
            out.append(cell)
    return out


def halls_of(world, name: str, mask=None, known=None) -> int:
    """该国**已落成**的市政厅**座数**（= 国祚，也是补员产能）。

    ★ 数**对手**的厅时必须传 `mask`（+ `known`）—— 厅只在**视野内**公开
      （引擎 `_public_buildings`："看不见就打不着"），**见过一次之后永久记得**。
      自己与盟友的厅走全知（`vision_mask` 本来就把盟友的地算进去了）。
    """
    return len(hall_cells(world, name, mask, known))


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


# ---------------------------------------------------------------- ★ 对手是**集合**
# 用户 2026-09-25：多玩家（3 人起步）⇒「敌人」不再是一个人。下列四个是上面四个的
# **集合版**：国土/兵力求和，厅取并集，距离取最近。`foes` 是名字列表（`_as_list`）。
# ⚠ 别在调用点自己写 `sum(... for n in foes)` —— 散落多处就会有一处漏掉某个对手，
#   而那种错**不报错**（分数少算一块，训练照跑）。
def foe_tiles(world, foes, mask=None) -> int:
    """对手**国土总和**（★ 每一个都过 `mask`）。"""
    return sum(tiles(world, n, mask) for n in foes)


def foe_armies(world, foes, mask=None) -> list[dict]:
    """对手**全部军队**（★ 每一个都过 `mask`；看不见的一个都不算）。"""
    return [a for n in foes for a in armies(world, n, mask)]


def foe_hall_cells(world, foes, mask=None, known=None) -> list:
    """对手**全部已落成的厅格**（并集，排序稳定 ⇒ 取"最近的那座"是确定性的）。"""
    return sorted({c for n in foes for c in hall_cells(world, n, mask, known)})


def foe_min_dist(world, foes, cell, mask=None) -> int:
    """**任意对手**的军队到 `cell` 的最近距离（没有 ⇒ 99）。"""
    pool = foe_armies(world, foes, mask)
    if not pool:
        return 99
    return min(max(abs(a["x"] - cell[0]), abs(a["y"] - cell[1])) for a in pool)