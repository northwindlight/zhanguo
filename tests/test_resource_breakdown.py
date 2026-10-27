"""Actual settlement provenance, not a reconstruction from current buildings."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from host import GameHost
from mods.native.human import HumanAPI
from mods.ui.desktop_player import frame,flow
from tests.test_human import config,A,B


class TestResourceBreakdown(unittest.TestCase):
    def host(self,*,fuel=True):
        h=GameHost.new(config());w=h.world
        site=next(s for s in w.buildings.snapshot().sites if s.owner==A)
        w.buildings._sites[site.id]=replace(site,resources=(('native:farmland',20),('native:wood',20)),capacity=20)
        farms=w.buildings.seed(site.id,'native:farm',4)
        w.buildings.seed(site.id,'native:supply-factory');w.buildings.seed(site.id,'native:wood-power-plant')
        if not fuel:w.resources.consume(A,'native:wood',w.resources.balance(A,'native:wood'))
        api=HumanAPI(h);h.cycle(on_human=lambda player:[api.submit(A,'end_turn',{})])
        return h,api,farms

    def test_four_farms_and_one_factory_net_two_and_survive_demolition_and_save(self):
        h,api,farms=self.host();state=api.state(A);rows=flow(state)
        sources={r['kind']:r for r in rows['sources']}
        self.assertEqual((sources['native:farm']['count'],dict(sources['native:farm']['outputs'])['native:food']),(4,4))
        self.assertEqual((sources['native:supply-factory']['count'],dict(sources['native:supply-factory']['inputs'])['native:food']),(1,2))
        self.assertEqual(dict(rows['net'])['native:food'],2)
        events=api.state(B) if h.clients[B].kind=='human' else h.player(B).observe()
        self.assertFalse(any(e['kind']=='production-detail' and (e.get('details') or {}).get('owner')==A for e in events['facts']['news']))
        observer=next(r for r in h._observer_resource_flows() if r['id']==A)
        self.assertEqual(observer['sources'],rows['sources'])
        h.world.buildings.demolish(A,farms[0].id)
        self.assertEqual(flow(api.state(A))['sources'],rows['sources'])
        with TemporaryDirectory() as directory:
            path=Path(directory)/'save.json';h.save(path);loaded=GameHost.load(path,h.cfg)
            self.assertEqual(flow(HumanAPI(loaded).state(A))['sources'],rows['sources'])
        projected=frame(h.ui,api.state(A))['nations'][0]['resource_flow']
        self.assertTrue(all(s['label'] for s in projected['sources']))

    def test_stopped_factory_is_not_reported_as_actual_consumption(self):
        h,api,_=self.host(fuel=False);rows=flow(api.state(A))
        kinds={r['kind'] for r in rows['sources']}
        self.assertNotIn('native:supply-factory',kinds);self.assertNotIn('native:wood-power-plant',kinds)
        self.assertEqual(dict(rows['net'])['native:food'],4)
