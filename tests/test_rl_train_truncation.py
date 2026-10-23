# -*- coding: utf-8 -*-
"""★ 训练收尾：截断**不产生伪终局**，终局口径**只有一份**（2026-10-22 重写后的第三道闸）。

★ 两条线：
  ① **截断 ≠ 终局**：被步数预算截断的一局，**不许**凭空造出结果
    （旧口径有一处会伪造 −0.5 的"平局"）⇒ 结果只由**真终局**给。
  ② **终局口径单一**：赢 / 输 / 平 / **亡国** 都走**同一个**函数
    （`Sandbox.reward`），训练侧只**转发**，不另算一份。

★ §9 第 1 条的裁定（2026-10-22 用户拍板）：
  **平局判定下，已亡国的一方按 −1（输）** ——
  亡国就是输（native 的 `native:succession` 已经这么判了），
  两处口径不一致会让"早死比输划算" ⇒ 模型会学出"主动送死"。
"""
from __future__ import annotations

import unittest

from rl import scoring as S


def _sandbox(**kw):
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=kw.pop("seed", 5), size=kw.pop("size", 8), **kw)
    sb.reset()
    return sb


class TestTruncationIsNotTerminal(unittest.TestCase):
    """① 截断不产生伪终局。"""

    def test_truncated_episode_has_no_outcome(self):
        from rl.sandbox import Sandbox as _SB
        sb = _sandbox()
        # ★ 未完局（`global_terminal=False`）且谁都还活着 ⇒ **不伪造结果**。
        for name in sb.players:
            self.assertIsNone(sb.outcome_for(name, global_terminal=False),
                              f"{name} 在没打完的局里拿到了结果")

    def test_truncated_but_entity_out_is_a_loss(self):
        from rl.sandbox import Sandbox as _SB
        sb = _sandbox()
        # ★ 唯一"截断了也算已定"的情形：**我的实体已经出局**（那是真输）。
        site = sb.env.tile(*sb.core_of("乙")).tile_id
        sb.env.call("native:territory", "transfer", site, sb.env.id_of("甲"))
        out = sb.outcome_for("乙", global_terminal=False)
        self.assertIsNotNone(out, "实体出局了却没判负")
        self.assertLess(out, 0.0, "实体出局却判成正数")

    def test_real_terminal_gives_winner_and_loser(self):
        # ★ 两国局：拔了乙的厅 ⇒ 场上只剩**一个**实体 ⇒ 终局。
        #   （三国局里丙还活着 ⇒ 只是"两个实体"，**不是**终局 —— 那是另一个测试的局。）
        sb = _sandbox(n_nations=2)
        # ★ 真终局：把乙的厅转给甲 ⇒ 场上只剩一个实体 ⇒ 甲赢。
        site = sb.env.tile(*sb.core_of("乙")).tile_id
        sb.env.call("native:territory", "transfer", site, sb.env.id_of("甲"))
        self.assertTrue(sb.done(), "拔了对方的厅却没终局")
        self.assertGreater(sb.reward("甲"), 0.0, "赢家拿的不是正数")
        self.assertEqual(sb.reward("乙"), S.REWARD_LOSS, "输家没按 −1 算")


class TestTerminalPricingIsSingleSourced(unittest.TestCase):
    """② 终局口径只有一份；§9 的亡国裁定生效。"""

    def test_draw_with_living_player(self):
        """★ 三国局、两个实体都还在 ⇒ **平局**（活着的一方拿"平"的价）。"""
        sb = _sandbox(n_nations=3)
        sb.t_max = 0                      # ★ 立刻到时限
        self.assertIsNone(sb.winner(), "还没打完就有胜方了")
        for name in sb.players:
            self.assertEqual(sb.reward(name), S.REWARD_DRAW,
                             f"{name} 在平局里拿的不是平的价")

    def test_dead_nation_in_a_draw_is_a_loss(self):
        """★★ §9 第 1 条：**亡国方在平局里按 −1**（不是 −0.5、不是 0）。"""
        sb = _sandbox(n_nations=3)
        sb.t_max = 0                      # ★ 平局
        # ★ 丙亡国（厅被甲占了）⇒ 甲、乙两个实体还在 ⇒ **平局**（不是甲独赢）。
        site = sb.env.tile(*sb.core_of("丙")).tile_id
        sb.env.call("native:territory", "transfer", site, sb.env.id_of("甲"))
        self.assertIsNone(sb.winner(), "拔了一家的厅却还剩一个胜方？")
        self.assertEqual(sb.reward("丙"), S.REWARD_LOSS,
                         "亡国方在平局里没有被判成输（会让'早死比输划算'）")
        self.assertLess(S.REWARD_LOSS, S.REWARD_DRAW,
                        "输必须比平差（否则早死比输划算）")

    def test_train_outcome_matches_sandbox_reward(self):
        """★ 训练侧的结果**必须**等于沙盒的终局价（不另算一份）。"""
        from rl.sandbox import Sandbox as _SB
        sb = _sandbox()
        site = sb.env.tile(*sb.core_of("乙")).tile_id
        sb.env.call("native:territory", "transfer", site, sb.env.id_of("甲"))
        for name in sb.players:
            self.assertEqual(sb.outcome_for(name, global_terminal=True),
                             sb.reward(name),
                             f"{name} 的终局价两处不一致")


if __name__ == "__main__":
    unittest.main()
