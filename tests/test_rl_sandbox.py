# -*- coding: utf-8 -*-
"""★ `rl/sandbox.py` 的**能力层契约**（2026-10-22 重写后的第一道闸）。

这份测试钉的是**结构**，不是某一局的数值：
  ① 引擎的**能力声明**能加载，且**方法名写错 ⇒ 加载即报**（不静默）；
  ② 沙盒**只**通过声明过的接口拿状态、不复刻引擎规则；
  ③ 旧门面**已经不在了**，影子状态与吞异常的写法**也不在了**（防回潮）。
"""
from __future__ import annotations

import importlib.util
import os
import re
import unittest
from pathlib import Path

from engine.runtime import ModError, Runtime

ROOT = Path(__file__).resolve().parent.parent
RL = ROOT / "rl"


def _sandbox(**kw):
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=kw.pop("seed", 3), size=kw.pop("size", 8), **kw)
    sb.reset()
    return sb


class TestCapabilityDeclaration(unittest.TestCase):
    """① 能力声明层。"""

    def test_release_manifest_loads(self):
        rt = Runtime.load(str(RL / "release.toml"))
        for cap in ("rl:sandbox", "rl:truth", "rl:odds", "rl:scenario",
                    "native:army", "native:territory", "native:vision",
                    "native:diplomacy", "native:buildings", "native:government",
                    "research:combat-probabilities"):
            self.assertIn(cap, rt.capabilities, f"能力 {cap} 没注册")

    def test_declared_methods_exist(self):
        """**声明的方法必须真的存在** ⇒ 改名就在加载期炸，而不是运行时静默算错。"""
        rt = Runtime.load(str(RL / "release.toml"))
        world = rt.world_class()()
        rt.check_interfaces(world)          # 正常情况：一条都不报

    def test_wrong_method_name_fails_at_load(self):
        """★ 把某个方法名写错 ⇒ **加载期** `ModError`（这是"不静默"的闸门）。"""
        rt = Runtime.load(str(RL / "release.toml"))
        world = rt.world_class()()
        mod = rt.units["rl:train"].mod
        mod.declaration["Interfaces"]["native:army"].append("visible_unitsXXX")
        with self.assertRaises(ModError):
            rt.check_interfaces(world)

    def test_unknown_capability_fails_at_load(self):
        """★ 声明一个**没人提供**的能力 ⇒ 同样在**加载期**炸。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "mod.toml").write_text(
                '[Mod]\n'
                'Id = "test:bogus"\n'
                'Version = "1.0.0"\n'
                'Entry = "rl.mods.train.mod:TrainMod"\n'
                '[State]\n'
                'Owns = ["test_bogus"]\n'
                '[Capabilities]\n'
                'Needs = ["native:不存在的"]\n'
                'Provides = ["test:bogus"]\n'
                '[Interfaces]\n'
                '"native:army" = ["snapshot"]\n'
                '[Sandbox]\n'
                'Stock = 1\n'
                'Supply = 1\n', encoding="utf-8")
            (root / "manifest.toml").write_text('[Load]\nPackages = ["pkg"]\n', encoding="utf-8")
            with self.assertRaises(ModError):
                Runtime.load(str(root / "manifest.toml"))

    def test_env_call_to_undeclared_method_raises(self):
        """★ RL 侧调**没声明**的方法 ⇒ 当场 `ModError`（不是静默算错）。"""
        from rl.env import Env
        env = Env.load()
        with self.assertRaises(ModError):
            env.call("native:army", "visible_unitsXXX", "rl:n0")


class TestSandboxRunsOnCapabilities(unittest.TestCase):
    """② 沙盒真的跑在能力层上。"""

    def test_two_nations_with_cores_and_armies(self):
        sb = _sandbox(n_nations=2)
        self.assertEqual(sb.players, ("甲", "乙"))
        for name in sb.players:
            self.assertTrue(sb.alive(name), f"{name} 开局就不活着")
            self.assertIsNotNone(sb.core_of(name), f"{name} 没有核心格")
            self.assertEqual(len(sb.armies_of(name)), 5, f"{name} 开局兵数不对")
        self.assertTrue(sb.env.war_between("甲", "乙"), "两国局应当是交战关系")

    def test_turn_number_is_the_engine_period(self):
        """★ 回合号**源自**引擎的期号（RL 侧不自己数 ⇒ 不会漂）。

        ★ 扣掉的是**建局基线**：建局里的 native 外交流程（宣战要"提交→结算→代投→再结算"）
          会推进几个期号，那些是"摆局"，**不是玩家走的一步** ⇒ 不该占 `t_max`。
          ⇒ 判据是"**跟着引擎走**"（差一个固定的基线），不是"等于引擎的期号"。
        """
        sb = _sandbox()
        self.assertEqual(sb.turn, sb.env.period() - sb.turn_base)
        before = sb.turn
        sb.end_turn()
        self.assertEqual(sb.turn, sb.env.period() - sb.turn_base)
        self.assertEqual(sb.turn, before + 1, "走完一回合，玩家回合数应当 +1")

    def test_setup_periods_do_not_eat_the_turn_budget(self):
        """★ 建局推进的期号**不占** `t_max`（否则 `t_max=1` 的局开局就"结束"了）。"""
        sb = _sandbox()
        self.assertEqual(sb.turn, 0, "开局时玩家回合数应当是 0")
        self.assertGreater(sb.env.period(), 0, "引擎的期号已经被建局推进过了")

    def test_legal_moves_come_from_the_engine_reachable_set(self):
        """★ 合法动作的落点**必须**在引擎的可达集里（结构上不会撞墙）。"""
        sb = _sandbox()
        for act in sb.legal():
            aid, kind, x, y = act
            if kind != "move":
                continue
            unit = sb.env.unit("甲", aid)
            self.assertIsNotNone(unit)
            reach = sb.env.reachable("甲", unit, for_attack=False)
            self.assertIn((x, y), reach,
                          f"落点 {(x, y)} 不在引擎的可达集里 ⇒ 那是 RL 自己算的")

    def test_legal_has_no_duplicates(self):
        """★ 同一动作**不出现两次**（否则候选分布被偏置，且不报错）。"""
        sb = _sandbox()
        acts = sb.legal()
        self.assertEqual(len(acts), len(set(acts)), "legal() 里有重复动作")

    def test_hold_marks_the_unit_for_this_turn_only(self):
        """★ "原地不动"只 mask **本回合**（引擎没有这个动作 ⇒ 沙盒自己记）。"""
        sb = _sandbox()
        acts = sb.legal()
        holds = [a for a in acts if a[1] == "hold"]
        self.assertTrue(holds, "合法集里没有 hold")
        aid = holds[0][0]
        unit = sb.env.unit(sb.current_player(), aid)
        before = len([a for a in sb.legal() if a[0] == aid])
        sb.step(holds[0])
        after = len([a for a in sb.legal() if a[0] == aid])
        self.assertLess(after, before, "hold 之后这支军仍能行动 ⇒ 没被 mask")

    def test_clone_is_independent(self):
        """★ 试演副本**改不到真身**（引擎状态按 mod 存档复制）。"""
        sb = _sandbox()
        copy = sb.clone()
        acts = copy.legal()
        self.assertTrue(acts)
        copy.step(acts[0])
        self.assertNotEqual(len(sb.env.armies()), 0)
        self.assertIsNot(sb.env, copy.env)


class TestNoFacadeNoShadowState(unittest.TestCase):
    """③ 旧门面与影子写法**不许回潮**。"""

    def test_facade_module_is_gone(self):
        self.assertIsNone(importlib.util.find_spec("rl.engine"),
                          "rl/engine.py 还在 ⇒ 门面没删干净")

    #: ★ **决策层**的模块 —— 这里的"吞异常"会把引擎的拒绝变成"静默算错"。
    #: （`league.py` 的 `close()` 那种"关库失败无所谓"是**清理**，不是规则判定，
    #:   不在这张表里。）
    RULE_MODULES = ("sandbox.py", "env.py", "encode.py", "evaluate.py",
                    "military.py", "odds.py", "opponents.py", "train.py",
                    "hall_memory.py", "war_memory.py")

    def test_no_swallowed_exceptions_in_rule_modules(self):
        """★ 决策层不许 `except ...: pass`（吞掉引擎的拒绝 = 静默算错）。"""
        bad = []
        for path in sorted(RL.rglob("*.py")):
            if path.name not in self.RULE_MODULES and path.parent.name != "teacher":
                continue
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"except[^\n]*:\s*\n\s*pass\b", text):
                bad.append(f"{path.relative_to(ROOT)}:{text[:m.start()].count(chr(10)) + 1}")
        self.assertEqual(bad, [], f"决策层有吞异常的写法：{bad}")

    def test_no_private_engine_state_pokes(self):
        """★ 不许碰引擎的私有字段（`world.units.guard_once` 那种）。"""
        bad = []
        for path in RL.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in ("guard_once", "._world.", "world._tiles", "world._army",
                          "world._reachable", "world._defs_at", "world._defense_pct"):
                if token in text and "mod.py" not in path.name:
                    bad.append(f"{path.relative_to(ROOT)}:{token}")
        self.assertEqual(bad, [], f"有人在碰引擎的私有状态：{bad}")

    def test_no_facade_shaped_access_in_rl(self):
        """★ 不许再出现旧门面的 `world.*` 形状（`world.tiles()` / `world.armies()` …）。"""
        bad = []
        pattern = re.compile(r"\bworld\.(tiles|armies|nations|turn|order|troops|blocs)\b")
        for path in RL.rglob("*.py"):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    bad.append(f"{path.relative_to(ROOT)}:{i}")
        self.assertEqual(bad, [], f"还有旧门面的访问形状：{bad}")


if __name__ == "__main__":
    unittest.main()
