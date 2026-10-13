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
        self.host,self.path=host,Path(journal_path)
        records=host.world.intelligence.to_save()
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
            self.line(f'[{self.host.world.government.period()}·行动] {self.host.roster[actor]["name"]} ◇ {action} {json.dumps(args,ensure_ascii=False)} → {receipt["status"]} {receipt.get("message","")}')
            # Full detached receipts belong to the journal, brief text to console.
            to_journal(self.path,'```json\n'+json.dumps(receipt,ensure_ascii=False)+'\n```')
    def on_call(self,actor,step,receipts):
        self.line(f'◈ {self.host.roster[actor]["name"]} 第 {step+1} 次调用');self.flush(actor)
    def events(self):
        records=self.host.world.intelligence.to_save()
        for event in records['events']:
            if event['id']>self.event:self.line(f'[{event["period"]}·{event["kind"]}] {event["text"]}')
        self.event=max([self.event,*[row['id'] for row in records['events']]])
        for message in records['messages']:
            if message['id']>self.message:self.line(f'[{message["sampled"]}·通信] {message["source"]} → {message["target"]} {json.dumps(message["payload"],ensure_ascii=False)}')
        self.message=max([self.message,*[row['id'] for row in records['messages']]])


def _body(text):
    return Path(text[1:]).read_text(encoding='utf-8') if text.startswith('@') else text


def command(host,line,observer):
    line=line.strip()
    if not line:return True
    first,_,body=line.partition(' ')
    if first in ('quit','exit','结束','退出'):return False
    if first in ('add','加入'):
        actor=host.add(body.strip());observer.line(f'已加入 {host.roster[actor]["name"]}')
    elif first in ('say','公告'):
        text=_body(body)
        if not text.strip():raise ValueError('公告正文不能为空')
        host.world.intelligence.emit('announcement',text,public=True);observer.line('已向全世界广播')
    elif first in ('send','寄信'):
        target,_,text=body.partition(' ')
        actor=next((a for a,row in host.roster.items() if target in (a,row['name'])),None)
        if actor not in host.world.nation_actors:raise ValueError('收件国不存在或已经灭亡')
        text=_body(text)
        if not text.strip():raise ValueError('信件正文不能为空')
        host.world.intelligence.emit('observer-letter',text,parties=(actor,));observer.line(f'已寄信 {target}')
    elif first in ('board','国力'):observer.line(host.board())
    elif first in ('map','地图'):
        from clients.view import player_map
        actor=next((a for a,row in host.roster.items() if body.strip() in (a,row['name'])),None)
        if actor is None:raise ValueError('用法：map 国名或国家 ID')
        observer.line(player_map(host.player(actor).observe()))
    elif first in ('rate','利率'):
        # Native credit policy owns its settings. No mutable legacy bank table.
        observer.line('银行利率由发行版的银行模块声明；请修改该模块配置后用于新局')
    else:raise ValueError('命令：add / send / say / board / map / quit')
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
    if pending:observer.notice('⚠ 多行命令缺少 END，未执行')
    return True


def run(argv=None):
    global CONSOLE
    parser=argparse.ArgumentParser(description='战国统一玩家 API 客户端宿主')
    parser.add_argument('--config',default='mp_config.json');parser.add_argument('--save')
    parser.add_argument('--new',action='store_true');parser.add_argument('--turns',type=int)
    parser.add_argument('--small-ctx',action='store_true');parser.add_argument('--ctx-window',type=int)
    args=parser.parse_args(argv)
    cfg=json.loads(Path(args.config).read_text(encoding='utf-8'));ctx.apply_defaults(cfg)
    for item in cfg['nations']:
        if args.small_ctx:item['ctx_window']=262144
        if args.ctx_window:item['ctx_window']=args.ctx_window
    save_path=Path(args.save or cfg.get('save','mp_save.json'))
    journal_path=Path(cfg.get('journal','mp_journal.md'))
    try:host,new=make_world(cfg,args.new,save_path)
    except (ValueError,OSError) as exc:
        print(f'启动失败：{exc}',file=sys.stderr);return 2
    observer=Observer(host,journal_path,resume=not new)
    if sys.stdin.isatty():CONSOLE=Console(is_multiline_start=_multiline).start()
    # Piped commands are deterministic and available before the first cycle.
    elif not sys.stdin.closed:
        import select
        if select.select([sys.stdin],[],[],0)[0]:
            if not _piped(host,sys.stdin.read(),observer):
                host.save(save_path);return 0
    observer.line('新开一局' if new else '读档续局')
    observer.line('玩家 API v1；规则 AI 只维护 v11plus；央行由发行版加载')
    observer.events()
    stop=args.turns if args.turns is not None else cfg.get('max_turns',200)
    if type(stop) is not int or stop<0:raise ValueError('turns must be nonnegative')
    try:
        while host.world.government.period()<stop:
            if not _commands(host,observer):break
            host.cycle(on_result=observer.result,emit=partial(panel_note,journal_path),
                       head=partial(panel_note,journal_path,head=True),on_call=observer.on_call)
            observer.events();observer.line(host.board());host.save(save_path)
            if CONSOLE is not None:CONSOLE.clear_panel()
        host.save(save_path)
        observer.line(f'终局：第 {host.world.government.period()} 周期，存档 {save_path}')
        return 0
    except KeyboardInterrupt:
        observer.notice('🛑 已中断；保留最近完整结算存档');return 130
    except Exception as exc:
        observer.notice(f'🛑 {type(exc).__name__}: {exc}；保留最近完整结算存档');return 1
    finally:
        if CONSOLE is not None:CONSOLE.close();CONSOLE=None


if __name__=='__main__':raise SystemExit(run())
