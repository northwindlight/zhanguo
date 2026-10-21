# -*- coding: utf-8 -*-
"""`_auto_advance` 的守卫 —— **"全部军队 mask 后自动结束"必须真的走到推进**。

★★ 修掉的真 bug（2026-09-25 查出来的）：
   `_auto_advance` 原来只推进**一步**，于是"结算后新一轮的第一个国家就不能动"
   就**卡在中途退出**：`legal()` 空、`is_terminal()` 假、**没有胜方**，而**不报错**。
   ⇒ 那一局既拿不到终局的 ±1、也不进"先手连赢"闸门，**静默地白打**。

★ "活着却不能动"是**正常的**（不是脏状态）：沙盒**没有征兵动作**（只有补员），
  一个**兵全打光**的国家要等下一次补员（每 `RESUPPLY_EVERY` 回合）才有事可做。
  而它**每一步都可能是当前玩家**。

★ 这里钉的是一条**契约**（与网络权重、随机种子都无关，所以不会假绿）：

      还没终局 ⇒ `current_player()` 有值 且 `legal()` 非空

  违反它 = 调用方（`collect_episode`）只能中断，那一局就废了。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox                      # noqa: E402


def _sb(seed=3, size=8, n=3, t_max=20):
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=t_max,
                   halls_known=True).reset()


def _disarm(sb, name):
    """把某一国的兵全部抹掉（等价于"打光了"，但不改它的厅 ⇒ 它**还活着**）。"""
    sb.world.armies[:] = [a for a in sb.world.armies if a["owner"] != name]


class TestNoStall(unittest.TestCase):
    def test_legal_is_never_empty_before_terminal(self):
        """★ 核心契约：没终局就不许出现"无人可动"。"""
        for seed in range(4):
            sb = _sb(seed=seed)
            _disarm(sb, sb.players[0])           # ★ 造出"有厅但没兵"的那个国家
            self.assertTrue(sb.alive(sb.players[0]), "用例前提：它还有厅")
            self.assertEqual(sb.count_of(sb.players[0], "native:infantry"), 0, "用例前提：它没兵")
            # ★ 状态被我改了 ⇒ 让它追平一次（真实流程里这一步由 `step`/`end_turn`
            #   内部触发；这里手工改 world，所以要自己叫一次）。
            sb._auto_advance()
            steps = 0
            while not sb.is_terminal():
                self.assertIsNotNone(
                    sb.current_player(),
                    f"seed={seed} turn={sb.turn}：没终局却没人能动（卡死）")
                acts = sb.legal()
                self.assertTrue(
                    acts,
                    f"seed={seed} turn={sb.turn} 当前玩家 {sb.current_player()} "
                    f"的 `legal()` 空了 —— 调用方只能中断，这一局就废了")
                sb.step(acts[0])
                steps += 1
                self.assertLess(steps, 5000, "步数没上限 ⇒ 可能死循环")
            self.assertTrue(sb.is_terminal(), f"seed={seed} 没走到终局")

    def test_disarming_everyone_still_reaches_terminal(self):
        """★★ 极端情形：**所有国家**都没兵（全场无军可动）。

        补员每 `RESUPPLY_EVERY` 回合会发兵 ⇒ 必须靠"结算 → 开新回合"自己爬出来，
        而不是当场卡住。★ 这条同时是把"循环"写成**有界**的证明（`done()` 收尾）。
        """
        sb = _sb(seed=5, t_max=20)
        for p in sb.players:
            _disarm(sb, p)
        sb._auto_advance()                        # 直接问它：能不能自己爬出来
        self.assertTrue(sb.is_terminal() or sb.can_act(sb.current_player()),
                        "全员没兵时 `_auto_advance` 没把自己推到「有事可做」或终局")

    def test_normal_game_also_never_stalls(self):
        """★ 反向对照：**不动手脚**的普通对局也必须满足同一条契约。

        只在"造了残废国家"的用例里测，可能是**为那个特例打的补丁**；
        普通对局一起测，才是"沙盒任何可达状态都不许卡"。
        """
        for seed in range(3):
            sb = _sb(seed=seed, size=12, n=3, t_max=12)
            while not sb.is_terminal():
                self.assertTrue(sb.legal(), f"seed={seed} turn={sb.turn} 正常对局也卡了")
                sb.step(sb.legal()[0])


class TestDeadNationDoesNotBlockTheTurnQueue(unittest.TestCase):
    """★★ **亡国但残兵还在**的国家不许占着行动位（2026-10-21 修的真 bug）。

    ★ 病灶：`can_act()` 原来**只看兵、不看死活** ⇒ 一个**厅没了、但残兵还在动**的
      亡国，`can_act` 照样返回 True ⇒ `_auto_advance` 以为"它还能动"就**停在它身上**
      ⇒ `current_player()` 指着这个**死人** ⇒ `legal()` 空（`legal()` 里
      `not self.alive(name)` 直接返回 `[]`）⇒ 采集循环只能中断
      ⇒ **那一局既不终局、也没胜方**（`truncated=True`），而且**全程不报错**。
      实测（3 国 8×8 第 6 回合）：乙的厅被拔但还剩 5 支军 ⇒ 死锁，
      `tests/test_rl_rollout_budget.py::test_untruncated_episode_finishes` 当场红。

    ★ 契约（本文件头那条）：**没终局 ⇒ `current_player()` 有值 且 `legal()` 非空**。
      上面那个用例本来就在守这条，只是**没走到这条路径** —— 这条专门把它钉死。
    """

    def test_dead_nation_with_surviving_armies_is_skipped(self):
        sb = _sb(seed=3, size=8, n=3, t_max=20)
        dead = sb.players[1]
        # ★ 先让它**站在行动位上**（`pending` 的队首）—— 真实流程里它是在自己那步
        #   之后才死的，`pending` 没被重建 ⇒ 死国留在队首。
        sb.pending = [dead] + [p for p in sb.players if p != dead]
        # ★ 拔掉它的厅 ⇒ 亡国；但**兵留着**（军不会跟着厅一起消失）。
        core = next(c for c, t in sorted(sb.world.tiles.items())
                    if t["owner"] == dead and t["buildings"].get("native:town-hall", 0) > 0)
        sb.world.tiles[core]["buildings"]["native:town-hall"] = 0
        self.assertFalse(sb.alive(dead), "用例前提：它已经亡国")
        self.assertTrue(sb.armies_of(dead), "用例前提：它还有残兵（这才是那个坑的前提）")
        self.assertTrue(sb.can_act(dead) is False,
                        "★ 亡国的国家不该算「还能动」—— 这条判据就是病灶")

        sb._auto_advance()

        self.assertNotEqual(sb.current_player(), dead,
                             "★ 亡国的人还占着行动位 ⇒ 这一局会死锁")
        if not sb.is_terminal():
            self.assertTrue(sb.legal(),
                            "★ 跳过亡国的人之后还是没人能动 ⇒ 契约破了")


if __name__ == "__main__":
    unittest.main()