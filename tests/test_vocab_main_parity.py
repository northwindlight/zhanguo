# -*- coding: utf-8 -*-
"""冻结词表必须 = main 的词表（外交项允许「表里有、本分支无」）。

跑法：python3 -m unittest tests.test_vocab_main_parity -v

为什么拿 `git show main:...` 而不是本分支的 `game.py` 来比：
**本分支的 game.py 正是要防的那个东西**。本分支删掉了「外交中心」，
如果拿它当基准，测试就永远发现不了「除了外交还删/改了别的」。

这条测试守的是一句话：**feat/rl 与 main 的枚举差异，只有外交。**
多一项、少一项、错一位，都要红。
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


class TestLocalBranchDiffersOnlyByDiplomacy(unittest.TestCase):
    """本分支的 game.py 与冻结表的差异，**必须恰好是 MAIN_ONLY**。"""

    def test_local_buildings_are_frozen_minus_diplomacy(self):
        import game
        local = list(game.BUILDINGS)
        expect = [b for b in vocab.BUILDING if b not in vocab.MAIN_ONLY]
        self.assertEqual(local, expect,
                         "本分支的建筑表 ≠ 「冻结表减去外交项」——除了外交还动了别的")

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
