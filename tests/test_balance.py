# -*- coding: utf-8 -*-
"""数值表守卫：**调参入口只有一个文件**（`balance.py`）。

病根：数值表散在各处时，"这建筑多少钱"有两个答案——改了一个、另一个没改，
  · 引擎按 A 算、面板按 B 显示（AI 被自己面板骗）；
  · 或者有人图省事 `game.BUILDINGS = {...}`（**重建容器**）——引擎拿的是老对象，
    改了等于没改，而且**不报错**（`feat/rl` 分支 `rl/jitter.py` 的 docstring 记着这条血案）。
本测试钉三件事：

  1. **转口是同一个对象，不是副本**：`game.X is balance.X`、`mp.X is balance.X`
     —— 就地改两边同时可见（`feat/rl` 那套域随机化就建立在这条上）；
  2. **本仓没有第二份定义**：`balance.py` 是这些名字唯一的赋值处
     （扫源码：别的文件只能 import，不许再写 `BUILDINGS = {...}`）；
  3. **依赖方向**：`balance` 不 import 任何引擎模块（纯数据、无环）——
     否则 `game → balance → game` 会成环。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import balance  # noqa: E402
import game  # noqa: E402
import mp  # noqa: E402

# 从 game.py 转口的名字
VIA_GAME = (
    "RESOURCES", "RESOURCE_MAX", "GOODS", "TERRAINS", "TERRAIN_STATS", "TERRAIN_WEIGHTS",
    "TERRAIN_CHARS", "MAX_SLOTS", "MARKET", "TRADEABLE", "MARKET_DEPTH", "PRICE_IMPACT",
    "MARKET_SPREAD", "PRICE_REVERT", "PRICE_MIN_RATIO", "PRICE_MIN_ABS",
    "PRICE_MAX_RATIO", "MARKET_SENS",
    "MARKET_EQ_MIN_RATIO", "MARKET_EQ_MAX_RATIO", "MARKET_GAP_ONE_SIDE", "BUILDINGS",
    "UNIT_TYPES", "ARMY_MAX_HP", "ARMY_STARVE_DAMAGE", "ARMY_HEAL_PER_TURN",
    "ARMY_ATTACK_DAMAGE", "RETREAT_RANGE", "RETREAT_ATK_PENALTY", "COMBAT_DIE_MOD",
    "DIPLO_CENTER_MIN_COST", "LETTER_COST", "LETTER_COST_ALLY", "LETTER_CENTER_DISCOUNT",
    "LETTER_COST_MIN", "LETTER_FREE_CHARS", "LETTER_CHARS_PER_GOLD", "SITE_BUILDING",
)
# 从 mp.py 转口的名字（开局/展示口径 + 钱 + 规则节奏）
VIA_MP = ("START_RES", "RES_KEYS", "RES_LABEL", "SPY_COST", "DIPLO_COST", "RETREAT_DEF_COVER",
          "SPY_TURNS", "PLAN_MAX_TURNS", "REPORT_EVERY")

# 刻意**留在原地**的（搬了要连守卫一起改，见 balance.py 头部的「没搬进来的」）
STAYS = {"game": ("NAME_PREFIX", "NAME_SUFFIX", "_CN_DIGIT"),
         "mp": ("SAVE_VERSION", "SAVE_KEYS", "CROSS", "SPEND_FIELDS", "LEDGER_FIELDS")}

def _rule_ai_files() -> list[Path]:
    """规则 AI 的全部源码文件：根目录的老几代 + `ruleai/` 包（含 v11 子目录）。

    ★ 全都要扫：挪进包就脱离守卫，是最容易发生的事（v11 就是这么挪进来的）。
    """
    return (list(ROOT.glob("expand_rule_*.py")) + list(ROOT.glob("ruleai/*.py"))
            + list(ROOT.glob("ruleai/*/*.py")))


# 引擎模块：balance.py 不许 import 它们（只要模块名，路径无所谓）
ENGINE_MODULES = {"game", "mp", "mp_ai", "mp_run", "ctx", "console", "settlement",
                  "spend_rules", "llm_provider"}


class TestSingleObject(unittest.TestCase):
    """转口 = 同一个对象（就地改两边都看得见），不是值拷贝。"""

    def test_game_reexports_same_object(self):
        for n in VIA_GAME:
            with self.subTest(n=n):
                self.assertIs(getattr(game, n), getattr(balance, n),
                              f"game.{n} 不是 balance.{n} 同一个对象——引擎会看不见改动")

    def test_mp_reexports_same_object(self):
        for n in VIA_MP:
            with self.subTest(n=n):
                self.assertIs(getattr(mp, n), getattr(balance, n),
                              f"mp.{n} 不是 balance.{n} 同一个对象")

    def test_inplace_edit_is_visible_everywhere(self):
        """就地改一处 → 引擎与 AI 层看到的是同一份（改完还原）。"""
        before = balance.BUILDINGS["农场"]["cost"]
        try:
            balance.BUILDINGS["农场"]["cost"] = before + 7
            self.assertEqual(game.BUILDINGS["农场"]["cost"], before + 7)
            self.assertEqual(mp.BUILDINGS["农场"]["cost"], before + 7)
        finally:
            balance.BUILDINGS["农场"]["cost"] = before
        self.assertEqual(game.BUILDINGS["农场"]["cost"], before)


class TestNoSecondDefinition(unittest.TestCase):
    """**全仓**没有第二份数值表定义：只能从 balance 转口，不许重新赋值。**无例外**。

    为什么连规则 AI 一起管：`balance.GOODS`（4 项物资）与规则 AI 里那个同名的
    `GOODS`（6 项交易品）曾经各说各话 —— 名字一样、内容不同、谁也不知道该改哪个。
    现在两边一律写成 `GOODS = tuple(TRADEABLE)`：值取自引擎那份，语义也只剩一份。
    """

    # 断言「唯一赋值处」的名字（SITE_BUILDING 是派生式，也一并在 balance 里）
    CANONICAL = VIA_GAME + VIA_MP

    def _assigned_names(self, path: Path) -> set[str]:
        """该文件里被赋值的顶层名字（含 `X: dict = ...` 注解式）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        return names

    def test_only_balance_defines_them(self):
        # ★ 也要扫 `ruleai/` 这个包：v11 的军事四件套与两层实现都住在里面，
        #   只扫根目录会让它们**悄悄脱离守卫**（挪进包就没人管了）。
        for p in sorted(_rule_ai_files()):
            if p.name == "balance.py":
                continue
            dup = self._assigned_names(p) & set(self.CANONICAL)
            self.assertEqual(dup, set(),
                             f"{p.name} 又定义了一份数值表：{sorted(dup)}——"
                             f"数值只许住在 balance.py（转口请用 import）")

    def test_rule_ai_imports_tables(self):
        """规则 AI（玩家）的引擎表一律 import 自 game，不自己抄数。

        ★ 两处都要管：根目录的各代 `expand_rule_*.py`，以及 `ruleai/` 包里的分层实现
        —— 挪进包就脱离守卫，那是最容易发生的事。
        ★ `ruleai/__init__.py` 例外：它只放**动作账本**（一层薄壳，不碰任何数值表），
        所以本来就不需要 import game。
        """
        # ★ 两个包级 `__init__` 与几处**薄壳**刻意列出（一条数值都不用）：
        #   `ruleai/__init__.py`（家谱说明）、`ruleai/v11/__init__.py`（分层说明 + 入口转口）、
        #   `ruleai/v11/entry.py`（四行胶水）、`ruleai/v11/ledger.py`（动作账本）、
        #   `ruleai/v11/targeting.py`（只铺候选格）。清单写出来，是为了"想往里塞手抄表"
        #   的时候看得见这道闸。
        exempt = {"__init__.py", "entry.py", "ledger.py", "targeting.py"}
        files = [p for p in sorted(_rule_ai_files()) if p.name not in exempt]
        self.assertGreaterEqual(len(files), 7, "文件都没找到？守卫会变成空转")
        for p in files:
            src = p.read_text(encoding="utf-8")
            self.assertTrue("from game import" in src or "from balance import" in src,
                            f"{p.name} 没有从 game/balance 取数值表（抄了一份自己的？）")

    def test_stays_put(self):
        """刻意没搬的名字仍在原处（免得以后有人"顺手"搬走，守卫却对不上）。"""
        for where, names in STAYS.items():
            mod = {"game": game, "mp": mp}[where]
            for n in names:
                with self.subTest(n=n):
                    self.assertTrue(hasattr(mod, n), f"{where}.{n} 不见了")


class TestNoCycle(unittest.TestCase):
    """balance 是纯数据：不 import 任何引擎模块（否则 game↔balance 成环）。"""

    def test_imports_nothing_from_engine(self):
        tree = ast.parse((ROOT / "balance.py").read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append((node.module or "").split(".")[0])
        bad = sorted(set(imported) & ENGINE_MODULES)
        self.assertEqual(bad, [], f"balance.py import 了引擎模块 {bad}——调参表不该依赖引擎")

    def test_engine_imports_balance(self):
        """反过来：game/mp 都得从 balance 转口（别哪天又抄回来一份）。"""
        for rel, mod in (("game.py", game), ("mp.py", mp)):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("from balance import", src, f"{rel} 没有从 balance 转口")
            self.assertIs(getattr(mod, "BUILDINGS", None), balance.BUILDINGS,
                          f"{rel} 的 BUILDINGS 不是 balance 那一份")