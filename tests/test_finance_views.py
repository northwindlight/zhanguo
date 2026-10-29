"""Shared rule decisions and player-only financial facts; no paid calls."""
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from host import GameHost
from mods.native.human import HumanAPI
from mods.native.army.defense import cover
from mods.ui.desktop_defense import project
from mods.ui.desktop_review import build_finance_fixture
from mods.ui.desktop_player import frame
from tests.test_human import config,A,B


class TestFinanceViews(TestCase):
    def test_auto_trade_preview_matches_submission_and_is_readonly(self):
        for mods in ('mods.toml','mods.experimental.toml'):
            with self.subTest(mods=mods):
                h=GameHost.new(config(mods));api=HumanAPI(h)
                values={'resource':'native:food','buy_below':20,'sell_above':50,'reserve_gold':70,'max_buy_price':1.25}
                before=h.to_save()
                with patch.object(h,'observer_snapshot',side_effect=AssertionError('observer leak')):
                    preview=api.auto_trade_options(A,**values)
                self.assertEqual(h.to_save(),before);self.assertTrue(preview['available'])
                actual=api.submit(A,'auto_trade',values)
                self.assertTrue(actual['ok']);self.assertEqual(actual['data'],preview['rule'])

    def test_invalid_auto_trade_preview_and_submit_use_same_rejection(self):
        for invalid in ({'buy_below':30,'sell_above':20},{'buy_limit':0},{'sell_below':5},
                        {'reserve_gold':-1},{'buy_below':True},{'max_buy_price':float('inf')}, {'enabled':1}):
            with self.subTest(invalid=invalid):
                h=GameHost.new(config());api=HumanAPI(h);before=h.to_save()
                values={'resource':'native:food',**invalid}
                self.assertFalse(api.auto_trade_options(A,**values)['available'])
                self.assertEqual(h.to_save(),before)
                if invalid.get('enabled')==1:
                    # Tool transport accepts coercible bools; the GUI sends
                    # typed booleans and previews the actual domain contract.
                    with self.assertRaises(ValueError):h.world.market.set_auto_trade(A,**values)
                elif 'sell_below' not in invalid:
                    result=api.submit(A,'auto_trade',values)
                    self.assertFalse(result['ok'])
                self.assertEqual(h.world.market.auto_trades(A)['rules'],[])
        with self.assertRaisesRegex(ValueError,'真人席位'):api.auto_trade_options(B,resource='native:food')

    def test_real_publications_and_only_owned_or_purchased_reports(self):
        fixture=build_finance_fixture();rows={s['name']:s['frame'] for s in fixture['frames']}
        self.assertEqual(rows['9']['reports'],[])
        self.assertEqual([(r['start'],r['end'],r['span']) for r in rows['10']['reports']],[(0,9,10)])
        self.assertEqual([(r['start'],r['end']) for r in rows['20']['reports']],[(0,9),(10,19)])
        self.assertTrue(all(r['owner']==A for r in rows['20']['reports']))
        self.assertEqual(rows['20']['facts']['bank']['purchases'],[])
        purchase=rows['purchased']['facts']['bank']['purchases'][0]
        self.assertEqual((purchase['buyer'],purchase['report']['owner'],purchase['report']['end']),(A,B,19))
        self.assertEqual(rows['20']['facts']['bank']['cash']-rows['purchased']['facts']['bank']['cash'],20)
        self.assertEqual(rows['purchased']['reports'],rows['20']['reports'])

    def test_bank_quote_actual_loan_and_save_roundtrip(self):
        h=GameHost.new(config());api=HumanAPI(h)
        site=next(s for s in h.world.buildings.snapshot().sites if s.owner==A and dict(s.resources).get('native:farmland',0))
        self.assertFalse(api.panel(A,{'panel':'quote','action':'borrow'})['can_borrow'])
        h.world.buildings.seed(site.id,'native:farm');h.world.buildings.seed(site.id,'native:wood-power-plant')
        h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
        quote=api.panel(A,{'panel':'quote','action':'borrow'});self.assertTrue(quote['can_borrow'])
        before=h.to_save();api.state(A);self.assertEqual(before,h.to_save())
        result=api.submit(A,'borrow',{});self.assertTrue(result['ok'])
        bank=api.state(A)['facts']['bank']
        self.assertEqual(bank['cash'],quote['cash']+quote['credit']-quote['fee'])
        self.assertEqual(bank['loan']['principal'],quote['credit'])
        self.assertEqual(bank['loan']['due'],quote['credit'])
        self.assertFalse(api.panel(A,{'panel':'quote','action':'borrow'})['can_borrow'])
        h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
        with TemporaryDirectory() as directory:
            path=Path(directory)/'session.json';h.save(path)
            self.assertEqual(HumanAPI(GameHost.load(path,config())).state(A)['facts']['bank'],api.state(A)['facts']['bank'])

    def test_cover_projection_uses_shared_rule_and_preserves_unknown_castle(self):
        scene={'ownership':[{'x':2,'y':3,'owner':A}],
               'tile_defenses':[{'x':2,'y':3,'terrain':25,'castle':20}],
               'armies':[{'owner':A,'id':1,'x':2,'y':3,'hp':80,'engaged':False},
                         {'owner':B,'id':2,'x':2,'y':3,'hp':80,'engaged':True}],
               'guards':[{'owner':'野人','id':3,'x':2,'y':3,'hp':100}]}
        project(scene)
        for row in scene['squad_defenses']:
            expected=cover(row['owner'],A,{B},25,20)
            self.assertEqual({k:row[k] for k in expected},expected)
        self.assertEqual(next(r for r in scene['squad_defenses'] if r['owner']==A)['total'],40)
        self.assertEqual(next(r for r in scene['squad_defenses'] if r['owner']==B)['total'],0)
        scene['tile_defenses'][0]['castle_known']=False;project(scene)
        self.assertIsNone(next(r for r in scene['squad_defenses'] if r['owner']==A)['total'])
        self.assertEqual(next(r for r in scene['squad_defenses'] if r['owner']==B)['total'],0)
        old=deepcopy(scene);project(scene);self.assertEqual(old,scene)

    def test_live_hud_cover_matches_bound_battle_api_without_observer(self):
        h=GameHost.new(config());api=HumanAPI(h);w=h.world
        self.assertTrue(api.submit(A,'declare_war',{'target':B,'confirm':True})['ok'])
        h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
        site=next(s for s in w.buildings.snapshot().sites if s.owner==A)
        w.buildings.seed(site.id,'native:castle',2)
        w.military.seed(A,'native:infantry',*site.position)
        enemy=w.military.seed(B,'native:infantry',*site.position)[0]
        h.player(B).call_api('native:army','attack',ids=[enemy.id],x=site.position[0],y=site.position[1])
        before=deepcopy(h.runtime.save_mod_state(w))
        with patch.object(h,'observer_scene',side_effect=AssertionError('observer leak')):
            battle=h.player(A).call_api('native:army','battle')
            visual=frame(h.ui,api.state(A))
        sides=next(row['sides'] for row in battle if tuple(row['position'])==site.position)
        for side in sides:
            displayed=next(r for r in visual['scene']['squad_defenses'] if r['owner']==side['owner'] and (r['x'],r['y'])==site.position)
            self.assertEqual(displayed['total'],side['combined_reduction'])
            self.assertEqual(displayed['terrain'],side['terrain_reduction'])
            self.assertEqual(displayed['castle'],side['castle_reduction'])
        self.assertEqual(before,h.runtime.save_mod_state(w))
