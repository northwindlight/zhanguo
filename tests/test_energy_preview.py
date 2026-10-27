"""Current power uses the real production algorithm, without executing a turn."""
from copy import deepcopy
import unittest
from tests import test_building_objects as fixtures
from tests.test_human import config,A
from host import GameHost
from mods.native.human import HumanAPI
from mods.ui.desktop_player import frame


class TestEnergyPreview(unittest.TestCase):
    def world(self,wood):
        w=fixtures.TestBuildings().world()
        w.resources.consume('Qin','native:wood',1000-wood)
        w.buildings.seed('example:qin','native:wood-power-plant',2)
        w.buildings.seed('example:qin','native:town-hall')
        w.buildings.seed('example:qin','native:barracks',2)
        return w

    def assert_matches(self,w,share=None):
        before=deepcopy(w.runtime.save_mod_state(w))
        preview=w.buildings.demand_preview('Qin',share)['energy']
        self.assertEqual(before,w.runtime.save_mod_state(w),'power forecast mutated real balances/buildings')
        report=w.buildings.produce(production_shares={'Qin':share} if share else None)
        actual=next(r for r in report.energy if r.owner=='Qin')
        self.assertEqual((preview['generated'],preview['demand'],preview['short']),
                         (actual.generated,actual.demand,actual.short))
        return preview

    def test_installed_capacity_is_not_generation_when_fuel_is_missing(self):
        w=self.world(1)
        preview=self.assert_matches(w)
        self.assertEqual((preview['capacity'],preview['generated'],preview['demand']),(4,2,3))
        self.assertEqual((preview['shortfall'],preview['fuel_short_plants']),(1,1))

    def test_current_gathering_can_fuel_plants_but_unfinished_plants_do_not_generate(self):
        w=self.world(0)
        w.buildings.seed('example:qin','native:lumber-camp',2)
        w.buildings.seed('example:qin','native:oil-power-plant',completed=False)
        preview=self.assert_matches(w)
        self.assertEqual((preview['capacity'],preview['generated'],preview['demand']),(4,4,3))
        self.assertEqual(preview['fuel_short_plants'],0)

    def test_tribute_rounding_is_applied_before_power_fuel(self):
        w=self.world(1)
        w.buildings.seed('example:qin','native:lumber-camp')
        preview=self.assert_matches(w,{'recipient':'Chu','percent':100})
        self.assertEqual(preview['generated'],2)

    def test_player_projection_exposes_only_own_power_and_last_actual_grid_status(self):
        h=GameHost.new(config());api=HumanAPI(h)
        energy=api.state(A)['facts']['econ']['preview']['energy']
        self.assertEqual((energy['generated'],energy['capacity'],energy['demand']),(0,0,1))
        self.assertIsNone(energy['previous_short'])
        before=h.to_save();visual=frame(h.ui,api.state(A))
        self.assertEqual(h.to_save(),before)
        self.assertEqual([p['id'] for p in visual['scene']['power']],[A])
        h.cycle(on_human=lambda p:api.submit(A,'end_turn',{}))
        energy=api.state(A)['facts']['econ']['preview']['energy']
        self.assertTrue(energy['previous_short'])


if __name__=='__main__':unittest.main()
