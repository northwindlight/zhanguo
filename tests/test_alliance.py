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
import mp_ai  # noqa: E402


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


class TestFreeDiplomacyIgnoresTreasury(unittest.TestCase):
    """★ **免费的外交动作，负国库也做得成**（2026-10-09 用户报的真实 bug：

    「投票免费，但是负资产不能投」）。

    病根在 `mp_ai._charge`：它写成 `if 黄金 < cost`，于是**负国库**时 `-45 < 0` 成立
    ⇒ 所有 **cost=0** 的动作（投票 / 联盟改名 / 移交盟主 / 解散联盟，以及有外交中心或
    同盟成员免外交费的那些）全被"国库不足：此操作需 0 金"挡死。

    负国库的代价本来就该是"买不起"（借贷/买报表/派间谍瘫痪，那是设计）——但**穷国尤其
    需要投票与外交**，那是它仅剩的武器。所以闸门只该在 `cost > 0` 时落下。
    """

    def _bloc_with_vote(self):
        w = make_world()
        make_bloc(w)
        w.bloc_join("燕", "北盟")                      # 触发入盟投票（免费表决）
        return w, w.votes[-1]["id"]

    def test_vote_works_with_negative_treasury(self):
        w, vid = self._bloc_with_vote()
        w.nations["秦"].res["黄金"] = -45              # 国库为负
        out = mp_ai.execute(w, "秦", "vote", {"vote_id": vid, "choice": "yes"})
        self.assertNotIn("国库不足", out, f"免费的投票被钱挡住了：{out}")
        self.assertTrue(w.votes and w.votes[-1]["votes"].get("秦") is True,
                        f"票没投进去：{out}")

    def test_dissolve_rename_transfer_with_negative_treasury(self):
        """联盟那三个免费动作（解散/改名/移交）同样不该被负国库挡住。"""
        for tool, args in (("bloc_rename", {"name": "新名"}),
                           ("bloc_transfer", {"to": "楚"}),
                           ("bloc_dissolve", {})):
            w = make_world()
            make_bloc(w)
            w.nations["秦"].res["黄金"] = -45
            out = mp_ai.execute(w, "秦", tool, args)
            self.assertNotIn("国库不足", out, f"{tool} 是免费动作，却被钱挡住：{out}")

    def test_paid_action_still_refused(self):
        """★ 反过来：**收费**的动作在负国库时照样拒绝（别把闸门整个拆了）。"""
        w = make_world()
        make_bloc(w)
        w.nations["秦"].res["黄金"] = -45
        out = mp_ai.execute(w, "秦", "declare_war", {"to": "燕"})   # 外交费 > 0
        self.assertIn("国库不足", out)
        self.assertEqual(w.wars, [], "国库不够却把仗宣了")

    def test_zero_cost_but_not_affordable_is_still_a_wall_for_real_costs(self):
        """国库 0、费用 5（写信下限）⇒ 照旧拒绝；这话是"要说清多少钱"，不是"永远放行"。"""
        w = make_world()
        w.nations["秦"].res["黄金"] = 0
        out = mp_ai.execute(w, "秦", "send_letter", {"to": "燕", "content": "你好"})
        self.assertIn("国库不足", out)


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

    # ---- ★ 条约冻结（2026-09-18）：缔结与解除都不行 ----

    def test_cannot_break_defense_at_war(self):
        """断约脱战已封：`break_pact` 原先会顺带退出该条约带来的战线，那等于留了条跑路通道。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        w.propose_pact("共同防御", "秦", "燕")
        w.accept_pact("燕", w.proposals[-1]["id"])
        w.declare_war("赵", "秦")                               # 燕按共同防御被拖入守侧
        self.assertEqual(w.wars[0]["followers"], ["燕"])
        ok, msg = w.break_pact("共同防御", "燕", "秦")
        self.assertFalse(ok)
        self.assertIn("战争期间", msg)
        self.assertTrue(w.pacts)                                # 条约还在
        self.assertIn("燕", w.wars[0]["followers"])             # 也没被踢出战线

    def test_cannot_cancel_guarantee_at_war(self):
        """撤回保障同口径冻结（对称：战时既不能缔结也不能撤回）。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        w.declare_guarantee("秦", "燕")
        w.declare_war("赵", "燕")                               # 秦按保障被拖入守侧
        self.assertEqual(w.wars[0]["followers"], ["秦"])
        ok, msg = w.cancel_guarantee("秦", "燕")
        self.assertFalse(ok)
        self.assertIn("战争期间", msg)
        self.assertTrue(w.pacts)

    def test_treaty_withdrawal_allowed_after_war(self):
        """冻结只在战时——停战后解除通道重开（不是永久锁死）。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        w.propose_pact("共同防御", "秦", "燕")
        w.accept_pact("燕", w.proposals[-1]["id"])
        w.declare_war("赵", "秦")
        self.assertFalse(w.break_pact("共同防御", "燕", "秦")[0])
        w.wars = []                                             # 议和 → 整条战线停战
        self.assertTrue(w.break_pact("共同防御", "燕", "秦")[0])
        self.assertEqual(w.pacts, [])

    def test_pact_vote_cannot_take_effect_during_war(self):
        """★联盟表决也绕不过冻结：票在**和平期**发出、战争爆发后才通过 —— 执行时要再查一次。

        这是"投票在途"的真实窗口：`break_pact` 当时合法（没打仗），但等票凑齐时已经在打了。
        """
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        make_bloc(w)                                            # 秦/楚/齐 结盟（盟主秦）
        w.propose_pact("共同防御", "秦", "燕")
        vid = w.votes[-1]["id"]
        w.cast_vote("楚", vid, True)
        w.cast_vote("齐", vid, True)                            # 通过 → 向燕发出邀约
        offer = [p for p in w.proposals if p["kind"] == "共同防御"][0]
        w.accept_pact("燕", offer["id"])                        # 燕是独立国家 → 直接缔结
        self.assertTrue(w.has_pact("共同防御", mp.ent_bloc("北盟"), mp.ent_nation("燕")))
        # 和平期发起「解除」表决：此刻合法，投票在途
        ok, msg = w.break_pact("共同防御", "秦", "燕")
        self.assertTrue(ok, msg)
        cid = [v["id"] for v in w.votes
               if v["kind"] == "缔约" and v["payload"].get("cancel")][0]
        # 战争爆发（赵打北盟 → 燕按共同防御被拖入守侧）
        w.declare_war("赵", "秦")
        # ★ 2026-10-11 用户加严：「一旦发生战争，战争之前还没有通过的联盟外交条约立刻作废」
        #   ——不再等它凑齐票再"拒绝生效"，**开战即作废**（在途表决本来就该随战前信息一起废）
        self.assertFalse([v for v in w.votes if v["id"] == cid], "在途的解除表决没被作废")
        ok, msg = w.cast_vote("楚", cid, True)
        self.assertFalse(ok, "作废的表决还能投票")
        self.assertIn("没有这个投票", msg)
        self.assertTrue(w.has_pact("共同防御", mp.ent_bloc("北盟"), mp.ent_nation("燕")),
                        "条约本身该照旧有效")


class TestWarVoidsPendingTreaties(unittest.TestCase):
    """★★ 2026-10-11 用户口径：「**一旦发生战争，战争之前还没有通过的联盟外交条约立刻作废**」。

    这是「战时条约冻结」的补漏：冻结管得住"战时新签"，却管不住**在途**——缔约投票、
    入盟投票、结盟提议、保障/共同防御邀约，全都是"**战前发起、战后再落地**"：
    发起那一刻还没打仗，通过那一刻已经在打了。
    ★ 但**议和与宣战投票不动**——那是战争本身的操作，清了等于把出口也堵死。
    """

    def _pact_in_flight(self):
        """秦楚齐结盟（盟主秦）；桌上留一张**在途的共同防御邀约**（秦→燕）。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        make_bloc(w)
        w.propose_pact("共同防御", "秦", "燕")      # 秦在盟 → 先过联盟表决
        vid = w.votes[-1]["id"]
        w.cast_vote("楚", vid, True)
        w.cast_vote("齐", vid, True)               # 通过 → 向燕发出邀约，躺在 proposals 里
        return w

    def test_pending_pact_offer_is_voided(self):
        w = self._pact_in_flight()
        self.assertTrue([p for p in w.proposals if p["kind"] == "共同防御"], "前提：邀约在桌上")
        w.declare_war("赵", "秦")                  # 开战
        self.assertEqual([p for p in w.proposals if p["kind"] == "共同防御"], [],
                         "战前发出的条约邀约没被作废")
        # 燕 这时才回信：邀约已经不在桌上了
        ok, _msg = w.accept_pact("燕", 1)
        self.assertFalse(ok, "作废的邀约还能接受")

    def test_pending_bloc_founding_is_voided(self):
        """结盟提议也算"还没通过的"——开战即流产（不悬空挂着）。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        w.propose_bloc("秦", "北盟", ["燕"])
        pid = w.proposals[-1]["id"]
        self.assertTrue([p for p in w.proposals if p["kind"] == "联盟"], "前提：提议在桌上")
        w.declare_war("秦", "赵")
        self.assertEqual([p for p in w.proposals if p["kind"] == "联盟"], [],
                         "战前的结盟提议没作废")
        ok, msg = w.accept_pact("燕", pid)          # 燕 这才回信（真实路径：按 id 查）
        self.assertFalse(ok, "作废的结盟提议还能接受")
        self.assertIn("邀约", msg)

    def test_peace_and_war_votes_are_kept(self):
        """**阴性对照**：议和与宣战投票**不动**——清了就等于把战争的出口也堵死。"""
        w = make_world(nations=("秦", "楚", "齐", "燕", "赵"))
        make_bloc(w)
        w.declare_war("秦", "燕")                  # 全盟对燕开战（投票#1，在途）
        w.votes.append({"id": 99, "kind": "议和", "bloc": "北盟", "proposer": "秦",
                        "payload": {"type": "offer", "war_id": 1, "to": "燕", "kind": "白和",
                                    "gold": 0, "note": "", "truce": 5},
                        "votes": {}, "turn": w.turn})
        w.declare_war("秦", "赵")                  # 又开一条战线 ⇒ 触发作废
        self.assertTrue([v for v in w.votes if v["kind"] == "议和"], "议和表决被误清")
        self.assertTrue([v for v in w.votes if v["kind"] == "宣战"], "宣战表决被误清")

    def test_the_parties_are_told(self):
        """作废要通知当事人（"结果不告诉当事人"是这套引擎反复踩的洞）。"""
        w = self._pact_in_flight()
        w.declare_war("赵", "秦")
        self.assertTrue(any("战前未通过的外交条约当场作废" in e
                            for e in w.events_for("燕", 40)), "燕 不知道自己那张邀约废了")


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


class TestPublicAffairs(unittest.TestCase):
    """★ 2026-09-19 用户口径：「**必须知情**」——条约与战争是**公开行为**，第三方有权知道
    （共同防御本来就是冲着第三方设计的，第三方却被蒙在鼓里说不过去）；
    而**商议过程**（联盟投票、求和来回、写信）仍然只给当事人。

    改之前这些纪事一律走 `log(nation=…)`（有几处连 `nation=` 都没有）⇒ 第三方近讯里一个字都没有，
    连签字双方都收不到「缔结」那条（只有看海台 journal 有）。现在走 `World.proclaim` → `broadcast`
    （`seen`=全体、`phase="外交"`、不受视野过滤，与央行利率公告同一条通道）。
    """

    def test_third_party_hears_treaty_and_war(self):
        w = make_world()
        w._conclude_pact("共同防御", mp.ent_nation("秦"), mp.ent_nation("楚"))
        for n in ("秦", "楚", "燕"):          # 连签字双方自己也算（以前他们自己都收不到）
            self.assertTrue(any("缔结共同防御" in e for e in w.events_for(n, limit=6)),
                            f"{n} 没收到缔结播报")
        w.declare_war("秦", "楚")
        self.assertTrue(any("宣战" in e for e in w.events_for("燕", limit=6)),
                        "第三国没收到宣战播报")
        self.assertTrue(any("宣战" in e for e in w.events_for("楚", limit=6)),
                        "被宣战方也该收到（以前只有宣战方看得到）")

    def test_third_party_hears_war_ending_and_bloc_changes(self):
        w = make_world()
        w.declare_war("秦", "楚")
        ok, msg = w.offer_peace("秦", "楚", "white")
        self.assertTrue(ok, msg)
        w.accept_peace("楚", w.peace_offers[-1]["id"])
        self.assertTrue(any("议和停战" in e for e in w.events_for("燕", limit=6)),
                        "第三国没收到议和播报")
        make_bloc(w, chief="秦", others=("楚", "齐"))
        self.assertTrue(any("联盟「北盟」成立" in e for e in w.events_for("燕", limit=8)),
                        "第三国没收到立盟播报")
        w.bloc_leave("楚")
        self.assertTrue(any("退出联盟" in e for e in w.events_for("燕", limit=8)),
                        "第三国没收到退盟播报")

    def test_deliberation_stays_private(self):
        """商议不公开：联盟内部投票与计票、求和提议的来回，第三方近讯里不该有。"""
        w = make_world()
        make_bloc(w)
        w.declare_guarantee("楚", "燕")            # 在盟 → 转成联盟投票（内部商议）
        v = w.votes[-1]
        w.cast_vote("秦", v["id"], True)
        w.declare_war("秦", "楚")
        w.offer_peace("秦", "楚", "white")         # 谈判来回
        feed = w.events_for("燕", limit=10)
        self.assertFalse(any("投票#" in e or "投票（" in e for e in feed), f"内部投票泄了：{feed}")
        self.assertFalse(any("求和提议" in e for e in feed), f"求和来回泄了：{feed}")
        out = mp_ai.execute(w, "燕", "query", {"panel": "diplomacy"})
        self.assertNotIn("投票#", out, "别盟的内部投票不该进第三方面板")

    def test_public_panel_lists_world_treaties_war_and_truce(self):
        w = make_world()
        w._conclude_pact("共同防御", mp.ent_nation("秦"), mp.ent_nation("楚"))
        make_bloc(w, chief="齐", others=("燕",), name="东盟")
        w.declare_war("秦", "齐")
        out = mp_ai.execute(w, "燕", "query", {"panel": "diplomacy"})
        self.assertIn("公开条约与战线", out)
        # 双向条约在 `_add_pact` 里按实体 id 排序存 ⇒ 显示顺序是「楚↔秦」而不是发起顺序
        self.assertIn("共同防御 楚↔秦", out)
        self.assertIn("联盟「东盟」", out)
        self.assertIn("⚔ 秦 ↔ 齐", out)
        # 收尾：议和后要能查到休战期（用两个**独立国**——在盟国家的议和要走联盟投票，另测）
        w2 = make_world(nations=("秦", "燕", "齐"))
        w2.declare_war("秦", "燕")
        ok, msg = w2.offer_peace("秦", "燕", "white", truce=3)
        self.assertTrue(ok, msg)
        w2.accept_peace("燕", w2.peace_offers[-1]["id"])
        out2 = mp_ai.execute(w2, "齐", "query", {"panel": "diplomacy"})
        self.assertIn("休战至第", out2)

    def test_spy_report_includes_diplomatic_relations(self):
        """★ 用户 2026-09-19：「**间谍要包含外交关系**」——给的是**该国自己的视角**。"""
        w = make_world()
        make_bloc(w)                                # 秦楚齐 立盟「北盟」
        w.declare_war("燕", "秦")                    # 燕 打 北盟
        txt = w._econ_snapshot("秦")
        self.assertIn("外交关系（该国的视角）", txt)
        self.assertIn("联盟「北盟」（盟主 秦）", txt)
        self.assertIn("燕=交战", txt)
        self.assertIn("战线: 燕↔秦", txt)
        self.assertNotIn("投票#", txt, "间谍给的是关系，不是别国的商议过程")


if __name__ == "__main__":
    unittest.main()
