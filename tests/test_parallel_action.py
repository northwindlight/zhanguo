"""并发行动期：客户端等待重叠，世界写入仍串行，回执按行动顺序归并。"""
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from host import GameHost


class Recorder:
    def __init__(self):
        self.lock=threading.Lock();self.active=0;self.peak=0;self.done=[];self.intervals={}

    def enter(self,actor):
        with self.lock:
            self.active+=1;self.peak=max(self.peak,self.active)
            self.intervals[actor]=[time.monotonic(),None]

    def leave(self,actor):
        with self.lock:
            self.active-=1;self.done.append(actor)
            if actor in self.intervals:self.intervals[actor][1]=time.monotonic()

    def overlap(self,left,right):
        a0,a1=self.intervals[left];b0,b1=self.intervals[right]
        return a0<b1 and b0<a1


class FakeHumanClient:
    """真人席位替身：先“思考”一会儿，再自己结束行动（不驱动 GUI 回调）。"""
    kind='human'
    def __init__(self,actor,recorder,delay=0.15):
        self.actor=actor;self.recorder=recorder;self.delay=delay
    def play(self,player,*,request=None):
        self.recorder.enter(self.actor)
        try:
            time.sleep(self.delay)
            return [player.submit('end_turn',{'summary':'真人并行测试'})]
        finally:self.recorder.leave(self.actor)


class FakeLlmClient:
    """LLM 客户端的离线替身：网络等待用 sleep 模拟；不产生流式回调。"""
    kind='llm'
    def __init__(self,actor,recorder,delay=0.0):
        self.actor=actor;self.recorder=recorder;self.delay=delay
    def play(self,player,emit=None,head=None,on_call=None,on_stream=None):
        self.recorder.enter(self.actor)
        try:
            time.sleep(self.delay)
            player.call_api('native:government','plan',{'text':'LLM 并行测试'})
            receipt=player.submit('end_turn',{'summary':'LLM 并行测试'})
            return [receipt]
        finally:self.recorder.leave(self.actor)


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

    def host_llm(self,workers=None,delays=(0.12,0.12,0.12)):
        h=GameHost.new(config(workers));recorder=Recorder()
        for actor,delay in zip(h.session.turn_order(),delays):
            h.session.clients[actor]=FakeLlmClient(actor,recorder,delay)
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

    def seed_units(self,h,positions):
        """在各国自有格种子一支军队，再把它挪到指定格，构造干扰/不干扰局面。"""
        w=h.world;sites={s.owner:s.position for s in w.buildings.snapshot().sites}
        units={}
        for actor,(x,y) in positions.items():
            seeded=w.military.seed(actor,'native:infantry',*sites[actor])[0]
            unit=w.units.by_id(seeded.id,actor)
            unit.update(x=x,y=y)
            units[actor]=unit
        return units

    def test_conflicting_pair_is_serialized_while_independent_actor_runs_parallel(self):
        h,recorder=self.host(workers=2,delays=(0.2,0.2,0.2))
        order=list(h.session.turn_order());a,b,c=order
        # a、b 的军队重合 → 可达范围必然相交；c 远在角落。
        self.seed_units(h,{a:(5,5),b:(5,5),c:(1,1)})
        h.cycle()
        self.assertFalse(recorder.overlap(a,b))
        self.assertTrue(recorder.overlap(c,a) or recorder.overlap(c,b))
        self.assertEqual([row['actor'] for row in h.session.last_results],order)

    def test_far_apart_actors_are_all_concurrent(self):
        h,recorder=self.host(workers=3,delays=(0.12,0.12,0.12))
        order=list(h.session.turn_order())
        self.seed_units(h,{order[0]:(1,1),order[1]:(8,8),order[2]:(9,1)})
        h.cycle()
        self.assertEqual(recorder.peak,3)

    def test_action_conflicts_uses_cached_envelopes(self):
        h,recorder=self.host(workers=2)
        order=list(h.session.turn_order())
        self.seed_units(h,{order[0]:(5,5),order[1]:(5,5),order[2]:(1,1)})
        military=h.world.military;calls=[]
        original=military.reachable
        def counted(*args,**kwargs):
            calls.append(args);return original(*args,**kwargs)
        military.reachable=counted
        pairs=h.session.action_conflicts(order);first=len(calls)
        h.session.action_conflicts(order)
        self.assertEqual(len(calls),first)
        self.assertIn((order[0],order[1]),pairs)

    def test_action_groups_respects_conflict_edges(self):
        from engine.session import Session
        groups=Session._action_groups(['a','b','c','d'],[('a','b'),('c','d')])
        self.assertEqual(groups,[['a','b'],['c','d']])

    def test_auto_trades_follow_rotation_order_not_completion_order(self):
        # 完成顺序被故意做成与轮换顺序相反；自动交易仍按轮换次序入账。
        h,recorder=self.host(workers=3,delays=(0.25,0.05,0.01))
        order=list(h.session.turn_order())
        for actor in order:
            receipt=h.player(actor).call_api('economy:queries','auto_trade',
                {'resource':'native:food','buy_below':2000,'buy_limit':10})
            self.assertTrue(receipt['ok'],receipt)
        h.cycle()
        rows=[row['actor'] for row in h.audit
              if (row.get('receipt') or {}).get('automatic')=='auto_trades']
        self.assertEqual(rows,order)
        self.assertNotEqual(recorder.done,order)

    def normalized(self,h):
        """去掉 id 与日志顺序后的世界关键状态：并发只允许这些结果保持不变。"""
        w=h.world;sites={}
        for inst in w.buildings.snapshot().instances:
            sites.setdefault(inst.site,[]).append((inst.kind,inst.level,inst.remaining))
        for site in sites:sites[site]=sorted(sites[site])
        return {'resources':{n.id:[(r.id,round(r.amount,6)) for r in w.resources.snapshot(n.id)]
                             for n in w.nations.snapshot()},
                'units':sorted((u.owner,u.kind,u.x,u.y,u.hp,u.engaged) for u in w.units if u.alive),
                'buildings':sites,
                'territory':sorted((tuple(t.tile.position),t.owner,t.tile.core) for t in w.territory.snapshot()),
                'market':(dict(w.market.snapshot().inventory),dict(w.market.snapshot().prices)),
                'period':w.government.period()}

    def play(self,workers,nations=3,size=24,cycles=2,setup=None):
        cfg={'map_size':size,'seed':73,'planning_workers':workers,
             'nations':[{'id':f'scenario:n{i}','name':f'N{i}','client':'rule'} for i in range(nations)]}
        h=GameHost.new(cfg)
        if setup is not None:setup(h)
        for _ in range(cycles):h.cycle()
        return h

    def test_serial_and_parallel_outcomes_match_without_conflicts(self):
        self.assertEqual(self.normalized(self.play(1)),self.normalized(self.play(2)))

    def test_serial_and_parallel_outcomes_match_for_conflicting_pair(self):
        def setup(h):
            w=h.world;order=list(h.session.turn_order());a,b=order[:2]
            sites={s.owner:s.position for s in w.buildings.snapshot().sites}
            ua=w.military.seed(a,'native:infantry',*sites[a])[0]
            ub=w.military.seed(b,'native:infantry',*sites[b])[0]
            w.units.by_id(ua.id,a).update(x=10,y=10)
            w.units.by_id(ub.id,b).update(x=10,y=11)
            h.player(a).submit('relations',{'action':'declare_war','target':b,'confirm':True})
        self.assertEqual(self.normalized(self.play(1,setup=setup)),
                         self.normalized(self.play(2,setup=setup)))

    def host_human(self,workers=None,human_delay=0.15,llm_delays=(0.30,0.30)):
        h=GameHost.new(config(workers));recorder=Recorder()
        order=list(h.session.turn_order())
        h.session.clients[order[0]]=FakeHumanClient(order[0],recorder,human_delay)
        for actor,delay in zip(order[1:],llm_delays):h.session.clients[actor]=FakeLlmClient(actor,recorder,delay)
        return h,recorder,order

    def test_human_acts_while_llm_seats_run_in_background(self):
        h,recorder,order=self.host_human()
        batches=[]
        started=time.monotonic();h.cycle(on_human=lambda player: [],on_batch=lambda actor,call: batches.append((actor,call)))
        elapsed=time.monotonic()-started
        # 真人在等 AI 思考的同一段时间里完成自己的回合：墙钟明显短于三次等待之和。
        self.assertGreaterEqual(recorder.peak,2)
        self.assertLess(elapsed,0.60)
        self.assertEqual([row['actor'] for row in h.session.last_results],order)
        # AI 侧不向主线程推送演出；只有真人自己的批次。
        self.assertEqual([actor for actor,_ in batches],[order[0]])
        self.assertTrue(h.session._boundary);self.assertEqual(h.session.world.phase,'行动')
        self.assertTrue(h.session.audit)

    def test_explicit_one_keeps_human_turn_serial(self):
        h,recorder,order=self.host_human(workers=1)
        started=time.monotonic();h.cycle(on_human=lambda player: [])
        elapsed=time.monotonic()-started
        self.assertEqual(recorder.peak,1)
        self.assertGreaterEqual(elapsed,0.60)
        self.assertEqual([row['actor'] for row in h.session.last_results],order)

    def test_human_with_rule_seats_stays_serial(self):
        h,recorder=self.host()
        order=list(h.session.turn_order())
        h.session.clients[order[0]]=FakeHumanClient(order[0],recorder,0.0)
        h.cycle(on_human=lambda player: [])
        self.assertEqual(recorder.peak,1)

    def test_auto_without_human_keeps_llm_games_serial(self):
        # 纯 AI 局默认串行：同一存档续跑必须得到相同结果；并发只做显式 opt-in。
        h,recorder=self.host_llm(delays=(0.20,0.20,0.20))
        order=list(h.session.turn_order())
        started=time.monotonic();h.cycle();elapsed=time.monotonic()-started
        self.assertEqual(recorder.peak,1)
        self.assertGreaterEqual(elapsed,0.60)
        self.assertEqual([row['actor'] for row in h.session.last_results],order)

    def test_explicit_one_disables_auto_parallel_for_llm(self):
        h,recorder=self.host_llm(workers=1)
        started=time.monotonic();h.cycle();elapsed=time.monotonic()-started
        self.assertEqual(recorder.peak,1)
        self.assertGreaterEqual(elapsed,0.30)

    def test_auto_means_human_concurrency_only_and_negative_is_rejected(self):
        # 0/缺省：纯 AI 局串行；真人 + LLM 局在真人行动期间并发。
        h,_=self.host(workers=0);h.cycle()
        self.assertEqual(h.session.planning_workers,0)
        self.assertFalse(h.session._parallel_action(None,None))
        llm,_=self.host_llm(workers=0);self.assertFalse(llm.session._parallel_action(None,None))
        human,_,_=self.host_human(workers=0);self.assertFalse(human.session._human_parallel(None,None))
        self.assertTrue(human.session._human_parallel(lambda player:[],None))
        with self.assertRaises(Exception):GameHost.new(config(-1))


if __name__=='__main__':unittest.main()
