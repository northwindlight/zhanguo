# -*- coding: utf-8 -*-
"""潜槽**写后归一化**的守卫 —— 2026-09-28（用户拍板「2」，PLAN §12.28）。

★ 病灶：`mem_out = s_in + gate * upd` 是**纯累加** —— 门只缩放增量
  （`sigmoid ∈ 0~1`），**从不从 `s_in` 里减掉任何东西** ⇒ N 步后是
  `mem_0 + Σ gate_t·upd_t` ⇒ **无界**，而且是**正反馈**
  （`upd` 由 `mem_write(Q=槽, K/V=观测)` 算出 ⇒ 槽越大 upd 越大）。
  实测（iter 385 的档、3686 步）：槽从 `mem0` 的 0.02 涨到 **~1e4**（涨 621×、
  **95.8% 的步在涨**）。两个后果：
    ① `mem_emb`（"这是第几个槽"的标记，零初始化、自己长到 ~0.03 —— **正好是
       `mem0` 的尺度**）被**淹没 ~1e5×** ⇒ **8 个槽在主干眼里长得一模一样**；
    ② 槽进 Block 后第一件事是 `ln1`（LayerNorm）⇒ 量级本来就被归一化掉
       ⇒ 涨上去的量级**一点信息都没带**，纯属白涨。
  连带后果（用户问的就是它）：`mem_activity` 的 ④a/④b KL 是 **3.5e-9 / −1.2e-9**，
  **比未训练基线（1.6e-6）还小**，换槽后动作 **0/8999** ⇒ 读路径死的。

★★ 这里钉**四件事**：
  ① **槽的 RMS 不再无界**（这是本 bug 本身）—— 且这条**能红**
     （把 `_rms_to` 摘掉就会涨到 1e3 量级，见提交信息里的故意破坏记录）；
  ② `mem0` 的初始化 std 与归一化目标**必须相等**（两个不同的量级 = 主干要同时适配两套尺度）；
  ③ `_rms_to` **只缩放不旋转**（用 LN 会减均值、白丢一个自由度，这是选 RMS 的理由）；
  ④ ★ **指纹比对是双向的** —— 旧档缺新键必须被拦。这条是**这次才修的洞**：
     原来 `if k in fp and fp[k] != v` 只查"旧档里有的键"，所以**新增指纹字段会被静默放行**
     —— 而"能加载、形状全对、跑出来是另一个东西"比形状不符危险得多。
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch                                          # noqa: E402

from rl import train as T                             # noqa: E402
from rl import vocab as V                             # noqa: E402
from rl.model import _rms_to, build_model             # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402

N_FEED = 300          # 喂多少步（要够久才看得出"无界"）


def _net_and_steps(seed=7, size=10):
    torch.manual_seed(seed)
    sb = Sandbox(seed=seed, size=size, t_max=20, n_nations=2, halls_known=True,
                 territory=True, alliances="none").reset()
    nets = {p: build_model(mem_slots=V.M_SLOTS) for p in sb.players}
    for m in nets.values():
        m.eval()
    steps, _ = T.collect_episode(nets, sb, rng=np.random.default_rng(seed))
    return nets[sb.players[0]], steps


class TestSlotIsBounded(unittest.TestCase):

    def test_slot_rms_stays_pinned(self):
        """① 本 bug：连喂 300 步，槽的 RMS 必须钉在 `MEM_SLOT_RMS` 附近。

        ★ 反例（没修之前）是**线性增长**：这些 obs 在循环里复用 ⇒ 增量近似同向
          ⇒ `Σ gate·upd` 一路加下去。实测摘掉 `_rms_to` 后能到 1e3 量级。
        """
        net, steps = _net_and_steps()
        self.assertGreater(len(steps), 4, "用例前提：得有几帧能喂")
        mem, rms = None, []
        with torch.inference_mode():
            for i in range(N_FEED):
                b = T.collate([steps[i % len(steps)].obs])
                _lg, _v, mem = net.forward_state(b, mem)
                rms.append(float(mem.pow(2).mean(-1).sqrt().mean()))
        lo, hi = V.MEM_SLOT_RMS * 0.5, V.MEM_SLOT_RMS * 2.0
        self.assertLess(
            max(rms), hi,
            f"喂 {N_FEED} 步后槽的 RMS 到了 {max(rms):.4g}（应 ≤ {hi:.4g}）—— "
            f"写后归一化没生效 ⇒ 又变成无界累加器了（轨迹尾：{[round(x,4) for x in rms[-4:]]}）")
        self.assertGreater(
            min(rms), lo,
            f"槽的 RMS 掉到 {min(rms):.4g}（应 ≥ {lo:.4g}）—— 归一化把槽压没了？")

    def test_rms_norm_preserves_direction(self):
        """③ 只缩放不旋转 —— 选 RMS 而不是 LayerNorm 就是为了这个（LN 会减均值）。"""
        torch.manual_seed(3)
        s = torch.randn(2, 4, 16)
        out = _rms_to(s, 0.02)
        # ★ **逐槽**算余弦（`dim=-1`）—— 归一化就是按最后一维做的。
        #   我第一版把整张量 `reshape(-1)` 拉平再算 ⇒ 那测的是"全局方向"，
        #   各槽朝向不同 ⇒ 必然 < 1（实测 0.9687）。**是测试写错了，不是代码错。**
        cos = torch.nn.functional.cosine_similarity(s, out, dim=-1)
        self.assertGreater(
            float(cos.min()), 1 - 1e-6,
            f"有槽的方向被转了（逐槽 cos 最小 {float(cos.min()):.8f}）—— 不该改方向")
        self.assertAlmostEqual(
            float(out.pow(2).mean(-1).sqrt().mean()), 0.02, places=6,
            msg="RMS 没钉到目标值")

    def test_rms_norm_survives_all_zero_slot(self):
        """★ 全零槽不能变成 NaN（`rms → 0` ⇒ 靠 `clamp_min` 兜住）。"""
        out = _rms_to(torch.zeros(1, 4, 16), 0.02)
        self.assertTrue(torch.isfinite(out).all(), "全零槽产出了 NaN/inf")
        self.assertEqual(float(out.abs().max()), 0.0)


class TestTheTwoScalesMustAgree(unittest.TestCase):

    def test_mem0_init_matches_the_norm_target(self):
        """② `mem0` 的 std 必须 = 归一化目标，否则第 0 步和之后每一步不在同一尺度。"""
        torch.manual_seed(5)
        net = build_model(mem_slots=V.M_SLOTS)
        got = float(net.mem0.std())
        self.assertAlmostEqual(
            got, V.MEM_SLOT_RMS, delta=V.MEM_SLOT_RMS * 0.15,
            msg=f"`mem0` 的 std 是 {got:.4f}，而归一化目标是 {V.MEM_SLOT_RMS} —— "
                f"两者必须一致（主干不该同时适配两个量级）")


class TestFingerprintIsBidirectional(unittest.TestCase):
    """④ 这次才修的洞：原来只查"旧档里有的键"⇒ **新增指纹字段会被静默放行**。"""

    def test_old_ckpt_missing_the_new_key_is_refused(self):
        old = {k: v for k, v in T._shape_fingerprint(8).items()
               if k != "mem_write_norm"}                    # 模拟旧档（8 键）
        bad = T._fp_mismatch(old, T._shape_fingerprint(8))
        self.assertIn("mem_write_norm", bad,
                      "旧档缺 `mem_write_norm` 却没被拦 ⇒ 语义变了还能**静默**续跑")

    def test_identical_fingerprints_pass(self):
        """非空泛性：同一个指纹不能自己跟自己不匹配。"""
        now = T._shape_fingerprint(8)
        self.assertEqual(T._fp_mismatch(now, dict(now)), {},
                         "同一个指纹被判为不匹配 ⇒ 这条守卫会把所有档都拒了")

    def test_memory_off_and_on_are_different(self):
        """开记忆与不开记忆的档必须互不兼容（既有语义，别被我改坏）。"""
        bad = T._fp_mismatch(T._shape_fingerprint(0), T._shape_fingerprint(8))
        self.assertIn("mem_slots", bad, "`mem_slots` 没进指纹比对")

    def test_a_changed_value_is_caught(self):
        """非空泛性：值变了要抓得住（不只是"缺键"这一种）。"""
        now = T._shape_fingerprint(8)
        bad = {**now, "glob_size": now["glob_size"] + 1}
        self.assertIn("glob_size", T._fp_mismatch(bad, now))


class TestGateSpectrum(unittest.TestCase):
    """★ 2026-09-28 第二刀：**"忘得多快"不拍一个数，给一梯队**（用户问出来的）。

    单一标量偏置定死了遗忘速度，而两边都会坏：定太长 ⇒ 门进饱和区
    （`∂/∂bias ∝ gate(1-gate) → 0` ⇒ 永远开不了）；定太短 ⇒ 记忆跨不过一个回合。
    ⇒ 8 个槽各一个初值偏置，铺成**时间尺度梯队**，让训练挑。
    """

    @staticmethod
    def _half_life(bias: float) -> float:
        """`h = ln2 / −ln(1−gate)`，`gate = sigmoid(bias)`。"""
        g = 1.0 / (1.0 + math.exp(-bias))
        return math.log(0.5) / math.log(1.0 - g)

    def test_bias_is_per_slot(self):
        net = build_model(mem_slots=V.M_SLOTS)
        self.assertEqual(tuple(net.mem_gate_bias.shape), (1, V.M_SLOTS, 1),
                         "门偏置不是每槽一个 ⇒ 8 个槽被钉在同一个遗忘速度上")

    def test_later_slots_forget_slower(self):
        """★★ **符号**：bias 越负 ⇒ gate 越小 ⇒ 忘得越慢。

        ★ 这条是拿血换的：我第一版写成 `+ STEP*i` ⇒ 槽 7 跑到 **+2.7**
          （gate 0.94，每步几乎忘光），**和文档里那张表正好反了**。
          是"把表打出来对一遍"当场抓到的 ⇒ 所以这里钉**单调性 + 首尾两个具体值**，
          只钉单调性是不够的（整体平移或反号都能骗过宽松的断言）。
        """
        b = build_model(mem_slots=V.M_SLOTS).mem_gate_bias.detach().flatten().tolist()
        for i in range(len(b) - 1):
            self.assertGreater(b[i], b[i + 1],
                               f"槽 {i}→{i+1} 的 bias 没有变得更负（{b[i]:.2f}→{b[i+1]:.2f}）"
                               f" ⇒ 后面的槽没有忘得更慢，梯队是反的")
        h0, h7 = self._half_life(b[0]), self._half_life(b[-1])
        self.assertAlmostEqual(h0, 3.4, delta=0.4,
                               msg=f"槽 0 的半衰期 {h0:.1f} 步，文档写的是 3.4")
        self.assertAlmostEqual(h7, 207.5, delta=15.0,
                               msg=f"槽 7 的半衰期 {h7:.1f} 步，文档写的是 207.5 —— "
                                   f"首尾差一个数量级才是「梯队」，这条最能抓反号")

    def test_the_spectrum_spans_a_usable_range_in_turns(self):
        """换算成**回合**才有意义（实测一局 ≈908 步/方、≈38 回合 ⇒ **24 步/回合**）。"""
        TURN = 24.0
        b = build_model(mem_slots=V.M_SLOTS).mem_gate_bias.detach().flatten().tolist()
        lo, hi = self._half_life(b[0]) / TURN, self._half_life(b[-1]) / TURN
        self.assertLess(lo, 0.5, f"最快的槽半衰期 {lo:.2f} 回合 —— 连回合内的工作记忆都撑不住")
        self.assertGreater(hi, 3.0, f"最慢的槽半衰期 {hi:.2f} 回合 —— 跨不了几个回合，"
                                    f"记忆就没有意义了（旧值 bias=-1.0 只有 0.09 回合）")

    def test_gradient_reaches_every_slot_bias(self):
        """★ 非空泛性：偏置得真的能学（每槽都拿得到梯度，不是死的）。"""
        net = build_model(mem_slots=V.M_SLOTS)
        x = torch.randn(2, V.M_SLOTS, 2 * net.d_model)
        (net.mem_gate(x) + net.mem_gate_bias).sum().backward()
        g = net.mem_gate_bias.grad
        self.assertIsNotNone(g, "门偏置没拿到梯度 ⇒ 梯队是死的")
        self.assertTrue(bool((g.abs().flatten() > 0).all()),
                        f"有槽的偏置梯度恒为 0（{g.abs().flatten().tolist()}）")

    def test_gate_is_not_saturated_at_init(self):
        """起点不许落进饱和区（`gate(1-gate) → 0` ⇒ 门永远开不了）。"""
        b = build_model(mem_slots=V.M_SLOTS).mem_gate_bias.detach().flatten().tolist()
        for i, bi in enumerate(b):
            g = 1.0 / (1.0 + math.exp(-bi))
            self.assertLess(g, 0.5, f"槽 {i} 的 gate 初值 {g:.3f} ≥0.5 —— 一上来就忘得比记得多")
            self.assertGreater(g * (1 - g), 1e-4,
                               f"槽 {i} 的 gate 初值 {g:.5f} 太靠饱和 ⇒ 梯度 ~{g*(1-g):.2e}，"
                               f"这个槽基本学不动了")
