# -*- coding: utf-8 -*-
"""批量采集回路（`collect_episodes_batched`）的守卫 —— **两层**，别混。

★ 为什么必须分两层：合批**不可能**逐位相同。
  `collate` 会把网格补到**批内最大**、BLAS 的分块/归约顺序也变了
  ⇒ 输出差最后几个 ulp；那几个 ulp 经**抽样**会放大成不同的动作、整局走向分叉。
  所以：

  **第一层（N=1）逐位相同** —— 钉的是**回路逻辑**（rng 归属 / 记忆搬运 /
  辅目标回填 / 截断处置 / 记账），**不是数值**。N=1 时数值路径与逐局版一致，
  所以这一层能钉到"我有没有把回路搬错"。

  **第二层（N>1）钉对齐** —— `logits[k]` 必须约等于**单独**跑第 k 个沙盒的 logits。
  这条抓的是最阴的一类错：**第 i 个沙盒拿到了第 j 个的输出**（错位）。
  它不会崩、不会报错、loss 照常下降，只是**学的是别人的局面**。

★ 非空泛性：还得证明它**真在批量**（前向调用次数远少于决策点数），
  否则"批量版"退化成 N=1 逐局跑也照样全绿。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl import train as T                             # noqa: E402
    from rl.encode import obs_of                          # noqa: E402
    from rl.model import build_model                      # noqa: E402
    from rl.sandbox import Sandbox                        # noqa: E402
else:
    Sandbox = build_model = obs_of = train = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口（见上面的说明）。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _mk(seed, size=8, t_max=12, mem_slots=8):
    torch.manual_seed(seed)
    sb = Sandbox(seed=seed, size=size, t_max=t_max, n_nations=2, halls_known=True,
                 territory=True, alliances="random2v2").reset()
    nets = {p: build_model(mem_slots=mem_slots) for p in sb.players}
    for n in nets.values():
        n.eval()
    return sb, nets


def _obs_equal(a, b):
    if a.keys() != b.keys():
        return False
    for k in a:
        if isinstance(a[k], dict):
            if not _obs_equal(a[k], b[k]):
                return False
        elif not np.array_equal(np.asarray(a[k]), np.asarray(b[k])):
            return False
    return True


def _step_equal(x, y):
    """逐步比：**标量全比 + 观测逐位 + mem_in 逐位**。"""
    if (x.aidx, x.player, x.done) != (y.aidx, y.player, y.done):
        return f"aidx/player/done 不同: {(x.aidx, x.player, x.done)} vs {(y.aidx, y.player, y.done)}"
    for f in ("logp", "value", "reward", "boot"):
        if getattr(x, f) != getattr(y, f):
            return f"{f} 不同: {getattr(x, f)!r} vs {getattr(y, f)!r}"
    if not _obs_equal(x.obs, y.obs):
        return "观测不同"
    if (x.mem_in is None) != (y.mem_in is None):
        return "mem_in 有无不同"
    if x.mem_in is not None and not torch.equal(x.mem_in, y.mem_in):
        return "mem_in 不同"
    return None


class TestBatchOneIsBitIdentical(unittest.TestCase):
    """**N=1 时必须与 `collect_episode` 逐位相同**（钉回路逻辑）。"""

    def test_single_sandbox_matches_the_plain_loop(self):
        sb1, nets1 = _mk(4101)
        steps_a, info_a = T.collect_episode(nets1, sb1,
                                            rng=np.random.default_rng(7))

        sb2, nets2 = _mk(4101)                       # 同种子 ⇒ 完全一样的局
        stepss, infos = T.collect_episodes_batched(
            nets2, [sb2], rngs=[np.random.default_rng(7)])
        steps_b, info_b = stepss[0], infos[0]

        self.assertGreater(len(steps_a), 5, "用例太小，量不出东西")
        self.assertEqual(len(steps_a), len(steps_b), "步数不同")
        for i, (x, y) in enumerate(zip(steps_a, steps_b)):
            why = _step_equal(x, y)
            self.assertIsNone(why, f"第 {i} 步不同：{why}")
        for k in ("turns", "truncated", "first"):
            self.assertEqual(info_a[k], info_b[k], f"概要 {k} 不同")

    def test_single_sandbox_matches_without_memory(self):
        """★ 潜槽关掉那条分支（`net(batch)` 返回 2 个值）也要逐位相同。"""
        sb1, nets1 = _mk(4102, mem_slots=0)
        steps_a, _ = T.collect_episode(nets1, sb1, rng=np.random.default_rng(3))
        sb2, nets2 = _mk(4102, mem_slots=0)
        stepss, _ = T.collect_episodes_batched(nets2, [sb2],
                                               rngs=[np.random.default_rng(3)])
        self.assertEqual(len(steps_a), len(stepss[0]))
        for i, (x, y) in enumerate(zip(steps_a, stepss[0])):
            self.assertIsNone(_step_equal(x, y), f"第 {i} 步不同（无记忆分支）")


class TestBatchAlignment(unittest.TestCase):
    """★★ N>1：**第 k 行必须拿到第 k 个沙盒的输出**（错位是静默的，必须钉）。"""

    TOL = 1e-6          # ★ 实测：补零泄漏修好后是 7.5e-08~9.7e-08（纯浮点重排）
                        #   ⇒ 1e-6 够宽又够紧；错位是 O(1) 量级，一抓一个准

    def _rows(self, n):
        """n 个**不同 size**的沙盒 + **同一张网**（要测的是批，不是网）。"""
        torch.manual_seed(5299)
        net = build_model(mem_slots=8)
        net.eval()
        out = []
        for i in range(n):
            sb = Sandbox(seed=5200 + i, size=8 + i % 3, t_max=12, n_nations=2,
                         halls_known=True, territory=True,
                         alliances="random2v2").reset()
            me = sb.current_player()
            acts = sb.legal()
            out.append((sb, net, me, obs_of(sb, me, acts)))
        return out

    def test_batched_logits_match_each_row_run_alone(self):
        rows = self._rows(4)
        net = rows[0][1]
        for _sb, nn, _me, _o in rows:
            self.assertIs(nn, net, "用例前提：4 行用的是同一张网")

        mems = [net.mem_init(1, device="cpu") for _ in rows]
        singles = []
        for (sb, _n, me, o), m in zip(rows, mems):
            with torch.inference_mode():
                lg, _v = net(to_dev_obs(o))
            singles.append(lg[0])
        with torch.inference_mode():
            batch = T.to_dev(T.collate([r[3] for r in rows]), "cpu")
            lg_b, _v, _mo = net.forward_state(batch, torch.cat(mems, 0))
        for k in range(len(rows)):
            n = singles[k].shape[0]
            # ★★ **只比有效段**（2026-10-23 修）：批里的候选会**补零到批内最大**
            #   （`collate`），单独跑的那份**不补零** ⇒ 形状本来就不同
            #   （补零那截在模型里被 `masked_fill(-1e9)` 掩掉）。
            #   ★ 这条用例以前"碰巧"能过：那时**每行的候选数都一样**
            #     （`hold + 8 邻` × 军数 ⇒ 45），补零长度恰好相同。
            #   ★ 上一轮修"**野地也能进攻**"之后，候选数**逐行不同**
            #     （攻不攻野地取决于地形与邻居）⇒ 老写法直接形状对不上而红。
            self.assertTrue(
                torch.allclose(lg_b[k][:n], singles[k], rtol=self.TOL, atol=self.TOL),
                f"第 {k} 行与单独跑的结果差太多 —— 批次错位了"
                f"（max|Δ|={(lg_b[k][:n] - singles[k]).abs().max().item():.3g}）")
            if lg_b[k].shape[0] > n:
                # ★ 补零那段必须**被掩掉** ⇒ 不进 softmax、不产生梯度。
                self.assertTrue(bool((lg_b[k][n:] <= -1e8).all()),
                                "补零的候选没有被掩掉 ⇒ 会进 softmax")

    def test_row_permutation_follows_the_rows(self):
        """★ 把行序换一下，输出必须**跟着行走**（不是跟着位置）。

        比上一条更狠：上一条只证明"数值接近"，这条证明**对应关系**是对的。
        """
        rows = self._rows(3)
        net = rows[0][1]
        obs = [r[3] for r in rows]
        mems = [net.mem_init(1, device="cpu") for _ in rows]
        order = [2, 0, 1]
        with torch.inference_mode():
            b1 = T.to_dev(T.collate(obs), "cpu")
            lg1, _v, _m = net.forward_state(b1, torch.cat(mems, 0))
            b2 = T.to_dev(T.collate([obs[i] for i in order]), "cpu")
            lg2, _v, _m = net.forward_state(b2, torch.cat([mems[i] for i in order], 0))
        for pos, i in enumerate(order):
            # ★ 只比**有效段**（候选数逐行不同 ⇒ 两批的补零长度可能不同，
            #   见上面那条用例的注释）。
            n = int(obs[i]["cand"]["type_idx"].shape[0])
            self.assertTrue(
                torch.allclose(lg2[pos][:n], lg1[i][:n], rtol=self.TOL, atol=self.TOL),
                f"换行序后第 {pos} 位没有跟着第 {i} 行走 —— 对应关系错了")


class TestBatchIsActuallyBatching(unittest.TestCase):
    """★ 非空泛性：**它得真在批量**，否则整组守卫都是空的。"""

    def test_forward_calls_far_fewer_than_decision_points(self):
        n = 4
        sbs, netss = [], []
        for i in range(n):
            sb, nets = _mk(6300 + i, size=8, t_max=12)
            sbs.append(sb)
            netss.append(nets)
        # 让 4 个沙盒共用**同一份**网络对象（否则按 net 分组，各批各的）
        nets = netss[0]
        rngs = [np.random.default_rng(i) for i in range(n)]

        calls = {"n": 0}
        real = type(nets["甲"]).forward_state

        def spy(self, batch, mem=None):
            calls["n"] += 1
            return real(self, batch, mem)

        type(nets["甲"]).forward_state = spy
        try:
            stepss, _infos = T.collect_episodes_batched(
                nets, sbs, rngs=rngs, max_steps=40)
        finally:
            type(nets["甲"]).forward_state = real

        total = sum(len(s) for s in stepss)
        self.assertGreater(total, 40, "用例太小，量不出批量")
        # 每轮**最多**每个网络一次前向 ⇒ 调用数应远小于总决策点数
        self.assertLess(calls["n"], total / 2,
                        f"前向调了 {calls['n']} 次 / {total} 个决策点 —— "
                        f"没有在批量（退化成逐局跑了）")


def to_dev_obs(o):
    return T.to_dev(T.collate([o]), "cpu")
