# -*- coding: utf-8 -*-
"""`Sandbox.clone()` 的守卫 —— 2026-09-29（`rl/opponents.py` 的稻草人每一步都要它）。

★ 病灶：`clone()` 原来是**手列属性**的，**漏了 `players`**
  ⇒ 在副本上 `step()` 当场 `AttributeError`（`_turn_order` → `self.players`）。
  ★ 手列清单**必然漏** —— 加一个属性就漏一个，而且**不报错**，直到某条路径用到它。
    我是在写稻草人时第一次跑就撞上的：克隆三次、每次 step 都崩。

★★ 这里钉**两件事**：
  ① **一个属性都不许漏**（`clone().__dict__` 的键集与真身**相同**）—— 这是根治那条；
  ② **副本改了不许影响真身**（world/turn/pending/log/三本账本逐个验）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox                        # noqa: E402


def _sb(seed=31):
    return Sandbox(seed=seed, size=10, t_max=60, n_nations=2,
                   halls_known=True, territory=True).reset()


class TestCloneIsCompleteAndIsolated(unittest.TestCase):

    def test_no_attribute_is_missed(self):
        """① 属性集必须**逐字相同** —— 手列清单漏一个就在这里红。"""
        sb = _sb()
        c = sb.clone()
        miss = set(sb.__dict__) - set(c.__dict__)
        self.assertEqual(miss, set(),
                         f"`clone()` 漏了这些属性：{sorted(miss)} ⇒ 副本上用到它们就崩")

    def test_step_on_a_clone_works(self):
        """① 端到端：副本上 `step()` 不许崩（病灶的现场）。"""
        sb = _sb()
        sb._auto_advance()
        c = sb.clone()
        acts = c.legal()
        self.assertGreater(len(acts), 0, "用例前提：副本上得有合法动作")
        ok, msg = c.step(acts[0])
        self.assertIsInstance(ok, bool, f"副本 step 没返回 bool：{ok!r} / {msg!r}")

    def test_the_original_is_untouched(self):
        """② 副本改了，真身不许动。"""
        sb = _sb()
        sb._auto_advance()
        # ★ 快照要取**会被 step 改到**的量：回合/待动队列/日志/军队数/击杀账本
        #   （`halls_known` 是个**布尔标志**不是字典 —— 我第一版拿它当字典，TypeError）
        snap = (sb.turn, list(sb.pending), len(sb.log),
                len(sb.world.armies), len(sb.kills.snapshot()[0]))
        c = sb.clone()
        for a in c.legal()[:20]:
            c.step(a)
        self.assertEqual((sb.turn, list(sb.pending), len(sb.log),
                          len(sb.world.armies), len(sb.kills.snapshot()[0])), snap,
                         "在副本上跑动作**改到了真身** —— 克隆没隔离")

    def test_ledgers_are_independent(self):
        """② 三本账本（厅记忆/击杀/情报）同理 —— 它们最容易被共享而看不出来。"""
        sb = _sb()
        c = sb.clone()
        self.assertIsNot(c.halls, sb.halls)
        self.assertIsNot(c.kills, sb.kills)
        self.assertIsNot(c.intel, sb.intel)
        self.assertIsNot(c.world, sb.world)


if __name__ == "__main__":
    unittest.main()
