# -*- coding: utf-8 -*-
"""`rl/tokenize.py` 的纯函数级测试。

这一层最贵的错误是**静默**的：token 宽度/顺序/掩码一变，训练照跑、loss 照降，
只是学的东西不是你以为的那个（`rl/vocab.py` 的 docstring 里写的正是这类事故）。
所以这里守的四条都是"不报错但全错"型：

1. **纯函数** —— 同状态两次逐位相等；
2. **预算** —— 上限不超 512（mask 掉的位置照样占张量，按**上限**算，不按实际用量）；
3. **顺序是状态的确定性函数** —— 军队按 id、patch 按 (i,j)，不许用 dict 遍历序；
4. **视野门控** —— 视野外的敌军不许进 A 组。
"""
import unittest

import numpy as np

from rl.env import ZhanguoEnv
from rl.tokenize import CAP, GROUPS, Window, assert_budget, tokenize
from rl.vocab import TOKEN_BUDGET


def _fresh(turns: int = 8, seed: int = 0, map_size: int = 16):
    env = ZhanguoEnv(map_size=map_size, max_turns=turns)
    obs = env.reset(seed)
    return env, obs


_MATURE: dict = {}


def _mature(turns: int = 40, seed: int = 0, map_size: int = 16):
    """跑规则 AI 到中局，拿一个**真有军队、真有领地**的状态。

    用 `collect_episode` 而不是自己编动作串：自编的"每回合 end_turn"永远长不出
    军队，测出来的 A 组恒空 —— 那种测试是绿的但什么都没测（这条踩过一次）。
    """
    key = (turns, seed, map_size)
    if key not in _MATURE:
        from rl.bc import collect_episode, get_teacher
        env = ZhanguoEnv(map_size=map_size, max_turns=turns)
        collect_episode(env, turns, seed=seed, teacher_fn=get_teacher("v9", turns))
        _MATURE[key] = (env, env._obs())
    return _MATURE[key]


class TestPurity(unittest.TestCase):
    def test_同状态两次逐位相等(self):
        env, obs = _fresh()
        a = tokenize(env, obs)
        b = tokenize(env, obs)
        for g in GROUPS:
            np.testing.assert_array_equal(a.feats[g], b.feats[g],
                                          err_msg=f"{g} 组特征不是纯函数")
            np.testing.assert_array_equal(a.mask[g], b.mask[g],
                                          err_msg=f"{g} 组掩码不是纯函数")

    def test_不写_env的任何东西(self):
        """tokenize 里那句 `env._refresh_armies()` 会写 `env.army_index`，
        换成自己排序就是为了不写。这里在两次调用之间**改坏** army_index，
        结果必须一模一样。"""
        env, obs = _fresh()
        a = tokenize(env, obs)
        env.army_index = {999: 999}
        b = tokenize(env, obs)
        np.testing.assert_array_equal(a.feats["a"], b.feats["a"])
        self.assertEqual(env.army_index, {999: 999}, "tokenize 不该动 army_index")

    def test_多次调用不累积状态(self):
        env, obs = _fresh(turns=20)
        first = tokenize(env, obs)
        for _ in range(5):
            tokenize(env, obs)
        last = tokenize(env, obs)
        np.testing.assert_array_equal(first.feats["m"], last.feats["m"])


class TestBudget(unittest.TestCase):
    def test_上限不超预算(self):
        hard = sum(CAP[g] for g in GROUPS)
        self.assertLessEqual(hard, TOKEN_BUDGET,
                             f"窗口上限 {hard} 超预算 {TOKEN_BUDGET}")

    def test_实际用量不超上限(self):
        env, obs = _fresh()
        win = tokenize(env, obs)
        self.assertLessEqual(win.total, sum(CAP[g] for g in GROUPS))
        assert_budget(win)

    def test_mask掉的位置特征全零(self):
        """`flat()` 和 P4 的编码器都依赖这条约定。"""
        env, obs = _fresh(turns=30)
        win = tokenize(env, obs)
        for g in GROUPS:
            off = ~win.mask[g]
            if off.any():
                self.assertEqual(float(np.abs(win.feats[g][off]).max()), 0.0,
                                 f"{g} 组被 mask 的位置特征不是 0")

    def test_各组宽度是常量(self):
        """宽度随局面变 = 拼批时错位、ckpt 作废。开局、中局、大地图都必须是同一宽度。"""
        widths = {}
        cases = [_fresh(turns=5, seed=0), _mature(turns=40, seed=0),
                 _mature(turns=30, seed=7, map_size=32)]
        for env, obs in cases:
            win = tokenize(env, obs)
            for g in GROUPS:
                w = win.feats[g].shape[1]
                widths.setdefault(g, w)
                self.assertEqual(widths[g], w,
                                 f"{g} 组宽度随局面变了：{widths[g]} → {w}")


class TestDeterminism(unittest.TestCase):
    def test_同一seed两次跑出的窗口相同(self):
        """顺序必须是状态的确定性函数 —— 不许用 dict 遍历序。"""
        e1, o1 = _fresh(turns=25, seed=11)
        e2, o2 = _fresh(turns=25, seed=11)
        w1, w2 = tokenize(e1, o1), tokenize(e2, o2)
        for g in GROUPS:
            np.testing.assert_array_equal(w1.feats[g], w2.feats[g], err_msg=g)

    def test_军队组自家在前且同序(self):
        env, obs = _mature(turns=40, seed=0)
        win = tokenize(env, obs)
        n_own = win.meta["n_own_armies"]
        self.assertGreaterEqual(n_own, 1, "中局该有自家军队，否则这条测试是空的")
        # 自家段（前 n_own 行）必须是「自己是 owner」的那一批
        self.assertTrue(win.mask["a"][:n_own].all(), "自家的 token 不该被 mask")
        self.assertTrue((win.feats["a"][:n_own, 0] == 1.0).all(),
                        "A 组前 n_own 行必须是自家的（owner one-hot 第 0 位）")
        # 自家之后的他国/野人段若存在，owner 位不许落在"自己"上
        if win.mask["a"][n_own:].any():
            self.assertEqual(float(win.feats["a"][n_own:, 0].max()), 0.0)

    def test_patch组从左上到右下(self):
        env, obs = _fresh(turns=12)
        win = tokenize(env, obs)
        ni, nj = win.meta["patch_grid"]
        n = ni * nj
        c = win.feats["m"].shape[1] - 3
        dx = win.feats["m"][:n, c]         # 行尾三列 = (x偏移, y偏移, 可见占比)
        dy = win.feats["m"][:n, c + 1]
        self.assertTrue(np.allclose(dx[:nj], dx[0]), "同一行的 patch x 偏移应相同")
        self.assertLessEqual(float(dx[:nj].max()), float(dx[nj:].min()) + 1e-6,
                             "patch 必须按 (i,j) 行优先排列（x 优先）")
        self.assertLessEqual(float(dy[::nj].max()), float(dy[1::nj].min()) + 1e-6,
                             "patch 必须按 (i,j) 行优先排列（y 随列递增）")


class TestVisionGate(unittest.TestCase):
    def test_视野外的敌军不进A组(self):
        """引擎视野（`World.visible_to`）之外的他国/野人军队，一个都不许出现。
        这是 v9 那次堵的同一类洞：模型不该看见它看不见的东西。"""
        env, obs = _mature(turns=40, seed=0, map_size=24)
        win = tokenize(env, obs)
        n_own = win.meta["n_own_armies"]
        w, me = env.world, env.agent
        own_ids = {a["id"] for a in w.nation_armies(me)}
        # 用引擎自己的 visible_to 复算一遍，逐行对账
        ax, ay = env.anchor
        from rl.vocab import POS_SCALE
        for i in range(n_own, int(win.mask["a"].sum())):
            x = int(round(win.feats["a"][i, 7] * POS_SCALE)) + ax
            y = int(round(win.feats["a"][i, 8] * POS_SCALE)) + ay
            self.assertTrue(w.visible_to(me, x, y),
                            f"A 组第 {i} 行的敌军在 {(x, y)} 不可见，却进了窗口")
        self.assertTrue(own_ids, "这局该有自家军队")

    def test_迷雾里的地块通道为零(self):
        """网格已经按视野门控过（`env._obs` 里 `chans[i] *= vis`），
        而 M 组的块内均值直接取自网格 —— 所以视野外那部分对 patch 的贡献必须是 0。"""
        env, obs = _fresh(turns=15)
        win = tokenize(env, obs)
        vis_ch = env.obs_channels().index("visible")
        h, w = obs.grid.shape[1], obs.grid.shape[2]
        ph, pw = win.meta["patch_hw"]
        ni, nj = win.meta["patch_grid"]
        for i in range(ni):
            for j in range(nj):
                k = i * nj + j
                blk_vis = obs.grid[vis_ch, i * ph:(i + 1) * ph, j * pw:(j + 1) * pw]
                self.assertAlmostEqual(float(win.feats["m"][k, -1]),
                                       float(blk_vis.mean()), places=5,
                                       msg="可见占比与网格对不上")
                if blk_vis.max() == 0.0:      # 整块在雾里
                    self.assertEqual(float(np.abs(win.feats["m"][k, :-3]).max()), 0.0,
                                     f"patch {k} 整块在雾里，通道却不是 0")


class TestFlat(unittest.TestCase):
    def test_flat形状与掩码一致(self):
        env, obs = _fresh(turns=10)
        win = tokenize(env, obs)
        toks, gid, msk = win.flat()
        self.assertEqual(toks.shape[0], win.total)
        self.assertEqual(len(gid), win.total)
        self.assertEqual(len(msk), win.total)
        self.assertEqual(int(msk.sum()), win.live)
        self.assertEqual(sorted(set(gid.tolist())), list(range(len(GROUPS))))

    def test_flat被mask的位置全零(self):
        env, obs = _fresh(turns=10)
        win = tokenize(env, obs)
        toks, _gid, msk = win.flat()
        self.assertEqual(float(np.abs(toks[~msk]).max()), 0.0)

    def test_mem传了就亮没传就灭(self):
        env, obs = _fresh()
        f = np.full((CAP["r"], 16), 0.5, np.float32)
        self.assertTrue(tokenize(env, obs, mem=f).mask["r"].all())
        self.assertFalse(tokenize(env, obs).mask["r"].any())


class TestDiploReserved(unittest.TestCase):
    def test_势力与事件组现在永远不亮(self):
        """`feat/rl` 没有外交。这两组是**给外交留的位**（TOKEN_DESIGN §4）——
        现在必须恒为 mask 掉 + 全零，将来回填时才不用动形状。"""
        env, obs = _fresh(turns=10)
        win = tokenize(env, obs)
        for g in ("n", "e", "k"):
            self.assertFalse(win.mask[g].any(), f"{g} 组现在不该亮")
            self.assertEqual(float(np.abs(win.feats[g]).max()), 0.0, f"{g} 组该全零")

    def test_全局组给外交留了位(self):
        env, obs = _fresh()
        win = tokenize(env, obs)
        self.assertEqual(win.feats["g"].shape[1], env.glob_size() + 8)
        # 预留位现在必须是 0
        self.assertEqual(float(np.abs(win.feats["g"][0, env.glob_size():]).max()), 0.0)


if __name__ == "__main__":
    unittest.main()
