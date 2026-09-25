# -*- coding: utf-8 -*-
"""**番号账本**的守卫 —— 用户 2026-09-25：

    「总之模型要识别的出，**这次击退了 a 兵团，下次露头的是 a 军团的残余，
      还是一支没见过的、满编的 b 军团**」

★ 钉的六件事，每条都对着一个**会静默教错**的形状：

  ① **看得见才记**：记忆是"我见过"，不是全图。
  ② **看得见就覆盖**（位置/血量变了要跟上）—— 这正是"追 a 的残余"所依赖的。
  ③ **看不见一个字都不动**（不是删）。
  ④ ★★ **绝不用全图真值删条目**：敌军**在视野外死掉**时，若拿
     `world.armies` 里"它没了"去删，模型就**白得一条情报**（"它死了"），
     而真玩家**不该知道** ⇒ 这条专门钉它。**这是用户总口径
     「除威胁系统外不许读对手当前状态」在记忆上的直接推论。**
  ⑤ **必须过期**（`age > max_age` 不再返回）—— 不然等于教模型**相信幽灵**。
  ⑥ **野人不记**（与 `encode.window_armies` 保持一致）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox                       # noqa: E402
from rl.war_memory import WarMemory                  # noqa: E402

FULL = None          # 全知掩码在下面按需构造


def _sb(size=12, n=3, seed=3):
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=150,
                   halls_known=True).reset()


def _all_cells(sb):
    return frozenset(sb.world.tiles)


def _enemy(sb, me):
    return [a for a in sb.world.armies
            if a["owner"] != me and a["owner"] in sb.world.nations and a["hp"] > 0]


class TestWarMemory(unittest.TestCase):
    def test_only_visible_are_recorded(self):
        """① 只记看得见的。

        ★ 我第一版写成"1 格掩码只该记 1 支"—— **错了**：多支军可以**叠在同一格**
          （开局几支都堆在敌国核心）⇒ 那是个假的不变量。
          真正的不变量是：**记下的每一条都落在掩码里**。
        """
        sb = _sb()
        me = sb.players[0]
        one = next(iter(_enemy(sb, me)))
        cell = (one["x"], one["y"])
        wm = WarMemory()
        wm.observe(sb.world, me, frozenset({cell}), 0)
        recs = wm.known(me, 1, frozenset())
        self.assertTrue(recs, "掩码里的敌军一条都没记下")
        self.assertTrue(all((r["x"], r["y"]) == cell for r in recs),
                        "记下了掩码之外的军 ⇒ 偷看了")
        # ★ 反向对照：空掩码 ⇒ 一条都不该记
        wm2 = WarMemory()
        wm2.observe(sb.world, me, frozenset(), 0)
        self.assertEqual(len(wm2), 0, "空掩码也记下了东西")

    def test_visible_overwrites_position_and_hp(self):
        """② 看见就覆盖（位置/血量）—— "追 a 的残余"靠的就是这个。"""
        sb = _sb()
        me = sb.players[0]
        a = next(iter(_enemy(sb, me)))
        wm = WarMemory()
        wm.observe(sb.world, me, _all_cells(sb), 0)
        hp0 = wm.known(me, 1, frozenset())       # 全部当"不可见"⇒ 都返回
        rec0 = next(r for r in hp0 if r["gid"] == a["gid"])
        # 打掉它一半血、并挪一格 ⇒ 再看一次
        a["hp"] = a["hp"] // 2
        a["x"], a["y"] = (a["x"] + 1) % sb.size, a["y"]
        wm.observe(sb.world, me, _all_cells(sb), 5)
        rec1 = next(r for r in wm.known(me, 6, frozenset()) if r["gid"] == a["gid"])
        self.assertEqual(rec1["hp"], a["hp"], "血量没跟上 ⇒ 认不出'残余'")
        self.assertEqual((rec1["x"], rec1["y"]), (a["x"], a["y"]), "位置没跟上")
        self.assertEqual(rec1["turn"], 5)

    def test_not_visible_means_untouched(self):
        """③ 看不见 ⇒ 一个字都不动（不是删）。"""
        sb = _sb()
        me = sb.players[0]
        wm = WarMemory()
        wm.observe(sb.world, me, _all_cells(sb), 0)
        before = len(wm)
        wm.observe(sb.world, me, frozenset(), 1)     # 什么都看不见
        self.assertEqual(len(wm), before, "看不见反而把账本弄小了")

    def test_annihilated_in_sight_is_removed_permanently(self):
        """★★ 「**被明确歼灭的，就要永久移除**」（用户 2026-09-25）。

        我**盯着那一格**看，而它没了 ⇒ 我确实看着它被歼灭 ⇒ 永久移除。
        """
        sb = _sb()
        me = sb.players[0]
        victim = next(iter(_enemy(sb, me)))
        cell = (victim["x"], victim["y"])
        gid = victim["gid"]
        wm = WarMemory()
        wm.observe(sb.world, me, frozenset({cell}), 0)
        self.assertIn(gid, {r["gid"] for r in wm.known(me, 1, frozenset())})
        # 它在**我盯着的这一格上**被歼灭
        sb.world.armies = [a for a in sb.world.armies if a["gid"] != gid]
        wm.observe(sb.world, me, frozenset({cell}), 1)      # ★ 仍在盯着那格
        self.assertNotIn(gid, {r["gid"] for r in wm.known(me, 2, frozenset())},
                         "当场地看见它被歼灭，却没有永久移除")
        # ★ 永久：再过很多回合也不会回来
        self.assertNotIn(gid, {r["gid"] for r in wm.known(me, 99, frozenset())})

    def test_moved_away_is_not_mistaken_for_death(self):
        """★ 反向对照：**挪走的军还在世上** ⇒ 不许被当成"已歼灭"。"""
        sb = _sb()
        me = sb.players[0]
        victim = next(iter(_enemy(sb, me)))
        cell = (victim["x"], victim["y"])
        gid = victim["gid"]
        wm = WarMemory()
        wm.observe(sb.world, me, frozenset({cell}), 0)
        victim["x"], victim["y"] = (victim["x"] + 3) % sb.size, victim["y"]   # 挪走
        wm.observe(sb.world, me, frozenset({cell}), 1)      # 我还在看**老地方**
        self.assertIn(gid, {r["gid"] for r in wm.known(me, 2, frozenset())},
                      "★ 把'绕后的敌军'当成'已歼灭'删掉了")

    def test_death_out_of_sight_is_not_learned(self):
        """★★ ③ **视野外死掉的敌军，账本不许知道**（不然白得一条情报）。

        ⇒ 那一格**已不在视野**，就不许因为"它不在世上了"而删它。
        """
        sb = _sb()
        me = sb.players[0]
        victim = next(iter(_enemy(sb, me)))
        cell = (victim["x"], victim["y"])
        gid = victim["gid"]
        wm = WarMemory()
        wm.observe(sb.world, me, _all_cells(sb), 0)
        sb.world.armies = [a for a in sb.world.armies if a["gid"] != gid]
        # ★ 掩码**不含**它最后出现的那一格 ⇒ 我根本看不见那儿
        wm.observe(sb.world, me, frozenset(), 1)
        self.assertIn(gid, {r["gid"] for r in wm.known(me, 2, frozenset())},
                      "★ 视野外死掉却把它删了 ⇒ 模型白知道了'它死了'")

    def test_expires_by_age(self):
        """⑤ 必须过期（陈旧度是纪律，不是装饰）。"""
        sb = _sb()
        me = sb.players[0]
        wm = WarMemory(max_age=5)
        wm.observe(sb.world, me, _all_cells(sb), 0)
        gid = next(iter(_enemy(sb, me)))["gid"]
        self.assertIn(gid, {r["gid"] for r in wm.known(me, 5, frozenset())}, "刚好到期不该作废")
        self.assertNotIn(gid, {r["gid"] for r in wm.known(me, 6, frozenset())},
                         "超期了还返回 ⇒ 教模型相信幽灵")

    def test_visible_units_are_not_repeated(self):
        """★ 此刻看得见的**不要重复发**（同一条信息占两行注意力）。"""
        sb = _sb()
        me = sb.players[0]
        wm = WarMemory()
        wm.observe(sb.world, me, _all_cells(sb), 0)
        vis = {a["gid"] for a in _enemy(sb, me)}
        self.assertEqual(wm.known(me, 1, vis), [], "看得见的也被当成'幽灵'发了一遍")

    def test_barbarians_not_recorded(self):
        """⑥ 野人不记（与 `encode.window_armies` 一致）。"""
        sb = _sb()
        me = sb.players[0]
        wm = WarMemory()
        wm.observe(sb.world, me, _all_cells(sb), 0)
        owners = {r["owner"] for r in wm.known(me, 1, frozenset())}
        self.assertTrue(owners <= set(sb.world.nations),
                        f"账本里混进了非国家势力：{owners - set(sb.world.nations)}")

    def test_cap_is_respected(self):
        """★ 条数有上界（注意力是 O(N²)，不能无界）。"""
        sb = _sb()
        me = sb.players[0]
        wm = WarMemory(cap=3)
        wm.observe(sb.world, me, _all_cells(sb), 0)
        self.assertLessEqual(len(wm.known(me, 1, frozenset())), 3, "超过上界了")

    def test_clone_is_independent(self):
        sb = _sb()
        me = sb.players[0]
        wm = WarMemory()
        wm.observe(sb.world, me, _all_cells(sb), 0)
        c = wm.clone()
        c._by[me].clear()
        self.assertGreater(len(wm), 0, "clone 之后原账本被改到了")


if __name__ == "__main__":
    unittest.main()

class TestSandboxWiring(unittest.TestCase):
    """★★ **接线守卫** —— 2026-09-25 栽的那个坑，钉死它。

    我把番号账本写成 `self.war`，而 `Sandbox.war` **本来就是个 bool**
    （「开局是否宣战」）。更毒的是 `WarMemory.__len__`：空账本 `len()==0`
    ⇒ **假值**，于是 `reset()` 里的 `elif self.war:` 判空账本为假
    ⇒ **开局一个宣战都没宣** ⇒ 每格战斗一轮就散、`p_win` 两边都成 1.0。

    它**不报错、不抛异常**：整套战斗测试只是"分布对不上"，看起来像复刻漂移。
    ⇒ 这里钉三件事：**名字**（`war` 必须是 bool）、**行为**（宣战真的宣了）、
      **账本每局重开**。
    """

    def test_sandbox_war_flag_is_still_a_bool(self):
        """★ **名字守卫**：`Sandbox.war` 是配置开关，任何账本都不许抢这个名。

        ★ 这条正是当初会立刻响的那一声 —— 账本一写成 `self.war` 它就红。
        """
        sb = Sandbox(seed=3, size=12, n_nations=3, war=False)
        self.assertIsInstance(sb.war, bool,
                              "`Sandbox.war` 被别的东西（账本？）抢走了 ⇒ "
                              "`elif self.war:` 的语义被静默改写")
        self.assertIs(sb.war, False)
        sb2 = Sandbox(seed=3, size=12, n_nations=3).reset()
        self.assertIsInstance(sb2.war, bool, "`reset()` 之后 `war` 不再是 bool")

    def test_declaring_war_actually_declares_it(self):
        """★★ **行为守卫**：`war=True` ⇒ 三国**两两宣战**；`war=False` ⇒ 一对都不宣。

        ★ 空账本假值那次，这一条会直接红（三个 pair 全 False）——
          而"名字守卫"只抓命名，抓不到**同名的别的假值来源**，两条都要。
        """
        for want in (True, False):
            sb = Sandbox(seed=3, size=12, n_nations=3, war=want).reset()
            w, ps = sb.world, sb.players
            pairs = [(a, b) for i, a in enumerate(ps) for b in ps[i + 1:]]
            self.assertEqual(len(pairs), 3, "三国局应有 3 个 pair")
            for a, b in pairs:
                self.assertIs(w.war_between(a, b), want,
                              f"war={want} 但 {a}-{b} 的宣战状态是 {w.war_between(a, b)}")

    def test_fresh_sandbox_has_an_empty_ledger(self):
        """★ 账本每局重开：上一局的"敌军在某处"对新一局是**纯噪声**，且不会自己消失。"""
        sb = _sb()
        me = sb.players[0]
        sb.known_enemies(me, _all_cells(sb))
        self.assertGreater(len(sb.war_mem), 0, "看见敌军却没记账")
        sb2 = sb.reset()
        self.assertEqual(len(sb2.war_mem), 0,
                         "新一局带着上一局的幽灵开局（age 只在同一局时间轴上算，不会自愈）")

    def test_clone_carries_an_independent_ledger(self):
        """★ 试演副本要**带着**记忆走，但**不共享**（试演不能凭空多知道，也不能污染本体）。"""
        sb = _sb()
        me = sb.players[0]
        sb.known_enemies(me, _all_cells(sb))
        c = sb.clone()
        self.assertEqual(len(c.war_mem), len(sb.war_mem), "副本把记忆丢了")
        c.war_mem._by[me].clear()
        self.assertGreater(len(sb.war_mem), 0, "副本改记忆改到了本体 ⇒ 试演会污染真身")

    def test_known_enemies_needs_no_extra_work_from_the_caller(self):
        """★ 与 `known_halls` 同款 lazy latch：不给 mask / armies 也能自己算。"""
        sb = _sb()
        me = sb.players[0]
        a = sb.known_enemies(me)                      # 不给 mask、不给 armies
        b = sb.known_enemies(me, _all_cells(sb))      # 全知掩码 ⇒ 全在 token 里 ⇒ 无幽灵
        self.assertIsInstance(a, list)
        self.assertEqual(b, [], "全知时看得见的还被当成'幽灵'发了一遍（重复占注意力）")
