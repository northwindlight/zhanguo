# -*- coding: utf-8 -*-
"""P3：token 窗口 → `WindowEncoder` → 原有的点积头。

守的是**补零不能泄漏**这一类错误。窗口是变长的（军队数逐帧不同），拼批要补；
补出来的位置如果参与了池化，模型就会"看见"一个随批内最长样本而变的假信号——
**不报错、loss 照样降**，只是学的东西不是你以为的那个。

    掩码池化：mask=0 的 token 对输出**零影响**（不是"影响很小"）
"""
import unittest

import numpy as np
import torch

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet, WindowEncoder
from rl.ppo import collate, collate_window
from rl.tokenize import GROUPS, tokenize


def _env_obs(turns: int = 30, seed: int = 0, map_size: int = 16):
    from rl.bc import collect_episode, get_teacher
    env = ZhanguoEnv(map_size=map_size, max_turns=turns)
    collect_episode(env, turns, seed=seed, teacher_fn=get_teacher("v9", turns))
    return env, env._obs()


def _widths(win):
    return {g: win.feats[g].shape[1] for g in GROUPS}


class TestMaskedPooling(unittest.TestCase):
    def setUp(self):
        self.env, self.obs = _env_obs()
        self.win = tokenize(self.env, self.obs)
        torch.manual_seed(0)
        self.enc = WindowEncoder(_widths(self.win), d_enc=8, d_out=16)

    def test_掩码位置改动对输出零影响(self):
        """把**被 mask 掉**的 token 改成任意值，输出必须逐位不变。"""
        wb = collate_window([self.win])
        with torch.no_grad():
            a = self.enc(wb)

            for g in GROUPS:
                m = wb["mask"][g]
                if (~m).any():
                    wb["feats"][g][~m] = 7.5
            b = self.enc(wb)
        torch.testing.assert_close(a, b, rtol=0, atol=0,
                                   msg="被 mask 的 token 泄漏进了池化")

    def test_全灭的组不影响输出(self):
        """外交/事件/记忆/关键节点四组现在恒灭。往里面塞什么都不该改结果 ——
        这是"预留位现在不参与"在数学上的样子。"""
        wb = collate_window([self.win])
        with torch.no_grad():
            a = self.enc(wb)
            for g in ("n", "e", "r", "k"):
                wb["feats"][g] += 123.0
            b = self.enc(wb)
        torch.testing.assert_close(a, b, rtol=0, atol=0)

    def test_全灭不产生NaN(self):
        """分母夹到 1.0 才不会有 0/0。全灭的组恒输出 0 向量。"""
        wb = collate_window([self.win])
        for g in GROUPS:
            wb["mask"][g] = torch.zeros_like(wb["mask"][g])
        out = self.enc(wb)
        self.assertFalse(bool(torch.isnan(out).any()), "全灭时出了 NaN")


class TestBatchInvariance(unittest.TestCase):
    def test_单条与批内逐位一致(self):
        """同一条窗口，单独跑和跟别的样本拼批跑，输出必须完全一样 ——
        拼批补的是 token 数那一维，补出来的位置不许影响结果。"""
        e1, o1 = _env_obs(turns=30, seed=0)
        e2, o2 = _env_obs(turns=30, seed=1)
        w1, w2 = tokenize(e1, o1), tokenize(e2, o2)
        torch.manual_seed(0)
        enc = WindowEncoder(_widths(w1), d_enc=8, d_out=16)
        with torch.no_grad():
            solo = enc(collate_window([w1]))
            pair = enc(collate_window([w1, w2]))
        torch.testing.assert_close(solo[0], pair[0], rtol=0, atol=0)

    def test_窗口数不同的两条都能拼(self):
        e1, o1 = _env_obs(turns=8, seed=0)
        e2, o2 = _env_obs(turns=30, seed=1)
        w1, w2 = tokenize(e1, o1), tokenize(e2, o2)
        n1 = int(w1.mask["a"].sum())
        n2 = int(w2.mask["a"].sum())
        self.assertNotEqual(n1, n2, "这两局的军队数该不同，否则这条测试是空的")
        wb = collate_window([w1, w2])
        self.assertEqual(wb["feats"]["a"].shape[0], 2)
        self.assertEqual(int(wb["mask"]["a"][0].sum()), n1)
        self.assertEqual(int(wb["mask"]["a"][1].sum()), n2)

    def test_宽度不一致会炸(self):
        """`tokenize` 承诺各组宽度是常量。哪天真变了，拼批必须**响亮地**炸，
        不能默默按第一条的宽度截断/补零（那会让整批错位且不报错）。"""
        e1, o1 = _env_obs(turns=8, seed=0)
        w1 = tokenize(e1, o1)
        w1.feats["g"] = np.concatenate(
            [w1.feats["g"], np.zeros((1, 3), np.float32)], axis=1)
        with self.assertRaises(AssertionError):
            collate_window([w1, tokenize(e1, o1)])


class TestPolicyWiring(unittest.TestCase):
    def setUp(self):
        self.env, self.obs = _env_obs()
        self.win = tokenize(self.env, self.obs)

    def _net(self, **kw):
        return PolicyNet(n_grid_ch=len(self.env.obs_channels()),
                         n_glob=self.env.glob_size(),
                         sub_sizes=[len(self.env.sub_tables[k]) for k in KINDS],
                         n_tiles=256, **kw)

    def test_两条路的输出形状相同(self):
        """P3 的接口约定：窗口路与 glob 路输出同一个 d_global，下游一律不用改。"""
        plain, winm = self._net(), self._net(win_widths=_widths(self.win))
        st = {"grid": self.obs.grid, "glob": self.obs.glob, "cand": self.obs.cand,
              "act": 0, "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False}
        grid, glob, cand, mask = collate([st], 256)
        with torch.no_grad():
            l1, v1 = plain(grid, glob, cand, mask)
            l2, v2 = winm(grid, glob, cand, mask, win=collate_window([self.win]))
        self.assertEqual(l1.shape, l2.shape)
        self.assertEqual(v1.shape, v2.shape)

    def test_没给窗口时退回glob路(self):
        """`win=None` 不该崩，也不该走随机初始化的编码器 —— 退回 glob_mlp。"""
        winm = self._net(win_widths=_widths(self.win))
        st = {"grid": self.obs.grid, "glob": self.obs.glob, "cand": self.obs.cand,
              "act": 0, "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False}
        grid, glob, cand, mask = collate([st], 256)
        with torch.no_grad():
            a = winm(grid, glob, cand, mask)
            b = winm(grid, glob, cand, mask, win=None)
        torch.testing.assert_close(a[0], b[0], rtol=0, atol=0)

    def test_窗口有梯度(self):
        """编码器必须真的在学 —— 别把它接成一个 `no_grad` 的死枝。"""
        winm = self._net(win_widths=_widths(self.win))
        st = {"grid": self.obs.grid, "glob": self.obs.glob, "cand": self.obs.cand,
              "act": 0, "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False}
        grid, glob, cand, mask = collate([st], 256)
        logits, v = winm(grid, glob, cand, mask, win=collate_window([self.win]))
        logits.sum().backward()
        g = winm.win_enc.proj["m"].weight.grad
        self.assertIsNotNone(g, "窗口编码器没拿到梯度")
        self.assertGreater(float(g.abs().sum()), 0.0)

    def test_价值头仍然吃原始glob(self):
        """P3 刻意的边界：**价值头不动**（它有自己的理由不共用 g，见 model.py）。
        所以窗口里塞垃圾也不该改 value —— 改了说明我不小心把它接进去了。"""
        winm = self._net(win_widths=_widths(self.win))
        st = {"grid": self.obs.grid, "glob": self.obs.glob, "cand": self.obs.cand,
              "act": 0, "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False}
        grid, glob, cand, mask = collate([st], 256)
        wb = collate_window([self.win])
        with torch.no_grad():
            v1 = winm(grid, glob, cand, mask, win=wb)[1]
            for g in GROUPS:
                wb["feats"][g] += 99.0
            v2 = winm(grid, glob, cand, mask, win=wb)[1]
        torch.testing.assert_close(v1, v2, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
