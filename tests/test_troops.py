# -*- coding: utf-8 -*-
"""引擎派生表守卫：`troops`（非野人名单）与 `guardians`（野人按格索引）必须**逐条等价**。

这两张表是引擎里唯一的"派生缓存"（不进存档），靠一把钥匙失效：

    (id(self.armies), len(self.armies), sum(next_army_seq.values()))

换来的是"不再为了滤掉/找出野人而扫 1500 支军队" —— `_defs_at` 每回合被 v11 的
战斗评估问 ~10 次，一次全表扫就是 0.2ms（40x40 上占整局 16%）。
**用一次静默错（扫描少算了几支军队）换这个好处不划算**，所以这里不靠"读代码时小心"，
用朴素定义逐条对照钉住：

  1. 随机局（三国 + 强制开战，**每回合**抽查）两张表 == 朴素定义；
  2. `_defs_at` 与朴素实现**逐位相同**（含**顺序** —— 顺序会进 `combat` 的伤害分摊，
     差一位就可能换一个"打不打"的判定）；
  3. 失效判据跟得上：新建 / 阵亡 / **同回合又建又亡（长度回到原值）** / 整表重建 / 读档；
  4. 野人恒为 `armies` 的**前缀**（`_defs_at` 顺序等价的前提，见那里的注释）；
  5. `nation_armies("野人")` / `_army("野人", …)` 仍拿得到野人 ——
     "野人不是国家军队"不等于"野人不存在"。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import rule_ai  # noqa: E402


def _naive_troops(w) -> list:
    return [a for a in w.armies if a["owner"] != "野人"]


def _naive_guards(w) -> dict:
    return {(a["x"], a["y"]): a for a in w.armies if a["owner"] == "野人"}


def _naive_defs(w, name, x, y) -> list:
    """`_defs_at` 的朴素版（照抄改动前那份实现，一行不改）。"""
    owner = w.owned_by(x, y)
    out = []
    for a in w.armies:
        if (a["x"], a["y"]) != (x, y) or a["owner"] == name:
            continue
        if a["owner"] == "野人":
            if owner is None:
                out.append(a)
            continue
        if w.war_between(name, a["owner"]):
            out.append(a)
    return out


class _Base(unittest.TestCase):
    def _world(self, size: int = 16) -> mp.World:
        w = mp.World(size=size, seed=3, nations=["秦", "楚", "齐"], max_turns=40)
        for a in ("秦", "楚", "齐"):           # 开战：把"交战/驻军/夺地"那几条路径逼出来
            for b in ("秦", "楚", "齐"):
                if a < b:
                    w.declare_war(a, b)
        return w

    def _play(self, w, turns: int = 5) -> None:
        _, fn = rule_ai.resolve("v11")
        rng = random.Random(11)
        for _ in range(turns):
            for n in list(w.alive()):
                fn(w, n, rng, max_actions=12)
            w.resolve_turn()
            w.begin_turn()

    def _check_all(self, w, tag: str) -> None:
        self.assertEqual(w.troops, _naive_troops(w), f"{tag}：`troops` 与朴素名单不同")
        self.assertEqual(w.guardians, _naive_guards(w), f"{tag}：`guardians` 与朴素表不同")
        for name in w.alive():
            for x in range(w.size):
                for y in range(w.size):
                    self.assertEqual(
                        w._defs_at(name, x, y), _naive_defs(w, name, x, y),
                        f"{tag}：{name} 在 ({x},{y}) 的守军不同（顺序也算）")


class TestTablesMatchNaive(_Base):
    def test_every_turn(self):
        """一局里每回合抽查：两张表 + 全图全格的 `_defs_at` 都要与朴素定义逐位相同。"""
        w = self._world()
        for t in range(5):
            self._check_all(w, f"第 {t} 回合（打之前）")
            self._play(w, 1)
            self._check_all(w, f"第 {t} 回合（结算后）")
        self.assertGreater(len(w.armies), 100, "地图上应当还有野人守卫")

    def test_guardians_are_a_prefix_of_armies(self):
        """野人恒为 `armies` 的**前缀** —— `_defs_at` 靠这条才敢"先野人、后国家军队"。"""
        w = self._world()
        for t in range(5):
            idx = [i for i, a in enumerate(w.armies) if a["owner"] == "野人"]
            self.assertEqual(idx, list(range(len(idx))),
                             f"第 {t} 回合：野人不再是 `armies` 的前缀（下标 {idx[:8]}…）")
            self._play(w, 1)

    def test_guardian_lookup_still_works(self):
        """野人不是"国家军队"，但**仍在册**：`nation_armies("野人")` / `_army("野人", id)`。"""
        w = self._world()
        self._play(w, 2)
        guards = [a for a in w.armies if a["owner"] == "野人"]
        self.assertTrue(guards, "地图上应当还有野人守卫")
        self.assertEqual(len(w.nation_armies("野人")), len(guards),
                         "传「野人」时必须走全表 —— 否则野人凭空消失")
        g = guards[0]
        self.assertIs(w._army("野人", g["id"]), g)
        self.assertIs(w.guardians[(g["x"], g["y"])], g)


class TestInvalidation(_Base):
    def test_create_and_death(self):
        w = self._world()
        self._play(w, 2)
        before = len(w.troops)
        # ① 新建：走引擎的编号器（只有它会让 next_army_seq 前进）
        gid, seq = w._new_army("秦")
        w.armies.append({"id": seq, "gid": gid, "name": f"秦·步军{seq}", "type": "步",
                         "hp": 100, "x": 1, "y": 1, "owner": "秦",
                         "moved_turn": -1, "engaged": False})
        self.assertEqual(len(w.troops), before + 1, "新建之后 `troops` 没跟上")
        self._check_all(w, "新建后")
        # ② 阵亡：直接摘掉（引擎里阵亡就是这么摘的）
        w.armies.remove(next(a for a in w.armies if a["owner"] == "秦" and a["id"] == seq))
        self.assertEqual(len(w.troops), before, "阵亡之后 `troops` 没跟上")
        self._check_all(w, "阵亡后")

    def test_create_and_death_in_one_turn(self):
        """★ 这条才是判据的要害：**同回合又建又亡** ⇒ 长度回到原值，
        只有 `next_army_seq` 动了 —— 钥匙少算这一项，这里就会静默给出旧名单。"""
        w = self._world()
        self._play(w, 2)
        before_n = len(w.armies)
        gid, seq = w._new_army("秦")
        w.armies.append({"id": seq, "gid": gid, "name": f"秦·步军{seq}", "type": "步",
                         "hp": 100, "x": 1, "y": 1, "owner": "秦",
                         "moved_turn": -1, "engaged": False})
        dead = next(a for a in w.armies if a["owner"] == "野人")
        w.armies.remove(dead)                  # 长度回到原值
        self.assertEqual(len(w.armies), before_n)
        self.assertEqual(w.troops, _naive_troops(w), "又建又亡之后 `troops` 是陈旧的")
        self.assertNotIn(dead, w.guardians.values(), "又建又亡之后 `guardians` 里还有阵亡的野人")
        self._check_all(w, "又建又亡后")

    def test_rebuild_and_load(self):
        w = self._world()
        self._play(w, 2)
        w.armies = list(w.armies)              # 整表重建（引擎里 `_drop_guardians`/亡国这么干）
        self._check_all(w, "整表重建后")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
            self.assertEqual(w2.troops, _naive_troops(w2), "读档后 `troops` 不对")
            self.assertEqual(w2.guardians, _naive_guards(w2), "读档后 `guardians` 不对")
            for name in w2.alive():
                self.assertEqual(w2._defs_at(name, 1, 1), _naive_defs(w2, name, 1, 1))


if __name__ == "__main__":
    unittest.main()