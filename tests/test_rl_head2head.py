# -*- coding: utf-8 -*-
"""**跨臂对打**的守卫 —— 2026-09-26 加的判据工具（`rl/head2head.py`）。

★ 为什么要有它：`rl/eval_fixed.py` 只取**一个** `--ckpt`、按座位轮发同一个池子
  ⇒ 那是**自对弈**，回答不了"记忆臂和基线臂哪个强"。而"谁更强"正是这条线唯一的判据
  （用户：「判据只看**采样臂**」、「最终**只看胜场**」）。

★ 每一条守卫都对着一个**会把座位偏差算成实力差**的形状（比"代码写错"隐蔽得多）：

  ① **交替配比**：3 国局里甲占 2 座时，甲只要**任一**国赢就算甲赢 ⇒ 对称局面下
     甲胜率天然 ≈ 2/3。不交替 ⇒ 这个数会稳定地把某方判强，而**没有任何东西报错**。
  ② **同侧座位不重样**：一局里同一份网扮两个国家 = 自己打自己。
  ③ **甲坐哪几个座位随种子变** + **先手轮换** ⇒ 甲不能总占先手位。
  ④ ★★ **公平性长跑**：注入一个**已知偏差**（"先手必赢"）跑几百局纯逻辑模拟，
     甲侧胜率必须仍 ≈ 50% —— 只要座位/先手有一处系统性偏向某侧，这条当场红。
     **这是本文件最重要的一条**，也是唯一真的能抓住偏差的那条。

★ 为什么公平性走**纯逻辑**而不是真打：真打一局十几秒（实测 24 局 494 秒）
  ⇒ 只能跑几十局 ⇒ 噪声 ±15% ⇒ 那条闸门**根本响不起来**（假绿）。
  抽成 `game_setup` / `side_of` 之后几百局是毫秒级，而且能注入偏差确认它会响。
  ⇒ 真打只留一条**接线**测试（跑得起来、输出有胜负行），不拿它判公平。

★ 故意破坏过：把 `game_setup` 里的先手改成"总在甲的座位上"⇒ ④ 当场红。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rl import head2head as H                              # noqa: E402
from rl import vocab as V                                  # noqa: E402


class TestSeatSplitIsFair(unittest.TestCase):
    """①② 座位怎么分。"""

    def test_alternates_so_the_seat_advantage_cancels(self):
        for k in (3, 4, 5, 6):
            got = [H.seats_for_a(k, i) for i in range(20)]
            self.assertEqual(sum(got), 10 * k,
                             f"k={k}：20 局甲总共只占 {sum(got)} 座，平均不是 k/2 "
                             f"⇒ 座位偏差**不会被抵消**")
            want = {k // 2, k - k // 2}
            self.assertEqual(set(got), want, f"k={k}：配比集合是 {set(got)}，应当是 {want}")
            self.assertTrue(all(1 <= x <= k - 1 for x in got),
                            f"k={k}：某方 [1,k-1] 之外 ⇒ 有一方没座位或全占")

    def test_even_k_needs_no_alternation(self):
        """k 为偶数时两边恒等（k=2 ⇒ 永远 1:1）—— 别把它当成"没交替"报错。"""
        for k in (2, 4, 6):
            self.assertEqual({H.seats_for_a(k, i) for i in range(20)}, {k // 2})

    def test_both_sides_always_get_seats(self):
        for k in (2, 3, 4, 5, 6):
            for i in range(12):
                a, b = H.assign(list(V.PLAYER_NAMES[:k]), i, k, 1000)
                self.assertTrue(a, f"k={k} 第 {i} 局甲没有座位")
                self.assertTrue(b, f"k={k} 第 {i} 局乙没有座位")
                self.assertEqual(len(a) + len(b), k, "座位数对不上")

    def test_seats_are_reproducible_and_vary(self):
        """③ 可复现（同种子同座位）+ 不总坐同一批座位。"""
        k, names = 3, list(V.PLAYER_NAMES[:3])
        seen = Counter()
        for i in range(18):
            a1, _ = H.assign(names, i, k, 1000)
            a2, _ = H.assign(names, i, k, 1000)
            self.assertEqual(a1, a2, f"第 {i} 局座位不可复现")
            seen[frozenset(a1)] += 1
        self.assertGreaterEqual(len(seen), k,
                                f"18 局只出现过 {len(seen)} 种座位组合 ⇒ 甲总坐同一个位子")

    def test_same_side_never_reuses_a_net(self):
        """② 同侧不重样（一局里同一份网扮两国 = 自己打自己）。"""
        from rl.model import build_model
        k, names = 4, list(V.PLAYER_NAMES[:4])
        pool_a = {i: build_model() for i in range(2)}     # 故意少于座位数
        pool_b = {i: build_model() for i in range(3)}
        for i in range(6):
            a_side, _ = H.assign(names, i, k, 1000)
            got = H.nets_for(names, a_side, pool_a, pool_b, i, 1000)
            self.assertEqual(set(got), set(names), "有座位没发到网")
            na = len([n for n in names if n in a_side])
            nb = k - na
            self.assertEqual(len({id(got[n]) for n in names if n in a_side}), na,
                             "甲侧有重复的网")
            self.assertEqual(len({id(got[n]) for n in names if n not in a_side}), nb,
                             "乙侧有重复的网")


class TestFairnessUnderAKnownBias(unittest.TestCase):
    """④ ★★ **公平性长跑**（本文件最重要的一条，也是唯一真能抓住偏差的）。

    做法：注入一个**已知偏差** —— 假设"**先手必赢**"（这是沙盒里真实存在的
    一种结构优势，用户为此专门定过 `first_streak_limit` 那道闸）—— 然后跑几百局，
    只用品 `甲侧胜率`。**甲侧的座位与先手无关** ⇒ 这个数必须是 ≈50%。

    ★ 它抓的是什么：甲若总坐先手位（或总坐"高编号"座位），这个数会稳定偏离 50%
      —— 那等于把**座位优势**算成**实力差**，而报告上只会写"甲更强"。
    """

    def _rate(self, k: int, n: int, bias) -> float:
        nations = list(V.PLAYER_NAMES[:k])
        aw = dec = 0
        for i in range(n):
            a_side, first = H.game_setup(nations, i, 1000)
            won = bias(a_side, first, nations, i)
            side = H.side_of(won, a_side)
            if side != "平":
                dec += 1
                aw += (side == "A")
        self.assertGreater(dec, n * 0.8, "注入的偏差没产生足够多的胜负局（模拟写错了？）")
        return aw / dec

    def test_first_player_always_wins_is_still_fifty_fifty(self):
        r = self._rate(3, 600, lambda a, first, ns, i: {first})
        self.assertGreater(r, 0.40, f"「先手必赢」下甲侧胜率 {r:.0%} ⇒ 甲偏向先手位")
        self.assertLess(r, 0.60, f"「先手必赢」下甲侧胜率 {r:.0%} ⇒ 甲偏向先手位")

    def test_lowest_index_always_wins_is_still_fifty_fifty(self):
        """★ 换一种偏差：**编号最小的国必赢**（座位抽签若有偏，这条会响）。"""
        r = self._rate(4, 600, lambda a, first, ns, i: {ns[0]})
        self.assertGreater(r, 0.40, f"「最小号必赢」下甲侧胜率 {r:.0%}")
        self.assertLess(r, 0.60, f"「最小号必赢」下甲侧胜率 {r:.0%}")

    def test_the_gate_can_actually_fire(self):
        """★★ **确认这条闸门会响**（用户铁律：每个闸门都要故意破坏一次）。

        破坏方式：把胜者钉成"**甲侧的第一个座位**" ⇒ 甲必胜 ⇒ 上面两条的判据
        （0.40~0.60）会当场红。这里用同一个 `_rate` 复现"会响"的那一刻。
        """
        r = self._rate(3, 200, lambda a, first, ns, i: {sorted(a)[0]})
        self.assertGreater(r, 0.95, "破坏版居然不是甲必胜 ⇒ 这个自检本身写错了")


class TestEndToEndWiring(unittest.TestCase):
    """真打的**接线**（不判公平 —— 那由上面那条纯逻辑长跑负责）。"""

    def test_runs_and_prints(self):
        import tempfile
        from pathlib import Path as _P
        from rl import train as T
        from rl.model import build_model
        with tempfile.TemporaryDirectory() as d:
            p = str(_P(d) / "a.pt")
            T._save_ckpt(p, {i: build_model() for i in range(3)}, 1,
                         meta=T._ckpt_meta(8, 8, True, 3, 3, 20, mem_slots=0))
            r = subprocess.run(
                [sys.executable, "-m", "rl.head2head", "--a", p, "--b", p,
                 "--seeds", "2", "--size", "8", "--t-max", "6", "--pool", "3"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, f"跨臂对打跑挂了：{r.stderr[-600:]}")
            for key in ("甲胜", "座位：", "ent/logK", "覆盖范围"):
                self.assertIn(key, r.stdout, f"输出里缺 `{key}`：{r.stdout[-300:]}")


if __name__ == "__main__":
    unittest.main()