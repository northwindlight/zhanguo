# -*- coding: utf-8 -*-
"""★★ **敌军的番号账本** —— 用户 2026-09-25：

    「总之模型要识别的出，**这次击退了 a 兵团，下次露头的是 a 军团的残余，
      还是一支没见过的、满编的 b 军团**」

为什么要有它
────────────
`HallMemory`（厅）解决的是"**永久事实**被当成瞬时观察"；
这里是**另一件事**，而且更难：

  · **军队会动** ⇒ "它现在在哪"和"我上次看见它在哪"**是两条不同的情报**，
    而后者**只有靠记忆才有**。
  · **军队会死、会被打残** ⇒ "残缺的 a 回来了"和"满编的 b 来了"
    在**当前这一帧**可能长得**一模一样**（同一格、同一兵种、同样看得见），
    **最优应对却相反**：a 的残余该追，没见过的满编 b 该先稳。
  · ⇒ 没有记忆时，模型面对的是一个**本质上的 POMDP**，而它只能输出确定性策略
    ⇒ 只能"折中"⇒ **学不出尖锐的战术**。这正是"战争迷雾"的代价。

★ 按**番号**记（引擎里每支军有唯一 `gid`，名字带国别序号 ——
  **番号在军事上本来就是玩家知道的信息**，不是偷看）。
★ 记的是「**最后一次看见它时**的样子」：回合 / 位置 / 兵种 / 血量 / 番号。

★★ 三条纪律（都对着"会静默教错"的形状）：

  ① **必须带陈旧度**（`age` = 距今多少回合）。不复用上古情报 ⇒
     不然等于教模型**相信幽灵**。`max_age` 之外**过期作废**。
  ② ★★ **"没了"和"走了"必须分开**，按**是否当场地看见**分流（用户 2026-09-25：
     「**被明确歼灭的，就要永久移除**」）：
       · **在我眼皮底下没了** —— 最后看见它的那一格**此刻仍在视野里**，
         而它已不在世上 ⇒ 我**看着它被歼灭** ⇒ **永久移除** ✓
       · **看不见时消失** —— 最后看见的那格**已不在视野** ⇒ 我无从知道
         它是死了还是绕后了 ⇒ **留着、让它自然变旧** ✓
     ★ 顺带这条把"**它挪走了**"也分开了：挪走的军**还在世上** ⇒ 不会被误删
       （不会把"绕后的敌军"当成"已歼灭"）。
  ③ ★ **不用全图真值去"发现"任何事**。真值只用来回答一个**我在盯着看的问题**：
     "我正看着的那一格，那支军还在不在"。★ 绝不拿"gid 不在 `world.armies` 里了"
     去**扫一遍全账本** —— 那会让模型在**视野外**白得一条情报（"它没了"），
     而真玩家**不该知道**。**这是用户总口径「除威胁系统外不许读对手当前状态」
     在记忆上的直接推论。**

★★ **本账本没有"外部注入"这条路**（2026-09-25 收回）：我一度加过 `tell()`
  （逐军注入位置 + 番号），而引擎的间谍明说「**位置/血量/番号不外泄**」
  （`mp.World._econ_snapshot`）⇒ 那条路**正是引擎禁止间谍给的东西**。
  合法的外部情报只有两种：**市政厅**（`hall_memory.tell`）与**各兵种数量**
  （`rl/intel.py`，只有数量）⇒ **位置与番号只能来自"自己看见"**。

★ 与 `HallMemory` 的对照：厅**拆不掉、不能移动** ⇒ 记忆**只增不减**；
  军队**会动会死** ⇒ 记忆**必须带时间戳、必须会过期**。两件事不能共用一套。
"""
from __future__ import annotations

DEFAULT_MAX_AGE = 20      # 超过这么多回合没再看见 ⇒ 作废（别拿上古情报当现状）
DEFAULT_CAP = 24          # 一条观测里最多带几支"幽灵军"（★ 注意力是 O(N²)，必须有界）


def visible_foes(world, name: str | None, mask) -> dict:
    """本帧**看得见**的敌军：`{gid: {x, y, kind, hp, no, owner}}`（**不含**见到的回合）。

    ★★ **唯一出处** —— `WarMemory.observe`（记进账本）和"即将离开视野"那条**辅助目标**
      （见 `rl/mem_aux.py`）**必须**用同一个口径。抄两遍的话"哪些算敌人 / 野人算不算"
      会**慢慢漂开**，而漂开的后果是辅助损失在教模型**错的东西**，**不报错**。
    ★ 口径（逐条与 `encode.window_armies` 对齐）：
      · 只算**国家**的军（野人不是"情报"，`window_armies` 也不给它们 token）；
      · `hp > 0`；
      · 落在 `mask`（视野）里。
    """
    if not name:
        return {}
    out = {}
    for a in world.armies:
        if a.get("owner") == name or a.get("owner") not in world.nations:
            continue
        if a.get("hp", 0) <= 0:
            continue
        if (a["x"], a["y"]) not in mask:
            continue
        out[a["gid"]] = {"x": int(a["x"]), "y": int(a["y"]),
                         "kind": a.get("type", "步"), "hp": int(a.get("hp", 0)),
                         "no": int(a.get("id", 0)), "owner": a["owner"]}
    return out


class WarMemory:
    """一局之内、**逐国**的敌军账本：`{我方: {gid: 最后看见时的样子}}`。"""

    def __init__(self, max_age: int = DEFAULT_MAX_AGE, cap: int = DEFAULT_CAP):
        self.max_age = int(max_age)
        self.cap = int(cap)
        self._by: dict[str, dict] = {}

    # ---------------------------------------------------------------- 记
    def observe(self, world, name: str | None, mask, turn: int,
                visible: bool = True) -> int:
        """把 `name` **此刻看得见**的敌军并进账本（返回更新条数）。

        ★ **只并看得见的**（`mask`）—— 记忆是"**我见过**"，不是"全图"。
        ★ 看见就**覆盖**（位置/血量会变，这正是要追的）；看不见**一个字都不动**。
        ★ **野人不记**（`owner not in world.nations`）—— 军队 token 也不给它们
          （见 `encode.window_armies`），两边保持一致。
        """
        if not name:
            return 0
        got = self._by.setdefault(name, {})
        # ★★ 先办"**在我眼皮底下被歼灭**"的：最后看见它的那一格此刻仍在视野里，
        #   而它已不在世上 ⇒ 我确实看着它没了 ⇒ **永久移除**（用户原话）。
        #   ★ 判据里"仍在视野"这一条是关键 —— 剔掉它就变成"扫全账本"，
        #     模型会在视野外白得一条情报（见文件头的 ③）。
        alive = {a["gid"] for a in world.armies}
        for gid in [g for g, r in got.items()
                    if (r["x"], r["y"]) in mask and g not in alive]:
            del got[gid]
        # ★ 口径走 `visible_foes`（**唯一出处**，见它的 docstring）—— 别在这里再抄一遍。
        seen = visible_foes(world, name, mask)
        for gid, rec in seen.items():
            got[gid] = {"turn": int(turn), **rec}   # ★ 看见就**覆盖**（位置/血量会变）
        return len(seen)

    # ---------------------------------------------------------------- 读
    def known(self, name: str | None, turn: int,
              visible_gids=frozenset()) -> list[dict]:
        """记忆里**此刻不可见**的敌军（附 `age`），按**最后看见的回合**降序取前 `cap` 条。

        ★ `visible_gids`：调用方把**这一帧看得见**的 gid 传进来 ——
          那些已经在军队 token 里了，**不要重复发**（重复会让同一条信息占两行注意力）。
        ★ 过期的（`age > max_age`）**不返回**（也别删 —— 删了就没法在它再露头时
          认出"这是很久以前那支"了；而且按文件头 ②，删除这个动作本身要小心）。
        """
        if not name:
            return []
        out = []
        for gid, rec in (self._by.get(name) or {}).items():
            if gid in visible_gids:
                continue
            age = int(turn) - int(rec["turn"])
            # ★ `age == 0`（本帧刚记下的）**不返回** —— 那些军就在**军队 token** 里；
            #   `visible_gids` 再排一道（双保险）。`age < 0` = 调用方给错了 turn。
            #   ★★ 记一笔：**这个账本没有"外部注入"这条路**（2026-09-25 收回）——
            #     我一度加过 `tell()`（逐军注入位置+番号），而引擎的间谍明说
            #     「**位置/血量/番号不外泄**」（`mp.World._econ_snapshot`）
            #     ⇒ 那条路**正是引擎禁止间谍给的东西**。
            #     合法的外部情报只有两种：**市政厅**（`hall_memory.tell`）与
            #     **各兵种数量**（`rl/intel.py`）。
            #     ⇒ **位置与番号只能来自"自己看见"** = 本账本唯一的写路径 `observe`。
            if age <= 0 or age > self.max_age:
                continue
            out.append({**rec, "gid": gid, "age": age})
        out.sort(key=lambda r: (r["age"], r["gid"]))     # 新的在前
        return out[:self.cap]

    def cell_ages(self, name: str | None, turn: int) -> dict:
        """每格**最后一次看见有敌军**距今几回合（**没记过的不在表里**）。

        ★ 与 `known()` 分开：那是"**按番号**"的读法（发给 token），
          这是"**按格**"的读法（填网格两列）。**同一次观测，两种读法**。
        """
        out: dict = {}
        for r in (self._by.get(name) or {}).values():
            age = int(turn) - int(r["turn"])
            if age > self.max_age:
                continue
            c = (r["x"], r["y"])
            if c not in out or age < out[c]:
                out[c] = age
        return out

    def __len__(self) -> int:
        return sum(len(v) for v in self._by.values())

    # ---------------------------------------------------------------- 复制
    def clone(self) -> "WarMemory":
        m = WarMemory(self.max_age, self.cap)
        m._by = {k: {g: dict(r) for g, r in v.items()} for k, v in self._by.items()}
        return m