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

    def test_building_active_prefix_is_main(self):
        """★ 2026-09-12 留位后口径变了：**不再要求相等，要求「main 是活跃前缀」**。

        `vocab.BUILDING` 现在是「main 的 16 项 + 4 个留位」。留位项只在表尾，
        所以 main 的键序必须逐位等于本表的前 16 项 —— 这样"加建筑"就是填留位槽，
        下标不动、宽度不变（§10.3）。
        """
        main_keys = _main_keys("game.py", "BUILDINGS")
        self.assertEqual(list(vocab.BUILDING)[:len(main_keys)], main_keys,
                         "main 的建筑键序必须是本表的前缀（留位只许加在表尾）")
        self.assertEqual(list(vocab.BUILDING)[len(main_keys):],
                         list(vocab.RESERVED_BUILDING),
                         "表尾只该是留位项")

    def test_building_includes_diplomacy_one(self):
        self.assertIn("外交中心", vocab.BUILDING,
                      "外交中心必须留在表里占位——外交回来时它要原地复活")

    def test_terrain_unit_tradeable_active_prefix_is_main(self):
        main_g = _main_keys("game.py", "TERRAINS")
        self.assertEqual(list(vocab.TERRAIN)[:len(main_g)], main_g,
                         "main 的地形键序必须是本表的前缀（表尾是留位）")
        self.assertEqual(list(vocab.TERRAIN)[len(main_g):], list(vocab.RESERVED_TERRAIN))
        main_u = _main_keys("game.py", "UNIT_TYPES")
        main_t = _main_list("game.py", "TRADEABLE")
        self.assertEqual(list(vocab.UNIT)[:len(main_u)], main_u,
                         "main 的兵种键序必须是本表的前缀")
        self.assertEqual(list(vocab.TRADEABLE)[:len(main_t)], main_t,
                         "main 的物资键序必须是本表的前缀")
        self.assertEqual(list(vocab.UNIT)[len(main_u):], list(vocab.RESERVED_UNIT))
        self.assertEqual(list(vocab.TRADEABLE)[len(main_t):], list(vocab.RESERVED_TRADEABLE))

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

    def test_local_buildings_are_frozen_active_prefix(self):
        """引擎的表 = 冻结表的**活跃段**（留位段引擎里没有，自然也枚举不出来）。

        这就是留位的定义：`BUILDING` 多出来的那 4 项在 `game.BUILDINGS` 里**不存在**，
        而观测/动作空间靠 `OBS_BUILDING` / `BUILDABLE` 把它们的语义说清楚。
        """
        import game
        self.assertEqual(list(game.BUILDINGS), list(vocab.BUILDING)[:len(game.BUILDINGS)],
                         "引擎的建筑表 ≠ 冻结表的活跃前缀 —— 枚举漂了")
        for r in vocab.RESERVED_BUILDING:
            self.assertNotIn(r, game.BUILDINGS, f"留位项 {r} 不该出现在引擎表里")

    def test_local_terrain_unit_tradeable_untouched(self):
        """地形逐项相同；兵种/物资与建筑同理 —— 引擎的是**活跃前缀**。"""
        import game
        self.assertEqual(list(game.TERRAINS), list(vocab.TERRAIN)[:len(game.TERRAINS)])
        self.assertEqual(list(game.UNIT_TYPES), list(vocab.UNIT)[:len(game.UNIT_TYPES)])
        self.assertEqual(list(game.TRADEABLE), list(vocab.TRADEABLE)[:len(game.TRADEABLE)])

    def test_active_kinds_match_env_exactly(self):
        """前 8 项必须与 rl/env.py 的 KINDS 逐字逐序相同。

        那是已经训过的 type_emb 下标；动一下 = 旧 ckpt 的语义被改掉。
        """
        self.assertEqual(vocab.ACTIVE_KINDS, tuple(ENV_KINDS))

    def test_observation_table_is_frozen_minus_main_only(self):
        """RL 观测/动作的建筑子表 = 冻结表 − MAIN_ONLY（19 = 15 真建筑 + 4 留位）。"""
        from rl.env import ZhanguoEnv
        env = ZhanguoEnv(map_size=12, max_turns=6)
        self.assertEqual(list(env.bnames), list(vocab.OBS_BUILDING))
        self.assertEqual(list(env.unames), list(vocab.UNIT))
        self.assertEqual(list(env.goods), list(vocab.TRADEABLE))

    def test_only_real_entities_become_candidates(self):
        """留位槽**不产生候选** —— 候选枚举走的是 `*_REAL` 那三张表。"""
        from rl.env import ZhanguoEnv
        env = ZhanguoEnv(map_size=12, max_turns=6)
        self.assertEqual(list(env.buildable), list(vocab.BUILDABLE))
        self.assertEqual(list(env.recruitable), list(vocab.RECRUITABLE))
        self.assertEqual(list(env.tradeable), list(vocab.TRADEABLE_REAL))
        for r in vocab.RESERVED:
            self.assertNotIn(r, env.buildable + env.recruitable + env.tradeable)


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
        """网格通道数是**冻结的**：改成 42 是一次口径变更（§10.6 第 5 行），
        此后加建筑/兵种/物资都不许再动它（留位就是为此付的钱）。

        为什么这条断言的是**实测值**而不是某个常量：这里曾有一个 `GRID_CHANNELS = 45`
        的"计划值"，从没接线，而且与实测的 36 口径不同 —— 两套口径各自漂了一年。
        现在唯一来源 = `env.obs_channels()`（§10.3 坑 6）。
        """
        from rl.env import ZhanguoEnv
        env = ZhanguoEnv(map_size=12, max_turns=6)
        ch = env.obs_channels()
        self.assertEqual(len(ch), 45,
                         "网格宽度变了 —— 这是 ckpt 的硬契约，改了就要重炼")
        # 分段自洽：地形 len(OBS_TERRAIN) + 地块资源 5 + 归属(1+对手+1)
        #          + 建筑 len(OBS_BUILDING) + 建造成本 1 + 标量 9 + 记忆预留 2
        n_owner = 1 + len(env.rivals) + 1
        self.assertEqual(len(ch), len(vocab.OBS_TERRAIN) + 5 + n_owner
                         + len(vocab.OBS_BUILDING) + 1 + 9 + 2)
        self.assertTrue(set(ch) >= {"visible", "home", "remembered", "probe"})
        # 国槽上限口径（实现是动态的，计划是固定 8 —— 这条偏差记在 §10.3）
        self.assertEqual(len(vocab.OWNER_CHANNELS), 1 + vocab.NATION_SLOTS + 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
