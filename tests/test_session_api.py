"""Distribution-owned API exposure with a game unrelated to native domains."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.runtime import Runtime, Unit, GameMod, Tool, Stage, ModError
from engine.session import Session
from host import GameHost, SaveFormatError
from tests import test_parliament as parliament


class Counter:
    def __init__(self):self.rows={};self.ended=set();self.period=0
    def can_act(self,actor):return actor in self.rows and actor not in self.ended
    def clock(self):return self.period
    def increment(self,actor,amount):
        self.rows[actor]+=amount
        return {'count':self.rows[actor]}
    def finish(self,actor):self.ended.add(actor);return {'ended':True}
    def advance(self):self.period+=1;self.ended.clear()


class CounterMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('counter',)
    def provides(self):return ('example:counter',)
    def initialize_world(self,world):world.counter=Counter()
    def objects(self,world):return {'example:counter':world.counter}
    def tools(self):
        return (Tool('increment',lambda w,a,amount=1:w.counter.increment(a,amount),aliases=('加',)),
                Tool('finish',lambda w,a:w.counter.finish(a)),
                Tool('count',lambda w,a:w.counter.rows[a],read_only=True))
    def stages(self):return (Stage('example:advance',lambda w,c:w.counter.advance()),)
    def save_state(self,world):
        return {'rows':dict(world.counter.rows),'ended':sorted(world.counter.ended),'period':world.counter.period}
    def load_state(self,world,data):
        world.counter.rows=dict(data['rows']);world.counter.ended=set(data['ended']);world.counter.period=data['period']


class CallerMod(GameMod):
    def mixins(self):return ()
    def tools(self):return (Tool('batch',self.batch),)
    def batch(self,world,actor):
        # No domain Interfaces/Needs: the bound API owns request permissions.
        self.player.observe()
        return {'inner':[self.player.submit('increment'),self.player.submit('finish')]}


class CounterClient:
    kind='counter';version='1'
    def __init__(self,cfg,*,record=None,services=None):pass
    def play(self,player,**kwargs):return [player.submit('increment'),player.submit('finish')]
    def to_save(self):return {'kind':self.kind,'version':self.version,'state':{}}


class CounterSession(Session):
    def entries(self):return self.cfg['seats']
    def configure(self):pass
    def _positions(self,count):return [None]*count
    def spawn(self,item,position):self.world.counter.rows[item['id']]=0;return item['id']
    def actors(self):return tuple(self.world.counter.rows)
    def identities(self):return self.actors()
    def clock(self):return self.world.counter.period
    def scheduled(self):pass
    def validate_boundary(self):
        if self.world.counter.ended:raise SaveFormatError('Unsettled counter cycle')


class CountingService:
    def __init__(self,cfg,*,record=None,services=None):
        self.calls=record['state']['calls'] if record else 0
        self.recorded=[];self.handler=None
    def submit(self,player,action,arguments,next_submit):
        self.calls+=1
        if self.handler:return self.handler(player,action,arguments,next_submit)
        return next_submit()
    def record(self,player,row):self.recorded.append(row)
    def to_save(self):return {'version':'1','state':{'calls':self.calls}}


class TestDistributionAPI(unittest.TestCase):
    def host(self):
        cfg={'seats':[{'id':'counter:left','name':'left','client':'counter'},
                      {'id':'counter:right','name':'right','client':'counter'}]}
        distribution={'Id':'example:counter','Version':'1','ActorCollection':'counter_actors',
                      'PlayerClock':{'Capability':'example:counter','Method':'clock'},
                      'ActionGate':{'Capability':'example:counter','Method':'can_act'},'PlayerState':['count'],
                      'Session':{'Entry':'tests.test_session_api:CounterSession','Services':{
                          'trace':{'Entry':'tests.test_session_api:CountingService'}}},
                      'ClientFactories':{'counter':{'Entry':'tests.test_session_api:CounterClient'}}}
        # The actor collection is an arbitrary declared property, not nations.
        class Scope:
            @property
            def counter_actors(self):return tuple(self.counter.rows)
        class WithScope(CounterMod):
            def mixins(self):return (Scope,)
        runtime=Runtime([Unit('example:counter',WithScope(),version='1'),
                         Unit('example:caller',CallerMod(),version='1')],distribution)
        return GameHost.new(cfg,runtime=runtime),cfg,runtime

    def test_arbitrary_game_initialization_client_type_and_roundtrip(self):
        host,cfg,runtime=self.host()
        self.assertFalse(hasattr(host.world,'nations'))
        self.assertEqual(host.player('counter:left').observe()['facts'],{'count':0})
        host.cycle();host.cycle()
        self.assertEqual(host.clock(),2)
        self.assertEqual(len(host.audit),8)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'counter.json';host.save(path)
            loaded=GameHost.load(path,cfg,runtime=runtime)
            self.assertEqual(loaded.to_save(),host.to_save())
            host.cycle();loaded.cycle()
            self.assertEqual(loaded.to_save(),host.to_save())

    def test_every_submission_and_alias_passes_service_once_and_is_audited(self):
        host,_,_=self.host();player=host.player('counter:left');service=host.services[player.actor]['trace']
        seen=[]
        def handle(p,action,args,next_submit):seen.append(action);return next_submit()
        service.handler=handle
        self.assertTrue(player.submit('加',{'amount':3})['ok'])
        self.assertFalse(player.submit('increment',{'actor':'counter:right'})['ok'])
        self.assertFalse(player.submit('missing')['ok'])
        self.assertEqual(seen,['increment','increment','missing'])
        self.assertEqual(player.query('count'),3)
        self.assertEqual(host.player('counter:right').query('count'),0)
        self.assertEqual(len(host.audit),3)
        self.assertEqual(service.recorded,host.audit)
        self.assertEqual(player.submit('player_state')['data'],player.observe())

    def test_continuation_cannot_execute_action_twice_and_fault_keeps_audit(self):
        host,_,_=self.host();player=host.player('counter:left')
        def twice(p,action,args,next_submit):next_submit();return next_submit()
        host.services[player.actor]['trace'].handler=twice
        with self.assertRaisesRegex(RuntimeError,'only once'):player.submit('increment')
        self.assertEqual(player.query('count'),1)
        self.assertEqual(len(host.audit),1)
        self.assertTrue(host.audit[0]['receipt']['ok'])
        self.assertEqual(host.audit[0]['error']['type'],'RuntimeError')
        with self.assertRaises(SaveFormatError):host.to_save()

    def test_control_change_invalidates_existing_handles_and_context_is_detached(self):
        host,_,_=self.host();old=host.player('counter:left')
        host.invalidate(old.actor,{'prompt':'new controller','reset_memory':True})
        self.assertFalse(old.observe()['can_act'])
        self.assertFalse(old.query('player_state')['action_state']['active'])
        self.assertFalse(old.submit('increment')['ok'])
        self.assertFalse(old.submit('finish')['ok'])
        fresh=host.player(old.actor)
        state=fresh.action_state();state['context']['prompt']='tampered'
        self.assertEqual(fresh.action_state()['context']['prompt'],'new controller')
        self.assertTrue(fresh.submit('increment')['ok'])
        self.assertEqual(fresh.query('count'),1)
        self.assertEqual([row['generation'] for row in host.audit],[0,0,1])
        self.assertTrue(host.player('counter:right').action_state()['active'])

    def test_nested_module_api_calls_have_independent_permissions_and_callbacks(self):
        host,_,runtime=self.host();runtime.units['example:caller'].mod.player=host.player('counter:left')
        class BatchClient(CounterClient):
            def play(self,player,**kwargs):return [player.submit('batch')]
        host.clients['counter:left']=BatchClient({})
        callbacks=[]
        receipts=host.cycle(on_result=lambda actor,action,args,receipt:callbacks.append((actor,action)))
        self.assertEqual(callbacks,[('counter:left','increment'),('counter:left','finish'),
                                    ('counter:left','batch'),('counter:right','increment'),('counter:right','finish')])
        self.assertEqual([row['action'] for row in host.audit],['increment','finish','batch','increment','finish'])
        self.assertTrue(all(receipt['ok'] for receipt in receipts))
        self.assertEqual(host.player('counter:left').query('count'),1)

    def test_terminal_rejection_and_record_failure_keep_single_audit_row(self):
        host,_,_=self.host();player=host.player('counter:left');service=host.services[player.actor]['trace']
        def reject(p,action,args,next_submit):
            return {'api':1,'actor':p.actor,'action':action,'period':host.clock(),'ok':False,
                    'status':'rejected','code':'service_rejected','message':'blocked','data':None}
        service.handler=reject
        self.assertFalse(player.submit('increment')['ok'])
        self.assertEqual(player.query('count'),0)
        service.handler=None
        def fail(p,row):raise RuntimeError('record failure')
        service.record=fail
        with self.assertRaisesRegex(RuntimeError,'record failure'):player.submit('increment')
        self.assertEqual(player.query('count'),1)
        self.assertEqual(len(host.audit),2)
        self.assertEqual(host.audit[-1]['error']['message'],'record failure')

    def test_missing_distribution_entry_or_service_state_is_rejected(self):
        host,cfg,runtime=self.host();saved=host.to_save()
        saved['host']['services']['counter:left']={}
        with self.assertRaises(SaveFormatError):host.restore(saved)
        runtime.distribution.pop('Session')
        with self.assertRaisesRegex(ModError,'Session.Entry'):GameHost.new(cfg,runtime=runtime)


class TestDirectCouncilAPI(unittest.TestCase):
    def host(self,decisions,kind='llm',*,revision=True):
        fixture=parliament.TestParliament();cfg=fixture.cfg();cfg['nations'][0]['client']=kind
        host=GameHost.new(cfg);service=host.services[fixture.actor]['council']
        service._review_backend=parliament.CouncilBackend(decisions)
        if revision:parliament.start_with_plan(host,fixture.actor)
        return host,host.player(fixture.actor),service

    def test_revision_after_queries_and_build_keeps_its_own_receipt(self):
        for approved in (True,False):
            with self.subTest(approved=approved):
                host,player,service=self.host([approved])
                query=player.submit('market')
                self.assertTrue(query['ok'])
                self.assertTrue(player.submit('build',{'site':player.actor+'/land-6-6',
                                                      'kind':'native:farm'})['ok'])
                receipt=player.submit('plan',{'text':'农场仍在施工，按实际库存修订国策'})
                self.assertEqual((receipt['actor'],receipt['action'],receipt['period']),
                                 (player.actor,'plan',1))
                self.assertEqual(receipt['ok'],approved)
                self.assertEqual(receipt['status'],'effective' if approved else 'rejected')
                self.assertEqual(receipt['code'],None if approved else 'plan_not_approved')
                self.assertEqual(host.audit[-1]['receipt'],receipt)
                self.assertNotIn('error',host.audit[-1])
                self.assertEqual(next(row['receipt'] for row in host.audit if row['action']=='market'),query)
                self.assertEqual(len(service._review_backend.requests),2)
                if not approved:
                    self.assertTrue(player.submit('explain_plan',{'text':'按要求纠正事实，下期重提'})['ok'])
                self.assertTrue(player.submit('end_turn',{'summary':'完成查询、动工和国策修订处理'})['ok'])
                host.cycle()
                self.assertEqual(host.clock(),2)

    def test_revision_after_query_keeps_receipt_on_automatic_approval(self):
        host,player,service=self.host([])
        self.assertTrue(player.submit('market')['ok'])
        with patch.object(service,'_council_turn',return_value=('核对旧国策',True)):
            receipt=player.submit('plan',{'text':'按实际库存修订国策'})
        self.assertEqual((receipt['actor'],receipt['action'],receipt['period']),
                         (player.actor,'plan',1))
        self.assertTrue(receipt['ok'])
        self.assertIn('议会瘫痪',receipt['message'])
        self.assertEqual(host.audit[-1]['receipt'],receipt)
        self.assertEqual(player.observe()['facts']['government']['plan']['text'],'按实际库存修订国策')

    def test_direct_submission_reviews_for_both_llm_and_rule_clients(self):
        for kind in ('llm','rule'):
            with self.subTest(kind=kind):
                host,player,service=self.host([True],kind)
                receipt=player.submit('plan',{'text':'以实际产出发展经济'})
                self.assertTrue(receipt['ok'])
                self.assertEqual(len(service._review_backend.requests),2)
                self.assertEqual(player.observe()['facts']['government']['plan']['text'],'以实际产出发展经济')
                self.assertEqual(host.audit[-1]['receipt'],receipt)

    def test_rule_client_obeys_same_review_and_control_changes(self):
        host,player,service=self.host([False]*3,'rule')
        for _ in range(3):host.cycle()
        self.assertEqual(host.clock(),4)
        self.assertEqual(host.generations[player.actor],1)
        self.assertEqual(host.clients[player.actor].state.generation,1)
        self.assertFalse(player.action_state()['active'])
        self.assertEqual(len(service._review_backend.requests),7) # Three reviews and briefing.

    def test_unbound_domain_transport_cannot_silently_approve_plan(self):
        from engine.runtime import Runtime
        fixture=parliament.TestParliament();runtime=Runtime.load('mods.experimental.toml')
        from mods.native.session import NativeSession
        session=NativeSession(runtime,fixture.cfg(),lambda *args:None)
        session.configure()
        item=session.entries()[0]
        session.spawn(item,(6,6))
        receipt=runtime.player(session.world,item['id']).submit('plan',{'text':'不得跳过发行版议会服务'})
        self.assertFalse(receipt['ok'])
        self.assertIsNone(runtime.player(session.world,item['id']).observe()['facts']['government']['plan'])

    def test_third_rejection_invalidates_script_handle_before_next_inner_action(self):
        host,player,service=self.host([False]*3)
        for period in range(3):
            self.assertFalse(player.submit('plan',{'text':'需重新核对的国策'})['ok'])
            if period<2:
                self.assertTrue(player.submit('explain_plan',{'text':'下一期将按实际回执修订'})['ok'])
                self.assertTrue(player.submit('end_turn',{'summary':'解释完成并等待下期修订'})['ok'])
                host.cycle() # Already ended: no monarch backend call.
        before=player.query('econ')
        self.assertFalse(player.action_state()['active'])
        self.assertFalse(player.submit('buy',{'resource':'native:wood','quantity':1})['ok'])
        self.assertFalse(player.submit('end_turn',{'summary':'旧君主后续指令应失效'})['ok'])
        self.assertEqual(player.query('econ'),before)
        fresh=host.player(player.actor)
        self.assertEqual(fresh.action_state()['current_generation'],1)
        self.assertIn('罢免',fresh.action_state()['context']['prompt'])
        self.assertTrue(fresh.submit('end_turn',{'summary':'接任君主完成本回合经营'})['ok'])
        host.cycle()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'council.json';host.save(path)
            loaded=GameHost.load(path,parliament.TestParliament().cfg())
            self.assertEqual(loaded.to_save(),host.to_save())
