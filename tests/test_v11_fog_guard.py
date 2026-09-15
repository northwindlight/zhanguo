# -*- coding: utf-8 -*-
"""v11 迷雾守卫：**决策层一格迷雾都不许开**（全项目最容易复发的一类 bug）。

`expand_rule_v9.py` 的文档里记着四处历史越权（`_new_tile` 开图看资源、用 `owned_by`
判迷雾格、兜底目标取全图野人列表……），所以这里不靠"读代码时小心"，靠两道闸：

  1. **运行期投毒**（`_ReservePoison`）：把 `world.mapgen.resources()` 包一层，
     凡是问到**看不见**的格的资源就当场炸，然后照常跑 v11 的整回合。
     ★ 只投毒 `resources`、不投毒 `terrain`：**地形**是引擎自己的合法读物
     （`_reachable` 逐格问地形算代价、`_withdraw_illegal` 找路），而**资源**在
     `_new_tile`（占地那一刻，占的必然是看得见的地）之外没人该问 ——
     它才是那个"占之前永远不该知道"的秘密。地形的迷雾纪律由
     `tests/test_pathfind.py::TestFogDiscipline` 用更精确的方式钉（直接证明
     `cell_cost` 对迷雾格**一次都没调** `tile_terrain`）。
  2. **源码扫描（AST）**：决策模块里不许出现 `_new_tile` / `tile_resources` /
     `import mapgen`；也不许下标读 `["resources"]`。只看**代码**，不看注释与文档字符串
     （文档里正解释着"为什么不能用它们"，字符串扫会误伤）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import rule_ai  # noqa: E402

DECISION_MODULES = ("ruleai/pathfind.py", "ruleai/targeting.py",
                    "ruleai/combat.py", "ruleai/grouping.py")
V11_MODULES = DECISION_MODULES + ("ruleai/military.py", "ruleai/economy.py",
                                  "ruleai/v11.py")


class _ReservePoison:
    """问到**看不见**的格的资源就炸；看得见的格（含引擎占地时掷资源）照常放行。"""

    def __init__(self, real, world, owner: str):
        self._real, self._world, self._owner = real, world, owner
        self.hits = 0

    def terrain(self, x: int, y: int) -> str:
        return self._real.terrain(x, y)          # 地形是引擎的合法读物（见文件头）

    def resources(self, x: int, y: int) -> dict:
        if not self._world.visible_to(self._owner, x, y):
            raise AssertionError(f"读了迷雾格 ({x},{y}) 的资源 = 开图偷看")
        self.hits += 1
        return self._real.resources(x, y)


class TestNoPeeking(unittest.TestCase):
    def _world(self) -> mp.World:
        return mp.World(size=20, seed=42, nations=["秦", "楚"],
                        starts={"秦": (5, 5), "楚": (14, 14)})

    def test_whole_v11_turn_never_peeks_at_resources(self):
        """整回合（含占地掷资源）跑 20 回合，投毒一次都不许响。"""
        _, fn = rule_ai.resolve("v11")
        w = self._world()
        w.mapgen                                     # 先建出 mapgen 才能包
        poison = _ReservePoison(w._mapgen, w, "秦")
        w._mapgen = poison
        rng = random.Random(3)
        for _ in range(20):
            w.begin_turn()
            fn(w, "秦", rng, max_actions=12)
            w.resolve_turn()
        self.assertGreater(poison.hits, 0,
                           "一次都没问过资源？那这条守卫是空的（占地时本该问一次）")
        self.assertGreater(len(w.own_tiles("秦")), 5, "20 回合该有扩张，否则没走到决策深处")


def _code_hits(name: str) -> tuple[list, list]:
    """扫一个模块的**代码**（AST，不看注释与文档字符串）→ `(禁用的名字, 读资源的下标)`。

    按名字报，不 dump 整棵树 —— 失败信息要一眼看得懂。
    """
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    keys, res = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("mapgen",):
            keys.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr in ("_new_tile", "tile_resources"):
            keys.append(node.attr)
        if isinstance(node, ast.Attribute) and node.attr == "mapgen":
            keys.append("mapgen")
        if isinstance(node, ast.Import):
            keys += [a.name for a in node.names if a.name == "mapgen"]
        if isinstance(node, ast.ImportFrom) and node.module == "mapgen":
            keys.append("from mapgen import")
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value == "resources":
            res.append(f"第 {node.lineno} 行")
    return keys, res


class TestSourceGuard(unittest.TestCase):
    """源码扫描：不许出现开图的两把钥匙（只扫代码，不扫注释/文档串）。"""

    def test_no_mapgen_keys_in_decision_modules(self):
        """`_new_tile` / `tile_resources` / `mapgen` —— 占地与掷资源的钥匙，决策层一个都不许碰。"""
        for name in V11_MODULES:
            with self.subTest(module=name):
                keys, _ = _code_hits(name)
                self.assertEqual(keys, [], f"{name} 的代码里出现了 {sorted(set(keys))} —— "
                                           f"那是占地/掷资源的钥匙，碰它就是开图偷看")

    def test_no_decision_module_reads_resources(self):
        """★ 四个决策模块**一处都不许**读地块资源（比"走闸门"更强的一条）。

        v11 的目标排序只看扩张效率（用户 2026-09-15：「不对地形估值了」），
        所以连"读资源"这个能力都不需要 —— 没有读的地方，就没有会变成偷看的地方。
        """
        for name in DECISION_MODULES:
            with self.subTest(module=name):
                _, res = _code_hits(name)
                self.assertEqual(res, [], f"{name} 读了地块资源（v11 不该需要它）")

    def test_executor_military_section_never_reads_resources(self):
        """执行器**军事段**（第 8 节起）不许读地块资源。

        经济段读 `t["resources"]` 是合法的（那是**自家**地块，永远看得见，
        建采集建筑本来就要看它）；军事段读它就是隔着迷雾挑目标了。
        """
        src = (ROOT / "ruleai" / "military.py").read_text(encoding="utf-8")
        marker = src.index("8. 扩张")
        tail = src[marker:]
        self.assertNotIn('["resources"]', tail, "军事段读了地块资源")
        self.assertNotIn("mapgen", tail, "军事段碰了 mapgen")


if __name__ == "__main__":
    unittest.main()