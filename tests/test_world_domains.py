"""剩余领域的正式入口验收。仅临时合成世界，不运行旧客户端。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from engine.runtime import GameMod, Runtime, ModError
from engine.state import unit_context

ROOT=Path(__file__).resolve().parents[1]
Q='scenario:qin';C='scenario:chu';Z='scenario:zhao';Y='scenario:yan';B='scenario:bloc'


class FixedMap(GameMod):
    def mixins(self):return ()
    def provides(self):return ('map:generation',)
    def objects(self, world):return {'map:generation':self}
    def cell(self, seed, size, x, y, terrain=None):
        from types import SimpleNamespace
        # 地图契约包含地名（map_generation/names.py）：替换实现也要给。
        return SimpleNamespace(position=(x,y),terrain=terrain or 'native:plain',
                               resources=(('native:farmland',5),('native:gold',2)),
                               name=f'定{x}-{y}')


class TestWorldDomains(unittest.TestCase):
    def world(self, runtime=None):
        world=(runtime or Runtime.load(ROOT/'mods.toml')).world_class()()
        world.territory.configure(8,73)
        for ident,name in ((Q,'秦'),(C,'楚'),(Z,'赵'),(Y,'燕')):
            world.nations.create(ident,name,{'native:gold':10000,'native:food':100,'native:wood':1000,'native:equipment':100,'native:supply':1000})
        return world
    def site(self, w, ident, owner, x, y, *, hall=True, core=True):
        w.territory.create(ident,owner,x,y,terrain='native:plain',core=owner if core else None)
        if hall:w.buildings.seed(ident,'native:town-hall')
        return ident
    def save(self,w):return json.loads(json.dumps(w.runtime.save_mod_state(w),allow_nan=False))
    def call(self,w,actor,tool,**args):return w.call_tool(actor,tool,args)
    def cycle(self,w):
        result=w.resolve_turn();w.phase='行动';return result
    def declare(self,w,a=Q,b=C):
        c=self.call(w,a,'declare_war',target=b);self.cycle(w)
        self.assertTrue(w.diplomacy.war_between(a,b));return c
    def found(self,w,members=(Q,Z)):
        self.call(w,members[0],'bloc_found',bloc=B,name='合纵',tos=list(members[1:]));self.cycle(w)
        for actor in members[1:]:
            offer=w.diplomacy.snapshot(actor).offers[0]
            self.call(w,actor,'accept_proposal',offer=offer.id)
        self.cycle(w)
        self.assertEqual(w.diplomacy.alliance(members[0]).members,members)
    def restore(self,w):
        saved=self.save(w);other=w.runtime.world_class()();w.runtime.load_mod_state(other,saved)
        self.assertEqual(self.save(other),saved);return other
    def edition(self, *, omit=(), map_entry=None):
        source=(ROOT/'mods/native/mod.toml').read_text()
        import tomllib
        members=tomllib.loads(source)['Distribution']['Members']
        absolute=[]
        for member in members:
            if member in omit:continue
            absolute.append(str(ROOT/'mods/native'/member))
        source=source.replace('Members = '+json.dumps(members),'Members = '+json.dumps(absolute))
        return source

    def test_world_config_map_identity_and_query_have_no_mutations(self):
        w=self.world();self.site(w,'scenario:capital',Q,1,1)
        before=self.save(w)
        first=w.territory.cell(3,3);self.assertEqual(first,w.territory.cell(3,3))
        self.assertEqual(self.call(w,Q,'land')[0].owner,Q)
        self.assertEqual(self.save(w),before)
        self.assertFalse(first.tile.id)
        with self.assertRaises(ValueError):w.territory.configure(9,74)
        with self.assertRaises(ValueError):w.territory.create('scenario:duplicate',Q,1,1)
        self.assertEqual(self.save(w),before)

    def test_replace_map_member_without_changing_territory(self):
        with TemporaryDirectory() as directory:
            path=Path(directory);(path/'map').mkdir()
            source=self.edition().replace(str(ROOT/'mods/native/map_generation/mod.toml'),'map/mod.toml')
            (path/'mod.toml').write_text(source)
            (path/'map/mod.toml').write_text('[Mod]\nId="native:map-generation"\nVersion="fixed"\nEntry="tests.test_world_domains:FixedMap"\n')
            w=self.world(Runtime.load(path));self.site(w,'scenario:capital',Q,1,1)
            self.assertEqual(dict(w.territory.get('scenario:capital').resources),{'native:farmland':5,'native:gold':2})
            self.assertEqual(w.territory.cell(7,7).tile.terrain,'native:plain')
            self.restore(w)

    def test_missing_domain_fails_during_loading_and_optional_policy_can_be_removed(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'mod.toml').write_text(self.edition(omit=('map_generation/mod.toml',)))
            with self.assertRaises(ModError):Runtime.load(path)
            (path/'mod.toml').write_text(self.edition(omit=('polities/huns/mod.toml',)))
            w=self.world(Runtime.load(path));self.site(w,'scenario:capital',Q,1,1)
            self.assertIsNone(w.runtime.find_policy('native:huns'));self.cycle(w)
            self.assertEqual(w.nation_actors,(Q,C,Z,Y))

    def test_national_plan_end_action_gate_and_clock(self):
        w=self.world();self.site(w,'scenario:capital',Q,1,1)
        self.call(w,Q,'plan',text='发展经济并保持独立')
        self.call(w,Q,'end_turn',summary='本期行动已经完成')
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,Q,'buy',resource='native:wood',quantity=1)
        self.assertEqual(self.save(w),before)
        self.assertTrue(self.call(w,Q,'government').ended)
        self.call(w,C,'send_letter',target=Q,text='保持和平')
        self.cycle(w)
        self.assertEqual(w.government.period(),1)
        self.assertFalse(self.call(w,Q,'government').ended)
        for _ in range(9):self.cycle(w)
        with self.assertRaises(ValueError):self.call(w,Q,'end_turn',summary='本期行动已经完成')
        self.call(w,Q,'plan',text='继续发展')
        self.call(w,Q,'end_turn',summary='国策已修订完成')

    def test_saved_runtime_phase_and_action_gate_remain_enforced(self):
        w=self.world();self.call(w,Q,'plan',text='发展')
        self.call(w,Q,'end_turn',summary='本期已经完成')
        other=self.restore(w)
        with self.assertRaises(ValueError):self.call(other,Q,'send_letter',target=C,text='又行动')
        self.cycle(w);w.phase='已结算';other=self.restore(w)
        self.assertEqual(other.phase,'已结算')
        with self.assertRaises(ValueError):self.call(other,Q,'buy',resource='native:wood',quantity=1)

    def test_diplomacy_submit_reserve_deduplicate_and_revalidate_refund(self):
        w=self.world();cash=w.resources.balance(Q,'native:gold')
        command=self.call(w,Q,'guarantee',target=C)
        self.assertEqual(command.status,'submitted');self.assertEqual(w.diplomacy.snapshot(Q).pacts,())
        self.assertEqual(w.resources.balance(Q,'native:gold'),cash-10)
        again=self.call(w,Q,'guarantee',target=C);self.assertEqual(again.id,command.id)
        self.assertEqual(w.resources.balance(Q,'native:gold'),cash-10)
        w.nations.extinguish(C,'synthetic cancellation')
        results=self.cycle(w)['diplomacy_results']
        self.assertEqual(results[0].status,'failed')
        self.assertEqual(w.resources.balance(Q,'native:gold'),cash)
        self.assertEqual(w.diplomacy.snapshot(Q).pacts,())

    def test_alliance_stable_id_rename_and_personal_pacts_are_removed(self):
        w=self.world();self.call(w,Q,'guarantee',target=C);self.cycle(w)
        self.found(w)
        self.assertEqual(w.diplomacy.snapshot(Q).pacts,())
        entity=w.diplomacy.entity(Q)
        self.call(w,Q,'bloc_rename',name='新合纵');self.cycle(w)
        self.assertEqual(w.diplomacy.entity(Q),entity)
        self.assertEqual(w.diplomacy.alliance(Q).name,'新合纵')
        self.call(w,Q,'bloc_transfer',target=Z);self.cycle(w)
        self.assertEqual(w.diplomacy.alliance(Q).chief,Z)
        self.restore(w)

    def test_vote_next_period_no_early_tally_and_abstention_is_not_no(self):
        w=self.world();self.found(w)
        self.call(w,Q,'guarantee',target=C);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        self.assertEqual(vote.deadline,w.government.period())
        self.call(w,Z,'vote',vote=vote.id,accept=True)
        self.assertEqual(w.diplomacy.snapshot(Q).pacts,())
        self.assertEqual(len(w.diplomacy.snapshot(Q).votes),1)
        self.cycle(w)
        self.assertEqual(len(w.diplomacy.snapshot(Q).pacts),1)
        self.assertEqual(w.diplomacy.snapshot(Q).pacts[0].a,'bloc/'+B)

    def test_vote_rejection_by_chief_is_an_ordinary_ballot(self):
        w=self.world();self.found(w,(Q,Z,Y))
        self.call(w,Q,'guarantee',target=C);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        self.call(w,Q,'vote',vote=vote.id,accept=False)
        self.call(w,Z,'vote',vote=vote.id,accept=True)
        self.call(w,Y,'vote',vote=vote.id,accept=True)
        self.cycle(w);self.assertEqual(len(w.diplomacy.snapshot(Q).pacts),1)

    def test_vote_failure_and_join_requires_alliance_approval(self):
        w=self.world();self.found(w)
        self.call(w,C,'bloc_join',bloc=B);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        self.call(w,Q,'vote',vote=vote.id,accept=True);self.call(w,Z,'vote',vote=vote.id,accept=False)
        self.cycle(w);self.assertIsNone(w.diplomacy.alliance(C))
        self.call(w,C,'bloc_join',bloc=B);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        self.call(w,Z,'vote',vote=vote.id,accept=True);self.cycle(w)
        self.assertEqual(w.diplomacy.entity(C),'bloc/'+B)

    def test_war_one_hop_guarantees_do_not_chain(self):
        w=self.world()
        self.call(w,Z,'guarantee',target=C);self.call(w,Y,'guarantee',target=Z);self.cycle(w)
        self.declare(w)
        self.assertTrue(w.diplomacy.war_between(Q,Z));self.assertFalse(w.diplomacy.war_between(Q,Y))
        self.assertEqual(w.diplomacy.snapshot(Q).wars[0].defenders,(C,Z))

    def test_defense_pact_and_wartime_membership_freeze(self):
        w=self.world();self.found(w)
        self.call(w,Y,'propose_defense',target=C);self.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        self.call(w,C,'accept_proposal',offer=offer.id);self.cycle(w)
        self.call(w,Q,'declare_war',target=C);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0];self.call(w,Z,'vote',vote=vote.id,accept=True);self.cycle(w)
        self.assertTrue(w.diplomacy.war_between(Z,Y))
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,Z,'bloc_leave')
        with self.assertRaises(ValueError):self.call(w,C,'break_defense',target=Y)
        self.assertEqual(self.save(w),before)

    def test_peace_payment_is_atomic_and_truce_blocks_redeclaration(self):
        w=self.world();self.declare(w)
        self.call(w,Q,'offer_peace',target=C,kind='pay',gold=100,truce=3);self.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        cash=w.resources.balance(C,'native:gold');self.call(w,C,'accept_peace',offer=offer.id)
        self.cycle(w)
        self.assertEqual(w.resources.balance(C,'native:gold'),cash-10+100)
        self.assertFalse(w.diplomacy.war_between(Q,C));self.assertTrue(w.diplomacy.active_truce(Q,C))
        with self.assertRaises(ValueError):self.call(w,Q,'declare_war',target=C)
        for _ in range(3):self.cycle(w)
        self.call(w,Q,'declare_war',target=C)

    def test_unowned_occupation_sets_core_and_peace_rebases_wartime_conquests(self):
        w=self.world();self.site(w,'scenario:capital',Q,1,1)
        w.military._guard(2,1)
        next(u for u in w.units if u.owner=='野人' and (u.x,u.y)==(2,1)).take_damage(999)
        unit=w.military.seed(Q,'native:infantry',1,1)[0]
        self.call(w,Q,'attack',ids=[unit.id],x=2,y=1)
        self.assertEqual(w.territory.cell(2,1).tile.core,Q)

        w=self.world();self.site(w,'scenario:capital',Q,1,1)
        w.territory.create('scenario:neutral',None,2,1,core=None)
        unit=w.military.seed(Q,'native:infantry',1,1)[0]
        self.call(w,Q,'attack',ids=[unit.id],x=2,y=1)
        self.assertEqual(w.territory.get('scenario:neutral').tile.core,Q)

        w=self.world();self.site(w,'scenario:qin-capital',Q,1,1);self.site(w,'scenario:chu-capital',C,2,1)
        self.declare(w)
        w.territory.transfer('scenario:chu-capital',Q)
        self.assertEqual(w.territory.get('scenario:chu-capital').tile.core,C)
        self.call(w,Q,'offer_peace',target=C);self.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        self.call(w,C,'accept_peace',offer=offer.id);self.cycle(w)
        self.assertEqual(w.territory.get('scenario:chu-capital').tile.core,Q)

    def test_wartime_occupation_returns_allied_core_to_ally(self):
        w=self.world();self.found(w)
        self.site(w,'scenario:capital',Q,1,1)
        w.territory.create('scenario:allied-core',C,2,1,core=Z)
        self.call(w,Q,'declare_war',target=C);self.cycle(w)
        vote=w.diplomacy.snapshot(Z).votes[0]
        self.call(w,Z,'vote',vote=vote.id,accept=True);self.cycle(w)
        self.assertTrue(w.diplomacy.war_between(Q,C))
        unit=w.military.seed(Q,'native:infantry',1,1)[0]
        self.call(w,Q,'attack',ids=[unit.id],x=2,y=1)
        fact=w.territory.get('scenario:allied-core')
        self.assertEqual((fact.owner,fact.tile.core),(Z,Z))

    def test_unaffordable_peace_reverts_whole_transition_and_refunds_acceptance_fee(self):
        w=self.world();self.declare(w)
        self.call(w,Q,'offer_peace',target=C,kind='demand',gold=20000);self.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0];cash=w.resources.balance(C,'native:gold')
        self.call(w,C,'accept_peace',offer=offer.id);result=self.cycle(w)['diplomacy_results'][0]
        self.assertEqual(result.status,'failed');self.assertTrue(w.diplomacy.war_between(Q,C))
        self.assertEqual(w.resources.balance(C,'native:gold'),cash)
        self.assertEqual(len(w.diplomacy.snapshot(C).offers),1)

    def test_diplomatic_fees_enter_spending_and_do_not_fake_gdp(self):
        w=self.world();self.call(w,Q,'guarantee',target=C);self.cycle(w)
        report=w.economy.turns(Q)[0]
        self.assertEqual(report.spending.total,10);self.assertEqual(report.gdp.total,0)
        self.assertEqual(report.cumulative_spending.total,10)

    def test_letter_gift_and_spy_are_delayed_private_and_sampled_at_delivery(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,6,6)
        self.call(w,Q,'send_letter',target=C,text='和平提议')
        self.call(w,Q,'gift',target=C,resource='native:food',quantity=5)
        self.call(w,Q,'spy',target=C)
        self.assertEqual(self.call(w,C,'intel'),())
        before=w.resources.balance(C,'native:food');self.cycle(w)
        self.assertEqual(self.call(w,C,'intel'),())
        self.cycle(w)
        self.assertEqual(w.resources.balance(C,'native:food'),before+5)
        self.assertEqual({m.kind for m in self.call(w,C,'intel')},{'letter','gift'})
        w.resources.credit(C,'native:food',7)
        self.cycle(w);self.cycle(w)
        spy=next(m for m in self.call(w,Q,'intel') if m.kind=='spy')
        self.assertEqual(spy.sampled,3);self.assertEqual(spy.payload['inventory']['native:food'],before+12)
        self.assertNotIn('units',spy.payload)
        self.assertEqual(self.call(w,Z,'intel'),())
        spy.payload['inventory']['native:food']=999
        self.assertNotEqual(self.call(w,Q,'intel')[0].payload['inventory']['native:food'],999)

    def test_dispatch_escrow_deduplication_and_target_death_refund(self):
        w=self.world();cash=w.resources.balance(Q,'native:gold');food=w.resources.balance(Q,'native:food')
        d=self.call(w,Q,'gift',target=C,resource='native:food',quantity=4)
        self.assertEqual(self.call(w,Q,'gift',target=C,resource='native:food',quantity=4).id,d.id)
        self.assertEqual(w.resources.balance(Q,'native:food'),food-4)
        w.nations.extinguish(C,'synthetic')
        self.cycle(w)
        self.assertEqual(w.resources.balance(Q,'native:food'),food)
        self.assertEqual(w.resources.balance(Q,'native:gold'),cash)

    def test_letter_center_discount_does_not_discount_extra_characters(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);w.buildings.seed('scenario:qc','native:diplomacy-center')
        d=self.call(w,Q,'send_letter',target=C,text='字'*31)
        self.assertEqual(d.fee,17)

    def test_visibility_and_foreign_building_secrecy_and_event_snapshot(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        w.buildings.seed('scenario:cc','native:castle');w.buildings.seed('scenario:cc','native:barracks')
        view=self.call(w,Q,'view');enemy=next(t for t in view if t.position==(2,1))
        self.assertEqual(set(dict(enemy.buildings)),{'native:town-hall','native:castle'})
        self.assertEqual(enemy.resources,())
        event=w.intelligence.emit('test','只有秦楚可见',position=(2,1))
        self.assertNotIn(Y,event.recipients)
        w.territory.transfer('scenario:qc',Y)
        self.assertEqual(w.intelligence.events(Y),())
        self.assertEqual(w.intelligence.events(Q)[0],event)

    def test_alliance_shared_view_and_watchtower_radius(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:zc',Z,6,6)
        self.assertFalse(w.runtime.object_call(w,'native:vision','visible',Q,6,6))
        self.found(w)
        self.assertTrue(w.runtime.object_call(w,'native:vision','visible',Q,6,6))
        self.assertFalse(w.runtime.object_call(w,'native:vision','visible',Q,4,4))
        w.buildings.seed('scenario:zc','native:watchtower')
        self.assertTrue(w.runtime.object_call(w,'native:vision','visible',Q,4,4))

    def test_movement_does_not_capture_and_joint_attack_invalidity_is_atomic(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        a,b=w.military.seed(Q,'native:infantry',1,1,2)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,Q,'attack',ids=[a.id,999],x=2,y=1)
        self.assertEqual(self.save(w),before)
        receipt=self.call(w,Q,'move',aid=a.id,x=2,y=1)
        self.assertTrue(receipt.position_changed);self.assertFalse(receipt.occupied)
        self.assertIsNone(w.territory.owner(2,1))
        with self.assertRaises(ValueError):self.call(w,Q,'attack',ids=[a.id],x=3,y=1)

    def test_static_guard_views_do_not_materialize_or_respawn(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        before=self.save(w);seen=self.call(w,Q,'army')
        self.assertTrue(any(u.owner=='野人' and (u.x,u.y)==(2,1) for u in seen))
        self.assertEqual(self.save(w),before)
        soldiers=w.military.seed(Q,'native:infantry',1,1,4)
        self.call(w,Q,'attack',ids=[u.id for u in soldiers],x=2,y=1)
        self.cycle(w)
        self.assertEqual(w.territory.owner(2,1),Q)
        self.assertFalse(any(u.owner=='野人' and (u.x,u.y)==(2,1) for u in self.call(w,Q,'army')))
        other=self.restore(w)
        self.assertEqual(self.call(other,Q,'army'),self.call(w,Q,'army'))

    def test_recruitment_newborn_quota_and_resources(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);w.buildings.seed('scenario:qc','native:barracks')
        food=w.resources.balance(Q,'native:food');equip=w.resources.balance(Q,'native:equipment')
        recruits=self.call(w,Q,'recruit',site='scenario:qc')
        self.assertEqual(w.resources.balance(Q,'native:food'),food-10)
        self.assertEqual(w.resources.balance(Q,'native:equipment'),equip-5)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,Q,'move',aid=recruits[0].id,x=2,y=1)
        with self.assertRaises(ValueError):self.call(w,Q,'recruit',site='scenario:qc')
        self.assertEqual(self.save(w),before)
        self.cycle(w)
        self.assertGreater(w.economy.turns(Q)[0].recruitment.total,0)
        self.call(w,Q,'move',aid=recruits[0].id,x=2,y=1)
        with self.assertRaises(ValueError):self.call(w,Q,'recruit',site='scenario:qc')

    def test_supply_shortage_and_healing_expenses(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        a=w.military.seed(Q,'native:infantry',1,1)[0];w.units.by_id(a.id,Q).take_damage(50)
        before=w.resources.balance(Q,'native:equipment');self.cycle(w)
        self.assertEqual(w.units.by_id(a.id,Q).hp,80)
        self.assertEqual(w.resources.balance(Q,'native:equipment'),before-1)
        w.resources.consume(Q,'native:supply',w.resources.balance(Q,'native:supply'))
        self.cycle(w);self.assertEqual(w.units.by_id(a.id,Q).hp,45)
        self.assertEqual(w.resources.balance(Q,'native:equipment'),before-1)

    def test_retreat_is_pending_until_after_battle(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        a=w.military.seed(Q,'native:infantry',2,1)[0];b=w.military.seed(C,'native:infantry',2,1)[0]
        self.declare(w)
        self.call(w,Q,'attack',ids=[a.id],x=2,y=1)
        receipt=self.call(w,Q,'retreat',aid=a.id,x=1,y=1)
        self.assertEqual(receipt.status,'submitted');self.assertTrue(w.units.by_id(a.id,Q).engaged)
        self.assertEqual((w.units.by_id(a.id,Q).x,w.units.by_id(a.id,Q).y),(2,1))
        self.cycle(w)
        unit=w.units.by_id(a.id,Q);self.assertLess(unit.hp,100);self.assertEqual((unit.x,unit.y),(1,1))
        self.assertFalse(unit.engaged)

    def test_peace_effect_prevents_current_period_battle(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        a=w.military.seed(Q,'native:infantry',1,1)[0];w.military.seed(C,'native:infantry',2,1)
        self.declare(w);self.call(w,Q,'offer_peace',target=C);self.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        self.call(w,Q,'attack',ids=[a.id],x=2,y=1)
        self.call(w,C,'accept_peace',offer=offer.id)
        result=self.cycle(w)
        self.assertEqual(result['battles'],());self.assertEqual(w.units.by_id(a.id,Q).hp,100)
        self.assertFalse(w.units.by_id(a.id,Q).engaged)
        self.assertEqual((w.units.by_id(a.id,Q).x,w.units.by_id(a.id,Q).y),(1,1))

    def test_defender_must_retreat_and_keeps_defensive_cover(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        a=w.military.seed(Q,'native:infantry',2,1)[0];b=w.military.seed(C,'native:infantry',2,1)[0]
        self.declare(w);self.call(w,Q,'attack',ids=[a.id],x=2,y=1)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,C,'move',aid=b.id,x=3,y=1)
        self.assertEqual(self.save(w),before)
        self.call(w,C,'retreat',aid=b.id,x=3,y=1)
        self.assertEqual(w.units.by_id(b.id,C).retreat_cover,50)
        self.cycle(w);defender=w.units.by_id(b.id,C)
        self.assertLess(defender.hp,100);self.assertGreater(defender.hp,60)
        self.assertEqual((defender.x,defender.y),(3,1))

    def test_damage_overflow_and_desert_castle_defense_match_original_rules(self):
        from unittest.mock import patch
        import random
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        w.territory.create('scenario:cc',C,2,1,terrain='native:desert')
        w.buildings.seed('scenario:cc','native:castle',2)
        w.military.seed(Q,'native:infantry',2,1,2)
        defenders=w.military.seed(C,'native:infantry',2,1,2)
        self.declare(w)
        w.units.by_id(defenders[0].id,C).take_damage(80)
        w.units.by_id(defenders[1].id,C).take_damage(10)
        self.call(w,Q,'attack',ids=[u.id for u in w.military.snapshot(Q)],x=2,y=1)
        with patch.object(random.Random,'randint',return_value=3):
            from engine.state import unit_context
            with unit_context('native:army'):report=w.military.battle()
        # Desert -10% and two castle levels 20% compose to 12% defense;
        # die 3 applies -5%, so round(100 * .95 * .88) = 84.
        self.assertEqual(dict(report[0][2])[C],84)
        self.assertIsNone(w.units.by_id(defenders[0].id,C))
        self.assertEqual(w.units.by_id(defenders[1].id,C).hp,26)

    def test_retreating_winner_does_not_take_land_and_out_of_range_has_no_penalty(self):
        from unittest.mock import patch
        import random
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        a=w.military.seed(Q,'native:infantry',2,1)[0];b=w.military.seed(C,'native:infantry',2,1)[0]
        self.declare(w);self.call(w,Q,'attack',ids=[a.id],x=2,y=1)
        self.call(w,Q,'retreat',aid=a.id,x=1,y=1)
        w.units.by_id(b.id,C).take_damage(99)
        with patch.object(random.Random,'randint',return_value=3):self.cycle(w)
        self.assertEqual(w.territory.owner(2,1),C)
        self.assertEqual((w.units.by_id(a.id,Q).x,w.units.by_id(a.id,Q).y),(1,1))
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,Q,'move',aid=a.id,x=7,y=7)
        self.assertEqual(self.save(w),before)

    def test_extinction_releases_ruins_forces_world_peace_and_never_uses_pending_hall(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        self.site(w,'scenario:ruin',C,3,1,hall=False)
        w.buildings.seed('scenario:ruin','native:farm');w.succession.enroll(Q);w.succession.enroll(C)
        w.military.seed(Q,'native:infantry',1,1)
        self.declare(w)
        w.territory.transfer('scenario:cc',Q)
        result=self.cycle(w)
        self.assertEqual(result['extinct_nations'],(C,))
        self.assertNotIn(C,w.nation_actors);self.assertIsNone(w.territory.owner(3,1))
        self.assertEqual(w.territory.get('scenario:cc').tile.core,Q)
        self.assertEqual(w.buildings.count('scenario:ruin','native:farm'),1)
        self.assertFalse(w.diplomacy.at_war(Q));self.assertTrue(w.diplomacy.active_truce(Q,Z))
        before=w.resources.balance(C,'native:food');self.cycle(w)
        self.assertEqual(w.resources.balance(C,'native:food'),before)
        self.restore(w)

    def test_incomplete_hall_does_not_save_enrolled_nation(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1,hall=False)
        w.buildings.seed('scenario:qc','native:town-hall',completed=False);w.succession.enroll(Q)
        result=self.cycle(w)
        self.assertEqual(result['extinct_nations'],(Q,))
        self.assertEqual(w.buildings.count('scenario:qc','native:town-hall'),1)
        self.assertIsNone(w.territory.owner(1,1))

    def test_policy_initialization_overrides_scope_permissions_and_persistence(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        self.assertTrue(w.runtime.activate_actor(w,Q,'native:huns'))
        self.assertFalse(w.runtime.activate_actor(w,Q,'native:huns'))
        self.assertEqual(len(w.military.snapshot(Q)),6)
        self.assertEqual(w.resources.balance(Q,'native:gold'),1000)
        self.assertEqual(w.runtime.parameter(w,Q,'build_cost_pct',100),130)
        self.assertEqual(w.runtime.parameter(w,C,'build_cost_pct',100),100)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,Q,'guarantee',target=C)
        with self.assertRaises(ValueError):self.call(w,C,'guarantee',target=Q)
        self.assertEqual(self.save(w),before)
        self.call(w,Q,'buy',resource='native:wood',quantity=15)
        receipt=self.call(w,Q,'build',site='scenario:qc',kind='native:farm')
        self.assertEqual(dict(receipt.costs)['native:gold'],26)
        other=self.restore(w);self.assertEqual(other.polity[Q],'native:huns')
        with self.assertRaises(ValueError):self.call(other,Q,'gift',target=C,resource='native:food',quantity=1)
        panel=w.runtime.panel(w,Q,'polity');panel['overrides']['build_cost_pct']=999
        self.assertEqual(w.runtime.parameter(w,Q,'build_cost_pct',100),130)

    def test_policy_schedule_is_seeded_and_scope_appears_only_with_loaded_policy(self):
        w=self.world();cfg=[{'name':'scenario:new','polity':'native:huns','enable_turn':5,'enable_turn_max':15}]
        first=w.runtime.actor_schedule(w,cfg,seed=73)
        self.assertEqual(w.runtime.actor_schedule(w,cfg,seed=73),first)
        self.assertTrue(5<=first['scenario:new']<=15)
        self.assertEqual(w.runtime.initial_actors(cfg),[])
        cfg[0]['appearance']='initial';self.assertEqual(w.runtime.initial_actors(cfg),['scenario:new'])

    def test_cross_domain_write_grants_and_read_snapshots(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        before=self.save(w)
        with unit_context('native:government'):
            with self.assertRaises(ModError):w.territory.transfer('scenario:qc',C)
            with self.assertRaises(ModError):w.diplomacy.submit(Q,'declare_war',target=C)
            with self.assertRaises(ModError):w.military.seed(Q,'native:infantry',1,1)
            with self.assertRaises(ModError):w.intelligence.emit('bad','bad')
            with self.assertRaises(ModError):w.runtime.object_call(w,'native:territory','transfer','scenario:qc',C)
        self.assertEqual(self.save(w),before)
        numbers=w.runtime.enumerate_numbers();numbers['native:map-generation']['TERRAINS']['平原']['木头'][0]=999
        self.assertNotEqual(w.runtime.enumerate_numbers()['native:map-generation']['TERRAINS']['平原']['木头'][0],999)

    def test_invalid_loaded_domain_state_is_rejected(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.found(w)
        data=self.save(w)
        cases=[]
        for mutate in (lambda d:d['native:territory']['tiles'][0].update(position=[99,99]),
                       lambda d:d['native:diplomacy']['alliances'][0].update(chief=C),
                       lambda d:d['native:diplomacy'].update(next_command=0),
                       lambda d:d['native:army']['units'].update(engage_sequence=-1),
                       lambda d:d['native:army']['military'].update(events=[{'kind':'battle'}]),
                       lambda d:d['$engine'].update(polity={Q:'missing:policy'})):
            bad=deepcopy(data);mutate(bad);cases.append(bad)
        for bad in cases:
            other=w.runtime.world_class()()
            with self.assertRaises((ValueError,ModError)):w.runtime.load_mod_state(other,bad)

    def test_shared_map_is_sampled_on_dispatch_and_survives_transit_save(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1)
        self.call(w,Q,'share_map',target=C)
        sent_period=w.government.period();self.cycle(w)
        dispatched={t.position for t in w.runtime.object_call(w,'native:vision','tiles',Q)}
        self.site(w,'scenario:remote',Q,6,6)
        other=self.restore(w)
        self.cycle(w);self.cycle(other)
        message=w.intelligence.messages(C)[0]
        self.assertEqual(message.sampled,sent_period)
        self.assertEqual({tuple(t['position']) for t in message.payload['tiles']},dispatched)
        self.assertEqual(self.save(w),self.save(other))
        bad=self.save(other);bad['native:intelligence']['messages'][0]['kind']='unknown'
        with self.assertRaises(ValueError):other.runtime.load_mod_state(other.runtime.world_class()(),bad)

    def test_vote_result_is_delivered_to_members_and_invitee_without_outsiders(self):
        w=self.world();self.found(w)
        self.call(w,C,'bloc_join',bloc=B);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0]
        self.call(w,Q,'vote',vote=vote.id,accept=False);self.cycle(w)
        for actor in (Q,Z,C):
            self.assertTrue(any(c.message=='Vote rejected or invalidated' for c in w.diplomacy.snapshot(actor).results))
            self.assertTrue(any(e.text=='Vote rejected or invalidated' for e in w.intelligence.events(actor)))
        self.assertFalse(any(e.text=='Vote rejected or invalidated' for e in w.intelligence.events(Y)))

    def test_founder_invitation_is_revalidated_after_joining_another_bloc(self):
        w=self.world()
        self.call(w,Q,'bloc_found',bloc=B,name='甲盟',tos=[C]);self.cycle(w)
        offer=w.diplomacy.snapshot(C).offers[0]
        self.call(w,Q,'bloc_found',bloc='scenario:other',name='乙盟',tos=[Z]);self.cycle(w)
        other_offer=w.diplomacy.snapshot(Z).offers[0]
        self.call(w,Z,'accept_proposal',offer=other_offer.id);self.cycle(w)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,C,'accept_proposal',offer=offer.id)
        self.assertEqual(self.save(w),before)

    def test_mid_action_pending_vote_dispatch_combat_and_rng_roundtrip_continue_identically(self):
        w=self.world();self.site(w,'scenario:qc',Q,1,1);self.site(w,'scenario:cc',C,2,1)
        self.site(w,'scenario:zc',Z,6,6);self.found(w)
        w.military.seed(Q,'native:infantry',1,1,3);w.military.seed(C,'native:infantry',2,1)
        self.call(w,Q,'declare_war',target=C);self.cycle(w)
        vote=w.diplomacy.snapshot(Q).votes[0];self.call(w,Z,'vote',vote=vote.id,accept=True)
        self.call(w,Q,'send_letter',target=C,text='三思而后行')
        other=self.restore(w)
        self.cycle(w);self.cycle(other)
        self.assertEqual(self.save(w),self.save(other))
        ids=[u.id for u in w.military.snapshot(Q)]
        self.call(w,Q,'attack',ids=ids,x=2,y=1);self.call(other,Q,'attack',ids=ids,x=2,y=1)
        other=self.restore(w)
        for _ in range(4):
            self.cycle(w);self.cycle(other)
            self.assertEqual(self.save(w),self.save(other))


if __name__=='__main__':unittest.main()
