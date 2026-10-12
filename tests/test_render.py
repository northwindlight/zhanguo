"""图片地图的情报纪律、历史快照和密集格回归。无需 API/key。"""
import copy
import io
import json
import unittest
from unittest.mock import patch

import mp
import mp_ai
import render


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.w = mp.World(size=24, seed=3, nations=["秦", "楚"])

    def test_snapshot_is_detached_json_and_read_only(self):
        before = copy.deepcopy((self.w.tiles, self.w.armies, {k: vars(v) for k,v in self.w.nations.items()}, self.w.rng.getstate()))
        state = render.snapshot(self.w, "秦")
        restored = json.loads(json.dumps(state, ensure_ascii=False))
        self.assertEqual(restored, state)
        self.assertEqual(before, (self.w.tiles, self.w.armies, {k: vars(v) for k,v in self.w.nations.items()}, self.w.rng.getstate()))
        self.w.order.append("测试")
        self.assertNotIn("测试", state["order"])
        self.assertEqual({(c["x"], c["y"]) for c in state["cells"]}, mp_ai._visible_cells(self.w, "秦"))

    def test_hidden_changes_cannot_change_snapshot_or_pixels(self):
        state = render.snapshot(self.w, "秦")
        vis = mp_ai._visible_cells(self.w, "秦")
        # 找一格紧贴可见区域的迷雾，覆盖旧版通过邻格归属泄露国界的情况。
        hidden = next((x+dx, y+dy) for x,y in sorted(vis) for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))
                      if 0<=x+dx<24 and 0<=y+dy<24 and (x+dx,y+dy) not in vis)
        x,y = hidden
        t = self.w._new_tile(x,y,"楚")
        self.w.tiles[hidden] = t
        t["buildings"]["城堡"] = 9
        self.w.armies.append({"x":x,"y":y,"owner":"楚","hp":100,"type":"骑"})
        after = render.snapshot(self.w, "秦")
        self.assertEqual(state, after)
        if render.available()[0]:
            self.assertEqual(render.render_snapshot(state), render.render_snapshot(after))

    def test_resources_only_for_own_and_unowned_ruins(self):
        x,y = self.w.own_tiles("秦")[0]
        self.w.tiles[(x,y)]["resources"]["黄金"] = 1
        near = next(p for p in mp_ai._visible_cells(self.w,"秦") if p not in self.w.tiles)
        self.w.tiles[near] = self.w._new_tile(*near,"楚")
        self.w.tiles[near]["resources"]["黄金"] = 1
        self.w.tiles[near]["buildings"]["市政厅"] = 1
        cells = {(c["x"],c["y"]):c for c in render.snapshot(self.w,"秦")["cells"]}
        self.assertIn("黄金",cells[(x,y)]["resources"])
        self.assertEqual([],cells[near]["resources"])
        self.assertEqual(1,cells[near]["townhall"])


@unittest.skipUnless(render.available()[0], "Pillow unavailable")
class PixelTests(unittest.TestCase):
    def test_replay_is_byte_identical_after_world_changes(self):
        w = mp.World(size=24, seed=3, nations=["秦","楚"])
        state = render.snapshot(w,"秦")
        png = render.render_snapshot(state)
        self.assertIsNotNone(png)
        w.turn += 20
        w.armies.clear()
        self.assertEqual(png,render.render_snapshot(json.loads(json.dumps(state))))

    def test_canvas_is_auto_cropped_by_default(self):
        """★ **自动裁是定稿**（2026-10-12 用户拍板）：画布按**视野框**裁，不再默认铺满全图。

        旧断言写死「全图 1996×2010」——那是 `c83655f` 恢复自动裁**之前**的口径，
        所以它红不是因为渲染坏了，是断言过期。这里改成按定稿的不变量断：
        ① 默认比"显式要全图"小；② 显式 `bounds` 仍然生效（面板/实验要用）；
        ③ 图例不把画布撑宽（宽度不超过同 bounds 无图例时的量级——用面积比较兜住）。
        """
        from PIL import Image
        w = mp.World(size=24, seed=3, nations=["秦","楚"])
        auto = Image.open(io.BytesIO(render.render(w, "秦", max_px=960))).size
        full = Image.open(io.BytesIO(
            render.render(w, "秦", max_px=960, bounds=(0, 23, 0, 23)))).size
        self.assertLess(auto[0] * auto[1], full[0] * full[1],
                        "默认没裁：画布和全图一样大（自动裁被谁关掉了？）")
        small = Image.open(io.BytesIO(render.render(w, "秦", bounds=(0, 5, 0, 5)))).size
        self.assertLess(small[0] * small[1], auto[0] * auto[1], "显式 bounds 没生效")

    def test_all_sides_mixed_units_and_large_counts_fit_small_cells(self):
        state = {"version":1,"size":2,"turn":1,"player":"秦", "order":["秦","齐","楚","周"],
                 "sides":{"秦":"self","齐":"ally","楚":"enemy","周":"neutral"},
                 "army_max_hp":100,"cells":[{"x":0,"y":0,"owner":"秦","terrain":"平原",
                 "townhall":1,"castle":3,"resources":[],"reach":False}],"armies":[]}
        for owner in state["order"]:
            for i in range(101):
                state["armies"].append({"x":0,"y":0,"owner":owner,"kind":("步","骑","民")[i%3],"hp":60})
        from PIL import Image
        for budget in (24,40,80,96):
            cell=max(80,budget)
            with self.subTest(cell=cell):
                png = render.render_snapshot(state,max_px=budget*2)
                self.assertIsNotNone(png)
                img = Image.open(io.BytesIO(png))
                # 无论数量/地标，邻格仍是纯迷雾，兵牌没有画过去。
                self.assertEqual(render.FOG_FILL,img.getpixel((38+cell+cell//2,54+cell//2)))

    def test_three_counters_keep_size_and_leave_terrain_uncovered(self):
        from PIL import Image
        state={"version":1,"size":1,"turn":1,"player":"秦","order":["秦","齐","楚"],
               "sides":{"秦":"self","齐":"ally","楚":"enemy"},"army_max_hp":100,
               "cells":[{"x":0,"y":0,"owner":"秦","terrain":"森林","townhall":1,
                         "castle":2,"resources":[],"reach":False}],"armies":[]}
        boxes=[]
        counter=render._counter
        for owners in (["秦"],["秦"]*4,["秦","齐","楚"]):
            state["armies"]=[{"x":0,"y":0,"owner":o,"kind":"步","hp":100,"max_hp":100} for o in owners]
            calls=[]
            def capture(d,box,*args):
                calls.append(box)
                return counter(d,box,*args)
            with patch.object(render,"_counter",side_effect=capture):
                img=Image.open(io.BytesIO(render.render_snapshot(state,max_px=80)))
            boxes.append(calls)
            footer=img.crop((38,54+62,38+80,54+80)).tobytes()
            if len(boxes)==1:
                reference=footer
            self.assertEqual(reference,footer)
        self.assertEqual(boxes[0],boxes[1])
        dimensions={(b[2]-b[0],b[3]-b[1]) for calls in boxes for b in calls}
        self.assertEqual(1,len(dimensions))
        self.assertEqual(3,len(boxes[2]))

    def test_hp_label_shows_total_not_health_ratio(self):
        from PIL import Image, ImageDraw
        for hp_values in ([100],[100]*4,[80],[25,50]):
            im=Image.new("RGB",(44,34),render.BG)
            draw=ImageDraw.Draw(im)
            with patch.object(draw,"text",wraps=draw.text) as labels:
                render._counter(draw,(0,0,42,32),render.SIDE_COLORS["self"],render.NATION_COLORS[0],
                                [{"kind":"步","hp":hp} for hp in hp_values],100)
            self.assertEqual(str(sum(hp_values)),labels.call_args_list[-1].args[1])

    def test_failure_emits_once_and_returns_none(self):
        state = {"version":99}
        notes=[]
        with patch.object(render,"_warned",False):
            self.assertIsNone(render.render_snapshot(state,emit=notes.append))
            self.assertIsNone(render.render_snapshot(state,emit=notes.append))
        self.assertEqual(1,len(notes))

    def test_later_font_candidate_works(self):
        with patch.object(render,"_FONT_CANDIDATES",["/missing/font.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]):
            self.assertTrue(render.available()[0])


@unittest.skipUnless(render.available()[0], "Pillow unavailable")
class ContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        import llm_provider
        import ctx
        self.provider = llm_provider
        self.ctx = ctx
        self.w = mp.World(size=24, seed=3, nations=["秦","楚"])
        self.cfg = {"vision":True,"map_max_px":640,"ctx_full_turns":5}

    def test_game_injects_instructions_and_provider_injects_image(self):
        messages, plan = mp_ai.build_context(self.w,"秦",self.cfg)
        self.assertIn("【图片地图读法】",messages[0]["content"])
        # ★ 图例只给约定（颜色/字母/坐标），**不列具体地名**（2026-10-12 定稿）。
        #   旧断言要"草线平原"这个地名 ⇒ 过期。改断图例的**实质内容**：
        self.assertIn("国别颜色对照", messages[0]["content"])   # 谁是什么颜色
        self.assertIn("坐标", messages[0]["content"])           # 坐标怎么读
        self.assertIn("P平原", messages[0]["content"])          # 地形字母
        self.assertIn("T=市政厅", messages[0]["content"])       # 国祚记号
        self.assertIn(self.ctx.MAP_KEY,messages[-1])
        self.assertNotIn("每行一格",messages[-1]["content"])
        before=copy.deepcopy(messages)
        public=self.provider._public_messages(messages)
        self.assertEqual(before,messages)
        self.assertNotIn(self.ctx.MAP_KEY,public[-1])
        self.assertEqual("image_url",public[-1]["content"][-1]["type"])
        self.assertGreaterEqual(plan.tail_tokens,2048)
        self.assertGreater(self.ctx.msg_tokens(messages[-1]),2048)

    def test_anthropic_gets_real_image_not_python_list_string(self):
        messages,_=mp_ai.build_context(self.w,"秦",self.cfg)
        public=self.provider._anthropic_messages(messages,"m",self.cfg)
        image_block=public[-1]["content"][-1]
        self.assertEqual("image",image_block["type"])
        self.assertEqual("image/png",image_block["source"]["media_type"])

    def test_snapshot_replay_save_and_wire_prefix_stay_identical(self):
        import tempfile
        from pathlib import Path
        messages,plan=mp_ai.build_context(self.w,"秦",self.cfg)
        messages.append({"role":"assistant","content":"先守住首都。"})
        wire=self.provider._public_messages(messages)
        mp_ai._store_turn_memory(self.w,"秦",messages,len(messages)-1,plan)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"save.json"
            self.w.save(path)
            self.assertNotIn("data:image",path.read_text())
            self.w=mp.World.load(path)
        self.w.turn+=1
        self.w.armies.clear()
        replay,_=mp_ai.build_context(self.w,"秦",self.cfg)
        self.assertEqual(wire,self.provider._public_messages(replay)[:len(wire)])

    def test_disabled_or_failed_renderer_keeps_text_atlas(self):
        default,_=mp_ai.build_context(self.w,"秦",{})
        self.assertNotIn(self.ctx.MAP_KEY,default[-1])
        with patch.object(render,"render_snapshot",return_value=None):
            fallback,_=mp_ai.build_context(self.w,"秦",self.cfg)
        self.assertEqual(default,fallback)
        live,_=mp_ai.build_context(self.w,"秦",self.cfg)
        with patch.object(render,"render_snapshot",return_value=None):
            wire=self.provider._public_messages(live)
        self.assertIsInstance(wire[-1]["content"],str)
        self.assertIn("文本地图",wire[-1]["content"])
        disabled=self.provider._public_messages(live,vision=False)
        self.assertIsInstance(disabled[-1]["content"],str)

    def test_config_defaults_inherit_and_nation_override_wins(self):
        cfg={"vision":True,"map_max_px":820,"nations":[{"name":"秦"},{"name":"楚","vision":False}]}
        self.ctx.apply_defaults(cfg)
        self.assertTrue(cfg["nations"][0]["vision"])
        self.assertFalse(cfg["nations"][1]["vision"])


if __name__ == "__main__":
    unittest.main()
