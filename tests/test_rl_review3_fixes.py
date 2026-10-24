# -*- coding: utf-8 -*-
"""★ 审查（2026-10-23 傍晚）四条缺陷的**回归闸门**。

| # | 缺陷 | 本文件对应 |
|---|---|---|
| P1-1 | 评估失败回退会替下一位玩家落子 | `TestRetryPolicy`（**不依赖 torch** ⇒ CI 也跑）/ `TestEvalStaleCandidates`（端到端） |
| P1-2 | 热快照超过缓存上限时恢复仍丢权重 | `TestHotRestoreUnderCacheCap` |
| P1-3 | 热快照的 Adam 状态没保存 | `TestHotOptimizerState` |
| P1-4 | 2v2 奖励不符合已声明的联盟胜利口径 | `TestAllianceOutcome` |
"""
from __future__ import annotations

import unittest

import numpy as np

from rl import evaluate
from rl.sandbox import Sandbox


def _need_torch() -> None:
    """没装 torch ⇒ **整条跳过**（CI 就是这个环境）。"""
    from tests.rl_deps import has_torch, torch_or_skip
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())


def _sandbox(**kw):
    sb = Sandbox(seed=kw.pop("seed", 5), size=kw.pop("size", 8), **kw)
    sb.reset(random_starts=kw.pop("random_starts", False))
    return sb


# ======================================================================
# P1-1 失败之后不许替下一位玩家落子
# ======================================================================
class _FakeTurn:
    """★ 最小替身（**不碰引擎**）：只给 `current_player()` 与 `step()`。

    ★ 为什么要它：`rl/eval_fixed.py` 顶部 import torch ⇒ **CI 测不到**那条路；
      而"失败换人 ⇒ 旧候选作废"的**口径本身**是纯逻辑 ⇒ 抽到
      `rl/sampling.py::execute_order`（**不依赖 torch**）⇒ CI 也能钉住。
    """

    def __init__(self, fail: str = "switch"):
        self.who = "甲"
        self.executed: list = []
        self.fail = fail            # "switch"（失败且换人）/ "stay"（失败不换人）/ "none"

    def current_player(self) -> str:
        return self.who

    def step(self, action):
        self.executed.append((self.who, action))
        if self.fail == "switch":
            # ★ 盲推失败：行动被消耗 ⇒ `_auto_advance` 把回合交给下一位玩家。
            self.fail = "none"
            self.who = "乙"
            return False, "盲推失败（行动额度已消耗）"
        if self.fail == "stay":
            self.fail = "none"
            return False, "引擎拒收（状态没变）"
        return True, "ok"


class TestRetryPolicy(unittest.TestCase):
    """★★ **不依赖 torch** ⇒ CI（不装 torch）也跑得到。"""

    def test_a_single_candidate_does_not_break_the_concentration_metric(self):
        """★ **候选只剩 1 个** ⇒ `logK = log(1) = 0` ⇒ 旧的 `ent/logK` **除零崩掉**。

        ★★ 2026-10-24 修（跑评测时崩的）：`rl/eval_fixed.py` 那行除法从文件创建
          （2026-09-25）起就没设防 ⇒ 某一方只剩一支军、只能"原地不动"时
          ⇒ `ZeroDivisionError` ⇒ **整个评测跑不完**（训练侧早有同样的守卫）。
        ⇒ 现在口径在 `rl/sampling.py::concentration`（**纯 numpy ⇒ CI 也测得到**）：
          **只有真的有得选**（`logK > 0`）的步进这个比值。
        """
        from rl.sampling import concentration
        # ★ 全是"只有一个候选"的步 ⇒ **不崩**，且给 `nan`（"看不出来"，
        #   而不是假装 0 —— 0 会被读成"策略最集中"）。
        v = concentration([0.0, 0.0], [0.0, 0.0])
        self.assertNotEqual(v, v, "全是退化步时该给 nan，而不是 0")
        # ★ 正常步照常算；退化步被排除（不影响均值）。
        self.assertAlmostEqual(concentration([2.0, 0.0], [2.0, 0.0]), 1.0, places=6)
        self.assertAlmostEqual(concentration([1.0, 2.0], [2.0, 0.0]), 0.5, places=6)
        self.assertAlmostEqual(concentration([2.0], [2.0]), 1.0, places=6)

    def test_a_failed_action_that_switches_players_discards_the_candidates(self):
        """★ 失败**换人** ⇒ 旧候选**立刻作废**（不许替下一位玩家落子）。"""
        from rl.sampling import execute_order
        acts = [(1, "attack", 5, 5), (1, "hold", 4, 5), (1, "move", 4, 4)]
        fake = _FakeTurn(fail="switch")
        ok, pos, switched, refused = execute_order(
            acts, [0, 1, 2], fake.step, fake.current_player)
        self.assertFalse(ok)
        self.assertTrue(switched, "没报告'回合换人了' ⇒ 候选表不会被作废")
        self.assertEqual(refused, 1)
        self.assertEqual(len(fake.executed), 1,
                         "失败换人之后**还继续执行了旧候选** ⇒ 替下一位玩家落子")
        self.assertEqual(fake.executed[0][0], "甲",
                         "第一次执行就该是原行动者自己的动作")

    def test_a_failed_action_that_keeps_the_player_still_falls_back(self):
        """★ 失败**没换人**（真拒收）⇒ 照旧**退到次优**（老口径不变）。"""
        from rl.sampling import execute_order
        acts = [(1, "attack", 5, 5), (1, "hold", 4, 5)]
        fake = _FakeTurn(fail="stay")
        ok, pos, switched, refused = execute_order(
            acts, [0, 1], fake.step, fake.current_player)
        self.assertTrue(ok)
        self.assertEqual(pos, 1, "没退到次优")
        self.assertFalse(switched)
        self.assertEqual(refused, 1)
        self.assertEqual(len(fake.executed), 2)


class _StubNet:
    """只按**指定的候选下标**出动作的假网（用来精确触发某个动作）。

    ★ 记下**被前向过几次** —— 断言"下一个玩家也拿到了自己的前向"。
    """

    def __init__(self, pick: int):
        self.pick = pick
        self.mem_slots = 0
        self.calls: list[str] = []

    def mem_init(self, batch_size, **kw):
        return None

    def _logits(self, n):
        import torch
        lg = torch.full((1, n), -30.0)
        lg[0, min(self.pick, n - 1)] = 30.0
        return lg

    def forward(self, batch):
        self.calls.append("?")
        return self._logits(int(batch["type_idx"].shape[1])), None

    # ★ `net(batch)` 是调用（`PolicyNet` 是 `nn.Module` ⇒ 可调用）⇒ 这里也实现 `__call__`。
    def __call__(self, batch):
        return self.forward(batch)

    def forward_state(self, batch, mem=None):
        self.calls.append("?")
        return self._logits(int(batch["type_idx"].shape[1])), None, None


class TestEvalStaleCandidates(unittest.TestCase):
    """★ 盲推失败会**消耗行动并换人** ⇒ 旧候选立刻作废。"""

    def _blind_push_sandbox(self):
        """造一局：甲的一支军站在**中立国丙**的地旁边，进攻它 ⇒ 盲推。

        ★ 盲推的条件（引擎）：目标**被挡**（没有生效的战争）**且看不见**
          ⇒ 引擎**消耗行动**（`consumed_action=True`）并切走回合。
        ★ `t_max` 压到 **6**：这个假网**永远挑同一个候选** ⇒ 局面不会"变聪明"
          ⇒ 回合数一多就慢；这里只要够走几步让"换人"发生即可。
        """
        sb = Sandbox(seed=5, size=8, n_nations=3, war=True, t_max=6,
                     wars=[("甲", "乙")],
                     starts={"甲": (1, 1), "乙": (6, 6), "丙": (5, 5)}
                     ).reset(random_starts=False)
        return sb

    def test_failed_action_does_not_play_for_the_next_player(self):
        """★ 执行的每一个动作，**必须属于当时行动的那一方**。

        ★★ 判据用**"这个动作在不在执行者自己的候选表里"**，
          **不用**"这支军属于谁"（`aid` 是**本国序号**，跨国会撞车：
          甲乙丙都有 `id=3` ⇒ 拿它跨国家找归属**必然误判**，
          这正是 review P0-3 记过的坑）。
        """
        _need_torch()                      # ★ `rl.eval_fixed` 顶部 import torch
        from tests.rl_deps import new_army
        from rl.eval_fixed import play
        sb = self._blind_push_sandbox()
        env = sb.env
        me = sb.current_player()
        # ★ 甲**只留一支军**（放在丙的核心旁边 (4,5)）⇒ 进攻 (5,5) = 盲推。
        #   ★ **只留一支**是必须的：盲推会消耗那支军的行动 ⇒ 若甲还有别的军能动，
        #   `_auto_advance` **不会**换人 ⇒ 测不到"旧候选被当成了下一国的动作"。
        for unit in list(env.armies()):
            if env.owner_of(unit) == me and (unit.x, unit.y) != (4, 5):
                env.call("native:army", "disband", unit.owner, unit.id)
        new_army(env, "甲", (4, 5))
        acts = sb.legal()
        pick = None
        for i, (aid, kind, x, y) in enumerate(acts):
            if kind == "attack" and (x, y) == (5, 5):
                pick = i
                break
        if pick is None:
            self.skipTest("这一局没造出'盲推'候选")
        # ★ 每个国家**一部网** ⇒ 能分别数"谁前向过几次"
        #   （用户 10-23 的实测口径：「甲前向一次、乙前向零次，
        #     评估器却替乙执行了 hold」⇒ 要能**分别**数）。
        nets = {i: _StubNet(pick if i == 0 else 0) for i in range(len(sb.players))}

        # ★ 记下**每个国家自己的候选表**（谁在什么时候拿到的候选）。
        legal_by_player: dict = {}
        real_legal = sb.legal

        def legal_spy():
            who = sb.current_player()
            lst = real_legal()
            legal_by_player.setdefault(who, []).append(list(lst))
            return lst

        sb.legal = legal_spy

        # ★ 记下每个动作是**谁**执行的；顺便记"**失败后换人**"发生过没有。
        played: list[tuple[str, tuple]] = []
        switches: list[tuple[str, str]] = []
        real_step = sb.step

        def spy(action):
            who = sb.current_player()
            ok, msg = real_step(action)
            played.append((who, action))
            after = sb.current_player()
            if not ok and after != who:
                switches.append((who, after))
            return ok, msg

        sb.step = spy
        play(nets, sb, greedy=True, rng=np.random.default_rng(0))
        self.assertTrue(played, "一局都没走")
        # ★★ 这一局**确实撞上了**"盲推失败 ⇒ 换人"（否则下面的断言是空的）。
        self.assertTrue(switches,
                        "这一局没发生'失败后换人' ⇒ 场景没造对，测试是空的")
        # ★★ 断言 1：**执行的每一个动作，都必须在执行者自己的候选表里**。
        #   旧口径：盲推失败换人后，继续拿**上一位玩家**的旧候选去执行
        #   ⇒ 那些动作**不在**下一位玩家的候选表里 ⇒ 这里会炸。
        for who, action in played:
            mine = legal_by_player.get(who, [])
            self.assertTrue(any(action in lst for lst in mine),
                            f"『{who}』的回合上执行了**不属于他的候选**的动作"
                            f"（{action}）⇒ 旧候选被当成了下一国的动作")
        # ★★ 断言 2：**动过的国家都自己前向过**（不许替别人决策）。
        for who in {w for w, _ in played}:
            idx = sb.players.index(who)
            self.assertGreaterEqual(len(nets[idx].calls), 1,
                                    f"『{who}』被动了，但**它自己没有前向过**"
                                    f" ⇒ 评估器替它落子了")

    def test_terminal_helper_takes_the_team_map(self):
        """★ `evaluate.terminal` 的队伍口径可注入（结算侧与训练侧统一）。"""
        sb = _sandbox(n_nations=2, war=True)
        self.assertTrue(sb.team_of, "沙盒没记下队伍身份")
        t = evaluate.terminal(sb.env, sb.players[0], teams=sb.team_of)
        self.assertIn(t, (None, 0.0, evaluate.S.INF, -evaluate.S.INF))

    def test_the_eval_survives_a_single_candidate_turn(self):
        """★★ 端到端：某一方**只剩一个候选**时，评测**不许崩**。

        ★ 旧口径：`ent / logK`，`logK = log(1) = 0` ⇒ `ZeroDivisionError`
          ⇒ **整个评测跑不完**（跑训练时实测崩在这）。
        """
        _need_torch()
        from rl.eval_fixed import play
        sb = _sandbox(n_nations=2, war=True, t_max=8)
        me = sb.current_player()
        unit = sb.armies_of(me)[0]
        real_legal = sb.legal

        def one_candidate():
            # ★ 只留一个"原地不动" ⇒ 策略分布退化成一点质量 ⇒ `logK = 0`
            return [(unit.id, "hold", unit.x, unit.y)]

        sb.legal = one_candidate
        res = play({0: _StubNet(0)}, sb, greedy=True, rng=np.random.default_rng(0))
        self.assertIn("ratio", res)
        self.assertGreater(res["n"], 0, "一局都没走")


# ======================================================================
# P1-2 热快照恢复：先钉住再加载
# ======================================================================
class TestHotRestoreUnderCacheCap(unittest.TestCase):
    """★ 缓存上限一紧，**刚灌进去的那份会被逐出** ⇒ 恢复成了假的。"""

    def test_restore_survives_a_tight_cache(self):
        _need_torch()
        import torch
        from rl.league import League
        from rl.model import build_model
        from rl import train as T

        mids = ["S1_test", "S2_test", "S3_test"]
        lg = League(None, mains=1, max_k=2, min_learners=1, net_cap=1,
                    device="cpu", fingerprint=T._shape_fingerprint(0),
                    log=lambda *a: None)
        for mid in mids:
            lg.add_snapshot(build_model(), 1, mid=mid)
            lg.mark_hot(mid)
        # ★ 改权重（模拟"学过"），并记住改后的指纹。
        new_state = {}
        for mid in mids:
            net = lg.net_of(mid)
            with torch.no_grad():
                for p in net.parameters():
                    p.add_(1.0)
            wkey = next(iter(net.state_dict()))
            new_state[mid] = net.state_dict()[wkey].detach().clone()
        hot = lg.hot_weights()
        self.assertEqual(len(hot), 3)

        # ★ 新池子：缓存上限 **1**（比要恢复的份数还小）⇒ 旧口径会当场逐出。
        lg2 = League(None, mains=1, max_k=2, min_learners=1, net_cap=1,
                     device="cpu", fingerprint=T._shape_fingerprint(0),
                     log=lambda *a: None)
        for mid in mids:
            lg2.add_snapshot(build_model(), 1, mid=mid)
        n = lg2.restore_hot(hot)
        self.assertEqual(n, 3, "报告恢复了几份")
        # ★ 真话：每份再取一次都必须是**新权重**。
        for mid in mids:
            wkey = next(iter(lg2.net_of(mid).state_dict()))
            got = lg2.net_of(mid).state_dict()[wkey]
            self.assertTrue(torch.equal(got, new_state[mid]),
                            f"{mid} 再取出来是**旧权重** ⇒ 恢复是假的"
                            f"（缓存上限 1 时被逐出了）")

    def test_the_legacy_hot_format_is_still_readable(self):
        """★ **老存档**（`league_hot` 是"纯权重 dict"）还能读回来。

        ★ 为什么要留这条：形状从 `{mid: {张量名: 张量}}` 改成
          `{mid: {"weights": …, "opt": …}}`（review P1-3）⇒
          **盘上已有的旧存档不能作废** ⇒ `restore_hot` 两种形状都认。
        """
        _need_torch()
        import torch
        from rl.league import League
        from rl.model import build_model
        from rl import train as T

        lg = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                    device="cpu", fingerprint=T._shape_fingerprint(0),
                    log=lambda *a: None)
        net = build_model()
        mid = "S1_test"
        lg.add_snapshot(net, 1, mid=mid)
        lg.mark_hot(mid)
        held = lg.net_of(mid)
        with torch.no_grad():
            for p in held.parameters():
                p.add_(1.0)
        wkey = next(iter(held.state_dict()))
        # ★ **旧形状**：直接是张量表。
        legacy = {mid: {k: v.detach().cpu().clone()
                        for k, v in held.state_dict().items()}}
        lg2 = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                     device="cpu", fingerprint=T._shape_fingerprint(0),
                     log=lambda *a: None)
        lg2.add_snapshot(build_model(), 1, mid=mid)
        self.assertEqual(lg2.restore_hot(legacy), 1, "旧存档读不回来")
        got = lg2.net_of(mid).state_dict()[wkey]
        self.assertTrue(torch.equal(got, held.state_dict()[wkey]),
                        "旧存档灌回来的权重不对")


# ======================================================================
# P1-3 热快照的 Adam 状态
# ======================================================================
class TestHotOptimizerState(unittest.TestCase):
    """★ 热快照的**动量**也要跟着走（不然这部分续训等于重开 Adam）。"""

    def test_optimizer_state_travels_with_the_hot_snapshot(self):
        _need_torch()
        import torch
        from rl.league import League
        from rl.model import build_model
        from rl import train as T

        lg = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                    device="cpu", fingerprint=T._shape_fingerprint(0),
                    log=lambda *a: None)
        net = build_model()
        mid = "S1_test"
        lg.add_snapshot(net, 1, mid=mid)
        lg.mark_hot(mid)
        held = lg.net_of(mid)
        # ★ 快照网是**推理态**（`requires_grad_(False)`）⇒ 反传前先打开梯度。
        for p in held.parameters():
            p.requires_grad_(True)
        # ★ 给它挂一个**走了一步**的 Adam（这样动量状态是真的存在）。
        opt = T._adam_for(held, 1e-4)
        loss = sum(p.sum() for p in held.parameters())
        loss.backward()
        opt.step()
        opt.zero_grad()
        n_state = len(opt.state_dict()["state"])
        self.assertGreater(n_state, 0)

        hot = lg.hot_weights()
        self.assertIn("opt", hot[mid], "热快照里没有优化器状态 ⇒ 续训会重开 Adam")
        self.assertGreater(len(hot[mid]["opt"]["state"]), 0)

        lg2 = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                     device="cpu", fingerprint=T._shape_fingerprint(0),
                     log=lambda *a: None)
        lg2.add_snapshot(build_model(), 1, mid=mid)
        self.assertEqual(lg2.restore_hot(hot), 1)
        got = getattr(lg2.net_of(mid), "_ppo_opt", None)
        self.assertIsNotNone(got, "恢复之后优化器不存在 ⇒ 动量全丢")
        self.assertEqual(len(got.state_dict()["state"]), n_state)

    def test_checkpoint_carries_the_hot_optimizer_state(self):
        _need_torch()
        import tempfile
        import torch
        from pathlib import Path
        from rl import train as T
        from rl.league import League
        from rl.model import build_model

        lg = League(None, mains=1, max_k=2, min_learners=1, net_cap=4,
                    device="cpu", fingerprint=T._shape_fingerprint(0),
                    log=lambda *a: None)
        net = build_model()
        mid = "S1_test"
        lg.add_snapshot(net, 1, mid=mid)
        lg.mark_hot(mid)
        held = lg.net_of(mid)
        for p in held.parameters():
            p.requires_grad_(True)
        opt = T._adam_for(held, 1e-4)
        loss = sum(p.sum() for p in held.parameters())
        loss.backward()
        opt.step()
        opt.zero_grad()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ck.pt"
            T._save_ckpt(str(p), {0: net}, 1,
                         meta=T._ckpt_meta(8, 8, True, None, 1, 60, mem_slots=0),
                         league=lg)
            blob = torch.load(p, map_location="cpu", weights_only=False)
            hot = blob.get("league_hot") or {}
            self.assertIn(mid, hot, "存档里没有热快照")
            self.assertIn("opt", hot[mid],
                          "存档里的热快照没有优化器状态 ⇒ 这部分续训会重开 Adam")


# ======================================================================
# P1-4 联盟胜利口径
# ======================================================================
class TestAllianceOutcome(unittest.TestCase):
    """★ "我战死但我的队赢了" ⇒ **我赢**（已声明的联盟胜利口径）。"""

    def _two_teams(self):
        sb = Sandbox(seed=7, size=8, n_nations=4, war=True,
                     alliances="random2v2", territory=False).reset(random_starts=False)
        teams: dict = {}
        for name, team in sb.team_of.items():
            teams.setdefault(team, []).append(name)
        pairs = [v for v in teams.values() if len(v) > 1]
        if not pairs:
            return None
        return sb, pairs[0]

    def test_team_identity_is_recorded(self):
        sb, pair = self._two_teams()
        if sb is None:
            self.skipTest("这一局没抽到 2v2")
        for name in sb.players:
            self.assertIn(name, sb.team_of, f"{name} 没有队伍身份")
        self.assertEqual(sb.team_of[pair[0]], sb.team_of[pair[1]],
                         "同一队的两个国家队伍身份不一致")

    def test_team_identity_follows_a_late_alliance(self):
        """★ **建局之后才结的盟**，队伍身份也要**跟着更新**。

        ★ 为什么单列一条：队伍身份一开始只在 `reset()` 里记 ⇒ 那种局里甲乙拿到的
          还是"各自为政"的旧标签 ⇒ 胜方判据看到**两个队伍** ⇒ `winner()` 是 `None`
          （实测：`tests/test_rl_terminal_outcome.py` 那条"成员亡国但联盟赢了"
          ⇒ 联盟明明赢了，`winner()` 却返回 `None`）。
          ★ 这**不是边角用法**：引擎那条"正在战争不能建盟"逼着调用方
          **先 `war=False` 建局、再结盟** ⇒ 这条路是**正路**。
        """
        from tests.rl_deps import give_site
        sb = Sandbox(seed=0, size=8, n_nations=3, t_max=20,
                     war=False).reset(random_starts=False)
        sb.set_alliance(("北方同盟", ["甲", "乙"]))
        self.assertEqual(sb.team_of["甲"], sb.team_of["乙"],
                         "结盟之后队伍身份没跟上 ⇒ 还是各自为政的旧标签")
        # ★ 只剩这个联盟 ⇒ 它赢（亡国的丙是另一个队伍 ⇒ 输）。
        give_site(sb.env, sb.core_of("丙"), None)
        sb.env.resolve()
        self.assertIsNotNone(sb.winner(), "联盟明明赢了，却判不出胜方")
        self.assertIn("甲", sb.winner_members())
        self.assertIn("乙", sb.winner_members())
        self.assertNotIn("丙", sb.winner_members())

    def test_dead_member_of_the_winning_team_wins(self):
        """★ 乙、丁同队 ⇒ 乙先亡、丁最终获胜 ⇒ **两人都是 +1**。"""
        from tests.rl_deps import give_site
        got = self._two_teams()
        if got is None:
            self.skipTest("这一局没抽到 2v2")
        sb, pair = got
        env = sb.env
        victim = pair[0]
        mate = pair[1]
        others = [n for n in sb.players if sb.team_of.get(n) != sb.team_of.get(victim)]
        # ★ 让同队的这一位先亡国。
        give_site(env, sb.core_of(victim), None)
        env.resolve()
        self.assertNotIn(victim, env.nations(), "亡国没生效")
        # ★ 对面全灭 ⇒ 同队获胜。
        for n in others:
            if n in env.nations():
                give_site(env, sb.core_of(n), None)
        env.resolve()
        self.assertEqual(set(env.nations()), {mate}, "只剩队友了")
        # ★ 亡国的那个也要算赢（联盟胜利口径）。
        self.assertEqual(sb.reward(victim), 1.0,
                         "亡国的队友拿到了输 ⇒ 不符合'联盟胜利'口径")
        self.assertEqual(sb.reward(mate), 1.0)
        self.assertEqual(sb.outcome_for(victim), 1.0)
        self.assertEqual(sb.outcome_for(mate), 1.0)
        # ★ 胜方成员名单里要有那个亡国的。
        self.assertIn(victim, sb.winner_members(),
                      "胜方名单里没有亡国的队友 ⇒ 口径没统一")

    def test_losing_team_gets_minus_one(self):
        """★ 输的那一队（**含已亡国的成员**）统一 −1。"""
        from tests.rl_deps import give_site
        got = self._two_teams()
        if got is None:
            self.skipTest("这一局没抽到 2v2")
        sb, pair = got
        env = sb.env
        # ★ 杀掉 pair 那一队 ⇒ 另一队赢 ⇒ **pair 那一队全员 −1**
        #   （含已经亡国的那个 —— 它本来就没打过）。
        for n in pair:
            if n in env.nations():
                give_site(env, sb.core_of(n), None)
        env.resolve()
        for n in pair:
            self.assertEqual(sb.reward(n), -1.0, f"{n} 输了却拿到 +1")
            self.assertEqual(sb.outcome_for(n), -1.0)
        winners = [n for n in sb.players if sb.team_of.get(n) != sb.team_of.get(pair[0])]
        for n in winners:
            self.assertEqual(sb.reward(n), 1.0, f"{n} 赢了却拿到 −1")

    def test_a_truncated_episode_does_not_settle_a_living_team(self):
        """★★ **被截断**的那一局：亡国但队伍还活着的玩家，**结果还没定**。

        ★★ 2026-10-24 修（跑训练时发现的）：`rl/train.py::_outcome_for` 原来
          **自己抄了一份**沙盒的截断分支，却**漏了 `teams`**
          ⇒ `evaluate.terminal(env, p)`（不带队伍表）⇒ 亡国的那个玩家
          "**我的实体查不到了**" ⇒ 判成"**出局（输）**"
          ⇒ 在**被步数预算截断**的一局里，队友明明还活着，它却被提前封成负。
          ⇒ 现在训练侧**真转发**沙盒那份（带 teams）⇒ 口径统一。
        """
        from tests.rl_deps import give_site
        got = self._two_teams()
        if got is None:
            self.skipTest("这一局没抽到 2v2")
        sb, pair = got
        env = sb.env
        victim, mate = pair[0], pair[1]
        # ★ 只杀一个成员 ⇒ **它的队友还活着** ⇒ 这一局**没结束**
        #   （两个队伍都还在 ⇒ 不是终局）。
        give_site(env, sb.core_of(victim), None)
        env.resolve()
        self.assertNotIn(victim, env.nations(), "亡国没生效")
        self.assertIn(mate, env.nations(), "队友不该跟着亡")
        self.assertFalse(sb.is_terminal(), "这一局还没结束 ⇒ 属于'截断'这条路")
        # ★ 沙盒侧（**单一出处**）：结果**还没定** ⇒ `None`。
        self.assertIsNone(sb.outcome_for(victim, global_terminal=False),
                          "亡国但队伍还活着的玩家被提前判成出局 ⇒ 联盟口径分叉")
        # ★ 旧口径（**不带队伍表**）会给 −INF ⇒ 这就是"漏传 teams"的后果。
        self.assertEqual(evaluate.terminal(env, victim), -evaluate.S.INF,
                         "旧口径的结论变了？⇒ 这条闸门的对照失效了")
        # ★ 训练侧必须跟沙盒侧**一致**（它现在只是转发）。
        _need_torch()
        from rl import train as T
        for p in sb.players:
            self.assertEqual(T._outcome_for(sb, p, global_terminal=False),
                             sb.outcome_for(p, global_terminal=False),
                             f"训练侧与沙盒侧在 {p} 上算出两个结果 ⇒ 口径分叉")
            self.assertEqual(T._outcome_for(sb, p, global_terminal=True),
                             sb.outcome_for(p, global_terminal=True),
                             f"训练侧与沙盒侧在 {p} 上算出两个结果 ⇒ 口径分叉")


if __name__ == "__main__":
    unittest.main()
