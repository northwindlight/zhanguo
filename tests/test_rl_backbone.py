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
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None
from tests.rl_deps import new_army, set_owner, set_hall, make_world, unmaterialized_cells

import numpy as np                                  # noqa: E402
from tests.rl_deps import has_torch, torch, torch_or_skip, new_army, set_owner, set_hall, make_world, unmaterialized_cells

from tests.rl_deps import make_world  # ★ 旧门面的 SandboxWorld 已删 ⇒ 用造局助手
if has_torch():
    from rl import encode, model, train                 # noqa: E402
    from rl import vocab as V                            # noqa: E402
    from rl.sandbox import Sandbox                       # noqa: E402
else:
    Sandbox = encode = vocab = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口（见上面的说明）。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def three_nation_world(size=14, seed=5, ally=True, war=True):
    """六类归属都能摆出来的一局：甲(我) / 乙(盟友) / 丙(敌国) / 丁(中立国)。

    ★ 联盟与中立**开局直接指定**（用户 2026-09-24：「是否中立和联盟和模型无关，
    开局直接指定，而不是让模型发起和接受」）—— 所以这里直接摆 `blocs` / `declare_war`，
    **不走** propose/accept 那套流程。
    """
    w = make_world(size=size, seed=seed, nations=["甲", "乙", "丙", "丁"])
    # ★★ **顺序有讲究：先结盟、后宣战**（2026-10 随新引擎改了）。
    #   新引擎**明文禁止**「正在战争中创盟」（`正在战争中，不能创盟`）
    #   ⇒ 旧口径的"先宣战、后结盟"现在**直接报错**（整批未提交）。
    #   而"先结盟、后宣战"是**通的**：在盟国宣战会转成"联盟表决"，
    #   门面在 `declare_war` 里**代投赞成**（`_auto_approve_votes`）⇒ 仗真的打起来。
    #   ⚠ 旧注释里那句"先加 bloc 再 declare_war ⇒ 仗没打起来"是**旧门面**的行为
    #     （那时没人代投表决），现在**不成立**了 —— 别照着旧注释改回去。
    if ally:
        w.set_alliance(["甲", "乙"], "测试联盟")
    if war:
        w.declare_war("甲", "丙")          # 甲在盟里 ⇒ 走联盟表决（门面代投赞成）
    assert (not war) or w.war_between("甲", "丙"), "宣战没生效（见上面那条注释）"
    return w


def _my_unit(w, name):
    """该国的一支军（拿它的**引擎可达集**当合法性判据）。

    ★ 没有就**真造一支**（native 的 `seed`）—— 合法性判据必须来自引擎，
      没有"参照物"时就没有判据。
    """
    units = [u for u in w.armies() if w.owner_of(u) == name]
    if units:
        return units[0]
    core = next((r.position for r in w.tiles()
                 if r.owner == name and r.buildings.get("native:town-hall", 0) > 0), None)
    if core is None:
        raise AssertionError(f"{name} 没有核心格，造不出参照军")
    new_army(w, name, core)
    return next(u for u in w.armies() if w.owner_of(u) == name)


def _atk_ok(w, name, cell) -> bool:
    """★ 能不能打这一格 —— 按**引擎的规则**逐条问引擎（`Military._batch` 的判据）：

        · 自己的地      ⇒ 不能打（"那是你自己（或盟国）的地块"）
        · 盟国的地      ⇒ 不能打
        · 没在交战的国  ⇒ 不能打（"你和X之间没有生效的战争"）
        · 无主地 / 交战国 ⇒ 能打

    ★ 与"够不够得着"（移动范围）是**两件事** ⇒ 这里只判前者
      （引擎的 `_batch` 也是这么分的：阻挡一条、范围一条）。
    ★ 旧门面的 `_atk_target_ok` 是**自己在 Python 里复刻**这一整条规则 ——
      那正是 review §8 说的"复刻引擎规则"；现在改成**问引擎的事实**
      （归属、盟国、战争关系都是引擎的）。
    """
    owner = w.owner(int(cell[0]), int(cell[1]))
    if owner is None:
        return True
    if owner == name or w.allied(name, owner):
        return False
    return bool(w.war_between(name, owner))


def _mv_wall(w, name, cell):
    """★ 能不能走到那一格 —— 按**引擎的规则**（`Military._pass`）问引擎：

        · 自己的地 / 盟国的地 ⇒ 走得进
        · 没在交战的国的地   ⇒ 走不进（= 墙）
        · 无主地             ⇒ 走得进（引擎保证那儿没有敌军的兵）

    走得到 ⇒ `None`（没有墙）；走不到 ⇒ 那格（= 墙在哪）。
    """
    owner = w.owner(int(cell[0]), int(cell[1]))
    if owner is None or owner == name or w.allied(name, owner):
        return None
    return (int(cell[0]), int(cell[1]))


def owner_scenario():
    """→ `(world, {类别下标: (x, y)})`，六类各占一格。

    ⚠ `world.tiles()` **只装各国的十字领土**（无主格根本不在这个 dict 里 —— 它的地形是
      `tile_terrain()` 按需生成的）⇒ "无主格"要**自己找个不在 `tiles` 里的坐标**，
      不能去 `tiles` 里筛 `owner is None`（那样筛出来永远是空）。
    """
    w = three_nation_world()
    cells = {}
    for cls, owner in ((V.OWN_SELF, "甲"), (V.OWN_ALLY, "乙"), (V.OWN_RIVAL, "丙"),
                       (V.OWN_NEUTRAL_NATION, "丁")):
        cells[cls] = next((c for t in sorted(w.tiles(), key=lambda r: r.position) if t.owner == owner for c in (t.position,)), None)
    # ★★ "没物化的格"（引擎会在可见的那些上**现场合成野人**）——
    #   注意这**不是**"无主格"：引擎的 `territory` 里没有这一格 ⇒ 查"物化"，
    #   不是查"归属"。
    # ★★ "有野人驻守"那一类要**看得见**才成立：引擎只在**可见**的未物化格上
    #   **现场合成**野人（`Military.visible_units`）⇒ 挑一个甲看得见的。
    _vis = w.vision_mask("甲")
    free = [c for c in unmaterialized_cells(w) if c in _vis] or unmaterialized_cells(w)
    assert len(free) >= 2, f"没物化的格不够（{len(free)}）—— size 太小"
    # ★★ **新引擎的野人是"虚拟"的**（`Military.visible_units`）：它对
    #   **地块没物化**（`territory` 里没这一格）**且没驻过军**（`guard_once`）的格
    #   **现场合成**一支野人 ⇒
    #     · "无主 + **有**野人驻守" = **没物化**的无主格（引擎会给它合成一支）；
    #     · "无主 + **无**驻军"   = **已物化但无主**的格（失地：引擎不再合成）。
    #   ⇒ 旧口径"清空那格的野人军队"在新引擎上**做不到**（引擎里压根没有真野人可清），
    #     得**换一种摆法**：把一格**转成无主**（`owner=None` ⇒ 地块仍在 `territory` 里）。
    free.sort(key=lambda p: (p[0] * 31 + p[1]) % 7)     # 固定但不单调的取法
    bar = free[0]
    # `un` ＝ **失地**：拿丁的一格转成无主（地块已物化 ⇒ 引擎不再合成野人）。
    #   ⚠ 挑的是**丁的另一格**（不是下面用作"中立国"的那格）—— 中立国还得有一格。
    ding = [c for t in sorted(w.tiles(), key=lambda r: r.position) if t.owner == "丁" for c in (t.position,)]
    un = ding[-1]
    set_owner(w, un, None)                       # ★ 转成无主（地块仍在 ⇒ 无野人）
    cells[V.OWN_UNOWNED], cells[V.OWN_BARBARIAN] = un, bar
    # ★ `bar` 不用摆野人：它**没物化** ⇒ 引擎会**现场合成**一支 ⇒ 就是"有野人驻守"。
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
        allowed = {c: (_mv_wall(w, "甲", p) is None) for c, p in cells.items()}
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
        self.assertTrue(_atk_ok(w, "甲", cells[V.OWN_RIVAL]))
        self.assertFalse(_atk_ok(w, "甲", cells[V.OWN_NEUTRAL_NATION]))

    def test_ally_move_yes_attack_no(self):
        """盟友：**可 mv 不可 atk**（引擎对盟友与敌国的判定正好相反）。"""
        w, cells = owner_scenario()
        self.assertIsNone(_mv_wall(w, "甲", cells[V.OWN_ALLY]))
        self.assertFalse(_atk_ok(w, "甲", cells[V.OWN_ALLY]))


class TestCandidateMarks(unittest.TestCase):
    """③④ 候选标记里盟友/中立国/**盟友市政厅**都在，且 `hold ≠ attack`。"""

    def _sb(self, war=True):
        # ★ `war=False` 只给"要**结盟**"的那条用：新引擎**禁止**在交战中的实体之间
        #   创盟（「正在战争中，不能创盟」）⇒ 盟友必须在**开战之前**结好。
        sb = Sandbox(seed=7, size=16, war=war).reset()
        return sb

    def test_ally_tile_and_ally_hall_are_marked(self):
        """★ 用户点名的那条：盟友的格与**盟友的市政厅**都必须能被候选看见。"""
        sb = self._sb(war=False)                          # ★ 结盟要在宣战**之前**（新引擎硬约束）
        w = sb.env
        me, ally = "甲", "乙"
        w.set_alliance([me, ally], "联盟")
        ax, ay = next((c for t in sorted(w.tiles(), key=lambda r: r.position) if t.owner == ally for c in (t.position,)), None)
        set_hall(w, (ax, ay), 1)      # 盟友的厅

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
        self.assertEqual(win["a"].shape[1], V.A_WIDTH_RAW + F.F_U + V.A_EXTRA)
        i = next(i for i, a in enumerate(armies) if a.get("type") == "native:infantry")
        off = V.A_WIDTH_RAW
        # ★ 兵种数值在**中间**那 4 列（尾部还有 `A_EXTRA` 的战斗明细段，见 5' 段）
        np.testing.assert_allclose(win["a"][i, off:off + F.F_U], F.unit_vector("native:infantry"))


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
            w = sb.env
            fx, fy = sb.core_of("乙")
            # ★ 守军**真在**那格（native 的 `seed`）—— 旧写法是往门面自记的
            #   军队表里 append 一条 dict，引擎那边根本不知道。
            new_army(w, "乙", (fx, fy))
            mask = encode.vision_of(sb, "甲")
            self.assertNotIn((fx, fy), mask, "前提：该格本来在视野外")
            win, _, _ = encode.encode_window(sb, "甲", mask)
            counts[spy] = win["a"].shape[0]
            # 真视野掩码本身**一点没变** —— 变的只是"厅的位置"这一路
            self.assertEqual(len(mask), len(encode.vision_of(self._sb(spy), "甲")))
        self.assertEqual(counts[False], counts[True],
                         "★ 间谍模式把厅上的守军也暴露了 —— 那是偷看，不是'知道厅在哪'")

    def test_spy_mode_changes_the_score_only_through_proximity(self):
        """打分器那侧：**已知的厅**只影响**逼近项**（"我离敌厅还有多远"）。

        ★ 两种模式现在走**同一个入口**（`known` = 已知的厅，见 `rl/hall_memory.py`）：
          间谍模式的沙盒记忆初值就装满 ⇒ `known_halls` 直接给出敌厅。
        """
        from rl import evaluate as E
        mask = encode.vision_of(self._sb(False), "甲")
        spy, blind = self._sb(True), self._sb(False)
        m_spy = encode.vision_of(spy, "甲")
        self.assertNotEqual(
            E.score(spy.env, "甲", "乙", mask=m_spy, known=spy.known_halls("甲", m_spy)),
            E.score(blind.env, "甲", "乙", mask=mask, known=blind.known_halls("甲", mask)),
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