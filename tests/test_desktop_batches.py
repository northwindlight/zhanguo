"""Actual model batches with an offline transport; no model API or live save."""
import json
from pathlib import Path
from queue import Queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from host import GameHost
from ui.desktop import Controller


def tool(name, **arguments):
    return {'id':name+str(arguments),'type':'function',
            'function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False)}}


class Backend:
    def __init__(self, batches):
        self.batches=batches;self.calls=0;self.next_call=threading.Event()
    def chat_turn(self,messages,tools,cfg,on_retry=None,on_delta=None):
        calls=self.batches[self.calls];self.calls+=1
        if self.calls>1:self.next_call.set()
        return {'content':f'离线响应{self.calls}','tool_calls':calls},{'out_tokens':1,'wall':.1}


class TestCallPlayback(unittest.TestCase):
    def host(self,batches,**extra):
        h=GameHost.new({'mods':'mods.toml','map_size':12,'seed':3,
                       'nations':[{'id':'scenario:qin','name':'秦','client':'llm','model':'offline'}],**extra})
        backend=Backend(batches);h.clients['scenario:qin']._backend=backend
        return h,backend

    def start(self,h,directory):
        rows=[];queue=Queue()
        def output(row):rows.append(row);queue.put(row)
        c=Controller(h,Path(directory)/'session.json',output,playback=True,delay=.05)
        worker=threading.Thread(target=c.run,daemon=True);worker.start()
        def receive(predicate):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                row=queue.get(timeout=max(.01,deadline-time.monotonic()))
                if row['type']=='error':raise AssertionError(row['message'])
                if predicate(row):return row
            raise AssertionError('desktop event timeout')
        self.addCleanup(lambda:(c.commands.put({'command':'stop'}),worker.join(20)))
        receive(lambda r:r['type']=='status' and r['state']=='paused')
        c.commands.put({'command':'step'})
        return c,rows,worker,receive

    def ack(self,c,row,**extra):
        c.commands.put({'command':'playback_ack','generation':row['generation'],'sequence':row['sequence'],**extra})

    def test_whole_call_then_playback_then_next_call_and_settlement(self):
        h,backend=self.host([
            [tool('plan',text='本期补充木头并核实库存，下期再检查产能。'),
             tool('economy',action='buy',resource='wood',quantity=1),
             tool('economy',action='buy',resource='wood',quantity=2)],
            [tool('end_turn',summary='两笔采购已经完成。')]])
        with tempfile.TemporaryDirectory() as d:
            c,rows,worker,receive=self.start(h,d)
            first=receive(lambda r:r['type']=='timeline')
            self.assertEqual(first['stage'],'action');self.assertEqual(first['call'],1)
            self.assertEqual([b['phase'] for b in first['beats']],['actor','plan','trade','trade'])
            self.assertTrue(all(a['receipt']['ok'] for a in h.audit))
            self.assertEqual(len(h.audit),3);self.assertEqual(backend.calls,1)
            self.assertEqual(h.clock(),0);self.assertFalse(h.session._boundary)
            saved=c.save.read_bytes()
            self.ack(c,first,generation='old')
            c.commands.put({'command':'pause'});c.commands.put({'command':'save'})
            receive(lambda r:r['type']=='status' and r['state']=='save-queued')
            self.ack(c,first)
            # A valid acknowledgement cannot defeat an explicit pause.
            self.assertFalse(backend.next_call.wait(.08))
            self.assertEqual(backend.calls,1);self.assertEqual(c.save.read_bytes(),saved)
            c.commands.put({'command':'resume','cycle_only':True})
            second=receive(lambda r:r['type']=='timeline')
            self.assertEqual(second['stage'],'action');self.assertEqual(second['call'],2)
            self.assertEqual(backend.calls,2);self.assertEqual(h.clock(),0)
            self.assertTrue(any(b['phase']=='turn-end' for b in second['beats']))
            self.ack(c,second)
            final=receive(lambda r:r['type']=='timeline')
            self.assertEqual(final['stage'],'settlement');self.assertEqual(h.clock(),1)
            self.assertEqual(c.saved_period,1)
            self.assertFalse(any(b['phase']=='march' for b in final['beats']))
            self.ack(c,final)
            receive(lambda r:r['type']=='status' and r['state']=='paused')
            self.assertEqual(backend.calls,2)  # Resuming a stepped cycle stays single-step.
            self.assertEqual(GameHost.load(c.save,h.cfg).to_save(),h.to_save())
            self.assertEqual([r['revision'] for r in rows],list(range(1,len(rows)+1)))
            c.commands.put({'command':'stop'});worker.join(20);self.assertFalse(worker.is_alive())

    def test_llm_military_umbrella_moves_inside_its_call_before_end_turn(self):
        h,backend=self.host([]);actor='scenario:qin'
        land=h.world.territory.owned(actor);origin=land[0].tile.position
        unit=h.world.military.seed(actor,'native:cavalry',*origin)[0]
        target=next(t.tile.position for t in land[1:]
                    if max(abs(t.tile.position[0]-origin[0]),abs(t.tile.position[1]-origin[1]))<=1)
        backend.batches=[
            [tool('plan',text='本期在本土调兵，下期检查补给。'),
             tool('military',action='move',id=unit.id,x=target[0],y=target[1])],
            [tool('end_turn',summary='完成调兵。')]]
        with tempfile.TemporaryDirectory() as d:
            c,rows,worker,receive=self.start(h,d)
            first=receive(lambda r:r['type']=='timeline')
            self.assertEqual([b['phase'] for b in first['beats']],['actor','plan','order','march','arrival'])
            self.assertEqual(h.clock(),0);self.assertEqual(backend.calls,1)
            moved=h.world.units.by_id(unit.id,actor)
            self.assertEqual((moved.x,moved.y),target)
            march=next(b for b in first['beats'] if b['phase']=='march')
            self.assertEqual(march['client_kind'],'llm');self.assertEqual(march['playback_scope'],'actor')
            self.assertEqual(march['motions'][0]['points'][0],list(origin))
            self.assertEqual(march['motions'][0]['points'][-1],list(target))
            # Closing while this batch is unacknowledged must finish/save/exit,
            # even though no later animation acknowledgement will arrive.
            c.commands.put({'command':'stop'})
            worker.join(20);self.assertFalse(worker.is_alive())
            self.assertEqual(backend.calls,2);self.assertEqual(h.clock(),1)
            self.assertEqual(c.saved_period,1)
            self.assertEqual(sum(r['type']=='timeline' for r in rows),1)

    def test_automatic_end_turn_is_played_before_settlement(self):
        h,backend=self.host([[tool('plan',text='本期核实库存，下期继续。')]],max_steps=1)
        rows=[]
        with tempfile.TemporaryDirectory() as d:
            def output(row):
                rows.append(row)
                if row['type']=='timeline' and row['stage']=='action':self.ack(c,row)
            c=Controller(h,Path(d)/'session.json',output,playback=True);c.advance()
        timelines=[r for r in rows if r['type']=='timeline']
        self.assertEqual([r['stage'] for r in timelines],['action','action','settlement'])
        self.assertEqual(timelines[1]['beats'][-1]['phase'],'turn-end')
        self.assertEqual(h.clock(),1)

    def test_failed_projection_isolated_from_rule_cycle_and_legal_save(self):
        h,backend=self.host([[tool('plan',text='本期休整，下期复查。'),tool('end_turn',summary='完成休整。')]])
        rows=[]
        with tempfile.TemporaryDirectory() as d:
            c=Controller(h,Path(d)/'session.json',rows.append,playback=True)
            frame=c.frame;attempts=[]
            def fail_once():
                attempts.append(1)
                if len(attempts)==1:raise RuntimeError('synthetic projection failure')
                return frame()
            with patch.object(c,'frame',side_effect=fail_once):c.advance()
            self.assertEqual(h.clock(),1);self.assertFalse(c.failed)
            self.assertTrue(any(r['type']=='notice' for r in rows))
            self.assertFalse(any(r['type']=='timeline' for r in rows))
            self.assertEqual(GameHost.load(c.save,h.cfg).to_save(),h.to_save())

    def test_script_inner_actions_stay_in_the_response_batch_even_on_failure(self):
        source='api.call("economy:queries","buy",resource="wood",quantity=1)\n1 / 0'
        h,backend=self.host([
            [tool('plan',text='本期购入木头并检查脚本，下期核实。'),
             tool('script',action='write',source=source),tool('script',action='run'),
             tool('economy',action='buy',resource='wood',quantity=2)],
            [tool('end_turn',summary='脚本失败后完成补购。')]],mods='mods.experimental.toml')
        rows=[]
        with tempfile.TemporaryDirectory() as d:
            def output(row):
                rows.append(row)
                if row['type']=='timeline' and row['stage']=='action':self.ack(c,row)
            c=Controller(h,Path(d)/'session.json',output,playback=True);c.advance()
        timelines=[r for r in rows if r['type']=='timeline']
        self.assertEqual([r['stage'] for r in timelines],['action','action','settlement'])
        phases=[b['phase'] for b in timelines[0]['beats']]
        self.assertEqual(phases.count('trade'),2)
        self.assertIn('rejected',phases)
        self.assertLess(phases.index('trade'),phases.index('rejected'))
        self.assertEqual(phases[-1],'trade');self.assertEqual(h.clock(),1)
        self.assertEqual(backend.calls,2);self.assertFalse(h.session.faults)


if __name__=='__main__':unittest.main()
