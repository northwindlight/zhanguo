"""Real player-scoped construction and recruitment eligibility, never trials."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from host import GameHost
from mods.native.human import HumanAPI
from mods.ui.desktop_catalog import present
from mods.ui.desktop_player import frame
from tests.test_human import config,A,B


class TestHumanPlanning(unittest.TestCase):
    def setUp(self):
        self.h=GameHost.new(config());self.api=HumanAPI(self.h)
        self.site=next(s for s in self.h.world.buildings.snapshot().sites if s.owner==A)

    def read(self,site=None):
        site=site or self.site
        return self.api.site_options(A,*site.position)

    @staticmethod
    def item(data,kind,group='buildings'):return next(r for r in data[group] if r['kind']=='native:'+kind)

    def second_site(self):
        w=self.h.world
        x,y=next((x,y) for x in range(2,10) for y in range(2,10) if w.territory.owner(x,y) is None and w.territory.terrain(x,y).claimable)
        return w.territory.create_claim('scenario:rts-second',A,x,y)

    def test_planning_is_read_only_and_cannot_consult_observer(self):
        before=self.h.to_save()
        with patch.object(self.h,'observer_snapshot',side_effect=AssertionError('observer')),patch.object(self.h,'observer_scene',side_effect=AssertionError('observer')):
            data=self.read();present(self.h.ui,data,self.api.state(A))
        self.assertEqual(before,self.h.to_save());self.assertEqual(len(data['buildings']),16)

    def test_foreign_and_unknown_coordinates_are_rejected(self):
        other=next(s for s in self.h.world.buildings.snapshot().sites if s.owner==B)
        with self.assertRaises(ValueError):self.api.site_options(A,*other.position)
        unknown=next(t for t in self.api.state(A)['map']['tiles'] if t['visibility']=='unknown')
        with self.assertRaises(ValueError):self.api.site_options(A,*unknown['position'])

    def test_missing_facility_and_slot_prerequisites_disable_candidates(self):
        data=self.read()
        self.assertFalse(self.item(data,'barracks')['available'])
        self.assertFalse(self.item(data,'infantry','units')['available'])
        self.assertFalse(self.item(data,'militia','units')['available'])
        self.assertTrue(all(not r['has_facility'] for r in data['units']))
        self.assertTrue(self.item(data,'castle')['available'])

    def test_spent_construction_order_is_local_to_selected_tile(self):
        other=self.second_site();site=self.h.world.buildings.site(other.tile.id)
        result=self.api.submit(A,'build',{'kind':'native:castle','x':self.site.position[0],'y':self.site.position[1]})
        self.assertTrue(result['ok']);a=self.read();b=self.read(site)
        self.assertTrue(a['ordered']);self.assertFalse(any(r['available'] for r in a['buildings']))
        self.assertFalse(b['ordered']);self.assertTrue(self.item(b,'castle')['available'])
        result=self.api.submit(A,'build',{'kind':'native:castle','x':site.position[0],'y':site.position[1]})
        self.assertTrue(result['ok']);self.assertTrue(self.read()['ordered'])
        self.assertTrue(self.read(site)['ordered'])

    def test_actual_price_includes_terrain_academy_and_policy_rounding(self):
        w=self.h.world;w.buildings._sites[self.site.id]=replace(self.site,penalty=40)
        w.buildings.seed(self.site.id,'native:academy')
        data=w.buildings.planning(A,self.site.id,policy_pct=80)
        row=self.item(data,'castle');price=row['price']
        self.assertEqual(price['costs'],{'native:gold':41,'native:wood':20})
        self.assertEqual((price['penalty'],price['discount'],price['policy_pct']),(40,25,80))
        self.assertEqual(price,w.buildings.quote(A,self.site.id,'native:castle',policy_pct=80))

    def test_money_shortage_disables_before_submit_without_spending(self):
        w=self.h.world;w.resources.consume(A,'native:gold',1495)
        before=self.h.to_save();row=self.item(self.read(),'castle')
        self.assertFalse(row['available']);self.assertEqual(row['price']['costs']['native:gold'],50)
        self.assertTrue(any(r.get('resource')=='native:gold' and not r['met'] for r in row['requirements']))
        self.assertEqual(before,self.h.to_save())

    def test_unfinished_camp_then_completed_then_quota_consumed(self):
        w=self.h.world
        w.buildings.seed(self.site.id,'native:militia-camp',completed=False)
        self.assertFalse(self.item(self.read(),'militia','units')['available'])
        self.h.cycle(on_human=lambda p:self.api.submit(A,'end_turn',{}))
        self.assertTrue(self.item(self.read(),'militia','units')['available'])
        args={'kind':'native:militia','count':1,'x':self.site.position[0],'y':self.site.position[1]}
        self.assertTrue(self.api.submit(A,'recruit',args)['ok'])
        self.assertFalse(self.item(self.read(),'militia','units')['available'])

    def test_material_shortage_and_previous_power_failure_disable_recruitment(self):
        w=self.h.world;w.buildings.seed(self.site.id,'native:barracks')
        self.assertTrue(self.item(self.read(),'infantry','units')['available'])
        w.military._grid_short[A]=True
        self.assertFalse(self.item(self.read(),'infantry','units')['available'])
        w.military._grid_short[A]=False;w.resources.consume(A,'native:equipment',5)
        row=self.item(self.read(),'infantry','units')
        self.assertFalse(row['available']);self.assertEqual(row['costs']['native:equipment'],5)

    def test_available_first_stable_order_and_no_fake_levels(self):
        data=present(self.h.ui,self.read(),self.api.state(A))
        enabled=[r['available'] for r in data['buildings']]
        self.assertEqual(enabled,sorted(enabled,reverse=True))
        self.assertIsNone(self.item(data,'farm')['level'])
        self.assertFalse(self.item(data,'farm')['leveled'])
        self.assertEqual(self.item(data,'castle')['level'],1)
        self.assertTrue(self.item(data,'farm')['description'])
        self.assertIsNotNone(self.item(data,'farm')['expected_net'])
        v=frame(self.h.ui,self.api.state(A))
        self.assertFalse(any('Lv.' in label for t in v['tiles'] for label in t.get('building_labels',[])))

    def test_castle_layers_and_defense_do_not_sum_ordinal_level_numbers(self):
        self.h.world.buildings.seed(self.site.id,'native:castle',3)
        data=self.read();self.assertEqual(self.item(data,'castle')['price']['level'],4)
        v=frame(self.h.ui,self.api.state(A))
        defense=next(d for d in v['scene']['tile_defenses'] if (d['x'],d['y'])==self.site.position)
        self.assertEqual(defense['castle'],30)

    def test_building_without_current_power_is_warned_not_illegally_blocked(self):
        self.h.world.buildings.seed(self.site.id,'native:castle',3)
        data=self.read();row=self.item(data,'barracks')
        self.assertTrue(row['available'])

    def test_hidden_panel_uses_the_same_own_scope_and_rejects_invalid_counts(self):
        x,y=self.site.position
        panel=self.h.player(A).query('query',{'panel':'site_planning','x':x,'y':y})
        self.assertEqual(panel['buildings'],self.read()['buildings'])
        for count in (0,-1,True,1.5,'1'):
            with self.subTest(count=count),self.assertRaises(ValueError):self.api.site_options(A,x,y,count)
        other=next(s for s in self.h.world.buildings.snapshot().sites if s.owner==B)
        with self.assertRaises(ValueError):self.h.player(A).query('query',{'panel':'site_planning','x':other.position[0],'y':other.position[1]})

    def test_custom_nation_colors_survive_save_load_and_player_projection(self):
        cfg=config();cfg['desktop_mode']='player'
        cfg['nations'][0].update(name='青岚',color='66B6A7');cfg['nations'][1]['color']='#c09a77'
        h=GameHost.new(cfg);api=HumanAPI(h)
        state=api.state(A);view=frame(h.ui,state)
        self.assertEqual(state['colors'][A],'#66b6a7');self.assertEqual(view['names'][A],'青岚')
        self.assertLessEqual(set(state['colors']),{n['id'] for n in state['facts']['foreign']['nations']})
        before=h.to_save()
        with TemporaryDirectory() as directory:
            save=Path(directory)/'session.json';h.save(save);loaded=GameHost.load(save,h.cfg)
        self.assertEqual(loaded.to_save(),before)
        restored=frame(loaded.ui,HumanAPI(loaded).state(A))
        self.assertEqual(restored['colors'],view['colors']);self.assertEqual(restored['names'],view['names'])

    def test_invalid_color_is_rejected_before_world_creation(self):
        for color in ('red','#abc','ff0000ff','x123456','123456\n'):
            cfg=config();cfg['nations'][0]['color']=color
            with self.subTest(color=color),self.assertRaises(ValueError):GameHost.new(cfg)
