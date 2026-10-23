# -*- coding: utf-8 -*-
"""★ 审查（2026-10-23 下午）十一条缺陷的**回归闸门**。

| # | 缺陷 | 本文件对应 |
|---|---|---|
| P1-1 | 采样评估完全忽略策略概率 | `TestSampling` |
| P1-2 | 灭国后无法继续争胜（战争被清空且不恢复） | `TestWarResume` |
| P1-3 | 评估时没带记忆（潜槽反复用初值） | `TestEvalMemory` |
| P1-4 | 观测泄露迷雾中的地形与城防 | `TestFogLeak` |
| P1-5 | 续训不恢复 Adam 状态 | `TestOptimizerResume` |
| P1-6 | 未到归档周期的快照学习在重启后丢失 | `TestHotSnapshotResume` |
| P1-7 | 步数预算可能永久饿死某个学习者 | `TestLearnerRotation` |
| P2-8 | 多玩家截断自举用了旧状态价值 | `TestTruncationBootstrap` |
| P2-9 | 评估静默混入随机网络 | `TestEvalPool` |
| P2-10 | 跨臂评估的首回合先手设置无效 | `TestHead2HeadFirst` |
| P2-11 | CUDA 对手模式漏搬对手网络 | `TestOpponentDevice` |
"""
from __future__ import annotations

import unittest

import numpy as np

from rl import encode, vocab as V
from rl.sampling import action_order
from tests.rl_deps import has_torch, torch, torch_or_skip


def _need_torch() -> None:
    """没装 torch ⇒ **整条跳过**（CI 就是这个环境）。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())


def _sandbox(**kw):
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=kw.pop("seed", 3), size=kw.pop("size", 8), **kw)
    sb.reset(random_starts=False)
    return sb


# ======================================================================
# P1-1 采样臂：按策略概率抽
# ======================================================================
class TestSampling(unittest.TestCase):
    """★ `--sample` 臂是用户定的**唯一判据** ⇒ 它必须真的按策略抽。"""

    def test_greedy_order_is_by_probability(self):
        p = np.array([0.1, 0.7, 0.2])
        order = action_order(p, np.random.default_rng(0), greedy=True)
        self.assertEqual(order[0], 1)
        self.assertEqual(order, [1, 2, 0])

    def test_sampling_follows_the_policy(self):
        """★ 强烈偏好某个动作时，**绝大多数**步都该选它（不是均匀随机）。"""
        p = np.array([0.9, 0.05, 0.05])
        rng = np.random.default_rng(12345)
        picks = [action_order(p, rng, greedy=False)[0] for _ in range(400)]
        rate = picks.count(0) / len(picks)
        self.assertGreater(rate, 0.8, f"采样臂几乎没按策略走（rate={rate:.2f}）")

    def test_uniform_policy_gives_uniform_samples(self):
        p = np.array([1 / 3, 1 / 3, 1 / 3])
        rng = np.random.default_rng(7)
        picks = [action_order(p, rng, greedy=False)[0] for _ in range(300)]
        for i in range(3):
            self.assertGreater(picks.count(i), 60, "均匀策略下采样应大致均匀")

    def test_order_covers_every_candidate_once(self):
        p = np.array([0.4, 0.3, 0.2, 0.1])
        rng = np.random.default_rng(3)
        order = action_order(p, rng, greedy=False)
        self.assertEqual(sorted(order), [0, 1, 2, 3])

    def test_rejected_action_is_resampled_from_the_rest(self):
        """★ 被引擎拒了 ⇒ **在剩下的里按概率重抽**（不是退化成均匀）。"""
        p = np.array([0.5, 0.3, 0.2])
        rng = np.random.default_rng(11)
        order = action_order(p, rng, greedy=False)
        self.assertEqual(len(order), 3)
        self.assertEqual(sorted(order), [0, 1, 2])


# ======================================================================
# P1-2 亡国之后：战争要能恢复
# ======================================================================
class TestWarResume(unittest.TestCase):
    """★ 引擎的亡国处理会**清空全部战争**并给幸存者停战。"""

    def test_engine_clears_wars_on_death(self):
        """★ 先把**引擎**的行为钉住（这是前提，不是我们要改的东西）。"""
        from tests.rl_deps import give_site
        sb = _sandbox(seed=3, size=8, n_nations=3, war=True)
        env = sb.env
        self.assertTrue(env.war_between("甲", "丙"))
        give_site(env, sb.core_of("乙"), None)
        env.resolve()
        self.assertNotIn("乙", env.nations())
        self.assertFalse(env.war_between("甲", "丙"), "引擎的亡国处理应清空战争")

    def test_sandbox_resumes_the_war_after_the_truce(self):
        """★ 停战期一过 ⇒ **沙盒替幸存者重新宣战**（RL 的动作空间没有"宣战"）。"""
        from tests.rl_deps import give_site
        sb = _sandbox(seed=3, size=8, n_nations=3, war=True)
        env = sb.env
        give_site(env, sb.core_of("乙"), None)
        env.resolve()
        self.assertNotIn("乙", env.nations())
        self.assertFalse(env.war_between("甲", "丙"))
        # ★ 停战期内**不许**宣战（引擎的规则，不绕过）。
        for _ in range(3):
            sb.end_turn()
        self.assertFalse(env.war_between("甲", "丙"), "停战期内就宣战 ⇒ 绕过了引擎规则")
        # ★ 停战期过（native 默认 10 期）⇒ 战争恢复。
        for _ in range(12):
            sb.end_turn()
        self.assertTrue(env.war_between("甲", "丙"),
                        "停战期过了还没恢复战争 ⇒ 幸存者再也打不起来")

    def test_resume_does_not_break_the_normal_flow(self):
        """★ 没人死的时候**不该**有多余动作（宣战流程一次都不做）。"""
        sb = _sandbox(seed=3, size=8, n_nations=3, war=True)
        env = sb.env
        before = env.period()
        sb.end_turn()
        self.assertEqual(env.period(), before + 1, "没死人就多推进了期号")
        self.assertTrue(env.war_between("甲", "丙"))


# ======================================================================
# P1-3 评估要带记忆
# ======================================================================
class TestEvalMemory(unittest.TestCase):
    """★ 记忆臂的评测必须走 `forward_state` + **逐国**的槽。"""

    def test_eval_uses_forward_state_and_passes_memory(self):
        _need_torch()
        from rl.eval_fixed import play
        from rl.model import build_model
        from rl import vocab as V

        net = build_model(mem_slots=V.M_SLOTS)
        calls = {"forward": 0, "state": 0, "mem_in": []}
        _fwd, _st = net.forward, net.forward_state

        def forward(batch):
            calls["forward"] += 1
            return _fwd(batch)

        def forward_state(batch, mem=None):
            calls["state"] += 1
            calls["mem_in"].append(None if mem is None else mem.detach().clone())
            return _st(batch, mem)

        net.forward = forward
        net.forward_state = forward_state
        sb = _sandbox(seed=3, size=8, n_nations=2, war=True)
        play({0: net}, sb, greedy=True, rng=np.random.default_rng(0))
        self.assertGreater(calls["state"], 0, "评估一次 forward_state 都没调 ⇒ 记忆等于没带")
        self.assertEqual(calls["forward"], 0, "评估走了普通 forward ⇒ 潜槽用的是初值")
        # ★ 槽必须**逐步传下去**（不是每次都用初值）。
        self.assertGreater(len(calls["mem_in"]), 1)
        first, second = calls["mem_in"][0], calls["mem_in"][1]
        self.assertFalse(torch.equal(first, second),
                         "每一步的 mem_in 都一样 ⇒ 槽没往前传")


# ======================================================================
# P1-4 迷雾里的地形/城防不许漏
# ======================================================================
class TestFogLeak(unittest.TestCase):
    """★ 看不见的格**不读真地形、不读真城防**。"""

    def test_invisible_cells_use_neutral_values(self):
        sb = _sandbox(seed=5, size=8, n_nations=2, war=True)
        env = sb.env
        mask = env.vision_mask("甲")
        grid = encode.encode_grid(sb, "甲", mask)
        x0, y0, h, w = encode.frame_of(sb, "甲", mask)
        n_invisible = 0
        for i in range(h):
            for j in range(w):
                x, y = x0 + j, y0 + i
                if not (0 <= x < env.size() and 0 <= y < env.size()):
                    continue
                if (x, y) in mask:
                    continue
                n_invisible += 1
                self.assertAlmostEqual(float(grid[V.GRID_DEFENSE, i, j]),
                                       encode._UNSEEN_DEFENSE, places=6,
                                       msg=f"{(x, y)} 看不见却写了真防御%")
                self.assertAlmostEqual(float(grid[V.GRID_MOVE, i, j]),
                                       encode._UNSEEN_MOVE, places=6,
                                       msg=f"{(x, y)} 看不见却写了真移动代价")
        self.assertGreater(n_invisible, 0, "这一局没有看不见的格 ⇒ 测不到")

    def test_invisible_terrain_does_not_change_the_encoding(self):
        """★★ 只改**不可见格**的地形 ⇒ 合法动作相同、**网格与候选输入也必须相同**。"""
        from tests.rl_deps import make_site
        sb1 = _sandbox(seed=5, size=8, n_nations=2, war=True)
        sb2 = _sandbox(seed=5, size=8, n_nations=2, war=True)
        env1, env2 = sb1.env, sb2.env
        mask = env1.vision_mask("甲")
        # ★ 找一个"甲看不见、又够不着"的格（在**够不着**这一条上很重要：
        #   山地会改移动代价 ⇒ 够得着的话合法动作就会变，测的就不是同一件事了）。
        cell = None
        for u in env1.armies():
            if env1.owner_of(u) != "甲":
                continue
            reach = env1.reachable("甲", u, for_attack=True)
            for x in range(env1.size()):
                for y in range(env1.size()):
                    if (x, y) in mask or env1.owner(x, y) is not None:
                        continue
                    if (x, y) in reach:
                        continue
                    cell = (x, y)
                    break
                if cell:
                    break
            if cell:
                break
        if cell is None:
            self.skipTest("这一局找不到'看不见又够不着'的格")
        make_site(env2, cell, None, terrain="native:mountain")
        # ★ 真值确实变了。
        self.assertEqual(env2.terrain(*cell), "native:mountain")
        self.assertNotEqual(env1.terrain(*cell), "native:mountain")
        # ★ 合法动作相同。
        self.assertEqual(sb1.legal(), sb2.legal(), "改地形不该改合法动作")
        obs1 = encode.obs_of(sb1, "甲")
        obs2 = encode.obs_of(sb2, "甲")
        self.assertTrue(np.array_equal(obs1["grid"], obs2["grid"]),
                        "网格里漏了不可见格的地形/城防")
        self.assertTrue(np.array_equal(obs1["cand"]["cand_content"],
                                       obs2["cand"]["cand_content"]),
                        "候选输入里漏了不可见格的地形")

    def test_candidate_terrain_is_neutral_when_invisible(self):
        from rl import features as F
        sb = _sandbox(seed=5, size=8, n_nations=2, war=True)
        env = sb.env
        mask = env.vision_mask("甲")
        acts = sb.legal()
        cand = encode.candidate_batch(sb, "甲", acts, mask)
        plain = F.terrain_vector("native:plain")
        n_checked = 0
        for i, (aid, kind, x, y) in enumerate(acts):
            if (x, y) in mask:
                continue
            n_checked += 1
            got = cand["cand_content"][i][-len(plain):]
            for a, b in zip(got, plain):
                self.assertAlmostEqual(float(a), float(b), places=6,
                                       msg=f"{(x, y)} 看不见却写了真地形")
        if n_checked == 0:
            self.skipTest("这一局的候选全在视野里 ⇒ 测不到不可见候选")


# ======================================================================
# P1-5 / P1-6 存档要带优化器状态与热快照
# ======================================================================
class TestOptimizerResume(unittest.TestCase):
    """★ 续训要**把 Adam 的动量读回来**（否则等于每轮重开 Adam）。"""

    def test_optimizer_state_survives_save_load(self):
        _need_torch()
        import tempfile
        from pathlib import Path
        from rl import train as T
        from rl.model import build_model

        net = build_model()
        opt = T._adam_for(net, 3e-4)
        # ★ 先走一步，让优化器里真的有状态（Adam 的 `exp_avg`/`exp_avg_sq`）。
        loss = sum(p.sum() for p in net.parameters())
        loss.backward()
        opt.step()
        opt.zero_grad()
        n_state = len(opt.state_dict()["state"])
        self.assertGreater(n_state, 0, "优化器里没有状态 ⇒ 测不到")

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ck.pt"
            T._save_ckpt(str(p), {0: net}, 1, meta=T._ckpt_meta(8, 8, True, None, 1, 60,
                                                                 mem_slots=0))
            blob = torch.load(p, map_location="cpu", weights_only=False)
            self.assertIn("opts", blob, "存档里没有优化器状态 ⇒ 续训等于重开 Adam")
            fresh = build_model()
            T._load_ckpt(str(p), {0: fresh}, mem_slots=0, log=lambda *a: None)
            got = getattr(fresh, "_ppo_opt", None)
            self.assertIsNotNone(got, "读档后优化器不存在 ⇒ 动量全丢")
            self.assertEqual(len(got.state_dict()["state"]), n_state)

    def test_command_line_lr_still_wins(self):
        """★ 存档里的 lr **不能盖住**命令行的 `--lr`。"""
        _need_torch()
        from rl import train as T
        from rl.model import build_model
        net = build_model()
        T._adam_for(net, 1e-3)
        T._adam_for(net, 5e-5)
        self.assertAlmostEqual(net._ppo_opt.param_groups[0]["lr"], 5e-5, places=9)


class TestHotSnapshotResume(unittest.TestCase):
    """★ "学过但还没冻结"的那批快照，重启后**不许回到旧指纹**。"""

    def test_hot_weights_are_saved_and_restored(self):
        _need_torch()
        import tempfile
        from pathlib import Path
        from rl import train as T
        from rl.league import League
        from rl.model import build_model

        with tempfile.TemporaryDirectory() as d:
            lg = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                        device="cpu", fingerprint=T._shape_fingerprint(0),
                        log=lambda *a: None)
            net = build_model()
            mid = "S00001_test"
            lg.add_snapshot(net, 1, mid=mid)
            lg.mark_hot(mid)
            # ★ 改一下**池子里那一份**的权重（模拟"学过"）—— 盘上那份还是旧的。
            #   （`add_snapshot` 存的是**副本** ⇒ 要改得改池子里那份。）
            held = lg.net_of(mid)
            with torch.no_grad():
                for p in held.parameters():
                    p.add_(1.0)
            wkey = next(iter(held.state_dict()))
            hot = lg.hot_weights()
            self.assertIn(mid, hot, "热快照没被导出 ⇒ 重启会丢")
            # ★★★ 2026-10-23（review P1-3）：热快照的形状从"**纯权重**"改成
            #   `{mid: {"weights": …, "opt": …}}`（**优化器状态也跟着走**）。
            #   ⇒ 这里按**新形状**断言；`restore_hot` 仍**兼容旧形状**
            #   （老存档能直接读）。
            self.assertIn("weights", hot[mid], "热快照里没有权重")
            self.assertFalse(
                torch.equal(hot[mid]["weights"][wkey], net.state_dict()[wkey]),
                "导出的还是旧权重 ⇒ 学到的东西没带走")
            ck = Path(d) / "ck.pt"
            T._save_ckpt(str(ck), {0: net}, 1,
                         meta=T._ckpt_meta(8, 8, True, None, 1, 60, mem_slots=0),
                         league=lg)
            blob = torch.load(ck, map_location="cpu", weights_only=False)
            self.assertIn("league_hot", blob, "存档里没有热快照的权重")
            # ★ 重新开一个池子，从存档灌回 ⇒ 权重必须是**新的**。
            lg2 = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                         device="cpu", fingerprint=T._shape_fingerprint(0),
                         log=lambda *a: None)
            lg2.add_snapshot(build_model(), 1, mid=mid)
            n = lg2.restore_hot(blob["league_hot"])
            self.assertEqual(n, 1)
            got = lg2.net_of(mid).state_dict()[wkey]
            self.assertTrue(torch.equal(got, held.state_dict()[wkey]),
                            "灌回来的还是旧权重 ⇒ 学习白学")


# ======================================================================
# P1-7 学习者轮转
# ======================================================================
class TestLearnerRotation(unittest.TestCase):
    """★ 学习者轮转按**实际开展的局数**算。"""

    def test_rotation_follows_played_games(self):
        _need_torch()          # ★ `rl.train` 顶部 import torch
        from rl.train import _learner_slot
        mids = ["L0", "L1"]
        self.assertEqual(_learner_slot(mids, 0), "L0")
        self.assertEqual(_learner_slot(mids, 1), "L1")
        self.assertEqual(_learner_slot(mids, 2), "L0")
        self.assertEqual(_learner_slot(mids, 3), "L1")

    def test_no_learner_is_starved_under_a_tight_budget(self):
        """★ 预算吃紧（每 iter 只开 1 局）⇒ **每个学习者都得上场**。"""
        _need_torch()          # ★ `rl.train` 顶部 import torch
        from rl.train import _learner_slot
        mids = ["L0", "L1", "L2"]
        played = 0
        seen = []
        for _it in range(3):                     # 三轮
            for _e in range(2):                  # 每轮计划 2 局
                if played >= 3:                  # 预算：只让开过 3 局
                    break
                seen.append(_learner_slot(mids, played))
                played += 1
        self.assertEqual(set(seen), set(mids), f"有学习者一局都没上场：{seen}")

    def test_planned_index_would_have_starved_someone(self):
        """★ 钉住"旧口径确实会饿死"（不然这条测试没意义）。"""
        mids = ["L0", "L1"]
        planned = []
        for it in range(3):
            for e in range(2):
                if e >= 1:                        # 预算：每 iter 只开第 0 局
                    break
                planned.append(mids[(it * 2 + e) % len(mids)])
        self.assertEqual(set(planned), {"L0"}, "旧口径（计划局号）本该饿死 L1")


# ======================================================================
# P2-8 多玩家截断的自举值
# ======================================================================
class TestTruncationBootstrap(unittest.TestCase):
    """★ 截断时**每个玩家**都该在**当前边界观测**上问一次网络。"""

    def test_every_player_gets_a_fresh_boundary_value(self):
        _need_torch()
        from rl import train as T
        from rl.model import build_model
        from rl.sandbox import Sandbox

        sb = Sandbox(seed=3, size=8, n_nations=3, t_max=5).reset(random_starts=False)
        steps = []
        # ★ 让**每个国家**都走几步（截断时每人都该拿到自己的边界值）。
        for turn in range(3):
            for me in list(sb.players):
                acts = sb.legal()
                if not acts:
                    continue
                obs = encode.obs_of(sb, me, acts)
                steps.append(T.Step(obs=obs, aidx=0, logp=-1.0, value=0.123,
                                    reward=0.0, done=False, player=me))
        nets = {p: build_model() for p in sb.players}
        settled = T._settle_episode(sb, steps, nets=nets, mem=None, device="cpu")
        self.assertTrue(settled["truncated"], "这一局应当是截断的（没打完）")
        boots = [s.boot for s in steps if s.boot is not None]
        # ★ 至少两个玩家拿到了**自举值**（不是只有一个）。
        self.assertGreaterEqual(len(boots), 2,
                                "只有一个玩家拿到边界值 ⇒ 其余在复用旧价值")
        # ★ 那个值**不是** `steps[i].value`（0.123）—— 说明是现问的。
        for b in boots:
            self.assertNotAlmostEqual(float(b), 0.123, places=6,
                                       msg="边界值 = 旧价值 ⇒ 截断自举目标不一致")


# ======================================================================
# P2-9 评测池不许混随机网
# ======================================================================
class TestEvalPool(unittest.TestCase):
    """★ 存档里几份就用几份，**不拿随机网凑数**。"""

    def test_pool_size_matches_the_checkpoint(self):
        _need_torch()
        import tempfile
        from pathlib import Path
        from rl import train as T
        from rl.eval_fixed import load_pool
        from rl.model import build_model

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ck.pt"
            nets = {i: build_model() for i in range(2)}
            T._save_ckpt(str(p), nets, 3, meta=T._ckpt_meta(8, 8, True, None, 2, 60,
                                                             mem_slots=0))
            logs = []
            pool = load_pool(str(p), 5, log=lambda m: logs.append(m))
            self.assertEqual(len(pool), 2, "要 5 份却只有 2 份 ⇒ 混进随机网了")
            self.assertTrue(any("不拿随机网凑数" in m for m in logs),
                            "没有把『存档里只有 2 份』这件事说出来")

    def test_empty_checkpoint_is_refused(self):
        _need_torch()
        import tempfile
        from pathlib import Path
        from rl.eval_fixed import load_pool
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ck.pt"
            torch.save({"nets": {}, "meta": {}}, p)
            with self.assertRaises(SystemExit):
                load_pool(str(p), 3, log=lambda *a: None)


# ======================================================================
# P2-10 跨臂评估的先手
# ======================================================================
class TestHead2HeadFirst(unittest.TestCase):
    """★ `first` 必须在 `reset()` **之前**定下来。"""

    def test_sandbox_honours_first_at_reset(self):
        """★ 机制：`reset()` 按 `self.first` **建行动队列** ⇒ 构造时给就生效。"""
        from rl.sandbox import Sandbox
        sb = Sandbox(seed=1000, size=8, n_nations=2, t_max=50,
                     first="乙").reset()
        self.assertEqual(sb.current_player(), "乙",
                         "构造时给的先手没生效")

    def test_head2head_wires_first_before_reset(self):
        """★ `rl/head2head.py` 那条路：**先抽先手、再建沙盒**（旧写法无效）。"""
        _need_torch()          # ★ `rl.head2head` 顶部 import torch
        from rl.head2head import game_setup
        from rl.sandbox import Sandbox
        nations = ["甲", "乙"]
        _side, first = game_setup(nations, 0, 1000)
        sb = Sandbox(seed=1000, size=8, n_nations=2, t_max=50,
                     first=first).reset()
        self.assertEqual(sb.current_player(), first,
                         "先手设置没生效（reset 后再改 first 是没用的）")

    def test_old_way_would_not_work(self):
        """★ 钉住"事后再改 `sb.first` 没用"（旧写法）。"""
        from rl.sandbox import Sandbox
        sb = Sandbox(seed=1000, size=8, n_nations=2, t_max=50).reset()
        before = sb.current_player()
        sb.first = "乙" if before == "甲" else "甲"
        self.assertEqual(sb.current_player(), before,
                         "reset 后再改 first 竟然生效了 ⇒ 钉住的结论错了")


# ======================================================================
# P2-11 对手网必须跟主网同设备
# ======================================================================
class TestOpponentDevice(unittest.TestCase):
    """★ CUDA 模式下对手网也要上设备（静态确认）。"""

    def test_opponent_net_goes_to_the_device(self):
        _need_torch()
        import inspect
        from rl import train as T
        src = inspect.getsource(T.train)
        self.assertIn("opp_net = opp_net.to(device)", src,
                      "对手网没有搬设备 ⇒ CUDA 模式下设备不一致")


if __name__ == "__main__":
    unittest.main()
