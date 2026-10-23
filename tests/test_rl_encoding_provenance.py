# -*- coding: utf-8 -*-
"""★★ **信息来源核对**（报告 §6）：军队所属方 / 不可见信息 / 候选去重。

用户 2026-10-21 的复盘报告（`~/rl-tactics-repair-upgrade-report.md` §6）查出来的三件事：

  §6.1 **军队所属方被"脚下那格的格主"顶替** ——
        敌军站在我方领土上时，它被编码成"我的军"。
        军队属于谁**只由它自己决定**（`army.owner`），跟它站在哪上面无关。
  §6.2 **不可见候选泄露归属** —— `candidate_batch()` 无条件用当前格主填归属，
        **在动作选择之前**就把真值喂进了网络（"允许盲探、事后看回执"是另一回事）。
  §6.3 **重复候选** —— 同一个动作元组出现两次 ⇒ 候选分布被静默偏置。

★ 口径不许变：盲探动作**保留**、合法动作的**集合**不变、只去掉**完全相同**的重复。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from tests.rl_deps import has_torch, torch_or_skip
from tests.rl_deps import new_army, set_owner
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None

if has_torch():
    from rl import encode as E                           # noqa: E402
    from rl import vocab as V                           # noqa: E402
    from rl.sandbox import Sandbox                      # noqa: E402
else:
    Sandbox = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _relocate(sb, name: str, cell) -> None:
    """把该国的一支军挪到 `cell`（**走引擎**：原地的解散 + 目标地重建）。

    ★ 引擎的 `move` 只能走一格，而这些用例要跨图挪 ⇒ 用"重建"这一招。
      旧写法是直接改门面自记的军队 dict ⇒ **引擎那边根本不知道**，
      挪了也看不见、也进不了窗口（用例自己都写了这句话）。
    """
    from tests.rl_deps import new_army
    units = sb.armies_of(name)
    if not units:
        return
    old = units[0]
    sb.env.call("native:army", "disband", sb.env.id_of(name), old.id)
    new_army(sb.env, name, (cell[0], cell[1]))
    # ★ 返回**新建的那支**（原地的已解散；`armies_of()[0]` 拿到的是**别的一支**）
    return next(u for u in sb.armies_of(name) if (u["x"], u["y"]) == (cell[0], cell[1]))


def _sb(seed=0, n=2, war=True, halls_known=False, size=8, territory=True):
    """★ `territory=False` ⇒ 只留十字那 5 格 ⇒ **大片无主地**（测"无主"用）。"""
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=20, war=war,
                   halls_known=halls_known, territory=territory).reset(
                       random_starts=False)


def _owner_class_of_row(win, row):
    """从军队 token 行里读出归属类别（`vocab.OWN_*`）。"""
    blk = win["a"][row][V.A_OWNER0:V.A_OWNER0 + len(V.OWNER_CHANNELS)]
    return int(np.argmax(blk))


def _norm(obs):
    return {k: ({kk: np.asarray(vv).tolist() for kk, vv in v.items()}
                if isinstance(v, dict) else np.asarray(v).tolist())
            for k, v in obs.items()}


def _diffs(x, y, path=""):
    """两份结构里**不一样**的地方。"""
    out = []
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            if k not in x or k not in y:
                out.append(f"{path}.{k}: 缺一边")
            else:
                out += _diffs(x[k], y[k], f"{path}.{k}")
        return out
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        if len(x) != len(y):
            out.append(f"{path}: 长度 {len(x)} vs {len(y)}")
            return out
        for i, (a, b) in enumerate(zip(x, y)):
            out += _diffs(a, b, f"{path}[{i}]")
        return out
    a, b = np.asarray(x), np.asarray(y)
    if a.shape != b.shape:
        out.append(f"{path}: 形状 {a.shape} vs {b.shape}")
    elif not np.array_equal(a, b):
        d = np.abs(a.astype(float) - b.astype(float))
        out.append(f"{path}: 值不同（最大差 {d.max():.4g}）")
    return out


class TestArmyOwnerIsTheArmyOwner(unittest.TestCase):
    """§6.1：**所属方来自 `army.owner`**，不来自脚下那格的格主。"""

    def test_my_army_on_enemy_land_is_still_mine(self):
        """★ 我军站在**敌国领土**上 ⇒ 仍然是"我的军"。"""
        sb = _sb()
        a = sb.armies_of("甲")[0]
        a["x"], a["y"] = sb.core_of("乙")
        win, _wm, armies = E.encode_window(sb, "甲", E.vision_of(sb, "甲"))
        row = next(i for i, u in enumerate(armies) if u.get("gid") == a.get("gid"))
        self.assertEqual(_owner_class_of_row(win, row), V.OWN_SELF,
                         "我军在敌地却不是'我的' ⇒ 归属被脚下格主顶替了")

    def test_enemy_army_on_my_land_is_still_enemy(self):
        """★★ 敌军站在**我方领土**上 ⇒ 仍然是"敌国军"（原来这里是"我的军"！）。"""
        sb = _sb()
        # ★ 挪军走**引擎**（native 的 `move` 只能走一格 ⇒ 这里"在目标格重建一支"
        #   + 解散原来那支）；旧写法是改门面自记的 dict ⇒ 引擎那边根本不知道。
        a = _relocate(sb, "乙", sb.core_of("甲"))
        win, _wm, armies = E.encode_window(sb, "甲", E.vision_of(sb, "甲"))
        row = next(i for i, u in enumerate(armies) if u.get("gid") == a.get("gid"))
        self.assertEqual(_owner_class_of_row(win, row), V.OWN_RIVAL,
                         "敌军在我地却被写成'我的军' ⇒ 归属被脚下格主顶替了")

    def test_allied_army_on_third_country_land_is_ally(self):
        """★ 盟军在**第三国土地**上 ⇒ 仍是"盟友"（外交关系决定，不看格主）。"""
        sb = _sb(n=3, war=False)
        sb.set_alliance(("北方同盟", ["甲", "乙"]))
        # ★ 乙的军站在**自己那块地上**，而那块地**归丙** ⇒
        #   "盟军站在**第三国土地**上"。
        #   ★ 为什么用"改格主"而不是"挪军"：视野掩码是**引擎**按单位位置算的，
        #   直接改门面那份 dict **挪不动引擎里的单位** ⇒ 挪了也看不见、也进不了窗口。
        ex, ey = sb.core_of("乙")
        # ★ 改格主走**引擎**（`territory.transfer`）—— 改门面自记的表，引擎不知道。
        set_owner(sb.env, (ex, ey), "丙")
        a = sb.armies_of("乙")[0]
        win, _wm, armies = E.encode_window(sb, "甲", E.vision_of(sb, "甲"))
        row = next(i for i, u in enumerate(armies) if u.get("gid") == a.get("gid"))
        self.assertEqual(_owner_class_of_row(win, row), V.OWN_ALLY,
                         "盟军在第三国土地上没被认成盟友 ⇒ 类别被格主顶替了")

    def test_national_army_on_unowned_land_keeps_its_identity(self):
        """★ **无主地上的国家军队** ⇒ 不能变成"无主"（那会把它当野人/空地）。"""
        sb = _sb()
        a = sb.armies_of("乙")[0]
        free = next((x, y) for x in range(sb.size) for y in range(sb.size)
                     if sb.env.owner(x, y) is None
                     and (x, y) != (a["x"], a["y"]))
        a = _relocate(sb, "乙", free)
        win, _wm, armies = E.encode_window(sb, "甲", E.vision_of(sb, "甲"))
        row = next(i for i, u in enumerate(armies) if u.get("gid") == a.get("gid"))
        cls = _owner_class_of_row(win, row)
        self.assertNotEqual(cls, V.OWN_UNOWNED,
                            "无主地上的国家军队被写成'无主' ⇒ 国家身份丢了")
        self.assertNotEqual(cls, V.OWN_BARBARIAN,
                            "无主地上的国家军队被写成'野人' ⇒ 国家身份丢了")

    def test_changing_the_ground_owner_does_not_change_the_army_owner(self):
        """★★ **差分**：只改脚下格主 ⇒ 军队的归属类别**不许变**。"""
        sb_a = _sb()
        sb_b = _sb()
        a_a = sb_a.armies_of("甲")[0]
        a_b = sb_b.armies_of("甲")[0]
        for sb, a in ((sb_a, a_a), (sb_b, a_b)):
            a["x"], a["y"] = sb.core_of("乙")
        # ★ 只改**脚下那格**的主人（军队自己不变）
        set_owner(sb_b.env, sb_b.core_of("乙"), "甲")
        wa, _m1, _a1 = E.encode_window(sb_a, "甲", E.vision_of(sb_a, "甲"))
        wb, _m2, _a2 = E.encode_window(sb_b, "甲", E.vision_of(sb_b, "甲"))
        ra = next(i for i, u in enumerate(_a1) if u.get("gid") == a_a.get("gid"))
        rb = next(i for i, u in enumerate(_a2) if u.get("gid") == a_b.get("gid"))
        self.assertEqual(_owner_class_of_row(wa, ra), _owner_class_of_row(wb, rb),
                         "只改了脚下格主，军队的归属类别却变了")


class TestInvisibleCandidateDoesNotLeakOwnership(unittest.TestCase):
    """§6.2：看不见且没记住 ⇒ 归属**未知**（既不写真值，也不伪装成无主）。"""

    def _probe(self, sb):
        """甲的一支军在 (5,5)；试探格 (6,5)。

        ★ 挪军走**引擎**（原地解散 + 目标地重建）—— 旧写法是改门面自记的 dict，
          引擎那边根本不知道 ⇒ 挪了也看不见、也进不了窗口。
        """
        return _relocate(sb, "甲", (5, 5))

    def test_invisible_unknown_candidate_owner_is_unknown(self):
        """★ 看不见、也不在厅记忆里 ⇒ 归属 one-hot **整块留空**。"""
        sb = _sb()
        self._probe(sb)
        mask = E.vision_of(sb, "甲")
        cell = (6, 5)
        self.assertNotIn(cell, mask)
        self.assertNotIn(cell, sb.known_halls("甲", mask))
        acts = [x for x in sb.legal() if x[2:] == cell]
        self.assertTrue(acts, "用例前提：(6,5) 该是合法候选")
        r = E.candidate_batch(sb, "甲", acts=acts, mask=mask)
        own = r["cand_marks"][:, V.CAND_OWN0:V.CAND_OWN0 + len(V.OWNER_CHANNELS)]
        self.assertTrue(np.allclose(own, 0.0),
                        "看不见的候选却带了归属 one-hot ⇒ 真值泄露")
        self.assertTrue(np.allclose(r["cand_marks"][:, V.CAND_OWN_KNOWN], 0.0),
                        "看不见的候选却标成'归属已知'")

    def test_unknown_is_distinguishable_from_every_real_class(self):
        """★★ **"未知"必须与每一个真实类别都分得开**（包括"无主"）。

        ★ 口径：未知 ⇒ 归属 one-hot **一个类都不写**（整块留空）且 `CAND_OWN_KNOWN=0`；
          已知 ⇒ **恰好写一个类**且 `CAND_OWN_KNOWN=1`。
          ⇒ "未知"既不是"无主"、也不是"敌国"、也不是别的任何一种 ⇒ 不会撒谎。
        """
        sb = _sb()
        self._probe(sb)
        mask = E.vision_of(sb, "甲")
        # ---- ① 未知：看不见、没记住 ----
        cell = (6, 5)
        acts = [x for x in sb.legal() if x[2:] == cell]
        self.assertTrue(acts, "用例前提：(6,5) 该是合法候选")
        r = E.candidate_batch(sb, "甲", acts=acts, mask=mask)
        own = r["cand_marks"][0][V.CAND_OWN0:V.CAND_OWN0 + len(V.OWNER_CHANNELS)]
        self.assertTrue(np.allclose(own, 0.0),
                        "未知却写了归属 ⇒ 撒谎（至少有一类是真的）")
        self.assertAlmostEqual(float(r["cand_marks"][0][V.CAND_OWN_KNOWN]), 0.0,
                               places=6)
        # ---- ② 已知：看得见的格 ⇒ 恰好一个类 ----
        vis_acts = [x for x in sb.legal() if (x[2], x[3]) in mask]
        self.assertTrue(vis_acts, "用例前提：该有看得见的候选")
        rv = E.candidate_batch(sb, "甲", acts=vis_acts, mask=mask)
        for i in range(rv["cand_marks"].shape[0]):
            own_v = rv["cand_marks"][i][V.CAND_OWN0:V.CAND_OWN0 + len(V.OWNER_CHANNELS)]
            self.assertAlmostEqual(float(own_v.sum()), 1.0, places=6,
                                   msg="看得见的候选必须**恰好**一个归属类")
            self.assertAlmostEqual(float(rv["cand_marks"][i][V.CAND_OWN_KNOWN]), 1.0,
                                   places=6)

    def test_unowned_is_its_own_class(self):
        """★ "无主"是**独立的一类**（不是"未知"的别名）。"""
        sb = _sb()
        # ★ 造一格"无主且无野人"：拿一个**已物化**的格（野人不会在那儿），
        #   把它的主人清空 ⇒ 它就该是"无主"而不是"野人"。
        core = sb.core_of("甲")               # ★ 先记住坐标（清了主人就找不到了）
        set_owner(sb.env, core, None)          # ★ 改格主走引擎（transfer）
        cls = E._owner_class_of(sb.env, "甲", None, core[0], core[1],
                                None, set())
        self.assertEqual(cls, V.OWN_UNOWNED,
                         "无主的格没被归成'无主' ⇒ 类别体系乱了")
        self.assertNotEqual(cls, V.OWN_BARBARIAN)

    def test_two_indistinguishable_worlds_give_identical_observation(self):
        """★★★ **差分验收**（报告 §6.2 的验收口径）。

        两份**对观察者不可区分**的世界：只改「看不见、也没被获知」的那个格子的主人
        ⇒ **动作前的观测必须逐位相同**。
        ★ 候选集合也必须相同（否则"候选出现条件"本身就在泄露）。
        """
        def build(owner):
            # ★ 三国局：那个格子要分别归**乙**和**丙** ⇒ 这两家都得**在册**
            #   （引擎的 `territory.create` 拒"不存在的国家"——那是引擎的硬约束）。
            sb = _sb(n=3)
            self._probe(sb)
            set_owner(sb.env, (6, 5), owner)   # ★ 改格主走引擎（transfer/create）
            return sb

        sb_a = build("乙")
        sb_b = build("丙")
        mask = E.vision_of(sb_a, "甲")
        self.assertNotIn((6, 5), mask)
        self.assertEqual(sb_a.legal(), sb_b.legal(),
                         "只改了一个看不见的格子的主人，候选集合却变了")
        ds = _diffs(_norm(E.obs_of(sb_a, "甲")), _norm(E.obs_of(sb_b, "甲")))
        self.assertEqual(ds, [], f"两份不可区分的世界给出了不同的观测：{ds[:8]}")

    def test_blind_probe_actions_are_preserved(self):
        """★ **盲探动作必须保留**（看不见的格照样给 move/attack）。"""
        sb = _sb()
        self._probe(sb)
        mask = E.vision_of(sb, "甲")
        cell = (6, 5)
        kinds = {a[1] for a in sb.legal() if a[2:] == cell}
        self.assertIn("move", kinds, "盲探的 move 没了")
        # ★ "盲探的 **attack**"在引擎里**不存在**：空格是**可以走进去**的格
        #   （`native:army.reachable(attack=True)` 只列"走不通、但够得着"的终点）
        #   ⇒ 对空格，引擎给的是 move，不是 attack ⇒ 这里只断 move 还在。
        #   （要断"看不见也能打"，得挑一个**有敌军**的格——那是另一条规则。）


class TestNoDuplicateCandidates(unittest.TestCase):
    """§6.3：候选**唯一**，且不同动作类型都保留。"""

    def test_legal_actions_are_unique(self):
        """★ `legal()` 里不许有**完全相同**的动作元组。"""
        for seed in range(5):
            sb = _sb(seed=seed)
            sb.armies_of("甲")[0].update(x=5, y=5)
            acts = sb.legal()
            self.assertEqual(len(acts), len(set(acts)),
                             f"seed={seed}：候选里有重复动作")

    def test_move_and_attack_both_preserved(self):
        """★ 去重**不许**把 `move` 与 `attack` 合并（语义不同）。"""
        sb = _sb()
        # ★ 挪军走**引擎**（旧写法是改门面自记的 dict ⇒ 引擎不知道）
        _relocate(sb, "甲", (5, 5))
        # ★ 要有"attack"候选，得有一个**够得着、且上面有敌军**的格
        #   （引擎的 `reachable(attack=True)` 只列"走不通、但够得着"的终点；
        #    空格是**能走进去**的 ⇒ 它给的是 move，不是 attack）。
        new_army(sb.env, "乙", (5, 6))
        acts = sb.legal()
        kinds = {a[1] for a in acts}
        self.assertIn("move", kinds)
        self.assertIn("attack", kinds)
        self.assertIn("hold", kinds)

    def test_candidate_features_align_row_by_row(self):
        """★ 候选特征**逐行对应**动作（去重后行数与动作数一致）。"""
        sb = _sb()
        acts = sb.legal()
        r = E.candidate_batch(sb, "甲", acts=acts)
        self.assertEqual(r["cand_marks"].shape[0], len(acts))
        self.assertEqual(r["type_idx"].shape[0], len(acts))
        for i, (_aid, kind, _x, _y) in enumerate(acts):
            self.assertEqual(int(r["type_idx"][i]), V.KIND_INDEX[kind],
                             f"第 {i} 行的动作类型与动作表对不上")

    def test_duplicate_input_is_rejected_loudly(self):
        """★ 别人传进来一份**带重复**的候选表 ⇒ **当场报错**（不静默偏置）。"""
        sb = _sb()
        acts = sb.legal()
        with self.assertRaises(ValueError):
            E.candidate_batch(sb, "甲", acts=list(acts) + [acts[0]])


if __name__ == "__main__":
    unittest.main()
