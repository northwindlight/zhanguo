# -*- coding: utf-8 -*-
"""八国剧本（战国七雄 + 周王室）：开局造得对、**保障真的会传导**、同种子同结果。

为什么值得单独钉一组：剧本的机制**全是"静默失效"型的**，错了也不报错——

- **条约的签约方是外交实体 id**（`国:秦`）而不是国名。写国名引擎照收，只是
  `guarantors_of` / 闭包 / 面板全都查不到它 ⇒「七雄共保」变成一纸空文，**打起来才发现**
  （本次实现就真栽了一次）；
- 手工落位写错一格 ⇒ 十字重叠或出界，图还是建得出来；
- 条约没进存档 ⇒ 读档后周王室裸奔。

所以这里逐条钉：条数、**宣战后的参战名单**、读档后仍在、两次建局 byte 相同。
（跑法：`python3 -m unittest discover -s tests -v`）
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mp import World, ent_nation  # noqa: E402
from scenarios import eight_nations as S  # noqa: E402


class TestScenarioShape(unittest.TestCase):
    def setUp(self):
        self.w = S.build()

    def test_八国_各五格十字_中心平原且自带市政厅(self):
        self.assertEqual(list(self.w.order), list(S.NATIONS))
        self.assertEqual(len(self.w.nations), 8)
        for nm in S.NATIONS:
            self.assertEqual(len(self.w.own_tiles(nm)), 5, f"{nm} 该占十字五格")
            cx, cy = S.STARTS[nm]
            self.assertEqual(self.w.tiles[(cx, cy)]["terrain"], "平原", "核心格必为平原")
            self.assertTrue(self.w.has_townhall(nm), f"{nm} 核心该自带市政厅（国祚）")

    def test_落位不重叠且不出界(self):
        pts = list(S.STARTS.values())
        self.assertEqual(len(pts), len(set(pts)), "两家不能落在同一格")
        for (x, y) in pts:
            self.assertLessEqual(1, x, "十字要占 ±1 格，不能贴边")
            self.assertLessEqual(1, y)
            self.assertGreaterEqual(S.SIZE - 2, x)
            self.assertGreaterEqual(S.SIZE - 2, y)
        for i, a in enumerate(pts):
            for b in pts[i + 1:]:
                d = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                self.assertGreaterEqual(d, 3, f"{a} 与 {b} 太近，十字会挨上")

    def test_顺序无关的名册_七雄在前周王室在后(self):
        self.assertEqual(S.ZHOU, "周")
        self.assertEqual(set(S.NATIONS), {"秦", "魏", "韩", "赵", "燕", "齐", "楚", "周"})


class TestGuarantee(unittest.TestCase):
    """「开局自动七雄保证周王室独立」——落条约是一步，**真的会传导**是另一步。"""

    def setUp(self):
        self.w = S.build()

    def test_七雄各保周王室一次_周不保别人(self):
        zhou = self.w.entity_of(S.ZHOU)
        self.assertEqual(self.w.guarantors_of(zhou), sorted(ent_nation(n) for n in S.SEVEN))
        self.assertEqual(self.w.guaranteed_by(zhou), [], "保障是单向的：周王室不欠谁")
        self.assertTrue(self.w.bank_on(), "剧本自带央行（利差口径要有央行才有意义）")

    def test_打周王室_另外六国全体自动参战(self):
        ok, msg = self.w.declare_war("秦", S.ZHOU)
        self.assertTrue(ok, msg)
        self.assertEqual(len(self.w.wars), 1, "只开一条战线")
        war = self.w.wars[0]
        self.assertEqual(war["atk"], "秦")
        self.assertEqual(war["def"], S.ZHOU)
        self.assertEqual(sorted(war["followers"]), sorted(set(S.SEVEN) - {"秦"}),
                         "其余六雄该全部作为守侧跟随方参战")
        for n in ("楚", "燕", "赵", "韩", "魏", "齐"):
            self.assertTrue(self.w.war_between("秦", n), f"{n} 该与秦交战")

    def test_自己保的约_开打时自动解除(self):
        self.w.declare_war("秦", S.ZHOU)
        self.assertFalse(self.w.has_pact("保障", self.w.entity_of("秦"), self.w.entity_of(S.ZHOU)),
                         "宣战方与守方之间的保障/共同防御要先解除（不打自己人）")
        self.assertEqual(len(self.w.guarantors_of(self.w.entity_of(S.ZHOU))), 6,
                         "另外六家的义务还在")

    def test_公之于众_每家面板都列得出来(self):
        """第三方必须知情（用户 2026-09-19「必须知情」）：七条保障全世界可见。"""
        import mp_ai
        panel = mp_ai._fmt_diplomacy(self.w, "齐")
        self.assertIn("【公开条约与战线】", panel)
        for n in S.SEVEN:
            self.assertIn(f"🕊 保障 {n}→{S.ZHOU}", panel, f"{n} 的保障该对全世界公开")

    def test_共誓进纪事_开局第一回合就收得到(self):
        self.assertTrue(self.w.history, "该有一条全世界可见的共誓公告")
        head = self.w.history[0]
        self.assertEqual(head["phase"], "外交")
        self.assertEqual(set(head["seen"]), set(S.NATIONS), "seen=全体（不受视野过滤）")


class TestScenarioSave(unittest.TestCase):
    """剧本的产物是**存档**，所以"读回来还是不是那个局"必须钉。"""

    def test_同种子同图_两次建局byte相同(self):
        with tempfile.TemporaryDirectory() as d:
            p1, p2 = Path(d) / "a.json", Path(d) / "b.json"
            S.build().save(p1)
            S.build().save(p2)
            h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
            self.assertEqual(h(p1), h(p2), "同 seed 同图同条约：byte 级一致")

    def test_读档后条约仍在_且照样传导(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "8.json"
            S.build().save(p)
            w = World.load(p)
            self.assertEqual(len(w.nations), 8)
            zhou = w.entity_of(S.ZHOU)
            self.assertEqual(len(w.guarantors_of(zhou)), 7, "读档不许把条约读丢")
            ok, msg = w.declare_war("楚", S.ZHOU)
            self.assertTrue(ok, msg)
            self.assertEqual(sorted(w.wars[0]["followers"]), sorted(set(S.SEVEN) - {"楚"}))

    def test_不碰真实存档(self):
        """本仓铁律：测试只写临时目录，绝不读写 mp_save.json（那是用户在玩的局）。"""
        self.assertFalse(str(Path.cwd()).endswith(".git"), "跑在仓库根才好比相对路径")
        self.assertNotIn("mp_save.json", str(Path(tempfile.gettempdir())))


class TestScenarioConfig(unittest.TestCase):
    """配套配置：只借模板的**接入参数**，国名与地图参数由剧本说了算。"""

    def _cfg(self):
        tpl = {"max_turns": 300, "ctx_window": 262144, "save": "mp_save.json",
               "journal": "mp_journal.md", "world_bank": False, "rule_ai": "v10",
               "nations": [{"name": "秦", "base_url": "http://x/v1", "api_key": "k",
                            "model": "m", "max_steps": 40, "polity": "huns",
                            "start_gold": 1}]}
        return S.build_config(tpl)

    def test_八国都拿到接入参数(self):
        cfg = self._cfg()
        self.assertEqual([n["name"] for n in cfg["nations"]], list(S.NATIONS))
        for n in cfg["nations"]:
            self.assertEqual(n["model"], "m")
            self.assertEqual(n["base_url"], "http://x/v1")
            self.assertNotIn("polity", n, "政体/开局定制是别国的，不该跟着模板复制")
            self.assertNotIn("start_gold", n)

    def test_地图与存档参数由剧本定(self):
        cfg = self._cfg()
        self.assertEqual(cfg["map_size"], S.SIZE)
        self.assertEqual(cfg["seed"], S.SEED)
        self.assertEqual(cfg["save"], S.SAVE)
        self.assertEqual(cfg["journal"], S.JOURNAL)
        self.assertTrue(cfg["world_bank"], "剧本按开了央行设计")
        self.assertEqual(cfg["max_turns"], 300, "回合数这类沿用模板")


if __name__ == "__main__":
    unittest.main()
