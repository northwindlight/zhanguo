"""逐军军需和批量回执：只使用合成世界，逐项核对实际副作用。"""
from copy import deepcopy
from unittest.mock import patch
from clients.model_view import model_view_for
import unittest

from engine.player import wire
from engine.runtime import DomainRejection
from tests import test_army_resources as resources, test_army_selection as selection
from tests import test_diplomacy_finish as hosts, test_world_domains as domains
Q,C,Z,Y=domains.Q,domains.C,domains.Z,domains.Y


class TestMilitaryRequirements(unittest.TestCase):
    def resource_world(self):
        fixture=resources.TestArmyResources();fixture.setUp();return fixture.world

    def test_per_unit_healing_preview_matches_settlement_with_limited_equipment(self):
        w=self.resource_world()
        for kind in ('native:infantry','native:militia','native:infantry'):
            unit=w.military.seed(Q,kind,1,1)[0];w.units.by_id(unit.id,Q).take_damage(40)
        w.resources.consume(Q,'native:equipment',w.resources.balance(Q,'native:equipment')-1)
        before=deepcopy(w.runtime.save_mod_state(w));plan=w.call_tool(Q,'logistics',{})
        self.assertEqual(w.runtime.save_mod_state(w),before)
        rows=plan['units']
        self.assertEqual([r['equipment_need'] for r in rows],[1,0,1])
        self.assertEqual([r['equipment_used'] for r in rows],[0.5,0,0.5])
        self.assertEqual([r['healing_hp'] for r in rows],[15,30,15])
        self.assertEqual([r['healing_status'] for r in rows],['equipment_shortage','ready','equipment_shortage'])
        self.assertEqual((plan['need'],plan['used'],plan['exempt_count']),(2,2,1))
        self.assertEqual((plan['equipment_need'],plan['healing_equipment'],plan['equipment_shortfall']),(2,1,1))
        hp={u.id:u.hp for u in w.units.of(Q)};supply=w.resources.balance(Q,'native:supply')
        w.military.supply()
        for row in rows:self.assertEqual(w.units.by_id(row['aid'],Q).hp-hp[row['aid']],row['healing_hp'])
        self.assertEqual(w.resources.balance(Q,'native:equipment'),0)
        self.assertEqual(supply-w.resources.balance(Q,'native:supply'),plan['used'])

    def test_supply_shortage_stops_even_free_militia_healing_but_not_exemption(self):
        w=self.resource_world()
        for kind in ('native:militia','native:infantry'):
            unit=w.military.seed(Q,kind,1,1)[0];w.units.by_id(unit.id,Q).take_damage(30)
        w.resources.consume(Q,'native:supply',w.resources.balance(Q,'native:supply'))
        plan=w.call_tool(Q,'logistics',{});rows=plan['units']
        self.assertEqual([r['supply_need'] for r in rows],[0,1])
        self.assertEqual([r['healing_status'] for r in rows],['supply_shortage']*2)
        self.assertEqual([r['starvation_damage'] for r in rows],[0,35])
        equipment=w.resources.balance(Q,'native:equipment');hp=[u.hp for u in w.units.of(Q)]
        w.military.supply()
        self.assertEqual([u.hp for u in w.units.of(Q)],[hp[0],hp[1]-35])
        self.assertEqual(w.resources.balance(Q,'native:equipment'),equipment)

    def test_full_health_and_engaged_tiles_do_not_demand_equipment(self):
        w=self.resource_world();w.military.seed(Q,'native:infantry',1,1)
        w.military.seed(Q,'native:infantry',2,1)
        w.units.by_id(2,Q).update(hp=40,engaged=True)
        rows=w.call_tool(Q,'logistics',{})['units']
        self.assertEqual([r['healing_status'] for r in rows],['full','engaged'])
        self.assertEqual([r['equipment_need'] for r in rows],[0,0])

    def test_militia_recovers_with_zero_equipment_and_catalog_states_cost(self):
        w=self.resource_world();w.military.seed(Q,'native:militia',1,1)
        w.units.by_id(1,Q).update(hp=38)
        w.resources.consume(Q,'native:equipment',w.resources.balance(Q,'native:equipment'))
        plan=w.call_tool(Q,'logistics',{})
        self.assertEqual(plan['healing_hp'],30);self.assertEqual(plan['equipment_need'],0)
        self.assertEqual(w.call_tool(Q,'unit_catalog',{})['native:militia']['healing']['equipment_per_30hp'],0)
        w.military.supply();self.assertEqual(w.units.by_id(1,Q).hp,68)

    def test_military_panel_econ_and_model_brief_have_same_per_unit_facts(self):
        h=hosts.TestDiplomacyFinish().host();w=h.world
        w.military.seed(Q,'native:militia',2,2);w.units.by_id(1,Q).update(hp=38)
        obs=h.player(Q).observe();h.ui.console.remember(obs)
        data=h.player(Q).query('logistics');econ=h.player(Q).query('econ')
        self.assertEqual(econ['preview']['military'],data)
        self.assertEqual(econ['preview']['inputs']['native:equipment'],0)
        for language in ('zh-CN','en-US'):
            view=model_view_for(h.ui, {'language':language});state,_=view.state(obs,[])
            from presentation.console import ConsoleUI
            console=ConsoleUI({'language':language},tool_layer=w.runtime.tool_layer)
            rendered=w.runtime.tool_layer.panel('logistics',data,{},Q,console)
            self.assertIn('#1',rendered);self.assertIn('30HP',rendered)
            self.assertIn('军需面板' if language=='zh-CN' else 'Military requirements',state)
        text=w.runtime.tool_layer.panel('econ',econ,{},Q,h.ui.console)
        self.assertIn('合计回血装备：需求0｜库存5｜实际扣除0',text);self.assertIn('合计补给',text)
        self.assertIn('不另扣黄金',text)

    def test_batch_rejection_distinguishes_moved_valid_and_newborn_for_tool_and_api(self):
        for api in (False,True):
            h=hosts.TestDiplomacyFinish().host();w=h.world;w.military.seed(Q,'native:infantry',2,2,3)
            w.units.by_id(1,Q).update(moved_turn=h.clock());w.units.by_id(3,Q).update(born_turn=h.clock())
            before=deepcopy(w.runtime.save_mod_state(w));args={'ids':[1,2,3],'x':2,'y':2}
            result=(h.player(Q).call_api('native:army','move',args) if api else h.player(Q).submit('military',{'action':'move',**args}))
            self.assertFalse(result['ok']);self.assertEqual(result['code'],'domain_rejected')
            self.assertEqual([r['status'] for r in result['data']['items']],['rejected','not_executed','rejected'])
            self.assertIn('已移动',result['data']['items'][0]['message'])
            self.assertIn('校验通过',result['data']['items'][1]['message'])
            self.assertIn('新兵',result['data']['items'][2]['message'])
            self.assertEqual(w.runtime.save_mod_state(w),before)
            text=h.ui.console.receipt_body('move',args,result,Q)
            for aid in (1,2,3):self.assertIn('#'+str(aid),text)

    def test_batch_routes_report_each_units_range_failure_without_moving_valid_units(self):
        w=selection.TestArmySelection().world();w.units.by_id(2,Q).update(x=7,y=7)
        before=deepcopy(w.units.to_save())
        with self.assertRaises(DomainRejection) as ctx:w.call_tool(Q,'move',{'ids':[1,2],'x':2,'y':2})
        self.assertEqual([r['status'] for r in ctx.exception.details['items']],['not_executed','rejected'])
        self.assertIn('移动范围',ctx.exception.details['items'][1]['message'])
        self.assertEqual(w.units.to_save(),before)

    def test_mixed_original_tile_attack_reports_separate_consumption_flags(self):
        w=selection.TestArmySelection().world();w.units.by_id(1,Q).update(x=3,y=2,moved_turn=w.government.period())
        receipt=w.call_tool(Q,'attack',{'ids':[1,2],'x':3,'y':2})
        self.assertEqual([r['position_changed'] for r in receipt.items],[False,True])
        self.assertEqual([r['consumed_action'] for r in receipt.items],[False,True])
        self.assertIn('原地进攻',receipt.items[0]['message'])

    def test_retreat_and_disband_report_valid_members_without_mutation(self):
        for action in ('retreat','disband'):
            w=selection.TestArmySelection().world();w.units.by_id(1,Q).update(engaged=True)
            before=deepcopy(w.units.to_save());args={'ids':[1,2,999]}
            if action=='retreat':args.update(x=3,y=2)
            with self.assertRaises(DomainRejection) as ctx:w.call_tool(Q,action,args)
            rows=ctx.exception.details['items']
            self.assertEqual([r['aid'] for r in rows],[1,2,999])
            self.assertEqual(rows[0 if action=='retreat' else 1]['status'],'not_executed')
            self.assertIn('不存在',rows[-1]['message']);self.assertEqual(w.units.to_save(),before)

    def test_reachable_marks_action_status_and_unknown_ids_are_atomic(self):
        w=selection.TestArmySelection().world();w.units.by_id(1,Q).update(moved_turn=w.government.period())
        before=deepcopy(w.units.to_save());rows=w.call_tool(Q,'reachable',{'ids':[1,2]})
        self.assertEqual([r['action_available'] for r in rows],[False,True])
        self.assertIn('已移动',rows[0]['action_reason'])
        with self.assertRaises(DomainRejection) as ctx:w.call_tool(Q,'reachable',{'ids':[1,999]})
        self.assertEqual(len(ctx.exception.details['items']),2);self.assertEqual(w.units.to_save(),before)

    def test_blind_failure_burns_each_action_and_receipt_says_so(self):
        fixture=domains.TestWorldDomains();w=selection.TestArmySelection().world();fixture.site(w,'scenario:hidden',C,3,2)
        # Hide an in-range target. Out-of-range requests must be rejected
        # without burning an action, even if their target is unseen.
        from mods.native.vision.mod import Vision
        with patch.object(Vision,'visible',return_value=False):
            receipt=w.call_tool(Q,'move',{'ids':[1,2],'x':3,'y':2})
        self.assertEqual(receipt.status,'failed')
        self.assertEqual([r['consumed_action'] for r in receipt.items],[True,True])
        self.assertTrue(all(u.moved_turn==w.government.period() for u in w.units.of(Q)))
        h=hosts.TestDiplomacyFinish().host();data=wire(receipt)
        text=h.ui.console.receipt_body('move',{'ids':[1,2],'x':3,'y':2},{'ok':False,'data':data},Q)
        self.assertIn('#1',text);self.assertIn('#2',text);self.assertIn('额度已消耗',text)

    def test_founder_batch_reports_all_invitees_and_does_not_charge_on_rejection(self):
        fixture=domains.TestWorldDomains();w=fixture.world();fixture.found(w,(C,Y))
        before=deepcopy(fixture.save(w))
        with self.assertRaises(DomainRejection) as ctx:
            w.call_tool(Q,'bloc_found',{'bloc':'scenario:new','name':'新盟','tos':[C,Z,'scenario:missing']})
        rows={r['target']:r for r in ctx.exception.details['items']}
        self.assertIn('已在联盟',rows[C]['message']);self.assertEqual(rows[Z]['status'],'not_submitted')
        self.assertIn('不存在',rows['scenario:missing']['message'])
        self.assertEqual(fixture.save(w),before)

    def test_bulk_disband_success_is_rendered_per_unit(self):
        h=hosts.TestDiplomacyFinish().host();w=h.world;w.military.seed(Q,'native:infantry',2,2,2)
        receipt=h.player(Q).submit('military',{'action':'disband','ids':[1,2]})
        text=h.ui.console.receipt_body('disband',{'ids':[1,2]},receipt,Q)
        self.assertTrue(receipt['ok']);self.assertIn('已遣散 #1',text);self.assertIn('已遣散 #2',text)
        self.assertFalse(w.units.of(Q))


if __name__=='__main__':unittest.main()
