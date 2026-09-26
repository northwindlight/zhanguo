# -*- coding: utf-8 -*-
"""**只读评级**（Glicko-2）的守卫 —— 用户 2026-09-26 问「知道 elo 吗」之后加的。

要它解的问题（都是裸胜率 + "打满 10 局"解不了的）：
  · 裸胜率**有混淆**：一份的胜率取决于它**抽到谁**；
  · "打满 10 局"在池子长大后**几乎凑不齐**（期望要 ≈1.7N 个 iter）。
Glicko-2 每局更新一次、把对手强度折进去，还带 **RD**（样本少 ⇒ RD 大 ⇒ 一眼看出别信）。

★ 每条守卫对着一个**会静默错**的形状：

  ① **赢家必须涨、输家必须跌** —— 分数符号写反了，评级表**照样有数、照样排序**，
     只是把弱的排在强的前面（"工具骗人"那一类）。
  ② **RD 必须随对局数下降** —— RD 不降就失去了"样本少别信"的作用，
     而判据（退役/筛选）又回到被噪声驱动。
  ③ **同一时段内部的顺序不许影响结果** —— 一局一局地"就地更新"（拿刚改过的评级
     去算下一个人）会引入**顺序依赖**，而两种写法给出的表**都"看起来合理"**。
  ④ ★★ **跨臂不比**：两个池子各自算 ⇒ 两张表**不可比**（评级只在连通分量内有意义）。
     把跨臂对局并进来（`--extra`）之后才可比 —— 而"并进"这件事**必须真的改变结论**
     （下面用"A 臂全胜 B 臂"的场景验：连图后 A 的均值必须高于 B，不连图则两边都在 1500 附近）。
  ⑤ **一局的分组键**：`game` 列（每局一个 id）在就必须用它；老行退回 `(iter,worker,ts)`
     时，**行数 < 2 的那桶要弃用**（一行看不出这是几人局 ⇒ 猜出来的输入是静默错的）。

★ 都故意破坏过（见各条 docstring）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import elo as E                                     # noqa: E402


def _g(period, mids, winners):
    return E.Game(period=period, mids=tuple(mids), winners=tuple(winners))


class TestDirectionAndUncertainty(unittest.TestCase):
    """① 赢家涨、输家跌；② RD 随对局数下降。"""

    def test_winner_rises_loser_falls(self):
        """★ 破坏方式：把 `s = 1.0 / 0.0` 两行对调（分数符号反了）⇒ 这条红。"""
        games = [_g(i, ("强", "弱"), ("强",)) for i in range(20)]
        res = E.rate(games)
        self.assertGreater(res["强"][0], E.INIT_R, "总赢的没涨过初始值")
        self.assertLess(res["弱"][0], E.INIT_R, "总输的没跌破初始值")
        self.assertGreater(res["强"][0], res["弱"][0], "赢的评级没高过输的")

    def test_rd_shrinks_with_games(self):
        few = E.rate([_g(0, ("甲", "乙"), ("甲",))])
        many = E.rate([_g(i, ("甲", "乙"), ("甲",)) for i in range(30)])
        self.assertLess(many["甲"][1], few["甲"][1], "对局多了 RD 没降")
        self.assertLess(many["甲"][1], E.INIT_RD, "RD 没跌破初始值")
        self.assertGreaterEqual(many["甲"][1], E.MIN_RD, "RD 跌破了下限")

    def test_symmetric_records_give_equal_ratings(self):
        """★★ 反向对照：**成绩完全对称的两份必须同分** —— 防"评级在乱跳"。

        两个总输给同一个赢家的成员，彼此之间没有任何区分信息 ⇒ 必须**恰好相等**
        （我第一版自检里写 `中 > 弱` ⇒ 那是我写错了，不是实现错了）。
        """
        games = [_g(i, ("强", "中", "弱"), ("强",)) for i in range(20)]
        res = E.rate(games)
        self.assertAlmostEqual(res["中"][0], res["弱"][0], places=6,
                               msg="成绩对称的两份评级不同 ⇒ 更新里混进了座位/顺序信息")


class TestMultiPlayerDecomposition(unittest.TestCase):
    """多人局拆成两两比较：赢家赢过所有人；输家之间是 0.5。"""

    def test_three_player_game_is_not_harsher_than_a_duel(self):
        """★ 一局 3 人时，赢家涨的幅度**不该**是 1v1 的 2 倍（拆解的作用）。

        （不做拆解、直接"赢家 +K"的写法会让 3 人局过度更新 ⇒ 这条会红。）
        """
        duel = E.rate([_g(0, ("甲", "乙"), ("甲",))])
        trio = E.rate([_g(0, ("甲", "乙", "丙"), ("甲",))])
        d = duel["甲"][0] - E.INIT_R
        t = trio["甲"][0] - E.INIT_R
        self.assertGreater(d, 0, "1v1 赢了没涨")
        self.assertGreater(t, 0, "3 人局赢了没涨")
        self.assertLess(t, d * 1.6,
                        f"3 人局的涨幅 {t:.0f} 远大于 1v1 的 {d:.0f} ⇒ 没有做两两拆解")

    def test_alliance_win_is_a_draw_among_winners(self):
        """两个赢家之间是 0.5（各涨一点、且**同分**）—— 联盟胜利时这样才不偏。"""
        games = [_g(i, ("甲", "乙", "丙"), ("甲", "乙")) for i in range(10)]
        res = E.rate(games)
        self.assertAlmostEqual(res["甲"][0], res["乙"][0], places=6,
                               msg="同为赢家却不同分 ⇒ 赢家之间的比较口径不一致")
        self.assertGreater(res["甲"][0], res["丙"][0], "赢家没高过输家")


class TestOrderIndependenceWithinAPeriod(unittest.TestCase):
    """③ 同一时段**内部**的顺序不许影响结果。"""

    def test_shuffling_within_one_period_is_equivalent(self):
        a = [_g("P", ("甲", "乙", "丙"), ("甲",)),
             _g("P", ("乙", "丙", "丁"), ("乙",))]
        b = list(reversed(a))
        ra = E.rate(a, period_key=lambda g: g.period)
        rb = E.rate(b, period_key=lambda g: g.period)
        for m in ("甲", "乙", "丙", "丁"):
            self.assertAlmostEqual(ra[m][0], rb[m][0], places=6,
                                   msg=f"{m} 的评级取决于同时段内的顺序 ⇒ "
                                       f"更新时拿的是「刚改过的」对手评级（就地更新）")

    def test_separate_periods_are_allowed_to_differ(self):
        """★ 反向对照：**跨时段**顺序**应当**有影响（Glicko 本来就按时间推）——
        别把它当成等价性检验（写了会误伤）。"""
        a = [_g("P1", ("甲", "乙"), ("甲",)), _g("P2", ("甲", "乙"), ("乙",))]
        b = list(reversed(a))
        ra = E.rate(a, period_key=lambda g: g.period)
        rb = E.rate(b, period_key=lambda g: g.period)
        self.assertNotAlmostEqual(ra["甲"][0], rb["甲"][0], places=3,
                                  msg="跨时段顺序居然没有影响 ⇒ 时段没生效（全并成一段了？）")


class TestCrossArmNeedsAConnectedGraph(unittest.TestCase):
    """④ ★★ 跨臂可比性 —— A/B 判据的关键。"""

    @staticmethod
    def _db(path: str, arm: str) -> None:
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, mid TEXT NOT NULL,
                won INTEGER NOT NULL, iter INTEGER, worker TEXT, ts TEXT NOT NULL,
                game TEXT);
        """)
        rows = []
        for i in range(12):                      # 臂内：A0/A1 互打（B 同理）
            rows += [(f"{arm}0", 1 if i % 2 == 0 else 0, i, "w", "t", f"{i}"),
                     (f"{arm}1", 0 if i % 2 == 0 else 1, i, "w", "t", f"{i}")]
        con.executemany("INSERT INTO results(mid,won,iter,worker,ts,game) "
                        "VALUES(?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def _run(self, d: str, extra: str | None):
        da, db_ = str(Path(d) / "a.db"), str(Path(d) / "b.db")
        self._db(da, "A")
        self._db(db_, "B")
        ga = E.games_from_db(da) + E.games_from_db(db_)
        if extra:
            ga += E.games_from_jsonl(extra)
        return E.rate(ga)

    def test_without_cross_games_the_two_arms_are_not_comparable(self):
        with tempfile.TemporaryDirectory() as d:
            res = self._run(d, None)
            for m in ("A0", "B0"):
                self.assertAlmostEqual(res[m][0], E.INIT_R, delta=60,
                                       msg=f"{m} 没在初始值附近 ⇒ 臂内自对弈不该造出强弱")

    def test_with_cross_games_the_two_arms_become_comparable(self):
        """A 臂全胜 B 臂 ⇒ 连图之后**两臂的均值必须分开**（这就是 A/B 要的那个数）。"""
        with tempfile.TemporaryDirectory() as d:
            extra = str(Path(d) / "x.jsonl")
            with open(extra, "w", encoding="utf-8") as fh:
                for i in range(24):
                    fh.write(json.dumps({"i": i, "mids": {"甲": "A0", "乙": "B0"},
                                         "winner": "A0"}) + "\n")
            res = self._run(d, extra)
            self.assertGreater(res["A0"][0], res["B0"][0] + 100,
                               f"A 臂全胜却没拉开：A0={res['A0'][0]:.0f} B0={res['B0'][0]:.0f}")


class TestGroupingKeys(unittest.TestCase):
    """⑤ 一局的分组键。"""

    def test_legacy_rows_without_game_need_at_least_two_rows(self):
        """老行（没有 `game` 列）里"只有一行"的桶要**弃用** —— 一行看不出几人局。"""
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "l.db")
            con = sqlite3.connect(p)
            con.executescript("""
                CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mid TEXT NOT NULL, won INTEGER NOT NULL, iter INTEGER,
                    worker TEXT, ts TEXT NOT NULL, game TEXT);
            """)
            con.executemany("INSERT INTO results(mid,won,iter,worker,ts,game) "
                            "VALUES(?,?,?,?,?,?)",
                            [("甲", 1, 1, "w", "00:00", None),          # 孤行 ⇒ 弃
                             ("乙", 1, 2, "w", "00:01", None),
                             ("丙", 0, 2, "w", "00:01", None)])         # 成对 ⇒ 用
            con.commit()
            con.close()
            gs = E.games_from_db(p)
            self.assertEqual(len(gs), 1, f"桶数不对：{[(g.mids, g.winners) for g in gs]}")
            self.assertEqual(set(gs[0].mids), {"乙", "丙"})
            self.assertEqual(gs[0].winners, ("乙",))

    def test_new_rows_use_the_game_column(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "l.db")
            con = sqlite3.connect(p)
            con.executescript("""
                CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mid TEXT NOT NULL, won INTEGER NOT NULL, iter INTEGER,
                    worker TEXT, ts TEXT NOT NULL, game TEXT);
            """)
            # ★ 同一秒、同一 iter 的**两局**（老口径会并成 6 行 ⇒ 静默错）
            rows = []
            for g in ("1:0", "1:1"):
                for m, w in (("甲", 1), ("乙", 0), ("丙", 0)):
                    rows.append((m, w, 1, "w", "00:00", g))
            con.executemany("INSERT INTO results(mid,won,iter,worker,ts,game) "
                            "VALUES(?,?,?,?,?,?)", rows)
            con.commit()
            con.close()
            gs = E.games_from_db(p)
            self.assertEqual(len(gs), 2, f"同一秒的两局没分开：{len(gs)} 局")
            self.assertTrue(all(len(g.mids) == 3 for g in gs))


if __name__ == "__main__":
    unittest.main()