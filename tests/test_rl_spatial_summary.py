# -*- coding: utf-8 -*-
"""★★ **地图进入全局推理和价值头**（报告 §8.2）—— 有界的**空间摘要**。

报告 §8.2 的原话：

    「当前网格只在**候选落点** gather 特征。1×1+3×3 的空间感受野为 3×3；
      候选互看能整合各候选的局部信息，但**没有可靠通路读取远离所有候选的已知地形**。
      价值头完全从**实体窗口均值池化**取值，**没有读网格卷积结果**。」

★ 修法：加一个**有界的**空间摘要 token 组 `"s"`（两档尺度、固定格数）⇒
  价值头（窗口掩码均值池化）**自动**读得到，候选（对窗口的交叉注意力）**也**读得到。

★★ 验收（报告 §8.2 的原话）：
    「构造**军队和全局标量相同、已知远方地形不同**的两局。
      在当前模型中，未交战时的价值路径**不读取**该差异；
      升级后必须有**结构及梯度通路**，且能在**小型有标准答案的路线选择任务**上**学会区分**。
      **随机初始化输出有微小差异只能证明接线，不能证明会规划**。」
"""
from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl import encode as E                           # noqa: E402
    from rl import tasks                                # noqa: E402
    from rl import vocab as V                           # noqa: E402
    from rl.model import build_model                    # noqa: E402
    from rl.sandbox import Sandbox                      # noqa: E402
    from rl.train import collate                        # noqa: E402
else:
    Sandbox = build_model = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _obs(seed=0, size=8, change_terrain=None):
    sb = Sandbox(seed=seed, size=size, n_nations=2, t_max=20, war=True).reset(
        random_starts=False)
    if change_terrain is not None:
        w = sb.world
        terr = w._world.territory
        fact = terr.cell(*change_terrain)
        if fact.tile.id:
            terr._tiles[fact.tile.id] = dataclasses.replace(fact.tile,
                                                           terrain="native:mountain")
    return E.obs_of(sb, "甲", sb.legal())


def _far_visible_cell(seed=0, size=8):
    """一个**看得见、但离核心远**的格（用来做"远处地形"的隔离实验）。"""
    sb = Sandbox(seed=seed, size=size, n_nations=2, t_max=20, war=True).reset(
        random_starts=False)
    w = sb.world
    mask = E.vision_of(sb, "甲")
    for tid, tile in w._world.territory._tiles.items():
        if not tid:
            continue
        x, y = tile.position
        if (x, y) in mask and (x, y) not in ((1, 1), (6, 6)) \
                and abs(x - 1) + abs(y - 1) >= 3:
            return (x, y)
    raise RuntimeError("用例前提：这一局得有个'看得见但远'的格")


class TestSpatialSummaryIsBounded(unittest.TestCase):
    """§8.2 的第一条纪律：**数量有界**、不许变成"地图面积的平方"。"""

    def test_group_size_is_independent_of_the_map_size(self):
        """★ 地图变大 ⇒ 摘要的**格数与列数都不变**。"""
        shapes = []
        for size in (8, 12, 16):
            obs = _obs(seed=0, size=size)
            shapes.append(obs["win"]["s"].shape)
        self.assertEqual(len(set(shapes)), 1,
                         f"摘要形状随地图大小变了：{shapes}")
        self.assertEqual(shapes[0], (V.SPATIAL_CELLS, V.SPATIAL_WIDTH))

    def test_unknown_area_is_not_blended_into_walkable(self):
        """★★ **降采样不许把语义混成"均匀可走"**。

        ★ 判据：把**视野外**的格全变"看不见" ⇒ 那一格的 `vis_frac` 必须是 0，
          而**地形那些量必须跟着掉到 0**（不是"保留一个平均值"）。
        """
        obs = _obs(seed=0, size=8)
        s = obs["win"]["s"]
        # ★ 看得见的比例在 [0,1]；**完全看不见**的格 ⇒ 那一列必须是 0
        self.assertTrue(np.all(s[:, 0] >= 0.0) and np.all(s[:, 0] <= 1.0))
        # ★ 没有任何一格是"部分看得见但地形却是别的格的平均"——
        #   地形列在"完全看不见"的格上必须为 0（看不到 ⇒ 不知道 ⇒ 不许有数值）
        for row in s:
            if row[0] == 0.0:
                self.assertAlmostEqual(float(row[1]), 0.0, places=6,
                                       msg="看不见的格却带着地形数值 ⇒ 混成了'可走'")
                self.assertAlmostEqual(float(row[2]), 0.0, places=6)

    def test_relative_position_and_visibility_markers_are_present(self):
        """★ 每格带**相对位置**与**可见性**标记（报告 §8.2 明写）。"""
        obs = _obs(seed=0, size=8)
        s = obs["win"]["s"]
        self.assertTrue(np.all(np.abs(s[:, 6]) <= 1.0))
        self.assertTrue(np.all(np.abs(s[:, 7]) <= 1.0))
        # ★ 相对位置**不能全一样**（否则"相对我家核心"这层信息就没了）
        self.assertGreater(len(set(np.round(s[:, 6], 3).tolist())), 1)


class TestValuePathReadsTheFarTerrain(unittest.TestCase):
    """★★ §8.2 的核心验收：**军队与全局标量相同、只有远处地形不同** ⇒ 价值必须不同。"""

    def test_only_the_spatial_group_changes(self):
        """★ 隔离：改**远处一格的地形** ⇒ `g`/`a`/`k` 三组**逐位相同**，
        只有 `"s"` 变。"""
        far = _far_visible_cell()
        A = _obs(seed=0)
        B = _obs(seed=0, change_terrain=far)
        for g in ("g", "a", "k"):
            self.assertTrue(np.array_equal(A["win"][g], B["win"][g]),
                            f"组 {g} 也变了 ⇒ 隔离不干净")
        self.assertFalse(np.array_equal(A["win"]["s"], B["win"]["s"]),
                         "远处地形变了，空间摘要却没变 ⇒ 摘要没在看地形")

    def test_value_output_differs(self):
        """★★ **价值路径**必须读到那个差异（升级前它**读不到**）。"""
        far = _far_visible_cell()
        A = _obs(seed=0)
        B = _obs(seed=0, change_terrain=far)
        torch.manual_seed(0)
        net = build_model()
        vA = float(net(collate([A]))[1].detach()[0])
        vB = float(net(collate([B]))[1].detach()[0])
        self.assertNotAlmostEqual(vA, vB, places=7,
                                  msg="远处地形变了，价值却一样 ⇒ 价值路径没读地图")

    def test_gradient_reaches_the_spatial_projection(self):
        """★★ **梯度通路**：价值的损失必须能流到空间摘要的投影上。"""
        obs = _obs(seed=0)
        torch.manual_seed(0)
        net = build_model()
        _logits, value = net(collate([obs]))
        value.sum().backward()
        grads = {n: p.grad for n, p in net.named_parameters() if n.startswith("proj.s")}
        self.assertTrue(grads, "找不到空间摘要的投影参数")
        for n, g in grads.items():
            self.assertIsNotNone(g, f"{n} 没有梯度")
            self.assertGreater(float(g.norm()), 0.0,
                               f"{n} 的梯度是 0 ⇒ 梯度到不了空间摘要")

    def test_army_token_index_stays_stable(self):
        """★★ 军队段的**稳定下标** `x[:, 1:1+n_a]` 仍然成立（报告 §8.3 的要求）。

        ★ 空间摘要组放在**最后** ⇒ 军队段的位置一个字节没动。
        """
        obs = _obs(seed=0)
        b = collate([obs])
        net = build_model()
        x, wm = net.encode_window(b["win"], b["win_mask"])
        n_a = b["win"]["a"].shape[1]
        self.assertEqual(net.groups[-1], "s", "空间摘要组应当在最后")
        self.assertTrue(bool(wm[:, 1:1 + n_a].all()),
                        "军队段不再是'全亮' ⇒ 下标被挤动了")
        self.assertGreater(int(wm[:, 1:1 + n_a].sum()), 0,
                           "军队段里没有 token 了 ⇒ 下标被挤动了")


class TestRouteChoiceLearnsFromTheMap(unittest.TestCase):
    """★★ §8.2 的学习验收：**小型有标准答案的路线选择任务**上**学会区分**。

    ★★ 为什么两个变体**一起训**：
      只训一个变体 ⇒ "学会了"与"背下了这个下标"**分不开** ⇒ 测不到"读地图"。
      两个变体**一起训**（答案相反、候选特征**完全相同**）⇒
      只有**真的读了远处地形**才能两个都对。
    """

    def test_variants_differ_only_in_the_far_terrain(self):
        """★ 两个变体的**候选特征逐位相同**，只有远处的山墙不同 ⇒
        答案**只能**从空间摘要里读出来。"""
        a = tasks.route_fixture("east_blocked")
        b = tasks.route_fixture("north_blocked")
        obs_a = E.obs_of(a[0], a[1], a[0].legal())
        obs_b = E.obs_of(b[0], b[1], b[0].legal())
        self.assertTrue(np.array_equal(obs_a["cand"]["cand_content"],
                                       obs_b["cand"]["cand_content"]),
                        "两个变体的候选内容不同 ⇒ 答案不只差在远处地形")
        self.assertTrue(np.array_equal(obs_a["cand"]["cand_marks"],
                                       obs_b["cand"]["cand_marks"]),
                        "两个变体的候选标记不同 ⇒ 答案不只差在远处地形")
        self.assertFalse(np.array_equal(obs_a["win"]["s"], obs_b["win"]["s"]),
                         "两个变体的空间摘要相同 ⇒ 读不出来")
        # ★ 答案相反
        ca = tasks.route_correct(a[0], a[1], a[2], a[3], "east_blocked")
        cb = tasks.route_correct(b[0], b[1], b[2], b[3], "north_blocked")
        self.assertNotEqual(ca, cb, "两个变体的正确候选相同 ⇒ 这题不用读地图")

    def test_model_learns_to_use_the_map(self):
        """★★★ 短训之后，**两个变体**的正确候选概率质量都要高。

        ★ 这是报告 §8.2 说的"**学会区分**"——
          候选特征完全相同、答案相反 ⇒ 两个都对 ⇒ **只可能是读了地图**。
        """
        out = tasks.route_train(iters=50, batch=24, seed=0)
        for variant, mass in out["final"].items():
            self.assertGreater(mass, 0.6,
                               f"变体 {variant} 的正确候选概率质量只有 {mass:.3f} "
                               f"⇒ 没学会读地图")
        # ★ 未训练网应当是**瞎猜**水平（9 个候选 ⇒ ~0.11）
        sb, me, e, n = tasks.route_fixture("east_blocked")
        acts = sb.legal()
        correct = tasks.route_correct(sb, me, e, n, "east_blocked")
        torch.manual_seed(0)
        net = build_model()
        with torch.inference_mode():
            logits, _v = net(collate([E.obs_of(sb, me, acts)]))
            probs = torch.softmax(logits[0], -1).numpy()
        self.assertLess(float(probs[correct]), 0.6,
                        "未训练网就已经全押正确候选 ⇒ 对照失效")


if __name__ == "__main__":
    unittest.main()
