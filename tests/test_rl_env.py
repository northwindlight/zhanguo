# -*- coding: utf-8 -*-
"""RL 环境测试：合法动作清单必须**真的合法**，奖励必须等于总消费增量，外交必须不存在。

全部用合成小局（map_size=12、turns=6、单国独局），不碰任何真实存档。
"""
from __future__ import annotations

import random
import unittest

import numpy as np

from mp import World
from rl.env import ZhanguoEnv


class TestLegalActions(unittest.TestCase):
    """核心不变式：env 枚举出来的每个动作，引擎都得接受。"""

    def test_every_legal_action_is_accepted(self):
        for seed in (0, 1, 2):
            env = ZhanguoEnv(map_size=12, seed=seed, max_turns=6)
            obs = env.reset()
            rng = random.Random(seed)
            for _ in range(60):
                acts = obs.cand["actions"]
                self.assertTrue(acts, "候选清单不能为空")
                self.assertEqual(acts[-1].kind, "end_turn", "end_turn 必须在清单里")
                a = acts[rng.randrange(len(acts))]
                ok, msg = env._apply(a)
                self.assertTrue(ok, f"引擎拒绝了 env 给出的合法动作 {a.label()}：{msg}")
                obs, _r, done, _info = env.step(a)
                if done:
                    break

    def test_candidates_match_observation(self):
        env = ZhanguoEnv(map_size=12, seed=3, max_turns=6)
        obs = env.reset()
        k = len(obs.cand["actions"])
        self.assertEqual(obs.grid.shape, (len(env.obs_channels()), 12, 12))
        self.assertEqual(obs.glob.shape, (env.glob_size(),))
        for key in ("type_idx", "sub_idx", "tile_idx", "army_idx", "amount_idx"):
            self.assertEqual(obs.cand[key].shape, (k,), key)
        self.assertTrue(obs.cand["mask"].all())
        # 地块下标要么落在图上，要么是 null（= 地块数）
        self.assertTrue(((obs.cand["tile_idx"] >= 0) & (obs.cand["tile_idx"] <= 144)).all())


class TestCastleLevels(unittest.TestCase):
    """城堡造价是逐级表，满级后不得再出现在候选里。

    回归：曾经「先取价、后判满级」，城堡升满 L5 后再枚举就会 `cost[5]` 越界，
    训练跑了半小时才崩——这类边界必须有测试。
    """

    def test_castle_max_level_does_not_crash(self):
        env = ZhanguoEnv(map_size=12, seed=11, max_turns=6)
        env.reset()
        w, me = env.world, env.agent
        w.add_res(me, "黄金", 200000)
        w.add_res(me, "木头", 20000)
        (x, y) = w.own_tiles(me)[0]
        w.tiles[(x, y)]["buildings"]["城堡"] = 5          # 顶到满级
        acts = env.legal_actions()                        # 不该抛 IndexError
        self.assertFalse([a for a in acts
                          if a.kind == "build" and a.sub == "城堡" and a.tile == (x, y)],
                         "满级城堡不该还能下单选它")

    def test_castle_pending_counts_toward_max(self):
        env = ZhanguoEnv(map_size=12, seed=12, max_turns=6)
        env.reset()
        w, me = env.world, env.agent
        w.add_res(me, "黄金", 200000)
        w.add_res(me, "木头", 20000)
        (x, y) = w.own_tiles(me)[0]
        w.tiles[(x, y)]["buildings"]["城堡"] = 4
        w.tiles[(x, y)]["pending"]["城堡"] = 1            # 在建也算：eff=5=满级
        acts = env.legal_actions()
        self.assertFalse([a for a in acts
                          if a.kind == "build" and a.sub == "城堡" and a.tile == (x, y)])


class TestMarketCandidates(unittest.TestCase):
    """市场候选**不得被类别限额截断**。

    回归：候选按「商品 × 数量档」成网格生成（同一商品的动作连在一起），
    cap=24 时只装得下 2.4 个商品，排在后头的「粮食/补给」在某一步**根本不存在**
    ——策略连"买粮食"都表达不出来（BC 时表现为 36% 的老师动作对不上候选）。
    """

    def test_every_good_is_representable(self):
        from game import TRADEABLE
        env = ZhanguoEnv(map_size=12, seed=21, max_turns=6)
        env.reset()
        w, me = env.world, env.agent
        w.add_res(me, "黄金", 500000)
        for g in TRADEABLE:
            w.add_res(me, g, 200)
        acts = env.legal_actions()
        sell = {a.sub for a in acts if a.kind == "sell"}
        buy = {a.sub for a in acts if a.kind == "buy"}
        self.assertEqual(sell, set(TRADEABLE),
                         f"这些商品卖不出去（候选被截断）：{set(TRADEABLE) - sell}")
        self.assertEqual(buy, set(TRADEABLE),
                         f"这些商品买不进来（候选被截断）：{set(TRADEABLE) - buy}")


class TestVision(unittest.TestCase):
    """观测必须走引擎视野（fog of war），不能是全图。"""

    def test_obs_is_fog_gated(self):
        env = ZhanguoEnv(map_size=12, seed=5, max_turns=6)
        obs = env.reset()
        vis = env._vision_mask()
        terrain = obs.grid[:5].sum(0)              # 地形 one-hot 求和
        self.assertTrue(((terrain > 0) & (vis < 0.5)).sum() == 0, "视野外不该有地形")
        self.assertTrue(np.allclose(obs.grid[-1], vis), "visible 通道应等于视野掩码")
        self.assertGreater(int(vis.sum()), 0)
        self.assertLess(int(vis.sum()), 12 * 12, "有雾：不可能全图可见")
        # 走几步后仍然成立
        for _ in range(40):
            a = obs.cand["actions"][0]
            obs, _r, done, _info = env.step(a)
            if done:
                break
        vis2 = env._vision_mask()
        terrain2 = obs.grid[:5].sum(0)
        self.assertTrue(((terrain2 > 0) & (vis2 < 0.5)).sum() == 0, "行进中也不该看到视野外")


class TestReward(unittest.TestCase):
    def test_reward_sums_to_final_spend(self):
        """Σ 每步奖励 ≡ 终局总消费 —— 密集奖励与目标函数逐分相等。"""
        env = ZhanguoEnv(map_size=12, seed=7, max_turns=6,
                         reward_scale=1.0)
        obs = env.reset()
        rng = random.Random(7)
        total_r = 0.0
        info = {}
        for _ in range(4000):
            a = obs.cand["actions"][rng.randrange(len(obs.cand["actions"]))]
            obs, r, done, info = env.step(a)
            total_r += r
            if done:
                break
        self.assertTrue(done, "回合上限内必须结束")
        self.assertAlmostEqual(total_r, info["spend_total"], places=4)

    def test_spend_is_monotonic(self):
        env = ZhanguoEnv(map_size=12, seed=9, max_turns=5)
        obs = env.reset()
        rng = random.Random(9)
        last = 0.0
        for _ in range(4000):
            a = obs.cand["actions"][rng.randrange(len(obs.cand["actions"]))]
            obs, _r, done, info = env.step(a)
            self.assertGreaterEqual(info["spend_total"] + 1e-9, last, "总消费不得回退")
            last = info["spend_total"]
            if done:
                break


class TestNoDiplomacy(unittest.TestCase):
    """外交功能必须从引擎里彻底消失。"""

    GONE = ("send_mail", "mystery_letter", "gift", "share_map", "spy", "propose_pact",
            "accept_pact", "reject_pact", "break_pact", "declare_guarantee",
            "cancel_guarantee", "declare_war", "offer_peace", "accept_peace",
            "reject_peace", "propose_bloc", "bloc_join", "bloc_leave", "bloc_rename",
            "bloc_transfer", "bloc_dissolve", "cast_vote", "bloc_of", "allied_between",
            "war_between", "at_war")

    def test_engine_has_no_diplomacy(self):
        for name in self.GONE:
            self.assertFalse(hasattr(World, name), f"World.{name} 应当已删除")

    def test_diplomacy_building_gone(self):
        from game import BUILDINGS
        self.assertNotIn("外交中心", BUILDINGS)

    def test_nations_cannot_attack_each_other(self):
        """永久中立：对他国领土的进攻必须被拒。"""
        w = World(size=12, seed=11, nations=["秦", "楚"])
        me = "秦"
        foe = [t for t, d in w.tiles.items() if d["owner"] == "楚"]
        self.assertTrue(foe)
        x, y = foe[0]
        # 直接在邻格造一支我方军队，再尝试进攻
        for (ax, ay) in w.neighbors(x, y):
            if w.owned_by(ax, ay) in (None, me):
                w.add_res(me, "黄金", 10000)
                w.add_res(me, "粮食", 10000)
                w.add_res(me, "装备", 10000)
                w.tiles[(ax, ay)] = w._new_tile(ax, ay, me)
                w.tiles[(ax, ay)]["buildings"]["兵营"] = 1
                ok, _ = w.recruit(me, ax, ay, 1, "步")
                self.assertTrue(ok)
                aid = w.nation_armies(me)[0]["id"]
                ok, _msg = w.attack(me, [aid], x, y)
                self.assertFalse(ok, "中立国领土不该能进攻")
                return
        self.skipTest("找不到合适的邻格")


if __name__ == "__main__":
    unittest.main()
