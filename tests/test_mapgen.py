# -*- coding: utf-8 -*-
"""地图生成守卫：蓝噪声 + 密度图调制（`mapgen.py`）。

钉四件事——每一条都对应一个**真会犯的错**：

1. **配额精确**：地形各类全局占比 == `TERRAIN_WEIGHTS` 的整数配额
   （"稀有类型先挑"漏了"取够配额"就会掉——实测踩过：60×60 山地只挑到 9.1%）；
2. **边缘分布 = 权重表**：每地形每资源的期望 == `TERRAINS` 的理论值
   （抖动阈值法保证的；写成别的抽样就会偏）；
3. **空间性质**：地形成片性**优于**逐格 i.i.d.、资源成簇**少于** i.i.d.
   （★ 这条最值钱：rank 发反时沙漠贴邻比 0.566→0.397、黄金簇 6.2→9.3，
     全是"看起来换了个算法，其实正好搞反"的那种错）；
4. **确定性**：同 `(seed, size)` 两次生成逐格相同，且**跨 `PYTHONHASHSEED` 一致**
   （生成过程若遍历裸 `set`/`dict`，或用了内置 `hash()`，就会分叉）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import os
import random
import statistics
import subprocess
import sys
import unittest
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import game  # noqa: E402
import mp  # noqa: E402
from balance import RESOURCES, TERRAINS, TERRAIN_WEIGHTS  # noqa: E402
from mapgen import MapGen, _quotas  # noqa: E402

SIZES = (16, 30, 45)
SEEDS = (3, 20260905, 987654321)


# ---------------------------------------------------------------- 指标
def _blocks(field, size, pred) -> int:
    """最大 8 邻域连通块。"""
    seen = [[False] * size for _ in range(size)]
    best = 0
    for y in range(size):
        for x in range(size):
            if seen[y][x] or not pred(field[y][x]):
                continue
            q, n = deque([(x, y)]), 0
            seen[y][x] = True
            while q:
                cx, cy = q.popleft()
                n += 1
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if (0 <= nx < size and 0 <= ny < size and not seen[ny][nx]
                                and pred(field[ny][nx])):
                            seen[ny][nx] = True
                            q.append((nx, ny))
            best = max(best, n)
    return best


def _nbr_ratio(field, size, pred) -> float:
    """贴邻比：pred 为真的格里，有同为真的 8 邻格的比例。"""
    tot = hit = 0
    for y in range(size):
        for x in range(size):
            if not pred(field[y][x]):
                continue
            tot += 1
            if any(pred(field[y + dy][x + dx])
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                   if 0 <= x + dx < size and 0 <= y + dy < size and (dx or dy)):
                hit += 1
    return hit / tot if tot else 0.0


def _iid(size, seed):
    """旧算法（逐格 i.i.d.）——只作对照基线。"""
    ter, res = [], []
    for y in range(size):
        tr, rr = [], []
        for x in range(size):
            t = game.roll_terrain(random.Random(f"{seed}:{x}:{y}"))
            tr.append(t)
            rr.append(game.roll_resources(random.Random(f"{seed}:{x}:{y}:res"), t))
        ter.append(tr)
        res.append(rr)
    return ter, res


class TestQuota(unittest.TestCase):
    def test_counts_match_weights(self):
        for size in SIZES:
            for seed in SEEDS[:2]:
                with self.subTest(size=size, seed=seed):
                    g = MapGen(seed, size)
                    cnt = {t: 0 for t in TERRAIN_WEIGHTS}
                    for y in range(size):
                        for x in range(size):
                            cnt[g.terrain(x, y)] += 1
                    self.assertEqual(cnt, _quotas(TERRAIN_WEIGHTS, size * size),
                                     "地形全局占比偏离权重表——'稀有类型先挑'没取够配额？")


class TestMarginals(unittest.TestCase):
    """每地形每资源的期望 == 权重表理论值（抖动阈值法的不变量）。"""

    SIZE = 45

    def test_expectation_matches_tables(self):
        obs: dict[tuple[str, str], list[int]] = {}
        for seed in SEEDS:
            g = MapGen(seed, self.SIZE)
            for y in range(self.SIZE):
                for x in range(self.SIZE):
                    ter = g.terrain(x, y)
                    for r in RESOURCES:
                        obs.setdefault((ter, r), []).append(g.resources(x, y)[r])
        for t in TERRAIN_WEIGHTS:
            for r in RESOURCES:
                w = TERRAINS[t][r]
                theo = sum(i * wt for i, wt in enumerate(w)) / sum(w)
                got = statistics.mean(obs[(t, r)])
                self.assertLess(abs(got - theo), 0.06,
                                f"{t}·{r}：实测 {got:.3f} vs 理论 {theo:.3f}——分布被改了")


class TestSpatial(unittest.TestCase):
    """★ 空间性质：地形成片、资源不结块——**必须优于 iid 基线**。"""

    SIZE = 45
    SEEDS = (11, 22, 33)

    def _avg(self, fn):
        return statistics.mean(fn(MapGen(s, self.SIZE)) for s in self.SEEDS)

    def _iid_avg(self, fn):
        return statistics.mean(fn(*_iid(self.SIZE, s)) for s in self.SEEDS)

    def test_terrain_forms_patches(self):
        def new_ter(g):
            return [[g.terrain(x, y) for x in range(self.SIZE)] for y in range(self.SIZE)]

        def old_ter(ter, _res):
            return ter

        for t in ("森林", "沙漠", "山地"):
            with self.subTest(t=t):
                nb = self._avg(lambda g, t=t: _blocks(new_ter(g), self.SIZE,
                                                      lambda v: v == t))
                ob = self._iid_avg(lambda ter, _r, t=t: _blocks(ter, self.SIZE,
                                                                lambda v: v == t))
                self.assertGreater(nb, ob * 1.2,
                                   f"{t} 没成片：新 {nb:.0f} vs iid {ob:.0f}")

    def test_rare_terrain_is_spread_not_clumped(self):
        """沙漠（最稀）的贴邻比要比 iid **高**——rank 发反时它会掉到 iid 以下（实测 0.397）。"""
        def new_ter(g):
            return [[g.terrain(x, y) for x in range(self.SIZE)] for y in range(self.SIZE)]

        nb = self._avg(lambda g: _nbr_ratio(new_ter(g), self.SIZE, lambda v: v == "沙漠"))
        ob = self._iid_avg(lambda ter, _r: _nbr_ratio(ter, self.SIZE, lambda v: v == "沙漠"))
        self.assertGreater(nb, ob, f"沙漠贴邻比没改善：新 {nb:.3f} vs iid {ob:.3f}")

    def test_gold_less_clumped_and_fewer_holes(self):
        def new_res(g):
            return [[g.resources(x, y) for x in range(self.SIZE)] for y in range(self.SIZE)]

        def gold(res):
            return [[res[y][x]["黄金"] for x in range(self.SIZE)] for y in range(self.SIZE)]

        nrc = self._avg(lambda g: _nbr_ratio(gold(new_res(g)), self.SIZE, lambda v: v >= 1))
        orc = self._iid_avg(lambda _t, res: _nbr_ratio(gold(res), self.SIZE, lambda v: v >= 1))
        nblk = self._avg(lambda g: _blocks(gold(new_res(g)), self.SIZE, lambda v: v >= 1))
        oblk = self._iid_avg(lambda _t, res: _blocks(gold(res), self.SIZE, lambda v: v >= 1))
        self.assertLess(nrc, orc, f"黄金还是结块：新 {nrc:.3f} vs iid {orc:.3f}")
        self.assertLessEqual(nblk, oblk, f"黄金最大簇没变小：新 {nblk} vs iid {oblk}")


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_map(self):
        for size in SIZES:
            a, b = MapGen(SEED := 424242, size), MapGen(424242, size)
            for y in range(size):
                for x in range(size):
                    with self.subTest(size=size, x=x, y=y):
                        self.assertEqual((a.terrain(x, y), a.resources(x, y)),
                                         (b.terrain(x, y), b.resources(x, y)))

    def test_size_enters_generation(self):
        """地图是 `(seed, size)` 的函数：同 seed 换尺寸**不是同一张图的截取**
        （低频场是坐标的纯函数、重叠区大体相似，但**配额与蓝噪声 rank 按整图算**
         ⇒ 尺寸确实进了生成。别把这条写成"完全相同"，也别写成"毫无关系"）。"""
        small, big = MapGen(7, 20), MapGen(7, 40)
        same = sum(1 for y in range(20) for x in range(20)
                   if small.terrain(x, y) == big.terrain(x, y))
        self.assertLess(same, 20 * 20 * 0.95, "换尺寸几乎没变——尺寸没进生成？")
        self.assertGreater(same, 0, "换尺寸完全变了——低频场不该是逐图随机的")

    def test_cross_process_hashseed(self):
        """跨 `PYTHONHASHSEED` 必须一致（生成过程不许遍历裸 set/dict、不许用内置 hash）。"""
        script = (
            "import sys, hashlib; sys.path.insert(0, {root!r})\n"
            "from mapgen import MapGen\n"
            "g = MapGen(31337, 30)\n"
            "b = repr([(g.terrain(x, y), g.resources(x, y))"
            " for y in range(30) for x in range(30)]).encode()\n"
            "print(hashlib.sha256(b).hexdigest())\n"
        ).format(root=str(ROOT))
        outs = []
        for hs in ("0", "123456789"):
            env = dict(os.environ, PYTHONHASHSEED=hs, PYTHONUTF8="1")
            r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, env=env, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            outs.append(r.stdout.strip())
        self.assertEqual(outs[0], outs[1], "跨 PYTHONHASHSEED 生成不一致")
        self.assertEqual(len(outs[0]), 64)


class TestWorldIntegration(unittest.TestCase):
    def test_world_uses_mapgen(self):
        w = mp.World(size=16, seed=5, nations=["秦"])
        # 开局铺十字就会物化地块 ⇒ 图在 `__init__` 里已经被问到了（不是懒到第一次查图）
        g = MapGen(5, 16)
        self.assertEqual(w.tile_terrain(3, 4), g.terrain(3, 4))
        self.assertEqual(w.tile_resources(3, 4), g.resources(3, 4))

    def test_load_does_not_build_map(self):
        """★ 惰性的意义在读档：存档自带已物化格的 terrain/resources，读档**不必**建整张图
        （60×60 要 0.2s），只有之后真占新地时才建。"""
        import tempfile
        w = mp.World(size=16, seed=5, nations=["秦", "楚"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        self.assertIsNone(w2._mapgen, "读档时就建了整张图（惰性没了）")
        w2.tile_terrain(1, 1)                       # 真需要时才建
        self.assertIsNotNone(w2._mapgen)

    def test_unmaterialised_tiles_stable_across_save(self):
        """存档只存已物化的格子；读回来后未物化格仍按同一张图生成。"""
        import tempfile
        w = mp.World(size=16, seed=9, nations=["秦", "楚"])
        w.begin_turn()
        w.resolve_turn()
        vals = {(x, y): (w.tile_terrain(x, y), w.tile_resources(x, y))
                for y in range(16) for x in range(16)}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        for (x, y), v in vals.items():
            with self.subTest(x=x, y=y):
                self.assertEqual((w2.tile_terrain(x, y), w2.tile_resources(x, y)), v)

    def test_resources_dict_is_not_shared_mutable(self):
        """`tile_resources` 返回的 dict 必须是**副本**：共享出去被就地改会污染整张图。"""
        w = mp.World(size=12, seed=3, nations=["秦"])
        a = w.tile_resources(2, 2)
        before = dict(a)
        a["矿石"] = 99
        self.assertEqual(w.tile_resources(2, 2), before, "返回了同一份可变对象（缓存被改）")


if __name__ == "__main__":
    unittest.main()