# -*- coding: utf-8 -*-
"""`rl/combat_probs.py` 守卫：概率必须是**引擎的概率**，不是长得像的概率。

`combat_probs` 是 `World._resolve_battles` 的**复刻**（把骰子从"取期望"改成
"枚举 6^n 组合 + 记忆化 DP"，好给出**概率**）。复刻得对不对**不能靠读代码自信**
—— 自己重写一遍伤害公式（v10 就是重写的）迟早会与引擎漂开，而"漂开"这件事
没有任何别的测试抓得到。所以这里的预言机是**引擎本体**：

    手工摆好一格战斗 → **反复直接调 `world._resolve_battles()`**
    （它一轮 = 一个回合的战斗结算，且不含治疗/经济 ⇒ 正好把战斗隔离出来）

钉五件事：

  1. **结局分布对拍** —— P(赢)/P(输)/P(同归于尽) 与引擎实测一致（含减伤、多兵种）。
  2. **轮数分布对拍** —— 用户 2026-09-24：「还要报告**多少概率打几个回合**」；
     逐档比对，且期望轮数也要对上。
  3. **撤退保命对拍** —— 撤退军留在战场吃本轮伤害（防御方 `cover=50`、进攻方全额），
     且自己输出 −80%。
  4. **减伤档真的进算式** —— 同一份伤害，`cover=50` 必须只吃一半。防的是
     "读了 cover 却没乘"这种静默失效。
  5. **按角色分增援** —— 攻方走 `_reachable(for_attack=True)`、守方走 `_reachable()`，
     两者**不是一回事**（用户：「进攻方的增援是 atk，防御方的增援是 mv」）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import combat_probs as CP                     # noqa: E402
from rl import scoring as S                          # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402

TRIALS = 2000


def stage(seed=3, size=14, owner="乙", sides=None, castle=0, terrain="丘陵",
          retreat=None, engaged=("甲",)):
    """摆一格战斗：`sides = {"甲": [兵种,…], "乙": […]}`。

    `retreat = (势力, 下标, cover)` —— 给那支军挂 `retreat_to`/`retreat_cover`。
    """
    sb = Sandbox(seed=seed, size=size).reset()
    w = sb.world
    cell = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == owner)
    x, y = cell
    w.tiles[cell]["terrain"] = terrain
    w.tiles[cell]["buildings"]["城堡"] = castle
    w.armies.clear()
    for name, kinds in (sides or {}).items():
        for i, k in enumerate(kinds):
            gid, seq = w._new_army(name)
            a = {"id": seq + i, "gid": gid, "name": f"{name}{seq}x{i}", "type": k,
                 "hp": 100 if k != "民" else 80, "x": x, "y": y, "owner": name,
                 "moved_turn": -1, "engaged": name in engaged}
            if retreat and retreat[0] == name and retreat[1] == i:
                a["retreat_to"] = [x, y]
                a["retreat_cover"] = retreat[2]
            w.armies.append(a)
    return w, cell


def _snapshot_armies(w):
    return [(a["owner"], a["type"], a["hp"], a.get("retreat_to"),
             a.get("retreat_cover"), a["engaged"]) for a in w.armies]


def run_engine(w, cell, trials=TRIALS, rounds=True):
    """★ 预言机：每局从**同一初始态**重打，用真引擎结算到定局。"""
    x, y = cell
    base = _snapshot_armies(w)
    tb = {c: (dict(t["buildings"]), t["owner"], t["core"]) for c, t in w.tiles.items()}
    occ, hist = Counter(), Counter()
    for _ in range(trials):
        w.armies.clear()
        for owner, k, hp, rt, rc, eng in base:
            gid, seq = w._new_army(owner)
            a = {"id": seq, "gid": gid, "name": f"{owner}{seq}", "type": k, "hp": hp,
                 "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": eng}
            if rt:
                a["retreat_to"] = list(rt)
                a["retreat_cover"] = rc
            w.armies.append(a)
        n = 0
        while n < 60:
            if not [a for a in w.armies if a.get("engaged") and a["owner"] != "野人"]:
                break
            w._resolve_battles()
            n += 1
        occ[frozenset(a["owner"] for a in w.armies if a["hp"] > 0)] += 1
        hist[n] += 1
        for c, (b, o, co) in tb.items():          # `_conquer` 会改归属，复原
            w.tiles[c]["buildings"] = dict(b)
            w.tiles[c]["owner"] = o
            w.tiles[c]["core"] = co
    return occ, hist, trials


class TestOddsVsEngine(unittest.TestCase):
    """① 结局分布 + ② 轮数分布，逐案与引擎对拍。"""

    CASES = [
        ("2v1 步", dict(sides={"甲": ["步", "步"], "乙": ["步"]})),
        ("2v2 步", dict(sides={"甲": ["步", "步"], "乙": ["步", "步"]})),
        ("3v2 步 + L3城", dict(sides={"甲": ["步"] * 3, "乙": ["步"] * 2}, castle=3)),
        ("2步 vs 3民", dict(sides={"甲": ["步", "步"], "乙": ["民"] * 3})),
        ("2步1骑 vs 2步1民", dict(sides={"甲": ["步", "步", "骑"],
                                       "乙": ["步", "步", "民"]})),
    ]

    def test_outcome_and_rounds(self):
        for label, kw in self.CASES:
            with self.subTest(label):
                w, cell = stage(**kw)
                b = CP.build(w, *cell)
                self.assertIsNotNone(b, f"{label}: build 返回 None")
                o = CP.assess(b)
                occ, hist, n = run_engine(w, cell)

                # ---- ① 结局分布（每个势力分别比）----
                for F in b.order:
                    en = set(b.enemies[F])
                    pw = sum(v for s, v in occ.items() if F in s and not (en & s)) / n
                    pl = sum(v for s, v in occ.items() if F not in s and (en & s)) / n
                    self.assertAlmostEqual(pw, o.p_win[F], delta=0.05,
                                           msg=f"{label} {F} 赢率")
                    self.assertAlmostEqual(pl, o.p_lose[F], delta=0.05,
                                           msg=f"{label} {F} 输率")
                self.assertAlmostEqual(occ.get(frozenset(), 0) / n, o.p_draw,
                                       delta=0.05, msg=f"{label} 同归于尽率")

                # ---- ② 轮数分布（用户：「还要报告多少概率打几个回合」）----
                for k in range(1, 7):
                    self.assertAlmostEqual(hist.get(k, 0) / n, o.p_rounds[k],
                                           delta=0.05,
                                           msg=f"{label} P(打 {k} 轮)")
                e_eng = sum(k * v for k, v in hist.items()) / n
                self.assertAlmostEqual(e_eng, o.e_rounds, delta=0.18,
                                       msg=f"{label} 期望轮数")
                # 轮数分布必须真是一条分布（不是把质量丢了）
                self.assertAlmostEqual(sum(o.p_rounds), 1.0, delta=1e-6,
                                       msg=f"{label} 轮数分布未归一")
                self.assertEqual(o.truncated, 0.0, f"{label} 有未收敛质量")

    def test_rounds_bins_monotone(self):
        """`round_bins` 是累积 ⇒ 必须单调不减、末档为 1（喂网络前的形状自检）。"""
        w, cell = stage(**self.CASES[1][1])          # 2v2 步
        b = CP.build(w, *cell)
        o = CP.assess(b)
        bins = o.round_bins()
        self.assertEqual(bins, sorted(bins), f"分档不单调：{bins}")
        self.assertAlmostEqual(bins[-1], 1.0, delta=1e-6, msg="末档必须是 1")
        # ★ 这个局面**一轮打不完**：一轮最多 甲100攻 × 骰6(×1.25) = 125，被 25% 减伤
        #   打到 93，再摊到 2 支 = 46/支 < 100hp ⇒ 无人阵亡。所以 ≤1 档**就该是 0**。
        #   （我第一版在这里断言了 `> 0`，是**我把引擎算错了**，不是代码错。）
        self.assertEqual(o.p_rounds[1], 0.0, "2v2 步一轮不该分胜负")
        self.assertGreater(bins[1], 0.3, "≤2 档该有实质质量（实测 41.7%）")
        self.assertAlmostEqual(bins[2], 1.0, delta=1e-6,
                               msg="该局面 3 轮内必胜负（引擎实测：2轮42% / 3轮58%）")


class TestRetreat(unittest.TestCase):
    """③ 撤退保命 vs 引擎；④ 减伤档必须真的进算式。"""

    def _one_round(self, w, cell, side, idx, trials=4000):
        """真引擎跑**一轮**，统计 `side` 第 `idx` 支活下来的比例。"""
        x, y = cell
        base = _snapshot_armies(w)
        tb = {c: (dict(t["buildings"]), t["owner"], t["core"]) for c, t in w.tiles.items()}
        alive = 0
        for _ in range(trials):
            w.armies.clear()
            for owner, k, hp, rt, rc, eng in base:
                gid, seq = w._new_army(owner)
                a = {"id": seq, "gid": gid, "name": f"{owner}{seq}", "type": k, "hp": hp,
                     "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": eng}
                if rt:
                    a["retreat_to"] = list(rt)
                    a["retreat_cover"] = rc
                w.armies.append(a)
            tid = [a["id"] for a in w.armies if a["owner"] == side][idx]
            w._resolve_battles()
            alive += bool([a for a in w.armies if a["id"] == tid and a["hp"] > 0])
            for c, (b, o, co) in tb.items():
                w.tiles[c]["buildings"] = dict(b)
                w.tiles[c]["owner"] = o
                w.tiles[c]["core"] = co
        return alive / trials

    def test_survival_matches_engine(self):
        """★ 局面必须选成"**真会死**"的：伤害被 `_spread` 摊平，一堆满血兵一轮打不死
        （不是 bug，是引擎事实）⇒ 只有己方快被打光时"撤退保命"才有非平凡答案。"""
        # 守方(cover=50) 2 支里撤 1：摊平后两支都只吃一半 ⇒ 必活
        w, cell = stage(sides={"甲": ["步"] * 6, "乙": ["步", "步"]},
                        retreat=("乙", 0, 50))
        a = [x for x in w.armies if x["owner"] == "乙"][0]
        self.assertAlmostEqual(CP.retreat_odds(w, *cell, a),
                               self._one_round(w, cell, "乙", 0), delta=0.04)
        # 守方只剩 1 支：≥3 点骰子必死 ⇒ P≈1/3，**有鉴别力**的一档
        w, cell = stage(sides={"甲": ["步"] * 6, "乙": ["步"]}, retreat=("乙", 0, 50))
        a = [x for x in w.armies if x["owner"] == "乙"][0]
        p = CP.retreat_odds(w, *cell, a)
        self.assertLess(p, 0.9, "1 支守军撤退不该必活 —— 这个案子失去鉴别力了")
        self.assertAlmostEqual(p, self._one_round(w, cell, "乙", 0), delta=0.04)
        # 进攻方撤退 ⇒ cover=100（吃全额）。若误用 50，这里会算成 ~1.0 而引擎是 0.0
        w, cell = stage(sides={"甲": ["步", "步"], "乙": ["步"] * 6},
                        retreat=("甲", 0, 100))
        a = [x for x in w.armies if x["owner"] == "甲"][0]
        p = CP.retreat_odds(w, *cell, a)
        self.assertAlmostEqual(p, 0.0, delta=0.05)
        self.assertAlmostEqual(p, self._one_round(w, cell, "甲", 0), delta=0.05)

    def test_cover_enters_the_formula(self):
        """④ 同一份伤害，cover=50 只吃一半 —— 防"读了 cover 却没乘"。"""
        w, cell = stage(sides={"甲": ["步"] * 6, "乙": ["步", "步"]})
        b = CP.build(w, *cell)
        i = b.order.index("乙")
        base_hp = [u[1] for u in b.init["乙"]]

        def mark(cov):
            """把 `乙` 那方的每支军都标成"撤退中、减伤 cov%"（绕开骰子只验算式）。"""
            st = [b.init[F] for F in b.order]
            st[i] = tuple((k, h, True, cov) for k, h, _r, _c in st[i])
            return tuple(st)

        dmg = tuple(140 for _ in b.order)
        hp50 = [u[1] for u in CP._apply(b, mark(50), dmg)[i]]
        hp100 = [u[1] for u in CP._apply(b, mark(100), dmg)[i]]
        per = 140 // 2                                       # 2 支 ⇒ 各 70
        self.assertEqual(hp50, [h - per * 50 // 100 for h in base_hp])
        self.assertEqual(hp100, [h - per for h in base_hp])
        self.assertNotEqual(hp50, hp100)


class TestReinforcements(unittest.TestCase):
    """⑤ 增援按**角色**分：攻方 atk / 守方 mv（用户：「有点区别」）。"""

    def test_attacker_uses_attack_reach(self):
        """守方的地，攻方够得着 ⇒ 由 `for_attack=True` 决定；两者**不是同一张可达图**。"""
        sb = Sandbox(seed=5, size=16).reset()
        w = sb.world
        cell = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == "乙")
        x, y = cell
        w.armies.clear()
        for name, eng in (("甲", True), ("乙", False)):
            gid, seq = w._new_army(name)
            w.armies.append({"id": seq, "gid": gid, "name": f"{name}{seq}", "type": "步",
                             "hp": 100, "x": x, "y": y, "owner": name,
                             "moved_turn": -1, "engaged": eng})
        # 旁边再放一支甲军（相邻格）：对**敌国领土**的终点，atk 可达而 mv 不可达
        nx, ny = next((a, b) for a, b in [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
                      if 0 <= a < w.size and 0 <= b < w.size)
        gid, seq = w._new_army("甲")
        w.armies.append({"id": seq, "gid": gid, "name": f"甲{seq}", "type": "步",
                         "hp": 100, "x": nx, "y": ny, "owner": "甲",
                         "moved_turn": -1, "engaged": False})
        mover = w.armies[-1]
        reach_mv = w._reachable("甲", mover, for_attack=False)
        reach_atk = w._reachable("甲", mover, for_attack=True)
        self.assertNotIn((x, y), reach_mv,
                         "敌国领土 mv 进不去（引擎口径）—— 那守方增援就不该算它")
        self.assertIn((x, y), reach_atk,
                      "敌国领土 atk 到得了 —— 攻方增援该算它")
        b = CP.build(w, *cell)
        got = CP.reachable_reinforcements(w, b, "甲")
        self.assertTrue(any(a["id"] == mover["id"] for a in got),
                        "攻方增援没用 atk 可达性")
        # 守方这边一支持不着（同一张图、守方走 mv）⇒ 空
        self.assertEqual([a["id"] for a in CP.reachable_reinforcements(w, b, "乙")], [])

    def test_reinforcement_shifts_the_odds(self):
        """★ 加增援必须**真的改变**概率；否则这特征是死的（防静默失效）。"""
        w, cell = stage(sides={"甲": ["步", "步"], "乙": ["步", "步"]})
        b = CP.build(w, *cell)
        base = CP.assess(b)
        b2 = CP.build(w, *cell, extra={"甲": [{"type": "步", "hp": 100, "owner": "甲",
                                               "engaged": True}]})
        with_r = CP.assess(b2)
        self.assertGreater(with_r.p_win["甲"], base.p_win["甲"] + 0.05,
                           f"加了一支攻方增援却没提高胜率：{base.p_win['甲']:.2f} → "
                           f"{with_r.p_win['甲']:.2f}")

    def test_snapshot_is_per_frame(self):
        """★ 用户：「**每个回合都要按照当前状态重算概率**」——
        同一个 `Battle` 对象在两帧 hp 不同时**不许**给同一答案。"""
        w, cell = stage(sides={"甲": ["步", "步"], "乙": ["步", "步"]})
        first = CP.snapshot(w)[cell]
        for a in w.armies:                    # 把一方打残 ⇒ 局面变了
            if a["owner"] == "乙":
                a["hp"] = 12
        second = CP.snapshot(w)[cell]
        self.assertNotAlmostEqual(first.p_win["甲"], second.p_win["甲"], delta=0.05,
                                  msg="局面变了概率没变 ⇒ 有跨帧缓存")


class TestPriorsAreLive(unittest.TestCase):
    """★ 尺度与上限都读 `rl/scoring.py`，且**改表立刻生效**（用户 2026-09-24：
    「搜索引擎是不是也是硬编码的，改成从 `balance` 抽」）。

    防的还是那个**不报错**的写法：`from .scoring import ROUND_BIN_EDGES` 会把值绑死在
    导入时 ⇒ 之后改表对推演毫无影响，而一切看起来正常。
    """

    def _battle(self):
        w, cell = stage(sides={"甲": ["步", "步", "步"], "乙": ["步", "步", "步"]})
        return CP.build(w, *cell)

    def test_round_bins_read_the_table(self):
        b = self._battle()
        o = CP.assess(b)
        self.assertEqual(len(o.round_bins()), len(S.ROUND_BIN_EDGES))
        with S.override(ROUND_BIN_EDGES=(2, 4)):
            self.assertEqual(len(o.round_bins()), 2, "★ 改表对分档没影响 ⇒ 值被绑死了")
            self.assertAlmostEqual(o.round_bins()[0], o.p_rounds[1] + o.p_rounds[2])

    def test_as_vec_scales_read_the_table(self):
        b = self._battle()
        o = CP.assess(b)
        v0 = o.as_vec("甲")[4]                       # 期望轮数那一项
        with S.override(PROB_ROUND_SCALE=S.PROB_ROUND_SCALE * 4):
            self.assertNotAlmostEqual(o.as_vec("甲")[4], v0, places=6,
                                      msg="★ 归一秒度改表没生效")

    def test_assess_limits_read_the_table(self):
        """上限被顶到时必须**看得见**（`truncated` 报出来），不许静默给个半截答案。"""
        b = self._battle()
        with S.override(ASSESS_MAX_ROUNDS=1):
            o = CP.assess(b)
        self.assertGreater(o.truncated, 0.0,
                           "★ 轮数上限压到 1 却没报未收敛 —— 静默截断了")
        self.assertEqual(CP.assess(b).truncated, 0.0, "默认上限下不该有未收敛质量")

    def test_report_and_as_vec_agree_on_edges(self):
        """`report` 的分档边界必须**来自** `round_bins`（原来两处各写一遍）。"""
        b = self._battle()
        o = CP.assess(b)
        with S.override(ROUND_BIN_EDGES=(1, 2)):
            self.assertIn("≤2:", o.report(b.order))


if __name__ == "__main__":
    unittest.main()