"""State ownership requires a module context; engine keys stay writable."""
import unittest

from engine.runtime import ModError
from engine.state import unit_context
from host import GameHost


class TestStateContext(unittest.TestCase):
    def host(self):
        return GameHost.new({'map_size': 10, 'seed': 9,
                             'nations': [{'id': 's:q', 'name': '秦', 'client': 'rule'},
                                         {'id': 's:c', 'name': '楚', 'client': 'rule'}]})

    def test_unidentified_context_cannot_write_domain_state(self):
        world = self.host().world
        with unit_context(None):
            with self.assertRaises(ModError):
                world.economy = {}
        # Engine-owned state stays writable without a module identity.
        with unit_context(None):
            world.phase = '行动'
        self.assertEqual(world.phase, '行动')


if __name__ == '__main__':
    unittest.main()
