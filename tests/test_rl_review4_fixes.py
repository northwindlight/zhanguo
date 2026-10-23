"""训练截断、磁盘快照和失败后重新决策的回归测试。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from rl.sandbox import Sandbox
from tests.rl_deps import give_site, has_torch, new_army, torch, torch_or_skip


def blind_push_sandbox():
    sb = Sandbox(seed=5, size=8, n_nations=3, war=True, t_max=6,
                 wars=[("甲", "乙")],
                 starts={"甲": (1, 1), "乙": (6, 6), "丙": (5, 5)}
                 ).reset(random_starts=False)
    new_army(sb.env, "甲", (4, 5))
    acts = sb.legal()
    idx = next(i for i, a in enumerate(acts) if a[1:] == ("attack", 5, 5))
    hold = next(i for i, a in enumerate(acts)
                if a[:2] == (acts[idx][0], "hold"))
    return sb, acts, idx, hold


class TestFailedDecision(unittest.TestCase):
    def test_consumed_failure_discards_candidates_without_switching_players(self):
        from rl.sampling import execute_order
        sb, acts, idx, hold = blind_push_sandbox()
        attempted = []

        def step(act):
            attempted.append(act)
            return sb.step(act)

        result = execute_order(acts, [idx, hold], step, sb.current_player,
                               decision_state=sb.decision_key)
        self.assertEqual(result, (False, None, True, 1))
        self.assertEqual(sb.current_player(), "甲", "必须覆盖失败后没有换人的场景")
        self.assertEqual(attempted, [acts[idx]])
        self.assertNotIn(acts[hold], sb.legal())

    def test_hold_rejects_a_consumed_unit(self):
        sb, acts, idx, hold = blind_push_sandbox()
        ok, _ = sb.step(acts[idx])
        self.assertFalse(ok)
        self.assertNotIn(acts[hold], sb.legal())
        ok, _ = sb.step(acts[hold])
        self.assertFalse(ok, "消耗行动的军队不能再 hold")

    def test_hold_rejects_repeated_and_stale_position_actions(self):
        sb = Sandbox(seed=5, size=8, n_nations=2, t_max=6).reset(random_starts=False)
        hold = next(a for a in sb.legal() if a[1] == "hold")
        aid, kind, x, y = hold
        self.assertFalse(sb.step((aid, kind, x + 1, y))[0])
        self.assertTrue(sb.step(hold)[0])
        self.assertEqual(sb.current_player(), "甲")
        self.assertFalse(sb.step(hold)[0])

    def test_unchanged_rejection_still_tries_the_next_candidate(self):
        from rl.sampling import execute_order
        sb = Sandbox(seed=5, size=8, n_nations=2, t_max=6).reset(random_starts=False)
        hold = next(a for a in sb.legal() if a[1] == "hold")
        result = execute_order([(999999, "hold", 0, 0), hold], [0, 1],
                               sb.step, sb.current_player, decision_state=sb.decision_key)
        self.assertEqual(result, (True, 1, False, 1))


@unittest.skipUnless(has_torch(), torch_or_skip())
class TestTrainingOutcomes(unittest.TestCase):
    def test_truncated_dead_member_remains_unsettled_while_teammate_lives(self):
        from rl import train as T
        sb = Sandbox(seed=7, size=8, n_nations=4, war=True, t_max=100,
                     alliances="random2v2", territory=False).reset(random_starts=False)
        victim = sb.players[0]
        mate = next(n for n in sb.players
                    if n != victim and sb.team_of[n] == sb.team_of[victim])
        foes = [n for n in sb.players if sb.team_of[n] != sb.team_of[victim]]
        give_site(sb.env, sb.core_of(victim), None)
        sb.env.resolve()
        self.assertNotIn(victim, sb.env.nations())
        self.assertTrue(sb.alive(mate))
        self.assertFalse(sb.is_terminal())
        steps = [T.Step({}, 0, 0.0, 0.37, 0.0, False, victim)]
        settled = T._settle_episode(sb, steps)
        self.assertIsNone(settled["outcomes"][victim])
        self.assertFalse(steps[0].terminal)
        self.assertEqual(steps[0].reward, 0.0)
        self.assertEqual(steps[0].boot, 0.37)
        for foe in foes:
            give_site(sb.env, sb.core_of(foe), None)
        sb.env.resolve()
        final_steps = [T.Step({}, 0, 0.0, 0.37, 0.0, False, victim)]
        T._settle_episode(sb, final_steps)
        self.assertEqual(final_steps[0].outcome, 1.0)
        self.assertTrue(final_steps[0].terminal)
        self.assertIsNone(final_steps[0].boot)


@unittest.skipUnless(has_torch(), torch_or_skip())
class TestDiskSnapshotTraining(unittest.TestCase):
    def test_train_retains_updates_until_the_snapshot_interval(self):
        from rl import train as T
        from rl.league import League
        from rl.model import build_model
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "league.db")
            ckpt = str(Path(d) / "train.pt")
            lg = League(db, net_cap=1, fingerprint=T._shape_fingerprint(0),
                        log=lambda *a: None)
            mids = ["S_cold_0", "S_cold_1"]
            for mid in mids:
                lg.add_snapshot(build_model(), 0, mid=mid)
            lg.close()
            updated = {}

            def training_pool(*args, **kwargs):
                pool = League(*args, **kwargs)
                self.addCleanup(pool.close)
                return pool

            def update(net, steps, **kw):
                # 只替换 PPO 数值计算，保留真实 train -> 缓存 -> 存档路径。
                opt = T._adam_for(net, kw["lr"])
                opt.zero_grad()
                next(net.parameters()).sum().backward()
                opt.step()
                updated[steps[0].member] = {k: v.detach().cpu().clone()
                                           for k, v in net.state_dict().items()}
                return {}

            with patch.object(League, "draw", return_value=mids), \
                 patch.object(T, "League", side_effect=training_pool), \
                 patch.object(T, "ppo_update", side_effect=update):
                T.train(iters=1, episodes_per_iter=1, size=8, nations=2,
                        pool=2, t_max=6, max_steps=2, out=ckpt,
                        league_db=db, league_cache=1, league_snapshot_every=3,
                        territory=False, log=lambda *a: None)
            self.assertTrue(updated)
            blob = torch.load(ckpt, map_location="cpu", weights_only=False)
            self.assertEqual(set(blob["league_hot"]), set(updated))
            restored = League(db, net_cap=1, fingerprint=T._shape_fingerprint(0),
                              log=lambda *a: None)
            try:
                restored.load()
                self.assertEqual(restored.restore_hot(blob["league_hot"]), len(updated))
                for mid, expected in updated.items():
                    net = restored.net_of(mid)
                    for key, value in expected.items():
                        self.assertTrue(torch.equal(value, net.state_dict()[key]), key)
                    self.assertGreater(len(net._ppo_opt.state), 0)
            finally:
                restored.close()

    def test_disk_hot_restore_keeps_weights_and_the_next_adam_step(self):
        from rl import train as T
        from rl.league import League
        from rl.model import build_model
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "league.db")
            lg = League(db, net_cap=1, fingerprint=T._shape_fingerprint(0),
                        log=lambda *a: None)
            restored = None
            try:
                mids = ["S_disk_0", "S_disk_1", "S_disk_2"]
                for mid in mids:
                    lg.add_snapshot(build_model(), 0, mid=mid)
                    lg.mark_hot(mid)
                    net = lg.net_of(mid)
                    net.requires_grad_(True)
                    opt = T._adam_for(net, 1e-4)
                    sum(p.sum() for p in net.parameters()).backward()
                    opt.step()
                    opt.zero_grad()
                ckpt = str(Path(d) / "hot.pt")
                T._save_ckpt(ckpt, {}, 1,
                             meta=T._ckpt_meta(8, 8, True, 2, 0, 6, mem_slots=0),
                             league=lg)
                restored = League(db, net_cap=1, fingerprint=T._shape_fingerprint(0),
                                  log=lambda *a: None)
                restored.load()
                T._load_ckpt(ckpt, {}, league=restored, log=lambda *a: None)
                for mid in mids:
                    before, after = lg.net_of(mid), restored.net_of(mid)
                    self.assertIn(mid, restored._nets)
                    for key, value in before.state_dict().items():
                        self.assertTrue(torch.equal(value, after.state_dict()[key]), key)
                    after.requires_grad_(True)
                    for net in (before, after):
                        sum(p.square().sum() for p in net.parameters()).backward()
                        net._ppo_opt.step()
                        net._ppo_opt.zero_grad()
                    for key, value in before.state_dict().items():
                        self.assertTrue(torch.equal(value, after.state_dict()[key]), key)
            finally:
                lg.close()
                if restored is not None:
                    restored.close()


@unittest.skipUnless(has_torch(), torch_or_skip())
class TestEvalRefresh(unittest.TestCase):
    def test_eval_observes_again_after_a_consumed_failure(self):
        from rl.eval_fixed import play
        sb, acts, idx, _ = blind_push_sandbox()

        class Net:
            mem_slots = 0
            calls = 0

            def __call__(self, batch):
                self.calls += 1
                logits = torch.full((1, batch["type_idx"].shape[1]), -30.0)
                logits[0, min(idx, logits.shape[1] - 1)] = 30.0
                return logits, None

        class StopAfterRefresh(Exception):
            pass

        net = Net()
        real_step = sb.step
        attempted = []

        def step(act):
            attempted.append(act)
            if len(attempted) == 2:
                self.assertEqual(net.calls, 2, "消耗行动后没有重新前向")
                self.assertIn(act, sb.legal())
                raise StopAfterRefresh
            result = real_step(act)
            self.assertEqual(act, acts[idx])
            self.assertFalse(result[0])
            self.assertEqual(sb.current_player(), "甲")
            return result

        sb.step = step
        with self.assertRaises(StopAfterRefresh):
            play({0: net}, sb, greedy=True, rng=np.random.default_rng(0))


if __name__ == "__main__":
    unittest.main()
