# -*- coding: utf-8 -*-
"""地图生成守卫：蓝噪声 + 密度图调制（`mods/native/map_generation/`）。

★ 本文件是旧架构 `tests/test_mapgen.py` 的**移植版**：旧引擎（`mp.py`/`game.py`/
  `balance.py`）已从主线删除，那份守卫随之失效；这里按新架构重写，**守住同一批
  不变量**（地形/资源一律用内容 ID）。

钉五件事——每一条都对应一个**真会犯的错**：

1. **配额精确**：地形各类全局占比 == `TERRAIN_WEIGHTS` 的整数配额
   （"稀有类型先挑"漏了"取够配额"就会掉——实测踩过：60×60 山地只挑到 9.1%）；
2. **边缘分布 = 权重表**：每地形每资源的期望 == `TERRAINS` 的理论值
   （抖动阈值法保证的；写成别的抽样就会偏）；
3. **空间性质**：地形成片性**优于**逐格 i.i.d.、资源成簇**少于** i.i.d.
   （★ 这条最值钱：rank 发反时沙漠贴邻比 0.566→0.397、黄金簇 6.2→9.3，
     全是"看起来换了个算法，其实正好搞反"的那种错）；
4. **确定性**：同 `(seed, size)` 两次生成逐格相同，且**跨 `PYTHONHASHSEED` 一致**
   （生成过程若遍历裸 `set`/`dict`，或用了内置 `hash()`，就会分叉）；
5. ★ **金色校验和**：钉住"具体那几张图"的地形+资源，而不只是"自洽"。

★ 第 5 条为什么单独一条（`test_same_seed_same_map` 已经证明确定性了）：那条只说
  "两次生成一样" ⇒ 一次把图**整体换掉**的改写照样能过它。而地图生成里任何
  "看着等价"的提速（**格点缓存** / 批量化 / 将来真去写 C）必须证明的是
  **同一张图**，那只能靠预先记下的绝对值。
  —— 这条守卫正是 `feat/rl-tactics` 那条**格点缓存**优化的护栏：
  缓存省掉 88% 的重复哈希，必须**逐位不变**（值来自同一个 `_unit(seed, salt, gx, gy)`）。

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

from mods.native.map_generation.algorithm import MapGen, _quotas  # noqa: E402
from mods.native.map_generation.parameters import (  # noqa: E402
    RESOURCES,
    TERRAINS,
    TERRAIN_WEIGHTS,
)

SIZES = (16, 30, 45)
SEEDS = (3, 20260905, 987654321)

# 地形 ID → 中文名（只给**断言消息**用；生成过程一律用内容 ID）。
_TERRAIN_LABEL = {
    "native:plain": "平原",
    "native:forest": "森林",
    "native:hill": "丘陵",
    "native:mountain": "山地",
    "native:desert": "沙漠",
}
_RESOURCE_LABEL = {
    "native:ore": "矿石",
    "native:gold": "黄金",
    "native:farmland": "耕地",
    "native:oil": "石油",
    "native:wood": "木头",
}


# ---------------------------------------------------------------- 指标
def _digest(seed: int, size: int) -> str:
    """整张图（地形 + **全部资源**，逐格、固定序）的 md5 前 12 位。

    ★ 编码方式**逐字继承**旧守卫（`tests/test_mapgen.py` 的同名函数），
      只是内容 ID 取代了中文名：每格 `"{x},{y}:{terrain}"`，
      再按 `RESOURCES` 的顺序拼 `|{id}={值}`。
    """
    g = MapGen(seed, size)
    h = hashlib.md5()
    for y in range(size):
        for x in range(size):
            h.update(f"{x},{y}:{g.terrain(x, y)}".encode("utf-8"))
            r = g.resources(x, y)
            h.update("".join(f"|{k}={r[k]}" for k in RESOURCES).encode("utf-8"))
    return h.hexdigest()[:12]


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
    """旧算法（逐格 i.i.d.）——只作对照基线。

    ★ 新架构里 `roll_terrain`/`roll_resources` 随旧引擎一起删了；这里按旧口径
      **就地复刻**（`TERRAIN_WEIGHTS` 加权抽样 + 按地形权重表抽样资源），
      只作对照基线用，不参与生产路径。
    """
    names = list(TERRAIN_WEIGHTS)
    weights = [TERRAIN_WEIGHTS[n] for n in names]
    ter, res = [], []
    for y in range(size):
        tr, rr = [], []
        for x in range(size):
            t = random.Random(f"{seed}:{x}:{y}").choices(names, weights=weights)[0]
            tr.append(t)
            rr.append({r: random.Random(f"{seed}:{x}:{y}:res").choices(
                range(len(w)), weights=w)[0]
                for r, w in TERRAINS[t].items()})
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
    """每地形每资源的期望 == 权重表理论值（抖动阈值法的不变量）。

    ★ **样本量是 12 个种子，不是 3 个**（旧守卫用 3 个）：那条 0.06 的容差是按
      **旧权重表**的 3 种子样本量的统计涨落定的；新架构换了权重表后，3 个种子的
      涨落就能到 0.08 —— 那是**抽样噪声，不是分布被改**（实测：种子数
      3→8→20→60 时最坏偏差 0.080→0.040→0.011→0.014，**收敛到 0**）。
      ⇒ 与其把容差放宽到"盖住噪声"（那等于放弃这条守卫），不如**把样本量加到
      容差以下**。实测 12 种子时最坏偏差 0.031，容差 0.05 留得住余量。
    """

    SIZE = 45
    SEEDS = (3, 20260905, 987654321, 20261000, 20261001, 20261002, 20261003,
             20261004, 20261005, 20261006, 20261007, 20261008)
    TOLERANCE = 0.05

    def test_expectation_matches_tables(self):
        obs: dict[tuple[str, str], list[int]] = {}
        for seed in self.SEEDS:
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
                self.assertLess(abs(got - theo), self.TOLERANCE,
                                f"{_TERRAIN_LABEL.get(t, t)}·{_RESOURCE_LABEL.get(r, r)}："
                                f"实测 {got:.3f} vs 理论 {theo:.3f}——分布被改了")


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

        for t in ("native:forest", "native:desert", "native:mountain"):
            with self.subTest(terrain=t):
                nb = self._avg(lambda g, t=t: _blocks(new_ter(g), self.SIZE,
                                                      lambda v: v == t))
                ob = self._iid_avg(lambda ter, _r, t=t: _blocks(ter, self.SIZE,
                                                                lambda v: v == t))
                self.assertGreater(nb, ob * 1.2,
                                   f"{_TERRAIN_LABEL.get(t, t)} 没成片：新 {nb:.0f} vs iid {ob:.0f}")

    def test_rare_terrain_is_spread_not_clumped(self):
        """沙漠（最稀）的贴邻比要比 iid **高**——rank 发反时它会掉到 iid 以下（实测 0.397）。"""
        def new_ter(g):
            return [[g.terrain(x, y) for x in range(self.SIZE)] for y in range(self.SIZE)]

        nb = self._avg(lambda g: _nbr_ratio(new_ter(g), self.SIZE,
                                            lambda v: v == "native:desert"))
        ob = self._iid_avg(lambda ter, _r: _nbr_ratio(ter, self.SIZE,
                                                      lambda v: v == "native:desert"))
        self.assertGreater(nb, ob,
                           f"沙漠贴邻比没改善：新 {nb:.3f} vs iid {ob:.3f}")

    def test_gold_less_clumped_and_fewer_holes(self):
        def new_res(g):
            return [[g.resources(x, y) for x in range(self.SIZE)] for y in range(self.SIZE)]

        def gold(res):
            return [[res[y][x]["native:gold"] for x in range(self.SIZE)] for y in range(self.SIZE)]

        nrc = self._avg(lambda g: _nbr_ratio(gold(new_res(g)), self.SIZE, lambda v: v >= 1))
        orc = self._iid_avg(lambda _t, res: _nbr_ratio(gold(res), self.SIZE, lambda v: v >= 1))
        nblk = self._avg(lambda g: _blocks(gold(new_res(g)), self.SIZE, lambda v: v >= 1))
        oblk = self._iid_avg(lambda _t, res: _blocks(gold(res), self.SIZE, lambda v: v >= 1))
        self.assertLess(nrc, orc, f"黄金还是结块：新 {nrc:.3f} vs iid {orc:.3f}")
        self.assertLessEqual(nblk, oblk, f"黄金最大簇没变小：新 {nblk} vs iid {oblk}")


class TestGoldenDigest(unittest.TestCase):
    """★ **金色校验和**：钉住"具体那几张图"的地形+资源，而不只是"自洽"。

    为什么单独一条（`test_same_seed_same_map` 已经证明确定性了）：那条只说
    "两次生成一样" ⇒ 一次把图**整体换掉**的改写照样能过它。而地图生成里任何
    "看着等价"的提速（格点缓存 / 批量化 / 将来真去写 C）必须证明的是
    **同一张图**，那只能靠预先记下的绝对值。

    ★ 这几个值是**在 `feat/rl-tactics` 的格点缓存合并之后**、用新架构
      （内容 ID）跑出来的：缓存返回的是**同一个 `_unit(seed, salt, gx, gy)`**
      的值 ⇒ 逐位不变。复算：
      `.venv/bin/python -c "import sys;sys.path.insert(0,'.');from tests.test_map_generation import _digest;print(_digest(SEED,SIZE))"`
    """

    GOLDEN = {
        (20260905, 20): "83892e58ef18",
        (424242, 30): "65a99fecc298",
        (31337, 12): "2c92bcf1b5e9",
        (7, 16): "d32c57d46686",
    }

    def test_maps_unchanged(self):
        for (seed, size), want in sorted(self.GOLDEN.items()):
            with self.subTest(seed=seed, size=size):
                self.assertEqual(_digest(seed, size), want,
                                 f"({seed},{size}) 换图了 —— 提速可以，**改图不行**")


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_map(self):
        for size in SIZES:
            a, b = MapGen(424242, size), MapGen(424242, size)
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
            "from mods.native.map_generation.algorithm import MapGen\n"
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

    def test_resources_dict_is_not_shared_mutable(self):
        """`resources` 返回的 dict 必须是**副本**：共享出去被就地改会污染整张图。"""
        g = MapGen(12, 3)
        a = g.resources(2, 2)
        before = dict(a)
        a["native:ore"] = 99
        self.assertEqual(g.resources(2, 2), before, "返回了同一份可变对象（缓存被改）")


if __name__ == "__main__":
    unittest.main()
