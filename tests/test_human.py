"""Single local human: real domains, isolated saves, no model/network calls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from engine.session import SaveFormatError
from host import GameHost
from mods.native.human import HumanAPI
from mods.native.intelligence.knowledge import MapKnowledge
from ui.desktop import Controller

A='scenario:human'
B='scenario:other'


def config(mods='mods.toml'):
    return {'mods':mods,'map_size':12,'seed':73,'nations':[
        {'id':A,'name':'秦','client':'human','nickname':'本地玩家'},
        {'id':B,'name':'楚','client':'rule'}]}


class Transport:
    def __init__(self,host,directory,playback=False):
        self.rows=[];self.condition=threading.Condition()
        def output(row):
            with self.condition:self.rows.append(deepcopy(row));self.condition.notify_all()
        self.control=Controller(host,Path(directory)/'session.json',output,playback=playback,delay=.05)
        self.thread=threading.Thread(target=self.control.run,daemon=True)
        self.thread.start();self.wait(lambda r:r.get('state')=='paused')

    def wait(self,predicate,after=0):
        with self.condition:
            if not self.condition.wait_for(lambda:any(predicate(r) for r in self.rows[after:]),10):
                raise AssertionError('Host response timeout: '+str(self.rows[-3:]))
            return next(r for r in self.rows[after:] if predicate(r))

    def put(self,**row):self.control.commands.put(row)

    def turn(self):
        self.put(command='step')
        return self.wait(lambda r:r['type']=='human_turn')

    def submit(self,turn,request_id,operation,arguments=None,**extra):
        self.put(command='human_submit',actor=turn['actor'],period=turn['period'],
                 control_generation=turn['control_generation'],seat_token=turn['seat_token'],
                 request_id=request_id,operation=operation,arguments=arguments or {},**extra)
        return self.wait(lambda r:r['type']=='human_result' and r.get('request_id')==request_id)

    def close(self):
        self.put(command='disconnect');self.thread.join(10)
        if self.thread.is_alive():raise AssertionError('Host did not stop')


class TestHumanLifecycle(unittest.TestCase):
    def test_native_and_experimental_real_turns_save_load_without_plans(self):
        for mods in ('mods.toml','mods.experimental.toml'):
            with self.subTest(mods=mods),TemporaryDirectory() as d:
                h=GameHost.new(config(mods));api=HumanAPI(h)
                def play(player):
                    return [api.submit(A,'buy',{'resource':'food','quantity':2}),api.submit(A,'end_turn',{})]
                for _ in range(3):h.cycle(on_human=play)
                self.assertEqual(h.clock(),3)
                government=h.player(A).observe()['facts']['government']
                self.assertIsNone(government['plan']);self.assertEqual(government['summaries'],[])
                self.assertNotIn('council',h.services[A]);self.assertNotIn('scripts',h.services[A])
                self.assertIsNotNone(h.player(B).observe()['facts']['government']['plan'])
                path=Path(d)/'session.json';h.save(path)
                self.assertEqual(GameHost.load(path,config(mods)).to_save(),h.to_save())
                changed=deepcopy(config(mods));changed['nations'][0]['client']='rule'
                with self.assertRaises(SaveFormatError):GameHost.load(path,changed)

    def test_single_local_player_and_ai_government_requirements(self):
        cfg=config();cfg['nations'][1]['client']='human'
        with self.assertRaisesRegex(ValueError,'一位真人'):GameHost.new(cfg)
        h=GameHost.new(config())
        self.assertFalse(h.player(B).submit('end_turn',{})['ok'])
        with self.assertRaisesRegex(ValueError,'交互宿主'):h.clients[A].play(h.player(A))
        cfg=config();cfg['nations'][0]['appearance']='manual'
        with self.assertRaisesRegex(ValueError,'开局登场'):GameHost.new(cfg)

    def test_player_reads_are_detached_and_never_touch_observer(self):
        h=GameHost.new(config());api=HumanAPI(h);before=h.to_save()
        with patch.object(h,'observer_snapshot',side_effect=AssertionError('observer leak')), \
             patch.object(h,'observer_scene',side_effect=AssertionError('observer leak')):
            state=api.state(A)
            self.assertTrue(any(t['visibility']=='unknown' for t in state['map']['tiles']))
            unknown=next(t for t in state['map']['tiles'] if t['visibility']=='unknown')
            self.assertEqual(set(unknown),{'position','visibility'})
            self.assertNotIn('map_seed',state)
            api.tile(A,*unknown['position'])
            self.assertEqual(h.to_save(),before)
            h.player(A).submit('player_state',{})
            self.assertEqual(h.to_save()['modules'],before['modules'])
            state['facts']['econ']['stocks'].clear()
            self.assertTrue(api.state(A)['facts']['econ']['stocks'])

    def test_callback_cannot_skip_human_input_or_expose_foreign_action_events(self):
        h=GameHost.new(config());api=HumanAPI(h)
        h.world.intelligence.emit('action','FOREIGN-PRIVATE-RECEIPT',parties=(A,),
            details={'source':'action_receipt','actor':B})
        self.assertNotIn('FOREIGN-PRIVATE-RECEIPT',json.dumps(api.state(A)))
        self.assertNotIn('FOREIGN-PRIVATE-RECEIPT',json.dumps(api.panel(A,{'panel':'news'})))
        with self.assertRaisesRegex(ValueError,'尚未明确结束'):h.cycle(on_human=lambda p:[])
        self.assertEqual(h.clock(),0)

    def test_received_offer_is_warned_and_end_turn_really_rejects(self):
        h=GameHost.new(config());api=HumanAPI(h)
        h.world.diplomacy.submit(B,'propose_defense',target=A)
        h.world.resolve_turn();h.world.phase='行动'
        offers=api.state(A)['pending_end']['offers']
        self.assertEqual(len(offers),1)
        receipt=api.submit(A,'end_turn',{})
        self.assertTrue(receipt['ok']);self.assertEqual(receipt['data']['auto_rejected']['offers'],[offers[0]['id']])

    def test_request_identity_pause_save_and_no_automatic_human_end(self):
        with TemporaryDirectory() as d:
            h=GameHost.new(config());t=Transport(h,d)
            try:
                turn=t.turn();saved=t.control.save.read_bytes()
                # Waiting for input does not own Session.lock.
                self.assertTrue(h.lock.acquire(timeout=1));h.lock.release()
                before=len(h.audit)
                first=t.submit(turn,'trade','buy',{'resource':'food','quantity':1})
                self.assertTrue(first['data']['ok'])
                mark=len(t.rows)
                t.submit(turn,'trade','buy',{'resource':'food','quantity':1})
                duplicate=t.wait(lambda r:r.get('duplicate'),after=mark)
                self.assertEqual(duplicate['data'],first['data']);self.assertEqual(len(h.audit),before+1)
                bad=dict(turn);bad['period']+=1
                self.assertFalse(t.submit(bad,'old','buy',{'resource':'food','quantity':1})['ok'])
                t.put(command='pause');t.wait(lambda r:r.get('state')=='playback-paused')
                self.assertFalse(t.submit(turn,'paused','buy',{'resource':'food','quantity':1})['ok'])
                t.put(command='save');t.wait(lambda r:r.get('state')=='save-queued')
                self.assertEqual(t.control.save.read_bytes(),saved)
                t.put(command='stop');t.wait(lambda r:r.get('state')=='exit-choice')
                self.assertTrue(h.player(A).action_state()['can_act']);self.assertEqual(h.clock(),0)
                t.put(command='resume',cycle_only=True)
                t.submit(turn,'end','end_turn')
                t.wait(lambda r:r.get('state')=='paused' and r.get('period')==1)
                self.assertEqual(t.control.saved_period,1)
                self.assertFalse(t.submit(turn,'stale','buy',{'resource':'food','quantity':1})['ok'])
                self.assertEqual(GameHost.load(t.control.save,config()).to_save(),h.to_save())
            finally:t.close()

    def test_explicit_discard_preserves_last_boundary(self):
        with TemporaryDirectory() as d:
            h=GameHost.new(config());t=Transport(h,d)
            turn=t.turn();saved=t.control.save.read_bytes()
            t.submit(turn,'buy','buy',{'resource':'wood','quantity':5})
            t.put(command='discard_progress',confirm=True);t.thread.join(10)
            self.assertFalse(t.thread.is_alive());self.assertEqual(t.control.save.read_bytes(),saved)
            self.assertFalse(t.control.failed);self.assertEqual(h.clock(),0)

    def test_batch_ack_blocks_writes_but_reads_and_duplicate_are_serviced(self):
        with TemporaryDirectory() as d:
            h=GameHost.new(config());t=Transport(h,d,playback=True)
            try:
                turn=t.turn();t.submit(turn,'buy','buy',{'resource':'food','quantity':1})
                timeline=t.wait(lambda r:r['type']=='timeline')
                self.assertFalse(t.submit(turn,'premature','buy',{'resource':'food','quantity':1})['ok'])
                t.put(command='human_query',actor=A,request_id='query',query='state')
                self.assertTrue(t.wait(lambda r:r.get('request_id')=='query')['ok'])
                t.put(command='playback_ack',generation=t.control.generation,sequence=timeline['sequence'])
                self.assertIsNone(t.control._capture.warning)
            finally:t.close()

    def test_foreign_streams_receipts_and_accounts_never_leave_host(self):
        with TemporaryDirectory() as d:
            h=GameHost.new(config());rows=[];control=Controller(h,Path(d)/'s.json',rows.append,playback=True)
            control.model_stream(B,{'event':'delta','text':'PRIVATE-THOUGHT'})
            control.model_line(h.ui.text('llm.speech',actor=B,text='PRIVATE-SPEECH'))
            control.activity(B,'PRIVATE-ACTIVITY')
            self.assertEqual(rows,[])
            from mods.ui.desktop_human import PlayerCapture
            capture=PlayerCapture(control)
            capture.capture(B,'economy',{'action':'buy','quantity':999}, {'ok':True,'secret':'FOREIGN-RECEIPT'})
            self.assertEqual(capture.drain(),[])
            frame=control.frame()
            self.assertNotIn('FOREIGN-RECEIPT',json.dumps(frame))
            self.assertEqual(frame['facts']['econ']['owner'],A)
            with patch.object(control.human_api,'state',side_effect=ValueError('synthetic projection failure')):
                with self.assertRaises(ValueError):control.frame()
            capture.beats=[{'phase':'human-action','text':'本国合法操作'}]
            control._capture=capture
            with patch.object(control.human_api,'state',side_effect=ValueError('FOREIGN-PRIVATE-FAULT')):
                control.batch(A,1)
            self.assertNotIn('FOREIGN-PRIVATE-FAULT',json.dumps(rows))
            control.command({'command':'human_query','actor':B,'query':'state'})
            self.assertFalse(rows[-1]['ok'])


class TestHumanMapAndArmy(unittest.TestCase):
    def test_mixed_speed_union_subsets_and_original_selection(self):
        h=GameHost.new(config());api=HumanAPI(h)
        point=next(t.tile.position for t in h.world.territory.owned(A) if t.tile.terrain=='native:plain')
        h.world.military.seed(A,'native:infantry',*point)
        h.world.military.seed(A,'native:cavalry',*point)
        # Own troops on an unlit tile do not reveal the tile.
        obs=h.player(A).observe();obs['facts']['view']=[]
        from mods.native.army.candidates import candidates
        selected=[1,2];result=candidates(obs,selected,'move',12)
        actual=dict(h.world.military.reachable(A,2))
        far=next(t for t in result['targets'] if tuple(t['position']) in actual and
                 max(abs(t['position'][0]-point[0]),abs(t['position'][1]-point[1]))==2)
        self.assertEqual(far['ids'],[2]);self.assertEqual(selected,[1,2]);self.assertTrue(far['uncertain'])
        # Submission recomputes the subset and calls the existing batch once.
        with patch.object(api,'targets',return_value=result),patch.object(h.session,'submit',wraps=h.session.submit) as submit:
            receipt=api.submit(A,'move',{'ids':selected,'x':far['position'][0],'y':far['position'][1]})
            self.assertEqual(submit.call_args.args[2]['ids'],[2]);self.assertEqual(submit.call_count,1)
        self.assertTrue(receipt['ok']);self.assertEqual(h.world.units.by_id(1,A).moved_turn,-1)

    def test_hidden_obstacle_does_not_change_candidates_or_reveal_tile(self):
        h=GameHost.new(config());api=HumanAPI(h)
        # Find unlit inland floor, place an own unit there without adding vision.
        live={tuple(t['position']) for t in h.player(A).observe()['facts']['view']}
        point=next((x,y) for x in range(2,10) for y in range(2,10) if (x,y) not in live and
                   h.world.territory.terrain(x,y).passable and (x+1,y) not in live)
        h.world.military.seed(A,'native:cavalry',*point)
        before=api.targets(A,[1],'attack');target=(point[0]+1,point[1])
        cell=h.world.territory.cell(*target)
        h.world.territory._cells[target]=replace(cell,tile=replace(cell.tile,terrain='native:ocean'))
        self.assertEqual(api.targets(A,[1],'attack'),before)
        tile=api.tile(A,*point)
        self.assertEqual(tile['tile']['visibility'],'unknown');self.assertEqual(tile['armies'][0]['owner'],A)
        self.assertNotIn('terrain',tile['tile'])

    def test_used_engaged_newborn_and_in_place_attack(self):
        h=GameHost.new(config());api=HumanAPI(h)
        p=next((x,y) for x in range(2,10) for y in range(2,10) if h.world.territory.owner(x,y) is None and h.world.territory.terrain(x,y).passable)
        h.world.military.seed(A,'native:infantry',*p,3)
        h.world.units.by_id(1,A).update(moved_turn=0,engaged=True)
        h.world.units.by_id(2,A).update(born_turn=0)
        result=api.targets(A,[1,2,3],'attack')
        current=next(t for t in result['targets'] if t['position']==list(p))
        self.assertEqual(current['ids'],[1,3])
        for t in api.targets(A,[1,2,3],'move')['targets']:self.assertEqual(t['ids'],[3])
        with self.assertRaises(ValueError):api.targets(A,[1,1],'move')

    def test_map_purchase_is_dated_finite_and_survives_inbox_pruning(self):
        h=GameHost.new(config());api=HumanAPI(h);state=api.state(A)
        offer=state['map_offers'][0]
        receipt=api.submit(A,'buy_map',{'target':B});self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['data']['price'],offer['price'])
        self.assertFalse(api.submit(A,'buy_map',{'target':B})['ok'])
        knowledge=api.state(A)['map'];memories=[t for t in knowledge['tiles'] if t['visibility']=='memory']
        self.assertTrue(memories)
        p=next(t for t in memories if any(k.startswith('building/') and v['value']['count'] for k,v in t['fields'].items()))
        # Develop and change ownership in the background; history stays frozen.
        site=next(s for s in h.world.buildings.snapshot().sites if s.position==tuple(p['position']))
        h.world.buildings.seed(site.id,'native:castle')
        h.world.intelligence._messages=[]
        self.assertEqual(api.tile(A,*p['position'])['tile'],p)
        with TemporaryDirectory() as d:
            h.cycle(on_human=lambda p:[api.submit(A,'end_turn',{})])
            path=Path(d)/'s.json';h.save(path)
            loaded=GameHost.load(path,config())
            self.assertEqual(HumanAPI(loaded).tile(A,*p['position'])['tile'],p)

    def test_field_provenance_preserves_partial_buildings_and_orders(self):
        book=MapKnowledge()
        def merge(rows,period,source,complete=False):
            book.merge(A,rows,sampled=period,order=book.order(),source=source,acquired=period,complete=complete)
        row={'position':[2,3],'terrain':'native:plain','owner':B,
             'buildings':{'native:equipment-factory':2,'native:town-hall':1},'resources':{'native:wood':3}}
        merge([row],12,'map',True)
        merge([{'position':[2,3],'owner':B,'buildings':{'native:town-hall':1}}],15,'vision')
        merge([{**row,'owner':A,'buildings':{}}],11,'late old report',True)
        fields=book.snapshot(A)[0]['fields']
        self.assertEqual(fields['owner']['value'],B)
        self.assertEqual(fields['building/native:equipment-factory']['sampled'],12)
        merge([{'position':[2,3],'buildings':{}}],15,'newer same period full map',True)
        self.assertEqual(book.snapshot(A)[0]['fields']['building/native:equipment-factory']['value']['count'],0)
        bad=book.to_save();bad['next_order']=1
        with self.assertRaises(ValueError):MapKnowledge().restore(bad,lambda n:None,12)

    def test_only_legal_cells_light_up_and_lost_view_freezes(self):
        h=GameHost.new(config());api=HumanAPI(h)
        self.assertTrue(api.submit(A,'buy_map',{'target':B})['ok'])
        before=api.state(A)
        live_before={tuple(t['position']) for t in before['map']['tiles'] if t['visibility']=='live'}
        site=next(s for s in h.world.buildings.snapshot().sites if s.owner==B and
                  all(max(abs(s.position[0]-p[0]),abs(s.position[1]-p[1]))>3 for p in live_before))
        h.world.territory.transfer(site.id,A);h.world.intelligence.capture_maps()
        now=api.state(A)
        lit={tuple(t['position']) for t in now['map']['tiles'] if t['visibility']=='live'}
        self.assertEqual(lit,{t.position for t in h.world.runtime.object_call(h.world,'native:vision','tiles',A)})
        self.assertLess(len(lit-live_before),len(h.world.intelligence._map(B)))
        h.world.territory.transfer(site.id,B);h.world.intelligence.capture_maps()
        frozen=api.tile(A,*site.position)['tile']
        self.assertEqual(frozen['visibility'],'memory');self.assertEqual(frozen['fields']['owner']['value'],A)
        h.world.buildings.seed(site.id,'native:castle')
        self.assertEqual(api.tile(A,*site.position)['tile'],frozen)


if __name__=='__main__':unittest.main()
