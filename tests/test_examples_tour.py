"""示例巡礼必须能离线跑完。

`mods/examples/tour.py` 是文档里每条配方的可运行版本。它腐化过一次：
发行版把客户端改名后脚本没跟上，脚本在联网请求上炸掉。
这里把它当冒烟测试锁住——不联网、不需要 API key、退出码必须是 0。
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 600


class TestExamplesTour(unittest.TestCase):

    def test_tour_runs_offline_to_the_end(self):
        result = subprocess.run([sys.executable, 'mods/examples/tour.py'],
                                cwd=str(ROOT), capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(result.returncode, 0,
                         f'示例巡礼失败：\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}')
        self.assertIn('文档：docs/Mod开发指南.md', result.stdout)

    def test_tour_never_asks_for_a_real_endpoint(self):
        """巡礼里不该出现真实接入参数：出现就说明某一步想联网了。"""
        source = (ROOT / 'mods' / 'examples' / 'tour.py').read_text(encoding='utf-8')
        for marker in ('base_url', 'api_key', 'http://', 'https://'):
            self.assertNotIn(marker, source, f'巡礼脚本里出现了 {marker}：它应当完全离线')


if __name__ == '__main__':
    unittest.main()
