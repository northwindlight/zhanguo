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

★★ **它只记"哪一格有厅、最后看见时是谁的"，不记厅上有什么** ——
  「知道厅在哪」≠「看得见厅上的守军」，那是两件情报（见 `encode.py` 的文件头）。

⚠ 归属是"**最后一次看见时**的主人"。看不见的时候它可能**易主**（我只知道那儿还有座厅）。
  两人沙盒里这几乎不可达（夺厅必然是我打过去的 ⇒ 我看见了）；多人局要留意。
  实现上归属**只是放宽"可见性"要求**用的，真正算不算对手的厅仍看**当前** `t["owner"]`
  ⇒ 取的是**保守**那一侧（不会凭空多算一座）。
"""
from __future__ import annotations

HALL = "市政厅"


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