# -*- coding: utf-8 -*-
"""国土/视野**地图**（常驻）+ 逐格明细/单格的**精确查询**（按需）。

覆盖三件事：
1. `_visible_cells`（反推法，快 280 倍）与引擎 `World.visible_to`（逐格问）**逐格等价** ——
   面板宁可快，但不能与引擎的视野口径漂开（那可是情报纪律）。
2. 地图：带坐标轴、只画自己国土+视野、有界、标记正确、**只读**（不物化地块、不改世界）。
3. 查询：`land` 的 cap/offset/filter、`tile` 的单格明细与**视野门禁**。

注意：`World()` 开局各国**自带** {len(CROSS)} 块地，所以本文件里所有格数都拿
`w.own_tiles(name)` 现算，不写死。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import sys
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import mp_ai  # noqa: E402


def _world(nations=("秦", "楚")):
    return mp.World(size=24, seed=3, nations=list(nations))


def _grow(w, name, n, *, farm=False, tower=False):
    """再划 n 块无主格给 name（手工物化，绕开扩张）。返回新加的那几块。"""
    added = []
    for x in range(w.size):
        for y in range(w.size):
            if len(added) >= n:
                break
            if (x, y) in w.tiles:
                continue
            t = w._new_tile(x, y, name)
            t["owner"] = name
            w.tiles[(x, y)] = t
            w._drop_guardians(x, y)      # 照引擎 `_conquer` 的口径：占下就清野人守军
            if farm and t["resources"].get("耕地", 0) >= 1:
                t["buildings"]["农场"] = 1
            added.append((x, y))
        if len(added) >= n:
            break
    if tower and added:
        w.tiles[added[0]]["buildings"]["瞭望塔"] = 1
    return added


def _grid_lines(s: str) -> str:
    """只取网格行（带行号的那些），去掉标题/摘要/图例——验"网格纯 ASCII"用。"""
    return "\n".join(l for l in s.splitlines()
                     if l.startswith("  ") and len(l) > 5 and l[2:5].strip().isdigit())


def _bbox(cells):
    xs = [p[0] for p in cells]
    ys = [p[1] for p in cells]
    return min(xs), max(xs), min(ys), max(ys)


class TestVisibleCellsEquivalence(unittest.TestCase):
    """★ 反推法必须与引擎的逐格判定完全一致（含瞭望塔圆与联盟共享视野）。"""

    def _cross_check(self, w, name):
        fast = mp_ai._visible_cells(w, name)
        slow = {(x, y) for x in range(w.size) for y in range(w.size)
                if w.visible_to(name, x, y)}
        self.assertEqual(fast, slow, f"{name} 的可见集合与 visible_to 不一致")

    def test_plain_territory(self):
        w = _world()
        _grow(w, "秦", 5)
        self._cross_check(w, "秦")

    def test_with_watchtower(self):
        w = _world()
        _grow(w, "秦", 5, tower=True)
        self._cross_check(w, "秦")

    def test_with_ally_shared_vision(self):
        """联盟共享视野：盟友的地盘及其相邻一圈也得算进来。"""
        w = _world()
        _grow(w, "秦", 3)
        _grow(w, "楚", 3)
        w.propose_bloc("秦", "北盟", ["楚"])
        w.accept_pact("楚", w.proposals[-1]["id"])
        self.assertIsNotNone(w.bloc_of("楚"))
        self._cross_check(w, "秦")
        self._cross_check(w, "楚")

    def test_cells_clipped_to_map(self):
        """贴边的国土：瞭望塔/邻圈算出界时必须裁掉（否则地图会越界取格）。"""
        w = mp.World(size=24, seed=3, nations=["秦"])
        t = w._new_tile(0, 0, "秦")
        t["owner"] = "秦"
        t["buildings"]["瞭望塔"] = 1
        w.tiles[(0, 0)] = t
        for p in mp_ai._visible_cells(w, "秦"):
            self.assertTrue(0 <= p[0] < w.size and 0 <= p[1] < w.size, p)
        self._cross_check(w, "秦")


class TestMapPanel(unittest.TestCase):
    def test_has_axes_and_summary(self):
        w = _world()
        _grow(w, "秦", 5, farm=True)
        own = w.own_tiles("秦")
        vis = mp_ai._visible_cells(w, "秦")
        x0, x1, y0, y1 = _bbox(vis)
        s = mp_ai._fmt_map(w, "秦")
        self.assertIn(f"地形/国土图 x {x0 + 1}→{x1 + 1}、y {y0 + 1}→{y1 + 1}", s)
        lab = [f"{(x + 1) % 100:02d}" for x in range(x0, x1 + 1)]
        self.assertIn("".join(d[0] + " " for d in lab), s, "缺列头（十位行）")
        self.assertIn("".join(d[1] + " " for d in lab), s, "缺列头（个位行）")
        self.assertIn(f"  {y0 + 1:3d} ", s, "缺行坐标")
        self.assertIn(f"国土 {len(own)} 块", s)
        self.assertIn(f"视野内 {len(vis)} 格", s)
        self.assertIn("每格 2 字符", s)

    def test_row_count_and_width_match_bbox(self):
        w = _world()
        _grow(w, "秦", 5)
        vis = mp_ai._visible_cells(w, "秦")
        x0, x1, y0, y1 = _bbox(vis)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        rows = [l for l in mp_ai._fmt_map(w, "秦").splitlines()
                if l.startswith("  ") and len(l) > 5 and l[2:5].strip().isdigit()]
        self.assertEqual(len(rows), bh, "行数应等于可见区高度")
        for r in rows:
            self.assertEqual(len(r) - 6, bw * 2, f"每格 2 字符、共 {bw} 列：{r!r}")

    def test_out_of_vision_stays_blank(self):
        """视野外的格只能显示占位符，不许泄内容。"""
        w = _world()
        _grow(w, "秦", 4)
        vis = mp_ai._visible_cells(w, "秦")
        x0, x1, y0, y1 = _bbox(vis)
        outside = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
                   if (x, y) not in vis]
        if not outside:
            self.skipTest("这个种子下包围盒被视野填满，没有可验的空角")
        s = mp_ai._fmt_map(w, "秦")
        rows = {int(l[2:5]): l for l in s.splitlines()
                if l.startswith("  ") and len(l) > 5 and l[2:5].strip().isdigit()}
        for (x, y) in outside:
            seg = rows[y + 1][6 + (x - x0) * 2: 8 + (x - x0) * 2]
            self.assertEqual(seg[0], "?", f"({x + 1},{y + 1}) 视野外却画了 {seg!r}")

    def _seg(self, s, w, pos):
        """取网格里某格的 2 字符。"""
        vis = mp_ai._visible_cells(w, "秦")
        x0, _, _, _ = _bbox(vis)
        rows = {int(l[2:5]): l for l in s.splitlines()
                if l.startswith("  ") and len(l) > 5 and l[2:5].strip().isdigit()}
        return rows[pos[1] + 1][6 + (pos[0] - x0) * 2: 8 + (pos[0] - x0) * 2]

    def test_no_info_leak_outside_vision(self):
        """★ 视野外的格只能是 `?` + 无记号：野人/**他国归属**/建筑都不许漏（与 visible_to 同纪律）。

        两个真踩过的漏点：`*`（野人）与**国别字母**——后者更严重（等于白送一张势力图）。
        下面显式构造"包围盒里、但视野外"的他国领土：秦两块飞地拉开包围盒，楚的地夹在中间。
        """
        w = mp.World(size=30, seed=3, nations=["秦", "楚"])
        for (x, y) in [(0, 0), (1, 0), (0, 1), (20, 20), (21, 20), (20, 21)]:
            t = w._new_tile(x, y, "秦")
            t["owner"] = "秦"
            w.tiles[(x, y)] = t
        for (x, y) in [(10, 10), (11, 10)]:      # 楚的地：离秦两块飞地都远 → 视野外
            t = w._new_tile(x, y, "楚")
            t["owner"] = "楚"
            w.tiles[(x, y)] = t
        vis = mp_ai._visible_cells(w, "秦")
        self.assertNotIn((10, 10), vis, "这个构造本该让楚的地在视野外")
        x0, x1, y0, y1 = _bbox(vis)
        self.assertTrue(x0 < 10 < x1, "楚的地应在包围盒内（才能验到框内但视野外的格）")
        s = mp_ai._fmt_map(w, "秦")
        rows = {int(l[2:5]): l for l in s.splitlines()
                if l.startswith("  ") and len(l) > 5 and l[2:5].strip().isdigit()}
        checked = 0
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if (x, y) in vis:
                    continue
                seg = rows[y + 1][6 + (x - x0) * 2: 8 + (x - x0) * 2]
                self.assertEqual(seg, "?.", f"({x + 1},{y + 1}) 视野外应画 ?.：{seg!r}")
                checked += 1
        self.assertGreater(checked, 0, "没验到任何框外格，用例白跑")
        self.assertNotIn("B", rows[11][6 + (10 - x0) * 2: 8 + (10 - x0) * 2],
                         "他国归属漏出了视野")

    def test_own_land_uses_own_country_code(self):
        """★ 统一口径：自家的地和别国的地写法**完全一致**，只是代码不同。

        不再有"大写=我的地"这套（用户 2026-09-18：「不要搞大小写了，和外国一样，
        只是换成本国代码」）——所以自家地上即使有建筑，第 2 位也只写自家代码。
        """
        w = _world()
        own = w.own_tiles("秦")
        x, y = own[0]
        w.tiles[(x, y)]["buildings"]["农场"] = 1     # 有建筑也不改变写法
        w.tiles[(x, y)]["pending"] = {"兵营": 1}     # 在建同理
        s = mp_ai._fmt_map(w, "秦")
        seg = self._seg(s, w, (x, y))
        self.assertEqual(seg[0], w.ter_char(x, y).lower(), "地形应统一小写")
        self.assertEqual(seg[1], "A", f"自家地应写自家代码 A：{seg!r}")
        self.assertIn("A=秦(你)", s, "国别对照里应标出(你)")
        self.assertNotIn("#", _grid_lines(s), "建筑不该再占归属那一位")

    def test_terrain_is_uniformly_lowercase_and_grid_has_no_case_trick(self):
        """第 1 位只允许 小写地形 / `?`；网格里不许再出现大写地形字母或 `#`。"""
        w = _world()
        _grow(w, "秦", 4)
        _grow(w, "楚", 4)
        s = mp_ai._fmt_map(w, "秦")
        grid = _grid_lines(s)
        first = {l[i] for l in grid.splitlines() for i in range(6, len(l), 2)}
        self.assertLessEqual(first, set("pfhmd?"), f"第 1 位出现了非法字符：{sorted(first)}")
        self.assertNotIn("#", grid)
        for up in "PFHMD":
            self.assertNotIn(up, grid, f"网格里还有大写地形 {up}（大小写 trick 应已废除）")

    def test_barbarian_marker(self):
        w = _world()
        _grow(w, "秦", 3)
        self.assertIn("*", mp_ai._fmt_map(w, "秦"), "野人守军没标出来")

    def test_armies_are_not_on_the_terrain_map(self):
        """★ 军队移出地形图（那是军事图的活）：地形图上不该出现 @/!。"""
        w = _world()
        own = w.own_tiles("秦")
        x, y = own[0]
        w.armies = [{"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
                     "x": x, "y": y, "owner": "秦", "moved_turn": -1, "engaged": False}]
        grid = _grid_lines(mp_ai._fmt_map(w, "秦"))
        self.assertNotIn("@", grid)
        self.assertNotIn("!", grid)

    def test_foreign_territory_shows_terrain_and_owner_letter(self):
        """视野里他国的地：**地形照给**（小写），第 2 位是该国字母 —— 两者都要有。"""
        w = _world()
        _grow(w, "秦", 3)
        _grow(w, "楚", 3)
        vis = mp_ai._visible_cells(w, "秦")
        foreign = next((p for p in sorted(vis)
                        if w.owned_by(*p) not in (None, "秦")), None)
        if foreign is None:
            self.skipTest("这个种子下视野里没有他国领土")
        s = mp_ai._fmt_map(w, "秦")
        seg = self._seg(s, w, foreign)
        self.assertEqual(seg[0], w.ter_char(*foreign).lower(), "他国地应显示小写地形")
        self.assertEqual(seg[1], "B", f"第 2 位应是国别字母（秦=A 楚=B）：{seg!r}")
        self.assertIn("B=楚", s, "缺国别字母对照")

    def test_map_is_read_only(self):
        """★ 渲染不许物化地块/改世界——它每回合都跑，一旦写状态就会毁掉复现性。"""
        w = _world()
        _grow(w, "秦", 4)
        snap = (len(w.tiles), sorted(w.tiles), len(w.armies), w.nations["秦"].res["粮食"])
        mp_ai._fmt_map(w, "秦")
        mp_ai._fmt_map(w, "秦")
        self.assertEqual(snap, (len(w.tiles), sorted(w.tiles), len(w.armies),
                                w.nations["秦"].res["粮食"]), "地图渲染改了世界状态")

    def test_deterministic(self):
        w = _world()
        _grow(w, "秦", 6, farm=True)
        self.assertEqual(mp_ai._fmt_map(w, "秦"), mp_ai._fmt_map(w, "秦"))

    def test_always_on_state_is_the_coordinate_atlas(self):
        """★ 常驻给的是**坐标地图**（每行一格文字），不是网格图（网格走 query panel=grid）。

        （用户 2026-09-19：「现在的地图对 llm 而言有点混乱……llm 还是文本理解好」。）
        """
        w = _world()
        _grow(w, "秦", 12)
        st = mp_ai.full_state(w, "秦")
        self.assertIn("坐标地图", st, "常驻里没有坐标地图的说明")
        self.assertIn("按势力分段", st)
        seg = st.split("【国土/视野】")[1].split("【军队】")[0]
        self.assertRegex(seg, r"\(\d+,\d+\)我平原", "坐标地图的行格式不对")
        self.assertNotIn("地形/国土图 x ", seg, "网格图不该再常驻（它走 panel=grid）")
        self.assertNotIn("军事图 x ", seg, "军事网格也不该常驻（驻军已进每行）")


class TestLandQuery(unittest.TestCase):
    def setUp(self):
        self.w = _world()
        _grow(self.w, "秦", 6, farm=True)
        self.own = self.w.own_tiles("秦")
        self.w.tiles[self.own[0]]["buildings"]["兵营"] = 1

    def test_no_filter_lists_tiles_and_frontier(self):
        s = mp_ai._fmt_land(self.w, "秦")
        self.assertIn(f"国土 {len(self.own)} 块", s)
        self.assertIn("可拓荒地", s)
        self.assertIn("兵营", s)

    def test_filter_by_building(self):
        s = mp_ai._fmt_land(self.w, "秦", filter_="兵营")
        self.assertIn(f"筛「兵营」命中 1 块（原国土 {len(self.own)} 块）", s)
        self.assertEqual(s.count("城L"), 1)

    def test_filter_counts_pending(self):
        x, y = self.own[1]
        self.w.tiles[(x, y)]["pending"] = {"兵营": 1}
        s = mp_ai._fmt_land(self.w, "秦", filter_="兵营")
        self.assertIn("命中 2 块", s)

    def test_filter_by_resource(self):
        s = mp_ai._fmt_land(self.w, "秦", filter_="耕地")
        hit = sum(1 for p in self.own if self.w.tiles[p]["resources"].get("耕地", 0) > 0)
        self.assertIn(f"筛「耕地」命中 {hit} 块", s)
        self.assertEqual(s.count("城L"), hit)

    def test_unknown_filter_is_rejected_with_options(self):
        s = mp_ai._fmt_land(self.w, "秦", filter_="可建")
        self.assertIn("只认", s)
        self.assertIn("兵营", s)      # 列出建筑名
        self.assertIn("耕地", s)      # 列出资源名

    def test_filter_skips_frontier_section(self):
        """筛选时只给命中的地，不再附可拓荒地清单。"""
        self.assertNotIn("可拓荒地", mp_ai._fmt_land(self.w, "秦", filter_="兵营"))

    def test_paging(self):
        small = mp_ai._fmt_land(self.w, "秦", cap=2, offset=0)
        self.assertIn("本次列 第 1–2 块", small)
        self.assertIn(f"还有 {len(self.own) - 2} 块，用 offset=2", small)
        page2 = mp_ai._fmt_land(self.w, "秦", cap=2, offset=2)
        self.assertIn("本次列 第 3–4 块", page2)
        self.assertNotEqual(small.splitlines()[1], page2.splitlines()[1])

    def test_offset_beyond_end_is_clamped(self):
        s = mp_ai._fmt_land(self.w, "秦", cap=2, offset=999)
        self.assertIn(f"本次列 第 {len(self.own)}–{len(self.own)} 块", s)

    def test_empty_territory(self):
        w = mp.World(size=16, seed=3, nations=["秦"])
        w.tiles = {}
        s = mp_ai._fmt_land(w, "秦")
        self.assertIn("国土 0 块", s)


class TestTileQuery(unittest.TestCase):
    def setUp(self):
        self.w = _world()
        _grow(self.w, "秦", 6, farm=True)
        self.pos = self.w.own_tiles("秦")[0]
        self.w.tiles[self.pos]["buildings"]["兵营"] = 1
        self.w.tiles[self.pos]["core"] = "秦"

    def test_shows_full_detail(self):
        s = mp_ai._fmt_tile(self.w, "秦", self.pos)
        self.assertIn(f"({self.pos[0] + 1},{self.pos[1] + 1})", s)
        self.assertIn("你的国土", s)
        self.assertIn("核心领土", s)
        self.assertIn("兵营", s)
        self.assertIn("建筑位", s)
        self.assertIn("资源：", s)
        self.assertIn("可下令建造", s)

    def test_refuses_out_of_vision(self):
        """★ 视野纪律：视野外的格一律不答（否则等于给了全图透视）。"""
        vis = mp_ai._visible_cells(self.w, "秦")
        far = next((x, y) for x in range(self.w.size) for y in range(self.w.size)
                   if (x, y) not in vis)
        s = mp_ai._fmt_tile(self.w, "秦", far)
        self.assertIn("不在你视野内", s)
        self.assertNotIn("建筑位", s)

    def test_外邦地_报城堡但仍瞒资源与其它建筑(self):
        """★ 城堡**公开**（用户 2026-09-19：「我想公开，因为不知道城堡很吃亏」），
        其余建筑与地块资源仍旧未探明。

        这里同时守着三条：① 城堡等级必须报（含防御加成）；② `资源：` 不许出现；
        ③ 敌国领土不许出现 `已建成`/`可下令建造`（原先是"资源与建筑一律不报"，
        2026-09-18 那轮把这两样都漏了出去，2026-09-19 只放行城堡这一档）。
        """
        w = _world()
        _grow(w, "秦", 3)
        vis = mp_ai._visible_cells(w, "秦")
        wild = next(p for p in sorted(vis) if w.owned_by(*p) is None)
        enemy = next(p for p in sorted(vis) if w.owned_by(*p) is None and p != wild)
        t = w._new_tile(*enemy, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 3
        t["buildings"]["兵营"] = 1
        t["pending"] = {"农场": 1}
        w.tiles[enemy] = t
        out = mp_ai._fmt_tile(w, "秦", enemy)
        self.assertIn("城堡：L3", out, "视野内的敌国城堡必须报")
        self.assertIn("+30% 防御", out, "该连它对防御的加成一起报")
        self.assertNotIn("资源：", out, f"资源仍不该报：{out}")
        self.assertNotIn("兵营", out, f"其它建筑仍不该报：{out}")
        self.assertNotIn("已建成", out, f"不该报建设明细：{out}")
        self.assertNotIn("可下令建造", out, f"不该给建造建议：{out}")
        self.assertIn("未探明", out)
        # 无主野地：没有城堡可报，同样不报资源
        out_wild = mp_ai._fmt_tile(w, "秦", wild)
        self.assertNotIn("资源：", out_wild)
        self.assertNotIn("兵营", out_wild)

    def test_视野外的城堡也不报(self):
        """城堡公开的前提是"看得见"——视野外照旧不给（与 visible_to 同纪律）。"""
        w = _world()
        _grow(w, "秦", 3)
        vis = mp_ai._visible_cells(w, "秦")
        far = next((x, y) for x in range(w.size) for y in range(w.size)
                   if (x, y) not in vis)
        t = w._new_tile(*far, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 5
        w.tiles[far] = t
        self.assertEqual(w.visible_buildings("秦", *far), {}, "视野外不该看得见城堡")
        self.assertIn("不在你视野内", mp_ai._fmt_tile(w, "秦", far))

    def test_visible_buildings_rule(self):
        """引擎侧唯一口径：自家地→全部；视野内他国→只有城堡；视野外→空。"""
        w = _world()
        own = _grow(w, "秦", 3)
        w.tiles[own[0]]["buildings"]["城堡"] = 2
        w.tiles[own[0]]["buildings"]["兵营"] = 1
        self.assertEqual(w.visible_buildings("秦", *own[0]), {"城堡": 2, "兵营": 1})
        adj = w.neighbors(*own[0])[0]
        t = w._new_tile(*adj, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 3
        t["buildings"]["兵营"] = 2
        w.tiles[adj] = t
        self.assertEqual(w.visible_buildings("秦", *adj), {"城堡": 3}, "只该看得见城堡")
        empty = next((x, y) for x in range(w.size) for y in range(w.size)
                     if (x, y) not in w.tiles)
        self.assertEqual(w.visible_buildings("秦", *empty), {}, "未物化的格没有建筑")

    def test_地形图摘要列出视野内城堡(self):
        w = _world()
        own = _grow(w, "秦", 3)
        adj = w.neighbors(*own[0])[0]
        t = w._new_tile(*adj, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 4
        w.tiles[adj] = t
        self.assertIn(f"视野内城堡", mp_ai._fmt_map(w, "秦"))
        self.assertIn(f"L4@({adj[0] + 1},{adj[1] + 1})", mp_ai._fmt_map(w, "秦"))

    def test_own_land_still_shows_everything(self):
        w = _world()
        own = _grow(w, "秦", 3)
        w.tiles[own[0]]["buildings"]["农场"] = 1
        out = mp_ai._fmt_tile(w, "秦", own[0])
        for k in ("资源：", "建筑位", "已建成", "可下令建造"):
            self.assertIn(k, out, f"自家地应给全明细，缺 {k}")

    def test_reports_unowned(self):
        vis = mp_ai._visible_cells(self.w, "秦")
        wild = next(p for p in sorted(vis) if self.w.owned_by(*p) != "秦")
        s = mp_ai._fmt_tile(self.w, "秦", wild)
        self.assertIn("无主", s)

    def test_render_is_read_only(self):
        snap = (len(self.w.tiles), sorted(self.w.tiles))
        mp_ai._fmt_tile(self.w, "秦", self.pos)
        self.assertEqual(snap, (len(self.w.tiles), sorted(self.w.tiles)))

    def test_out_of_bounds(self):
        s = mp_ai._fmt_tile(self.w, "秦", (self.w.size + 3, 0))
        self.assertIn("超出地图范围", s)


class TestQueryDispatch(unittest.TestCase):
    """走 `_exec` 的真实分发（模型看到的那一层）。"""

    def setUp(self):
        self.w = _world()
        _grow(self.w, "秦", 6, farm=True)
        self.own = self.w.own_tiles("秦")
        self.w.tiles[self.own[0]]["buildings"]["兵营"] = 1

    def test_tile_panel_by_xy_and_by_name(self):
        x, y = self.own[0]
        s = mp_ai._exec(self.w, "秦", "query", {"panel": "tile", "x": x + 1, "y": y + 1})
        self.assertIn("建筑位", s)
        name = self.w.tiles[(x, y)]["name"]
        s2 = mp_ai._exec(self.w, "秦", "query", {"panel": "tile", "at": name})
        self.assertIn(name, s2)

    def test_tile_panel_without_target_gives_usage(self):
        self.assertIn("用法", mp_ai._exec(self.w, "秦", "query", {"panel": "tile"}))

    def test_land_panel_passes_cap_offset_filter(self):
        s = mp_ai._exec(self.w, "秦", "query",
                        {"panel": "land", "cap": 2, "offset": 2, "filter": "兵营"})
        self.assertIn("本次列 第 1–1 块", s)      # 筛完只剩 1 块
        self.assertIn("筛「兵营」", s)

    def test_bad_cap_offset_do_not_crash(self):
        s = mp_ai._exec(self.w, "秦", "query",
                        {"panel": "land", "cap": "很多", "offset": None})
        self.assertIn(f"国土 {len(self.own)} 块", s)

    def test_unknown_panel_falls_back_to_all(self):
        self.assertIn("【国土/视野】",
                      mp_ai._exec(self.w, "秦", "query", {"panel": "不存在的面板"}))

    def test_query_schema_advertises_new_panel_and_args(self):
        """工具表是模型唯一的说明书——新面板/新参数必须写进去。"""
        sch = [s for s in mp_ai.tool_schemas(self.w, "秦")
               if s["function"]["name"] == "query"][0]["function"]
        self.assertIn("tile", sch["parameters"]["properties"]["panel"]["enum"])
        for k in ("cap", "offset", "filter", "x", "y", "at"):
            self.assertIn(k, sch["parameters"]["properties"], f"query 少了参数 {k}")
        self.assertIn("常驻", sch["description"])


class TestMilMap(unittest.TestCase):
    """军事图：自家 1..9a..z、他国 A..Z；一格只画一个符号；野人不在图上。"""

    def setUp(self):
        self.w = _world()
        _grow(self.w, "秦", 4)
        self.own = self.w.own_tiles("秦")
        a, b = self.own[0], self.own[1]
        self.w.armies = [
            {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
             "x": a[0], "y": a[1], "owner": "秦", "moved_turn": -1, "engaged": False},
            {"id": 2, "gid": 2, "name": "秦·骑二军", "type": "骑", "hp": 80,
             "x": b[0], "y": b[1], "owner": "秦", "moved_turn": -1, "engaged": False},
        ]

    def test_own_armies_use_own_country_code(self):
        """★ 军队用**国别符号**，不给每支军编号（用户 2026-09-18：「军队不用数字，也用国家符号就行」）。"""
        s = mp_ai._fmt_mil_map(self.w, "秦")
        grid = _grid_lines(s)
        self.assertIn("A", grid, "自家军应画自家国别代码 A")
        body = "\n".join(l[6:] for l in grid.splitlines())     # 去掉行号（行号本身是数字）
        self.assertLessEqual(set(body) - set(" \n"), set(".ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                             f"格子区只该有 . 与国别字母，却出现：{sorted(set(body))}")
        for digit in "0123456789":
            self.assertNotIn(digit, body, f"军事图格子区不该出现数字 {digit!r}")
        self.assertIn("自家军 2 支", s)

    def test_foreign_army_uses_its_own_code(self):
        vis = mp_ai._visible_cells(self.w, "秦")
        spot = next(p for p in sorted(vis) if self.w.owned_by(*p) is None)
        self.w.armies.append({"id": 3, "gid": 3, "name": "楚·步三军", "type": "步", "hp": 60,
                              "x": spot[0], "y": spot[1], "owner": "楚",
                              "moved_turn": -1, "engaged": False})
        s = mp_ai._fmt_mil_map(self.w, "秦")
        self.assertIn("B", _grid_lines(s), "他国军应画该国代码 B")
        self.assertIn("B×1", s, "摘要里应有他国军的国籍计数")
        self.assertNotIn("楚·步三军", s, "逐军明细不该再堆在图注里（那在【军队】/【威胁】面板）")

    def test_same_nation_stack_draws_one_symbol(self):
        """★ 重叠只标一个：同国叠兵在图上就是一个符号（不重复、不编号）。"""
        vis = mp_ai._visible_cells(self.w, "秦")
        spot = next(p for p in sorted(vis) if self.w.owned_by(*p) is None)
        for i in (3, 4):
            self.w.armies.append({"id": i, "gid": i, "name": f"楚·步{i}军", "type": "步",
                                  "hp": 60, "x": spot[0], "y": spot[1], "owner": "楚",
                                  "moved_turn": -1, "engaged": False})
        s = mp_ai._fmt_mil_map(self.w, "秦")
        self.assertEqual(_grid_lines(s).count("B"), 1, "同格两支楚军只该画一个 B")
        self.assertIn("B×2", s, "两支都该计入国籍计数")

    def test_cross_nation_stack_is_noted(self):
        """跨国的同格重叠要单独指出来（那才是战术信息：敌我挤在一格）。"""
        vis = mp_ai._visible_cells(self.w, "秦")
        spot = next(p for p in sorted(vis) if self.w.owned_by(*p) is None)
        self.w.armies.append({"id": 3, "gid": 3, "name": "楚·步三军", "type": "步", "hp": 60,
                              "x": spot[0], "y": spot[1], "owner": "楚",
                              "moved_turn": -1, "engaged": False})
        self.w.armies.append({"id": 4, "gid": 4, "name": "秦·步一军", "type": "步", "hp": 100,
                              "x": spot[0], "y": spot[1], "owner": "秦",
                              "moved_turn": -1, "engaged": False})
        s = mp_ai._fmt_mil_map(self.w, "秦")
        self.assertIn(f"同格混编：A+B@({spot[0] + 1},{spot[1] + 1})", s)
        self.assertEqual(_grid_lines(s).count("A") + _grid_lines(s).count("B"), 3,
                         "混编格只画一个符号（自家优先 → A）")

    def test_barbarians_are_not_on_the_military_map(self):
        """野人守军每块无主地都有——画进军事图会把图糊满；它在地形图里用 * 表示。"""
        w = _world()
        _grow(w, "秦", 3)
        s = mp_ai._fmt_mil_map(w, "秦")
        barb = [a for a in w.armies if a["owner"] == "野人"]
        self.assertTrue(barb, "这个局面本该有野人")
        self.assertNotIn("野人", s.replace("野人守军**不在**此图", ""))

    def test_deterministic(self):
        self.assertEqual(mp_ai._fmt_mil_map(self.w, "秦"), mp_ai._fmt_mil_map(self.w, "秦"))

    def test_read_only(self):
        snap = (len(self.w.tiles), sorted(self.w.tiles), len(self.w.armies))
        mp_ai._fmt_mil_map(self.w, "秦")
        self.assertEqual(snap, (len(self.w.tiles), sorted(self.w.tiles), len(self.w.armies)))


class TestCoordinateAtlas(unittest.TestCase):
    """★ 坐标地图（常驻默认）：每行一格、自带语义。

    用户 2026-09-19：「现在的地图对 llm 而言有点混乱……加一个 tool 展示其他地图，
    默认是坐标地图……llm 还是文本理解好」。
    """

    def _setup(self):
        w = mp.World(size=24, seed=3, nations=["秦", "楚"])
        own = w.own_tiles("秦")[0]
        w.tiles[own]["buildings"]["城堡"] = 2                     # 自家 L2
        adj = w.neighbors(*own)[0]
        t = w._new_tile(*adj, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 3
        w.tiles[adj] = t
        w._drop_guardians(*adj)
        # ★ 用 `+` 追加而不是整体替换：`w.armies = [...]` 会把全图 566 个野人守军一起冲掉，
        #   于是"无主格都有野人"这个真实局面在测试里消失（这正是刚才三条用例失败的原因）。
        w.armies += [
            {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
             "x": own[0], "y": own[1], "owner": "秦", "moved_turn": -1, "engaged": False},
            {"id": 2, "gid": 2, "name": "楚·骑二军", "type": "骑", "hp": 100,
             "x": adj[0], "y": adj[1], "owner": "楚", "moved_turn": -1, "engaged": False},
        ]
        return w, own, adj

    def test_line_format(self):
        """`(x,y)归属地形，[L2城][，地名][，番号…]` —— **番号垫在行尾**（它长度不定）。

        （用户 2026-09-19：「先地名后军队，这样美观，军队会扩展」。）
        """
        w, own, adj = self._setup()
        out = mp_ai._fmt_atlas(w, "秦")
        mine = next(l for l in out.splitlines() if l.startswith(f"({own[0]+1},{own[1]+1})"))
        self.assertTrue(mine.startswith(f"({own[0]+1},{own[1]+1})我平原，L2城，"), mine)
        self.assertTrue(mine.endswith("，步1"), f"番号该在行尾：{mine}")
        outer = next(l for l in out.splitlines() if l.startswith(f"({adj[0]+1},{adj[1]+1})"))
        self.assertTrue(outer.startswith(f"({adj[0]+1},{adj[1]+1})楚山地，L3城，"), outer)
        self.assertTrue(outer.endswith("，骑2"), f"番号该在行尾：{outer}")

    def test_野与空地(self):
        w, _own, _adj = self._setup()
        lines = mp_ai._fmt_atlas(w, "秦").splitlines()
        self.assertTrue(any(l.endswith("，野人") for l in lines),
                        "无主+野人守军该写成 `(x,y)野山地，野人`")
        # 清掉某块无主地的野人 → 它就该只剩 `野+地形`（空地）。
        # （注意：开局每块无主地都有野人，所以"空地"必须显式造出来。）
        vis = sorted(mp_ai._visible_cells(w, "秦"))
        wild = next(p for p in vis if w.owned_by(*p) is None and mp_ai._has_barb(w, *p))
        w._drop_guardians(*wild)
        line = next(l for l in mp_ai._fmt_atlas(w, "秦").splitlines()
                    if l.startswith(f"({wild[0] + 1},{wild[1] + 1})"))
        self.assertEqual(len(line.split("，")), 1, f"空地该只有 `(x,y)野地形` 一段：{line}")
        self.assertNotIn("城", line, f"空地不该有城：{line}")

    def test_只列视野内的格(self):
        """视野外一律不列（与迷雾同纪律）。"""
        w, _own, _adj = self._setup()
        vis = mp_ai._visible_cells(w, "秦")
        far = next((x, y) for x in range(w.size) for y in range(w.size) if (x, y) not in vis)
        t = w._new_tile(*far, "楚")
        t["owner"] = "楚"
        w.tiles[far] = t
        out = mp_ai._fmt_atlas(w, "秦")
        self.assertNotIn(f"({far[0]+1},{far[1]+1})楚", out, "视野外的格不该出现在坐标地图里")

    def test_番号段_格主的排前面且省前缀(self):
        """同格混编：格主的部队省归属前缀、排前面；别人的带国名。"""
        w, own, _adj = self._setup()
        w.armies.append({"id": 3, "gid": 3, "name": "楚·步三军", "type": "步", "hp": 60,
                         "x": own[0], "y": own[1], "owner": "楚",
                         "moved_turn": -1, "engaged": False})
        out = mp_ai._fmt_atlas(w, "秦")
        self.assertIn("步1、楚步3", out, "格主的部队应排前面且省前缀：" + out)

    def test_按势力分段(self):
        """★ 分段顺序：我 → 野人 → 其他国，段间空行（用户 2026-09-19：

        「顺便按自己，野人，其他国排开 要一个回车分割势力」）。
        """
        w, _own, _adj = self._setup()
        out = mp_ai._fmt_atlas(w, "秦")
        self.assertIn("【我】", out)
        self.assertIn("【野人】", out)
        self.assertIn("【楚】", out)
        self.assertLess(out.index("【我】"), out.index("【野人】"))
        self.assertLess(out.index("【野人】"), out.index("【楚】"))
        self.assertIn("\n\n【野人】", out, "段间要有空行")
        self.assertIn("\n\n【楚】", out)

    def test_格局图仍可按需取(self):
        """网格版没删——`query panel=grid` 取，且默认面板不再是网格。"""
        w, _own, _adj = self._setup()
        g = mp_ai._exec(w, "秦", "query", {"panel": "grid"})
        self.assertIn("地形/国土图 x ", g)
        self.assertIn("军事图 x ", g)
        self.assertIn("坐标地图", g, "取网格图时应提示默认是坐标地图")
        self.assertNotIn("地形/国土图 x ", mp_ai.full_state(w, "秦"))

    def test_query_schema_advertises_grid(self):
        w, _own, _adj = self._setup()
        sch = [x for x in mp_ai.tool_schemas(w, "秦")
               if x["function"]["name"] == "query"][0]["function"]
        self.assertIn("grid", sch["parameters"]["properties"]["panel"]["enum"])
        self.assertIn("grid=", sch["description"])


class TestCastleReportedEverywhere(unittest.TestCase):
    """★ 城堡公开要**铺满所有"报某格/某军"的地方**（2026-09-19 用户：

    「对齐视野机制了吗，不光是地图改，视野也应该回报城堡」）。

    光在 tile 查询里报是不够的——守方靠城减伤，攻方在地图、军队栏、威胁栏、战报里
    都得看得见这个城。这里逐个钉住。
    """

    def _setup(self):
        w = mp.World(size=20, seed=3, nations=["秦", "楚"])
        own = w.own_tiles("秦")[0]
        w.tiles[own]["buildings"]["城堡"] = 2          # 自家城堡（L2）
        adj = w.neighbors(*own)[0]
        t = w._new_tile(*adj, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 3                     # 敌国城堡（L3）
        w.tiles[adj] = t
        w.armies = [
            {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
             "x": own[0], "y": own[1], "owner": "秦", "moved_turn": -1, "engaged": False},
            {"id": 2, "gid": 2, "name": "楚·步二军", "type": "步", "hp": 100,
             "x": adj[0], "y": adj[1], "owner": "楚", "moved_turn": -1, "engaged": False},
        ]
        return w, own, adj

    def test_地图摘要(self):
        w, _own, adj = self._setup()
        self.assertIn(f"L3@({adj[0] + 1},{adj[1] + 1})", mp_ai._fmt_map(w, "秦"))

    def test_军队面板报自家军驻的城(self):
        w, _own, _adj = self._setup()
        self.assertIn("城L2", mp_ai._fmt_armies(w, "秦"))

    def test_威胁面板报敌军脚下的城(self):
        w, _own, _adj = self._setup()
        out = mp_ai._fmt_threats(w, "秦")
        self.assertIn("楚·步二军", out)
        self.assertIn("城L3", out, "报『敌军在某格』就要报出它脚下的城")

    def test_战报格子标记带城(self):
        w, _own, adj = self._setup()
        w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [],
                   "atk_followers": [], "turn": 1}]
        w.attack("秦", [1], adj[0], adj[1])
        w.rng = random.Random(1)
        w.resolve_turn()
        rep = " ".join(w.events_for("秦", limit=6))
        self.assertIn("城L3", rep, f"战报应点出守方的城：{rep}")

    def test_冲入交战回执报城(self):
        """atk 冲进去的那一刻就要知道对面有城（不然下一回合才发现打不动）。"""
        w, _own, adj = self._setup()
        w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [],
                   "atk_followers": [], "turn": 1}]
        ok, msg = w.attack("秦", [1], adj[0], adj[1])
        self.assertTrue(ok, msg)
        self.assertIn("城L3", msg, f"冲入回执应报出该格城堡：{msg}")

    def test_mv撞墙报错报城(self):
        """mv 撞在敌国领土上时，顺带告诉你那格有城（进攻决策就差这一句）。

        注意：城堡是在**调用点**补的，不是写进 `_mv_wall` —— 后者在 `_reachable`
        的逐格 BFS 热路径上，往里加 `visible_to`（O(全表)）会把寻路拖垮。
        """
        w, _own, adj = self._setup()
        ok, msg = w.move("秦", 1, adj[0], adj[1])
        self.assertFalse(ok, msg)
        self.assertIn("城L3", msg, f"mv 报错应带上该格城堡：{msg}")

    def test_占领回执报缴获的城(self):
        """★ 占领后建筑原样保留 ⇒ 回执要报出缴获的要塞。

        这条日志是**视野广播**的（同格谁看得见谁收到），所以只能带公开信息：
        城堡可以，兵营/工厂不行（那些会向第三者泄露被占国的内政）。
        """
        w = mp.World(size=20, seed=3, nations=["秦", "楚"])
        t = w._new_tile(5, 5, "楚")
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 3
        t["buildings"]["兵营"] = 2
        w.tiles[(5, 5)] = t
        ok, msg = w._conquer(5, 5, "秦", "攻陷")
        self.assertTrue(ok, msg)
        self.assertIn("（城L3）", msg, "占领回执应报出缴获的城堡")
        self.assertNotIn("兵营", msg, "广播日志不许带非公开信息")
        self.assertEqual(w.tiles[(5, 5)]["buildings"]["兵营"], 2, "建筑应原样保留")

    def test_拓疆回执不带城(self):
        w = mp.World(size=20, seed=3, nations=["秦"])
        ok, msg = w._conquer(8, 8, "秦", "进驻")
        self.assertTrue(ok, msg)
        self.assertNotIn("城L", msg)

    def test_无城堡时不加后缀(self):
        """没城就不该冒出一个 城L0——那是噪声。"""
        w = mp.World(size=20, seed=3, nations=["秦", "楚"])
        own = w.own_tiles("秦")[0]
        w.armies = [{"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
                     "x": own[0], "y": own[1], "owner": "秦", "moved_turn": -1,
                     "engaged": False}]
        self.assertNotIn("城L", mp_ai._fmt_armies(w, "秦"))
        self.assertNotIn("城L", mp_ai._fmt_threats(w, "秦"))


class TestGridIsPureAscii(unittest.TestCase):
    """★ 网格必须是**纯 ASCII 且半角**。

    `■`(U+25A0) 与 `·`(U+00B7) 的 East Asian Width 是 **Ambiguous** —— 在 CJK 等宽字体下
    按全角渲染，整张格子会错位（2026-09-18 用户发现）。这张用例把它钉死。
    """

    def _assert_ascii_grid(self, text):
        grid = _grid_lines(text)
        self.assertTrue(grid, "没取到网格行")
        for line in grid.splitlines():
            for ch in line:
                self.assertLess(ord(ch), 128, f"网格里出现非 ASCII 字符 {ch!r}：{line!r}")
                self.assertEqual(unicodedata.east_asian_width(ch), "Na",
                                 f"网格里出现非半角字符 {ch!r}：{line!r}")

    def test_terrain_map_grid(self):
        w = _world()
        _grow(w, "秦", 6, farm=True)
        self._assert_ascii_grid(mp_ai._fmt_map(w, "秦"))

    def test_military_map_grid(self):
        w = _world()
        own = _grow(w, "秦", 4)
        w.armies = [{"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
                     "x": own[0][0], "y": own[0][1], "owner": "秦",
                     "moved_turn": -1, "engaged": False}]
        self._assert_ascii_grid(mp_ai._fmt_mil_map(w, "秦"))

    def test_axis_labels_are_ascii(self):
        w = _world()
        _grow(w, "秦", 4)
        for fn in (mp_ai._fmt_map, mp_ai._fmt_mil_map):
            for line in fn(w, "秦").splitlines()[1:3]:   # 只查两行列头（标题是中文散文）
                for ch in line:
                    self.assertLess(ord(ch), 128, f"坐标轴/标题混入非 ASCII：{line!r}")

    def test_no_ambiguous_width_markers_left(self):
        """守死"别再手滑用 ■/·"：那两个字符的宽度是歧义值，CJK 字体下按全角渲染。
        只查记号（中文散文与破折号不在此列——它们不在网格里，不影响对齐）。"""
        for bad in ("■", "·", "▣", "□"):
            self.assertNotIn(bad, mp_ai.MAP_LEGEND + mp_ai.MIL_LEGEND,
                             f"图例里还有宽度歧义的记号 {bad!r}")


if __name__ == "__main__":
    unittest.main()
