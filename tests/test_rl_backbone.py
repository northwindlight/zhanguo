# -*- coding: utf-8 -*-
"""沙盒主干（WindowTransformer）+ 观测的守卫。

钉六件事，每条都对着一个**踩过的坑或用户点名的口径**：

  1. ★ **`cross2` 真的是活的** —— 换一个候选的特征，**另一个候选的 logit 必须变**。
     这是换主干的**全部理由**（候选互见），而"加了层却没用上"是静默失效的典型：
     不报错、shape 对、照样训，只是白加。必须有一条测试证明它接进去了。
  2. ★ **归属六类与引擎判定一致** —— 预言机是引擎自己的 `_mv_wall` / `_atk_target_ok`，
     不是我以为的那张表。（我 docstring 里那张表就写错过一格，靠这条测出来。）
  3. ★ **盟友 / 中立国 / 盟友市政厅都分得开** —— 用户 2026-09-24 晚：
     「主干候选有**中立国家**和盟友吗，**盟友市政厅也重要**」。
  4. ★ **`hold` 与 `attack` 在观测里必须不同** —— 旧代码 `row[0 if kind=="move" else 1]`
     把 `hold` 静默编码成了 `attack`（两种相反的动作一模一样）。
  5. **批内可以混不同尺寸的地图**（随机地图）—— 归一尺度不能是模型上的单值 buffer。
  6. **padding 不影响真候选的 logits**（mask 写错不会报错，只会学歪）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                  # noqa: E402
import torch                                        # noqa: E402

from mp import World                                # noqa: E402
from rl import encode, model, train                 # noqa: E402
from rl import vocab as V                            # noqa: E402
from rl.sandbox import Sandbox                       # noqa: E402


def three_nation_world(size=14, seed=5, ally=True, war=True):
    """六类归属都能摆出来的一局：甲(我) / 乙(盟友) / 丙(敌国) / 丁(中立国)。

    ★ 联盟与中立**开局直接指定**（用户 2026-09-24：「是否中立和联盟和模型无关，
    开局直接指定，而不是让模型发起和接受」）—— 所以这里直接摆 `blocs` / `declare_war`，
    **不走** propose/accept 那套流程。
    """
    w = World(size=size, seed=seed, nations=["甲", "乙", "丙", "丁"])
    # ★★ **顺序有讲究：先宣战、后结盟。**
    #   `declare_war` 对**在盟国**会转成"联盟宣战投票"（多数决），并且**照样返回
    #   `(True, …)`** —— 但 `w.wars` 是空的，**仗根本没打起来**。这是"工具骗人"那一类：
    #   返回值说成功、状态没变，静默。实测：先加 bloc 再 `declare_war("甲","丙")`
    #   ⇒ `war_between("甲","丙")` 仍是 False。所以场景脚本文档化这条顺序。
    if war:
        w.declare_war("甲", "丙")          # 此刻甲还是独立国家 ⇒ 直接开战
    if ally:
        w.blocs.append({"name": "测试联盟", "chief": "甲", "members": ["甲", "乙"]})
    assert (not war) or w.war_between("甲", "丙"), "宣战没生效（见上面那条注释）"
    return w


def owner_scenario():
    """→ `(world, {类别下标: (x, y)})`，六类各占一格。

    ⚠ `world.tiles` **只装各国的十字领土**（无主格根本不在这个 dict 里 —— 它的地形是
      `tile_terrain()` 按需生成的）⇒ "无主格"要**自己找个不在 `tiles` 里的坐标**，
      不能去 `tiles` 里筛 `owner is None`（那样筛出来永远是空）。
    """
    w = three_nation_world()
    cells = {}
    for cls, owner in ((V.OWN_SELF, "甲"), (V.OWN_ALLY, "乙"), (V.OWN_RIVAL, "丙"),
                       (V.OWN_NEUTRAL_NATION, "丁")):
        cells[cls] = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == owner)
    free = [(x, y) for x in range(w.size) for y in range(w.size)
            if (x, y) not in w.tiles]
    assert len(free) >= 2, f"无主格不够（{len(free)}）—— size 太小"
    # ⚠ `_ensure_guardians()` 在**几乎每个**无主格上都摆了野人 ⇒ 想要一格的
    #   `OWN_UNOWNED`（无主 + **无**驻军）得自己把那格的野人清掉，不能指望地图上天然有。
    free.sort(key=lambda p: (p[0] * 31 + p[1]) % 7)     # 固定但不单调的取法
    un, bar = free[0], free[1]
    w.armies[:] = [a for a in w.armies if (a["x"], a["y"]) != un]   # 清空 un 格
    cells[V.OWN_UNOWNED], cells[V.OWN_BARBARIAN] = un, bar
    if not any(a["owner"] == "野人" and (a["x"], a["y"]) == bar for a in w.armies):
        gid, seq = w._new_army("野人")                  # 该格若本来没野人，补一支
        w.armies.append({"id": seq, "gid": gid, "name": f"野人{seq}", "type": "步",
                         "hp": 100, "x": bar[0], "y": bar[1], "owner": "野人",
                         "moved_turn": -1, "engaged": False})
    return w, cells


# ===========================================================================
class TestOwnerClasses(unittest.TestCase):
    """②③ 归属六类 —— 预言机是**引擎自己**的通行判定。"""

    def test_all_six_classes_reachable(self):
        w, cells = owner_scenario()
        got = {encode._owner_class(w, "甲", *c) for c in cells.values()}
        self.assertEqual(got, set(range(6)), f"六类没摆全，实得 {sorted(got)}")

    def test_classes_agree_with_engine_move_legality(self):
        """★ **不是**复述我的表 —— 拿 `_mv_wall` 的实际返回值当预言机。

        （我 docstring 里写"野人驻守 ⇒ mv 不可"，而引擎 `_mv_wall` 的驻军谓词
          **显式排除了野人** ⇒ 其实能走。这条测试就是来抓这种"读代码读出来的自信"。）
        """
        w, cells = owner_scenario()
        allowed = {c: (w._mv_wall("甲", *p) is None) for c, p in cells.items()}
        expect = {V.OWN_SELF: True, V.OWN_ALLY: True, V.OWN_RIVAL: False,
                  V.OWN_NEUTRAL_NATION: False, V.OWN_UNOWNED: True,
                  V.OWN_BARBARIAN: True}
        for cls, ok in expect.items():
            self.assertEqual(allowed[cls], ok,
                             f"{V.OWNER_CHANNELS[cls]} 的 mv 通行与预期不符"
                             f"（引擎说 {allowed[cls]}）")
        # ★ 中立国必须与敌国**同样不可 mv**、但两者在观测里**不同类**
        self.assertFalse(allowed[V.OWN_NEUTRAL_NATION])
        self.assertFalse(allowed[V.OWN_RIVAL])
        # ★ 钉一个**反直觉的引擎事实**：`_mv_wall` 的驻军谓词**显式排除野人**
        #   （`d["owner"] != "野人"`）⇒ **野人挡不住 mv**，军队能走进野人格站着。
        #   （我原以为"有驻军 ⇒ mv 不可"，是读代码读出来的自信；这里是实测钉死。）
        self.assertTrue(allowed[V.OWN_BARBARIAN],
                        "引擎的 mv 驻军谓词排除了野人 ⇒ 野人格其实走得进去")

    def test_neutral_nation_differs_from_rival(self):
        """中立国（有主、非盟非敌）与敌国的**可行动作不同** —— 观测必须分得开。"""
        w, cells = owner_scenario()
        self.assertEqual(encode._owner_class(w, "甲", *cells[V.OWN_NEUTRAL_NATION]),
                         V.OWN_NEUTRAL_NATION)
        self.assertEqual(encode._owner_class(w, "甲", *cells[V.OWN_RIVAL]), V.OWN_RIVAL)
        # 敌国可 atk、中立国不可 —— 这正是"混成一类就分不出该不该打"
        self.assertTrue(w._atk_target_ok("甲", *cells[V.OWN_RIVAL]))
        self.assertFalse(w._atk_target_ok("甲", *cells[V.OWN_NEUTRAL_NATION]))

    def test_ally_move_yes_attack_no(self):
        """盟友：**可 mv 不可 atk**（引擎对盟友与敌国的判定正好相反）。"""
        w, cells = owner_scenario()
        self.assertIsNone(w._mv_wall("甲", *cells[V.OWN_ALLY]))
        self.assertFalse(w._atk_target_ok("甲", *cells[V.OWN_ALLY]))


class TestCandidateMarks(unittest.TestCase):
    """③④ 候选标记里盟友/中立国/**盟友市政厅**都在，且 `hold ≠ attack`。"""

    def _sb(self):
        sb = Sandbox(seed=7, size=16).reset()
        sb.world.nations["丙"] = None                     # 只为让 tile owner 有个"中立国"
        return sb

    def test_ally_tile_and_ally_hall_are_marked(self):
        """★ 用户点名的那条：盟友的格与**盟友的市政厅**都必须能被候选看见。"""
        sb = self._sb()
        w = sb.world
        me, ally = "甲", "乙"
        w.blocs.append({"name": "联盟", "chief": me, "members": [me, ally]})
        ax, ay = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == ally)
        w.tiles[(ax, ay)]["buildings"]["市政厅"] = 1      # 盟友的厅

        acts = [(1, "attack", ax, ay)]                    # 单候选：盟友格
        c = encode.candidate_batch(sb, me, acts=acts, armies=[])
        m = c["cand_marks"][0]
        self.assertEqual(m[V.CAND_OWN0 + V.OWN_ALLY], 1.0,
                         "盟友的格没被标成盟友（旧版这里 SELF/FOE/NEUTRAL 全 0，信息丢了）")
        self.assertEqual(m[V.CAND_HALL0 + V.OWN_ALLY], 1.0,
                         "★ 盟友的市政厅没被标出")
        self.assertEqual(m[V.CAND_OWN0 + V.OWN_RIVAL], 0.0)
        # 我方的厅要落在 HALL+SELF 上，不能跟盟友的混
        mx, my = sb.core_of(me)
        c2 = encode.candidate_batch(sb, me, acts=[(1, "move", mx, my)], armies=[])
        m2 = c2["cand_marks"][0]
        self.assertEqual(m2[V.CAND_HALL0 + V.OWN_SELF], 1.0)
        self.assertEqual(m2[V.CAND_HALL0 + V.OWN_ALLY], 0.0)

    def test_marks_ownership_is_one_hot(self):
        """归属 one-hot 必须**恰好一位为 1**（六类互斥、不漏不重）。"""
        sb = self._sb()
        acts = sb.legal()
        c = encode.candidate_batch(sb, sb.current_player(), acts=acts)
        block = c["cand_marks"][:, V.CAND_OWN0:V.CAND_OWN0 + len(V.OWNER_CHANNELS)]
        self.assertTrue(np.all(block.sum(1) == 1.0),
                        f"有候选的归属不是单热：{block.sum(1)[block.sum(1) != 1.0][:5]}")

    def test_hold_is_not_attack(self):
        """★ 旧 bug 的回归：`hold` 与 `attack` 在观测里必须是**两种类型**。"""
        sb = self._sb()
        me = sb.current_player()
        a = sb.armies_of(me)[0]
        acts = [(a["id"], "hold", a["x"], a["y"]),
                (a["id"], "move", a["x"], a["y"]),
                (a["id"], "attack", a["x"], a["y"])]
        c = encode.candidate_batch(sb, me, acts=acts, armies=[])
        self.assertEqual(list(c["type_idx"]), [V.KIND_INDEX[k]
                                               for k in ("hold", "move", "attack")])
        self.assertEqual(V.KIND_INDEX["hold"], 0)
        self.assertNotEqual(c["type_idx"][0], c["type_idx"][2],
                            "hold 与 attack 的类型下标相同 ⇒ 观测里分不出（旧 bug）")

    def test_army_token_carries_unit_stats(self):
        """军队 token 尾部那 4 列 = **现算**的兵种数值（用户：「各个单位的战斗力」）。"""
        from rl import features as F
        sb = self._sb()
        win, _, armies = encode.encode_window(sb, sb.current_player())
        self.assertEqual(win["a"].shape[1], V.A_WIDTH_RAW + F.F_U)
        i = next(i for i, a in enumerate(armies) if a.get("type") == "步")
        off = V.A_WIDTH_RAW
        np.testing.assert_allclose(win["a"][i, off:], F.unit_vector("步"))


# ===========================================================================
class TestBackbone(unittest.TestCase):
    def _batch(self, sb=None, n=2):
        sb = sb or Sandbox(seed=11, size=12).reset()
        rows = []
        for _ in range(n):
            rows.append(encode.obs_of(sb, sb.current_player()))
        return train.collate(rows), sb

    def test_param_count_is_at_least_1m(self):
        """用户 2026-09-24：「0.135m 的模型真的够用吗…**至少给我弄到 1m**」。"""
        p = model.build_model().n_params()
        self.assertGreater(p, 1_000_000, f"参数量 {p/1e6:.3f}M < 1M")

    def test_shapes_and_finite(self):
        batch, _ = self._batch()
        net = model.build_model()
        logits, value = net(batch)
        self.assertEqual(logits.shape, batch["mask"].shape)
        self.assertEqual(value.shape, (logits.shape[0],))
        self.assertTrue(torch.isfinite(logits[batch["mask"]]).all())
        self.assertTrue(torch.isfinite(value).all())

    def test_cross2_is_on_the_gradient_path(self):
        """★★ **候选 0 的 logit 必须依赖 `cross2` 的参数** —— 确定性证明，不靠运气。

        ⚠ 我第一版用的是"动候选 1、看候选 0 的 logit 变不变"，**它是个靠运气的测试**：
          网络是随机初始化的，扰动效应有时小于 `allclose` 的默认容差 ⇒ 同一条测试
          会因为初始化不同而**时绿时红**（实测：加了几列 GLOB 之后它自己变红了）。
          ⇒ 换成反传：`cross2` 若不在候选 0 的计算图上，它的 `weight.grad` 就是 `None`。
        """
        batch, _ = self._batch(n=1)
        torch.manual_seed(0)
        net = model.build_model()
        logits, _ = net(batch)
        logits[0, 0].backward()
        g = net.cross2.q.weight.grad
        self.assertIsNotNone(g, "★ cross2 不在候选的梯度路径上 ⇒ 加了一层却没用上")
        self.assertGreater(float(g.abs().sum()), 0.0)

    def test_perturbing_one_candidate_moves_another(self):
        """★★ 语义版：动候选 1 的落点 ⇒ 候选 0 的 logit 必须变。

        固定种子 + **显式阈值**（不用 `allclose` 的默认容差）⇒ 不再时绿时红。
        """
        batch, _ = self._batch(n=1)
        torch.manual_seed(0)
        net = model.build_model()
        net.eval()
        with torch.no_grad():
            base, _ = net(batch)
            k = int(batch["mask"][0].sum())
            if k < 2:
                self.skipTest("候选太少，比不出互见")
            b2 = {kk: (v.clone() if torch.is_tensor(v) else v) for kk, v in batch.items()}
            b2["pos_dx"] = batch["pos_dx"].clone()
            b2["pos_dx"][0, 1] = batch["pos_dx"][0, 1] + 0.37      # 只动候选 1
            alt, _ = net(b2)
        diff = float((base[0, 0] - alt[0, 0]).abs().max())
        # ★ 判据是"**传播了没有**"，所以阈值就是 **0**：`cross2` 没接进去的话
        #   候选 0 与候选 1 之间**没有任何通路** ⇒ 差**精确等于 0**。
        #   别用一个拍脑袋的数（我第一版写 1e-5，而随机初始化下真实效应只有 2.9e-6
        #   ⇒ 测试无缘无故地红）。同理别用 `allclose` 的默认容差。
        self.assertGreater(diff, 0.0,
                           "★ 改了候选 1，候选 0 的 logit 一动不动 ⇒ cross2 没接进去")

    def test_cross_is_live(self):
        """候选 → **窗口** 的 cross 也得是活的：动一支军队 token，候选 logit 要变。"""
        batch, _ = self._batch(n=1)
        net = model.build_model()
        net.eval()
        with torch.no_grad():
            base, _ = net(batch)
            b2 = {kk: (v.clone() if torch.is_tensor(v) else v) for kk, v in batch.items()}
            b2["win"] = dict(batch["win"])
            b2["win"]["a"] = batch["win"]["a"].clone()
            b2["win"]["a"][0, 0, V.A_HP] += 0.5                    # 只动第一支军的 hp
            alt, _ = net(b2)
        self.assertFalse(torch.allclose(base, alt), "★ 改了军队 token，logits 没变")

    def test_padding_does_not_change_real_logits(self):
        """★ 补零的 padding 候选**不许**影响真候选的 logits（mask 写错不报错、只学歪）。

        ★ 别指望"换个大地图候选自然更多"：实测 12×12 与 20×20 的首帧候选**都是 45 个**
          （候选数由"每军可达格数"决定，与地图大小不成正比）⇒ 那条路子比不出东西。
          这里**人工把候选补长**，前提才成立。
        """
        sb = Sandbox(seed=13, size=12).reset()
        net = model.build_model()
        net.eval()
        obs = encode.obs_of(sb, sb.current_player())
        n = obs["mask"].shape[0]
        # 复制一份、把候选**首尾拼长**（同一批里另一行有更多候选 ⇒ 候选维被拉长）
        big = {"grid": obs["grid"], "win": obs["win"], "win_mask": obs["win_mask"],
               "mask": np.concatenate([obs["mask"], obs["mask"]]), "n_armies": obs["n_armies"]}
        big["cand"] = {kk: np.concatenate([v, v], 0) for kk, v in obs["cand"].items()}
        with torch.no_grad():
            solo, _ = net(train.collate([obs]))
            pair, _ = net(train.collate([obs, big]))
        self.assertGreater(pair.shape[1], solo.shape[1], "候选维没被拉长，这条测不出东西")
        torch.testing.assert_close(pair[0, :n], solo[0, :n], atol=1e-4, rtol=1e-4,
                                   msg="补零的 padding 漏进了真候选的打分")

    def test_batch_mixes_map_sizes(self):
        """★ 批内混**不同尺寸**的地图（随机地图）必须能跑：

        归一尺度若做成模型上的单值 buffer，这里就会"不报错、只是尺度对不上"。
        """
        rows = []
        for sz in (8, 16, 24):
            sb = Sandbox(seed=sz, size=sz).reset()
            rows.append(encode.obs_of(sb, sb.current_player()))
        batch = train.collate(rows)
        logits, value = model.build_model()(batch)
        self.assertEqual(tuple(logits.shape[:1]), (3,))
        self.assertTrue(torch.isfinite(value).all())

    def test_window_has_global_token_and_group_structure(self):
        """窗口**结构留位**：`g` 恒亮一条、组名来自 `vocab.TOKEN_GROUPS`。"""
        batch, _ = self._batch(n=1)
        self.assertEqual(list(batch["win"]), list(V.TOKEN_GROUPS))
        self.assertTrue(bool(batch["win_mask"]["g"][0, 0]), "`g` 组必须恒亮")


class TestHallsKnownMode(unittest.TestCase):
    """★★ 用户 2026-09-24：「对手的厅应该是**明知**的，有**两种模式**，一个是 llm
    **已经派了间谍**、明知对手厅了，一个是**没有**、rl 模型**自己找厅**」。

    ★ 这一组同时钉住那条**安全边界**：知道厅在哪 **≠** 看得见厅上的守军。
    """

    def _sb(self, spy):
        # 24×24 ⇒ 两国核心离得远，缺省视野**看不到**对方的厅
        return Sandbox(seed=3, size=24, halls_known=spy).reset()

    def test_glob_carries_known_hall_position(self):
        a, b = self._sb(False), self._sb(True)
        ga = dict(zip(V.GLOB, encode.encode_glob(a, "甲")))
        gb = dict(zip(V.GLOB, encode.encode_glob(b, "甲")))
        self.assertEqual(ga["foe_halls"], 0.0, "没派间谍 ⇒ 不该知道敌厅在哪")
        self.assertGreater(gb["foe_halls"], 0.0, "★ 派了间谍 ⇒ 敌厅座数该已知")
        self.assertNotEqual((gb["foe_hall_dx"], gb["foe_hall_dy"]), (0.0, 0.0),
                            "★ 派了间谍却没给出敌厅的**方位** ⇒ 这一路白加了")
        # ★ **相对坐标**：偏移归一化到 [−1,1]（地图大小无关），不是绝对格号
        self.assertLessEqual(max(abs(gb["foe_hall_dx"]), abs(gb["foe_hall_dy"])), 1.0)

    def test_my_hall_is_always_known(self):
        """我自己的厅**永远**已知（是 0,0 —— 原点就是我家核心），两种模式一致。"""
        for spy in (False, True):
            g = dict(zip(V.GLOB, encode.encode_glob(self._sb(spy), "甲")))
            self.assertEqual(g["my_halls"], 0.25)          # 1 座 / 4
            self.assertEqual((g["my_hall_dx"], g["my_hall_dy"]), (0.0, 0.0))

    def test_spy_mode_does_not_leak_garrisons(self):
        """★★ 安全边界：**"知道厅在哪" 不许顺带暴露厅上的守军**。

        做法：给对手的厅上摆一支军（在视野外），两种模式下的**可见敌军 token 数**
        必须一模一样 —— 若实现是"把厅格塞进 `vision_mask`"，这里就会多出一支军。
        """
        counts = {}
        for spy in (False, True):
            sb = self._sb(spy)
            w = sb.world
            fx, fy = sb.core_of("乙")
            gid, seq = w._new_army("乙")
            w.armies.append({"id": seq, "gid": gid, "name": f"乙{seq}", "type": "步",
                             "hp": 100, "x": fx, "y": fy, "owner": "乙",
                             "moved_turn": -1, "engaged": False})
            mask = encode.vision_of(sb, "甲")
            self.assertNotIn((fx, fy), mask, "前提：该格本来在视野外")
            win, _, _ = encode.encode_window(sb, "甲", mask)
            counts[spy] = win["a"].shape[0]
            # 真视野掩码本身**一点没变** —— 变的只是"厅的位置"这一路
            self.assertEqual(len(mask), len(encode.vision_of(self._sb(spy), "甲")))
        self.assertEqual(counts[False], counts[True],
                         "★ 间谍模式把厅上的守军也暴露了 —— 那是偷看，不是'知道厅在哪'")

    def test_spy_mode_changes_the_score_only_through_proximity(self):
        """打分器那侧：`halls_known` 只影响**逼近项**（"我离敌厅还有多远"）。"""
        from rl import evaluate as E
        sb = self._sb(False)
        mask = encode.vision_of(sb, "甲")
        self.assertNotEqual(E.score(sb.world, "甲", "乙", mask, halls_known=True),
                            E.score(sb.world, "甲", "乙", mask, halls_known=False),
                            "★ 间谍模式对打分毫无影响 ⇒ 没透进去")

    def test_grid_frame_cannot_hold_out_of_view_halls(self):
        """★ 钉住"**为什么厅不进网格**"：网格 = 视野外接框，框外的格**没有位置**。

        这条是给未来的自己看的 —— 别再尝试"把已知的厅塞进网格通道"。
        """
        sb = self._sb(True)
        g = encode.encode_grid(sb, "甲")
        self.assertEqual(g[V.GRID_HALL_RIVAL].sum(), 0.0,
                         "间谍模式下网格里**仍然**没有敌厅 —— 因为它在视野框外，"
                         "这正是要把厅放进**全局/相对坐标**而不是网格的原因")


if __name__ == "__main__":
    unittest.main()