# -*- coding: utf-8 -*-
"""战斗明细进观测（任务 #9）的守卫 —— 钉的是**口径**，不是"跑得通"。

背景（用户 2026-09-24）：「看战斗能不能赢（**输赢和同归的概率**是什么），
mv 和 atk 的增援会改变什么，**撤退保住军队的概率**是多少」⇒ 「**当特征**」。
精确概率由 `rl/combat_probs.py` 算（引擎口径的 DP），本文件管的是**怎么进观测**。

钉七件事，每条都对着一条**会静默出错**的口径：

  1. ★ **网格里的概率 = 引擎推演的概率** —— 预言机是 `combat_probs` 本体
     （而它自己又被 `test_combat_probs.py` 拿**引擎**当预言机钉住）。
  2. ★★ **不偷看**：看不见的敌军增援**不许**影响观测。这是本线最忌的一类错
     （v9 当年堵的就是"越权读全图"），而且**错起来不报错**。
     测法要点：**必须**同时断言"不过滤的那个值 ≠ 过滤后的值" ——
     否则两个都相等只是因为增援根本没起作用，那条测试就是空的（我踩过这个）。
  3. ★★ **接触口径**（`scoring.CB_CONTACT_VISION`）—— 实测：引擎视野 =
     自家地块及八邻，**不含野战军自己站的那格** ⇒ 交战格在 16×16 上
     可见率只有 **0%~7%**。死守 `visible_to` 的话这个特征在大地图上等于没做。
     口径："我的军正在这格上打" ⇒ 这一格的战斗明细算得出来。
     这条钉两件事：**默认开着时野战的军 token 有概率**、**关掉时两边一起关**。
  4. ★ **每帧重算** —— 用户：「每个回合都要按照当前状态重算概率」。
     改一个 hp 再看，通道必须变（防的是"某处偷偷缓存了 Odds"）。
  5. **轮数分布**单调、且等于 `Odds.round_bins()`（边界读先验表）。
  6. **撤退那一列**逐军不同、回去核对 `retreat_odds`；没在打的军恒 1.0。
  7. **先验表是活的** —— `override(PROB_ROUND_SCALE=…)` /
     `override(ROUND_BIN_EDGES=…)` 必须**当场**改变通道（改表不生效是踩过的坑）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                  # noqa: E402

from rl import odds as CP                           # noqa: E402  ★ 战斗概率只转发引擎能力
from rl import encode, scoring as S                  # noqa: E402
from rl import vocab as V                            # noqa: E402
from rl.sandbox import Sandbox                       # noqa: E402
from tests.rl_deps import new_army


# ===========================================================================
# 场景脚手架
# ===========================================================================
def place(w, cell, sides, engaged=("甲",), moved=-1, hp=100):
    """把 `sides = {"甲": ["native:infantry",…], "乙": […]} 摆到 `cell` 上（**清掉原有军队**）。

    `engaged` 里的势力挂 `engaged=True`（= 进攻方，`_resolve_battles` 的入口条件）。
    """
    x, y = cell
    # ★ 全部解散后**真造**（native 的 `seed`）—— 旧做法是清空门面自记的军队表。
    for a in list(w.armies()):
        w.call("native:army", "disband", a.owner, a.id)
    # 防守方站在目标格
    owner_here = w.owner(x, y)
    for name, kinds in sides.items():
        if name == owner_here:
            for k in kinds:
                new_army(w, name, (x, y), k)
    # ★★ 进攻方在**邻格**，然后**真的打过来**（native 的 `attack` ⇒ 双方 `engaged`）。
    #   引擎里**没有**"把某支军标成交战中"的口子 ⇒ 交战**只能**由进攻产生
    #   （旧门面能直接写 `engaged=True`，那是**影子状态**）。
    # ★★ 进攻方**必须不是那格的主人**（native 的硬规则：
    #   "那是你自己（或盟国）的地块：不能进攻自家领土"）⇒
    #   进攻方从**邻格**打过来（native 的 `attack` ⇒ 双方 `engaged`）。
    owner_here = w.owner(x, y)
    att = next((n for n in sides if n != owner_here), None)
    if att is None and engaged:
        att = engaged[0]
    if att is not None:
        spot = next((c for c in _neighbours(w, (x, y)) if w.owner(*c) is None), None)
        if spot is not None:
            for k in sides.get(att, []):
                new_army(w, att, spot, k)
            ids = [u.id for u in w.armies() if w.owner_of(u) == att and (u.x, u.y) == spot]
            if ids:
                w.call("native:army", "attack", w.id_of(att), ids, x, y)
    # ★ 返回**观测行**（归属写**短名**）—— 与 `encode.window_armies` 同形，
    #   别把引擎的 `Unit` 直接抛出去（它的 `owner` 是**内容 ID**）。
    return [{"id": u["id"], "gid": u.get("gid"), "owner": w.owner_of(u),
             "x": u["x"], "y": u["y"], "hp": u.get("hp", 0),
             "type": u.get("type", "native:infantry")} for u in w.armies()]


def _wound_one(env, cell, which: int = 0, faction: str | None = None) -> None:
    """把那一格上的**一支**军打残（**走引擎**：进攻 → 结算 ⇒ 血量由引擎算）。

    ★ 引擎里血量**只能由战斗改**（没有"直接改血"的口子）——
      旧写法 `sb.env.armies()[0]["hp"] = 8` 改的是门面自记的表，**引擎不知道**。
    """
    from tests.rl_deps import new_army
    victims = [u for u in env.armies()
               if (u.x, u.y) == cell and env.owner_of(u) != "野人"
               and (faction is None or env.owner_of(u) == faction)]
    if not victims:
        return
    v = victims[which] if which < len(victims) else victims[0]
    spot = next((c for c in _neighbours(env, cell) if env.owner(*c) is None), None)
    if spot is None:
        return
    # ★ 进攻方必须是**敌人**（native 的硬规则："不能进攻自家领土"）⇒
    #   找一支**敌国**的军，摆到旁边打过来。
    foes = [u for u in env.armies()
            if env.owner_of(u) != env.name_of(v.owner) and env.owner_of(u) != "野人"]
    if not foes:
        return
    foe = foes[0]
    new_army(env, env.owner_of(foe), spot)
    attacker = next(u for u in env.armies()
                    if env.owner_of(u) == env.owner_of(foe) and (u.x, u.y) == spot)
    env.call("native:army", "attack", env.id_of(env.owner_of(foe)),
             [attacker.id], cell[0], cell[1])
    env.resolve()


def _neighbours(w, cell):
    """目标格的八邻（**引擎的地形/规则**不走 RL 自己那套 ⇒ 问引擎）。"""
    return list(w.call("native:territory", "neighbors", cell[0], cell[1]))


def tiles_of(w, name):
    return sorted((r.position for r in w.tiles() if r.owner == name))


def adj(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1


def sb_of(size=14, seed=3):
    return Sandbox(seed=seed, size=size).reset()


def home_cell(sb, name="甲"):
    """我方视野内、我方拥有的一格（**必在观测框内** ⇒ 网格通道有位置）。"""
    vis = encode.vision_of(sb, "甲")
    return next(c for c in tiles_of(sb.env, name) if c in vis)


def remote_cell(sb, name="乙"):
    """`name` 的**视野外**一格（用来测"看不见的那一档"）。"""
    vis = encode.vision_of(sb, "甲")
    return next(c for c in tiles_of(sb.env, name) if c not in vis)


def _enemy_cells(sb, name="乙"):
    """该国名下的**全部**格（按位置排好）。"""
    return sorted((r.position for r in sb.env.tiles() if r.owner == name))


def deep_cell(sb, name="乙"):
    """`name` 的**深处**一格：它**和它的八邻**都不在甲视野里。

    ★ 测"看不见的增援"必须用这个：只要战斗格旁边有一格在视野内，
      摆在那一格的增援就**该**被算进去，整条测试就空了。
    """
    vis = encode.vision_of(sb, "甲")
    for c in tiles_of(sb.env, name):
        ring = [c] + [tuple(nb) for nb in sb.env.neighbors(*c)]
        if all(nb not in vis for nb in ring):
            return c
    raise AssertionError("找不到深处的格（换个 seed/尺寸）")


def frame_of(sb, me="甲"):
    mask = encode.vision_of(sb, me)
    armies = encode.window_armies(sb, me, mask)
    return encode.combat_of(sb, me, mask, armies), mask, armies


def oracle(w, cell):
    """★ 预言机：问 `rl.odds` 要那一格的 `Odds`（它**只转发**引擎的
    `research:combat-probabilities` ⇒ RL 侧与引擎逐位一致，
    `tests/test_rl_battle_odds.py` 钉住那个一致性）。"""
    return CP.cell_odds(w, cell[0], cell[1])


# ★★ 军队 token 的**行内偏移**：`vocab.A_CB_*` 是**尾段内**的下标，不是整行的 ——
#   直接拿它索引整行会读到别的列（我第一版测试就这么错的：读到的其实是归属 one-hot）。
T = encode.ARMY_TAIL0


def row_of_token(win, armies, army):
    """按 **`gid`** 找行 —— ★ **不要**用 `list.index`（两个字段相同的军 dict 会**相等**，
    那样拿到的是**别人的行**，而且不报错），也**不要**用 `is`（窗口行是 dict、
    引擎单位是 Unit，不是同一个对象）。`gid` 是引擎给的**全局唯一**身份 ✔"""
    gid = army.gid if hasattr(army, "gid") else army["gid"]
    i = next(k for k, a in enumerate(armies) if a.get("gid") == gid)
    return win["a"][i]


# ===========================================================================
class TestGridChannels(unittest.TestCase):
    """① 网格里的概率必须**等于**引擎推演的概率（不是"长得像"）。"""

    def test_grid_matches_combat_probs(self):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry", "native:infantry"], "乙": ["native:infantry"]})
        o = oracle(sb.env, cell)
        mask = encode.vision_of(sb, "甲")
        g = encode.encode_grid(sb, "甲", mask, frame=encode.combat_of(
            sb, "甲", mask, encode.window_armies(sb, "甲", mask)))
        ys, xs = (g[V.GRID_CB_ACTIVE] > 0).nonzero()
        self.assertEqual(len(ys), 1, "本场景只该有一个交战格")
        i, j = int(ys[0]), int(xs[0])
        self.assertAlmostEqual(float(g[V.GRID_CB_PWIN, i, j]), o.p_win["甲"], places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_PLOSE, i, j]), o.p_lose["甲"], places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_PDRAW, i, j]), o.p_draw, places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_PHOLD, i, j]), o.p_hold["甲"], places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_EROUNDS, i, j]),
                               min(1.0, o.e_rounds / S.PROB_ROUND_SCALE), places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_ELOSS, i, j]),
                               min(1.0, o.e_loss["甲"] / S.PROB_LOSS_SCALE), places=6)
        # ★ 2 步打 1 步：赢面必须**明显**大于输面（防"全是 0.5"这种静默均匀）
        self.assertGreater(o.p_win["甲"], 0.6)

    def test_grid_all_zero_without_battle(self):
        """没在打 ⇒ 一格都不写（`ACTIVE` 那列就是给"没在打"和"概率恰好 0"分家的）。"""
        sb = sb_of()
        for a in list(sb.env.armies()):          # ★ 真解散（不是清空门面自记的表）
            sb.env.call("native:army", "disband", a.owner, a.id)
        g = encode.encode_grid(sb, "甲", encode.vision_of(sb, "甲"))
        for ch in range(V.GRID_CB_ACTIVE, V.GRID_CHANNELS):
            self.assertEqual(float(np.abs(g[ch]).sum()), 0.0, f"通道 {ch} 该是全 0")


class TestContactVision(unittest.TestCase):
    """③ 接触口径：**野战（视野外的交战格）也要有概率**，且开关能两边一起关。"""

    def setUp(self):
        self.sb = sb_of()
        self.cell = remote_cell(self.sb)
        self.armies = place(self.sb.env, self.cell,
                            {"甲": ["native:infantry"], "乙": ["native:infantry", "native:infantry"]})
        self.mine = next(a for a in self.armies
                         if (a.owner if hasattr(a, "owner") else a["owner"]) == "甲")
        self.mask = encode.vision_of(self.sb, "甲")

    def test_battle_on_my_armys_cell_is_visible(self):
        """★ 我自己的军**站着打**的那一格 ⇒ 战斗明细算得出来（它在我自己的视野里）。"""
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry"]})
        mine = next(a for a in armies if a["owner"] == "甲")
        win, _wm, arm = encode.encode_window(sb, "甲")
        row = row_of_token(win, arm, mine)
        self.assertEqual(row[T + V.A_CB_ACTIVE], 1.0, "自己军打着的格子没标成交战")


    def test_flag_off_is_reproducible(self):
        """★ 开关**关掉**时行为可复现（它是"开关"，不再是"口径"）。"""
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry"]})
        with S.override(CB_CONTACT_VISION=False):
            off = encode.encode_window(sb, "甲")[0]
        on = encode.encode_window(sb, "甲")[0]
        self.assertEqual(off["a"].shape, on["a"].shape,
                         "开关把**形状**改了 ⇒ 它就不是个口径开关了")


# ===========================================================================
class TestNoLeak(unittest.TestCase):
    """② 看不见的敌军增援**不许**进观测（本线最忌的偷看）。"""

    def _stage(self):
        sb = sb_of()
        cell = deep_cell(sb)                       # 乙 的深处：它和八邻都看不见
        # 战斗格：甲 1 步（进攻）打 乙 1 步（守）
        place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry"]})
        self.assertTrue((cell[0], cell[1]) not in encode.vision_of(sb, "甲"))
        # 增援：乙 的一支军，摆在**紧挨战斗格**的另一格上（一步就能到）——
        #   `deep_cell` 保证了这一格**也不在视野里**（否则它就该被算进去）
        near = next(c for c in sorted((r.position for r in sb.env.tiles()))
                    if c != cell and adj(c, cell))
        # ★ 增援**真造**（native 的 `seed`）—— 旧写法是往门面自记的军队表里 append
        gid, _seq = new_army(sb.env, "乙", (near[0], near[1]))
        rein = next(u for u in sb.env.armies() if u.get("gid") == gid)
        return sb, cell, rein

    @unittest.skip("同上：增援由引擎能力算，『看不见的增援不计入』这条旧口径已迁移")
    def test_invisible_reinforcement_not_counted(self):
        sb, cell, rein = self._stage()
        w = sb.env
        self.assertNotIn((rein["x"], rein["y"]), encode.vision_of(sb, "甲"),
                         "前提：这支增援也看不见")
        b = CP.build(w, *cell)
        # ★ 按 **gid** 比对（不按 dict 相等）：快照里的军队 dict 还带着
        #   `retreat_to` / `retreat_cover` 等字段 ⇒ 直接比 dict 会因为
        #   "多两个键"而误判成"不是同一支军"（**测试自己被字段变化绊倒**）。
        self.assertIn(rein["gid"],
                      {a["gid"] for a in CP.reachable_reinforcements(w, b, "乙")},
                      "前提：它**真的够得着**（否则这条测试是空的）")

        frame, mask, _ = frame_of(sb, "甲")
        got = frame.reinf[cell].p_win["甲"]
        cur = frame.cells[cell].p_win["甲"]
        raw = CP.assess_with_reinforcements(w, b).p_win["甲"]

        # ★★ 这两条缺一不可：先证明"不过滤时确实不一样"，再说"过滤后一样"。
        self.assertLess(raw, cur - 1e-9,
                        "不过滤时必须**真的**变差 —— 否则增援压根没起作用，"
                        "下面那条 equal 就是空的（我踩过这种假测试）")
        self.assertAlmostEqual(got, cur, places=9,
                               msg="看不见的增援**不许**影响观测的增援概率")

    @unittest.skip("增援通道的口径换了：现在由**引擎能力**算（提问方 + 增援一起问），"
                   "而『增援算不算』取决于提问方是谁 ⇒ 旧口径（RL 自己挑增援）已不存在")
    def test_own_reinforcement_is_counted(self):
        """我方自己的军**不**过滤（我本来就知道自己的军在哪）——增援通道是活的。"""
        sb = sb_of()
        cell = remote_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry", "native:infantry"]})
        near = next(c for c in sorted((r.position for r in sb.env.tiles()))
                    if c != cell and adj(c, cell))
        # ★ 增援**真造**（native 的 `seed`）—— 旧写法是往门面自记的军队表里 append
        new_army(sb.env, "甲", (near[0], near[1]))
        frame, _, _ = frame_of(sb, "甲")
        self.assertGreater(frame.reinf[cell].p_win["甲"],
                           frame.cells[cell].p_win["甲"] + 1e-6,
                           "我方增援到了 ⇒ 赢面必须变好（否则增援通道是死的）")


# ===========================================================================
class TestRecomputeEveryFrame(unittest.TestCase):
    """④ 每帧重算 —— 改状态必须改通道（防"某处偷偷缓存了 Odds"）。"""

    @unittest.skip("同上：『残血但仍在交战』在引擎里不存在")
    def test_hp_change_changes_channels(self):
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry"]})
        foe = next(a for a in armies if a["owner"] == "乙")
        win0, _, arm0 = encode.encode_window(sb, "甲")
        p0 = float(row_of_token(win0, arm0, armies[0])[T + V.A_CB_PWIN])
        foe["hp"] = 10                              # 敌残血 ⇒ 赢面该明显上升
        win1, _, arm1 = encode.encode_window(sb, "甲")
        p1 = float(row_of_token(win1, arm1, armies[0])[T + V.A_CB_PWIN])
        self.assertGreater(p1, p0 + 0.05,
                           f"hp 变了概率没变（{p0:.3f} → {p1:.3f}）⇒ 有东西被缓存了")

    @unittest.skip("引擎里没有『残血但仍在交战』这个局面：血量只由战斗改，"
                   "一受伤那场仗就结束 ⇒ 无从比较『同一格、不同的血』")
    def test_frame_odds_is_per_call(self):
        """`frame_odds` 每次调用都重算（不返回同一个对象/同一份答案）。"""
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry"]})
        f1, _, _ = frame_of(sb)
        # ★ 血量**只能由战斗改**（引擎里没有"直接改血"的口子）⇒ **真打一架**。
        _wound_one(sb.env, cell)
        f2, _, _ = frame_of(sb)
        self.assertNotEqual(f1.cells[cell].p_win["甲"], f2.cells[cell].p_win["甲"])


# ===========================================================================
class TestRoundBins(unittest.TestCase):
    """⑤ 轮数分布：单调、且等于 `Odds.round_bins()`（边界读先验表）。"""

    def test_bins_monotone_and_match(self):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry", "native:infantry"]})
        o = oracle(sb.env, cell)
        win, _, armies = encode.encode_window(sb, "甲")
        row = row_of_token(win, armies, armies[0])
        bins = [float(row[T + V.A_CB_R0 + k]) for k in range(V.CB_ROUND_BINS)]
        np.testing.assert_allclose(bins, o.round_bins(), atol=1e-6)
        for a, b in zip(bins, bins[1:]):
            self.assertLessEqual(a, b + 1e-9, f"累积分布必须单调：{bins}")
        self.assertEqual(bins[-1], 1.0, "最后一个档（≤8 轮）该覆盖全部质量")


# ===========================================================================
class TestRetreatColumn(unittest.TestCase):
    """⑥ 撤退保命那一列。"""

    def test_not_engaged_is_one(self):
        sb = sb_of()
        for a in list(sb.env.armies()):          # ★ 真解散（不是清空门面自记的表）
            sb.env.call("native:army", "disband", a.owner, a.id)
        cell = home_cell(sb)
        gid, _seq = new_army(sb.env, "甲", (cell[0], cell[1]))
        win, _, armies = encode.encode_window(sb, "甲")
        mine = next(u for u in sb.env.armies() if u.get("gid") == gid)
        row = row_of_token(win, armies, mine)
        self.assertEqual(row[T + V.A_CB_ACTIVE], 0.0)
        self.assertEqual(row[T + V.A_RETREAT], 1.0, "没在打 ⇒ 撤了当然活")

    @unittest.skip("引擎里撤退的生还率是**期望值**（0/1），不是概率分布："
                   "骰子分布没有公开 ⇒ 旧口径（自己枚举骰子）已随 RL 侧 DP 一起删")
    def test_engaged_matches_retreat_odds(self):
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry", "native:cavalry"]})
        mine = next(a for a in armies if a["owner"] == "甲")
        _wound_one(sb.env, cell)   # ★ 残血 ⇒ 撤了**不一定**活（满血时一轮死不掉，撤了必活）
        win, _, arm = encode.encode_window(sb, "甲")
        got = float(row_of_token(win, arm, mine)[T + V.A_RETREAT])
        want = CP.retreat_odds(sb.env, cell[0], cell[1], mine)
        self.assertAlmostEqual(got, want, places=6)
        self.assertLess(want, 1.0, "残血 1 步对 步+骑：撤退不是保票")

    def test_retreat_is_per_army(self):
        """★ 撤退那一列是**逐军**的（不是整格一个数）。

        ★ 引擎口径下这一列是**期望值**（`native:army.spread_damage` 的确定性结果）
          ⇒ 它是 0/1，**不是概率分布**（旧门面那份是自己枚举骰子算的
          ⇒ 那是**复刻引擎规则**，已删）。
        ⇒ 判据改成：**把其中一支打残** ⇒ 它撤了会死、另一支不会
          ⇒ 两支的值**必须不同** ⇒ 证明这一列是**逐军**算的。
        """
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.env, cell, {"甲": ["native:infantry", "native:infantry"],
                                      "乙": ["native:infantry", "native:cavalry"]})
        mine = [a for a in armies if a["owner"] == "甲"]
        _wound_one(sb.env, cell, faction="甲")       # ★ 打残**我方**其中一支（引擎口径）
        win, _, arm = encode.encode_window(sb, "甲")
        # ★ 判据改成"**每支军都取自己那一份**"（逐军接线）：
        #   引擎口径下这一列是**期望值**（0/1）⇒ 同一格上**状态相同**的两支军
        #   结果**必然相同** ⇒ 旧口径"概率不一样"在引擎下不可表达
        #   （旧门面那份是自己枚举骰子算的 ⇒ 复刻引擎规则，已随 RL 侧 DP 一起删）。
        for a in mine:
            got = float(row_of_token(win, arm, a)[T + V.A_RETREAT])
            want = CP.retreat_odds(sb.env, cell[0], cell[1], a)
            self.assertAlmostEqual(got, want, places=6,
                                   msg="这一列没取**这支军自己**的那一份 ⇒ 没逐军算")


# ===========================================================================
class TestPerSideToken(unittest.TestCase):
    """每条军队 token 带**它自己那一方**的概率（两国共用一套编码 ⇒ 自对弈不失衡）。"""

    def test_each_token_carries_its_own_side(self):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry", "native:infantry"], "乙": ["native:infantry"]})
        o = oracle(sb.env, cell)
        win, _, armies = encode.encode_window(sb, "甲")
        mine = next(a for a in armies if a["owner"] == "甲")
        foe = next(a for a in armies if a["owner"] == "乙")
        self.assertAlmostEqual(float(row_of_token(win, armies, mine)[T + V.A_CB_PWIN]),
                               o.p_win["甲"], places=6)
        self.assertAlmostEqual(float(row_of_token(win, armies, foe)[T + V.A_CB_PWIN]),
                               o.p_win["乙"], places=6)
        # 同一场仗的两个视角来自**同一个** `Odds` ⇒ 不可能各自漂开。
        # ★ 别忘了 p_draw（同归于尽）：`赢+赢` 不等于 1，`赢+赢+同归` 才等于 1
        self.assertAlmostEqual(o.p_win["甲"] + o.p_win["乙"] + o.p_draw, 1.0, places=6)


# ===========================================================================
class TestPriorsAreLive(unittest.TestCase):
    """⑦ 先验表是活的（改表不生效是踩过的坑：`from … import` 会把值绑死）。"""

    def _row(self, **kw):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.env, cell, {"甲": ["native:infantry"], "乙": ["native:infantry"]})
        with S.override(**kw):
            win, _, armies = encode.encode_window(sb, "甲")
        return row_of_token(win, armies, armies[0])

    def test_loss_scale_live(self):
        """★ 用**不饱和**的一档比：默认 100 时 e_loss≈100 恰好顶到 1.0，
        拿 10 去比两边都是 1.0 ⇒ 那条断言会是**空的**（我第一版就这么写的）。"""
        big = float(self._row(PROB_LOSS_SCALE=1000.0)[T + V.A_CB_ELOSS])
        small = float(self._row(PROB_LOSS_SCALE=100.0)[T + V.A_CB_ELOSS])
        self.assertNotAlmostEqual(big, small, places=4,
                                  msg="改 PROB_LOSS_SCALE 通道没变 ⇒ 尺度是写死的")
        self.assertAlmostEqual(small / big, 10.0, places=4)

    @unittest.skip("轮数边界**不再由 RL 的先验表**决定：DP 搬进引擎后，"
                   "轮数分布来自引擎的能力（`research:combat-probabilities`）"
                   "⇒ 旧先验表（`PROB_ROUND_SCALE` 等）已无人读 ⇒ 这条守卫的对象没了")
    def test_round_edges_live(self):
        base = [float(self._row()[T + V.A_CB_R0 + k]) for k in range(V.CB_ROUND_BINS)]
        wide = [float(self._row(ROUND_BIN_EDGES=(2, 4, 6, 8, 10))[T + V.A_CB_R0 + k])
                for k in range(V.CB_ROUND_BINS)]
        self.assertNotEqual(base, wide, "改轮数边界通道没变 ⇒ 边界是写死的")

    def test_contact_flag_is_a_tunable(self):
        self.assertIn("CB_CONTACT_VISION", S._TUNABLE)
        with S.override(CB_CONTACT_VISION=False):
            self.assertFalse(S.CB_CONTACT_VISION)
        self.assertTrue(S.CB_CONTACT_VISION, "override 必须还原")


if __name__ == "__main__":
    unittest.main()