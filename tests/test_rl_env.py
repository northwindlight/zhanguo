# -*- coding: utf-8 -*-
"""RL 环境测试：合法动作清单必须**真的合法**，奖励必须等于总消费增量，外交不在这条线上。

全部用合成小局（map_size=12、turns=6、单国独局），不碰任何真实存档。
"""
from __future__ import annotations

import random
import unittest

import numpy as np

from mp import World
from rl import vocab as V
from rl.env import KINDS, ZhanguoEnv


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
        h, w = obs.grid.shape[1], obs.grid.shape[2]
        # ★观测是「**可见区外接框**」，不是整幅地图（用户 2026-09-11 定的方案）：
        #   成本 O(可见区)、与地图尺寸无关；数组形状反映的是帝国的铺开程度，
        #   所以既不泄漏地图尺寸，也不暴露「我在地图哪个位置」。
        self.assertEqual(obs.grid.shape, (len(env.obs_channels()), h, w))
        self.assertLess(h * w, 12 * 12, "开局可见区必然远小于全图（否则就是没裁）")
        self.assertEqual(obs.glob.shape, (env.glob_size(),))
        for key in ("type_idx", "sub_idx", "tile_dx", "tile_dy", "army_idx", "amount_idx"):
            self.assertEqual(obs.cand[key].shape, (k,), key)
        self.assertTrue(obs.cand["mask"].all())
        # 落点用**框内相对坐标**（因为逐帧尺寸可变，扁平下标拼批后必然错位）：
        # 要么落在框内，要么 -1 = 这个候选没有落点（buy/sell/end_turn）
        dx, dy = obs.cand["tile_dx"], obs.cand["tile_dy"]
        self.assertTrue(((dx >= -1) & (dx < h)).all(), "落点 x 越出外接框")
        self.assertTrue(((dy >= -1) & (dy < w)).all(), "落点 y 越出外接框")
        self.assertTrue(((dx == -1) == (dy == -1)).all(), "dx/dy 的空值必须成对")

    def test_home_is_origin_anchor(self):
        """家必须**恰好**标出一格，且观测坐标以它为原点。

        用户口径：「对他而言，家中心永远是 0,0」—— 引擎用绝对坐标，env 负责翻译。
        所以家通道只能有一格热，且它换算回绝对坐标必须等于 `env.anchor`。
        """
        for n in (12, 20, 32):
            env = ZhanguoEnv(map_size=n, seed=11, max_turns=4)
            obs = env.reset()
            hc = obs.grid[env.obs_channels().index("home")]
            self.assertEqual(int((hc > 0).sum()), 1, f"{n}×{n}: 家通道该只有一格热")
            hx, hy = np.unravel_index(int(hc.argmax()), hc.shape)
            x0, y0, _x1, _y1 = env._bbox
            self.assertEqual((int(hx) + x0, int(hy) + y0), env.anchor,
                             f"{n}×{n}: 家通道的位置换算回绝对坐标不对")


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
        x0, y0, x1, y1 = env._bbox
        vis = env._vision_mask()[x0:x1 + 1, y0:y1 + 1]   # ★裁到同一外接框才可比
        terrain = obs.grid[:5].sum(0)                    # 地形 one-hot 求和
        self.assertTrue(((terrain > 0) & (vis < 0.5)).sum() == 0, "视野外不该有地形")
        vch = obs.grid[env.obs_channels().index("visible")]   # ← 按名字取，别用 [-1]
        self.assertTrue(np.allclose(vch, vis), "visible 通道应等于裁过的视野掩码")
        self.assertGreater(int(vis.sum()), 0)
        self.assertLess(int(vis.sum()), 12 * 12, "有雾：不可能全图可见")
        # 走几步后仍然成立
        for _ in range(40):
            a = obs.cand["actions"][0]
            obs, _r, done, _info = env.step(a)
            if done:
                break
        x0, y0, x1, y1 = env._bbox
        vis2 = env._vision_mask()[x0:x1 + 1, y0:y1 + 1]
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


class TestDiplomacyOutOfScope(unittest.TestCase):
    """**外交留在引擎里，但不进 RL 这条线**（2026-09-12 变基口径，取代原来的「引擎里删干净」）。

    为什么改口径：砍外交对 RL 没有收益——训练是单国独局，外交代码路径一次都不触发，
    却要每次跟着 main 变基重做一遍外科手术。现在引擎（mp.py/game.py/mp_ai.py/mp_run.py）
    与 main 逐字相同，代价是**外交中心这项建筑回到了 game.BUILDINGS**（16 项）。

    于是防线从「引擎里没有外交」换成两条**真正会咬人**的不变式：
    ① RL 的动作种类里不许有外交动作；
    ② RL 的观测/动作子表不许把「外交中心」算进去 —— 它一旦进观测，网格与全局
       宽度就从 36/48 变 37/49，**已训好的 ckpt 全部加载不了**（rl/PLAN.md 的重炼铁律）。
    """

    DIPLOMACY_KINDS = ("send_letter", "gift", "share_map", "spy", "bloc_found",
                       "bloc_join", "bloc_leave", "vote", "propose",
                       "declare_war", "offer_peace", "accept_peace")

    def test_no_diplomacy_action_kinds(self):
        for k in self.DIPLOMACY_KINDS:
            self.assertNotIn(k, KINDS, f"RL 动作空间里不该有外交动作 {k}")

    def test_diplomacy_building_not_in_observation(self):
        env = ZhanguoEnv(map_size=12, max_turns=6)
        self.assertEqual(list(env.bnames), list(V.OBS_BUILDING))
        self.assertNotIn("外交中心", env.bnames,
                         "外交中心进了观测子表 —— 观测宽度会跟着引擎变，ckpt 会废")
        # 宽度是 ckpt 的硬契约（§10.6）：**45 网格通道 / 58 全局**
        #   45 = 36 + 4（建筑留位）+ 2（§9 记忆预留）+ 2（地形留位）+ 1（建造成本通道）
        #   58 = 48 + 4（建筑留位）+ 2+2（物资留位：price/eq）+ 2（兵种留位：army_kind）
        self.assertEqual(len(env.obs_channels()), 45)
        self.assertEqual(env.glob_size(), 58)
        self.assertIn("build_cost", env.obs_channels())

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
