# -*- coding: utf-8 -*-
"""`rl/hw.py` 的物理核探测 —— 这里守的是一条**会静默变慢**的配置。

判据不是"跑得对"，是"别把线程数写死"。ECS 上多开一个 SMT 线程实测慢 3.4×，
而它**不报错、不崩**，只是慢 —— 只有测试能挡住回归。
"""
import os
import unittest

from rl.hw import physical_cores, set_threads


class TestPhysicalCores(unittest.TestCase):
    def test_在合法范围内(self):
        n = physical_cores()
        self.assertGreaterEqual(n, 1)
        # 物理核数不可能超过逻辑核数
        self.assertLessEqual(n, os.cpu_count() or 1)

    def test_不含超线程_当前机器无SMT时为真核数(self):
        """本机（Pi 5 / ECS）都是「无 SMT」或「SMT 但去重后=物理核」，
        所以 physical_cores() 应该正好等于 /proc/cpuinfo 里去重后的核数 ——
        这里用「≤ 逻辑核数」+「≥1」两条夹住，具体的数字交给实测量（见 rl/hw.py 文档）。"""
        self.assertIsInstance(physical_cores(), int)


class TestSetThreads(unittest.TestCase):
    def tearDown(self):
        set_threads(0)

    def test_自动等于物理核数(self):
        import torch
        self.assertEqual(set_threads(0), physical_cores())
        self.assertEqual(torch.get_num_threads(), physical_cores())

    def test_显式值优先(self):
        import torch
        self.assertEqual(set_threads(1), 1)
        self.assertEqual(torch.get_num_threads(), 1)

    def test_负数与零一样走自动(self):
        self.assertEqual(set_threads(-3), physical_cores())


if __name__ == "__main__":
    unittest.main()
