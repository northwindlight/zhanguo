# -*- coding: utf-8 -*-
"""v11 寻路守卫：视野掩码、多回合代价场、本回合落点。

这四件事各自钉一个**病**（都在 v10 上真发生过）：

  1. **视野掩码必须与引擎逐格等价** —— 它替掉的是逐格调 `visible_to`
     （那个函数答"看不见"时要**遍历全图 tiles** 找瞭望塔，逐格问就是 O(格数×地块数)）；
     等价性错了就是"看得见/看不见"错了，而那是整个信息集的地基。
  2. **迷雾格绝不读真地形** —— `cell_cost` 对视野外的格必须返回平原估价，
     读了 `tile_terrain` 就是开图偷看（v9 堵掉的四处越权之一）。
  3. **代价场认地形** —— 一道山墙两侧代价必须拉开（v10 的切比雪夫看不见地形，
     骑兵一头扎进森林就吃满移动力、卡在原地）。
  4. **落点只认"严格更近"** —— 这一条是防抖的根：场固定时每步严格下降，
     A→B→A 结构上不可能（v10 每回合按切比雪夫贪心挪一格，来回摆）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
from ruleai import pathfind  # noqa: E402
from game import building_effect  # noqa: E402


class _Base(unittest.TestCase):
    def _world(self, size: int = 16) -> mp.World:
        """秦在 (5,5)、楚远在 (12,12)，4..10 × 4..7 铺成自家平原走廊（照
        `tests/test_move_path.py::_Base` 的写法，避开"中立不可入境"混进来）。"""
        w = mp.World(size=size, seed=5, nations=["秦", "楚"],
                     starts={"秦": (5, 5), "楚": (12, 12)})
        w.armies = []
        for x in range(4, 11):
            for y in range(4, 8):
                t = w._new_tile(x, y, "秦")
                t["terrain"] = "平原"
                w.tiles[(x, y)] = t
        return w

    def _army(self, w, kind: str, x: int, y: int, owner: str = "秦", aid: int = 1) -> dict:
        a = {"id": aid, "gid": aid, "name": f"{owner}·{kind}军{aid}", "type": kind,
             "hp": 100, "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": False}
        w.armies.append(a)
        return a

    def _ter(self, w, x, y, terrain: str) -> None:
        if (x, y) not in w.tiles:
            t = w._new_tile(x, y, "秦")
            w.tiles[(x, y)] = t
        w.tiles[(x, y)]["terrain"] = terrain


class TestVisionMask(_Base):
    """① 视野掩码 == 引擎 `visible_to`（逐格等价），含瞭望塔与联盟两种边界。"""

    def test_mask_equals_engine_on_sample(self):
        w = self._world(size=24)
        mask = pathfind.vision_mask(w, "秦")
        for x in range(0, 24, 2):
            for y in range(0, 24, 2):
                self.assertEqual(w.visible_to("秦", x, y), (x, y) in mask,
                                 f"({x},{y}) 掩码与引擎不一致")

    def test_tower_circle_is_included(self):
        w = self._world(size=24)
        # 在自家走廊里造一座瞭望塔，塔半径外一格不该可见、半径内该可见
        r = building_effect("瞭望塔", "vision_radius")
        t = w.tiles[(9, 7)]
        t["buildings"]["瞭望塔"] = 1
        mask = pathfind.vision_mask(w, "秦")
        inside, outside = (9, 7 - r), (9, 7 - r - 1)
        self.assertTrue(w.visible_to("秦", *inside) and inside in mask, "塔半径内该可见")
        self.assertFalse(w.visible_to("秦", *outside) != (outside in mask), "掩码与引擎必须同判")
        self.assertEqual(w.visible_to("秦", *outside), outside in mask)

    def test_ally_tiles_extend_vision(self):
        w = self._world(size=24)
        # 给楚一块地（联盟前）：秦看不见它背后那一圈；结盟后看得见
        w._new_tile(16, 4, "楚") and w.tiles.setdefault((16, 4), w._new_tile(16, 4, "楚"))
        far = (17, 4)
        self.assertFalse(w.visible_to("秦", *far))
        w.blocs.append({"name": "试盟", "chief": "秦", "members": ["秦", "楚"], "turn": 0})
        self.assertTrue(w.visible_to("秦", *far), "联盟共享视野：盟友地块视同己方")
        self.assertIn(far, pathfind.vision_mask(w, "秦"))


class TestFogDiscipline(_Base):
    """② 视野外**绝不读真地形**：一律按平原估价。"""

    def test_unseen_cell_costs_plain(self):
        """找一格**真地形崎岖、但秦看不见**的格：代价必须仍是 1（平原估价）。

        ★ 这条测试的真意是"模块没去读 `tile_terrain`" —— 所以先证明那一格**真的**是
        崎岖（`tile_terrain` 说得出来），再证明 `cell_cost` 对它**不以为意**。
        """
        w = self._world(size=24)
        mask = pathfind.vision_mask(w, "秦")
        rough = None
        for x in range(24):
            for y in range(24):
                if (x, y) not in mask and w.tile_terrain(x, y) in ("森林", "山地"):
                    rough = (x, y)
                    break
            if rough:
                break
        self.assertIsNotNone(rough, "这张图上该有看不见的崎岖格")
        self.assertIn(w.tile_terrain(*rough), ("森林", "山地"))
        self.assertEqual(pathfind.cell_cost(w, "骑", rough, mask), 1,
                         "看不见的格必须按平原估（读了真地形就是偷看）")

    def test_unseen_cell_never_calls_tile_terrain(self):
        """更精确的一条：对迷雾格算代价时，`world.tile_terrain` **一次都不许被调用**。

        （`cell_cost` 只要"先读地形再决定要不要用"就已经是偷看了 —— 这里直接数调用次数。）
        """
        w = self._world(size=24)
        mask = pathfind.vision_mask(w, "秦")
        seen = []
        real = w.tile_terrain
        w.tile_terrain = lambda x, y: (seen.append((x, y)), real(x, y))[1]
        try:
            for x in range(24):
                for y in range(24):
                    if (x, y) not in mask:
                        self.assertEqual(pathfind.cell_cost(w, "骑", (x, y), mask), 1)
        finally:
            w.tile_terrain = real
        self.assertEqual(seen, [], f"算了迷雾格的代价却读了真地形：{seen[:5]}")

    def test_seen_cell_costs_true_terrain(self):
        w = self._world(size=24)
        self._ter(w, 6, 6, "森林")              # 自家走廊里 ⇒ 看得见
        mask = pathfind.vision_mask(w, "秦")
        self.assertIn((6, 6), mask)
        self.assertEqual(pathfind.cell_cost(w, "骑", (6, 6), mask), 2, "骑兵进森林要 2")
        self.assertEqual(pathfind.cell_cost(w, "步", (6, 6), mask), 1, "步兵进森林仍是 1")

    def test_no_resource_reader_left(self):
        """迷雾闸门（`known_tile`）已随"不估值"一起删掉：v11 侧**没有**读资源的地方了。

        删掉的是**功能**（估值），不是**纪律**：本模块一行资源都不读，
        所以也不需要一个"读也得走闸门"的入口。真要想再加估值回来，
        必须把闸门一起加回来 —— 这条测试就是那道提醒。
        """
        self.assertFalse(hasattr(pathfind, "known_tile"), "闸门已删；要加回估值请连它一起加")


class TestCostField(_Base):
    """③ 代价场认地形（山墙两侧拉开、骑兵比步兵贵）。"""

    def test_mountain_wall_raises_cost(self):
        w = self._world(size=24)
        for y in range(3, 12):                  # 竖一道山地墙
            self._ter(w, 9, y, "山地")
        mask = pathfind.vision_mask(w, "秦")
        fld = pathfind.cost_field(w, "秦", {(11, 5)}, "骑", mask, max_cost=12)
        # 墙没封死整张图（可以从两端绕）⇒ 墙前那格代价明显大于墙后（绕路）
        self.assertNotIn("_", fld)
        self.assertGreater(fld.get((8, 5), 10 ** 9), fld.get((10, 5), 10 ** 9),
                           "山墙拦在路上：墙前(8,5)比墙后(10,5)要贵（得绕）")

    def test_cavalry_pays_more_in_forest(self):
        """一道**贯穿全图**的林带：绕不过去 ⇒ 只能穿，骑兵必须比步兵贵。

        （★ 别只放一格森林：八邻可以斜着绕过去，两边代价就一样了——
          这是本用例第一版踩的坑。）
        """
        w = self._world(size=24)
        for y in range(24):
            self._ter(w, 9, y, "森林")
        mask = pathfind.vision_mask(w, "秦")
        goal = {(11, 5)}
        fld_r = pathfind.cost_field(w, "秦", goal, "骑", mask, max_cost=20)
        fld_s = pathfind.cost_field(w, "秦", goal, "步", mask, max_cost=20)
        self.assertGreater(fld_r.get((5, 5), 0), fld_s.get((5, 5), 0),
                           "穿过森林：骑兵比步兵贵（代价场必须认兵种）")

    def test_field_truncated_at_max_cost(self):
        w = self._world(size=24)
        mask = pathfind.vision_mask(w, "秦")
        fld = pathfind.cost_field(w, "秦", {(5, 5)}, "步", mask, max_cost=2)
        self.assertTrue(all(v <= 2 for v in fld.values()))
        self.assertNotIn((10, 7), fld)           # 超过上限 ⇒ 缺席（不是无穷大）

    def test_wall_blocks_foreign_territory(self):
        w = self._world(size=24)
        w._new_tile(8, 5, "楚")
        w.tiles[(8, 5)] = w._new_tile(8, 5, "楚")
        w.tiles[(8, 5)]["terrain"] = "平原"
        mask = pathfind.vision_mask(w, "秦")
        self.assertTrue(pathfind.is_wall(w, "秦", (8, 5), mask), "他国领土是墙")
        self.assertFalse(pathfind.is_wall(w, "秦", (12, 12), mask),
                         "看不见的格不当墙（乐观假设，撞上了下回合重规划）")


class TestBestStep(_Base):
    """④ 落点：从引擎合法集里挑、只认严格更近。"""

    def test_step_is_engine_legal_and_multi_tile(self):
        w = self._world(size=24)
        a = self._army(w, "骑", 5, 5)
        mask = pathfind.vision_mask(w, "秦")
        fld = pathfind.cost_field(w, "秦", {(5, 1)}, "骑", mask, max_cost=12)
        nxt = pathfind.best_step(w, "秦", a, fld)
        self.assertIsNotNone(nxt)
        self.assertIn(nxt, w._reachable("秦", a),
                      "落点必须来自引擎的合法集（否则撞墙烧额度/走不到）")
        self.assertLess(fld[nxt], fld[(5, 5)], "只往严格更近的格走")

    def test_no_step_when_already_closest(self):
        w = self._world(size=24)
        a = self._army(w, "骑", 5, 5)
        mask = pathfind.vision_mask(w, "秦")
        fld = pathfind.cost_field(w, "秦", {(5, 5)}, "骑", mask, max_cost=12)
        self.assertIsNone(pathfind.best_step(w, "秦", a, fld),
                          "已经在目标上 ⇒ 不动（不许原地打转）")

    def test_monotone_descent_no_oscillation(self):
        """连走 8 回合，代价必须**单调不增**，且不会回到走过的格（防抖的根）。"""
        for kind in ("骑", "步"):
            w = self._world(size=24)
            a = self._army(w, kind, 5, 7)
            mask = pathfind.vision_mask(w, "秦")
            goal = (5, 1)
            fld = pathfind.cost_field(w, "秦", {goal}, kind, mask, max_cost=12)
            seen = {(a["x"], a["y"])}
            prev = fld.get((a["x"], a["y"]), 10 ** 9)
            for _ in range(8):
                nxt = pathfind.best_step(w, "秦", a, fld)
                if nxt is None:
                    break
                self.assertLess(fld[nxt], prev, f"{kind}: 每一步都必须更近")
                self.assertNotIn(nxt, seen, f"{kind}: 走进了走过的格 ⇒ 抖动")
                seen.add(nxt)
                prev = fld[nxt]
                a["x"], a["y"] = nxt
                a["moved_turn"] = -1
                w.begin_turn()

    def test_marchable_uses_attack_reach(self):
        w = self._world(size=24)
        w._new_tile(7, 5, "楚")
        w.tiles[(7, 5)] = w._new_tile(7, 5, "楚")
        w.tiles[(7, 5)]["terrain"] = "平原"
        w.declare_war("秦", "楚")
        a = self._army(w, "骑", 5, 5)
        self.assertTrue(pathfind.marchable(w, "秦", a, (7, 5)), "平地 2 格 ⇒ 够得着")
        b = self._army(w, "步", 5, 6, aid=2)
        self.assertFalse(pathfind.marchable(w, "秦", b, (7, 5)), "步兵 1 格 ⇒ 够不着")


if __name__ == "__main__":
    unittest.main()