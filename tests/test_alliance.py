# -*- coding: utf-8 -*-
"""联盟机制测试：盟主（产生/移交/不可退盟/否决/解散）、投票（赞成>反对、弃权）、
战争期禁令、旧档迁移。全合成数据。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402


def make_bloc(w, chief="秦", others=("楚", "齐"), name="北盟"):
    """走正规流程立盟：chief 发起、others 全部接受。返回联盟 dict。"""
    w.propose_bloc(chief, name, list(others))
    p = w.proposals[-1]
    for m in others:
        w.accept_pact(m, p["id"])
    return w.bloc_of(chief)


def make_world(nations=("秦", "楚", "齐", "燕")):
    return mp.World(size=24, seed=3, nations=list(nations))


class TestChief(unittest.TestCase):
    def test_founder_becomes_chief(self):
        w = make_world()
        b = make_bloc(w)
        self.assertEqual(b["chief"], "秦")
        self.assertEqual(w.bloc_chief(b), "秦")

    def test_chief_cannot_leave(self):
        w = make_world()
        make_bloc(w)
        ok, msg = w.bloc_leave("秦")
        self.assertFalse(ok)
        self.assertIn("盟主", msg)
        self.assertIn("秦", w.bloc_of("秦")["members"])

    def test_member_can_leave(self):
        w = make_world()
        make_bloc(w)
        ok, _ = w.bloc_leave("楚")
        self.assertTrue(ok)
        self.assertNotIn("楚", w.bloc_of("秦")["members"])

    def test_last_member_leaving_dissolves_bloc(self):
        w = make_world()
        make_bloc(w, others=("楚",))
        self.assertTrue(w.bloc_leave("楚")[0])
        self.assertIsNone(w.bloc_of("秦"))

    def test_transfer_only_by_chief_and_only_to_member(self):
        w = make_world()
        b = make_bloc(w)
        self.assertFalse(w.bloc_transfer("楚", "齐")[0])        # 非盟主
        self.assertFalse(w.bloc_transfer("秦", "燕")[0])        # 非成员
        self.assertTrue(w.bloc_transfer("秦", "齐")[0])
        self.assertEqual(b["chief"], "齐")
        self.assertTrue(w.bloc_leave("秦")[0])                  # 移交后即可退盟

    def test_chief_succession_on_death(self):
        w = make_world()
        b = make_bloc(w)
        w.tiles = {k: t for k, t in w.tiles.items() if t["owner"] != "秦"}
        self.assertTrue(w._eliminate_if_dead("秦"))
        self.assertEqual(w.bloc_chief(b), "楚")                 # 最早加入的剩余成员

    def test_rename_only_by_chief_and_keeps_votes(self):
        w = make_world()
        b = make_bloc(w)
        w.bloc_join("燕", "北盟")
        vid = w.votes[-1]["id"]
        self.assertFalse(w.bloc_rename("楚", "南盟")[0])          # 非盟主
        self.assertFalse(w.bloc_rename("秦", "")[0])              # 必须起名
        self.assertFalse(w.bloc_rename("秦", "太" * 13)[0])       # 超长
        self.assertFalse(w.bloc_rename("秦", "北 盟")[0])         # 含空格
        self.assertFalse(w.bloc_rename("秦", "北盟")[0])          # 与现名相同
        self.assertTrue(w.bloc_rename("秦", "合纵")[0])
        self.assertEqual(b["name"], "合纵")
        self.assertEqual(w.bloc_by_name("合纵"), b)
        self.assertIsNone(w.bloc_by_name("北盟"))
        self.assertEqual(w.votes[-1]["bloc"], "合纵")             # 进行中的投票跟着改名
        self.assertEqual(w.votes[-1]["id"], vid)

    def test_rename_rejects_duplicate_name(self):
        w = make_world()
        make_bloc(w)
        w.add_nation("林胡")
        w.propose_bloc("林胡", "南盟", ["燕"])          # 第二个联盟占名
        w.accept_pact("燕", w.proposals[-1]["id"])
        self.assertFalse(w.bloc_rename("秦", "南盟")[0])

    def test_dissolve_only_by_chief(self):
        w = make_world()
        make_bloc(w)
        self.assertFalse(w.bloc_dissolve("楚")[0])
        self.assertTrue(w.bloc_dissolve("秦")[0])
        self.assertEqual(w.blocs, [])

    def test_dissolve_refused_while_any_member_at_war(self):
        w = make_world()
        make_bloc(w)
        w.declare_war("秦", "燕")                               # 盟员发起 → 宣战投票
        vid = w.votes[-1]["id"]
        w.cast_vote("楚", vid, True)
        w.cast_vote("齐", vid, True)                            # 通过 → 全盟对燕开战
        ok, msg = w.bloc_dissolve("秦")
        self.assertFalse(ok)
        self.assertIn("战争期间", msg)


class TestVoteRules(unittest.TestCase):
    def _join_vote(self, w, candidate="燕"):
        w.bloc_join(candidate, "北盟")
        return w.votes[-1]["id"]

    def test_yes_must_exceed_no(self):
        w = make_world()
        make_bloc(w)
        vid = self._join_vote(w)
        w.cast_vote("秦", vid, True)
        ok, msg = w.cast_vote("楚", vid, False)
        self.assertTrue(ok)                                     # 1:1 尚未定论
        ok, msg = w.cast_vote("齐", vid, False)
        self.assertFalse(ok)                                    # 1:2 → 未通过
        self.assertNotIn("燕", w.bloc_of("秦")["members"])

    def test_abstain_does_not_block_pass(self):
        w = make_world()
        make_bloc(w)
        vid = self._join_vote(w)
        w.cast_vote("楚", vid, None)                            # 弃权
        w.cast_vote("齐", vid, None)                            # 弃权
        ok, msg = w.cast_vote("秦", vid, True)                  # 1 赞成 0 反对
        self.assertTrue(ok)
        self.assertIn("燕", w.bloc_of("秦")["members"])

    def test_chief_vote_no_is_veto(self):
        w = make_world()
        make_bloc(w)
        vid = self._join_vote(w)
        w.cast_vote("楚", vid, True)
        ok, msg = w.cast_vote("秦", vid, False)                 # 盟主反对 → 否决
        self.assertFalse(ok)
        self.assertIn("否决", msg)
        self.assertEqual(w.votes, [])                           # 议案直接作废
        self.assertNotIn("燕", w.bloc_of("秦")["members"])

    def test_expiry_counts_unvoted_as_abstain(self):
        w = make_world()
        make_bloc(w)
        vid = self._join_vote(w)
        w.cast_vote("秦", vid, True)                            # 只有盟主投了赞成
        w.turn += 1
        w._expire_votes()                                       # 其余到期=弃权 → 1>0 通过
        self.assertIn("燕", w.bloc_of("秦")["members"])

    def test_expiry_with_no_majority_fails(self):
        w = make_world()
        make_bloc(w)
        vid = self._join_vote(w)
        w.cast_vote("秦", vid, True)
        w.cast_vote("楚", vid, False)
        w.turn += 1
        w._expire_votes()                                       # 1:1 → 不通过
        self.assertNotIn("燕", w.bloc_of("秦")["members"])


class TestWartimeRestrictions(unittest.TestCase):
    def _war(self, w):
        make_bloc(w)                                            # 秦/楚/齐 结盟
        w.declare_war("秦", "燕")                               # 盟员发起 → 宣战投票
        vid = w.votes[-1]["id"]
        w.cast_vote("楚", vid, True)
        w.cast_vote("齐", vid, True)                            # 通过 → 全盟对燕开战
        return w

    def test_cannot_found_or_join_alliance_at_war(self):
        w = self._war(make_world())
        self.assertFalse(w.propose_bloc("燕", "南盟", ["楚"])[0])
        self.assertFalse(w.bloc_join("燕", "北盟")[0])

    def test_cannot_guarantee_or_pact_at_war(self):
        w = self._war(make_world())
        self.assertFalse(w.declare_guarantee("燕", "楚")[0])
        self.assertFalse(w.propose_pact("共同防御", "燕", "楚")[0])

    def test_peace_time_allowed(self):
        w = make_world()                                        # 没打仗
        self.assertTrue(w.propose_pact("共同防御", "秦", "燕")[0])
        self.assertTrue(w.declare_guarantee("齐", "燕")[0])


class TestSaveLoad(unittest.TestCase):
    def test_chief_roundtrip(self):
        w = make_world()
        b = make_bloc(w)
        w.bloc_transfer("秦", "齐")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        self.assertEqual(w2.bloc_of("齐")["chief"], "齐")

    def test_save_without_version_rejected(self):
        """去旧存档兼容：无 version（旧档）一律拒载，不再逐字段猜测迁移。"""
        w = make_world()
        make_bloc(w)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("version", None)                           # 模拟旧档（无版本号）
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(mp.SaveFormatError):
                mp.World.load(p)


if __name__ == "__main__":
    unittest.main()
