# -*- coding: utf-8 -*-
"""Adam **必须跨 iter 常驻** —— 2026-09-28 那一刀的守卫。

★ 病灶：两处 PPO 更新里都是就地 `Adam(net.parameters(), lr=lr)`，而
  `ppo_update` **每个 iter 每个成员只调一次** ⇒ **动量每迭代清零**。
  Adam 起步由偏差修正主导（`m/sqrt(v) ≈ sign(g)`）⇒ 每步都是
  「固定大小 ≈lr、方向由当次噪声梯度决定」⇒ 更新互相抵消。
  ★ 实测对得上：一次调用挪 `‖Δθ‖=2.33`，**265 次净挪只有 1.88**
    （随机方向本该 38、方向一致本该 617），`cos(两半数据)=0.20`、
    同一份数据重跑也只有 0.33 ⇒ 方向被噪声主导。

★ 这里钉**两件事**：
  ① 优化器**是同一个对象**（不是每次新建）；
  ② ★ **`step` 计数跨调用累积** —— 这才是"动量真的留下来了"的直接证据。
     只钉 ①（对象相同）是不够的：把 state 清掉也满足 ①。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch                                          # noqa: E402

from rl import train as T                             # noqa: E402
from rl.model import build_model                      # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402


def _net_and_steps(seed=21, size=10):
    torch.manual_seed(seed)
    sb = Sandbox(seed=seed, size=size, t_max=20, n_nations=2, halls_known=True,
                 territory=True, alliances="random2v2").reset()
    nets = {p: build_model(mem_slots=8) for p in sb.players}
    for m in nets.values():
        m.eval()
    steps, _ = T.collect_episode(nets, sb, rng=np.random.default_rng(seed))
    net = nets["甲"]
    net.train()
    return net, steps


def _max_step(opt) -> int:
    """所有参数里最大的 Adam 步数 —— ★ 不能只看 `next(net.parameters())`：
    第一个参数可能压根没梯度（也就没有 state）。"""
    best = 0
    for st in opt.state.values():
        v = st.get("step", 0)
        try:
            best = max(best, int(v))
        except TypeError:                     # 新版 torch 的 step 可能是张量
            best = max(best, int(v.item()))
    return best


def _steps_of(net, steps):
    """只喂这个网络的步（和训练里 `buf[成员]` 同形）。"""
    me = "甲"
    return [s for s in steps if s.player == me] or steps


class TestAdamPersistsAcrossUpdates(unittest.TestCase):

    def test_same_optimizer_object_and_step_counter_accumulates(self):
        net, steps = _net_and_steps()
        ss = _steps_of(net, steps)
        self.assertGreater(len(ss), 8, "用例太小")

        T.ppo_update(net, ss, epochs=1, lr=3e-4)
        opt1 = getattr(net, "_ppo_opt", None)
        self.assertIsNotNone(opt1, "`ppo_update` 没有把优化器挂到网上")
        step1 = _max_step(opt1)
        self.assertGreater(step1, 0, "第一次调用后 Adam 的 step 还是 0 —— 根本没步进")

        T.ppo_update(net, ss, epochs=1, lr=3e-4)
        opt2 = getattr(net, "_ppo_opt", None)
        self.assertIs(opt2, opt1,
                      "第二次调用换了一个新的 Adam ⇒ **动量又清零了**（这就是本来的病灶）")
        step2 = _max_step(opt2)
        self.assertGreater(step2, step1,
                           f"step 没累积（{step1} → {step2}）⇒ 动量没留下来")

    def test_lr_follows_the_caller(self):
        """★ 反向：`lr` 必须每次同步 —— 否则改了 `--lr` 会被旧值静默吃掉。"""
        net, steps = _net_and_steps(seed=22)
        ss = _steps_of(net, steps)
        T.ppo_update(net, ss, epochs=1, lr=3e-4)
        T.ppo_update(net, ss, epochs=1, lr=1e-3)
        got = net._ppo_opt.param_groups[0]["lr"]
        self.assertAlmostEqual(got, 1e-3, places=12,
                               msg=f"lr 没跟着走（还是 {got}）⇒ 改 --lr 会被静默忽略")

    def test_two_nets_do_not_share_an_optimizer(self):
        """★ 反向：优化器挂在**网对象**上 ⇒ 两张网必须各有一份。"""
        n1, s1 = _net_and_steps(seed=23)
        n2, s2 = _net_and_steps(seed=24)
        T.ppo_update(n1, _steps_of(n1, s1), epochs=1, lr=3e-4)
        self.assertIsNone(getattr(n2, "_ppo_opt", None),
                          "没训过的网不该有优化器")
        T.ppo_update(n2, _steps_of(n2, s2), epochs=1, lr=3e-4)
        self.assertIsNot(n1._ppo_opt, n2._ppo_opt, "两张网共用了同一个优化器")
