# -*- coding: utf-8 -*-
"""★★ **可写观测层**：外部告知的情报 —— 用户 2026-09-25 的四句话：

    「任何记忆都允许合法外部修改，或者有办法传入新的，**这是配合情报的设计**」
    「情报内容**对应引擎玩家的间谍内容**」
    「但是模型**只管市政厅**就行」
    「间谍顺便会告诉**军队总量**，这个也是一个**可写观测层**」

★★ 为什么必须有这一层
────────────────────
这台模型最终要跟 **LLM 玩家**配合。而真玩家的情报来源**远不止自己那点视野** ——
间谍、盟友、战报、自己的推理。**那些必须能进观测**，否则模型看到的永远是
"只有它自己眼睛看到的世界"，而真玩家不是那样玩的。

★★ 口径**照抄引擎**（`mp.World._econ_snapshot` 的注释，逐字）：
      「粗略军情：**只有各兵种数量 —— 位置/血量/番号不外泄**
        （间谍能探到敌国在扩军，但别想精准侦察）」
   ⇒ 这一层只接**两种**内容：
       ① **市政厅**（哪一格、归谁）—— 位置 + 归属，见 `hall_memory.HallMemory.tell`；
       ② **各兵种数量**（`{国: {"步": n, "骑": n, "民": n}}`）—— **本条**。
     ★ 引擎的间谍还给了国库/收入/外交/地块建设/整张地图，但用户划了范围
       （「模型只管市政厅就行」+「军队总量」）⇒ **其余一律不接**。
     ★★ 引擎明说**位置/血量/番号不外泄** ⇒ 我一度写的
       「逐军注入位置+番号」(`tell_enemies`) **正是引擎禁止间谍给的东西** ——
       已删。**位置/番号只能来自自己看见**（那才是 `war_memory.WarMemory` 的活）。

★★ 三条纪律（与另两套记忆同源）：

  ① **带时间戳**（`turn` = 这份情报**是什么时候的**）。间谍 3 回合才回报，
     拿到手时它已经旧了 ⇒ `age` 必须进观测。拿掉 age 就等于教模型**相信幽灵**。
  ② ★★ **合法性由调用方负责，且这是唯一的要求**。注进来的必须是
     "**这个玩家合法拥有的情报**"。把引擎真值（`world.armies` 的数量）
     **自动**倒进来 = 偷看 ⇒ 所以口子**只开在沙盒层**（`sandbox.tell_armies`），
     `encode` 里**不许**调（有静态守卫钉着）。
  ③ ★ **按"六类归属"归并成定长**（盟友/对手/中立各一份）——
     国家数**不许**进观测形状（本线铁律）。多个来源国报了同一类 ⇒
     取**最新的那一份**（同回合则按国名定序，保证**确定性**）。
"""
from __future__ import annotations

# ★ 这一层归并出来的三类（"自己"不用探："我自己的兵力我全知道"）。
#   ★ 与 `vocab.OWNER_CHANNELS` 的六类**同源**，这里只取"别人的"那三类。
INTEL_CLASSES = ("ally", "rival", "neutral")


class Intel:
    """一局之内、**逐国**的"外部告知"账本：`{我方: {来源国: {turn, 兵种数量}}}`。"""

    def __init__(self):
        self._by: dict[str, dict] = {}

    # ---------------------------------------------------------------- 写（唯一入口）
    def tell_armies(self, name: str, items: dict, turn: int) -> int:
        """★★ **外部告知某国的军情** `{来源国: {"步": n, "骑": n, "民": n}}`。

        `turn` = 这份情报**是什么时候的**（间谍有在途时间 ⇒ 常常比现在旧）。
        返回写入条数。

        ★ 后到的**覆盖**先到的（情报是"最新的一份"）—— 与"看见就覆盖"同一条纪律。
        ★★ **不许自动倒引擎真值**：调用方负责合法性（见文件头的 ②）。
        """
        from . import vocab as V
        got = self._by.setdefault(name, {})
        n = 0
        for src, kinds in dict(items).items():
            if not isinstance(kinds, dict):
                raise ValueError(
                    f"情报的来源国 {src!r} 给的不是 `{{兵种: 数量}}`（拿到 {type(kinds).__name__}）")
            # ★★ **当场校验**：只接**引擎口径里那几个兵种**，别的键一律报错。
            #   ★ 为什么不留"静默忽略"：`{**kinds, "x":9, "gid":"g9"}` 这种
            #     混着位置/番号进来**不报错**的话，等于给"偷看通道"留了一扇门 ——
            #     改天有人发现"多塞的键好像没被用"就会放心地塞更多。
            #   ★ 引擎明说间谍「**位置/血量/番号不外泄**」⇒ 这里就是那句话的牙齿。
            bad = [k for k in kinds if k not in V.UNIT]
            if bad:
                raise ValueError(
                    f"情报只接**各兵种数量**（{V.UNIT}），不接受 {bad} —— "
                    f"引擎的间谍明说「位置/血量/番号不外泄」（`mp.World._econ_snapshot`）")
            try:
                cnt = {k: int(v) for k, v in kinds.items()}
            except (TypeError, ValueError) as e:
                raise ValueError(f"情报的数量必须能取整：{kinds!r}（{e}）") from None
            got[src] = {"turn": int(turn), "kinds": cnt}
            n += 1
        return n

    # ---------------------------------------------------------------- 读
    def armies_of(self, name: str | None, turn: int) -> dict:
        """`{来源国: {"turn", "kinds", "age"}}` —— 读的时候顺手把 `age` 算出来。"""
        if not name:
            return {}
        out = {}
        for src, rec in (self._by.get(name) or {}).items():
            age = int(turn) - int(rec["turn"])
            if age < 0:
                continue                      # 时间倒流 ⇒ 调用方给错了 turn，丢掉
            out[src] = {**rec, "age": age}
        return out

    def __len__(self) -> int:
        return sum(len(v) for v in self._by.values())

    # ---------------------------------------------------------------- 复制
    def clone(self) -> "Intel":
        m = Intel()
        m._by = {k: {s: {"turn": r["turn"], "kinds": dict(r["kinds"])}
                     for s, r in v.items()} for k, v in self._by.items()}
        return m