# -*- coding: utf-8 -*-
"""P4 主干 `rl/transformer.py` 的骨架测试。

守两类**静默**错误：

1. **padding 泄漏**：窗口的 padding 位置若不挡在注意力外，补出来的零向量会以
   "内容"的身份参与 softmax —— 不报错、loss 照样降，只是学的东西不是你以为的。
   这和 `test_window_head.py` 守的是同一件事，但机制不同（那边是池化，这边是注意力）。
2. **候选侧的信息不许来自网格**：P4 里 CNN 退场，候选只带自己的相对落点。
   如果哪天有人把 `tile_idx` 又接回来，候选就会偷偷看到整幅地形，
   而我们在 P5 会以为是"注意力学得好"。

参数规模只做**量级**断言（别写死具体数字）：3M 是 `TOKEN_DESIGN` §6 的预算锚点，
数量级跑偏说明某处的维度写错了。
"""
import unittest

import numpy as np
import torch

from rl.env import KINDS, ZhanguoEnv
from rl.ppo import collate, collate_window
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer


def _pair(turns: int = 30, seed: int = 0):
    """跑一局规则 AI → `(env, obs)`。窗口必须用**它自己那个 env** 去 tokenize
    （锚点/外接框/军队表都在 env 上），拿错 env 会得到一份静默错误的窗口。"""
    from rl.bc import collect_episode, get_teacher
    env = ZhanguoEnv(map_size=16, max_turns=turns)
    collect_episode(env, turns, seed=seed, teacher_fn=get_teacher("v9", turns))
    return env, env._obs()


def _batch(pairs):
    """`[(env, obs), ...]` → `(cand, cand_mask, window_batch)`，批大小 = 条数。"""
    steps = [{"grid": o.grid, "glob": o.glob, "cand": o.cand, "act": 0, "logp": 0.0,
              "val": 0.0, "rew": 0.0, "done": False} for _e, o in pairs]
    _grid, _glob, cand, mask = collate(steps, 0)
    return cand, mask, collate_window([tokenize(e, o) for e, o in pairs])


def _net(win, **kw):
    net = WindowTransformer({g: win.feats[g].shape[1] for g in GROUPS}, **kw)
    net.set_sub_sizes([1] * len(KINDS))
    return net


class TestShapes(unittest.TestCase):
    def setUp(self):
        self.env, self.obs = _pair()
        self.cand, self.cmask, self.wb = _batch([(self.env, self.obs)])
        torch.manual_seed(0)
        self.net = _net(tokenize(self.env, self.obs), d_model=32, n_layer=2, n_head=2)

    def test_输出形状(self):
        logits, v = self.net(self.wb, self.cand, self.cmask)
        b, k = self.cand["type_idx"].shape
        self.assertEqual(logits.shape, (b, k))
        self.assertEqual(v.shape, (b,))

    def test_被mask的候选拿不到分(self):
        logits, _v = self.net(self.wb, self.cand, self.cmask)
        off = ~self.cmask
        if off.any():
            self.assertTrue(bool((logits[off] <= -1e8).all()),
                            "补齐出来的候选必须被打到 -1e9，否则会进 softmax")

    def test_参数规模在预算量级(self):
        net = _net(tokenize(self.env, self.obs), d_model=192, n_layer=4, n_head=4)
        n = net.n_params()
        self.assertGreater(n, 1_000_000, f"参数太少（{n:,}），维度大概写错了")
        self.assertLess(n, 12_000_000, f"参数太多（{n:,}），超了 ECS 3.3GiB 的账")

    def test_梯度能回到窗口编码器(self):
        logits, v = self.net(self.wb, self.cand, self.cmask)
        (logits.sum() + v.sum()).backward()
        g = self.net.proj["a"].weight.grad
        self.assertIsNotNone(g)
        self.assertGreater(float(g.abs().sum()), 0.0)


class TestPaddingLeak(unittest.TestCase):
    """padding 位置的改动**必须**对输出零影响。"""

    def setUp(self):
        # 短局（军队少）+ 长局（军队多）→ 拼批时短的那条会被补一大截
        self.p_short, self.p_long = _pair(turns=6, seed=3), _pair(turns=30, seed=0)
        self.cand, self.cmask, self.wb = _batch([self.p_short, self.p_long])
        torch.manual_seed(0)

    def test_补出来的token不影响输出(self):
        wb = self.wb
        net = _net(tokenize(*self.p_short), d_model=32, n_layer=2, n_head=2)
        with torch.no_grad():
            a = net(wb, self.cand, self.cmask)
            # 只动第 0 条（短窗口）里**被 mask 掉**的位置
            for g in GROUPS:
                m = wb["mask"][g][0]
                if (~m).any():
                    wb["feats"][g][0, ~m] = 1e3
            b = net(wb, self.cand, self.cmask)
        torch.testing.assert_close(a[0], b[0], rtol=0, atol=0,
                                   msg="窗口 padding 泄漏进了注意力")
        torch.testing.assert_close(a[1], b[1], rtol=0, atol=0,
                                   msg="窗口 padding 泄漏进了价值头")

    def test_整行全灭不产生NaN(self):
        wb = collate_window([tokenize(*self.p_short)])
        for g in GROUPS:
            wb["mask"][g][:] = False
        cand, cmask, _ = _batch([self.p_short])
        net = _net(tokenize(*self.p_short), d_model=32, n_layer=2, n_head=2)
        out = net(wb, cand, cmask)
        self.assertFalse(bool(torch.isnan(out[0]).any()), "窗口全灭时出了 NaN")


class TestCandidateSide(unittest.TestCase):
    """候选侧不许碰网格。"""

    def setUp(self):
        self.env, self.obs = _pair()
        self.cand, self.cmask, self.wb = _batch([(self.env, self.obs)])
        torch.manual_seed(0)
        self.net = _net(tokenize(self.env, self.obs), d_model=32, n_layer=2, n_head=2)

    def test_候选侧看不到tile_idx(self):
        """把 `tile_idx` 改烂，输出必须不变 —— P4 的候选只带自己的相对落点。"""
        with torch.no_grad():
            a = self.net(self.wb, self.cand, self.cmask)
            self.cand["tile_idx"] = torch.randint(0, 9999, self.cand["tile_idx"].shape)
            b = self.net(self.wb, self.cand, self.cmask)
        torch.testing.assert_close(a[0], b[0], rtol=0, atol=0)

    def test_无落点的候选与有落点的可区分(self):
        """`tile_dx = -1` 是 buy/sell/end_turn。补出来的位置也是 -1，
        所以"没有落点"这一位必须显式进特征，不能靠 -1 隐式表达。"""
        cand = {k: (v.clone() if torch.is_tensor(v) else v) for k, v in self.cand.items()}
        cand["tile_dx"][0, 0] = -1
        cand["tile_dy"][0, 0] = -1
        with torch.no_grad():
            v1 = self.net(self.wb, cand, self.cmask)[0][0, 0]
            cand["tile_dx"][0, 0] = 3
            cand["tile_dy"][0, 0] = 5
            v2 = self.net(self.wb, cand, self.cmask)[0][0, 0]
        self.assertNotAlmostEqual(float(v1), float(v2), places=6)

    def test_拼批不改单条结果(self):
        """一条候选序列单独跑 vs 跟别的拼批跑，logits 必须逐位一致
        （窗口的 padding 挡住之后，批内别的样本不该影响它）。"""
        p2 = _pair(turns=30, seed=5)
        net = _net(tokenize(self.env, self.obs), d_model=32, n_layer=2, n_head=2)
        with torch.no_grad():
            solo = net(self.wb, self.cand, self.cmask)[0]
            cand2, cmask2, wb2 = _batch([(self.env, self.obs), p2])
            # 第二条样本的候选数不同，所以只比第一条自己的 K 列
            k = self.cand["type_idx"].shape[1]
            pair = net(wb2, cand2, cmask2)[0][0, :k]
        torch.testing.assert_close(solo[0, :k], pair, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
