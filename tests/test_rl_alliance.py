# -*- coding: utf-8 -*-
"""**随机结盟（只限 2v2）**的守卫 —— 用户 2026-09-26：「考虑加入随机结盟(只限 2v2 对局)」。

★ 为什么这件事值得做（也是"允许大图、最高 5 国"能成立的原因）：
  胜者口径是 `winning_entity()` = **场上只剩一个实体**。5 国局要灭 4 家 ⇒
  在 400 回合里几乎打不出来（实测旧配置 10 局 0 胜者）。2v2 下只剩**两个实体**，
  灭掉对面那一家就赢 ⇒ **大图重新可赢**。

★ 每条守卫对着一个**会静默错**的形状：

  ① **只限 2v2**：3 国/5 国不该结盟 —— 5 国若被"配成 2v2+1"，那个落单的会变成
     中立/孤儿的怪异局面（引擎里"中立不可入境"，它会冻在角落里而**不报错**）。
  ② **配成两个"两人"实体、覆盖全部 4 国、两两不重叠** —— 配错（漏一个/重叠）
     会让某个国家一开局就没有实体归属。
  ③ **逐局可复现**（同 seed 同结盟）+ **加了结盟不许挪动别的随机**（地图/厅/时钟/国土）。
  ④ **盟内真的互盟、跨界真的不盟**（`world.allied_between`）—— 只看 `blocs` 列表
     不够：真正决定"能不能打"的是引擎那两个判定。
  ⑤ **关掉时不留残盟**（`alliance_pairs` 每局清空）—— 上一局的盟漏进这一局
     **不报错**，只是开局关系悄悄变了。

★ 都故意破坏过（见各条 docstring）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox                              # noqa: E402


def _sb(seed=7, size=16, n=4, mode="random2v2"):
    return Sandbox(seed=seed, size=size, n_nations=n, halls_known=True,
                   alliances=mode).reset()


class TestOnlyTwoVersusTwo(unittest.TestCase):
    """① 只限 2v2；② 两个两人实体、覆盖全部、不重叠。"""

    def test_four_nations_get_two_disjoint_pairs(self):
        for seed in range(12):
            sb = _sb(seed=seed)
            self.assertEqual(len(sb.alliance_pairs), 2, f"seed={seed} 不是两个同盟")
            a, b = sb.alliance_pairs
            self.assertEqual(len(a), 2, f"seed={seed} 的同盟不是两人：{a}")
            self.assertEqual(len(b), 2, f"seed={seed} 的同盟不是两人：{b}")
            self.assertEqual(set(a) | set(b), set(sb.players), "同盟没覆盖全部国家")
            self.assertEqual(set(a) & set(b), set(), "两家挤进同一个同盟了")

    def test_odd_counts_do_not_ally(self):
        """★ 破坏方式：把 `if len(self.players) != 4` 改成 `if False`（谁都能结盟）
        ⇒ 3 国会配出"2+1"（那一个的数量对不上）⇒ 这条红。"""
        for n in (3, 5):
            for seed in range(6):
                sb = _sb(seed=seed, n=n)
                self.assertEqual(sb.alliance_pairs, (),
                                 f"{n} 国局不该结盟（凑不出 2v2），却配出了 {sb.alliance_pairs}")

    def test_off_switch(self):
        sb = _sb(mode="none")
        self.assertEqual(sb.alliance_pairs, ())
        self.assertEqual([bl for bl in sb.world.blocs], [], "关掉了还有 bloc")


class TestDeterminismAndNoPerturbation(unittest.TestCase):
    """③ 同 seed 可复现；且**不许挪动别的随机**。"""

    def test_same_seed_same_alliance(self):
        self.assertEqual(_sb(seed=31).alliance_pairs, _sb(seed=31).alliance_pairs)
        # ★ 不同的 seed 会抽到不同的配对（否则"随机"是假的）
        seen = {_sb(seed=s).alliance_pairs for s in range(20)}
        self.assertGreater(len(seen), 1, f"20 个 seed 只抽到一种配对：{seen}")

    def test_map_halls_clock_territory_are_untouched(self):
        """★ 加结盟**不许**动地图/厅位置/回合偏移/国土目标（版间 A/B 的地基）。"""
        for seed in (3, 11, 42):
            on, off = _sb(seed=seed), _sb(seed=seed, mode="none")
            self.assertEqual([on.core_of(p) for p in on.players],
                             [off.core_of(p) for p in off.players], "厅位置被挪动了")
            self.assertEqual(on.turn_offset, off.turn_offset, "回合偏移被挪动了")
            self.assertEqual(on.territory_target, off.territory_target, "国土目标被挪动了")
            self.assertEqual(
                {c: on.world.tiles[c]["owner"] for c in on.world.tiles},
                {c: off.world.tiles[c]["owner"] for c in off.world.tiles},
                "地块归属被挪动了")


class TestEngineActuallySeesTheAlliance(unittest.TestCase):
    """④ 盟内互盟、跨界不盟（真正决定"能不能打"的是引擎的判定）。"""

    def test_allied_within_not_across(self):
        for seed in range(8):
            sb = _sb(seed=seed)
            a, b = sb.alliance_pairs
            w = sb.world
            self.assertTrue(w.allied_between(a[0], a[1]), f"seed={seed}：{a} 盟内没结上")
            self.assertTrue(w.allied_between(b[0], b[1]), f"seed={seed}：{b} 盟内没结上")
            self.assertFalse(w.allied_between(a[0], b[0]),
                             f"seed={seed}：跨盟居然也算盟友 ⇒ 谁都不打谁")
            # ★ 我方的地**走得进**（盟国地放行）、但**打不了**（`_atk_target_ok` 要非盟）
            self.assertTrue(w.allied_between(a[0], a[1]))

    def test_winner_can_be_a_whole_alliance(self):
        """★ 实体胜利：灭掉对面那一家 ⇒ **整个同盟都算赢**（`winner_members` 给出两人）。

        ★ 破坏方式：把 `set_alliance` 改成"只往 blocs 里塞一个人"（表面看还是结了盟）
        ⇒ 这条红（赢家只剩一个国名）。
        """
        sb = _sb(seed=5, size=12)
        a, b = sb.alliance_pairs
        # 手动把 b 那两家灭掉（拔掉它们的厅）—— 不跑完整局，直接验口径
        for name in b:
            for cell, t in list(sb.world.tiles.items()):
                if t["owner"] == name:
                    t["buildings"]["市政厅"] = 0
            if name in sb.world.nations:
                sb.world.nations.pop(name)          # 模拟被灭国
        ent = sb.winning_entity()
        self.assertIsNotNone(ent, "对面全灭之后居然还没定局")
        mem = set(sb.winner_members())
        self.assertEqual(mem, set(a),
                         f"赢的应当是整个同盟 {a}，实际是 {mem} ⇒ 实体胜利没接到联盟上")


class TestNoStaleAlliance(unittest.TestCase):
    """⑤ 关掉/换 seed 之后不许留下上一局的盟。"""

    def test_pairs_are_cleared_between_games(self):
        sb = _sb(seed=8)
        self.assertTrue(sb.alliance_pairs, "第一次就没结上（测试前提不成立）")
        sb.alliances = "none"
        sb.reset()
        self.assertEqual(sb.alliance_pairs, (), "换了模式还留着上一局的盟")
        self.assertEqual(sb.world.blocs, [], "换模式后 world 里还有旧 bloc")


if __name__ == "__main__":
    unittest.main()