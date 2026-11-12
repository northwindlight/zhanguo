"""桌面宿主在"真人 + LLM 席位"局里并发推进：真人输入不被 AI 等待阻塞。

用离线替身代替真实 LLM（sleep 模拟网络等待），直接驱动 Controller 的协议：
human_turn → human_submit(end_turn) → 演出 ack → 结算。
"""
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from queue import Queue, Empty

from host import GameHost
from ui.desktop import Controller


class FakeLlmClient:
    kind = 'llm'
    def __init__(self, actor, delay=0.25):
        self.actor = actor; self.delay = delay
    def play(self, player, *, emit=None, head=None, on_call=None, on_stream=None):
        time.sleep(self.delay)
        return [player.submit('end_turn', {'summary': '离线替身'})]
    def to_save(self): return {'kind': self.kind, 'version': 'fake/1', 'state': {}}


def config():
    return {'map_size': 12, 'seed': 73, 'nations': [
        {'id': 'scenario:qin', 'name': '秦', 'client': 'human', 'nickname': '测试玩家'},
        {'id': 'scenario:chu', 'name': '楚', 'client': 'rule'},
        {'id': 'scenario:zhao', 'name': '赵', 'client': 'rule'}]}


class TestDesktopHumanParallel(unittest.TestCase):
    def test_human_turn_finishes_while_llm_seats_are_still_thinking(self):
        host = GameHost.new(config())
        order = list(host.session.turn_order())
        host.session.clients[order[1]] = FakeLlmClient(order[1], 0.30)
        host.session.clients[order[2]] = FakeLlmClient(order[2], 0.30)
        rows = []; lock = threading.Lock(); event = threading.Event()
        def output(row):
            with lock: rows.append(row)
            event.set()
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            control = Controller(host, folder/'session.json', output, delay=0.0, playback=True)
            worker = threading.Thread(target=control.advance, daemon=True)
            started = time.monotonic(); worker.start()
            human_turn = None; submitted = False; acks = 0
            while worker.is_alive() or True:
                event.wait(0.05); event.clear()
                with lock: pending = list(rows); rows.clear()
                for row in pending:
                    if row['type'] == 'error': self.fail(row)
                    if row['type'] == 'human_turn' and not submitted:
                        human_turn = row
                        control.commands.put({'command': 'human_submit',
                            'actor': row['actor'], 'period': row['period'],
                            'control_generation': row['control_generation'], 'seat_token': row['seat_token'],
                            'request_id': 'test/end', 'operation': 'end_turn', 'arguments': {}})
                        submitted = True
                    if row['type'] == 'timeline':
                        control.commands.put({'command': 'playback_ack',
                            'generation': row['generation'], 'sequence': row['sequence']})
                        acks += 1
                elapsed = time.monotonic()-started
                if not worker.is_alive(): break
                self.assertLess(elapsed, 5.0, f'真人回合没有在 AI 思考结束前返回：{human_turn}')
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic()-started, 0.60)
            self.assertTrue(submitted); self.assertGreaterEqual(acks, 1)
            self.assertEqual([row['actor'] for row in host.session.last_results], order)
            self.assertTrue(host.session._boundary)
            self.assertEqual(host.session.world.phase, '行动')
            self.assertTrue(folder.joinpath('session.json').exists())


if __name__ == '__main__':
    unittest.main()
