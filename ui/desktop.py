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

    def send(self, kind, **data):
        self.revision += 1
        self.output({'protocol': PROTOCOL, 'generation': self.generation,
                     'revision': self.revision, 'type': kind, **data})

    def status(self, state, **extra):
        self.send('status', state=state, period=self.host.clock(),
                  saved_period=self.saved_period, save=str(self.save), **extra)

    def snapshot(self):
        frame=self.host.ui.desktop_frame(self.host.observer_snapshot())
        frame['clients']=[{'actor':actor,'kind':client.kind,'version':client.version,
                           'strategy':getattr(getattr(client,'state',None),'profile',None)} for actor,client in self.host.clients.items()]
        self.send('snapshot', frame=frame)

    def persist(self):
        self.host.save(self.save)
        self.saved_period = self.host.clock()

    def command(self, row):
        op = row.get('command')
        if op == 'pause':
            self.running = False
            self.persist()
            self.status('paused')
        elif op in ('resume', 'step'):
            if self.failed:
                raise ValueError('本回合执行失败，保留最近完整存档；请关闭后重新启动')
            if self.host.finished() or self.host.clock() >= self.max_turns:
                raise ValueError('对局已结束或达到回合上限')
            if op=='step' and self.waiting is not None:raise ValueError('本期演出尚未完成，请先继续或快进')
            self.running = op == 'resume'
            if op == 'step':self.advance()
            else:self.status('running')
        elif op == 'save':
            self.persist();self.status('running' if self.running else 'paused')
        elif op == 'snapshot':self.snapshot()
        elif op == 'playback_ack':
            if row.get('generation')==self.generation and row.get('sequence')==self.waiting:
                self.waiting=None
                self.status('running' if self.running else 'paused')
        elif op == 'stop':
            self.running = False
            if not self.failed:self.persist()
            self.stopping = True
            self.status('stopped')
        else:raise ValueError(f'未知桌面命令：{op}')

    def advance(self):
        if self.waiting is not None:raise ValueError('等待本期演出确认后才能推进下一期')
        self.status('advancing')
        try:
            capture=None
            if self.playback:
                from mods.ui.desktop_activity import TimelineCapture
                try:capture=TimelineCapture(self.host)
                except Exception as exc:self.send('notice',message=f'演出初始化失败，使用完整快照：{exc}')
            with redirect_stdout(sys.stderr):
                if capture:self.host.cycle(on_result=capture.capture)
                else:self.host.cycle()
        except Exception:
            self.running = False;self.failed = True
            raise
        self.persist()
        if capture:
            beats=capture.finish()
            if capture.warning:self.send('notice',message=capture.warning)
            if beats:
                self.timeline_sequence+=1;self.waiting=self.timeline_sequence
                self.send('timeline',sequence=self.waiting,period=capture.period,beats=beats)
        self.snapshot()
        if self.host.finished() or self.host.clock() >= self.max_turns:self.running = False
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
                except Exception as exc:self.send('error', message=str(exc))
                deadline = time.monotonic()+self.delay
            except Exception as exc:
                self.running = False
                self.send('error', message=str(exc))


def main(argv=None):
    p = argparse.ArgumentParser(description='战国 Godot 离线观察者宿主')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=20261023)
    p.add_argument('--delay', type=float, default=0.8)
    p.add_argument('--resume', type=Path)
    args = p.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    resume = args.resume or (Path(os.environ['ZHANGUO_RESUME']) if os.environ.get('ZHANGUO_RESUME') else None)
    if resume:
        cfg = json.loads(resume.with_name('config.json').read_text(encoding='utf-8'))
        if not cfg.get('nations') or any(n.get('client') != 'rule' for n in cfg['nations']):
            raise ValueError('桌面首版只支持离线规则 AI 演示档')
        if os.environ.get('ZHANGUO_UI_MODS'):cfg['ui_mods'] = os.environ['ZHANGUO_UI_MODS']
        with redirect_stdout(sys.stderr):host = GameHost.load(resume, cfg)
    else:
        cfg = build_config({'rule_ai': 'v13', 'nations': [{'client': 'rule', 'rule_ai': 'v13'}]}, args.seed)
        if os.environ.get('ZHANGUO_UI_MODS'):cfg['ui_mods'] = os.environ['ZHANGUO_UI_MODS']
        with redirect_stdout(sys.stderr):host = build(seed=args.seed, config=cfg)
    write_json(args.output/'config.json', cfg)
    if not callable(getattr(host.ui, 'desktop_frame', None)):
        raise ValueError('当前 UI Mod 未提供 desktop_frame')
    protocol_out = sys.stdout
    def output(row):
        protocol_out.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        protocol_out.flush()
    control = Controller(host, args.output/'session.json', output, delay=max(0.05, args.delay),playback=True)
    def read():
        for line in sys.stdin:
            try:
                row = json.loads(line)
                if not isinstance(row, dict):raise ValueError('命令须为 JSON 对象')
            except (ValueError, TypeError):row = {'command': 'invalid'}
            control.commands.put(row)
        control.commands.put({'command': 'stop'})
    Thread(target=read, daemon=True).start()
    control.run()


if __name__ == '__main__':main()
