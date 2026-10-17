"""Session host CLI: clients act through PlayerSession, host settles and saves."""
from __future__ import annotations
import argparse
from functools import partial
import json
from pathlib import Path
import queue
import re
import sys
import time
import traceback

from ui.terminal import Console
from host import GameHost
from ui import load_ui
from engine.config import read_config

CONSOLE=None
ANSI_ESCAPE=re.compile(r'\033\[[0-9;]*m')


def report_faults(host,observer,ui,journal_path):
    """模块内部错误只作废一个动作：在终端与日志里留痕，别让它们无声明地消失。"""
    faults=getattr(host,'faults',None) or []
    while len(faults)>report_faults.seen:
        row=faults[report_faults.seen];report_faults.seen+=1
        observer.notice(ui.text('console.module_fault',period=row['period'],action=row['action'],
                                kind=row['type'],error=row['message']))
        to_journal(journal_path,ui.text('console.failure_detail',period=row['period'],
                                        kind=row['type'],error=row['message'],detail=row['detail']))


report_faults.seen=0


def to_journal(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as stream:stream.write(ANSI_ESCAPE.sub('',text)+'\n')


def echo_line(text):
    if CONSOLE is not None:CONSOLE.write(text)
    else:print(ANSI_ESCAPE.sub('',text),flush=True)


def panel_note(journal_path,text,head=False):
    to_journal(journal_path,text)
    if CONSOLE is not None and CONSOLE.panel_on:CONSOLE.set_panel(head=text if head else None,note=None if head else text)
    else:echo_line(text)


def make_world(cfg,force_new,save_path):
    if not force_new and Path(save_path).exists():return GameHost.load(save_path,cfg),False
    host=GameHost.new(cfg);host.save(save_path);return host,True


class Observer:
    def __init__(self,host,journal_path,*,resume=False):
        required=('add','records','announce','send','board')
        describe=getattr(host,'management',None)
        available=describe() if callable(describe) else tuple(name for name in required if callable(getattr(host,name,None)))
        missing=[name for name in required if name not in available]
        if missing:raise ValueError('当前发行版不支持终端管理接口：'+', '.join(missing))
        self.host,self.path=host,Path(journal_path)
        self.ui=host.ui
        records=host.records()
        self.event=max([0,*[row['id'] for row in records['events']]]) if resume else 0
        # Dispatch IDs are allocated before delivery; a delayed spy can arrive
        # after a newer letter. A maximum ID would silently skip that report.
        self.messages_seen={row['id'] for row in records['messages']} if resume else set()
        self.overview_period=None
        self.pending={};self.results=[];self.stop_requested=False;self.paused=False
        # 看海时最常问的两件事：现在是谁在行动、他为什么这么做。
        # (period,actor) 作键：同一国跨回合连续行动时也要重新打抬头。
        self.current_actor=None;self.started_at=0;self.show_thinking='brief';self.last_thinking=''
    def names(self):return {a:r['name'] for a,r in self.host.roster.items()}
    def snapshot(self):
        method=getattr(self.host,'observer_snapshot',None)
        if not callable(method):raise ValueError(self.ui.text('console.unsupported',name='observer_snapshot'))
        return method()
    def board(self):
        if callable(getattr(self.host,'observer_snapshot',None)):
            return self.ui.console.overview(self.snapshot())
        return self.host.board()
    def show_overview(self):
        period=self.host.clock()
        if self.overview_period==period:return
        self.line(self.ui.text('console.period',period=period))
        self.line(self.board())
        self.overview_period=period
    def model_output(self,text):
        if text.startswith('📜'):
            to_journal(self.path,text)
            return
        # Preserve speech and reasoning in the journal; reasoning may be folded on screen.
        for actor,name in self.names().items():
            for icon in ('🗣','💭','🧠','⚠','⏳'):
                text=text.replace(icon+' '+actor+' ',icon+' '+name+' ',1)
        if text.startswith('💭'):
            self.last_thinking=text
            if self.show_thinking=='off':
                to_journal(self.path,text)
                return
            if self.show_thinking!='full':
                # 摘要模式：终端只留思考的第一行，全文照旧进日志。
                to_journal(self.path,text)
                first=next((row for row in text.splitlines() if row.strip()),'')
                if len(first)>160:first=first[:160]+'…'
                # 进滚动区（不是状态面板）：看海时要能往回翻“当时为什么这么做”。
                echo_line(first)
                return
        if text.startswith(('⚠','🧠','⏳')):panel_note(self.path,text)
        else:self.line(text)
    def actor_start(self,actor):
        key=(self.host.clock(),actor)
        if key==self.current_actor:return
        self.current_actor=key;self.started_at=time.monotonic()
        clear=getattr(CONSOLE,'clear_panel',None)
        if callable(clear):clear()
        self.line(self.ui.text('console.actor_start',actor=self.names().get(actor,actor),period=self.host.clock()))
    def actor_finish(self,actor):
        if not self.current_actor or self.current_actor[1]!=actor:return
        self.line(self.ui.text('console.actor_finish',actor=self.names().get(actor,actor),seconds=time.monotonic()-self.started_at))
        self.current_actor=None
    def context_head(self,text):
        for actor,name in self.names().items():
            if text.startswith('🧠 '+actor+' '):self.actor_start(actor)
            text=text.replace('🧠 '+actor+' ','🧠 '+name+' ',1)
        panel_note(self.path,text,head=True)
    def line(self,text):to_journal(self.path,text);echo_line(text)
    def notice(self,text,head=False):panel_note(self.path,text,head)
    def result(self,actor,action,arguments,receipt):
        self.actor_start(actor)
        self.pending.setdefault(actor,[]).append((action,arguments,receipt))
        if self.host.clients[actor].kind=='rule':
            self.flush(actor)
            if action=='end_turn' and receipt['ok']:self.actor_finish(actor)
        # 模型侧不一定会以 end_turn 收尾（议会转换、步数耗尽）；只看行动权是否还在，
        # 否则下一轮同一个国家再行动时抬头不会重打，看海就分不清谁在做。
        try:
            if not self.host.player(actor).action_state().get('can_act'):self.actor_finish(actor)
        except Exception:pass
    def flush(self,actor):
        remember=getattr(self.ui.console,'remember',None)
        if callable(remember):remember(self.host.player(actor).observe())
        for action,args,receipt in self.pending.pop(actor,[]):
            full=self.ui.console.action(self.host.clock(),actor,action,args,receipt,self.names())
            to_journal(self.path,full)
            preview=getattr(self.ui.console,'preview',None)
            echo_line(preview(full,action=action) if callable(preview) else full)

    def on_call(self,actor,step,receipts):
        self.actor_start(actor)
        self.line(self.ui.text('console.call',actor=self.host.roster[actor]['name'],count=step+1));self.flush(actor)
        if not _commands(self.host,self):self.stop_requested=True
        if any(r.get('action')=='end_turn' and r.get('ok') for r in receipts):self.actor_finish(actor)
    def events(self,*,period=None):
        records=self.host.records()
        fresh_events=[]
        for event in records['events']:
            # These remain in player-visible history; the observer already
            # writes each primary receipt through flush().
            detail=event.get('details')
            mirrored=isinstance(detail,dict) and detail.get('source')=='action_receipt'
            if event['id']>self.event and not mirrored:fresh_events.append(event)
        messages=[message for message in records['messages'] if message['id'] not in self.messages_seen]
        if period is not None and (fresh_events or messages):
            self.line(self.ui.text('console.round_events',period=period))
        for event in fresh_events:self.line(self.ui.console.event(event,self.names()))
        self.event=max([self.event,*[row['id'] for row in records['events']]])
        for message in messages:
            full=self.ui.console.message(message,self.names())
            to_journal(self.path,full)
            preview=getattr(self.ui.console,'preview',None)
            echo_line(preview(full) if callable(preview) else full)
        self.messages_seen={row['id'] for row in records['messages']}


def _body(text):
    return Path(text[1:]).read_text(encoding='utf-8') if text.startswith('@') else text


def command(host,line,observer):
    text_ui=observer.ui.text
    line=line.strip()
    if not line:return True
    first,_,body=line.partition(' ')
    if first in ('quit','exit','结束','退出'):return False
    if first in ('help','帮助','?'):observer.line(text_ui('console.commands'))
    elif first in ('pause','暂停'):
        if CONSOLE is None:raise ValueError(text_ui('console.interactive_only'))
        observer.paused=True;observer.notice(text_ui('console.pause_pending'))
    elif first in ('resume','继续'):
        observer.paused=False;observer.notice(text_ui('console.resumed'))
    elif first in ('thoughts','思考'):
        mode=body.strip()
        if mode in ('on','展开'):observer.show_thinking='full'
        elif mode in ('off','收起'):observer.show_thinking='off'
        elif mode in ('brief','摘要'):observer.show_thinking='brief'
        elif mode:raise ValueError(text_ui('console.thought_usage'))
        elif observer.last_thinking:echo_line(observer.last_thinking)
        label={'full':'console.on','off':'console.off','brief':'console.brief'}[observer.show_thinking]
        observer.notice(text_ui('console.thought_mode',mode=text_ui(label)))
    elif first in ('add','加入','加'):
        parts=body.split()
        if not 1<=len(parts)<=2:raise ValueError(text_ui('console.add_usage'))
        if len(parts)==1:actor=host.add(parts[0])
        else:actor=host.add(parts[0],polity=parts[1])
        observer.line(text_ui('console.joined',name=host.roster[actor]['name']))
    elif first in ('say','公告','广播','宣告','announce'):
        text=_body(body)
        if not text.strip():raise ValueError(text_ui('console.empty_announcement'))
        host.announce(text);observer.line(text_ui('console.broadcast'))
    elif first in ('send','寄信','寄','写信','神秘信'):
        target,_,text=body.partition(' ')
        actor=next((a for a,row in host.roster.items() if target in (a,row['name'])),None)
        if actor not in host.actors():raise ValueError(text_ui('console.missing_country'))
        text=_body(text)
        if not text.strip():raise ValueError(text_ui('console.empty_letter'))
        host.send(actor,text);observer.line(text_ui('console.sent',target=target))
    elif first in ('board','国力'):observer.line(observer.board())
    elif first in ('map','地图'):
        import shlex
        parts=shlex.split(body)
        if len(parts)>2:raise ValueError(text_ui('console.map_usage'))
        actor=next((a for a,row in host.roster.items() if parts and parts[0] in (a,row['name'])),None)
        if actor is None:
            if len(parts)>1 or parts and Path(parts[0]).suffix.lower() not in ('.txt','.svg','.png'):raise ValueError(text_ui('console.map_usage'))
            state=observer.snapshot();obs=state['map'];map_text=observer.ui.observer_map(state)
            path=Path(parts[0]) if parts else None
        else:
            if actor not in host.actors():raise ValueError(text_ui('console.map_usage'))
            obs=host.player(actor).observe();map_text=observer.ui.map_text(obs)
            path=Path(parts[1]) if len(parts)==2 else None
        if path is None:observer.line(map_text)
        else:
            if path.suffix.lower()=='.svg':content=observer.ui.map_svg(obs).encode('utf-8')
            elif path.suffix.lower()=='.png':
                content=observer.ui.map_image(obs)
                if content is None:raise ValueError(text_ui('map.empty'))
            elif path.suffix.lower()=='.txt':content=map_text.encode('utf-8')
            else:raise ValueError(text_ui('console.map_format'))
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
            observer.line(text_ui('console.map_saved',path=path))
    elif first in ('rate','利率'):
        import math
        method=getattr(host,'set_rate',None)
        if not callable(method):raise ValueError(text_ui('console.unsupported',name='set_rate'))
        rate=None
        if body.strip():
            try:rate=float(body.strip().rstrip('%'))/100
            except ValueError:raise ValueError(text_ui('console.rate_usage')) from None
            if not math.isfinite(rate):raise ValueError(text_ui('console.rate_usage'))
        observer.line(text_ui('console.bank',**method(rate)))
    elif first in ('cheat','作弊','补助'):
        import re
        parts=body.split();resources={};cavalry=0
        keys={'金':'gold','粮':'food','木':'wood','矿':'ore','油':'oil','装':'equipment','补':'supply','骑':'cavalry'}
        if len(parts)<2:raise ValueError(text_ui('console.cheat_usage'))
        actor=next((a for a,row in host.roster.items() if parts[0] in (a,row['name'])),None)
        for value in parts[1:]:
            match=re.fullmatch(r'(金|粮|木|矿|油|装|补|骑|gold|food|wood|ore|oil|equipment|supply|cavalry)=?(\d+)',value)
            if not match:raise ValueError(text_ui('console.cheat_usage'))
            key=keys.get(match[1],match[1]);amount=int(match[2])
            if key=='cavalry':cavalry+=amount
            else:resources['native:'+key]=resources.get('native:'+key,0)+amount
        method=getattr(host,'subsidize',None)
        if not callable(method):raise ValueError(text_ui('console.unsupported',name='subsidize'))
        result=method(actor,resources,cavalry)
        observer.line(text_ui('console.subsidy',actor=observer.names().get(actor,actor),details=json.dumps(observer.ui.console.value(result,observer.names()),ensure_ascii=False)))
    else:raise ValueError(text_ui('console.commands'))
    return True


def _multiline(line):
    return line.strip() in ('say','公告','广播','宣告','announce') or line.strip().startswith(('send ','寄信 ','寄 ','写信 ','神秘信 ')) and len(line.strip().split())==2


def _commands(host,observer):
    while CONSOLE is not None:
        try:line=CONSOLE.queue.get_nowait()
        except queue.Empty:return True
        # Console preserves multiline bodies; the first newline is a separator.
        line=line.replace('\n',' ',1) if '\n' in line and line.split('\n',1)[0].strip() in ('say','公告','广播','宣告','announce') else line
        if line.startswith(('send ','寄信 ','寄 ','写信 ','神秘信 ')) and '\n' in line:
            first,body=line.split('\n',1);line=first+' '+body
        try:
            if not command(host,line,observer):
                observer.notice(observer.ui.text('console.stop_pending'));return False
        except (ValueError,OSError) as exc:observer.notice('⚠ '+str(exc))
    return True


def _piped(host,text,observer):
    pending=[]
    for line in text.splitlines():
        if pending:
            if line.strip().upper()!='END':pending.append(line);continue
            header,*body=pending;line=header+' '+'\n'.join(body);pending=[]
        elif _multiline(line):pending=[line];continue
        try:
            if not command(host,line,observer):return False
        except (ValueError,OSError) as exc:observer.notice('⚠ '+str(exc))
    if pending:observer.notice(observer.ui.text('console.missing_end'))
    return True


def run(argv=None):
    global CONSOLE
    parser=argparse.ArgumentParser(description='战国统一玩家 API 客户端宿主')
    parser.add_argument('--config',default='mp_config.json');parser.add_argument('--save')
    parser.add_argument('--new',action='store_true');parser.add_argument('--turns',type=int)
    parser.add_argument('--language',help='UI language, e.g. zh-CN or en-US')
    parser.add_argument('--small-ctx',action='store_true');parser.add_argument('--ctx-window',type=int)
    args=parser.parse_args(argv)
    overrides={}
    if args.language is not None:overrides['language']=args.language
    if args.small_ctx:overrides['ctx_window']=262144
    if args.ctx_window is not None:overrides['ctx_window']=args.ctx_window
    if args.save is not None:overrides['save']=args.save
    if args.turns is not None:overrides['max_turns']=args.turns
    ui=None
    try:
        prepared=GameHost.prepare(read_config(args.config),overrides=overrides)
        cfg=prepared.data
        save_path=Path(cfg.get('save','mp_save.json'))
        journal_path=Path(cfg.get('journal','mp_journal.md'))
        ui=load_ui(cfg,runtime=prepared.runtime)
        host,new=make_world(prepared,args.new,save_path)
    except (ValueError,OSError) as exc:
        message=ui.text('console.start_failed',error=exc) if ui else f'配置 / 启动失败：{exc}'
        print(message,file=sys.stderr);return 2
    ui=host.ui
    try:observer=Observer(host,journal_path,resume=not new)
    except (ValueError,OSError) as exc:
        print(ui.text('console.start_failed',error=exc),file=sys.stderr);return 2
    if sys.stdin.isatty():
        from _thread import interrupt_main
        CONSOLE=Console(prompt=ui.text('console.prompt'),on_interrupt=interrupt_main,is_multiline_start=_multiline).start()
    # Piped commands are deterministic and available before the first cycle.
    elif not sys.stdin.closed:
        import select
        if select.select([sys.stdin],[],[],0)[0]:
            if not _piped(host,sys.stdin.read(),observer):
                host.save(save_path);return 0
    observer.line(ui.text('console.new' if new else 'console.resume'))
    observer.line(ui.text('console.banner'))
    observer.line(ui.text('console.watch',save=save_path,journal=journal_path))
    observer.line(ui.text('console.commands'))
    observer.events()
    stop=cfg.get('max_turns',200)
    try:
        while host.clock()<stop:
            finished=getattr(host,'finished',None)
            if callable(finished) and finished():break
            if not _commands(host,observer):break
            while observer.paused:
                if not _commands(host,observer):observer.stop_requested=True;break
                time.sleep(.1)
            if observer.stop_requested:break
            observer.show_overview()
            host.cycle(on_result=observer.result,emit=observer.model_output,
                       head=observer.context_head,on_call=observer.on_call)
            report_faults(host,observer,ui,journal_path)
            for actor in list(observer.pending):observer.flush(actor)
            observer.events(period=host.clock()-1);ending=observer.snapshot() if callable(getattr(host,'observer_snapshot',None)) else None
            host.save(save_path)
            if callable(getattr(host,'observer_snapshot',None)):
                map_path=journal_path.with_suffix('.map.txt')
                map_path.write_text(ui.observer_map(ending),encoding='utf-8')
            if CONSOLE is not None:CONSOLE.clear_panel()
            if observer.stop_requested:break
            finished=getattr(host,'finished',None)
            if callable(finished) and finished():break
        host.save(save_path)
        if getattr(host,'victory_event',None) is not None:
            observer.line(ui.text('console.victory',event=json.dumps(host.victory_event,ensure_ascii=False)))
        observer.line(ui.text('console.finish',period=host.clock(),path=save_path))
        return 0
    except KeyboardInterrupt:
        if CONSOLE is not None:CONSOLE.close();CONSOLE=None
        observer.line(ui.text('console.interrupt'));return 130
    except Exception as exc:
        # 停机是最后手段：留下回合号、完整 traceback 和续局办法，别只留一行被擦掉的字。
        to_journal(journal_path,ui.text('console.failure_detail',kind=type(exc).__name__,error=exc,
                                        period=host.clock(),detail=traceback.format_exc()))
        if CONSOLE is not None:CONSOLE.close();CONSOLE=None
        observer.line(ui.text('console.failure',kind=type(exc).__name__,error=exc,
                                period=host.clock(),path=save_path));return 1
    finally:
        if CONSOLE is not None:CONSOLE.close();CONSOLE=None


if __name__=='__main__':raise SystemExit(run())
