# -*- coding: utf-8 -*-
"""`panel=battle` 与自动摘要 `battle_brief`。

这个面板的存在理由是 2026-10-07 的一次事故：燕用五支满血步兵砸周的都城、五支被全歼，
而它事前**看不到**守方投入了多少、吃几档减伤（`engaged` 只标记"我是进攻方"，守方
没有任何标记）。所以本文件盯两件事：

  ① **可见性纪律**：看不见又没在场的仗，连存在都不提；看不见但**我在场**（盲战）
     只报我方——**尤其不报减伤**（它含城堡分量，报出去等于让 AI 反推雾里的城防）；
  ② **只给事实**：面板不做胜率/概率（用户否掉了接战斗 DP：「有点作弊了」）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import mp_ai  # noqa: E402


def _world() -> mp.World:
    """秦在 (5,5) 一带、楚在 (12,12) 一带。于是 (6,6) 在秦视野内、(12,12) 不在。"""
    w = mp.World(size=16, seed=11, nations=["秦", "楚"],
                 starts={"秦": (5, 5), "楚": (12, 12)})
    w.armies = []
    w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [], "turn": 1}]
    return w


def _army(w, kind: str, x: int, y: int, owner: str, aid: int,
          hp: int = 100, engaged: bool = False, type_: str | None = None) -> dict:
    a = {"id": aid, "gid": aid, "name": f"{owner}·{kind}{aid}军",
         "hp": hp, "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": engaged}
    if type_ != "none":
        a["type"] = type_ or kind
    w.armies.append(a)
    return a


def _tile(w, x: int, y: int, owner: str | None, terrain: str = "平原",
          castle: int = 0, hall: int = 0) -> dict:
    """物化一格（引擎的 `tiles` 是**惰性**的，不建就 KeyError）。"""
    t = w._new_tile(x, y, owner or "秦")
    t["owner"] = owner
    t["terrain"] = terrain
    t["buildings"]["城堡"] = castle
    t["buildings"]["市政厅"] = hall
    w.tiles[(x, y)] = t
    return t


class TestBattlePanel(unittest.TestCase):
    def test_empty_state_is_teaching_not_blank(self):
        w = _world()
        s = mp_ai._fmt_battle(w, "秦")
        self.assertIn("当前没有交战", s)
        self.assertNotIn("⚔", s)

    def test_visible_battle_lists_both_sides_with_facts(self):
        w = _world()
        _tile(w, 6, 6, "楚", terrain="丘陵", castle=2, hall=1)
        _army(w, "步", 6, 6, "秦", 1, engaged=True)
        _army(w, "骑", 6, 6, "秦", 2, engaged=True)
        _army(w, "步", 6, 6, "楚", 3, hp=88)
        s = mp_ai._fmt_battle(w, "秦")
        self.assertIn("攻方（你） 秦", s)
        self.assertIn("2 支 · 步1 骑1", s)          # 支数 + 兵种构成
        self.assertIn("合计 200HP", s)              # 总 HP
        self.assertIn("输出基数", s)                 # 总战力（基数，不含骰子）
        self.assertIn("守方 楚", s)
        self.assertIn("楚·步3军(#3) 88HP", s)        # 逐支番号 + HP
        self.assertIn("**仅防御方**", s)             # 减伤归属必须明说
        self.assertIn("丘陵 +25%", s)
        self.assertIn("城堡 L2 +20%", s)
        self.assertIn("合计减伤 40%", s)             # 相乘叠加，不是 45%
        self.assertIn("【市政厅·国祚】", s)          # 厅要显眼标出来
        self.assertIn("本回合都不回血", s)           # 在交战格就不回血

    def test_invisible_and_uninvolved_battle_is_not_shown_at_all(self):
        """★ 别国在我看不见的地方互殴：**坐标、番号、连计数都不出现**。"""
        w = _world()
        _tile(w, 12, 12, "楚")
        _army(w, "步", 12, 12, "楚", 1, engaged=True)
        _army(w, "步", 12, 12, "赵", 2, hp=90)      # 赵不在 nations 里也没关系：只按格算
        s = mp_ai._fmt_battle(w, "秦")
        self.assertIn("当前没有交战", s)
        self.assertNotIn("(13,13)", s)
        self.assertNotIn("赵", s)
        self.assertEqual(mp_ai.battle_brief(w, "秦"), "")

    def test_blind_battle_shows_only_my_side(self):
        """★ 盲战：看不见但**我在场** ⇒ 出现、只报我方，敌方与减伤一律不给。

        减伤（哪怕只是"合计"）绝不能报——它含城堡分量，等于让 AI 用算术反推雾里的城防。
        """
        w = _world()
        _tile(w, 12, 12, "楚", terrain="山地", castle=3)
        _army(w, "步", 12, 12, "秦", 1, engaged=True)
        _army(w, "骑", 12, 12, "秦", 2, engaged=True)
        _army(w, "步", 12, 12, "楚", 3, hp=77)
        self.assertFalse(w.visible_to("秦", 12, 12), "前提：该格对秦不可见")
        s = mp_ai._fmt_battle(w, "秦")
        self.assertIn("盲战", s)
        self.assertIn("攻方（你） 秦", s)      # 盲战里"我方"按角色标签给（攻/守）
        self.assertIn("2 支 · 步1 骑1", s)          # 自己的军情照给
        self.assertIn("合计 200HP", s)
        self.assertNotIn("楚·步3军", s)              # 敌方番号不给
        self.assertNotIn("77HP", s)
        self.assertIn("敌情不明", s)
        self.assertNotIn("山地", s)                  # 地形不给
        self.assertNotIn("城堡 L3", s)
        self.assertNotIn("减伤 4", s)                # 任何减伤数字都不给
        b = mp_ai.battle_brief(w, "秦")
        self.assertIn("盲战", b)
        self.assertIn("敌情不明", b)

    def test_starving_side_is_reported_with_hp_cost(self):
        """缺粮要连**会流多少血**一起报（光说"缺 N"AI 看不出代价；交战中也照扣）。"""
        w = _world()
        _tile(w, 6, 6, "楚")
        _army(w, "步", 6, 6, "秦", 1, engaged=True)
        _army(w, "步", 6, 6, "楚", 6)
        w.add_res("秦", "补给", -w.res("秦", "补给"))
        s = mp_ai._fmt_battle(w, "秦")
        need, short, per = w.supply_shortfall("秦")
        self.assertGreater(short, 0)
        self.assertIn(f"每军 −{per}HP/回合", s)
        self.assertIn("交战中也照扣", s)

    def test_truncation_gives_remaining_coordinates(self):
        w = _world()
        for i in range(6):                        # 造 6 处可见交战（秦都占着格）
            x, y = 6 + i, 6
            _tile(w, x, y, "楚")
            _army(w, "步", x, y, "秦", 100 + i, engaged=True)
            _army(w, "步", x, y, "楚", 200 + i, hp=50)
        s = mp_ai._fmt_battle(w, "秦")
        self.assertIn(f"【交战】{6} 处", s)          # 表头报**总数**（展开 4 + 折叠 2）
        self.assertIn("另 2 处", s)
        self.assertIn(f"({7 + mp_ai.BATTLE_CELL_CAP},7)", s)   # 剩余坐标给全（1-based），否则 AI 无从下手

    def test_dispatch_and_schema_enum(self):
        w = _world()
        self.assertIn("当前没有交战",
                      mp_ai.execute(w, "秦", "query", {"panel": "battle"}))
        q = next(t for t in mp_ai.TOOL_SCHEMAS if t["function"]["name"] == "query")
        self.assertIn("battle",
                      q["function"]["parameters"]["properties"]["panel"]["enum"])

    def test_retreating_army_is_flagged_with_its_cover(self):
        w = _world()
        _tile(w, 6, 6, "楚")
        a = _army(w, "步", 6, 6, "秦", 1, engaged=True)
        a["retreat_to"] = [5, 6]
        a["retreat_cover"] = 100
        a["retreat_role"] = "攻"
        _army(w, "步", 6, 6, "楚", 2, hp=60)
        s = mp_ai._fmt_battle(w, "秦")
        self.assertIn("⚑撤退中", s)
        self.assertIn("进攻撤退", s)


class TestBattleBrief(unittest.TestCase):
    def test_no_battle_no_line(self):
        w = _world()
        self.assertEqual(mp_ai.battle_brief(w, "秦"), "")
        self.assertNotIn("⚔", mp_ai.compact_state(w, "秦"))     # 不白占 token

    def test_brief_reports_hp_totals_and_defender_soak(self):
        w = _world()
        _tile(w, 6, 6, "楚", terrain="丘陵")
        _army(w, "步", 6, 6, "秦", 1, engaged=True)
        _army(w, "步", 6, 6, "楚", 2, hp=90)
        b = mp_ai.battle_brief(w, "秦")
        self.assertIn("你攻(7,7)", b)                 # 1-based 呈现
        self.assertIn("你1支100HP", b)
        self.assertIn("守1支90HP", b)                 # 双方 HP 合计：答"这场仗读起来是不是在赢"
        self.assertIn("守方减伤25%", b)
        self.assertIn("panel=battle", b)              # 指路到明细
        self.assertIn("⚔", mp_ai.compact_state(w, "秦"))

    def test_brief_is_bounded(self):
        w = _world()
        for i in range(6):
            x, y = 6 + i, 6
            _tile(w, x, y, "楚")
            _army(w, "步", x, y, "秦", 100 + i, engaged=True)
            _army(w, "步", x, y, "楚", 200 + i, hp=50)
        b = mp_ai.battle_brief(w, "秦")
        self.assertLessEqual(len(b), 220, "摘要必须长度有界（它每次行动都挂）")
        self.assertIn("另", b)


class TestNoProbabilitySolver(unittest.TestCase):
    """★ 结构性护栏：面板**不做胜率**——不许把规则 AI 的战斗评估引进 mp_ai。

    用户 2026-10-07 否掉了接 RL 战斗 DP：「有点作弊了」；项目元规律「给事实不给判断」。
    （`mp_ai` 本来就 import `rule_ai`（规则 AI 注册表），所以只禁**战斗求解器**那一支。）
    """

    def test_mp_ai_does_not_import_combat_solver(self):
        tree = ast.parse((ROOT / "mp_ai.py").read_text(encoding="utf-8"))
        banned = ("ruleai", "combat_probs")
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                for b in banned:
                    self.assertNotIn(b, m, f"mp_ai.py 不该引入战斗求解器：{m}")


if __name__ == "__main__":
    unittest.main()
