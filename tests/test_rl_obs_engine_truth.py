# -*- coding: utf-8 -*-
"""★ 观测必须与**引擎真值**一致（2026-10-22 重写后的第二道闸）。

旧门面的观测层是从**门面自记的表**里读出来的（地块表、军队表），
而那些表**可能与引擎不一致**（门面自己复刻规则、自己缓存）⇒
"模型看到的"与"引擎判的"会悄悄分叉，**且不报错**。

现在观测层的每一项都必须**现算自引擎**：
  · 格的归属 → `native:territory.cell`
  · 视野     → `native:vision.positions`
  · 防御%    → `native:army.defense_pct`
  · 守军     → `native:army.visible_units`（野人是引擎**现场合成**的）
"""
from __future__ import annotations

import unittest

from rl.env import Env


def _sandbox(**kw):
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=kw.pop("seed", 11), size=kw.pop("size", 8), **kw)
    sb.reset()
    return sb


class TestTileTruth(unittest.TestCase):
    """地块归属：观测读到的 = 引擎说的。"""

    def test_tile_owner_matches_engine(self):
        sb = _sandbox()
        for row in sb.env.tiles():
            fact = sb.env.call("native:territory", "cell", row.position[0], row.position[1])
            self.assertEqual(row.owner, sb.env.name_of(fact.owner) if fact.owner else None,
                             f"{row.position} 的归属对不上引擎")

    def test_core_tile_owns_the_town_hall(self):
        sb = _sandbox()
        for name in sb.players:
            core = sb.core_of(name)
            self.assertIsNotNone(core)
            row = sb.env.tile(*core)
            self.assertEqual(row.owner, name, "核心格不归自己")
            self.assertGreaterEqual(row.buildings.get("native:town-hall", 0), 1,
                                    "核心格上没有市政厅")

    def test_territory_counts_match(self):
        sb = _sandbox()
        for name in sb.players:
            owned = {r.position for r in sb.env.tiles() if r.owner == name}
            engine = {tuple(f.tile.position) for f in sb.env.call(
                "native:territory", "owned", sb.env.id_of(name))}
            self.assertEqual(owned, engine, f"{name} 的国土集与引擎对不上")


class TestVisionTruth(unittest.TestCase):
    """视野：观测的 mask = 引擎的视野。"""

    def test_vision_mask_is_the_engine_vision(self):
        sb = _sandbox()
        for name in sb.players:
            engine = {tuple(p) for p in sb.env.call(
                "native:vision", "positions", sb.env.id_of(name))}
            self.assertEqual(set(sb.env.vision_mask(name)), engine,
                             f"{name} 的视野与引擎对不上")

    def test_visible_armies_are_the_engine_visible_units(self):
        sb = _sandbox()
        for name in sb.players:
            self.assertEqual(
                [u.gid for u in sb.env.visible_armies(name)],
                [u.gid for u in sb.env.call(
                    "native:army", "visible_units", sb.env.id_of(name))],
                f"{name} 的可见军队与引擎对不上")


class TestDefenseTruth(unittest.TestCase):
    """防御%：走 native 的公式，不自己算。"""

    def test_defense_pct_is_the_engine_formula(self):
        sb = _sandbox()
        for name in sb.players:
            for row in sb.env.tiles():
                if row.owner != name:
                    continue
                terrain = sb.env.terrain(*row.position)
                castle = 0
                if row.tile_id:
                    castle = (sb.env.call("native:buildings", "count", row.tile_id, "native:castle")
                              * sb.env.call("native:buildings", "effects", row.tile_id,
                                            "native:castle").get("defense_per_level", 0))
                expect = sb.env.call("native:army", "defense_pct", terrain.defense, castle)
                self.assertEqual(sb.env.defense_pct(row.position[0], row.position[1], name),
                                 expect, f"{row.position} 的防御%与引擎对不上")


class TestBarbarianTruth(unittest.TestCase):
    """野人：**引擎合成**的，RL 侧不自己造。"""

    def test_barbarian_cells_come_from_the_engine_units(self):
        sb = _sandbox()
        real = {(u.x, u.y) for u in sb.env.call("native:army", "snapshot")
                if u.owner == "野人" and u.alive}
        self.assertTrue(real <= sb.env.barbarian_cells(),
                        "引擎里存在的真野人不在野人格集合里")

    def test_defenders_use_the_engine_synthesis(self):
        """★ 引擎会为"可见的未物化格"现场合成野人 ⇒ 守军里能查到它们。"""
        sb = _sandbox()
        seen_synthetic = False
        for x in range(sb.size):
            for y in range(sb.size):
                if sb.env.owner(x, y) is not None:
                    continue
                if (x, y) not in sb.env.vision_mask("甲"):
                    continue
                defs = sb.env.defenders(x, y, "甲")
                if any(u.owner == "野人" for u in defs):
                    seen_synthetic = True
        self.assertTrue(seen_synthetic,
                        "可见的无主格上没有引擎合成的野人 ⇒ 野人口径与引擎不一致")


class TestTruthCapabilityIsSeparate(unittest.TestCase):
    """训练用真值**单独声明**（不混进玩家面）。"""

    def test_truth_capability_is_declared_and_separate(self):
        env = Env.load()
        self.assertIn("rl:truth", env.rt.capabilities)
        # 真值能力**只**在训练 mod 里声明，不在 native 的玩家 API 上。
        native = env.rt.units["native:army"].mod
        self.assertNotIn("rl:truth", getattr(native, "provides", lambda: ())())


if __name__ == "__main__":
    unittest.main()
