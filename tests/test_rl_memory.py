# -*- coding: utf-8 -*-
"""**潜槽（`--memory latent`）**的守卫 —— 用户 2026-09-25 的隐空间设计。

设计摘要（逐条对着一个会静默学错的形状）：

  · M 个潜槽当**一组新 token** 挂进窗口 —— **读走现有注意力**（不变主干）；
  · 写走**门控 cross-attention**；★ **初值下 `upd ≡ 0`** ⇒ 槽跨步**一动不动**
    ⇒「开了记忆的第一版」**信息上等于马尔可夫基线** ⇒ 有**干净的对照**；
  · 辅助损失：从槽预测「**下一帧即将离开视野的那部分**」（`rl/mem_aux.py`）；
  · TBPTT 短窗 + **窗口边界 stop-grad**（不许跨窗回传，更不许跨局）；
  · `--memory none` ⇒ **一个参数都不多建**（老档照样读，老行为逐字不变）。

★ 钉七件事，每条都对着"**不报错但学错**"：

  ① **不开记忆 = 一个字都不变**：`mem_slots=0` 时不许多出任何参数
     （否则老 ckpt 一个都读不了，而 `load_state_dict` 可能**静默**只灌一部分）。
  ② ★★ **初值槽跨步精确不动**（`mem_out == mem_in`）—— 这条是"对照干净"的**全部**内容。
     ⚠ 我第一版只把门偏置压到 -3，以为"几乎不写"：**实测槽每步仍变 4.1e-02**，
     而 `mem0` 的标准差才 0.02 ⇒ 那个说法**不成立**，而且是个**近似**、钉不住。
     改成**把写头输出层零初始化** ⇒ `upd` 恒为 0、等式成立。
  ③ **读路径是活的**：换一组槽 ⇒ **logits 必须变**（槽真的被注意力读走了）。
     这条挡的是"槽挂进去了但没人读"（那会让整个设计变成装饰品）。
  ④ **梯度传得进写头**（零初始化不许把学习掐死）：辅助损失对写头权重的梯度 ≠ 0。
  ⑤ ★★ **窗口不跨局**（`done` 处强制断开）—— 跨了就是把两局焊成一局、记忆跨局泄漏，
     **loss 照降**。
  ⑥ ★ **记忆真的学得起来**：拿真的一局跑几十步优化，辅助损失要**降**、
     槽要**开始动**。★ 这条最值钱：它挡的是"整套机制接好了、但梯度其实进不去"
     （零初始化写头 + 门 + 链式槽，任何一处接错都会变成"跑得动但学不到"）。
  ⑦ **形状指纹**：带记忆与不带记忆的档**互不相容**，且拒绝的理由要**干净**
     （`SystemExit`），不能是 `load_state_dict` 的形状异常 —— 那看不懂。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                    # noqa: E402
import torch                                          # noqa: E402

from rl import encode as E                            # noqa: E402
from rl import scoring as S                           # noqa: E402
from rl import train as T                             # noqa: E402
from rl import vocab as V                             # noqa: E402
from rl.model import build_model                      # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402

# ★ 无记忆模型的参数量**黄金值**。它变了 ⇒ 主干被动了 ⇒ 老 ckpt 全废。
#   （这个数不是"规格"，是**看门狗**：故意改主干时请一并更新。）
#   ★ 它已经**正确地响过一次**：加情报那 12 列时 1385586 → 1387506，
#     差额 1920 = 12 × 160（`g` 组的投影多 12 个输入）+ 160（偏置）——
#     也就是"**观测宽度变了**"这件事被它当场抓住（那正是老 ckpt 全废的时刻）。
PARAMS_NO_MEM = 1387506


def _sb(seed=7, size=10, n=3, t_max=60):
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=t_max,
                   halls_known=True).reset()


def _batch(sb, me):
    return T.collate([E.obs_of(sb, me)])


class TestNoMemoryIsUntouched(unittest.TestCase):
    """① 不开记忆 ⇒ 与原来逐字相同。"""

    def test_no_extra_parameters(self):
        net = build_model()
        self.assertEqual(net.mem_slots, 0)
        self.assertEqual(net.n_params(), PARAMS_NO_MEM,
                         "无记忆模型的参数量变了 ⇒ 主干被动了（老 ckpt 全废）")
        self.assertFalse([k for k in net.state_dict() if k.startswith("mem")],
                         "不开记忆却建了记忆相关的参数 ⇒ 老档读不了")

    def test_forward_signature_is_unchanged(self):
        """★ 老调用点（`collect`/`ppo_update`/`eval_fixed`）拿到的是**两元组**。"""
        sb = _sb()
        net = build_model()
        out = net(_batch(sb, sb.players[0]))
        self.assertEqual(len(out), 2, "`forward` 的返回元数变了 ⇒ 所有老调用点会炸")
        logits, value = out
        self.assertEqual(logits.shape[0], 1)
        self.assertEqual(value.shape, (1,))

    def test_forward_state_reports_none(self):
        net = build_model()
        _, _, mem = net.forward_state(_batch(_sb(), "甲"), None)
        self.assertIsNone(mem, "不开记忆时 `mem_out` 必须是 None")


class TestInitialSlotsAreInformationallyEmpty(unittest.TestCase):
    """② 初值：槽跨步**精确**不动 ⇒ 信息上等于马尔可夫基线。"""

    def test_slots_do_not_move_at_init(self):
        sb = _sb()
        net = build_model(mem_slots=V.M_SLOTS)
        net.eval()
        batch = _batch(sb, sb.players[0])
        with torch.no_grad():
            _, _, m0 = net.forward_state(batch, None)
            m = m0
            for _ in range(30):                     # 连着走 30 步
                _, _, m = net.forward_state(batch, m)
        self.assertEqual(float((m - m0).abs().max()), 0.0,
                         "初值时槽自己动了 ⇒ 记忆路径**信息上不是空的** ⇒ "
                         "「开记忆的第一版 = 马尔可夫基线」这个对照不成立")

    def test_init_slots_do_not_depend_on_the_episode(self):
        """★ 换一局（不同 seed）⇒ 初值槽**一模一样**（它本来就是学出来的常量）。"""
        a = build_model(mem_slots=V.M_SLOTS)
        b = build_model(mem_slots=V.M_SLOTS)
        b.load_state_dict(a.state_dict())
        a.eval(), b.eval()
        sa = _sb(seed=1)
        sb_ = _sb(seed=2)
        with torch.no_grad():
            _, _, ma = a.forward_state(_batch(sa, sa.players[0]), None)
            _, _, mb = b.forward_state(_batch(sb_, sb_.players[0]), None)
        self.assertEqual(float((ma - mb).abs().max()), 0.0, "初值槽跟着局面变了")


class TestReadPathIsAlive(unittest.TestCase):
    """③ 换槽 ⇒ logits 必须变（槽真的被读走了）。"""

    def test_different_slots_change_the_logits(self):
        sb = _sb()
        net = build_model(mem_slots=V.M_SLOTS)
        net.eval()
        batch = _batch(sb, sb.players[0])
        torch.manual_seed(0)
        m1 = net.mem_init(1)
        m2 = net.mem_init(1) + 0.5 * torch.randn_like(m1)   # ★ 一组**不同**的槽
        with torch.no_grad():
            l1, v1, _ = net.forward_state(batch, m1)
            l2, v2, _ = net.forward_state(batch, m2)
            l3, v3, _ = net.forward_state(batch, torch.zeros_like(m1))
        self.assertGreater(float((l1 - l2).abs().max()), 1e-6,
                           "换了槽 logits 一点没变 ⇒ 槽挂进去了但**没人读**")
        self.assertGreater(float((l1 - l3).abs().max()), 1e-6, "置零槽也没影响")
        self.assertGreater(float((v1 - v2).abs().max()), 1e-6,
                           "估值对槽无感 ⇒ 记忆没进价值头（价值应该反映「我还有一支看不见的军」）")

    def test_forward_without_state_equals_the_initial_state(self):
        """★ `forward(batch)` = 用**初值槽**跑（"不带记忆"的对照臂语义被钉住）。"""
        sb = _sb()
        net = build_model(mem_slots=V.M_SLOTS)
        net.eval()
        batch = _batch(sb, sb.players[0])
        with torch.no_grad():
            l2, v2 = net(batch)
            l1, v1, _ = net.forward_state(batch, None)
        self.assertEqual(float((l1 - l2).abs().max()), 0.0)
        self.assertEqual(float((v1 - v2).abs().max()), 0.0)


class TestGradientReachesTheWriteHead(unittest.TestCase):
    """④ 零初始化**不许**把学习掐死。"""

    def test_aux_loss_has_gradient_on_the_write_head(self):
        """★ 零初始化**只让上游晚一步**，不是掐死学习。

        ★ 我第一版在这里断言"第一步 `mem_write.q` 的梯度 > 0" —— **错了**：
          输出层零初始化 ⇒ `∂upd/∂q ≡ out.weight @ … = 0` ⇒ 注意力**内部**的那些
          权重（q/kv）第一步**本来就该**是 0 梯度。这是该初始化的**标准性质**，
          不是 bug。真正该钉的是：**第一步输出层有梯度**，而**走一步之后**上游也有了。
        """
        sb = _sb()
        net = build_model(mem_slots=V.M_SLOTS)
        net.train()
        batch = _batch(sb, sb.players[0])

        def grads():
            net.zero_grad(set_to_none=True)
            _, _, mem = net.forward_state(batch, None)
            net.mem_aux_loss(mem, torch.ones(1, V.M_AUX)).backward()
            return (float(net.mem_write.out.weight.grad.abs().sum()),
                    float(net.mem_write.q.weight.grad.abs().sum()))

        g_out0, g_q0 = grads()
        self.assertGreater(g_out0, 0.0,
                           "辅助损失对写头**输出层**的梯度是 0 ⇒ 写头永远开不了口")
        self.assertEqual(g_q0, 0.0, "零初始化下上游第一步不该有梯度（性质变了就要重新想）")
        # ★ 走一步（只动输出层）⇒ 上游立刻拿到梯度 ⇒ 学习**没有**被掐死
        opt = torch.optim.SGD(net.parameters(), lr=1.0)
        opt.step()
        _, g_q1 = grads()
        self.assertGreater(g_q1, 0.0,
                           "走一步之后写头内部（q）仍无梯度 ⇒ 学习被零初始化**掐死**了")


class TestWindowsNeverCrossAnEpisode(unittest.TestCase):
    """⑤ 窗口不许跨局。"""

    def test_windows_break_at_done(self):
        """★★ **缓冲区里装着好几局**（一个 iter 的真实形状）。

        ★ 我第一版只收了**一局**（而且是截断的 ⇒ 唯一的 `done` 就在最后一步、
          正好落在末窗末尾）⇒ 把 `done` 断点**删掉**，这条守卫**照样绿** ——
          **假绿**。真实的缓冲区是 `episodes_per_iter` 局拼起来的，`done` 在**中间**
          ⇒ 必须按那个形状造。（这条是"故意破坏一次"抓出来的。）
        """
        nets = {p: build_model(mem_slots=V.M_SLOTS) for p in _sb().players}
        steps: list = []
        # ★★ **每局的步数不许是 `MEM_TBPTT` 的整数倍** —— 我第二版写 24（=8×3），
        #   于是每个 `done` 都**恰好落在窗口边界上**（构造性巧合）⇒ 把断点删掉
        #   守卫**照样绿**。**又是假绿**。用 20 ⇒ 局末落在窗口**中间**。
        for e in range(3):                            # ★ 三局拼一个缓冲区
            sb = _sb(seed=20 + e, t_max=60)
            ln = 20 + e
            self.assertNotEqual(ln % S.MEM_TBPTT, 0,
                                "局末必须落在窗口中间，否则这条守卫测不到东西")
            st, _ = T.collect_episode({p: nets[p] for p in sb.players}, sb,
                                      rng=np.random.default_rng(e), max_steps=ln)
            steps += st
        n_done = sum(1 for st_ in steps[:-1] if st_.done)
        self.assertGreater(n_done, 0,
                           "缓冲区里除了最后一步没有别的局末 ⇒ 这条守卫测不到东西"
                           "（那是**假绿**的形状，见 docstring）")
        wins = T._mem_windows(steps, S.MEM_TBPTT)
        self.assertTrue(all(len(w) <= S.MEM_TBPTT for w in wins), "窗口超长")
        for w in wins:                       # ★ 窗内除了**最后一步**不许有 done
            for i in range(len(w) - 1):
                self.assertFalse(steps[w[i]].done,
                                 "★ 窗口跨过了一局的结束 ⇒ 下一局会接着上一局的槽算"
                                 "（记忆跨局泄漏，而 loss 照降）")
        self.assertEqual([i for w in wins for i in w], list(range(len(steps))),
                         "窗口没有覆盖全部步（有步被丢了 ⇒ 那些步不进梯度）")

    def test_windows_stay_in_order(self):
        """★ 顺序不许打乱（记忆是**有向**的）。"""
        sb = _sb()
        nets = {p: build_model(mem_slots=V.M_SLOTS) for p in sb.players}
        steps, _ = T.collect_episode(nets, sb, rng=np.random.default_rng(0),
                                     max_steps=40)
        wins = T._mem_windows(steps, 8)
        flat = [i for w in wins for i in w]
        self.assertEqual(flat, sorted(flat), "窗口把步序打乱了 ⇒ 槽链是错的")


class TestMemoryActuallyLearns(unittest.TestCase):
    """⑥ ★★ 最值钱的一条：拿真的一局跑几十步优化，辅助损失要降、槽要开始动。"""

    def test_aux_loss_drops_and_slots_start_moving(self):
        sb = _sb(seed=11, size=10, n=3, t_max=60)
        net = build_model(mem_slots=V.M_SLOTS)
        net.train()
        nets = {p: net for p in sb.players}
        steps, _ = T.collect_episode(nets, sb, rng=np.random.default_rng(1),
                                     max_steps=48)
        buf = [s for s in steps if s.player == sb.players[0]]
        self.assertGreater(len(buf), 8, "这一方步数太少，测不出东西")
        self.assertTrue(any(s.aux is not None for s in buf), "一个辅助目标都没有")

        def aux_loss_now():
            """跑一遍链、把辅助项单独量出来（不更新）。"""
            net.eval()
            tot, n = 0.0, 0
            mem = buf[0].mem_in
            with torch.no_grad():
                for st in buf:
                    b = T.collate([st.obs])
                    _, _, mem = net.forward_state(b, mem)
                    if st.aux is not None:
                        tot += float(net.mem_aux_loss(
                            mem, torch.as_tensor(st.aux).unsqueeze(0)))
                        n += 1
            net.train()
            return tot / max(1, n)

        before = aux_loss_now()
        # ★ 不许跨局：`done` 处从**收集时的边界槽**重新起算（与 `_update` 同一条纪律）
        st = T.ppo_update(net, buf, epochs=3, tbptt=S.MEM_TBPTT, minibatch=16)
        self.assertTrue(st, "更新什么都没返回")
        after = aux_loss_now()
        self.assertLess(after, before,
                        f"辅助损失没降（{before:.4f} → {after:.4f}）⇒ "
                        "「把即将离开视野的东西装进槽」这条信号**没接进参数**")
        # ★ 槽开始动了吗（门/写头被推开了）
        net.eval()
        with torch.no_grad():
            _, _, m0 = net.forward_state(T.collate([buf[0].obs]), None)
            m = m0
            for st_ in buf[:12]:
                _, _, m = net.forward_state(T.collate([st_.obs]), m)
        self.assertGreater(float((m - m0).abs().max()), 1e-9,
                           "优化之后槽**仍然一动不动** ⇒ 写头没被推开")


class TestShapeFingerprintSeparatesMemoryModes(unittest.TestCase):
    """⑦ 带记忆与不带记忆的档互不相容，且拒绝要**干净**。"""

    def test_fingerprint_differs(self):
        self.assertNotEqual(T._shape_fingerprint(0), T._shape_fingerprint(V.M_SLOTS),
                            "指纹里没体现 `mem_slots` ⇒ 两种结构的档会互相被接受")
        self.assertEqual(T._shape_fingerprint(0)["mem_slots"], 0)

    def test_resuming_across_modes_is_refused_cleanly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "m.pt")
            nets = {0: build_model(mem_slots=V.M_SLOTS)}
            T._save_ckpt(p, nets, 3, meta=T._ckpt_meta(8, 8, True, 3, 1, 60,
                                                       V.M_SLOTS))
            # ★ 同一套结构 ⇒ 续得上
            T._load_ckpt(p, {0: build_model(mem_slots=V.M_SLOTS)},
                         mem_slots=V.M_SLOTS, log=lambda *_: None)
            # ★ 换开关 ⇒ 必须**干净地拒**（SystemExit），不是 shape 异常
            for ms in (0,):
                with self.assertRaises(SystemExit, msg="指纹没有拦住换开关的档"):
                    T._load_ckpt(p, {0: build_model(mem_slots=ms)},
                                 mem_slots=ms, log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()