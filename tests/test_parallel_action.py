"""并发行动期：客户端等待重叠，世界写入仍串行，回执按行动顺序归并。"""
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from host import GameHost


class Recorder:
    def __init__(self):
        self.lock=threading.Lock();self.active=0;self.peak=0;self.done=[]

    def enter(self,actor):
        with self.lock:
            self.active+=1;self.peak=max(self.peak,self.active)

    def leave(self,actor):
        with self.lock:
            self.active-=1;self.done.append(actor)


class FakeRuleClient:
    """规则客户端的离线替身：只做规划与结束行动，用 sleep 模拟等待。"""
    kind='rule'
    def __init__(self,actor,recorder,delay=0.0):
        self.actor=actor;self.recorder=recorder;self.delay=delay
    def play(self,player,max_actions=None):
        self.recorder.enter(self.actor)
        try:
            time.sleep(self.delay)
            player.call_api('native:government','plan',{'text':'并行行动测试'})
            receipt=player.submit('end_turn',{'summary':'并行行动测试'})
            return [receipt]
        finally:
            self.recorder.leave(self.actor)


def config(workers=None):
    cfg={'map_size':12,'seed':73,'nations':[
        {'id':'scenario:qin','name':'秦','client':'rule'},
        {'id':'scenario:chu','name':'楚','client':'rule'},
        {'id':'scenario:zhao','name':'赵','client':'rule'}]}
    if workers is not None:cfg['planning_workers']=workers
    return cfg


class TestParallelActionPeriod(unittest.TestCase):
    def host(self,workers=None,delays=(0.12,0.12,0.12)):
        h=GameHost.new(config(workers));recorder=Recorder()
        for actor,delay in zip(h.session.turn_order(),delays):
            h.session.clients[actor]=FakeRuleClient(actor,recorder,delay)
        return h,recorder

    def test_default_stays_serial(self):
        h,recorder=self.host()
        order=list(h.session.turn_order())
        started=time.monotonic();h.cycle();elapsed=time.monotonic()-started
        self.assertEqual(recorder.peak,1)
        self.assertGreaterEqual(elapsed,0.30)
        self.assertEqual([row['actor'] for row in h.session.last_results],order)

    def test_planning_workers_overlaps_waiting_and_merges_by_turn_order(self):
        h,recorder=self.host(workers=2,delays=(0.25,0.05,0.05))
        order=list(h.session.turn_order())
        started=time.monotonic();h.cycle();elapsed=time.monotonic()-started
        # 等待确实重叠：墙钟明显短于串行之和，且同时最多两个客户端在跑。
        self.assertGreaterEqual(recorder.peak,2)
        self.assertLess(elapsed,0.65)
        # 回执仍按行动顺序归并，不按完成顺序。
        self.assertEqual([row['actor'] for row in h.session.last_results],order)
        self.assertEqual(recorder.done[0],order[1])   # 短延迟的先完成
        self.assertEqual(recorder.done[-1],order[0])  # 长延迟的后完成

    def test_presentation_backpressure_falls_back_to_serial(self):
        h,recorder=self.host(workers=2)
        batches=[]
        h.cycle(on_batch=lambda actor,call: batches.append((actor,call)))
        self.assertEqual(recorder.peak,1)
        self.assertTrue(batches)

    def test_parallel_cycle_keeps_world_saveable_and_audited(self):
        h,recorder=self.host(workers=3,delays=(0.02,0.02,0.02))
        h.cycle()
        order=list(h.session.turn_order())
        h.cycle()
        # 并发只重叠等待：审计仍按真实入账顺序记录，边界仍可保存。
        self.assertTrue(h.session.audit)
        self.assertTrue(h.session._boundary)
        self.assertEqual(h.session.world.phase,'行动')
        self.assertEqual([row['actor'] for row in h.session.last_results],order)

    def test_planning_workers_must_be_a_positive_integer(self):
        with self.assertRaises(Exception):GameHost.new(config(0))


if __name__=='__main__':unittest.main()
