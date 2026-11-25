import unittest

from .env import Env


class DiplomacyCacheTest(unittest.TestCase):
    def test_founding_tracks_cash_across_fixed_fee(self):
        env = Env(13, 3).reset(700)
        name = env.seat_name(0)
        world = env.world
        world.add_res(name, "黄金", 105 - world.res(name, "黄金"))

        def pick(kind, args=None):
            return next(c for c in env.candidates(0)
                        if c.kind == kind and (args is None or c.args == args))

        self.assertTrue(pick("bloc_found").executable)
        self.assertTrue(env.act(0, pick("buy", ("粮食", 5)))[0])
        self.assertEqual(world.res(name, "黄金"), 97)
        found = pick("bloc_found")
        self.assertFalse(found.executable)
        self.assertFalse(env.act(0, found)[0])

        self.assertTrue(env.act(0, pick("sell", ("粮食", 5)))[0])
        self.assertGreaterEqual(world.res(name, "黄金"), 100)
        found = pick("bloc_found")
        self.assertTrue(found.executable)
        self.assertTrue(env.act(0, found)[0])
        self.assertLess(world.res(name, "黄金"), 100)
        self.assertTrue(pick("bloc_found", found.args).executable)
        self.assertTrue(env.act(0, pick("bloc_found", found.args))[0])
        self.assertEqual(len(world.diplo_pending), 1)
