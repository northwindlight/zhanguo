"""Offline observer projection and boundary control, using temporary saves."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from host import GameHost
from scenarios.eight_nations import build, build_config
from ui.desktop import Controller


class TestDesktop(unittest.TestCase):
    def host(self):
        cfg = build_config({'rule_ai': 'v13', 'nations': [{'client': 'rule', 'rule_ai': 'v13'}]}, 20261023)
        host = build(seed=20261023, config=cfg)
        return host

    def test_detached_exact_projection(self):
        h = self.host(); snapshot = h.observer_snapshot(); before = h.to_save()
        self.assertTrue(all(client.version == 'v13' for client in h.clients.values()))
        frame = h.ui.desktop_frame(snapshot)
        self.assertEqual(len(frame['tiles']), 576)
        self.assertEqual(len(frame['nations']), 8)
        self.assertEqual(len(frame['armies']), 0)
        self.assertEqual(len(frame['scene']['guards']),292)
        self.assertEqual(len(h.world.units.guard_once),0)
        self.assertEqual(frame['map_seed'],20261023)
        self.assertEqual(sum(len(t['buildings']) for t in frame['tiles']),len(h.world.buildings.snapshot().instances))
        for nation in frame['nations']:
            self.assertEqual(nation['land'], 5)
            self.assertEqual(sum(nation['units'].values()), 0)
            self.assertTrue(all('native:' not in s['name'] for s in nation['stocks']))
        json.dumps(frame, allow_nan=False)
        frame['tiles'].clear()
        self.assertEqual(before, h.to_save())
        self.assertFalse(h.player('scenario:qin').submit('observer_snapshot', {})['ok'])

    def test_pause_queued_during_slow_cycle_saves_boundary(self):
        h = self.host(); original = h.cycle
        entered = threading.Event(); release = threading.Event(); rows = []
        def slow(**kwargs):
            entered.set();self.assertTrue(release.wait(10));return original(**kwargs)
        h.cycle = slow
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'session.json'
            control = Controller(h, path, rows.append, delay=.05)
            worker = threading.Thread(target=control.run, daemon=True);worker.start()
            control.commands.put({'command': 'resume'})
            self.assertTrue(entered.wait(10))
            control.commands.put({'command': 'pause'})
            control.commands.put({'command': 'stop'})
            self.assertEqual(h.clock(), 0)
            release.set();worker.join(20);self.assertFalse(worker.is_alive())
            self.assertEqual(h.clock(), 1)
            self.assertEqual(control.saved_period, 1)
            self.assertEqual(GameHost.load(path, h.cfg).to_save(), h.to_save())
            self.assertEqual(rows[-1]['state'], 'stopped')
            self.assertEqual([r['revision'] for r in rows], list(range(1,len(rows)+1)))

    def test_fault_does_not_overwrite_last_complete_save(self):
        h = self.host(); rows = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'session.json';control = Controller(h,path,rows.append)
            control.persist();before = path.read_bytes()
            def fail(**kwargs):
                h.session._boundary = False
                raise RuntimeError('synthetic failure')
            h.cycle = fail
            with self.assertRaises(RuntimeError):control.advance()
            control.command({'command':'stop'})
            self.assertEqual(path.read_bytes(),before)
            self.assertTrue(control.failed)

    def test_model_line_forwards_thinking_and_speech_streams(self):
        """emit 行按真实本地化模板拆回 actor/类型/文本并转发为 stream 消息。"""
        h = self.host(); rows = []
        with tempfile.TemporaryDirectory() as directory:
            control = Controller(h, Path(directory)/'session.json', rows.append)
            for key, kind in (('llm.thinking','thinking'),('llm.speech','body')):
                line = h.ui.text(key, actor='scenario:qin', text='先屯粮，再看邻国')
                control.model_line(line)
                self.assertEqual(rows[-1]['type'], 'stream')
                self.assertEqual(rows[-1]['actor'], 'scenario:qin')
                self.assertEqual(rows[-1]['kind'], kind)
                self.assertEqual(rows[-1]['text'], '先屯粮，再看邻国')
            # 等待模型转为 activity（非 stream）；无关行与畸形行不产生消息。
            streams = [r for r in rows if r['type'] == 'stream']
            control.model_line(h.ui.text('llm.request', actor='scenario:qin', count=2))
            self.assertEqual(rows[-1]['type'], 'activity')
            control.model_line('完全无关的一行')
            control.model_line(None)
            self.assertEqual([r for r in rows if r['type'] == 'stream'], streams)

    def test_model_line_reports_waiting_and_retry_activity(self):
        """等待模型与重试行转为 activity 消息，供地图徽标显示。"""
        h = self.host(); rows = []
        with tempfile.TemporaryDirectory() as directory:
            control = Controller(h, Path(directory)/'session.json', rows.append)
            control.model_line(h.ui.text('llm.request', actor='scenario:qin', count=2))
            self.assertEqual(rows[-1]['type'], 'activity')
            self.assertEqual(rows[-1]['actor'], 'scenario:qin')
            self.assertIn('正在思考', rows[-1]['text'])
            self.assertIn('2', rows[-1]['text'])
            control.model_line(h.ui.text('llm.retry', actor='scenario:qin', attempt=3, total=10, error='timeout', wait=5))
            self.assertEqual(rows[-1]['type'], 'activity')
            self.assertIn('重试', rows[-1]['text'])
            self.assertIn('3', rows[-1]['text'])
            self.assertIn('API', rows[-1]['text'])


if __name__ == '__main__':unittest.main()
