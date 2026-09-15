# -*- coding: utf-8 -*-
"""移动路径化守卫（2026-09-15）：多格移动改成**逐格走**，地形开始影响机动。

旧行为：mv/atk 是"5×5 内任意格直接落子"，**中间格从不被检查** —— 骑兵可以穿过
山脉、穿过敌国领土跳到纵深，地形对机动零影响（`mp.py` 旧 `move`/`attack` 的
切比雪夫距离判定）。现在：

| 兵种 | 移动力 | 进 平原/沙漠/丘陵 | 进 森林/山地 |
|---|---|---|---|
| 步兵 / 民兵 | 1 | 1（⇒ 1 格） | 1（⇒ 1 格，照走不误） |
| 骑兵 | 2 | 1（⇒ **平地 2 格**） | 2（⇒ **只能 1 格**） |

★ 由此**山地不可穿越**：进山地一步就把移动力花光——不是特判，是代价算出来的。
★ atk 同样逐格：隔着山/隔着别人的地界冲不进去（匈奴"跳过一格直取纵深"没了）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
from balance import MOVE_COST, TERRAINS  # noqa: E402

WILD = "野地"


class _Base(unittest.TestCase):
    def _world(self) -> mp.World:
        # 秦在 (5,5)，楚远在 (12,12)：先把 4..10 × 4..7 铺成自家平原走廊，避免
        # "中立不可入境"混进来（本文件只测**地形**对机动的影响）。
        w = mp.World(size=16, seed=5, nations=["秦", "楚"],
                     starts={"秦": (5, 5), "楚": (12, 12)})
        w.armies = []            # 清掉野人守卫：它们不参与本文件的结论，却会被
        for x in range(4, 11):   # atk 的"第三方驻守"判定拦下（真局里占地时会被赶走）
            for y in range(4, 8):
                t = w._new_tile(x, y, "秦")
                t["terrain"] = "平原"
                w.tiles[(x, y)] = t
        return w

    def _army(self, w, kind: str, x: int, y: int, owner: str = "秦") -> dict:
        a = {"id": 1, "gid": 1, "name": f"{owner}·{kind}一军", "type": kind,
             "hp": 100, "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": False}
        w.armies.append(a)
        return a

    def _ter(self, w, x, y, terrain: str) -> None:
        w.tiles[(x, y)]["terrain"] = terrain

    def _wall(self, w, x: int, ys=range(3, 8), terrain: str = "山地") -> None:
        """竖一道崎岖墙（含两端多一格）：骑兵进它就吃满移动力 ⇒ 挡住去路。"""
        for y in ys:
            if (x, y) not in w.tiles:
                t = w._new_tile(x, y, "秦")
                w.tiles[(x, y)] = t
            self._ter(w, x, y, terrain)

    def _enemy(self, w, x, y, terrain: str = "平原") -> None:
        """把 (x,y) 变成楚的领土并宣战（atk 用）。"""
        t = w._new_tile(x, y, "楚")
        t["terrain"] = terrain
        w.tiles[(x, y)] = t
        w.declare_war("秦", "楚")

    def _back(self, w, a, x: int, y: int) -> None:
        """把军队放回起点并恢复额度（"还能不能再走"的用例必须先回去）。"""
        a["x"], a["y"] = x, y
        a["moved_turn"] = -1


class TestCavalryReach(_Base):
    def test_two_tiles_on_flat(self):
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        ok, msg = w.move("秦", 1, 7, 5)            # 平原 ×2
        self.assertTrue(ok, msg)

    def test_rough_terrain_halves_it(self):
        for ter in ("森林", "山地"):
            with self.subTest(ter=ter):
                w = self._world()
                a = self._army(w, "骑", 5, 5)
                self._ter(w, 6, 5, ter)
                ok, msg = w.move("秦", 1, 6, 5)     # 进崎岖：1 格合法
                self.assertTrue(ok, f"骑兵进{ter}应当可以（1 格）：{msg}")

    def test_rough_slows_both_ways(self):
        """★ 对称口径（用户 2026-09-15）：**待在崎岖里的那一回合同样慢** ——
        从森林/山地出发也只能走 1 格（不能"从山上冲出来跑得比平地还快"）。"""
        for ter in ("森林", "山地"):
            with self.subTest(ter=ter):
                w = self._world()
                a = self._army(w, "骑", 6, 5)
                self._ter(w, 6, 5, ter)
                ok, msg = w.move("秦", 1, 7, 5)        # 1 格：可以
                self.assertTrue(ok, f"从{ter}里挪 1 格该允许：{msg}")
                self._back(w, a, 6, 5)
                ok, msg = w.move("秦", 1, 8, 5)        # 2 格：不行
                self.assertFalse(ok, f"骑兵从{ter}里还能走 2 格：{msg}")
                self.assertIn("移动力", msg)

    def test_wall_of_rough_blocks(self):
        """一道崎岖墙挡住 2 格外的目标——绕不过去（每格都要花掉全部移动力）。"""
        for ter in ("森林", "山地"):
            with self.subTest(ter=ter):
                w = self._world()
                a = self._army(w, "骑", 5, 5)
                self._wall(w, 6, terrain=ter)
                ok, msg = w.move("秦", 1, 7, 5)
                self.assertFalse(ok, f"骑兵越过了{ter}墙：{msg}")

    def test_hills_are_not_slow(self):
        """丘陵按 0.5 算（用户 2026-09-15 口径）⇒ 骑兵在丘陵照样 2 格。"""
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._ter(w, 6, 5, "丘陵")
        self._ter(w, 7, 5, "丘陵")
        ok, msg = w.move("秦", 1, 7, 5)
        self.assertTrue(ok, f"丘陵不该减速：{msg}")

    def test_reachable_set(self):
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._ter(w, 6, 5, "山地")
        reach = w._reachable("秦", a)
        self.assertEqual(reach[(6, 5)], 2)                 # 可以进去（花光）
        self.assertIn((7, 4), reach)                       # 绕开单格山地仍是 2 格
        self.assertEqual(reach[(7, 4)], 2)
        self.assertNotIn((5, 5), {k: v for k, v in reach.items() if v > 2})

    def test_single_mountain_can_be_walked_around(self):
        """单格山地不挡路（8 邻域可绕）——"阻挡"指的是**穿不过去**，不是"过不去"。"""
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._ter(w, 6, 5, "山地")
        ok, msg = w.move("秦", 1, 7, 5)
        self.assertTrue(ok, f"对角线绕行该允许：{msg}")
        self.assertEqual((a["x"], a["y"]), (7, 5))


class TestInfantryUnchanged(_Base):
    def test_one_tile_any_terrain(self):
        for ter in ("平原", "森林", "山地", "沙漠", "丘陵"):
            with self.subTest(ter=ter):
                w = self._world()
                a = self._army(w, "步", 5, 5)
                self._ter(w, 6, 5, ter)
                ok, msg = w.move("秦", 1, 6, 5)
                self.assertTrue(ok, f"步兵进{ter}该走得动：{msg}")
                self._back(w, a, 5, 5)                  # 回起点再来（不是从 6,5 再走一格）
                ok, msg = w.move("秦", 1, 7, 5)
                self.assertFalse(ok, f"步兵不该走 2 格：{msg}")

    def test_militia_same_as_infantry(self):
        w = self._world()
        a = self._army(w, "民", 5, 5)
        ok, msg = w.move("秦", 1, 6, 5)
        self.assertTrue(ok, msg)
        self._back(w, a, 5, 5)
        ok, msg = w.move("秦", 1, 7, 5)
        self.assertFalse(ok, msg)


class TestAttackPathed(_Base):
    def test_attack_two_tiles_on_flat(self):
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._enemy(w, 7, 5)
        ok, msg = w.attack("秦", [1], 7, 5)
        self.assertTrue(ok, f"平地两格该冲得进去：{msg}")

    def test_attack_blocked_by_mountain_wall(self):
        """★ 隔着一道山打不到——这是匈奴"跳过一格直取纵深"被取消的直接后果。"""
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._wall(w, 6)                       # (6,3)..(6,7) 全山地
        self._enemy(w, 7, 5)
        ok, msg = w.attack("秦", [1], 7, 5)
        self.assertFalse(ok, f"隔着山打进去了：{msg}")
        self.assertIn("冲不进去", msg)

    def test_attack_blocked_by_foreign_land_in_path(self):
        """中途借道别人的地界也不行（旧版 5×5 直取可以凭空跳过）。"""
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._wall(w, 6, terrain="平原")        # 中途一排是楚的领土（未宣战 ⇒ 不可入）
        for y in range(3, 8):
            w.tiles[(6, y)]["owner"] = "楚"
        self._enemy(w, 7, 5)
        ok, msg = w.attack("秦", [1], 7, 5)
        self.assertFalse(ok, f"借道他国领土冲进去了：{msg}")

    def test_attack_in_place_still_free(self):
        """原地 atk 不耗额度（旧行为保留：已在目标格上就不算移动）。"""
        w = self._world()
        a = self._army(w, "骑", 7, 5)
        self._enemy(w, 7, 5)
        a["x"], a["y"] = 7, 5
        a["moved_turn"] = w.turn              # 已经动过了
        ok, msg = w.attack("秦", [1], 7, 5)
        self.assertTrue(ok, f"原地进攻不该被'已移动过'拦住：{msg}")


class TestRetreatUnchanged(_Base):
    def test_retreat_still_one_tile_and_terrain_blind(self):
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._ter(w, 6, 5, "山地")
        a["engaged"] = True                    # 交战中才能撤
        ok, msg = w.retreat("秦", 1, 6, 5)
        self.assertTrue(ok, f"撤退进山地该允许（撤退不看兵种速度、也不看地形代价）：{msg}")
        a["moved_turn"] = -1
        ok, msg = w.retreat("秦", 1, 7, 5)
        self.assertFalse(ok, f"撤退固定 1 格，不该退 2 格：{msg}")


class TestBlindCost(_Base):
    def test_out_of_budget_does_not_burn(self):
        """★ 够不着 ≠ 撞墙：旧版射程不够也不罚，新版照旧（地形/预算不够不烧额度）。"""
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        self._wall(w, 6)                       # 一道山墙挡住 2 格路线
        ok, msg = w.move("秦", 1, 7, 5)
        self.assertFalse(ok)
        self.assertEqual(a["moved_turn"], -1, "够不着不该烧移动额度")

    def test_wall_out_of_sight_still_burns(self):
        """撞**墙**（视野外的中立/敌国领土）仍然报错如实 + 烧额度（迷雾限眼不限手）。"""
        w = self._world()
        a = self._army(w, "骑", 5, 5)
        # (7,6) 远离自家走廊（走廊只到 x=10,y=7… 用一块更远的格更稳）
        t = w._new_tile(11, 9, "楚")
        t["terrain"] = "平原"
        w.tiles[(11, 9)] = t
        self.assertFalse(w.visible_to("秦", 11, 9), "用例前提：目标必须在视野外")
        ok, msg = w.move("秦", 1, 11, 9)
        self.assertFalse(ok)
        self.assertIn("中立不可入境", msg)
        self.assertEqual(a["moved_turn"], w.turn, "视野外撞墙该烧额度")


class TestCostTable(_Base):
    def test_balance_table_is_the_source(self):
        """代价表住在 balance.py（唯一调参入口），且五个地形都得有值。"""
        for kind in ("步", "骑", "民"):
            for ter in TERRAINS:
                self.assertIn(ter, MOVE_COST[kind], f"{kind} 缺 {ter} 的代价")
        self.assertEqual(MOVE_COST["骑"]["山地"], 2)
        self.assertEqual(MOVE_COST["骑"]["丘陵"], 1)
        self.assertEqual(MOVE_COST["步"]["山地"], 1)

    def test_budget_is_unit_speed(self):
        w = self._world()
        self.assertEqual(w._reachable("秦", self._army(w, "步", 5, 5))[(6, 5)], 1)
        r = w._reachable("秦", self._army(w, "骑", 5, 5))
        self.assertEqual(r[(6, 5)], 1)
        self.assertEqual(r[(6, 6)], 1)         # 对角同样是"一步"


if __name__ == "__main__":
    unittest.main()