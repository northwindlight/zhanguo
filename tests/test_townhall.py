# -*- coding: utf-8 -*-
"""**国祚＝市政厅**——用户 2026-09-21 的规则改版：

  ① 「开局默认有个市政厅，在国家核心」——每国十字开局的**中心格**白送一座（已落成、
     不花钱、也不受"本地已用位≥6"那道门槛约束——那是"自己再盖一座"的门槛）。
  ② 「灭国条件改成当一个国家没有任意市政厅时亡国」——旧口径是"领土尽失"。
     旧口径是新的**特例**（地都丢了，盖在地上的厅自然也没了），反过来不成立：
     **还握着大片土地、厅却被拔光的国家照样亡国**。
  ③ 「剩余领土变成空地，建筑保留，但是无主」——亡国那一刻，它的地 `owner` 置 None、
     建筑原样留在原地，谁 atk 进驻就归谁（`_conquer` 的 `old is None` 分支必须**复用**该格，
     不许 `_new_tile` 重造——重造会把地名/建筑/资源一起洗掉，等于把 ③ 静默吃掉）。

配套的可见性口径（同一天定的，见 `World.visible_buildings`）：市政厅与城堡同档**公开**
——它是国祚，看不见就没法瞄准；而**无主故土全公开**（没有国，就没有"内政底细"要护）。

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


def cross_center(world, name: str) -> tuple[int, int]:
    """十字开局的中心格。

    ★ 别拿 `core == name` 找它：`_new_tile` 把**每一格**的 `core` 都写成首任 owner，
    所以开局的五块地个个都是"核心格"（`core` 是"首任归属/战后重算"，不是"首都"）。
    中心格的判据只能是几何：**它的十字邻域整圈都在自家名下**。
    """
    own = set(world.own_tiles(name))
    for p in sorted(own):
        if {(p[0] + dx, p[1] + dy) for dx, dy in mp.CROSS} <= own:
            return p
    raise AssertionError(f"{name} 没有十字中心（开局被裁过？）")


def kill_all_townhalls(world, victim: str, by: str = "秦",
                       keep: tuple[tuple[int, int], ...] = ()) -> list[tuple[int, int]]:
    """拔光 victim 的市政厅（**不**走 `own_tiles` 全占——那会连"余土"一起拿走）。
    `keep` 里的格放过（阴性对照要留一座厅）。返回被拔的格。"""
    halls = [p for p in world.own_tiles(victim)
             if world.tiles[p]["buildings"].get("市政厅") and p not in keep]
    for p in halls:
        world._conquer(p[0], p[1], by, "攻陷")
    return halls


def adjacent_world() -> mp.World:
    """两国挨着的世界：两个十字**不重叠**，但 楚 的中心与 秦 的东臂相贴
    ⇒ 秦 看得见楚的国祚（可见性/瞄准这类测试要用）。"""
    return mp.World(size=20, seed=7, nations=["秦", "楚"],
                    starts={"秦": (5, 5), "楚": (7, 6)})


class TestStartingTownhall(unittest.TestCase):
    """① 开局：核心格白送一座，且是**已落成**的（不是在建）。"""

    def test_every_nation_starts_with_one_on_core(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        for nm in ("秦", "楚", "齐"):
            c = cross_center(w, nm)
            self.assertEqual(w.tiles[c]["buildings"]["市政厅"], 1, f"{nm} 的核心格没有市政厅")
            self.assertEqual(w.tiles[c]["buildings"] and w.tiles[c]["pending"]["市政厅"], 0,
                             "白送的厅该是已落成，不是在建")
            self.assertTrue(w.has_townhall(nm))
        self.assertEqual(w.nation_building_count("秦", "市政厅"), 1, "开局只送一座，不是五座")

    def test_gift_is_free_and_does_not_burn_the_turn(self):
        """白送就是白送：不扣钱、不占"每地块每回合限建 1 座"的那个额度。"""
        w = mp.World(size=20, seed=7, nations=["秦"])
        c = cross_center(w, "秦")
        self.assertEqual(w.nations["秦"].res["黄金"], mp.START_RES["黄金"], "开国库不该少钱")
        self.assertEqual(w.tiles[c]["built_this_turn"], 0, "首都本回合还该能下单建造")
        self.assertEqual(w.spend.get("秦"), None, "白送不进总消费账（它不是花出去的钱）")

    def test_midgame_joiner_also_gets_one(self):
        """中途登场的国家（匈奴那种）走同一个 `_place_crosses` ⇒ 同样有国祚。"""
        w = mp.World(size=40, seed=11, nations=["秦", "楚"])
        ok, msg = w.add_nation("燕")
        self.assertTrue(ok, msg)
        self.assertTrue(w.has_townhall("燕"), "新登场的国家也该有市政厅")
        self.assertEqual(w.nation_building_count("燕", "市政厅"), 1)

    def test_center_out_of_bounds_still_gets_one(self):
        """畸形开局（中心格落在界外）也要兜底：否则这国一登场就是死的。"""
        w = mp.World(size=8, seed=1, nations=["秦"], starts={"秦": (-1, 0)})
        self.assertTrue(w.own_tiles("秦"), "前提：还剩得下一块地")
        self.assertTrue(w.has_townhall("秦"), "中心格出界时该在别的自家地上补一座")

    def test_center_is_always_plains(self):
        """★ 用户 2026-09-21：「中心格必然是平原」——国祚所在地不该顶着地形施工惩罚。

        **阴性对照**：拿裸 `MapGen` 看这些格**本来**是什么——必须真出现过非平原的种子，
        否则这条测试证明不了"改写"发生过（可能只是运气好）。
        """
        import mapgen
        overridden = 0
        for seed in range(30):
            w = mp.World(size=30, seed=seed, nations=["秦", "楚", "齐"])
            gen = mapgen.MapGen(seed, 30)
            for nm in w.nations:
                c = cross_center(w, nm)
                self.assertEqual(w.tiles[c]["terrain"], "平原", f"seed={seed} {nm} 中心不是平原")
                self.assertEqual(w.tile_terrain(*c), "平原", "地图/面板读的也是平原")
                if gen.terrain(*c) != "平原":
                    overridden += 1
        self.assertGreater(overridden, 0, "阴性对照失败：30 个种子里中心格本来就全是平原？")

    def test_center_resources_follow_the_forced_terrain(self):
        """改了地形就得按**平原**权重重算资源——不然留下一格"平原产石油"的怪物。

        ★ 查的是**地块自己的** `resources`（引擎内一切读它的地方——`build` 的资源上限、
        `land`/`tile` 面板——都以此为准）；`World.tile_resources()` 是**地图原始值**
        的取用口（和 `tile_terrain` 不同，它不看已物化地块）——这条不对称是旧有的，
        面板只在"格子还没物化"时才走它，所以两者不一致不会露到人前。
        """
        import mapgen
        for seed in range(20):
            w = mp.World(size=30, seed=seed, nations=["秦"])
            c = cross_center(w, "秦")
            want = mapgen.MapGen(seed, 30).resources_as(c[0], c[1], "平原")
            self.assertEqual(w.tiles[c]["resources"], want, f"seed={seed} 中心资源没按平原重算")
            self.assertEqual(w.tiles[c]["terrain"], "平原")


class TestDeathByTownhall(unittest.TestCase):
    """② 亡国条件＝市政厅尽失（领土还在也照亡）。"""

    def _world(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        return w

    def test_losing_last_townhall_kills_while_land_remains(self):
        w = self._world()
        before = len(w.own_tiles("楚"))
        kill_all_townhalls(w, "楚")
        self.assertNotIn("楚", w.nations, "厅被拔光了还活着")
        self.assertGreater(before, 1, "前提：楚本来不止一块地")
        self.assertTrue(w.own_tiles("楚") == [], "亡国后不该还有挂名的地")

    def test_second_townhall_keeps_nation_alive(self):
        """**阴性对照**：还有一座厅 ⇒ 丢地不亡国（别把灭国条件做成"丢地即死"）。"""
        w = self._world()
        spare = [p for p in w.own_tiles("楚") if not w.tiles[p]["buildings"].get("市政厅")][0]
        w.tiles[spare]["buildings"]["市政厅"] = 1          # 第二座（自己盖的那种）
        killed = kill_all_townhalls(w, "楚", keep=(spare,))
        self.assertIn("楚", w.nations, "还剩一座厅就亡国了——条件写成'丢地'了")
        self.assertTrue(killed, "前提：确实拔掉过一座")
        self.assertTrue(w.own_tiles("楚"), "前提：地也还在")
        self.assertTrue(w.has_townhall("楚"))

    def test_all_land_lost_still_kills(self):
        """**阴性对照**：领土尽失照旧亡国（老口径是新口径的特例，没被改掉）。"""
        w = self._world()
        for p in list(w.own_tiles("楚")):
            w._conquer(p[0], p[1], "秦", "攻陷")
        self.assertNotIn("楚", w.nations)

    def test_pending_townhall_does_not_count(self):
        """口径：**在建不算**——地上的厅被拔光、新的还在工地，就是没有厅（同"产出只认已落成"）。"""
        w = self._world()
        c = cross_center(w, "楚")
        w.tiles[c]["buildings"]["市政厅"] = 0
        w.tiles[c]["pending"]["市政厅"] = 1
        self.assertFalse(w.has_townhall("楚"))
        self.assertTrue(w._eliminate_if_dead("楚"), "在建的厅不该保命")

    def test_death_is_public_and_names_the_orphan_land(self):
        """亡国是公开事实，且要说清**余土成了无主之地**（否则没人知道有废墟可捡）。"""
        w = self._world()
        kill_all_townhalls(w, "楚")
        news = [e for e in w.events_for("齐", 40) if "亡国" in e]
        self.assertTrue(news, "第三方该收到亡国播报")
        self.assertIn("无主之地", news[-1], f"该报出余土的下场：{news[-1]}")


class TestOrphanedLand(unittest.TestCase):
    """③ 余土：无主空地、建筑保留、可被进驻继承。"""

    def _world_with_orphans(self):
        """楚 丢掉国祚（厅被拔），余下几块地带建筑 → 变成无主故土。返回 (w, 亡国前的自家格)。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 5
        halls = [p for p in w.own_tiles("楚") if w.tiles[p]["buildings"].get("市政厅")]
        rest = [p for p in w.own_tiles("楚") if p not in halls]
        keep = rest[0]
        w.tiles[keep]["buildings"]["兵营"] = 2      # 余土上留点建筑，好验证"保留"
        w.tiles[keep]["resources"] = {**w.tiles[keep]["resources"], "耕地": 3}
        name = w.tiles[keep]["name"]
        kill_all_townhalls(w, "楚")
        return w, keep, name

    def test_orphans_are_ownerless_with_buildings_kept(self):
        w, keep, name = self._world_with_orphans()
        t = w.tiles[keep]
        self.assertIsNone(t["owner"], "余土该是无主的")
        self.assertEqual(t["buildings"]["兵营"], 2, "建筑必须留在原地")
        self.assertEqual(t["buildings"]["市政厅"], 0, "厅是被拔走的那两格，不在这块上")
        self.assertEqual(t["name"], name, "地名不该被洗掉")
        self.assertIsNone(t["core"], "没有国了，核心主张随之作废")
        self.assertFalse([a for a in w.armies if a["owner"] == "野人"
                          and (a["x"], a["y"]) == keep], "无主故土不该凭空冒出野人")
        self.assertEqual(w.frontier_of("秦") & {keep}, set(),
                         "它已经物化了 ⇒ 不算'未物化的可拓荒地'（那是另一条口径）")

    def test_occupying_orphan_land_keeps_the_buildings(self):
        """atk 进驻无主故土：**格子复用**（地名/资源/建筑都在），不是白地重造。"""
        w, keep, name = self._world_with_orphans()
        res_before = dict(w.tiles[keep]["resources"])
        # 找一格与余土相邻的秦地，把一支军放上去（atk 只认相邻可达）
        spot = next(q for q in w.neighbors(*keep) if w.owned_by(*q) in ("秦", None))
        w.armies.append({"id": 99, "gid": 99, "name": "秦·步一军", "type": "步", "hp": 100,
                         "x": spot[0], "y": spot[1], "owner": "秦",
                         "moved_turn": -1, "engaged": False})
        ok, msg = w.attack("秦", [99], keep[0], keep[1])
        self.assertTrue(ok, f"进驻无主故土失败了：{msg}")
        t = w.tiles[keep]
        self.assertEqual(t["owner"], "秦")
        self.assertEqual(t["name"], name, "占了废墟却把地名换了")
        self.assertEqual(t["buildings"]["兵营"], 2, "占了废墟却把建筑洗了")
        self.assertEqual(t["resources"], res_before, "占了废墟却把资源洗了")
        self.assertIn("无主之地", msg, f"回执该说明这是无主之地：{msg}")

    def test_pending_build_on_orphan_land_lands_for_the_occupier(self):
        """余土上带**在建**建筑（亡国时那笔工地还没落地）：谁占了，它下回合就为谁落成。

        ★ 这不是为无主特判的规矩，是既有的"在建跟着地块走"（攻占别国工地同理）；
        余土只是不设例外。顺便钉住一条不变量：**余土上不会有已落成的厅**
        （亡国＝厅尽失 ⇒ 剩下的格上至多只有一座**在建**的），所以"捡现成的国祚"
        正常是捡不到的，捡到的是一座要等一回合的工地。
        """
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 5
        halls = [p for p in w.own_tiles("楚") if w.tiles[p]["buildings"].get("市政厅")]
        keep = [p for p in w.own_tiles("楚") if p not in halls][0]
        w.tiles[keep]["pending"]["市政厅"] = 1
        kill_all_townhalls(w, "楚")
        self.assertIsNone(w.tiles[keep]["owner"], "前提:楚已亡国、余土无主")
        self.assertEqual(w.tiles[keep]["pending"]["市政厅"], 1, "在建的工地该留在原地")
        w.armies.append({"id": 7, "gid": 7, "name": "秦·步一军", "type": "步", "hp": 100,
                         "x": w.neighbors(*keep)[0][0], "y": w.neighbors(*keep)[0][1],
                         "owner": "秦", "moved_turn": -1, "engaged": False})
        ok, msg = w.attack("秦", [7], keep[0], keep[1])
        self.assertTrue(ok, msg)
        self.assertEqual(w.tiles[keep]["pending"]["市政厅"], 1,
                         "占了废墟，那座在建的厅该继续为占领者施工")


class TestVisibility(unittest.TestCase):
    """配套口径：厅与城堡同档公开；无主故土全公开。"""

    def test_foreign_townhall_is_public_but_barracks_are_not(self):
        w = adjacent_world()
        c = cross_center(w, "楚")
        w.tiles[c]["buildings"]["兵营"] = 3
        self.assertTrue(w.visible_to("秦", *c), "前提：秦 看得见楚的中心（两家挨着）")
        seen = w.visible_buildings("秦", *c)
        self.assertEqual(seen.get("市政厅"), 1, "视野内他国市政厅必须看得见（否则没法瞄准国祚）")
        self.assertNotIn("兵营", seen, "兵营仍是内政底细")

    def test_orphan_land_shows_everything(self):
        """无主故土：全报（没有国，就没有内政底细要护）——不然"建筑保留"没人看得见。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 5
        halls = [p for p in w.own_tiles("楚") if w.tiles[p]["buildings"].get("市政厅")]
        rest = [p for p in w.own_tiles("楚") if p not in halls]
        keep = next(p for p in rest if p in w.neighbors(*halls[0]))
        w.tiles[keep]["buildings"]["兵营"] = 2
        kill_all_townhalls(w, "楚")
        self.assertIsNone(w.owned_by(*keep), "前提：这格已无主")
        self.assertTrue(w.visible_to("秦", *keep), "前提：秦 看得见它（就在刚打下的城边上）")
        self.assertEqual(w.visible_buildings("秦", *keep).get("兵营"), 2,
                         "无主故土的建筑该看得见（占了才知道＝这条口径白写了）")

    def test_panel_lists_visible_townhalls(self):
        """地图摘要要列出**视野内**他国的厅——那是"拔光即亡国"的瞄准清单。"""
        w = adjacent_world()
        out = mp_ai._fmt_map(w, "秦")
        self.assertIn("视野内市政厅", out)
        self.assertIn("拔光即亡国", out)

    def test_rules_and_panel_state_the_condition(self):
        """提示也要改（用户 2026-09-21）：「国祚」这条口径必须出现在 AI 读得到的地方。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        self.assertIn("亡国条件＝市政厅尽失", mp_ai.rules_text(w, "总览"))
        self.assertIn("国祚", mp_ai.rules_text(w, "建筑与造价"))
        self.assertIn("国祚: 市政厅 1 座", mp_ai._res_line(w, "秦"))
        tile = next(p for p in w.own_tiles("秦") if w.tiles[p]["buildings"].get("市政厅"))
        self.assertIn("市政厅", mp_ai._fmt_tile(w, "秦", tile))


class TestOldSaveMigration(unittest.TestCase):
    """老档（改版前存的）里谁都没有厅 ⇒ 读档时补发，否则第一次丢地就当场亡国。"""

    def test_load_backfills_one_per_living_nation(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 3
        w.tiles[cross_center(w, "秦")]["buildings"]["市政厅"] = 0   # 伪装成改版前的档
        w.tiles[cross_center(w, "楚")]["buildings"]["市政厅"] = 0
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            for t in data["tiles"].values():        # 老档：一座厅都没有
                t["buildings"]["市政厅"] = 0
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            w2 = mp.World.load(p)
        for nm in ("秦", "楚", "齐"):
            self.assertEqual(w2.nation_building_count(nm, "市政厅"), 1, f"{nm} 没补上市政厅")
            self.assertTrue(w2.has_townhall(nm))
        c = cross_center(w2, "秦")
        self.assertEqual(w2.tiles[c]["buildings"]["市政厅"], 1, "该补在核心格上")


if __name__ == "__main__":
    unittest.main()
