"""Menu presets describe actual scenario hooks and placement, without models."""
import json
from pathlib import Path
import unittest

from host import GameHost
from scenarios.eight_nations import NATIONS,IDS,STARTS,SIZE


class TestDesktopScenarios(unittest.TestCase):
    def test_bundled_catalog_matches_real_eight_nations_and_free_start(self):
        path=Path(__file__).resolve().parents[1]/'mods/ui/desktop/scenario-catalog.json'
        catalog=json.loads(path.read_text())['scenarios'];formal,free=catalog
        self.assertEqual(formal['id'],'eight_nations');self.assertEqual(formal['map_size'],SIZE)
        self.assertEqual(formal['nations'],[{'id':IDS[n],'name':n,'position':list(STARTS[n])} for n in NATIONS])
        self.assertEqual(free['id'],'');self.assertEqual(len(free['nations']),2)
        for scenario in catalog:
            cfg={'map_size':scenario['map_size'],'seed':20261023,
                 'nations':[{**n,'client':'rule','rule_ai':'v13'} for n in scenario['nations']]}
            if scenario['id']:cfg['scenario']=scenario['id']
            h=GameHost.new(cfg)
            treaties=h.world.diplomacy.to_save()['pacts']
            if scenario is formal:
                self.assertEqual(len(h.actors()),8)
                self.assertEqual(sum(t['kind']=='guarantee' and t['b']==h.world.diplomacy.entity(IDS['周']) for t in treaties),7)
            else:
                self.assertEqual(len(h.actors()),2);self.assertFalse(treaties)


if __name__=='__main__':unittest.main()
