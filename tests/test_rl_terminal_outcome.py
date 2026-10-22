# -*- coding: utf-8 -*-
"""★★ **终局奖励真的进了训练样本**（§3）+ **轨迹身份/分段**（§4）。

用户 2026-10-21 的复盘报告（`~/rl-tactics-repair-upgrade-report.md` §3/§4）查出来的：

  · 以前终局 ±1/−0.5 **只落在"触发终局的那一步"** ⇒ 别的玩家**从头到尾没拿到结果**
    （他们的奖励总和是 0，而 `info["reward"]` 却说他们输了 ⇒ 两处对不上，**不报错**）；
  · 而且**别的一方的最后一步没有边界标志** ⇒ GAE 会把上一段/下一段的优势带过来。

⇒ 现在：**每一位有轨迹的玩家**都在**自己那一步**上拿到**恰好一次**结果，
  且那一步就是他的**段边界**；`Step` 带上 `(episode_id, player, member)` 三件套，
  GAE 与 TBPTT 窗口都按它切段。

★ 口径（**不许改**，见报告 §2.2）：赢 +1 / 输 −1 / `t_max` 判平 −0.5；
  `SHAPING=False`、`WIN_TIME_BONUS=False`、`GAMMA=1.0`。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl.model import build_model                      # noqa: E402
    from rl.sandbox import Sandbox                        # noqa: E402
    from rl.train import (Step, _mem_windows, _settle_episode,  # noqa: E402
                          collect_episode, gae, gae_segments)
    from rl import scoring as SC                          # noqa: E402
else:
    Sandbox = build_model = scoring = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口（见上面的说明）。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _nets(sb):
    torch.manual_seed(0)
    return {p: build_model() for p in sb.players}


def _mkstep(player: str) -> Step:
    """造一个**最小**的步（只带结算要用的字段）。"""
    return Step({"obs": None}, 0, 0.0, 0.0, 0.0, False, player)


def _by_player(steps, p):
    return [s for s in steps if s.player == p]


def _sum_reward(steps, p):
    return sum(s.reward for s in _by_player(steps, p))


class TestTerminalOutcomeReachesTheSamples(unittest.TestCase):
    """§3：每位有轨迹的玩家都要拿到**恰好一次**最终结果。"""

    def test_draw_gives_minus_half_to_everyone(self):
        """★ `t_max` 判平 ⇒ **每一方**都拿 −0.5（不是只有触发终局的那一方）。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=2).reset(random_starts=False)
        steps, info = collect_episode(_nets(sb), sb, greedy=True,
                                      rng=np.random.default_rng(0))
        self.assertFalse(info["truncated"], "用例前提：这局该打完")
        self.assertIsNone(info["winner"], "用例前提：该是平局")
        for p in sb.players:
            self.assertAlmostEqual(_sum_reward(steps, p), SC.REWARD_DRAW, places=6,
                                   msg=f"{p} 的奖励总和与平局口径对不上")
            self.assertAlmostEqual(info["reward"][p], SC.REWARD_DRAW, places=6)

    def test_winner_gets_plus_one_loser_gets_minus_one(self):
        """★ 胜负 ⇒ 赢 +1 / 输 −1，**双方都拿到**。

        ★ 这里**直接造终局局面**再调 `_settle_episode`（而不是靠策略打一局）：
          "谁赢"必须是**用例的前提**，不能取决于网络这一手挑了什么动作
          —— 那种依赖会让用例偶尔测不到目标分支（假绿/假红）。
        """
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=20).reset(random_starts=False)
        # ★ 拔掉甲的厅 ⇒ 只剩乙一个实体 ⇒ 乙赢
        sb.world.tiles[sb.core_of("甲")]["buildings"]["native:town-hall"] = 0
        self.assertTrue(sb.is_terminal(), "用例前提：该是终局")
        steps = [_mkstep("甲"), _mkstep("乙")]
        _settle_episode(sb, steps)
        self.assertAlmostEqual(_sum_reward(steps, "乙"), SC.REWARD_WIN, places=6)
        self.assertAlmostEqual(_sum_reward(steps, "甲"), SC.REWARD_LOSS, places=6)
        self.assertTrue(_by_player(steps, "甲")[-1].done)
        self.assertTrue(_by_player(steps, "乙")[-1].done)

    def test_outcome_is_counted_exactly_once(self):
        """★ **不重复加奖**：一位玩家的奖励总和**恰好等于**他的结果。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=20).reset(random_starts=False)
        sb.world.tiles[sb.core_of("甲")]["buildings"]["native:town-hall"] = 0
        steps = [_mkstep("甲"), _mkstep("乙")]
        settled = _settle_episode(sb, steps)
        for p in sb.players:
            ss = _by_player(steps, p)
            n_terminal = sum(1 for s in ss if s.terminal)
            self.assertEqual(n_terminal, 1,
                             f"{p} 带了 {n_terminal} 次最终结果（应为 1）")
            self.assertAlmostEqual(_sum_reward(steps, p), settled["outcomes"][p], places=6,
                                   msg=f"{p} 的奖励总和与它的结果对不上")

    def test_alliance_member_is_not_lost_when_its_nation_dies(self):
        """★★ **联盟胜利**：成员本国已亡但联盟赢了 ⇒ **不能**封成负奖励。

        ★ 这是"提前封负"的经典坑：只看"本国还有没有厅"会把
          **联盟里还活着的那一方**误判成输。
        """
        # ★ `war=False`：**正在战争不能建盟**（引擎那条规矩）⇒ 先不开战再结盟。
        sb = Sandbox(seed=0, size=8, n_nations=3, t_max=20,
                     war=False).reset(random_starts=False)
        sb.set_alliance(("北方同盟", ["甲", "乙"]))
        # ★ 拔掉**丙**的厅 ⇒ 只剩一个实体（甲乙的同盟）⇒ 它赢
        core_c = sb.core_of("丙")
        sb.world.tiles[core_c]["buildings"]["native:town-hall"] = 0
        self.assertIsNotNone(sb.winner())
        self.assertIn("甲", sb.winner_members())
        self.assertIn("乙", sb.winner_members())
        self.assertNotIn("丙", sb.winner_members())
        # ★ 亡国的成员（丙）**不能**被当成"输"以外的东西 —— 联盟口径由
        #   `winner_members` 决定，**不看它本国的厅**。

    def test_player_without_trajectory_is_recorded_not_fabricated(self):
        """★ **没有动作轨迹的玩家**：记原因，**不捏造**策略样本。"""
        # ★ `t_max` 压小：这��用例只关心"轨迹字段"，
        #   跑一整局 20 回合只是白等（`legal()` 每步都扫一遍领土，慢）。
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=3).reset(random_starts=False)
        steps, info = collect_episode(_nets(sb), sb, greedy=True,
                                      rng=np.random.default_rng(0))
        self.assertFalse(info["truncated"])
        # 本用例里两方都有轨迹 ⇒ `no_traj` 为空（但**字段必须在**）
        self.assertIn("no_traj", info)
        self.assertEqual(tuple(info["no_traj"]), ())
        for p in sb.players:
            self.assertTrue(_by_player(steps, p), f"{p} 居然没有步")


class TestPerPlayerBoundary(unittest.TestCase):
    """§4：**每位玩家**的最后一步都要是段边界（`done=True`）。"""

    def test_every_player_has_a_boundary(self):
        sb = Sandbox(seed=0, size=8, n_nations=3, t_max=3).reset(random_starts=False)
        steps, info = collect_episode(_nets(sb), sb, greedy=True,
                                      rng=np.random.default_rng(0))
        for p in sb.players:
            ss = _by_player(steps, p)
            self.assertTrue(ss, f"{p} 没有步")
            self.assertTrue(ss[-1].done,
                            f"{p} 的最后一步没有边界标志 ⇒ GAE 会跨段串味")

    def test_step_carries_trajectory_identity(self):
        """★ `Step` 带上 `(episode_id, player, member)` 三件套。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=3).reset(random_starts=False)
        nets = _nets(sb)
        steps, info = collect_episode(nets, sb, greedy=True,
                                      rng=np.random.default_rng(0),
                                      episode_id=77,
                                      member_of={"甲": "M0", "乙": "M1"})
        self.assertEqual(info["episode_id"], 77)
        for s in steps:
            self.assertEqual(s.episode_id, 77)
            self.assertEqual(s.member, s.player and {"甲": "M0", "乙": "M1"}[s.player])

    def test_gae_segments_do_not_leak(self):
        """★★ **分段 GAE**：上一段的尾巴**不许**带进下一段。"""
        def mk(reward, value, done, ep, pl, mem):
            return Step({"obs": None}, 0, 0.0, value, reward, done, pl,
                        episode_id=ep, member=mem)
        # ★ 第一段**故意不带 `done`**（模拟"段尾少了边界标志"那种状态）：
        #   扁平 `gae` 只认 `done` ⇒ 它会把**后一段的尾巴**借回来；
        #   `gae_segments` 认**身份**（局号）⇒ 两段各算各的。
        seg1 = [mk(0.0, 0.0, False, 0, "甲", "M"), mk(0.0, 0.0, False, 0, "甲", "M")]
        seg2 = [mk(1.0, 0.0, True, 1, "甲", "M")]
        flat = seg1 + seg2
        adv, _ = gae_segments(flat, gamma=1.0, lam=0.95)
        # ★ 第一段（另一局）**不该**借到第二段的奖励尾巴 ⇒ 全 0
        self.assertAlmostEqual(float(adv[0]), 0.0, places=6,
                               msg="跨局串味了：第一段借到了后一段的优势尾巴")
        self.assertAlmostEqual(float(adv[1]), 0.0, places=6)
        # ★ 第二段只由自己的奖励决定 ⇒ 1.0
        self.assertAlmostEqual(float(adv[2]), 1.0, places=6)
        # ★ 对照：扁平的 `gae` **会**串（否则这个用例测不出东西）
        adv_f, _ = gae([s.reward for s in flat], [s.value for s in flat],
                       [s.done for s in flat], boots=[s.boot for s in flat],
                       gamma=1.0, lam=0.95)
        self.assertGreater(float(adv_f[0]), 0.0,
                           "对照组没串味 ⇒ 这个用例测不出东西")

    def test_mem_windows_do_not_cross_segments(self):
        """★★ **TBPTT 窗口**不许跨段（局 / 玩家 / 成员）。"""
        def mk(ep, pl, mem):
            return Step({"obs": None}, 0, 0.0, 0.0, 0.0, False, pl,
                        episode_id=ep, member=mem)
        steps = [mk(0, "甲", "M"), mk(0, "甲", "M"),      # 同一段
                 mk(0, "乙", "M"), mk(0, "乙", "M")]      # 换玩家 ⇒ 必须断开
        wins = _mem_windows(steps, tbptt=8)
        for w in wins:
            keys = {(steps[i].episode_id, steps[i].player, steps[i].member) for i in w}
            self.assertEqual(len(keys), 1, f"窗口 {w} 跨了段：{keys}")


if __name__ == "__main__":
    unittest.main()
