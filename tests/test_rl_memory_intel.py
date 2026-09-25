# -*- coding: utf-8 -*-
"""**情报注入**与**记忆归属**的守卫 —— 用户 2026-09-25 的三句话：

    「对了视野内的地块归属有记忆吗，不过没有也没关系，模型可能自己学会如何记忆」
    「厅是厅的记忆，但是归属处理了吗」  →「**厅的归属会变的**」
    「**任何记忆都允许合法外部修改，或者有办法传入新的，这是配合情报的设计**」

★ 钉六件事：

  ① **归属只在看得见时写**（栅格）。原来是无条件写的，而网格是**视野的外接矩形**
     ⇒ 框内看不见的格子带**真值归属**（实测每帧泄漏 10%~16% 的框内格）。
     这与用户让我整批删掉的 `foe_*` 是**同一类**：「有没有超越真玩家的内容」。
  ② ★★ **厅的归属按"我认知里的主人"**：看得见用真值，**看不见用记忆里最后看见的**。
     原来两个消费端都拿当前真值 ⇒ 一座记得的厅只要**在我看不见时易主**，
     就当场从记忆里消失（实测 `foe_hall_cells(乙)` 变 `[]`）——
     「发现厅就永久标记」被无声推翻，而势函数跟着跳（正是要禁的闪断）。
     ★ 反方向同样要钉：看不见的易主**不许被学到**（那是白得情报）。
  ③ **注入是单调合并**：注进去之后，"看不见"的观测**一个字都不动**它；
     而**看得见**仍然**覆盖**（真视野 > 情报）。
  ④ **注入是分国的**：给甲的情报**不许**出现在乙的观测里。
  ⑤ ★★ **口子只在沙盒层**：`encode` 里不许出现 `tell(` —— 观测只**读**记忆。
     写记忆只有两条合法路径：① "看见就覆盖"（`observe`）；② 显式的 `tell`（情报）。
     ★ 把引擎真值**自动**倒进记忆 = 偷看，这条静态守卫就是拦它的。
  ⑥ **敌军情报没有番号就不收**（对不上号的记忆等于没有）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import encode as E                            # noqa: E402
from rl import scoring as S                           # noqa: E402
from rl import evaluate as EV                         # noqa: E402
from rl import vocab as V                             # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402

NC = len(V.OWNER_CHANNELS)


def _sb(size=12, n=3, seed=4, halls_known=False):
    return Sandbox(seed=seed, size=size, n_nations=n,
                   halls_known=halls_known).reset()


def _intel(glob_row) -> dict:
    """观测里那 12 个情报列（`{列名: 值}`）。"""
    return {k: float(v) for k, v in zip(V.GLOB, glob_row) if k.startswith("intel")}


def _an_enemy_hall(sb, me):
    return next(c for c, t in sorted(sb.world.tiles.items())
                if t["owner"] != me and t["buildings"].get("市政厅", 0) > 0)


class TestOwnershipIsOnlyWrittenWhenVisible(unittest.TestCase):
    """① 栅格归属：看不见的格子**一格都不许带归属**。"""

    def test_invisible_cells_carry_no_owner(self):
        leak = []
        for seed in range(12):
            for size, n in ((8, 2), (12, 3), (16, 3)):
                sb = _sb(size, n, seed, halls_known=True)
                for me in sb.players:
                    mask = E._vision(sb.world, me)
                    g = E.encode_grid(sb, me, mask)
                    x0, y0, h, w = E.frame_of(sb, me, mask)
                    for i in range(h):
                        for j in range(w):
                            x, y = x0 + j, y0 + i
                            if not (0 <= x < sb.size and 0 <= y < sb.size):
                                continue
                            if (x, y) in mask:
                                continue
                            if g[V.GRID_OWNER0:V.GRID_OWNER0 + NC, i, j].sum() > 0:
                                leak.append((seed, size, (x, y)))
        self.assertEqual(leak, [],
                         f"框内看不见却有归属的格子 {len(leak)} 个（前几个 {leak[:5]}）"
                         " ⇒ 不侦察就知道那块地是谁的（超越真玩家）")

    def test_own_territory_is_never_lost(self):
        """★ 反向对照：只写可见**不会**丢掉自家疆域（自家地本来就在视野里）。"""
        for seed in range(12):
            sb = _sb(12, 3, seed)
            for me in sb.players:
                mask = E._vision(sb.world, me)
                g = E.encode_grid(sb, me, mask)
                x0, y0, h, w = E.frame_of(sb, me, mask)
                mine = {(x, y) for (x, y), t in sb.world.tiles.items()
                        if t["owner"] == me}
                self.assertTrue(mine <= mask, "自家地块竟然不在视野里")
                n_self = 0
                for i in range(h):
                    for j in range(w):
                        x, y = x0 + j, y0 + i
                        if (x, y) in mine:
                            n_self += int(g[V.GRID_OWNER0 + V.OWN_SELF, i, j])
                self.assertEqual(n_self, len(mine), "自家疆域在观测里丢了")


class TestHallOwnershipFollowsMemoryWhenUnseen(unittest.TestCase):
    """② 「厅的归属会变的」—— 看不见时用**记忆里的主人**。"""

    def test_hall_of_remembered_owner_is_not_lost(self):
        sb = _sb()
        me, other, third = sb.players[0], sb.players[1], sb.players[2]
        cell = _an_enemy_hall(sb, me)
        hx, hy = E._home_cell(sb, me)
        sb.known_halls(me, frozenset({cell}))            # ① 我看见它（乙的厅）
        tiny = frozenset({(hx, hy)})                     # ② 之后一直看不见那格
        sb.world.tiles[cell]["owner"] = third            # ★ 我看不见时它易主给丙
        known = sb.known_halls(me, tiny)
        self.assertEqual(known.get(cell), other, "记忆不许因为一次没看见的易主就改口")
        self.assertIn(cell, EV.foe_hall_cells(sb.world, [other], tiny, known),
                      "★ 记忆里明明有乙的厅，消费端却把它丢了 ⇒「永久标记」被无声推翻")
        self.assertIn(cell, E._hall_cells_of(sb.world, other, tiny, known),
                      "★ 观测侧与打分器侧必须同口径（原来两处各写了一遍谓词）")

    def test_an_unseen_conquest_is_not_learned(self):
        """★ 反方向：**我看不见的易主不许被学到**（那是白得情报）。"""
        sb = _sb()
        me, other, third = sb.players[0], sb.players[1], sb.players[2]
        cell = _an_enemy_hall(sb, me)
        hx, hy = E._home_cell(sb, me)
        sb.known_halls(me, frozenset({cell}))
        tiny = frozenset({(hx, hy)})
        sb.world.tiles[cell]["owner"] = third
        known = sb.known_halls(me, tiny)
        self.assertEqual(EV.foe_hall_cells(sb.world, [third], tiny, known), [],
                         "★ 没看见的易主却让丙'得到'了一座厅 ⇒ 白得情报")

    def test_visible_truth_wins_over_memory(self):
        """★ 看得见时以**真值**为准（记忆只是"看不见时的替身"，不是否决权）。"""
        sb = _sb()
        me, other, third = sb.players[0], sb.players[1], sb.players[2]
        cell = _an_enemy_hall(sb, me)
        sb.known_halls(me, frozenset({cell}))
        sb.world.tiles[cell]["owner"] = third
        seen = frozenset({cell})                         # 我**看得见**这格
        known = sb.known_halls(me, seen)
        self.assertEqual(known.get(cell), third, "看得见就该刷新记忆")
        self.assertIn(cell, EV.foe_hall_cells(sb.world, [third], seen, known))


class TestIntelInjection(unittest.TestCase):
    """③④⑤⑥ 情报注入。"""

    def test_injection_is_monotone_then_vision_overwrites(self):
        """③ 注进去就留着（"看不见"擦不掉它）；**看得见仍然覆盖**。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        cell = _an_enemy_hall(sb, me)
        hx, hy = E._home_cell(sb, me)
        self.assertEqual(sb.tell_halls(me, {cell: other}), 1, "注入没写进去")
        tiny = frozenset({(hx, hy)})
        for _ in range(3):                               # 反复"看不见"地观测
            sb.known_halls(me, tiny)
        self.assertEqual(sb.halls.known(sb.world, me).get(cell), other,
                         "注入的情报被后续的视野观测擦掉了（那就不叫记忆）")
        sb.known_halls(me, frozenset({cell}))            # ★ 看得见 ⇒ 覆盖
        self.assertEqual(sb.halls.known(sb.world, me).get(cell),
                         sb.world.tiles[cell]["owner"], "看得见时真值必须覆盖情报")

    def test_injection_is_per_nation(self):
        """④ 给甲的情报不许出现在乙的观测里。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        cell = _an_enemy_hall(sb, me)
        sb.tell_halls(me, {cell: other})
        self.assertIn(cell, sb.halls.known(sb.world, me))
        self.assertNotIn(cell, sb.halls.known(sb.world, other),
                         "★ 给甲的情报漏到了乙的记忆里")

    def test_army_intel_lands_in_the_right_class(self):
        """★ 军情按**六类归属**落到对应那一档（对手/盟友/中立各一份）。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        self.assertTrue(sb.world.war_between(me, other), "这一对应当是敌对（缺省全对宣战）")
        sb.tell_armies(me, {other: {"步": 3, "骑": 1, "民": 4}}, turn=sb.turn)
        v = _intel(E.encode_glob(sb, me))
        self.assertGreater(v["intel_rival_步"], 0.0, "军情没进「对手」那一档")
        self.assertEqual(v["intel_ally_步"], 0.0, "注到对手身上的军情漏进了盟友档")

    def test_army_intel_carries_its_own_age(self):
        """★ 情报带"是什么时候的" ⇒ `age` 必须随回合涨（间谍 3 回合才回报）。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        sb.turn = 2                                   # ★ 情报只能来自"当前或过去"
        sb.tell_armies(me, {other: {"步": 3}}, turn=2)
        self.assertEqual(_intel(E.encode_glob(sb, me))["intel_rival_age"], 0.0)
        sb.turn = 12
        self.assertGreater(_intel(E.encode_glob(sb, me))["intel_rival_age"], 0.0,
                           "情报不记陈旧度 ⇒ 教模型相信幽灵")

    def test_intel_has_no_positions_or_serial_numbers(self):
        """★★ **口径守卫**：情报里**只有数量** —— 引擎明说
        「位置/血量/番号不外泄」，而用户要的是"**对应引擎玩家的间谍内容**"。

        ★ 顺带钉住"那条被删掉的路不许回来"：逐军注入位置+番号（`tell_enemies`）
          正是引擎禁止间谍给的东西。
        """
        sb = _sb()
        self.assertFalse(hasattr(sb, "tell_enemies"),
                         "★ `tell_enemies`（注入位置+番号）又回来了 —— "
                         "引擎明说间谍**不给**位置/血量/番号")
        from rl.war_memory import WarMemory
        self.assertFalse(hasattr(WarMemory, "tell"),
                         "★ `WarMemory.tell` 又回来了（番号账本只能从「自己看见」写）")
        # ★★ 混进位置/番号 ⇒ **当场报错**（不是静默忽略 —— 留一扇门比留个洞更糟）
        me, other = sb.players[0], sb.players[1]
        for extra in ({"x": 9}, {"gid": "g9"}, {"no": 77}, {"hp": 100}):
            with self.assertRaises(ValueError,
                                   msg=f"情报塞了 {extra} 却没报错 ⇒ 偷看通道留了门"):
                sb.tell_armies(me, {other: {"步": 3, **extra}})
        # ★ 合法的数量照收，且观测里**只有** 12 个情报列
        sb.tell_armies(me, {other: {"步": 3, "骑": 1}})
        v = _intel(E.encode_glob(sb, me))
        self.assertGreater(v["intel_rival_步"], 0.0, "数量该进去")
        self.assertEqual(len(v), 12, f"情报列不止 12 个了：{sorted(v)}")

    def test_multiple_sources_take_the_newest_deterministically(self):
        """★ 同类多来源 ⇒ 取**最新**；**同回合按国名定序** ⇒ 确定性。"""
        sb = _sb()
        me, a, b = sb.players[0], sb.players[1], sb.players[2]
        sb.turn = 3
        sb.tell_armies(me, {a: {"步": 1}, b: {"步": 7}}, turn=3)
        self.assertAlmostEqual(_intel(E.encode_glob(sb, me))["intel_rival_步"],
                               min(1.0, 7 / S.INTEL_ARMY_SCALE), places=6,
                               msg="同回合应取国名靠前的那份（确定性）")
        sb.turn = 9
        sb.tell_armies(me, {a: {"步": 5}}, turn=9)      # a 的更新
        self.assertAlmostEqual(_intel(E.encode_glob(sb, me))["intel_rival_步"],
                               min(1.0, 5 / S.INTEL_ARMY_SCALE), places=6,
                               msg="后到的那份不算「最新」")

    def test_intel_from_the_future_is_refused_loudly(self):
        """★★ 未来回合的情报 ⇒ **当场报错**（静默丢弃是本线最忌讳的形状）。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        with self.assertRaises(ValueError, msg="未来回合的情报被静默收下了"):
            sb.tell_armies(me, {other: {"步": 3}}, turn=5)

    def test_no_intel_means_all_zeros(self):
        sb = _sb()
        v = _intel(E.encode_glob(sb, sb.players[0]))
        self.assertTrue(all(x == 0.0 for x in v.values()),
                        f"还没收到情报却不是全 0：{ {k: x for k, x in v.items() if x} }")

    def test_intel_is_per_nation(self):
        """★ 给甲的情报不许出现在乙的观测里（同 `tell_halls`）。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        sb.tell_armies(me, {other: {"步": 6}})
        self.assertEqual(_intel(E.encode_glob(sb, other))["intel_rival_步"], 0.0,
                         "给甲的军情漏进了乙的观测")

    def test_intel_survives_reset_being_fresh(self):
        """★ 每局重开情报账本（上一局的战报对新一局是纯噪声）。"""
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        sb.tell_armies(me, {other: {"步": 6}})
        self.assertGreater(len(sb.intel), 0)
        sb2 = sb.reset()
        self.assertEqual(len(sb2.intel), 0, "新一局带着上一局的情报开局")

    def test_intel_clone_is_independent(self):
        sb = _sb()
        me, other = sb.players[0], sb.players[1]
        sb.tell_armies(me, {other: {"步": 6}})
        c = sb.clone()
        c.intel._by.clear()
        self.assertGreater(len(sb.intel), 0, "副本改情报改到了本体")

    def test_encode_never_writes_memory(self):
        """⑤ ★★ **静态守卫**：`rl/` 里除 `sandbox.py` 外不许**调用** `tell*`。

        ★ 用 **AST** 而不是 grep 行文本：我第一版就是按行找 `.tell(`，
          结果被 **docstring 里的一句散文**（"见 `sandbox.tell_halls`"）判红 ——
          那种守卫会逼着人**改注释**来迁就测试，而真正的调用照样能溜过去
          （换个换行、起个别名就躲开了）。AST 只认**真的调用**。
        """
        import ast
        root = Path(__file__).resolve().parent.parent / "rl"
        bad = []
        for f in sorted(root.glob("*.py")):
            if f.name == "sandbox.py":          # ★ 唯一允许的口子
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (
                    fn.id if isinstance(fn, ast.Name) else "")
                if name == "tell" or name.startswith("tell_"):
                    bad.append(f"{f.name}:{node.lineno} 调了 {name}()")
        self.assertEqual(bad, [],
                         f"★ 这些地方在**写记忆**：{bad}\n"
                         "  观测只**读**记忆。写记忆只有两条合法路径："
                         "① `observe`（看见就覆盖）；② 沙盒层的 `tell_*`（情报）。"
                         "把引擎真值自动倒进来 = 偷看 ⇒ 口子只开在 `sandbox.py`。")

    def test_the_ast_guard_actually_fires(self):
        """★ 闸门自检：造一份"真的调了 `tell`"的源码，守卫必须响。

        ★ 规矩是**每个自动闸门都要故意破坏一次确认它会响** ——
          AST 守卫也一样：它得抓得住**真调用**（上一版按行 grep 连散文都抓）。
        """
        import ast
        src = "def f(sb, me):\n    sb.tell_halls(me, {(1, 1): '乙'})\n"
        hits = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else ""
                if name == "tell" or name.startswith("tell_"):
                    hits.append(name)
        self.assertEqual(hits, ["tell_halls"], "AST 守卫抓不住真调用 ⇒ 它是假绿")


if __name__ == "__main__":
    unittest.main()