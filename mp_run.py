"""Session host CLI: clients act through PlayerSession, host settles and saves."""
from __future__ import annotations
import argparse
from functools import partial
import json
from pathlib import Path
import queue
import sys

import ctx
from console import Console
from host import GameHost
from ui import load_ui

CONSOLE=None


def to_journal(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as stream:stream.write(text+'\n')


def echo_line(text):
    if CONSOLE is not None:CONSOLE.write(text)
    else:print(text,flush=True)


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
        self.message=max([0,*[row['id'] for row in records['messages']]]) if resume else 0
        self.pending={};self.results=[]
    def line(self,text):to_journal(self.path,text);echo_line(text)
    def notice(self,text,head=False):panel_note(self.path,text,head)
    def result(self,actor,action,arguments,receipt):
        self.pending.setdefault(actor,[]).append((action,arguments,receipt))
        if self.host.clients[actor].kind=='rule':self.flush(actor)
    def flush(self,actor):
        for action,args,receipt in self.pending.pop(actor,[]):
            self.line(self.ui.text('console.action',period=self.host.clock(),actor=self.host.roster[actor]['name'],action=action,arguments=json.dumps(args,ensure_ascii=False),status=receipt['status'],message=receipt.get('message','')))
            # Full detached receipts belong to the journal, brief text to console.
            to_journal(self.path,'```json\n'+json.dumps(receipt,ensure_ascii=False)+'\n```')
    def on_call(self,actor,step,receipts):
        self.line(self.ui.text('console.call',actor=self.host.roster[actor]['name'],count=step+1));self.flush(actor)
    def events(self):
        records=self.host.records()
        for event in records['events']:
            if event['id']>self.event:self.line(f'[{event["period"]}·{event["kind"]}] {event["text"]}')
        self.event=max([self.event,*[row['id'] for row in records['events']]])
        for message in records['messages']:
            if message['id']>self.message:self.line(self.ui.text('console.message',period=message['sampled'],source=message['source'],target=message['target'],payload=json.dumps(message['payload'],ensure_ascii=False)))
        self.message=max([self.message,*[row['id'] for row in records['messages']]])


def _body(text):
    return Path(text[1:]).read_text(encoding='utf-8') if text.startswith('@') else text


def command(host,line,observer):
    text_ui=observer.ui.text
    line=line.strip()
    if not line:return True
    first,_,body=line.partition(' ')
    if first in ('quit','exit','结束','退出'):return False
    if first in ('add','加入'):
        actor=host.add(body.strip());observer.line(text_ui('console.joined',name=host.roster[actor]['name']))
    elif first in ('say','公告'):
        text=_body(body)
        if not text.strip():raise ValueError(text_ui('console.empty_announcement'))
        host.announce(text);observer.line(text_ui('console.broadcast'))
    elif first in ('send','寄信'):
        target,_,text=body.partition(' ')
        actor=next((a for a,row in host.roster.items() if target in (a,row['name'])),None)
        if actor not in host.actors():raise ValueError(text_ui('console.missing_country'))
        text=_body(text)
        if not text.strip():raise ValueError(text_ui('console.empty_letter'))
        host.send(actor,text);observer.line(text_ui('console.sent',target=target))
    elif first in ('board','国力'):observer.line(host.board())
    elif first in ('map','地图'):
        import shlex
        parts=shlex.split(body)
        if not 1<=len(parts)<=2:raise ValueError(text_ui('console.map_usage'))
        actor=next((a for a,row in host.roster.items() if parts[0] in (a,row['name'])),None)
        if actor not in host.actors():raise ValueError(text_ui('console.map_usage'))
        obs=host.player(actor).observe()
        if len(parts)==1:observer.line(observer.ui.map_text(obs))
        else:
            path=Path(parts[1])
            if path.suffix.lower()=='.svg':content=observer.ui.map_svg(obs).encode('utf-8')
            elif path.suffix.lower()=='.png':
                content=observer.ui.map_image(obs)
                if content is None:raise ValueError(text_ui('map.empty'))
            else:raise ValueError(text_ui('console.map_format'))
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
            observer.line(text_ui('console.map_saved',path=path))
    elif first in ('rate','利率'):
        # Native credit policy owns its settings. No mutable legacy bank table.
        observer.line(text_ui('console.rate'))
    else:raise ValueError(text_ui('console.commands'))
    return True


def _multiline(line):
    return line.strip() in ('say','公告') or line.strip().startswith(('send ','寄信 ')) and len(line.strip().split())==2


def _commands(host,observer):
    while CONSOLE is not None:
        try:line=CONSOLE.queue.get_nowait()
        except queue.Empty:return True
        # Console preserves multiline bodies; the first newline is a separator.
        line=line.replace('\n',' ',1) if '\n' in line and line.split('\n',1)[0].strip() in ('say','公告') else line
        if line.startswith(('send ','寄信 ')) and '\n' in line:
            first,body=line.split('\n',1);line=first+' '+body
        try:
            if not command(host,line,observer):return False
        except (ValueError,OSError) as exc:observer.notice('⚠ '+str(exc))
    return True


def _piped(host,text,observer):
    pending=[]
    for line in text.splitlines():
        if pending:
            if line.strip()!='END':pending.append(line);continue
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
    cfg=json.loads(Path(args.config).read_text(encoding='utf-8'));ctx.apply_defaults(cfg)
    if args.language:
        cfg['language']=args.language
        for item in cfg['nations']:item['language']=args.language
    ui=load_ui(cfg)
    for item in cfg['nations']:
        if args.small_ctx:item['ctx_window']=262144
        if args.ctx_window:item['ctx_window']=args.ctx_window
    save_path=Path(args.save or cfg.get('save','mp_save.json'))
    journal_path=Path(cfg.get('journal','mp_journal.md'))
    try:host,new=make_world(cfg,args.new,save_path)
    except (ValueError,OSError) as exc:
        print(ui.text('console.start_failed',error=exc),file=sys.stderr);return 2
    ui=host.ui
    try:observer=Observer(host,journal_path,resume=not new)
    except (ValueError,OSError) as exc:
        print(ui.text('console.start_failed',error=exc),file=sys.stderr);return 2
    if sys.stdin.isatty():CONSOLE=Console(prompt=ui.text('console.prompt'),is_multiline_start=_multiline).start()
    # Piped commands are deterministic and available before the first cycle.
    elif not sys.stdin.closed:
        import select
        if select.select([sys.stdin],[],[],0)[0]:
            if not _piped(host,sys.stdin.read(),observer):
                host.save(save_path);return 0
    observer.line(ui.text('console.new' if new else 'console.resume'))
    observer.line(ui.text('console.banner'))
    observer.events()
    stop=args.turns if args.turns is not None else cfg.get('max_turns',200)
    if type(stop) is not int or stop<0:raise ValueError('turns must be nonnegative')
    try:
        while host.clock()<stop:
            if not _commands(host,observer):break
            host.cycle(on_result=observer.result,emit=partial(panel_note,journal_path),
                       head=partial(panel_note,journal_path,head=True),on_call=observer.on_call)
            observer.events();observer.line(host.board());host.save(save_path)
            if CONSOLE is not None:CONSOLE.clear_panel()
        host.save(save_path)
        observer.line(ui.text('console.finish',period=host.clock(),path=save_path))
        return 0
    except KeyboardInterrupt:
        observer.notice(ui.text('console.interrupt'));return 130
    except Exception as exc:
        observer.notice(ui.text('console.failure',kind=type(exc).__name__,error=exc));return 1
    finally:
        if CONSOLE is not None:CONSOLE.close();CONSOLE=None


if __name__=='__main__':raise SystemExit(run())
