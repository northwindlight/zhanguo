"""Offline desktop host: NDJSON on stdout, diagnostics on stderr.

Only this thread touches the session. The input thread queues commands, including
EOF as a save-and-stop request. No real configuration or live save is opened.
"""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
from queue import Queue, Empty
import sys
from threading import Thread
import time
import uuid

from scenarios.eight_nations import build_config, build
from host import GameHost, write_json

PROTOCOL = 'zhanguo.desktop/1'


class Controller:
    def __init__(self, host, save, output, *, delay=0.8, max_turns=300, playback=False):
        self.host, self.save, self.output = host, Path(save), output
        self.delay, self.max_turns = delay, max_turns
        self.commands = Queue()
        self.running = False
        self.stopping = False
        self.failed = False
        self.generation = uuid.uuid4().hex
        self.revision = 0
        self.saved_period = None
        self.playback=playback;self.waiting=None;self.timeline_sequence=0
        self._in_cycle=False;self._cycle_paused=False;self._stop_after_cycle=False
        self._capture=None
        self._probes=None
        self.contexts={}
        self._error_actor=None
        self._streamed_actors=set()
        self._stream_buffer=None
        self._stream_sent=0
        self.human_api=None;self.viewer=None;self._human_player=None;self._seat_token=None
        self._human_results={};self._human_receipts=[]
        self._last_player_frame=None
        humans=[a for a,c in host.clients.items() if c.kind=='human']
        if not humans and any(n.get('client')=='human' and n.get('enabled',True) for n in host.cfg.get('nations',[])):
            raise ValueError('真人交互宿主需要至少一位开局登场的真人')
        if humans:
            from mods.native.human import HumanAPI
            self.human_api=HumanAPI(host);self.viewer=humans[0]

    def flush_stream(self):
        if self._stream_buffer:
            self.send('stream',**self._stream_buffer)
            self._stream_buffer=None;self._stream_sent=time.monotonic()

    def model_stream(self, actor, event):
        """Actual provider deltas only; coalesce small packets without reordering."""
        if self.human_api is not None:return
        self._streamed_actors.add(actor)
        row={'actor':actor,**event}
        if event['event']=='delta':
            if self._stream_buffer and any(self._stream_buffer.get(k)!=row.get(k) for k in ('actor','call','period','kind')):
                self.flush_stream()
            if self._stream_buffer:self._stream_buffer['text']+=row['text']
            else:self._stream_buffer=row
            if time.monotonic()-self._stream_sent>=.04 or len(self._stream_buffer['text'])>=512:self.flush_stream()
        else:
            self.flush_stream();self.send('stream',**row)

    def activity(self,actor,text):
        """当前国家的实时活动（思考/写信/处理政务…），供地图徽标显示。"""
        if self.human_api is None or actor==self.viewer:self.send('activity',actor=actor,text=text)
    # The engine forwards client emit lines through cycle(emit=...); localized
    # templates are probed once with sentinel markers so parsing survives
    # template changes in either locale. First two fields of each shape are
    # the parse sentinels; later fields are filler so str.format succeeds.
    def _probe(self):
        if self._probes is None:
            probes={}
            text=getattr(self.host.ui,'text',None)
            shapes=(('llm.thinking','stream','thinking',{'actor':'\ue000','text':'\ue001'}),
                    ('llm.speech','stream','body',{'actor':'\ue000','text':'\ue001'}),
                    ('llm.context','stream','context',{'actor':'\ue000','plan':'\ue001'}),
                    ('llm.request','activity','request',{'actor':'\ue000','count':'\ue001'}),
                    ('llm.retry','activity','retry',{'actor':'\ue000','attempt':'\ue001','total':'T','error':'E','wait':'0'}),
                    ('llm.compacting','activity','compacting',{'actor':'\ue000','attempt':'\ue001','limit':'L'}))
            if callable(text):
                for key,mode,kind,fields in shapes:
                    try:
                        first,second=list(fields.values())[:2]
                        shaped=text(key,**fields)
                        prefix,rest=shaped.split(first,1)
                        middle,suffix=rest.split(second,1)
                        probes[key]=(mode,kind,prefix,middle,suffix)
                    except (ValueError,TypeError):pass
            self._probes=probes
        return self._probes

    def model_line(self,line):
        """模型回合的上下文/思考/正文流与等待/重试活动行，实时转发给桌面。"""
        if self.human_api is not None or not isinstance(line,str):return
        try:
            for mode,kind,prefix,middle,suffix in self._probe().values():
                if not line.startswith(prefix):continue
                rest=line[len(prefix):]
                if suffix and rest.endswith(suffix):rest=rest[:-len(suffix)]
                if middle not in rest:break
                actor,remainder=rest.split(middle,1)
                if mode=='stream':
                    if kind in ('body','thinking') and actor in self._streamed_actors:return
                    if kind=='context':self.contexts[actor]=remainder
                    self.send('stream',actor=actor,event='context' if kind=='context' else 'message',kind=kind,text=remainder)
                else:
                    token=remainder.strip().split('/')[0]
                    number=''.join(ch for ch in token if ch.isdigit())
                    label={'request':f'正在思考（第{number}次调用）' if number else '正在思考',
                           'retry':f'模型API异常，自动重试中（第{number}次）' if number else '模型API异常，自动重试中',
                           'compacting':'正在整理长期记忆'}[kind]
                    self.send('activity',actor=actor,text=label)
                break
        except Exception:pass

    def send(self, message_type, **data):
        self.revision += 1
        self.output({'protocol': PROTOCOL, 'generation': self.generation,
                     'revision': self.revision, 'type': message_type, **data})

    def status(self, state, **extra):
        self.send('status', state=state, period=self.host.clock(),
                  saved_period=self.saved_period, save=str(self.save),
                  cycle_active=self._in_cycle,playback_paused=self._cycle_paused,auto_running=self.running,**extra)

    def frame(self):
        if self.human_api is not None:
            try:
                frame=self.human_api.state(self.viewer)
                from mods.ui.desktop_player import frame as player_frame
                frame['visual']=player_frame(self.host.ui,frame)
                self._last_player_frame=frame
                return {'scope':'player',**frame}
            except ValueError:
                if self.viewer in self.host.actors():raise
                return {'scope':'player','actor':self.viewer,'period':self.host.clock(),
                        'eliminated':True,'last_known':self._last_player_frame}
        frame=self.host.ui.desktop_frame(self.host.observer_snapshot())
        frame['clients']=[{'actor':actor,'kind':client.kind,'version':client.version,
                           'model':getattr(client,'cfg',{}).get('model') if client.kind=='llm' else None,
                           'strategy':getattr(getattr(client,'state',None),'profile',None)} for actor,client in self.host.clients.items()]
        return frame

    def snapshot(self):self.send('snapshot',frame=self.frame())

    def persist(self):
        self.host.save(self.save)
        self.saved_period = self.host.clock()

    def command(self, row):
        op = row.get('command')
        if op in ('human_query','human_submit'):
            self.human_command(row);return
        if op in ('discard_progress','disconnect') and self.human_api is not None:
            if op=='discard_progress' and row.get('confirm') is not True:raise ValueError('需明确确认放弃未保存进度')
            self.running=False;self.stopping=True;self.waiting=None
            self.status('discarded')
            if self._in_cycle:
                from mods.ui.desktop_human import DiscardHumanCycle
                raise DiscardHumanCycle()
            return
        if op=='stop' and self._in_cycle and self.human_api is not None:
            self.running=False;self._cycle_paused=True
            self.status('exit-choice',message='本回合尚未完成；继续本回合，或明确放弃未保存进度退出')
            return
        if self._in_cycle and op in ('pause','resume','save','stop','step'):
            # There is no legal save in the middle of a cycle. Pause stays at
            # this batch; closing drains the cycle without further visual waits.
            if op=='step':raise ValueError('本回合尚未完成，请先继续演出')
            if op=='pause':
                self.running=False;self._cycle_paused=True;self.status('playback-paused')
            elif op=='resume':
                self.running=not row.get('cycle_only',False);self._cycle_paused=False;self.status('presenting')
            elif op=='save':self.status('save-queued')
            else:
                self.running=False;self._stop_after_cycle=True;self._cycle_paused=False
                self.waiting=None;self.status('stopping')
            return
        if op == 'pause':
            self.running = False
            self.persist()
            self.status('paused')
        elif op in ('resume', 'step'):
            if self.failed:
                raise ValueError('本回合执行失败，保留最近完整存档；请关闭后重新启动')
            if self.host.finished() or self.host.clock() >= self.max_turns:
                raise ValueError('对局已结束或达到回合上限')
            if self.human_api is not None and self.viewer not in self.host.actors():
                raise ValueError('玩家国家已亡国；本局真人操作已结束')
            if op=='step' and self.waiting is not None:raise ValueError('本期演出尚未完成，请先继续或快进')
            self.running = op == 'resume' and not row.get('cycle_only',False)
            if op == 'step':self.advance()
            else:self.status('running' if self.running else 'paused')
        elif op == 'save':
            self.persist();self.status('running' if self.running else 'paused')
        elif op == 'snapshot':self.snapshot()
        elif op == 'playback_ack':
            if self.waiting is not None and row.get('generation')==self.generation and row.get('sequence')==self.waiting:
                self.waiting=None
                if not self._in_cycle:self.status('running' if self.running else 'paused')
        elif op == 'stop':
            self.running = False
            if not self.failed:self.persist()
            self.stopping = True
            self.status('stopped')
        else:raise ValueError(f'未知桌面命令：{op}')

    def human_command(self,row):
        """Run on the sole session thread; input threads only enqueue JSON."""
        request_id=row.get('request_id')
        try:
            if self.human_api is None:raise ValueError('本局没有真人席位')
            actor=row.get('actor')
            if row['command']=='human_query':
                if actor!=self.viewer:raise ValueError('不能读取未接管席位的私有信息')
                query=row.get('query','state');args=row.get('arguments',{})
                if not isinstance(args,dict):raise ValueError('查询参数须为对象')
                if query=='state':data=self.human_api.state(actor)
                elif query=='tile':data=self.human_api.tile(actor,**args)
                elif query=='targets':data=self.human_api.targets(actor,**args)
                elif query=='panel':data=self.human_api.panel(actor,args)
                else:raise ValueError('未知真人查询')
                self.send('human_result',request_id=request_id,ok=True,data=data);return
            player=self._human_player
            if (player is None or actor!=player.actor or row.get('seat_token')!=self._seat_token or
                row.get('period')!=self.host.clock() or row.get('control_generation')!=player.generation):
                raise ValueError('真人席位、回合或控制权已改变；请求未执行')
            if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作需要唯一请求编号')
            identity=json.dumps({key:row.get(key) for key in ('operation','arguments')},sort_keys=True,ensure_ascii=False,allow_nan=False)
            if request_id in self._human_results:
                old,result=self._human_results[request_id]
                if identity!=old:raise ValueError('同一请求编号不能用于另一项操作')
                self.send('human_result',request_id=request_id,duplicate=True,**result);return
            if self.viewer!=actor:raise ValueError('不是本机玩家席位')
            if self._cycle_paused or self.waiting is not None:raise ValueError('请先继续并完成本批演出')
            if not player.action_state()['can_act']:raise ValueError('本国行动已经结束')
            receipt=self.human_api.submit(actor,row.get('operation'),row.get('arguments',{}))
            result={'ok':True,'data':receipt}
            self._human_results[request_id]=(identity,result)
            self._human_receipts.append(receipt)
            self.send('human_result',request_id=request_id,**result)
            self.batch(actor,len(self._human_receipts))
            if not self.playback:self.snapshot()
        except ValueError as exc:
            self.send('human_result',request_id=request_id,ok=False,code='request_rejected',message=str(exc))

    def human_turn(self,player):
        """Wait outside Session.lock; pause, reads, ack and close remain serviced."""
        self._human_player=player;self._seat_token=uuid.uuid4().hex
        self._human_results={};self._human_receipts=[]
        if self.viewer!=player.actor:raise ValueError('当前只支持单机一位真人')
        self.send('human_turn',actor=player.actor,period=self.host.clock(),
                  control_generation=player.generation,seat_token=self._seat_token,
                  nickname=self.host.clients[player.actor].cfg.get('nickname','玩家'))
        self.snapshot()
        self.status('human-waiting')
        try:
            while player.action_state()['can_act']:
                try:self.command(self.commands.get())
                except ValueError as exc:self.send('error',message=str(exc))
            return list(self._human_receipts)
        finally:
            self._human_player=None;self._seat_token=None;self._human_results={}

    def publish(self,beats,stage,*,actor=None,call=None,frame=None):
        if frame is None:frame=self.frame()
        self.flush_stream()
        self.timeline_sequence+=1;self.waiting=self.timeline_sequence
        self.send('timeline',sequence=self.waiting,period=self._capture.period,
                  stage=stage,actor=actor,call=call,beats=beats)
        # A detached frame at the same fact boundary as these beats, never the
        # final cycle frame while an earlier call is still being watched.
        self.send('snapshot',frame=frame)

    def batch(self,actor,call):
        capture=self._capture
        if not capture or self._stop_after_cycle:return
        if capture.warning:
            self.send('notice',message=capture.warning)
            self._capture=None
            try:self.snapshot()
            except Exception as exc:self.send('notice',message=(f'当前画面暂不可用，回合结束后同步：{exc}'
                if self.human_api is None else '本国画面暂不可用，回合结束后重试玩家快照'))
            return
        beats=capture.drain()
        if not beats:return
        try:frame=self.frame()
        except Exception as exc:
            capture.warning=(f'本批画面采集失败，回合结束后同步：{exc}' if self.human_api is None
                             else '本国画面采集失败，回合结束后重试玩家快照')
            self.send('notice',message=capture.warning);self._capture=None;return
        self.status('presenting')
        self.publish(beats,'action',actor=actor,call=call,frame=frame)
        while (self.waiting is not None or self._cycle_paused) and not self._stop_after_cycle:
            try:self.command(self.commands.get())
            except Exception as exc:
                from mods.ui.desktop_human import DiscardHumanCycle
                if isinstance(exc,DiscardHumanCycle):raise
                self.send('error',message=str(exc) if self.human_api is None else '演出处理异常，已暂停',
                          actor=actor if self.human_api is None else None)
        if not self._stop_after_cycle:self.status('human-waiting' if self._human_player is not None and self._human_player.action_state()['can_act'] else 'advancing')

    def advance(self):
        if self.waiting is not None:raise ValueError('等待本期演出确认后才能推进下一期')
        self.status('advancing')
        self._streamed_actors.clear()
        capture=None
        self._in_cycle=True;self._cycle_paused=False;self._stop_after_cycle=False
        try:
            if self.playback:
                from mods.ui.desktop_activity import TimelineCapture
                from mods.ui.desktop_human import PlayerCapture
                try:capture=PlayerCapture(self) if self.human_api is not None else TimelineCapture(self.host,contexts=self.contexts,activity=self.activity)
                except Exception as exc:self.send('notice',message=(f'演出初始化失败，使用完整快照：{exc}'
                    if self.human_api is None else '本国演出初始化失败，仅使用玩家快照'))
            self._capture=capture
            with redirect_stdout(sys.stderr):
                if capture:self.host.cycle(on_result=capture.capture,emit=self.model_line,on_stream=self.model_stream,on_batch=self.batch,on_human=self.human_turn)
                else:self.host.cycle(emit=self.model_line,on_stream=self.model_stream,on_human=self.human_turn)
        except Exception as exc:
            from mods.ui.desktop_human import DiscardHumanCycle
            if isinstance(exc,DiscardHumanCycle):return
            self.flush_stream()
            self.running = False; self.failed = True
            self._error_actor=getattr(capture,'actor',None) if capture else None
            raise
        finally:self._in_cycle=False
        self.flush_stream()
        self.persist()
        if self._stop_after_cycle:
            self.stopping=True;self.status('stopped');return
        if capture:
            beats=capture.finish()
            if capture.warning:self.send('notice',message=capture.warning)
            if beats:
                self._capture=capture
                self.publish(beats,'settlement')
            else:self.snapshot()
        else:self.snapshot()
        if (self.host.finished() or self.host.clock() >= self.max_turns or
            self.human_api is not None and self.viewer not in self.host.actors()):self.running = False
        self.status('running' if self.running else 'paused')

    def run(self):
        self.persist();self.snapshot();self.status('paused')
        deadline = time.monotonic()
        while not self.stopping:
            try:
                row = self.commands.get(timeout=max(0, deadline-time.monotonic()) if self.running and self.waiting is None else None)
                self.command(row)
            except Empty:
                try:self.advance()
                except Exception as exc:
                    self.send('error', message=str(exc) if self.human_api is None else '本回合处理失败，保留最近完整存档',
                              actor=self._error_actor if self.human_api is None else None)
                    self._error_actor=None
                deadline = time.monotonic()+self.delay
            except Exception as exc:
                self.running = False
                self.send('error', message=str(exc) if self.human_api is None else '当前请求处理失败，已暂停',
                          actor=self._error_actor if self.human_api is None else None)
                self._error_actor=None


def main(argv=None):
    p = argparse.ArgumentParser(description='战国 Godot 离线观察者宿主')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=20261023)
    p.add_argument('--delay', type=float, default=0.8)
    p.add_argument('--resume', type=Path)
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    resume = args.resume or (Path(os.environ['ZHANGUO_RESUME']) if os.environ.get('ZHANGUO_RESUME') else None)
    config_path = os.environ.get('ZHANGUO_DESKTOP_CONFIG')
    if config_path:
        # External game (may include LLM nations): the config must be the one
        # the session was created with; saves stay in this launch's own output.
        cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
        if os.environ.get('ZHANGUO_UI_MODS'):cfg['ui_mods'] = os.environ['ZHANGUO_UI_MODS']
        with redirect_stdout(sys.stderr):
            host = GameHost.load(resume, cfg) if resume else GameHost.new(cfg)
        max_turns = int(cfg.get('max_turns') or 300)
    elif resume:
        cfg = json.loads(resume.with_name('config.json').read_text(encoding='utf-8'))
        if not cfg.get('nations') or any(n.get('client') not in ('rule','human') for n in cfg['nations']):
            raise ValueError('此入口仅自动恢复离线配置；请通过 --config 提供对局配置')
        if os.environ.get('ZHANGUO_UI_MODS'):cfg['ui_mods'] = os.environ['ZHANGUO_UI_MODS']
        with redirect_stdout(sys.stderr):host = GameHost.load(resume, cfg)
        max_turns = 300
    else:
        cfg = build_config({'rule_ai': 'v13', 'nations': [{'client': 'rule', 'rule_ai': 'v13'}]}, args.seed)
        if os.environ.get('ZHANGUO_UI_MODS'):cfg['ui_mods'] = os.environ['ZHANGUO_UI_MODS']
        with redirect_stdout(sys.stderr):host = build(seed=args.seed, config=cfg)
        max_turns = 300
    write_json(args.output/'config.json', cfg)
    if not callable(getattr(host.ui, 'desktop_frame', None)):
        raise ValueError('当前 UI Mod 未提供 desktop_frame')
    protocol_out = sys.stdout
    def output(row):
        protocol_out.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        protocol_out.flush()
    control = Controller(host, args.output/'session.json', output, delay=max(0.05, args.delay),playback=True,max_turns=max_turns)
    def read():
        for line in sys.stdin:
            try:
                row = json.loads(line)
                if not isinstance(row, dict):raise ValueError('命令须为 JSON 对象')
            except (ValueError, TypeError):row = {'command': 'invalid'}
            control.commands.put(row)
        control.commands.put({'command': 'disconnect' if control.human_api is not None else 'stop'})
    Thread(target=read, daemon=True).start()
    control.run()


if __name__ == '__main__':main()
