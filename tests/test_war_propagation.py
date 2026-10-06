# -*- coding: utf-8 -*-
"""守侧传导的**跳数**：一跳不级联（"无限跳"只指联盟收成一个点）。

用户 2026-10-07 定案：「**不无限跳，无限跳指联盟**」。此前 `_declare_war_internal`
的守侧用的是 `while stack` 传递闭包，于是实盘第 152 回合出现：

    秦 宣战 魏 ⇒ 守侧参战：赵、韩、楚

赵、韩是魏的**直接**共防伙伴（对）；**楚 与 魏 之间没有任何条约**——它纯粹是
"魏被打 → 韩参战 → 韩的共防伙伴楚也参战"这一跳的产物。条约的触发条件是
**它被打**，不是**它被卷进来**（《游戏说明书》§五：共同防御 = "谁被打，另一方
就自动参战"；`propose` 工具：「仅守：它被打才自动并肩参战」）。

"无限跳"在本作里只剩一个含义：**联盟收成一个点**——打盟员 = 打联盟，全盟一起上
（`entity_members(def_ent)` 一次取齐），联盟内部没有双边条约，也就不存在"经由某个
成员把盟外国家串成一串"的链条。见《游戏说明书》§五「联盟是「星形」，不是「链条」」。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
from mp import ent_bloc, ent_nation  # noqa: E402


def _defense(w, a: str, b: str) -> None:
    """a、b 两个独立国缔结共同防御（走正规提议/接受流程）。"""
    ok, msg = w.propose_pact("共同防御", a, b)
    assert ok, msg
    ok, msg = w.accept_pact(b, w.proposals[-1]["id"])
    assert ok, msg
    assert w.has_pact("共同防御", ent_nation(a), ent_nation(b))


def _bloc(w, chief: str, others, name: str) -> dict:
    """走正规流程立盟：chief 发起、others 全部接受。"""
    w.propose_bloc(chief, name, list(others))
    p = w.proposals[-1]
    for m in others:
        ok, msg = w.accept_pact(m, p["id"])
        assert ok, msg
    return w.bloc_of(chief)


class TestOneHopNoCascade(unittest.TestCase):
    """★ 核心不变量：援军**自己身上的条约不再往下传**。"""

    def test_共防不级联_实盘152回合回归(self):
        """魏—韩共防、韩—楚共防 ⇒ 秦打魏**只该拖进韩**。楚与魏无约，不该动。

        这是 2026-10-07 实盘那件事的最小复现；没有这条测试，闭包改回 `while stack`
        也不会有任何红灯。
        """
        w = mp.World(size=24, seed=17, nations=["秦", "魏", "韩", "楚"])
        _defense(w, "魏", "韩")
        _defense(w, "韩", "楚")
        ok, msg = w.declare_war("秦", "魏")
        self.assertTrue(ok, msg)
        war = w.wars[0]
        self.assertEqual(war["followers"], ["韩"], "只有魏的直接共防伙伴参战")
        self.assertFalse(w.war_between("秦", "楚"), "楚是被韩的义务传染进来的——不该发生")
        self.assertFalse(w.war_between("楚", "魏"), "楚与魏之间本来就没有条约")

    def test_保障不级联(self):
        """赵保燕、燕保楚 ⇒ 秦打楚**只有燕上**，赵不动（赵的义务对象是燕，燕没被打）。"""
        w = mp.World(size=24, seed=17, nations=["秦", "赵", "燕", "楚"])
        self.assertTrue(w.declare_guarantee("赵", "燕")[0])
        self.assertTrue(w.declare_guarantee("燕", "楚")[0])
        ok, msg = w.declare_war("秦", "楚")
        self.assertTrue(ok, msg)
        self.assertEqual(w.wars[0]["followers"], ["燕"])
        self.assertFalse(w.war_between("秦", "赵"), "隔山打牛：赵保的是燕")

    def test_联盟实体同样只传一跳(self):
        """联盟保燕、燕又保赵 ⇒ 秦打赵**只有燕上**，联盟一兵不动。"""
        w = mp.World(size=24, seed=5, nations=["秦", "楚", "齐", "燕", "赵"])
        _bloc(w, "楚", ("齐",), "南盟")
        w.declare_guarantee("楚", "燕")                 # 在盟 → 转联盟投票
        vid = w.votes[-1]["id"]
        w.cast_vote("楚", vid, True)
        w.cast_vote("齐", vid, True)
        self.assertTrue(w.has_pact("保障", ent_bloc("南盟"), ent_nation("燕")))
        self.assertTrue(w.declare_guarantee("燕", "赵")[0])
        ok, msg = w.declare_war("秦", "赵")
        self.assertTrue(ok, msg)
        self.assertEqual(w.wars[0]["followers"], ["燕"])
        for m in ("楚", "齐"):
            self.assertFalse(w.war_between("秦", m), f"{m} 是被联盟那张纸的**上一跳**牵来的")


class TestOneHopStillBites(unittest.TestCase):
    """★ 反向护栏：别把"不级联"改成"不触发"——直接签约方必须照常参战。"""

    def test_直接签约方一个不少(self):
        """魏 的**直接**保障国与**直接**共防伙伴都该上，且各算一次（去重）。"""
        w = mp.World(size=24, seed=17, nations=["秦", "魏", "韩", "赵", "楚"])
        _defense(w, "魏", "韩")
        self.assertTrue(w.declare_guarantee("赵", "魏")[0])     # 赵保障魏
        ok, msg = w.declare_war("秦", "魏")
        self.assertTrue(ok, msg)
        self.assertEqual(w.wars[0]["followers"], ["赵", "韩"])
        for n in ("赵", "韩"):
            self.assertTrue(w.war_between("秦", n))

    def test_打盟员_全盟参战不设跳数(self):
        """★「无限跳指联盟」：盟里多少人、跳几层都不算——打任何一个成员，全盟一起上。"""
        w = mp.World(size=24, seed=17, nations=["秦", "魏", "韩", "赵", "楚"])
        b = _bloc(w, "魏", ("韩", "赵", "楚"), "合纵")
        self.assertEqual(len(b["members"]), 4)
        ok, msg = w.declare_war("秦", "魏")
        self.assertTrue(ok, msg)
        war = w.wars[0]
        self.assertEqual(war["def"], "魏", "防御主导 = 盟主")
        self.assertEqual(war["followers"], ["韩", "赵", "楚"], "全盟落在守侧，一个不少")
        for n in ("魏", "韩", "赵", "楚"):
            self.assertTrue(w.war_between("秦", n))


if __name__ == "__main__":
    unittest.main()
