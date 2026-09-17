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

    def test_peace_authority_follows_chief_transfer(self):
        """#7 回归：盟主移交后，"联盟主体"必须跟着换人——
        旧创始盟主（members[0]）不得再出面议和，新盟主出面须走联盟投票，
        绝不允许不经表决直接落下 peace_offer（那等于绕开全盟私签和平）。"""
        w = make_world()
        make_bloc(w, chief="秦", others=("楚",))
        ok, msg = w.declare_war("燕", "秦")                    # 燕（非成员）直接宣战
        self.assertTrue(ok, msg)
        self.assertTrue(w.bloc_transfer("秦", "楚")[0])        # 盟主移交给楚
        # 旧创始盟主秦：已非盟主 → 无出面资格
        ok1, m1 = w.offer_peace("秦", "燕", "white")
        self.assertFalse(ok1, m1)
        self.assertIn("楚", m1)                                # 提示真正的代表
        # 新盟主楚：有资格，但必须发起投票而不是直接落 offer
        ok2, m2 = w.offer_peace("楚", "燕", "white")
        self.assertTrue(ok2, m2)
        self.assertIn("投票", m2)
        self.assertEqual([p for p in w.peace_offers if p["a"] == "楚"], [])

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


class TestDiplomaticEntity(unittest.TestCase):
    """★ 2026-09-18 外交改革：**签约主体是外交实体**（独立国家 | 联盟）。

    在盟的国家不是实体——保障独立/共同防御/宣战/议和一律由联盟出面、且须联盟投票；
    成员个人签不了也撤不了任何条约。这一批用例就是那几条规矩的可执行版本。
    """

    def test_entity_of_independent_vs_bloc(self):
        w = make_world()
        self.assertEqual(w.entity_of("燕"), "国:燕")            # 独立国家 = 自己的实体
        make_bloc(w)
        self.assertEqual(w.entity_of("秦"), "盟:北盟")          # 在盟 = 联盟实体
        self.assertEqual(w.entity_of("齐"), "盟:北盟")
        self.assertIn(mp.ent_bloc("北盟"), w.entities())
        self.assertNotIn(mp.ent_nation("秦"), w.entities())    # 成员不是实体

    def test_entity_members_and_chief(self):
        w = make_world()
        make_bloc(w)
        ent = w.entity_of("秦")
        self.assertEqual(sorted(w.entity_members(ent)), sorted(["秦", "楚", "齐"]))
        self.assertEqual(w.entity_chief(ent), "秦")
        w.bloc_transfer("秦", "齐")
        self.assertEqual(w.entity_chief(ent), "齐")            # 代表跟着盟主走

    def test_pact_with_own_member_refused(self):
        w = make_world()
        make_bloc(w)
        ok, msg = w.declare_guarantee("秦", "楚")               # 同一实体内部
        self.assertFalse(ok)
        self.assertIn("同属一个实体", msg)
        self.assertEqual(w.pacts, [])

    def test_member_guarantee_routed_to_bloc_vote(self):
        """★ 成员的保障动作不直接生效：先落成联盟投票，通过才由**联盟**签。"""
        w = make_world()
        make_bloc(w)
        ok, msg = w.declare_guarantee("楚", "燕")
        self.assertTrue(ok)
        self.assertIn("投票", msg)
        self.assertEqual(w.pacts, [])                           # 还没表决 → 无条约
        v = w.votes[-1]
        self.assertEqual(v["kind"], "缔约")
        self.assertEqual(v["payload"]["A"], mp.ent_bloc("北盟"))  # 签约方是联盟
        self.assertEqual(v["payload"]["B"], mp.ent_nation("燕"))
        w.cast_vote("秦", v["id"], True)
        w.cast_vote("齐", v["id"], True)
        self.assertTrue(w.has_pact("保障", mp.ent_bloc("北盟"), mp.ent_nation("燕")))
        self.assertFalse(w.has_pact("保障", mp.ent_nation("楚"), mp.ent_nation("燕")))

    def test_member_pact_proposal_routed_to_bloc_vote(self):
        w = make_world()
        make_bloc(w)
        ok, msg = w.propose_pact("共同防御", "楚", "燕")
        self.assertTrue(ok)
        self.assertIn("投票", msg)
        self.assertEqual([p for p in w.proposals if p["kind"] == "共同防御"], [])
        v = w.votes[-1]
        self.assertTrue(v["payload"].get("offer"))
        w.cast_vote("秦", v["id"], True)
        w.cast_vote("齐", v["id"], True)
        offers = [p for p in w.proposals if p["kind"] == "共同防御"]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["A"], mp.ent_bloc("北盟"))   # 邀约以联盟名义发出
        self.assertEqual(offers[0]["B"], mp.ent_nation("燕"))

    def test_member_cannot_withdraw_pact_alone(self):
        """成员也不能单独解约——同样要过联盟投票。"""
        w = make_world()
        make_bloc(w)
        w.declare_guarantee("秦", "燕")
        vid = w.votes[-1]["id"]
        w.cast_vote("秦", vid, True)
        w.cast_vote("齐", vid, True)                            # 条约已签（实体级）
        ok, msg = w.cancel_guarantee("楚", "燕")
        self.assertTrue(ok)
        self.assertIn("投票", msg)
        self.assertTrue(w.has_pact("保障", mp.ent_bloc("北盟"), mp.ent_nation("燕")))

    def test_chief_veto_on_pact_vote(self):
        w = make_world()
        make_bloc(w)
        w.declare_guarantee("楚", "燕")
        vid = w.votes[-1]["id"]
        ok, msg = w.cast_vote("秦", vid, False)                 # 盟主一票否决
        self.assertFalse(ok)
        self.assertIn("否决", msg)
        self.assertEqual(w.votes, [])
        self.assertEqual(w.pacts, [])

    def test_attacking_member_drags_whole_bloc(self):
        """★ 打成员 = 打联盟：全盟自动落在守侧，不需要投票（防守无须表决）。"""
        w = make_world()
        make_bloc(w)
        ok, msg = w.declare_war("燕", "楚")
        self.assertTrue(ok, msg)
        war = w.wars[0]
        self.assertEqual(war["atk"], "燕")
        self.assertEqual(war["def"], "秦")                      # 防御主导 = 盟主
        self.assertEqual(sorted(war["followers"]), sorted(["楚", "齐"]))
        self.assertIn("楚", msg)

    def test_bloc_guarantee_pulls_bloc_into_defense(self):
        """★ 联盟保障某独立国 → 谁打它，全盟按实体闭包参战。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        make_bloc(w)
        w.declare_guarantee("秦", "燕")
        vid = w.votes[-1]["id"]
        w.cast_vote("秦", vid, True)
        w.cast_vote("齐", vid, True)
        self.assertTrue(w.has_pact("保障", mp.ent_bloc("北盟"), mp.ent_nation("燕")))
        ok, msg = w.declare_war("赵", "燕")
        self.assertTrue(ok, msg)
        war = w.wars[0]
        self.assertEqual(war["atk"], "赵")
        self.assertEqual(sorted(war["followers"]), sorted(["秦", "楚", "齐"]))

    def test_joining_bloc_voids_personal_pacts(self):
        """★ 入盟即放弃个人条约（两个方向都作废）。"""
        w = make_world()
        self.assertTrue(w.declare_guarantee("燕", "秦")[0])     # 燕保障秦
        self.assertTrue(w.declare_guarantee("燕", "楚")[0])     # 燕又保障楚
        self.assertEqual(len(w.pacts), 2)
        make_bloc(w, chief="秦", others=("楚", "燕"))            # 燕入盟
        self.assertEqual([p for p in w.pacts if "国:燕" in (p["a"], p["b"])], [])

    def test_joining_bloc_voids_incoming_pacts(self):
        """别人给你的个人条约也一样作废——留着的条约会指向一个不再是实体的国家。"""
        w = make_world()
        self.assertTrue(w.declare_guarantee("燕", "秦")[0])     # 燕(外部)保障 秦
        make_bloc(w, chief="秦", others=("楚",))
        self.assertEqual(w.pacts, [])

    def test_bloc_to_bloc_pact_needs_both_votes(self):
        """★ 联盟↔联盟：两边各自过自己的联盟投票，缺一边签不成。"""
        w = mp.World(size=16, seed=3, nations=["秦", "楚", "齐", "赵"])
        make_bloc(w, chief="秦", others=("楚",), name="北盟")
        make_bloc(w, chief="齐", others=("赵",), name="南盟")
        w.propose_pact("共同防御", "秦", "齐")
        v1 = w.votes[-1]
        w.cast_vote("秦", v1["id"], True)
        w.cast_vote("楚", v1["id"], True)                      # 北盟通过 → 发出邀约
        self.assertEqual(w.pacts, [])                          # 南盟还没表态
        offer = [p for p in w.proposals if p["kind"] == "共同防御"][0]
        self.assertEqual(offer["A"], mp.ent_bloc("北盟"))
        self.assertEqual(offer["B"], mp.ent_bloc("南盟"))
        ok, msg = w.accept_pact("齐", offer["id"])
        self.assertTrue(ok)
        self.assertIn("表决", msg)                              # 接受 = 提交南盟表决
        self.assertEqual(w.pacts, [])
        v2 = [v for v in w.votes if v["kind"] == "缔约"][-1]
        w.cast_vote("齐", v2["id"], True)
        w.cast_vote("赵", v2["id"], True)
        self.assertTrue(w.has_pact("共同防御", mp.ent_bloc("北盟"), mp.ent_bloc("南盟")))

    def test_rename_syncs_pact_refs(self):
        """★ 联盟改名必须同步条约里的实体 id（实体 id 里嵌着联盟名）。"""
        w = make_world()
        make_bloc(w)
        w.declare_guarantee("秦", "燕")
        vid = w.votes[-1]["id"]
        w.cast_vote("秦", vid, True)
        w.cast_vote("齐", vid, True)
        self.assertTrue(w.has_pact("保障", mp.ent_bloc("北盟"), mp.ent_nation("燕")))
        self.assertTrue(w.bloc_rename("秦", "合纵")[0])
        self.assertTrue(w.has_pact("保障", mp.ent_bloc("合纵"), mp.ent_nation("燕")))
        self.assertFalse(w.has_pact("保障", mp.ent_bloc("北盟"), mp.ent_nation("燕")))

    def test_death_voids_pacts_of_that_entity(self):
        w = make_world()
        self.assertTrue(w.declare_guarantee("燕", "秦")[0])
        self.assertEqual(len(w.pacts), 1)
        w.tiles = {k: t for k, t in w.tiles.items() if t["owner"] != "燕"}
        self.assertTrue(w._eliminate_if_dead("燕"))
        self.assertEqual(w.pacts, [])

    def test_bloc_war_vote_target_is_entity(self):
        w = make_world()
        make_bloc(w)
        w.declare_war("楚", "燕")
        v = w.votes[-1]
        self.assertEqual(v["kind"], "宣战")
        self.assertEqual(v["payload"]["target"], mp.ent_nation("燕"))   # 实体 id
        w.cast_vote("秦", v["id"], True)
        w.cast_vote("齐", v["id"], True)
        war = w.wars[0]
        self.assertEqual(war["atk"], "秦")                     # 进攻主导 = 盟主
        self.assertEqual(sorted(war["atk_followers"]), sorted(["楚", "齐"]))


class TestWartimeLock(unittest.TestCase):
    """★ 战时锁死（2026-09-18）：**不退盟、不解散**——盟员被锁到整盟停战为止。"""

    def _warring_bloc(self):
        w = make_world()
        make_bloc(w)
        w.declare_war("秦", "燕")
        vid = w.votes[-1]["id"]
        w.cast_vote("楚", vid, True)
        w.cast_vote("齐", vid, True)                           # 全盟对燕开战
        return w

    def test_member_cannot_leave_at_war(self):
        w = self._warring_bloc()
        ok, msg = w.bloc_leave("楚")
        self.assertFalse(ok)
        self.assertIn("战争期间", msg)
        self.assertIn("楚", w.bloc_of("秦")["members"])

    def test_chief_cannot_dissolve_at_war(self):
        w = self._warring_bloc()
        ok, msg = w.bloc_dissolve("秦")
        self.assertFalse(ok)
        self.assertIn("战争期间", msg)
        self.assertTrue(w.blocs)

    def test_leave_allowed_again_after_war_ends(self):
        """锁死只在战时——停战后出口重新打开。"""
        w = self._warring_bloc()
        self.assertFalse(w.bloc_leave("楚")[0])
        w.wars = []                                            # 议和（整条战线停战）
        self.assertTrue(w.bloc_leave("楚")[0])
        self.assertTrue(w.bloc_dissolve("秦")[0])

    def test_lock_covers_members_who_did_not_start_it(self):
        """锁死看的是"任一成员在交战"，不是"我要不要打"。"""
        w = make_world()
        make_bloc(w)
        w.declare_war("燕", "秦")                               # 燕(独立)打盟主 → 全盟守侧
        self.assertTrue(w.wars)
        self.assertFalse(w.bloc_leave("齐")[0])                 # 齐自己没发起，但一样锁死


class TestEntityPactTable(unittest.TestCase):
    """条约表的实体级原语（面板/闭包/亡国清理都建在它上面）。"""

    def test_pact_direction_and_symmetry(self):
        w = make_world()
        w.declare_guarantee("燕", "秦")                         # 单向：燕保障秦
        self.assertTrue(w.has_pact("保障", mp.ent_nation("燕"), mp.ent_nation("秦")))
        self.assertFalse(w.has_pact("保障", mp.ent_nation("秦"), mp.ent_nation("燕")))
        self.assertEqual(w.guaranteed_by(mp.ent_nation("燕")), [mp.ent_nation("秦")])
        self.assertEqual(w.guarantors_of(mp.ent_nation("秦")), [mp.ent_nation("燕")])

    def test_defense_pact_is_symmetric(self):
        w = make_world()
        self.assertTrue(w.propose_pact("共同防御", "秦", "燕")[0])
        w.accept_pact("燕", w.proposals[-1]["id"])
        self.assertTrue(w.has_pact("共同防御", mp.ent_nation("秦"), mp.ent_nation("燕")))
        self.assertTrue(w.has_pact("共同防御", mp.ent_nation("燕"), mp.ent_nation("秦")))
        self.assertEqual(w.defense_partners_of(mp.ent_nation("燕")), [mp.ent_nation("秦")])

    def test_defense_pact_upgrades_and_drops_guarantee(self):
        """保障 < 共同防御：缔结高档自动解除低档（沿用旧口径）。"""
        w = make_world()
        w.declare_guarantee("燕", "秦")
        self.assertEqual(len(w.pacts), 1)
        w.propose_pact("共同防御", "燕", "秦")
        w.accept_pact("秦", w.proposals[-1]["id"])
        self.assertEqual([p["kind"] for p in w.pacts], ["共同防御"])

    def test_guarantee_refused_when_defense_pact_exists(self):
        w = make_world()
        w.propose_pact("共同防御", "秦", "燕")
        w.accept_pact("燕", w.proposals[-1]["id"])
        ok, msg = w.declare_guarantee("秦", "燕")
        self.assertFalse(ok)
        self.assertIn("更高一档", msg)

    def test_pacts_survive_save_load(self):
        w = make_world()
        make_bloc(w)
        w.declare_guarantee("秦", "燕")
        vid = w.votes[-1]["id"]
        w.cast_vote("秦", vid, True)
        w.cast_vote("齐", vid, True)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        self.assertEqual([(x["kind"], x["a"], x["b"]) for x in w2.pacts],
                         [(x["kind"], x["a"], x["b"]) for x in w.pacts])
        self.assertTrue(w2.has_pact("保障", mp.ent_bloc("北盟"), mp.ent_nation("燕")))


if __name__ == "__main__":
    unittest.main()
