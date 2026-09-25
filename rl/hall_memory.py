# -*- coding: utf-8 -*-
"""★ **市政厅的永久记忆** —— 用户 2026-09-24：

    「**发现厅了就应该永久标记，因为厅是拆不掉也不能移动的**」

为什么要有这个东西
──────────────────
引擎的视野是**逐帧**的（`visible_to` = 自家/盟方地块及其八邻 + 瞭望塔），
而"厅在哪"这件事**一旦知道就永远成立**：

  · 厅**拆不掉** —— 引擎里没有拆建筑的路径：`_conquer` 只改 `t["owner"]`，
    `t["buildings"]` 原样留给新主人（实测：打下对手的厅 ⇒ 那格归我、**厅还在**、
    我这边"国祚 +1"）。**这条对敌我都成立** ⇒ 谁也不会少一座厅（只会易主）。
  · 厅**不会移动** —— 建筑挂在格子上，格子不动。

⇒ 拿"**当前视野**"回答"厅在哪"，是把**永久事实**当成了**瞬时观察**。
  后果两条，**都不报错**：

  ① **观测里闪断**：上一帧看见了、这一帧看不见 ⇒ 通道归零。模型刚"记住"的东西
     在输入里消失，等于让它去学一件**没法学**的事（同样的局面、不同的读数）。
  ② **打分器梯度闪断**：`evaluate` 的「逼近 / 守家」要读**敌厅在哪**
     （`hall_cells`）⇒ 敌厅一离开视野，那几项**当帧塌成 0**，
     势函数差分变成噪声（本该是"我看见过它，它一直在那儿"）。

★ 所以记忆**只增不减**（monotone）—— 这正是"厅拆不掉"的直接推论。

★ 两种可见模式**是同一个机制的两种初值**（用户：「对手的厅应该是**明知**的，
  有两种模式，一个是 llm **已经派了间谍**、明知对手厅了，一个是**没有**、
  **rl 模型自己找厅**」）：
    · 间谍模式 `all_known=True` —— 开局就**全知**（等价于"记忆初始就装满了"）
    · 自己找厅 `all_known=False` —— 记忆**从空开始**，靠 `observe` 累积
  ⇒ 调用方**不需要**再分情况：一律问 `known`，两种模式的差别只在构造时。

★★ **记忆必须可写**（用户 2026-09-25：「**任何记忆都允许合法外部修改，或者有办法
  传入新的，这是配合情报的设计**」）—— 见 `tell`。`all_known` 只是它的一个特例：
  这套东西最终要跟 **LLM 玩家**配合，而玩家的情报来源不止自己那点视野
  （间谍/盟友/战报/推理），**那些必须能进记忆**。★ 而"注进去的必须是合法情报"
  这条**只能由调用方负责**（自动倒引擎真值 = 偷看）⇒ 口子只开在沙盒层。

★★ **它只记"哪一格有厅、最后看见时是谁的"，不记厅上有什么** ——
  「知道厅在哪」≠「看得见厅上的守军」，那是两件情报（见 `encode.py` 的文件头）。

⚠ 归属是"**最后一次看见时**的主人"。看不见的时候它可能**易主**（我只知道那儿还有座厅）。
  两人沙盒里这几乎不可达（夺厅必然是我打过去的 ⇒ 我看见了）；多人局要留意。
  实现上归属**只是放宽"可见性"要求**用的，真正算不算对手的厅仍看**当前** `t["owner"]`
  ⇒ 取的是**保守**那一侧（不会凭空多算一座）。
"""
from __future__ import annotations

HALL = "市政厅"


def owner_seen(world, cell, mask, known) -> str | None:
    """★★ 那格那座厅、**我认知里的主人** —— 用户 2026-09-25：「**厅的归属会变的**」。

    · **看得见** ⇒ 当前真值（而且 `HallMemory.observe` 顺手就把记忆刷新了）；
    · **看不见** ⇒ **记忆里"最后看见时的"主人**（我不知道它易主了，就不许装作知道）。

    ★★ 为什么必须有这个函数：原来**两个消费端**（`encode._hall_cells_of` 和
      `evaluate.hall_cells`）都拿 **`t["owner"]`（当前真值）** 去判"这算不算某国的厅"，
      而记忆**只被用来放宽"可见性"**。后果是我实测出来的：

          我看着乙的厅 → 它在我看不见的时候易主给丙
          ⇒ `foe_hall_cells(乙)` **当场变成 []**（记忆里明明还写着乙）

      ⇒「**发现厅了就永久标记**」（用户 2026-09-24）这条**被无声推翻**：
        厅还在那儿、记忆也没丢，只是**消费端不肯认**。而且这个跳变**发生在一次
        我没有看见的事件上** ⇒ 势函数（逼近/守家）会跟着跳 ⇒
        正是 `HallMemory` 当初要堵的**闪断**，从"归属"这扇门又进来了。
      ★ 反过来（看不见的易主被**学到**）也是错的：那是白得情报。用记忆里的主人
        ⇒ **两个方向同时修好**：标记不丢、易主也学不到。
    """
    if known is not None and cell not in mask and cell in known:
        return known[cell]
    t = world.tiles.get(cell)
    return None if t is None else t["owner"]


def cells_of_seen(world, name: str | None, mask, known) -> list:
    """`name` 名下的厅格（**按我认知里的主人**，口径见 `owner_seen`）—— 排序稳定。

    ★★ **唯一实现**：`encode._hall_cells_of`（观测）与 `evaluate.hall_cells`（打分器）
      都走它。原来这两处各写了一遍同样的谓词 —— 抄两遍就会**慢慢漂开**，
      而漂开的后果是"同一件事在观测和打分器里读数不一致"，**不报错**。
    """
    if not name:
        return []
    out = []
    for cell, t in sorted(world.tiles.items()):
        if t["buildings"].get("市政厅", 0) <= 0:
            continue
        # ★ 记忆只放行"哪一格有厅"；`mask=None`（自家/盟友，全知）⇒ 一律放行
        if mask is not None and cell not in mask \
                and (known is None or known.get(cell) != name):
            continue
        if owner_seen(world, cell, mask if mask is not None else (), known) != name:
            continue
        out.append(cell)
    return out


class HallMemory:
    """一局之内、**逐国**的"已知市政厅"账本。`{格: 最后看见时的主人}`。"""

    def __init__(self, all_known: bool = False):
        # ★ `all_known=True` ⇒ **不记账**，每次都现读全图（间谍模式）
        self.all_known = bool(all_known)
        self._by: dict[str, dict] = {}

    # ---------------------------------------------------------------- 记
    def observe(self, world, name: str | None, mask) -> int:
        """把 `name` **此刻看得见**的厅并进它的记忆（返回新增座数）。

        ★ 只并**看得见的**（`mask`）—— 记忆是"**我见过**"，不是"全图"。
        ★ 只增不减：已经在册的不再改归属（`setdefault`）—— 免得"这一帧看不见"
          反而把已知的擦掉。同格易主时**看得见**就会更新（下面的覆盖）。
        """
        if self.all_known or not name:
            return 0
        got = self._by.setdefault(name, {})
        n0 = len(got)
        for cell, t in world.tiles.items():
            if t["buildings"].get(HALL, 0) > 0 and cell in mask:
                got[cell] = t["owner"]
        return len(got) - n0

    # ---------------------------------------------------------------- 读
    # ---------------------------------------------------------------- 情报注入
    def tell(self, name: str, items: dict) -> int:
        """★★ **外部注入情报**（用户 2026-09-25：「**任何记忆都允许合法外部修改，
        或者有办法传入新的，这是配合情报的设计**」）。

        `items` = `{格: 主人}`（例如盟友/间谍报来的"那几个厅是谁的"）。
        返回写入条数。

        ★★ 为什么记忆必须**可写**：这套东西最终要跟**LLM 玩家**配合 ——
          玩家的情报来源**远不止自己那点视野**（间谍、盟友、战报、自己的推理），
          那些情报**必须能进记忆**，否则模型看到的永远是"只有它自己眼睛看到的世界"，
          而真实玩家不是那样玩的。
        ★ `all_known=True` 那种"开局全知"其实就是**这个口子的一个特例**
          （把"全部厅"一次性注入）—— 两条路合成一条，少一套机制。
        ★★ **合法性由调用方负责，而且这是唯一的要求**：注进去的必须是
          "**这个玩家合法拥有的情报**"。把引擎真值（`world.tiles[...]["owner"]`）
          **自动**倒进来就变成了偷看 —— 那是本线最忌的事。
          ⇒ 所以这个口子**只开在沙盒层**（`sandbox.tell_halls`），
            **`encode` 里不许调它**：观测只读记忆，不写记忆（除了"看见就覆盖"）。
        ★ 合并语义与"看见"**完全一致**：写进去就是"我（被告知）看见了"，
          之后**看不见时一个字都不动**（不会被下一次视野观测擦掉）。
        """
        got = self._by.setdefault(name, {})
        n = 0
        for cell, owner in dict(items).items():
            c = (int(cell[0]), int(cell[1]))
            if owner is None:
                continue
            got[c] = owner
            n += 1
        return n

    def known(self, world, name: str | None) -> dict:
        """该国**已知**的厅 `{格: 主人}`（记忆的**副本** —— 别让调用方改到账本）。"""
        if self.all_known:
            return {c: t["owner"] for c, t in world.tiles.items()
                    if t["buildings"].get(HALL, 0) > 0}
        return dict(self._by.get(name) or {})

    def cells_of(self, world, name: str | None, owner: str) -> frozenset:
        """该国记忆里、**最后看见时属于 `owner`** 的厅格（"我知道对手的哪些厅在哪"）。"""
        return frozenset(c for c, o in self.known(world, name).items() if o == owner)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by.values())

    # ---------------------------------------------------------------- 复制
    def clone(self) -> "HallMemory":
        m = HallMemory(self.all_known)
        m._by = {k: dict(v) for k, v in self._by.items()}
        return m