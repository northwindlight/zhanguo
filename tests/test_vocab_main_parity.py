# -*- coding: utf-8 -*-
"""冻结词表必须 = main 的词表；RL 的观测子表则冻结在「减去 MAIN_ONLY」。

跑法：python3 -m unittest tests.test_vocab_main_parity -v

理由分两层，2026-09-12 变基到 main 时定死：

1. **引擎枚举 = main 逐字逐序**。本分支的引擎文件（mp.py/game.py/mp_ai.py/mp_run.py）
   与 main 一字不差，所以 `game.*` 的枚举表当然也相同——但这条测试仍然拿
   `git show main:...` 当基准，因为**要防的是有人在本分支上单方面改表**。
2. **RL 侧的词表是冻结的**（`rl/vocab.py`，下标只许追加）。其中 `MAIN_ONLY`
   现在指「**不进 RL 观测/动作空间**的项」（外交中心）：它一旦进观测，网格与全局
   宽度就从 36/48 变 37/49，**已训好的 ckpt 全部加载不了**。

一句话：引擎跟着 main 走，观测跟着冻结表走，两者之间那道缝就是 MAIN_ONLY。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rl import vocab  # noqa: E402
from rl.env import KINDS as ENV_KINDS  # noqa: E402


def _show(path: str) -> str:
    """读 main 上的某个文件。取不到（没 git / 没 main）就 skip。"""
    try:
        r = subprocess.run(["git", "show", f"main:{path}"], cwd=ROOT,
                           capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise unittest.SkipTest(f"取不到 main:{path}（{e}）") from e
    return r.stdout


def _main_keys(path: str, name: str) -> list[str]:
    """取 main 某文件里某个**顶层 dict 赋值**的键（保序，不求值）。

    只取键不求值：值是纯字面量与否不影响下标，而键序正是要守的东西。
    """
    tree = ast.parse(_show(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            raise AssertionError(f"main:{path} 的 {name} 不是字面 dict，取不到键序")
        return [ast.literal_eval(k) for k in node.value.keys]
    raise AssertionError(f"main:{path} 里找不到 {name}")


def _main_list(path: str, name: str) -> list:
    tree = ast.parse(_show(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) \
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"main:{path} 里找不到 {name}")


def _main_tools() -> set[str]:
    """main 的 mp_ai 工具集（全部 tool 名）。"""
    src = _show("mp_ai.py")
    return set(re.findall(r'\{"type": "function", "function": \{\s*"name": "([A-Za-z_]+)"', src))


class TestEnumTablesMatchMain(unittest.TestCase):
    """枚举表逐字逐序等于 main。"""

    def test_building_is_main_16(self):
        self.assertEqual(list(vocab.BUILDING), _main_keys("game.py", "BUILDINGS"),
                         "建筑表与 main 不一致（顺序也算）")

    def test_building_includes_diplomacy_one(self):
        self.assertIn("外交中心", vocab.BUILDING,
                      "外交中心必须留在表里占位——外交回来时它要原地复活")

    def test_terrain_unit_tradeable_match_main(self):
        self.assertEqual(list(vocab.TERRAIN), _main_keys("game.py", "TERRAINS"))
        self.assertEqual(list(vocab.UNIT), _main_keys("game.py", "UNIT_TYPES"))
        self.assertEqual(list(vocab.TRADEABLE), _main_list("game.py", "TRADEABLE"))

    def test_kind_covers_all_main_tools(self):
        """main 的工具集必须被 KIND + 只读/LLM 专属 恰好覆盖（不多不少）。"""
        main_tools = _main_tools()
        self.assertTrue(main_tools, "没解析出 main 的工具集——正则该修了")
        covered = set(vocab.KIND) | set(vocab.QUERY_TOOLS) | set(vocab.LLM_ONLY_TOOLS)
        missing = main_tools - covered
        extra = covered - main_tools - {"_reserved_1", "_reserved_2",
                                        "_reserved_3", "_reserved_4"}
        self.assertFalse(missing, f"main 有、词表没覆盖的动作：{sorted(missing)}"
                                  f" —— 加外交时会漏掉它们")
        self.assertFalse(extra, f"词表有、main 没有的动作：{sorted(extra)}")

    def test_diplo_kinds_are_actually_main_diplomacy(self):
        main_tools = _main_tools()
        for k in vocab.DIPLO_KINDS:
            self.assertIn(k, main_tools, f"{k} 不是 main 的动作")


class TestLocalBranchMatchesMain(unittest.TestCase):
    """本分支的引擎枚举 = 冻结表 = **main**（2026-09-12 变基起不再砍外交）。

    口径变过一次，这里记清楚，免得下次接手的人按旧文档改回去：
    · 旧口径：本分支删掉「外交中心」，所以 `game.BUILDINGS` = 冻结表 − MAIN_ONLY；
    · 新口径：**引擎（mp.py/game.py/mp_ai.py/mp_run.py）与 main 逐字相同**，
      枚举当然也逐字相同；`MAIN_ONLY` 的含义随之改成「**不进 RL 观测/动作空间**的项」
      （外交中心独局下造不出来，进观测只会白白改变宽度、废掉 ckpt）。
    """

    def test_local_buildings_equal_frozen(self):
        import game
        self.assertEqual(list(game.BUILDINGS), list(vocab.BUILDING),
                         "本分支的建筑表 ≠ 冻结表 —— 引擎枚举漂了")

    def test_local_terrain_unit_tradeable_untouched(self):
        import game
        self.assertEqual(list(game.TERRAINS), list(vocab.TERRAIN))
        self.assertEqual(list(game.UNIT_TYPES), list(vocab.UNIT))
        self.assertEqual(list(game.TRADEABLE), list(vocab.TRADEABLE))

    def test_active_kinds_match_env_exactly(self):
        """前 8 项必须与 rl/env.py 的 KINDS 逐字逐序相同。

        那是已经训过的 type_emb 下标；动一下 = 旧 ckpt 的语义被改掉。
        """
        self.assertEqual(vocab.ACTIVE_KINDS, tuple(ENV_KINDS))

    def test_observation_table_is_frozen_minus_main_only(self):
        """RL 观测/动作的建筑子表 = 冻结表 − MAIN_ONLY（15 项，宽度与旧 ckpt 一致）。"""
        from rl.env import ZhanguoEnv
        env = ZhanguoEnv(map_size=12, max_turns=6)
        self.assertEqual(list(env.bnames),
                         [b for b in vocab.BUILDING if b not in vocab.MAIN_ONLY])


class TestFrozenInternalConsistency(unittest.TestCase):
    def test_no_duplicates(self):
        for name in ("TERRAIN", "BUILDING", "UNIT", "TRADEABLE",
                     "TILE_RES", "STOCK", "KIND", "EVENT", "RELATION"):
            t = getattr(vocab, name)
            self.assertEqual(len(t), len(set(t)), f"{name} 有重复项")

    def test_kind_is_32_and_index_consistent(self):
        self.assertEqual(len(vocab.KIND), 32)
        for i, k in enumerate(vocab.KIND):
            self.assertEqual(vocab.KIND_INDEX[k], i)

    def test_sub_tables_defined_for_every_kind(self):
        for k in vocab.KIND:
            self.assertIn(k, vocab.SUB_TABLE_OF, f"{k} 没有子项表")
        self.assertEqual(len(vocab.SUB_SIZES), len(vocab.KIND))

    def test_grid_channels_frozen(self):
        """通道数是冻结的：加国家、加外交都**不许**改它。"""
        self.assertEqual(vocab.GRID_CHANNELS, 45)
        self.assertEqual(len(vocab.OWNER_CHANNELS), 1 + vocab.NATION_SLOTS + 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
