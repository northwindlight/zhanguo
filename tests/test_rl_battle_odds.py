# -*- coding: utf-8 -*-
"""★ 战斗概率**只有一份实现**：引擎的 `research:combat-probabilities`（第五道闸）。

★★ 2026-10-22 重写（review §2.4）
  ────────────────────────────────────────────
  旧 `rl/combat_probs.py`（836 行）+ `rl/_combat_fast.c`（999 行）是
  `mods/experimental/combat_research/` 的**同源复制**
  （那份 README 明写「来源为 `origin/feat/rl-tactics` 的 `rl/combat_probs.py`」）
  ⇒ 引擎的摊分/减伤/撤退规则改一次要改**两处**，而**改漏了不报错**。

  ⇒ 现在 RL 侧**只有转发**（`rl/odds.py` → `rl:odds` → `research:combat-probabilities`），
    本测试钉住：**RL 看到的数字与引擎给的数字逐位相等**。
"""
from __future__ import annotations

import unittest


def _battle_sandbox():
    """摆一个**能打起来**的局面：两国各一支兵，贴在一起。"""
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=4, size=8, n_nations=2, war=True, territory=False,
                 starts={"甲": (1, 1), "乙": (5, 5)}).reset(random_starts=False)
    env = sb.env
    # ★ 用 native 的 `seed`（初始化/导入）摆位置：不消耗行动额度。
    for unit in list(env.armies()):
        env.call("native:army", "disband", unit.owner, unit.id)
    # ★ 目标格 (2,2) 在**甲的视野内**（甲的核心 (1,1) 的八邻），
    #   敌方那支兵就站在那儿 ⇒ 这一格"甲看得见"，引擎才答得出评估。
    env.call("native:army", "seed", env.id_of("甲"), "native:infantry", 1, 1, 1)
    env.call("native:army", "seed", env.id_of("乙"), "native:infantry", 2, 2, 1)
    # ★ **真的打起来**（native 的 `attack` ⇒ 两边 `engaged`）——
    #   否则这一格只是"兵站在一起"，不是引擎认的交战格。
    mine = [u for u in env.armies() if env.owner_of(u) == "甲"]
    env.call("native:army", "attack", env.id_of("甲"), [u.id for u in mine], 2, 2)
    return sb


class TestOddsComeFromTheEngine(unittest.TestCase):
    """★ 战斗概率逐位等于引擎能力。"""

    def test_odds_service_forwards_to_the_engine_capability(self):
        from rl.env import Env
        env = Env.load()
        self.assertIn("research:combat-probabilities", env.rt.capabilities)
        # ★ `rl:odds` 就是转发（声明里写明了唯一出处）。
        mod = env.rt.units["rl:train"].mod
        self.assertIn("research:combat-probabilities", mod.declaration["Interfaces"])

    def test_engine_assess_is_deterministic(self):
        """★ 同一局面连问两次 ⇒ 数字**逐位相同**（否则模型在跟噪声学）。"""
        sb = _battle_sandbox()
        env = sb.env
        mine = [u for u in env.armies() if env.owner_of(u) == "甲"]
        ids = [u.id for u in mine]
        a = env.assess("甲", ids, 2, 2)
        b = env.assess("甲", ids, 2, 2)
        self.assertEqual(a, b, "引擎的评估不是确定性的")

    def test_rl_odds_match_the_engine_bit_for_bit(self):
        """★★ 迁移对拍：RL 层看到的 `p_win` 与引擎给的**逐位相等**。

        ★ 这就是"删掉 RL 那份 DP"之后必须成立的**等价性**证据。
        """
        from rl import odds as O
        sb = _battle_sandbox()
        env = sb.env
        mine = [u for u in env.armies() if env.owner_of(u) == "甲"]
        ids = [u.id for u in mine]
        # ★ 用**适配层自己的**提问方与部队（不复制它的规则 ⇒ 迁移对拍才有意义）。
        actor = O.actor_of(env, 2, 2)
        self.assertIsNotNone(actor, "这一格没有提问方")
        native = env.assess(actor, O.ids_of(env, 2, 2, actor), 2, 2)
        self.assertTrue(native.get("known"), "这一格可见 ⇒ 引擎应当给得出评估")
        rl = O.cell_odds(env, 2, 2)
        self.assertIsNotNone(rl, "RL 侧没拿到这一格的评估")
        p = native["probabilities"]
        # ★ 引擎的归属键是**内容 ID**（`rl:n0`），RL 侧一律用**短名**（甲/乙）⇒
        #   对拍时按**短名**比（顺带钉住"RL 侧不出现内容 ID"这件事）。
        for owner, value in p["p_win"].items():
            name = env.name_of(owner)
            self.assertAlmostEqual(rl.p_win.get(name, 0.0), value, places=12,
                                   msg=f"{name} 的胜率与引擎不一致")
        for owner, value in p["p_lose"].items():
            name = env.name_of(owner)
            self.assertAlmostEqual(rl.p_lose.get(name, 0.0), value, places=12,
                                   msg=f"{name} 的败率与引擎不一致")
        for key in rl.p_win:
            self.assertNotIn(":", key, "RL 侧的归属键不该是内容 ID")
        self.assertAlmostEqual(rl.p_draw, p["p_draw"], places=12, msg="平局率不一致")
        self.assertAlmostEqual(rl.e_rounds, p["expected_rounds_partial"], places=12,
                               msg="期望轮数不一致")
        self.assertEqual(rl.n_states, p["n_states"], "状态数不一致")

    def test_probabilities_are_sane(self):
        """★ 概率不许越界；未收敛的质量要**看得见**（不是悄悄归一化掉）。"""
        from rl import odds as O
        sb = _battle_sandbox()
        env = sb.env
        ids = [u.id for u in env.armies() if env.owner_of(u) == "甲"]
        native = env.assess("甲", ids, 2, 2)
        p = native["probabilities"]
        # ★ 逐个概率都在 [0,1]（**多方时各家之和可以 > 1** ——
        #   引擎的口径是"我活着且我的敌人都死了"，不是互斥事件 ⇒ 不能加总断言）。
        for table in (p["p_win"], p["p_lose"]):
            for value in table.values():
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0 + 1e-9, "单个概率越界")
        self.assertGreaterEqual(p["p_draw"], 0.0)
        self.assertLessEqual(p["p_draw"], 1.0)
        self.assertLessEqual(p["unresolved"], 1e-6, "这一局没收敛")

    def test_unknown_target_is_reported_not_guessed(self):
        """★ 看不见的目标 ⇒ 引擎说"不知道"，RL **不许**编一个数出来。"""
        from rl import odds as O
        sb = _battle_sandbox()
        env = sb.env
        ids = [u.id for u in env.armies() if env.owner_of(u) == "甲"]
        far = env.assess("甲", ids, 7, 7)          # ★ 那一格甲看不见
        self.assertFalse(far.get("known"), "看不见的格子被当成已知的了")
        rl = O.cell_odds(env, 7, 7)
        self.assertIsNone(rl, "看不见的格子却给出了评估")

    def test_no_local_dp_implementation_remains(self):
        """★ RL 侧**不许**再有战斗 DP 的实现（那会和引擎漂移）。"""
        import pathlib
        rl = pathlib.Path(__file__).resolve().parent.parent / "rl"
        self.assertFalse((rl / "combat_probs.py").exists(), "RL 的战斗 DP 还在")
        self.assertFalse((rl / "_combat_fast.c").exists(), "RL 的 C 扩展还在")
        for path in rl.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("def equal_allocation", text,
                             f"{path.name} 里有一份自己的伤害分摊实现")


if __name__ == "__main__":
    unittest.main()
