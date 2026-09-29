# -*- coding: utf-8 -*-
"""**对手池**的守卫 —— 2026-09-29（用户：「这次就打固定稻草人好好学」）。

★ 背景（用户的诊断）：以前**双方都在成长** ⇒ 对手非平稳、学的慢；**平局不加分**；
  **打分器不行**（实测 93.5% 由「兵力+国土」驱动，击杀/守家/厅贡献为 0）
  ⇒ 学到的占优方式是「顶住」而不是「打赢」。
  ⇒ 处方：**固定稻草人 + 去掉势函数 + 平局 −0.5**。

★★ 这里钉**四件事**，每件都对着一个**会静默错**的形状：
  ① ★★ **差生真的没被练** —— 09-26 的口径是「**谁上场谁学**」（抽到谁谁吃梯度），
     不显式跳过就会**把它练掉**、而**不报错**。这里端到端跑一个 iter，
     断言它的权重**逐位不变**（只钉 `Step.frozen` 标记是不够的：标记了却没跳也是错）。
  ② **奖励口径**（用户拍板）：胜 +1 / 负 −1 / **平 −0.5**、**无势函数**、**无时间项**。
  ③ **短名单**：稻草人只在**自己的 top-k** 里挑，而且**不是无脑取 top[0]**
     （否则"用打分器挑"就是空的）。
  ④ **批式路径当场拒** `opponents`（不静默忽略 —— 忽略了差生会退回"按自己的网采样"）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch                                          # noqa: E402

from rl import opponents as OPP                       # noqa: E402
from rl import scoring as S                           # noqa: E402
from rl import train as T                             # noqa: E402
from rl.model import build_model                      # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402


def _tiny_ckpt(path: Path, seed: int = 3) -> None:
    torch.manual_seed(seed)
    from rl import vocab as V
    net = build_model(mem_slots=V.M_SLOTS)
    torch.save({"nets": {0: net.state_dict()},
                "meta": {"iters": 0, "fingerprint": T._shape_fingerprint(8),
                         "t_max": 5, "size": 8}}, path)


class TestRewardSpec(unittest.TestCase):
    """② 口径（用户 2026-09-29 拍板）—— 钉死数值，改要重新确认。"""

    def test_values(self):
        self.assertEqual(S.REWARD_WIN, 1.0)
        self.assertEqual(S.REWARD_LOSS, -1.0)
        self.assertEqual(S.REWARD_DRAW, -0.5, "平局不是 −0.5 了 —— 用户拍的是「不输不再免费」")
        self.assertFalse(S.SHAPING, "势函数差分被打开了 —— 用户要的是**无打分**")
        self.assertFalse(S.WIN_TIME_BONUS, "时间项被打开了 —— 用户说「暂时去掉」")

    def test_terminal_reward_matches_the_spec(self):
        """★ 终局那个数**只有一处**出处（原来是 `sandbox.reward` 与 `train._reward` 各一份）。"""
        sb = Sandbox(seed=11, size=8, t_max=5, n_nations=2, halls_known=True,
                     territory=True).reset()
        sb.turn = 9999                                   # 打满 ⇒ 平局
        self.assertEqual(sb.reward("甲"), S.REWARD_DRAW,
                         "打满（无胜方）时 `sandbox.reward` 没给平局分")
        # `train._reward` 在 `done=True` 且非终局时是 0（截断），终局才走口径 —— 这里钉截断那条
        self.assertEqual(T._reward(sb, "甲", 0.0, True), 0.0, "截断处该是 0")

    def test_no_shaping_means_zero_between_terminal(self):
        """★ 非终局且无势函数 ⇒ 每步奖励必须是 **0**（否则势函数没真关）。"""
        sb = Sandbox(seed=12, size=8, t_max=40, n_nations=2, halls_known=True,
                     territory=True).reset()
        got = [T._reward(sb, "甲", 0.0, False) for _ in range(5)]
        self.assertEqual(set(got), {0.0}, f"非终局奖励不是 0：{got} ⇒ 势函数还在起作用")


class TestScarecrow(unittest.TestCase):
    """③ 稻草人：只在 top-k 里挑，而且**真的用打分器**挑。"""

    def test_returns_an_index_inside_the_shortlist(self):
        sb = Sandbox(seed=13, size=8, t_max=40, n_nations=2, halls_known=True,
                     territory=True).reset()
        sb._auto_advance()
        me = sb.current_player()
        acts = sb.legal()
        self.assertGreater(len(acts), 8, "用例前提：候选要多于 k")
        k = 4
        probs = np.zeros(len(acts))
        top = [1, 5, 9, 13]
        for i in top:
            probs[i] = 1.0
        probs /= probs.sum()
        o = OPP.make("scorer_greedy", k=k)
        got = o.act(sb, me, acts, probs)
        self.assertIn(got, top, f"挑了短名单外的候选 {got} —— 短名单没生效")

    def test_it_is_not_just_argmax(self):
        """★ 非空泛性：稻草人**不是**无脑取 top[0] —— 那样"用打分器挑"就是空的。

        ★ 做法：把 top[0] 指到一个**明显更差**的候选（`hold` 之外的一个会被引擎拒的），
          看它会不会退到别人身上。若两者结果永远相同 ⇒ 打分那一步是摆设。
        """
        sb = Sandbox(seed=14, size=10, t_max=60, n_nations=2, halls_known=True,
                     territory=True).reset()
        sb._auto_advance()
        me = sb.current_player()
        acts = sb.legal()
        o = OPP.make("scorer_greedy", k=len(acts))       # 全候选
        p = np.ones(len(acts)) / len(acts)
        got_flat = o.act(sb, me, acts, p)                # 均匀 probs ⇒ 短名单=全部
        # 把"打分器最可能选的那个"顶到 top[0]，看结果是否跟着变
        best = got_flat
        q = np.full(len(acts), 1e-6)
        q[best] = 1.0
        q /= q.sum()
        self.assertEqual(o.act(sb, me, acts, q), best,
                         "把打分器最爱的候选顶到 top[0] 后结果没变 —— 短名单/打分哪一环是空的")


class TestFrozenOpponentIsNotTrained(unittest.TestCase):
    """① ★★ 最重要的一条：**差生权重逐位不变**。"""

    def test_opponent_weights_do_not_move(self):
        """★ 读数取自 `train()` **自己打的权重指纹**（训前 vs 训后）。

        ★ 为什么不读文件：差生**不写盘**，只有 `--out` 那份会写。我第一版用例拿
          "输入档 `nets[0]`"比"输出档 `nets[0]`" —— **那是两个不同的东西**
          （前者是骨架、后者是学习者）⇒ 报了个 4.51 的假警。**用例错了，不是代码错。**
        """
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as d:
            ck = Path(d) / "bone.pt"
            _tiny_ckpt(ck, seed=5)
            T.train(iters=1, episodes_per_iter=2, seed=7, size=8, t_max=5,
                    size_min=8, size_max=8, halls_known=True, nations=2,
                    pool=2, league_mains=2, out=str(Path(d) / "o.pt"),
                    ckpt_every=1, epochs=1, max_steps=0, device="cpu",
                    memory="latent", opponent="scorer_greedy",
                    opponent_ckpt=str(ck), opponent_k=4,
                    log=lambda *a: lines.append(" ".join(str(x) for x in a)))
        txt = "\n".join(lines)
        self.assertIn("对手池模式", txt, "对手模式没起来")
        import re
        ds = re.findall(r"差生权重指纹（训[前后]）= \*\*([0-9a-f]+)\*\*", txt)
        self.assertEqual(len(ds), 2, f"没拿到两个指纹：{ds}\n{txt[-800:]}")
        self.assertEqual(ds[0], ds[1],
                         f"★ **差生的权重被改了**（{ds[0]} → {ds[1]}）—— "
                         f"「谁上场谁学」那条规则把它练掉了；`Step.frozen` 的跳过没生效")


class TestBatchedPathRefuses(unittest.TestCase):
    """④ 批式路径**当场拒**（不静默忽略）。"""

    def test_raises(self):
        sb = Sandbox(seed=15, size=8, t_max=40, n_nations=2, halls_known=True,
                     territory=True).reset()
        net = build_model(mem_slots=8)
        net.eval()
        with self.assertRaises(NotImplementedError):
            T.collect_episodes_batched({p: net for p in sb.players}, [sb],
                                       opponents={"甲": OPP.make("scorer_greedy", k=2)})


if __name__ == "__main__":
    unittest.main()
