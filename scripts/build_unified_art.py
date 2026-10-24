#!/usr/bin/env python3
"""Draw the original SVG art study and export its textures/previews.

Offline developer tool; never imports the game or edits its running assets.
Requires Pillow and the system librsvg/cairo libraries (or CairoSVG).
"""
from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
from dataclasses import dataclass, field
import hashlib
import html
import io
import json
from pathlib import Path
import random
import re
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "mods/ui/desktop/assets/unified-v1"
PREVIEW = ROOT / "docs/design/unified-v1"
NS = "http://www.w3.org/2000/svg"

# One light direction; color ramps are shared by terrain, buildings and actors.
P = {
    "ink": "303d3b", "ink_soft": "47514a", "wood_dark": "59483b",
    "wood": "886444", "wood_mid": "ae8757", "wood_light": "d1ae71",
    "wall_dark": "8d9078", "wall": "c4c2a0", "wall_light": "e1d5ac",
    "roof_dark": "334d55", "roof": "4b6971", "roof_mid": "66848b",
    "roof_light": "90a4a1", "clay_dark": "895644", "clay": "b77854",
    "clay_light": "d39969", "stone_dark": "414c4d", "stone": "64716c",
    "stone_mid": "7d8980", "stone_light": "9ca495", "stone_top": "707b73",
    "grass_dark": "54724b", "grass": "739357", "grass_light": "86a365",
    "grass_high": "9caf70", "leaf_dark": "355b46", "leaf": "507b49",
    "leaf_mid": "71964e", "leaf_light": "97ad61", "pine_dark": "304f48",
    "pine": "416d59", "pine_mid": "59866b", "pine_light": "81a181",
    "dirt_dark": "8f805d", "dirt": "b4a075", "dirt_light": "c7b58b",
    "sand_dark": "b2ae7d", "sand": "d6c997", "sand_light": "e7daab",
    "sea_deep": "365b80", "sea_mid": "47799b", "sea_shallow": "70a2b0",
    "sea_light": "85b0b5", "snow_dark": "b0c3bc", "snow": "d7e1d2",
    "snow_light": "eff0db", "skin_dark": "aa7855", "skin": "d0a579",
    "skin_light": "e5c399", "cloth_dark": "3e5c60", "cloth": "63868a",
    "cloth_light": "8da6a1", "gold_dark": "a38345", "gold": "d3b965",
    "gold_light": "ead68b", "red_dark": "824d43", "red": "b46e53",
    "red_light": "d19064", "metal": "a6b2a4", "metal_light": "d5d9bd",
}


def color(v: str) -> str:
    return "#" + P.get(v, v.lstrip("#"))


class Svg:
    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.parts: list[str] = []

    def rect(self, x, y, w, h, fill, **attrs):
        if w <= 0 or h <= 0:
            return
        more = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color(fill)}"{more}/>')

    def poly(self, points, fill):
        points = " ".join(f"{x},{y}" for x, y in points)
        self.parts.append(f'<polygon points="{points}" fill="{color(fill)}"/>')

    def group(self, name: str, content: str, x=0, y=0):
        content = prefix_ids(content, name)
        self.parts.append(f'<g id="{name}" transform="translate({x} {y})">{content}</g>')

    def begin(self, name):
        self.parts.append(f'<g id="{name}">')

    def end(self):
        self.parts.append('</g>')

    def body(self):
        return "\n".join(self.parts)

    def document(self):
        return (f'<svg xmlns="{NS}" width="{self.width}" height="{self.height}" '
                f'viewBox="0 0 {self.width} {self.height}" shape-rendering="crispEdges">\n'
                + self.body() + "\n</svg>\n")


def prefix_ids(content, prefix):
    return re.sub(r'\bid="([^"]+)"', lambda match: f'id="{prefix}-{match[1]}"', content)


class Rasterizer:
    """Use the installed SVG renderer, not a second interpretation of the art."""
    def __init__(self):
        try:
            import cairosvg
            self.convert = lambda svg: cairosvg.svg2png(bytestring=svg.encode())
            self.backend = "CairoSVG"
            return
        except ImportError:
            pass
        self.rsvg = C.CDLL(ctypes.util.find_library("rsvg-2"))
        self.cairo = C.CDLL(ctypes.util.find_library("cairo"))
        self.gobject = C.CDLL(ctypes.util.find_library("gobject-2.0"))
        self.glib = C.CDLL(ctypes.util.find_library("glib-2.0"))
        self.rsvg.rsvg_handle_new_from_data.argtypes = [C.c_char_p, C.c_size_t, C.POINTER(C.c_void_p)]
        self.rsvg.rsvg_handle_new_from_data.restype = C.c_void_p
        self.rsvg.rsvg_handle_render_document.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
        self.rsvg.rsvg_handle_render_document.restype = C.c_int
        self.cairo.cairo_image_surface_create.argtypes = [C.c_int, C.c_int, C.c_int]
        self.cairo.cairo_image_surface_create.restype = C.c_void_p
        self.cairo.cairo_create.argtypes = [C.c_void_p]
        self.cairo.cairo_create.restype = C.c_void_p
        self.cairo.cairo_destroy.argtypes = [C.c_void_p]
        self.cairo.cairo_surface_destroy.argtypes = [C.c_void_p]
        self.cairo.cairo_surface_write_to_png_stream.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
        self.cairo.cairo_surface_write_to_png_stream.restype = C.c_int
        self.gobject.g_object_unref.argtypes = [C.c_void_p]
        self.glib.g_error_free.argtypes = [C.c_void_p]
        self.backend = "librsvg / Cairo"

    def render(self, text: str) -> Image.Image:
        if hasattr(self, "convert"):
            return Image.open(io.BytesIO(self.convert(text))).convert("RGBA")
        root = ET.fromstring(text)
        w, h = int(root.attrib["width"]), int(root.attrib["height"])
        raw = text.encode()
        error = C.c_void_p()
        handle = self.rsvg.rsvg_handle_new_from_data(raw, len(raw), C.byref(error))
        if not handle:
            if error:
                self.glib.g_error_free(error)
            raise ValueError("Invalid SVG")
        surface = self.cairo.cairo_image_surface_create(0, w, h)
        context = self.cairo.cairo_create(surface)
        viewport = (C.c_double * 4)(0, 0, w, h)
        chunks = []
        callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_uint)

        @callback_type
        def write(_closure, data, length):
            chunks.append(C.string_at(data, length))
            return 0

        try:
            if not self.rsvg.rsvg_handle_render_document(handle, context, viewport, C.byref(error)):
                if error:
                    self.glib.g_error_free(error)
                raise ValueError("SVG rendering failed")
            status = self.cairo.cairo_surface_write_to_png_stream(surface, write, None)
            if status:
                raise ValueError(f"PNG encoding failed: {status}")
            return Image.open(io.BytesIO(b"".join(chunks))).convert("RGBA")
        finally:
            self.cairo.cairo_destroy(context)
            self.cairo.cairo_surface_destroy(surface)
            self.gobject.g_object_unref(handle)


@dataclass
class Asset:
    name: str
    title: str
    category: str
    svg: Svg
    anchor: tuple[int, int]
    meta: dict = field(default_factory=dict)


ASSETS: dict[str, Asset] = {}
IMAGES: dict[str, Image.Image] = {}


def add(name, title, category, svg, anchor=None, **meta):
    assert name not in ASSETS, name
    ASSETS[name] = Asset(name, title, category, svg, anchor or (svg.width // 2, svg.height), meta)


def texture(svg, base, seed, accent=None, count=12):
    rng = random.Random(seed)
    for _ in range(count):
        x, y = rng.randrange(svg.width), rng.randrange(min(16, svg.height))
        svg.rect(x, y, rng.choice([1, 1, 2]), 1, accent or base)


def terrain():
    for material, base, accent in [
        ("grass", "grass", "grass_light"), ("dirt", "dirt", "dirt_light"),
        ("rock", "stone_top", "stone_mid"), ("sand", "sand", "sand_light"),
        ("snow", "snow", "snow_light"),
    ]:
        for variant in range(3):
            s = Svg(16, 16)
            s.rect(0, 0, 16, 16, base)
            texture(s, base, 100 + variant * 17 + len(material), accent, 5 if material != "rock" else 7)
            if material == "rock":
                s.rect(3 + variant, 9, 3, 1, "stone")
                s.rect(4 + variant, 8, 2, 1, "stone_light")
            add(f"ground-{material}-{variant}", f"{material} 地表 {variant+1}", "ground", s, (0, 0), tile=[16, 16])
    for kind, base, light in [("deep", "sea_deep", "41668a"), ("mid", "sea_mid", "5282a0"), ("shallow", "sea_shallow", "7aaab4")]:
        still = Svg(16, 16)
        still.rect(0, 0, 16, 16, base)
        add(f"water-{kind}-still", "静水底色 · " + kind, "water-base", still, (0, 0), tile=[16, 16])
        s = Svg(96, 16)
        for frame in range(6):
            t = Svg(16, 16)
            t.rect(0, 0, 16, 16, base)
            for j, (px, py, length) in enumerate([(1, 3, 4), (8, 10, 3), (12, 5, 2)]):
                x = (px + frame // 2) % 16
                for dx in range(length):
                    t.rect((x + dx) % 16, py, 1, 1, light)
            s.group(f"water-{frame}", t.body(), frame * 16, 0)
        add(f"water-{kind}", {"deep": "深水", "mid": "中水", "shallow": "浅水"}[kind], "water", s,
            (0, 0), cell=[16, 16], columns=6, rows=1, fps=4, loop=True)

    # NESW bit mask: a set bit means a lower neighbor. Equal-height edges have
    # no rim or wall. Front walls occupy 8px below the logical 16px cap.
    for snowy in [False, True]:
        for mask in range(16):
            s = Svg(16, 24)
            s.rect(0, 0, 16, 16, "snow" if snowy else "stone_top")
            texture(s, "stone_top", 318, "snow_light" if snowy else "stone_mid", 6)
            if not snowy:
                s.rect(3, 7, 4, 1, "stone_light")
                s.rect(8, 11, 3, 1, "stone")
                s.rect(12, 3, 2, 1, "stone_light")
            if mask & 1:
                s.rect(0, 0, 16, 1, "snow_light" if snowy else "stone_light")
            if mask & 2:
                s.rect(15, 0, 1, 16, "stone")
                s.rect(14, 3, 1, 4, "stone_mid")
                s.rect(14, 11, 1, 3, "stone_mid")
            if mask & 8:
                s.rect(0, 0, 1, 16, "snow_light" if snowy else "stone_light")
            if mask & 4:
                s.rect(0, 16, 16, 7, "stone_dark")
                s.rect(0, 16, 16, 1, "stone_light" if not snowy else "snow_dark")
                s.rect(0, 17, 16, 4, "stone")
                for x, y, w, h, c in [(0, 17, 3, 3, "stone_mid"), (4, 18, 2, 4, "stone_dark"),
                                       (7, 17, 4, 2, "stone_mid"), (11, 20, 4, 1, "stone_dark"),
                                       (13, 17, 2, 3, "stone_mid"), (1, 22, 4, 1, "ink_soft")]:
                    s.rect(x, y, w, h, c)
                s.rect(0, 23, 16, 1, "ink")
                s.rect(0, 15, 16, 1, "snow_light" if snowy else "stone_light")
                if snowy:
                    s.rect(2, 16, 3, 2, "snow")
                    s.rect(10, 16, 2, 1, "snow")
            add(f"mountain-{'snow' if snowy else 'rock'}-{mask:02d}",
                f"{'覆雪' if snowy else '裸岩'}连接块 {mask:04b}", "mountain", s, (0, 0),
                tile=[16, 16], overflow=[0, 8], lower_neighbor_mask=mask,
                mask_bits={"north": 1, "east": 2, "south": 4, "west": 8})
    # Independent strips permit taller walls without scaling their texture.
    for variant in range(3):
        s = Svg(16, 8)
        s.rect(0, 0, 16, 8, "stone")
        s.rect(0, 0, 16, 1, "stone_light")
        for x, y, w, h, c in [(0, 1, 3, 4, "stone_mid"), (4+variant, 2, 1, 5, "stone_dark"),
                               (7, 1, 4, 2, "stone_mid"), (10, 5, 4, 1, "stone_dark"),
                               (13, 1, 2, 4, "stone_mid"), (0, 7, 16, 1, "ink")]:
            s.rect(x, y, w, h, c)
        add(f"cliff-face-{variant}", f"岩壁立面 {variant+1}", "cliff", s, (0, 0), tile=[16, 8])
    for mask in range(16):
        s = Svg(16, 16)
        s.rect(0, 0, 16, 16, "sea_shallow")
        s.rect(4, 8, 4, 1, "sea_light")
        for y in range(16):
            for x in range(16):
                dist = min([99] + ([y] if mask & 1 else []) + ([15-x] if mask & 2 else [])
                           + ([15-y] if mask & 4 else []) + ([x] if mask & 8 else []))
                if dist < 3:
                    s.rect(x, y, 1, 1, "sand" if dist < 2 else "sand_dark")
        add(f"coast-{mask:02d}", f"岸线连接块 {mask:04b}", "coast", s, (0, 0),
            tile=[16, 16], land_neighbor_mask=mask)


def oak_parts(variant=0):
    trunk, back, left, right = [Svg(32, 40) for _ in range(4)]
    trunk.rect(13, 21, 5, 17, "wood_dark")
    trunk.rect(14, 22, 2, 14, "wood")
    trunk.rect(14, 35, 1, 3, "wood_mid")
    trunk.rect(11, 36, 3, 2, "wood_dark")
    trunk.rect(18, 36, 3, 2, "wood_dark")
    trunk.poly([(14, 27), (10, 27), (10, 23), (8, 23), (8, 21), (12, 21), (12, 24), (14, 24)], "wood_dark")
    back.poly([(8, 2), (19, 2), (19, 4), (25, 4), (25, 8), (29, 8), (29, 18), (27, 18), (27, 24),
               (21, 24), (21, 27), (8, 27), (8, 24), (3, 24), (3, 20), (1, 20), (1, 10), (4, 10), (4, 5), (8, 5)], "leaf_dark")
    back.poly([(9, 3), (18, 3), (18, 5), (23, 5), (23, 9), (26, 9), (26, 16), (22, 16), (22, 21),
               (8, 21), (8, 23), (4, 23), (4, 19), (2, 19), (2, 11), (5, 11), (5, 6), (9, 6)], "leaf")
    back.rect(10, 4, 7, 2, "leaf_mid")
    back.rect(8, 6, 8, 3, "leaf_mid")
    back.rect(10, 5, 5, 1, "leaf_light")
    left.poly([(4, 10), (12, 10), (12, 12), (16, 12), (16, 19), (13, 19), (13, 23), (7, 23), (7, 21), (3, 21), (3, 14), (4, 14)], "leaf_mid")
    left.rect(5, 11, 6, 3, "leaf_light")
    left.rect(4, 14, 3, 2, "leaf_light")
    left.rect(6, 17, 7, 2, "leaf")
    left.rect(9, 20, 4, 2, "leaf_dark")
    right.poly([(18, 9), (25, 9), (25, 12), (28, 12), (28, 19), (25, 19), (25, 23), (20, 23), (20, 25),
                (13, 25), (13, 21), (16, 21), (16, 15), (18, 15)], "leaf")
    right.rect(19, 10, 5, 3, "leaf_mid")
    right.rect(18, 13, 7, 2, "leaf_mid")
    right.rect(18, 12, 3, 1, "leaf_light")
    right.rect(23, 19, 3, 2, "leaf_dark")
    if variant:
        left.rect(5, 11, 6, 3, "leaf_mid")
        right.rect(18, 13, 7, 2, "leaf_light")
    return trunk, back, left, right


def tree_frame(parts, frame=0):
    s = Svg(32, 40)
    trunk, back, left, right = parts
    sway = [0, 0, 1, 1, 0, 0, -1, -1][frame % 8]
    # The rooted trunk never receives a transform. Only leaf groups move.
    s.group("trunk", trunk.body())
    s.group("canopy-back", back.body(), sway, 0)
    s.group("canopy-left", left.body(), sway, int(frame == 3))
    s.group("canopy-right", right.body(), -sway if frame % 4 == 0 else sway, 0)
    return s


def nature():
    for variant in range(2):
        parts = oak_parts(variant)
        add(f"tree-oak-{variant}", f"阔叶树 {variant+1}", "tree", tree_frame(parts), (16, 38),
            layers=["trunk", "canopy-back", "canopy-left", "canopy-right"])
        if variant == 0:
            for label, part in zip(["trunk", "canopy-back", "canopy-left", "canopy-right"], parts):
                add("oak-part-"+label, "阔叶树组件 · "+label, "component", part, (16, 38))
            sheet = Svg(256, 40)
            for frame in range(8):
                sheet.group(f"sway-{frame}", tree_frame(parts, frame).body(), frame*32, 0)
            add("tree-oak-sway", "叶簇轻摆 / 树干固定", "animation", sheet, (16, 38),
                cell=[32, 40], columns=8, rows=1, fps=6, loop=True, fixed_trunk_box=[10, 29, 12, 10])
    s = Svg(32, 48)
    s.rect(14, 29, 5, 17, "wood_dark")
    s.rect(15, 31, 2, 14, "wood_mid")
    for y, width in [(3, 6), (10, 12), (18, 20), (27, 28)]:
        x = 16-width//2
        s.poly([(x+width//2-1, y-2), (x+width//2+2, y-2), (x+width//2+2, y), (x+width-2, y+8),
                (x+width, y+8), (x+width, y+11), (x, y+11), (x, y+8), (x+2, y+8), (x+width//2-1, y)], "pine_dark")
        s.poly([(x+width//2, y), (x+width//2+1, y), (x+width-3, y+8), (x+2, y+8)], "pine")
        s.poly([(x+width//2, y), (x+width//2+1, y+2), (x+width//2-2, y+7), (x+3, y+7)], "pine_mid")
        s.rect(x+max(2,width//3), y+5, max(2,width//4), 1, "pine_light")
    add("tree-pine", "松树", "tree", s, (16, 46))
    s = Svg(16, 24)
    s.rect(7, 12, 2, 11, "wood_dark")
    s.poly([(5, 1), (10, 1), (10, 3), (13, 3), (13, 7), (15, 7), (15, 13), (12, 13), (12, 16),
            (4, 16), (4, 14), (1, 14), (1, 6), (3, 6), (3, 3), (5, 3)], "leaf_dark")
    s.rect(4, 5, 8, 8, "leaf")
    s.rect(5, 3, 5, 5, "leaf_mid")
    s.rect(5, 4, 3, 2, "leaf_light")
    add("tree-young", "幼树", "tree", s, (8, 23))
    for kind in ["boulder", "ore-iron", "ore-gold"]:
        for variant in range(2):
            s = Svg(16, 16)
            s.poly([(3, 4), (6, 2), (10, 2), (13, 5), (15, 8), (15, 12), (11, 14), (3, 14), (1, 11), (1, 8)], "stone_dark")
            s.poly([(3, 5), (6, 3), (10, 3), (12, 6), (12, 10), (8, 12), (2, 11), (2, 8)], "stone_mid")
            s.poly([(4, 5), (7, 3), (10, 4), (10, 7), (6, 8), (3, 8)], "stone_light")
            s.rect(4, 11, 4, 1, "stone")
            s.rect(11, 6, 2, 3, "stone")
            if kind != "boulder":
                dark, light = ("gold_dark", "gold_light") if kind == "ore-gold" else ("metal", "metal_light")
                s.rect(4+variant, 6, 2, 3, dark)
                s.rect(5+variant, 6, 1, 2, light)
                s.rect(10, 9-variant, 3, 1, light)
                s.rect(9, 10-variant, 2, 2, dark)
            add(f"{kind}-{variant}", {"boulder": "岩石", "ore-iron": "铁矿", "ore-gold": "金矿"}[kind]+str(variant+1), "resource", s, (8, 14))
    for grown in [False, True]:
        s = Svg(16, 16)
        for x, y in [(2, 11), (5, 8), (9, 11), (12, 9)]:
            s.rect(x, y-6, 1, 8, "gold_dark" if grown else "grass_dark")
            for dy in range(0, 5, 2):
                s.rect(x-1, y-6+dy, 1, 1, "gold" if grown else "grass_light")
                s.rect(x+1, y-5+dy, 1, 1, "gold_light" if grown else "grass_high")
        add("wheat-ripe" if grown else "wheat-young", "成熟麦丛" if grown else "青苗", "resource", s, (8, 14))


def foundation(s, x=8, y=42, w=32):
    s.rect(x, y, w, 6, "stone_dark")
    s.rect(x, y, w, 2, "stone_light")
    s.rect(x+1, y+2, w-2, 3, "stone_mid")
    for k in range(x+5, x+w-1, 8):
        s.rect(k, y+2, 1, 3, "stone")
    s.rect(x+5, y+5, w-10, 2, "stone_mid")


def roof(s, x, y, w, h=16, clay=False):
    s.begin(f"roof-{x}-{y}")
    dark, main, mid, light = (("clay_dark", "clay", "clay_light", "gold") if clay else ("roof_dark", "roof", "roof_mid", "roof_light"))
    s.poly([(x+4,y), (x+w-4,y), (x+w-4,y+2), (x+w-2,y+2), (x+w-2,y+5), (x+w,y+5),
            (x+w,y+h), (x,y+h), (x,y+5), (x+2,y+5), (x+2,y+2), (x+4,y+2)], dark)
    s.poly([(x+5,y+2), (x+w-5,y+2), (x+w-5,y+4), (x+w-3,y+4), (x+w-3,y+7),
            (x+w-1,y+7), (x+w-1,y+h-3), (x+1,y+h-3), (x+1,y+7), (x+3,y+7), (x+3,y+4), (x+5,y+4)], main)
    for dy in range(4,h-2,3):
        left=x+max(2,6-dy//3)
        s.rect(left,y+dy,w-(left-x)*2,1,mid)
        for dx in range(left+3,x+w-3,6):
            s.rect(dx+(dy//3%2), y+dy+1, 1, 1, dark)
    s.rect(x+5,y, w-10,1, light)
    s.rect(x+3,y+h-2,w-6,1,mid)
    s.rect(x-1,y+h-1,w+2,2,dark)
    s.rect(x-1,y+h-2,3,1,light)
    s.rect(x+w-2,y+h-2,3,1,mid)
    s.end()


def window(s, x, y, w=5, h=6):
    s.rect(x,y,w,h,"wood_dark")
    s.rect(x+1,y+1,w-2,h-2,"gold_dark")
    s.rect(x+1,y+1,w-2,2,"gold_light")
    s.rect(x+w//2,y+1,1,h-2,"wood")
    s.rect(x-1,y+h,w+2,1,"wood_mid")


def door(s,x,y,w=6,h=12):
    s.rect(x-1,y-1,w+2,h+1,"wood_mid")
    s.rect(x,y,w,h,"wood_dark")
    s.rect(x+1,y+1,w-2,h-1,"wood")
    s.rect(x+2,y+1,1,h-1,"wood_mid")
    s.rect(x+w-2,y+h//2,1,1,"gold")


def house(variant=0):
    s=Svg(48,56)
    foundation(s)
    s.rect(9,22,30,20,"wood_dark")
    s.rect(10,24,28,17,"wall")
    s.rect(10,24,18,3,"wall_light")
    s.rect(33,24,5,17,"wall_dark")
    for x in [10,22,36]:
        s.rect(x,24,2,18,"wood")
    s.rect(10,39,28,2,"wood_mid")
    door(s,24,30,7,12)
    window(s,14,28)
    if variant:
        s.rect(31,6,4,9,"stone_dark")
        s.rect(32,6,2,7,"stone_mid")
        s.rect(30,5,6,2,"stone_light")
    roof(s,5,7,38,19,clay=bool(variant))
    s.rect(13,43,6,2,"stone_light")
    if variant:
        s.rect(4,39,4,6,"wood_dark")
        s.rect(5,40,2,4,"clay")
    return s


def buildings():
    for variant in range(2):
        add(f"house-{variant}", "灰瓦民居" if not variant else "陶瓦民居", "building", house(variant), (24,48), footprint=[8,16,32,32])
    s=Svg(48,56)
    s.rect(7,16,34,32,"wood_dark")
    s.rect(8,17,32,30,"dirt_dark")
    for x in range(10,40,5):
        s.rect(x,18,1,28,"wood")
        for y in range(20,46,6):
            s.rect(x+1,y-3,1,5,"gold_dark")
            s.rect(x,y-2,1,2,"gold")
            s.rect(x+2,y-3,1,2,"gold_light")
    s.rect(32,34,9,11,"wood_dark")
    s.rect(33,35,7,9,"wood_mid")
    roof(s,29,29,14,8,True)
    s.rect(34,39,4,6,"wood_dark")
    add("farm", "麦田与草棚", "building",s,(24,48),footprint=[8,16,32,32])
    s=Svg(48,56)
    foundation(s,8,43)
    s.poly([(7,38),(7,25),(13,25),(13,17),(30,17),(30,21),(37,21),(37,30),(41,30),(41,43),(7,43)],"stone_dark")
    s.poly([(9,30),(9,25),(15,25),(15,19),(28,19),(28,23),(35,23),(35,32),(39,32),(39,42),(10,42)],"stone_mid")
    s.rect(17,27,15,16,"ink")
    s.rect(15,24,19,4,"wood_dark")
    s.rect(16,24,17,2,"wood_mid")
    for x in [15,32]:
        s.rect(x,26,3,17,"wood_dark")
        s.rect(x,27,1,15,"wood_mid")
    for x in [22,28]:
        s.rect(x,35,1,15,"stone_dark")
    for y in [38,42,46]:
        s.rect(21,y,9,1,"wood")
    s.rect(6,36,7,7,"wood_dark")
    s.rect(7,37,5,4,"wood_mid")
    s.rect(7,42,2,2,"ink")
    s.rect(11,42,2,2,"ink")
    s.rect(9,35,3,2,"stone_light")
    add("mine","矿场", "building",s,(24,48),footprint=[8,16,32,32])
    s=Svg(48,56)
    foundation(s)
    s.rect(10,24,28,18,"wood_dark")
    for x in range(11,37,4):
        s.rect(x,25,2,15,"wood_mid")
    s.rect(10,37,28,2,"wood")
    door(s,23,29,7,13)
    roof(s,6,9,36,17)
    for x in range(9,21,4):
        s.rect(x,39,3,6,"wood_dark")
        s.rect(x,39,2,1,"wood_light")
    add("granary","粮仓", "building",s,(24,48),footprint=[8,16,32,32])
    s=Svg(48,56)
    s.rect(8,32,32,15,"dirt_dark")
    for x in range(8,41,4):
        s.rect(x,36,2,12,"wood_dark")
        s.rect(x,36,1,11,"wood_mid")
    for y in [39,44]:
        s.rect(8,y,32,1,"wood")
    s.rect(11,25,25,17,"wood_dark")
    s.rect(13,27,21,12,"wall")
    door(s,21,30,6,12)
    roof(s,8,12,32,16)
    s.rect(39,8,1,31,"wood_dark")
    s.poly([(40,9),(47,9),(47,14),(43,14),(43,16),(40,16)],"red")
    s.rect(40,9,6,1,"red_light")
    add("barracks","兵营", "building",s,(24,48),footprint=[8,16,32,32])
    s=house()
    s.rect(29,1,5,18,"stone_dark")
    s.rect(30,3,3,14,"stone")
    for y in range(4,17,4):s.rect(30,y,2,1,"stone_light")
    s.rect(28,1,7,2,"stone_light")
    s.rect(11,33,9,10,"wood_dark")
    s.rect(12,35,7,6,"red_dark")
    s.rect(14,37,3,4,"clay_light")
    s.rect(14,38,2,3,"gold_light")
    add("workshop","工坊", "building",s,(24,48),footprint=[8,16,32,32],effect_anchor=[32,2])
    s=Svg(48,56)
    s.rect(8,30,32,17,"dirt_dark")
    s.rect(11,25,27,17,"wood_dark")
    s.rect(13,26,23,15,"wall")
    s.rect(34,26,2,15,"wall_dark")
    roof(s,7,13,35,15)
    door(s,24,30,7,12)
    for y in [39,43]:
        s.rect(7,y,14,3,"wood_dark")
        s.rect(8,y,12,1,"wood_mid")
        s.rect(8,y+1,2,2,"wood_light")
    s.rect(38,31,3,12,"wood")
    s.rect(35,30,8,3,"metal")
    s.rect(36,30,6,1,"metal_light")
    add("lumberyard","林场", "building",s,(24,48),footprint=[8,16,32,32])
    s=Svg(48,56)
    foundation(s)
    s.rect(12,32,24,9,"stone")
    s.rect(16,25,4,16,"stone_dark")
    s.rect(29,25,3,16,"stone_dark")
    s.poly([(17,25),(24,10),(28,10),(33,26),(30,26),(26,16),(20,26)],"stone_dark")
    s.rect(15,18,23,3,"roof")
    s.rect(13,17,27,2,"roof_light")
    s.rect(11,16,4,8,"stone_dark")
    s.rect(35,17,5,9,"stone_dark")
    s.rect(36,18,3,6,"stone_mid")
    s.rect(12,24,1,12,"metal")
    s.rect(10,37,5,3,"ink")
    s.rect(27,37,8,4,"wood_dark")
    s.rect(28,38,6,1,"wood_mid")
    add("oilwell","油井", "building",s,(24,48),footprint=[8,16,32,32])
    s=Svg(64,64)
    foundation(s,8,48,48)
    s.rect(9,29,46,19,"stone_dark")
    s.rect(10,30,44,17,"wall")
    s.rect(48,30,6,17,"wall_dark")
    s.rect(47,5,6,26,"stone_dark")
    s.rect(48,6,4,23,"stone")
    for y in range(9,28,5):s.rect(48,y,3,1,"stone_mid")
    s.rect(46,4,8,3,"stone_light")
    roof(s,6,17,36,15)
    roof(s,36,24,22,9)
    for x in [13,23,39]:window(s,x,35,5,6)
    door(s,30,37,6,11)
    s.rect(45,42,8,6,"wood_dark")
    s.rect(46,43,6,1,"wood_mid")
    add("factory","工厂", "building",s,(32,56),footprint=[8,24,48,32],effect_anchor=[50,4])
    s=Svg(80,80)
    foundation(s,8,65,64)
    s.rect(12,44,56,21,"wood_dark")
    s.rect(14,45,52,18,"wall")
    s.rect(58,45,8,18,"wall_dark")
    for x in [15,27,50,62]:
        s.rect(x,44,3,22,"wood_dark")
        s.rect(x,45,1,19,"wood_mid")
    for x in [20,54]:window(s,x,48,7,8)
    door(s,34,47,12,18)
    roof(s,8,28,64,19)
    s.rect(25,18,30,13,"wood_dark")
    s.rect(27,19,26,11,"wall")
    for x in [29,36,43]:window(s,x,21,5,6)
    roof(s,20,6,40,15)
    s.rect(39,2,2,5,"wood_dark")
    s.rect(36,1,8,2,"gold")
    s.rect(29,71,22,3,"stone_light")
    s.rect(27,74,26,2,"stone_mid")
    s.rect(10,49,1,17,"wood_dark")
    s.poly([(11,49),(19,49),(19,55),(15,55),(15,57),(11,57)],"red")
    add("townhall","市政厅", "building",s,(40,72),footprint=[8,24,64,48])
    s=Svg(48,56)
    foundation(s)
    for x in [10,23,36]:
        s.rect(x,21,2,22,"wood_dark")
        s.rect(x,21,1,20,"wood_mid")
    for y in [25,35,41]:s.rect(8,y,32,2,"wood_mid")
    for x in [12,25]:
        s.poly([(x,36),(x+1,36),(x+9,25),(x+8,25)],"wood")
    s.rect(13,38,21,4,"stone_mid")
    s.rect(13,32,16,5,"wall")
    add("construction","施工脚手架", "building",s,(24,48),footprint=[8,16,32,32])


def person(role, direction, frame, action):
    mounted=role=="cavalry"
    s=Svg(24 if mounted else 16,24 if mounted else 16)
    # Six-pixel body width, ten-pixel standing height shared across all roles.
    cx, foot=(12,16) if mounted else (8,14)
    phase=[0,1,0,0,-1,0][frame%6] if action=="walk" else 0
    front=direction=="south"
    back=direction=="north"
    side=direction in ["east","west"]
    sign=-1 if direction=="west" else 1
    soldier=role in ["sword","spear","axe","cavalry"]
    body="cloth" if not soldier else "roof"
    light="cloth_light" if not soldier else "roof_mid"
    dark="cloth_dark" if not soldier else "roof_dark"
    attack=[0,1,2,0][frame%4] if action=="act" else 0
    if mounted:
        s.begin("horse")
        gallop=phase
        if side:
            s.rect(4,13,15,6,"wood_dark")
            s.rect(5,13,12,4,"wood")
            s.rect(6,13,9,1,"wood_mid")
            s.rect(17 if sign>0 else 3,9,4,7,"wood_dark")
            s.rect(18 if sign>0 else 3,10,3,4,"wood_mid")
            s.rect(20 if sign>0 else 1,14,2,2,"wood_dark")
            s.rect(18 if sign>0 else 4,8,1,3,"wood_dark")
            for x,offset in [(6,gallop),(10,-gallop),(15,-gallop),(18,gallop)]:
                s.rect(x,18,1,4-offset,"wood_dark")
                s.rect(x,21-offset,2,1,"ink")
            s.rect(2 if sign>0 else 21,15,2,5,"wood_dark")
            s.rect(8,13,7,3,"red_dark")
        else:
            s.rect(7,12,10,8,"wood_dark")
            s.rect(8,12,8,7,"wood")
            s.rect(8,12,3,5,"wood_mid")
            for x,offset in [(8,gallop),(15,-gallop)]:
                s.rect(x,18,1,5-offset,"wood_dark")
                s.rect(x,22-offset,2,1,"ink")
            s.rect(9,13,6,3,"red_dark")
            if front:
                s.rect(9,17,6,5,"wood_dark")
                s.rect(10,17,4,4,"wood_mid")
                s.rect(10,21,4,1,"wood_dark")
                s.rect(9,16,1,2,"wood_dark")
                s.rect(14,16,1,2,"wood_dark")
            else:
                s.rect(9,8,6,6,"wood_dark")
                s.rect(10,9,4,5,"wood_mid")
                s.rect(11,8,2,5,"wood_dark")
                s.rect(11,19,2,4,"wood_dark")
        s.end()
        foot=15
    # Legs and feet; riders use the same torso/head and shortened bent legs.
    s.begin("legs")
    s.rect(cx-2,foot-3,1,3,"ink")
    s.rect(cx+1,foot-3,1,3,"ink")
    if action=="walk" and not mounted:
        s.rect(cx-2,foot,2,1,"ink")
        s.rect(cx+1,foot-phase,2,1,"ink")
        if phase:
            s.rect(cx-2,foot-1,1,1,"wood_mid")
    else:
        s.rect(cx-2,foot,2,1,"ink")
        s.rect(cx+1,foot,2,1,"ink")
    s.end()
    s.begin("body")
    s.rect(cx-3,foot-7,6,5,dark)
    s.rect(cx-2,foot-7,4,4,body)
    s.rect(cx-2,foot-7,2,2,light)
    s.rect(cx-2,foot-3,4,1,"wood_dark")
    if soldier:
        s.rect(cx-2,foot-6,1,2,"metal")
        s.rect(cx+1,foot-6,1,2,"metal")
        s.rect(cx-1,foot-4,2,1,"metal")
        s.rect(cx-2,foot-3,1,1,"gold")
    # The face/hair coordinate system is identical for civilians and soldiers.
    s.end()
    s.begin("head")
    s.rect(cx-2,foot-10,4,3,"wood_dark")
    s.rect(cx-1,foot-9,3,3,"skin")
    s.rect(cx-1,foot-9,2,1,"skin_light")
    if back:
        s.rect(cx-2,foot-10,4,4,"wood_dark")
        s.rect(cx-1,foot-10,2,1,"wood")
    elif not (action=="idle" and frame%2):
        s.rect(cx+(1 if sign>0 else -1),foot-8,1,1,"ink")
    if soldier:
        s.rect(cx-2,foot-11,4,2,"roof_dark")
        s.rect(cx-1,foot-11,2,1,"metal")
        s.rect(cx-3,foot-9,6,1,"roof_dark")
        if back:s.rect(cx-2,foot-8,4,1,"roof")
    elif role=="worker":
        s.rect(cx-3,foot-11,5,1,"wood_mid")
        s.rect(cx-4,foot-10,7,1,"wood_light")
    else:
        s.rect(cx-1,foot-11,2,1,"wood_dark")
    s.end()
    s.begin("arms")
    arm=phase if action=="walk" else attack
    s.rect(cx-3,foot-7+max(0,arm),1,3,"skin_dark")
    s.rect(cx+2,foot-6-min(1,arm),1,3,"skin")
    s.end()
    s.begin("equipment")
    if not soldier:
        if role=="worker":
            tx=cx+3*sign
            s.rect(tx,foot-7-attack,1,6,"wood_dark")
            s.rect(tx-1,foot-8-attack,4,2,"metal")
            s.rect(tx-1,foot-8-attack,3,1,"metal_light")
        elif action=="walk":
            s.rect(cx-4,foot-5,3,4,"wood_dark")
            s.rect(cx-4,foot-5,3,1,"wood_light")
            s.rect(cx-3,foot-3,1,1,"wood_mid")
    else:
        if role in ["spear","cavalry"]:
            if action=="act" and side:
                start=cx+2*sign
                for k in range(2,8+attack):
                    x=cx+k*sign
                    if 0<=x<s.width:s.rect(x,foot-7,1,1,"wood_mid")
                x=min(s.width-2,max(0,cx+(7+attack)*sign))
                s.rect(x,foot-8,2,2,"metal_light")
            else:
                tx=cx+4*sign
                s.rect(tx,foot-12,1,12,"wood_mid")
                s.rect(tx,foot-14,1,3,"metal_light")
                s.rect(tx-1,foot-12,3,1,"metal")
        elif role=="sword":
            tx=cx+4*sign
            s.rect(tx,foot-8-attack,1,6,"metal")
            s.rect(tx,foot-8-attack,1,3,"metal_light")
            s.rect(tx-1,foot-3-attack,3,1,"gold")
        else:
            tx=cx+4*sign
            s.rect(tx,foot-9-attack,1,9,"wood_mid")
            s.rect(tx-1,foot-10-attack,3,3,"metal")
            s.rect(tx-1,foot-10-attack,2,1,"metal_light")
        if role!="cavalry":
            sx=cx-4 if sign>0 else cx+2
            s.rect(sx,foot-6,3,5,"wood_dark")
            s.rect(sx,foot-6,2,4,"red_dark")
            s.rect(sx+1,foot-5,1,2,"gold")
    s.end()
    return s


def characters():
    names={"resident":"居民", "worker":"工人", "sword":"剑兵", "spear":"矛兵", "axe":"斧兵", "cavalry":"骑兵"}
    for role,title in names.items():
        cell=24 if role=="cavalry" else 16
        s=Svg(cell*6,cell*12)
        for action_index,action in enumerate(["idle","walk","act"]):
            for direction_index,direction in enumerate(["south","east","north","west"]):
                row=action_index*4+direction_index
                for frame in range(6):
                    s.group(f"{action}-{direction}-{frame}",person(role,direction,frame,action).body(),frame*cell,row*cell)
        add("actor-"+role,title,"actor",s,(cell//2,22 if role=="cavalry" else 14),
            cell=[cell,cell],columns=6,rows=12,directions=["south","east","north","west"],
            actions={"idle":{"rows":[0,1,2,3],"frames":2,"fps":2},
                     "walk":{"rows":[4,5,6,7],"frames":6,"fps":8},
                     "act":{"rows":[8,9,10,11],"frames":4,"fps":7}},
            common_body_height=10,national_color="red_dark / red / red_light")


def export(renderer):
    for folder in [ART/"svg",ART/"png",PREVIEW]:folder.mkdir(parents=True,exist_ok=True)
    for name,a in ASSETS.items():
        text=a.svg.document()
        ET.fromstring(text)
        (ART/"svg"/(name+".svg")).write_text(text,encoding="utf-8")
        im=renderer.render(text)
        IMAGES[name]=im
        im.save(ART/"png"/(name+".png"),optimize=True)


class Scene:
    def __init__(self,width,height):
        self.image=Image.new("RGBA",(width,height))
        self.svg=Svg(width,height)
        self.used=set()
        self.defs=[]

    def stamp(self,name,x,y,crop=None):
        a=ASSETS[name]
        im=IMAGES[name]
        if crop:
            cx,cy,w,h=crop
            im=im.crop((cx,cy,cx+w,cy+h))
        self.image.alpha_composite(im,(int(x),int(y)))
        if name not in self.used:
            self.used.add(name)
            self.defs.append(f'<g id="art-{name}">{prefix_ids(a.svg.body(), name)}</g>')
        if crop:
            cx,cy,w,h=crop
            self.svg.parts.append(f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="{cx} {cy} {w} {h}" overflow="hidden"><use href="#art-{name}"/></svg>')
        else:self.svg.parts.append(f'<use href="#art-{name}" x="{x}" y="{y}"/>')

    def save(self,path):
        path.with_suffix(".png").parent.mkdir(parents=True,exist_ok=True)
        self.image.save(path.with_suffix(".png"))
        self.svg.parts.insert(0,"<defs>"+"\n".join(self.defs)+"</defs>")
        path.with_suffix(".svg").write_text(self.svg.document(),encoding="utf-8")


def sample_scene():
    cols,rows=40,26
    heights=[[0]*cols for _ in range(rows)]
    # Discrete hand-composed study. Every height change is a whole 16px tile.
    spans=[(13,31),(10,34),(7,35),(6,37),(7,36),(5,35),(7,33),(8,32),(10,29),(12,27),(14,24)]
    for y,(left,right) in enumerate(spans,1):
        for x in range(left,right):
            heights[y][x]=1
            if left+2<=x<right-2 and y<9:heights[y][x]=2
    peaks={2:(25,28),3:(24,30),4:(23,29),5:(24,28),6:(25,27)}
    for y,(left,right) in peaks.items():
        for x in range(left,right):heights[y][x]=3
    for x,y in [(12,3),(13,3),(11,4),(12,4),(13,4),(14,4),(12,5),(13,5),(14,5),(13,6)]:
        heights[y][x]=3
    for x,y in [(19,1),(19,2),(19,3),(19,4),(18,4),(18,5),(18,6),(18,7),(17,7),(17,8),(16,8),(16,9),(15,10)]:
        heights[y][x]=0
    land=[]
    for y in range(rows):
        land.append([x>=max(0,7-(y//3)) and not (y>20 and x<10+(y-20)) for x in range(cols)])
    scene=Scene(cols*16,rows*16)
    for y in range(rows):
        for x in range(cols):
            if land[y][x]:
                beach=any(not (0<=xx<cols and 0<=yy<rows and land[yy][xx]) for xx,yy in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)])
                kind="sand" if beach else "grass"
                scene.stamp(f"ground-{kind}-{(x*7+y*3)%3}",x*16,y*16)
            else:
                mask=sum(bit for dx,dy,bit in [(0,-1,1),(1,0,2),(0,1,4),(-1,0,8)]
                         if 0<=x+dx<cols and 0<=y+dy<rows and land[y+dy][x+dx])
                if mask:scene.stamp(f"coast-{mask:02d}",x*16,y*16)
                else:
                    near=any(0<=x+dx<cols and 0<=y+dy<rows and land[y+dy][x+dx]
                             for dx in range(-2,3) for dy in range(-2,3))
                    kind="mid" if near else "deep"
                    if (x*11+y*7)%9==0:
                        scene.stamp("water-"+kind,x*16,y*16,(((x+y)%3)*16,0,16,16))
                    else:scene.stamp("water-"+kind+"-still",x*16,y*16)
    # Connected dirt lanes; houses leave a two-tile-wide ground footprint.
    roads=[(x,18) for x in range(17,38)]+[(27,y) for y in range(12,25)]+[(x,23) for x in range(18,36)]
    for x,y in roads:scene.stamp(f"ground-dirt-{(x+y)%3}",x*16,y*16)
    for y in range(rows):
        for x in range(cols):
            level=heights[y][x]
            if not level:continue
            mask=0
            for dx,dy,bit in [(0,-1,1),(1,0,2),(0,1,4),(-1,0,8)]:
                neighbor=heights[y+dy][x+dx] if 0<=x+dx<cols and 0<=y+dy<rows else 0
                if neighbor<level:mask|=bit
            below=heights[y+1][x] if y+1<rows else 0
            drop=level-below
            if drop>1:
                for depth in range(1,drop):
                    scene.stamp(f"cliff-face-{x%3}",x*16,y*16-level*8+16+depth*8)
            snowy=level==3 and (x+y)%5!=0
            scene.stamp(f"mountain-{'snow' if snowy else 'rock'}-{mask:02d}",x*16,y*16-level*8)
    objects=[]
    def obj(name,x,y):objects.append((y,name,x,y))
    for name,x,y in [("mine",245,184),("townhall",438,250),("house-0",337,280),("house-1",381,280),
                     ("granary",552,286),("barracks",595,280),("house-1",348,352),("house-0",397,352),
                     ("workshop",555,369),("farm",484,366),("farm",526,405),("lumberyard",265,312),
                     ("construction",602,405)]:obj(name,x,y)
    rng=random.Random(4129)
    for y in range(11,25,2):
        for x in range(8,16,2):
            if rng.random()<.18:continue
            obj("tree-oak-"+str(rng.randrange(2)),x*16+rng.randrange(-5,6),y*16+rng.randrange(-4,6))
    for x,y in [(8,10),(9,9),(21,10),(23,10),(26,9),(30,9),(33,8),(35,7),(37,12),(38,14),(22,21),(20,24),(37,24)]:
        obj("tree-pine" if y<11 else "tree-oak-0",x*16,y*16)
    for x,y in [(9,7),(12,8),(21,6),(31,7),(34,5),(27,10)]:
        level=heights[y][x]
        obj("ore-gold-0" if x%3==0 else "boulder-1",x*16+8,y*16-level*8+14)
    for ysort,name,x,y in sorted(objects):
        ax,ay=ASSETS[name].anchor
        scene.stamp(name,x-ax,y-ay)
    # Small people, shared body scale, with space around doors and lanes.
    for i,(x,y) in enumerate([(309,288),(329,292),(363,289),(410,291),(430,289),(461,289),(480,291),(530,290),
                               (442,315),(445,341),(429,367),(446,382),(373,367),(321,312),(277,318),(270,187)]):
        role="worker" if i%4==0 else "resident"
        scene.stamp("actor-"+role,x-8,y-14,((i%6)*16,(4+i%4)*16,16,16))
    for row in range(3):
        for col in range(5):
            role=["spear","sword","axe"][col%3]
            scene.stamp("actor-"+role,575+col*10,299+row*12,(0,16,16,16))
    for i in range(3):scene.stamp("actor-cavalry",583+i*18,344,(0,24,24,24))
    scene.save(PREVIEW/"scene")
    scene.image.resize((1280,832),Image.Resampling.NEAREST).save(PREVIEW/"scene-2x.png")
    return scene


FONT_PATH="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD="/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def font(size,bold=False):
    try:return ImageFont.truetype(FONT_BOLD if bold else FONT_PATH,size,index=2)
    except OSError:return ImageFont.load_default()


def board():
    im=Image.new("RGB",(1600,1320),"#f0eddf")
    d=ImageDraw.Draw(im)
    d.rectangle((0,0,1600,133),fill="#223d3d")
    d.text((48,21),"战国  /  山河与人间",font=font(36,True),fill="#f4eedb")
    d.text((50,81),"原创 SVG 素材 · 第一批     16 px 地形格 / 2×2 普通建筑 / 共用人物比例",font=font(20),fill="#b9cdc0")

    def label(x,y,text):d.text((x,y),text,font=font(24,True),fill="#304b43")
    def small(x,y,text):d.text((x,y),text,font=font(17),fill="#647268")
    def sprite(name,x,y,scale=3,crop=None,bg=None):
        a=IMAGES[name]
        if crop:
            cx,cy,w,h=crop;a=a.crop((cx,cy,cx+w,cy+h))
        a=a.resize((a.width*scale,a.height*scale),Image.Resampling.NEAREST)
        if bg:d.rectangle((x,y,x+a.width,y+a.height),fill=bg)
        im.paste(a,(x,y),a)

    label(48,160,"01  格状山体与岩壁")
    small(48,198,"明确台阶 · 邻格连接 · 裸岩 / 覆雪")
    # Connected two-tier mountain block, using the actual exported pieces.
    study=Scene(176,96)
    grid=["00001111000","00112222100","01123332110","11223332110","01122221100","00111111000"]
    hh=[[int(c) for c in row] for row in grid]
    for yy in range(6):
        for xx in range(11):study.stamp("ground-grass-0",xx*16,yy*16)
    for yy,row in enumerate(hh):
        for xx,level in enumerate(row):
            if not level:continue
            mask=sum(bit for dx,dy,bit in [(0,-1,1),(1,0,2),(0,1,4),(-1,0,8)]
                     if not (0<=xx+dx<11 and 0<=yy+dy<6) or hh[yy+dy][xx+dx]<level)
            bottom=hh[yy+1][xx] if yy<5 else 0
            for depth in range(1,max(1,level-bottom)):
                study.stamp("cliff-face-0",xx*16,yy*16-level*8+32+depth*8)
            study.stamp(f"mountain-{'snow' if level==3 else 'rock'}-{mask:02d}",xx*16,yy*16-level*8+16)
    panel=study.image.resize((528,288),Image.Resampling.NEAREST)
    im.paste(panel,(48,232),panel)
    small(48,534,"左上受光 / 岩壁下缘暗部 / 共享边消隐")

    label(624,160,"02  树干固定，叶簇分层")
    small(624,198,"树冠明暗统一 · 组件可独立做动画")
    for name,x,y in [("tree-oak-0",625,265),("tree-oak-1",740,265),("tree-pine",855,241),("tree-young",975,313)]:
        sprite(name,x,y,3)
    for i,name in enumerate(["trunk","canopy-back","canopy-left","canopy-right"]):
        sprite("oak-part-"+name,625+i*95,421,2)
    small(624,534,"树干           后冠           左叶簇         右叶簇")

    label(1092,160,"03  水与资源")
    small(1092,198,"低对比水纹 / 统一石材色阶")
    for i,kind in enumerate(["deep","mid","shallow"]):
        sprite("water-"+kind,1094+i*130,247,7,(0,0,16,16))
        small(1094+i*130,370,["深水","中水","浅水"][i])
    for i,name in enumerate(["boulder-0","ore-iron-0","ore-gold-0","wheat-ripe"]):sprite(name,1093+i*106,425,4)
    small(1092,534,"岩石 / 铁矿 / 金矿 / 麦丛")

    d.line((48,590,1552,590),fill="#d2d5c3",width=2)
    label(48,615,"04  建筑统一画法与尺度")
    small(48,651,"民居、生产设施为 2×2 小格；工厂 3×2；市政厅 4×3。图像高度与地面占地分别记录。")
    names=["house-0","house-1","granary","farm","mine","lumberyard","barracks","workshop","oilwell"]
    for i,name in enumerate(names):
        x=48+i*167
        sprite(name,x,708,3)
        small(x+12,883,ASSETS[name].title)
    d.line((48,938,1552,938),fill="#d2d5c3",width=2)
    label(48,964,"05  同一套人物：居民 → 工人 → 剑 / 矛 / 斧 → 骑兵")
    small(48,1001,"相同身体结构与像素密度；装备区分身份。下方角色放大 5×，右侧为同尺度建筑对照。")
    for i,role in enumerate(["resident","worker","sword","spear","axe","cavalry"]):
        cell=24 if role=="cavalry" else 16
        sprite("actor-"+role,65+i*133,1060,5,(0,cell,cell,cell))
        small(65+i*133,1190,ASSETS["actor-"+role].title)
    sprite("house-0",1050,1017,4)
    sprite("actor-resident",1238,1153,4,(0,16,16,16))
    sprite("actor-spear",1302,1153,4,(0,16,16,16))
    small(1063,1250,"同尺度：民居与人物")
    small(48,1270,"全部为可编辑 SVG + 真透明 PNG；素材样板，尚未接入游戏。")
    im.save(PREVIEW/"overview.png",optimize=True)


def animations():
    frames=[]
    for f in range(24):
        im=Image.new("RGB",(960,384),"#e9e9da")
        d=ImageDraw.Draw(im)
        d.text((28,16),"叶簇轻摆与统一人物动作",font=font(24,True),fill="#304b43")
        d.text((28,56),"固定树干 / 行走 / 劳动 / 攻击    ·    原始组件按整数像素移动",font=font(16),fill="#657365")
        source=IMAGES["tree-oak-sway"].crop(((f%8)*32,0,(f%8+1)*32,40))
        source=source.resize((192,240),Image.Resampling.NEAREST)
        im.paste(source,(24,108),source)
        for i,role in enumerate(["resident","worker","sword","spear","cavalry"]):
            cell=24 if role=="cavalry" else 16
            row=5 if role in ["resident","cavalry"] else 9
            frame=f%6 if row==5 else f%4
            actor=IMAGES["actor-"+role].crop((frame*cell,row*cell,(frame+1)*cell,(row+1)*cell))
            actor=actor.resize((cell*5,cell*5),Image.Resampling.NEAREST)
            y=320-(22 if cell==24 else 14)*5
            im.paste(actor,(254+i*132,y),actor)
            d.text((254+i*132,345),ASSETS["actor-"+role].title,font=font(17),fill="#304b43")
        frames.append(im)
    # Shared palette avoids GIF color flicker. This is a sample clip, not gameplay.
    palette=frames[0].quantize(colors=128)
    palframes=[frame.quantize(palette=palette,dither=Image.Dither.NONE) for frame in frames]
    palframes[0].save(PREVIEW/"motion.gif",save_all=True,append_images=palframes[1:],duration=125,loop=0,disposal=2)


def catalog():
    im=Image.new("RGB",(1600,1840),"#f0eddf")
    d=ImageDraw.Draw(im)
    d.rectangle((0,0,1600,105),fill="#223d3d")
    d.text((40,24),"新素材图录 / 建筑、地形连接与四方向人物",font=font(30,True),fill="#f4eedb")

    def stamp(name,x,y,scale=3,crop=None):
        image=IMAGES[name]
        if crop:
            cx,cy,w,h=crop;image=image.crop((cx,cy,cx+w,cy+h))
        image=image.resize((image.width*scale,image.height*scale),Image.Resampling.NEAREST)
        im.paste(image,(x,y),image)

    buildings=[name for name,a in ASSETS.items() if a.category=="building"]
    for i,name in enumerate(buildings):
        x=40+(i%6)*256;y=130+(i//6)*260
        a=ASSETS[name]
        scale=2 if max(a.svg.width,a.svg.height)>64 else 3
        stamp(name,x+(224-a.svg.width*scale)//2,y+210-a.svg.height*scale,scale)
        d.text((x+10,y+215),a.title+" · "+name,font=font(15),fill="#304b43")
    d.line((40,658,1560,658),fill="#c9cebc",width=2)
    d.text((40,675),"连接掩码：N=1 / E=2 / S=4 / W=8；山体位表示邻格更低，岸线位表示邻格是陆地。",font=font(19),fill="#304b43")
    for row,(prefix,title) in enumerate([("mountain-rock","裸岩"),("mountain-snow","覆雪"),("coast","岸线")]):
        y=724+row*108
        d.text((40,y+15),title,font=font(20,True),fill="#304b43")
        for mask in range(16):
            x=154+mask*86
            stamp(f"{prefix}-{mask:02d}",x,y,3)
            d.text((x+9,y+75),f"{mask:02d}",font=font(14),fill="#657365")
    d.line((40,1050,1560,1050),fill="#c9cebc",width=2)
    d.text((40,1068),"四方向人物 / 每方向依次展示：待机、行走、劳动或攻击",font=font(22,True),fill="#304b43")
    for i,title in enumerate(["朝南","朝东","朝北","朝西"]):
        d.text((290+i*290,1114),title,font=font(18),fill="#657365")
    for row,role in enumerate(["resident","worker","sword","spear","axe","cavalry"]):
        y=1160+row*96
        name="actor-"+role;cell=24 if role=="cavalry" else 16
        d.text((42,y+20),ASSETS[name].title,font=font(21,True),fill="#304b43")
        for direction in range(4):
            for action in range(3):
                frame=1 if action else 0
                cy=(action*4+direction)*cell
                stamp(name,230+direction*290+action*86,y,3,(frame*cell,cy,cell,cell))
    d.text((40,1780),"全部纹理由 SVG 源文件导出；图录以最近邻放大，不加入平滑滤镜。",font=font(18),fill="#657365")
    im.save(PREVIEW/"catalog.png",optimize=True)


def atlas_and_manifest(renderer):
    atlas=Image.new("RGBA",(1024,1024))
    records=[]
    x=y=2;row_height=0
    for name,a in ASSETS.items():
        image=IMAGES[name]
        if x+image.width+2>1024:
            x=2;y+=row_height+4;row_height=0
        if y+image.height+2>1024:raise ValueError("Atlas capacity exceeded")
        atlas.alpha_composite(image,(x,y))
        png=ART/"png"/(name+".png")
        svg=ART/"svg"/(name+".svg")
        records.append(dict(id=name,title=a.title,category=a.category,size=list(image.size),anchor=list(a.anchor),
                            svg=str(svg.relative_to(ART)),png=str(png.relative_to(ART)),
                            atlas_rect=[x,y,image.width,image.height],
                            sha256={"svg":hashlib.sha256(svg.read_bytes()).hexdigest(),"png":hashlib.sha256(png.read_bytes()).hexdigest()},**a.meta))
        x+=image.width+4;row_height=max(row_height,image.height)
    atlas.save(ART/"atlas.png",optimize=True)
    manifest=dict(format="zhanguo.art-study/1",status="connected-desktop",license="MPL-2.0",
                  authoring="Original SVG components authored for Zhanguo; no extracted or copied game art",
                  runtime={"atlas":"atlas.png","renderer":"Godot RegionArtwork / WorldMap","world_pixel_scale":1,
                           "pixel_font":"fusion-pixel-12px-monospaced-zh_hans.ttf","water_fps":4,"oak_fps":6,
                           "actor_actions":"idle / walk / act","palette":"civilian cloth slots; soldier shield / saddle slots"},
                  renderer=renderer.backend,tile_size=16,height_step=8,filter="nearest",light="upper-left",
                  palette=P,atlas={"file":"atlas.png","size":[1024,1024],"padding":2},assets=records)
    (ART/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return manifest


def validate(manifest,renderer):
    checks=[]
    allowed={tuple(bytes.fromhex(c)) for c in P.values()}|{tuple(bytes.fromhex(c)) for c in ["41668a","5282a0","7aaab4"]}
    for name,a in ASSETS.items():
        im=IMAGES[name]
        assert im.size==(a.svg.width,a.svg.height),name
        assert im.getbbox(),name
        alpha=set(im.getchannel("A").getdata())
        assert alpha<={0,255},(name,"fractional alpha",alpha)
        colors={rgb[:3] for rgb in im.getdata() if rgb[3]}
        assert colors<=allowed,(name,"off-palette",colors-allowed)
        root=ET.fromstring(a.svg.document())
        ids=[n.attrib["id"] for n in root.iter() if "id" in n.attrib]
        assert len(ids)==len(set(ids)),(name,"duplicate SVG IDs")
        assert not any(n.tag.rsplit("}",1)[-1] in {"image","filter","script","foreignObject"} for n in root.iter()),name
    checks.append("All SVGs parse with unique component IDs; raster sizes match; pixels use the shared palette and binary alpha")
    for material in ["rock","snow"]:
        reference=IMAGES[f"mountain-{material}-00"]
        for mask in range(16):
            im=IMAGES[f"mountain-{material}-{mask:02d}"]
            for y in range(2,14):
                for x in range(2,14):assert im.getpixel((x,y))==reference.getpixel((x,y)),(material,mask,x,y)
            assert im.crop((0,16,16,24)).getbbox() is not None if mask&4 else im.crop((0,16,16,24)).getbbox() is None
    checks.append("32 mountain connection variants share their cap interior; front walls appear only on exposed south edges")
    sheet=IMAGES["tree-oak-sway"]
    trunks=[]
    for frame in range(8):trunks.append(sheet.crop((frame*32+10,29,frame*32+22,39)).tobytes())
    assert len(set(trunks))==1
    assert len({sheet.crop((frame*32,0,(frame+1)*32,28)).tobytes() for frame in range(8)})>=3
    checks.append("All eight tree frames keep the lower trunk fixed; at least three canopy poses differ")
    for name in ["resident","worker","sword","spear","axe","cavalry"]:
        im=IMAGES["actor-"+name];cell=24 if name=="cavalry" else 16
        for row in range(12):
            for frame in range(6):
                crop=im.crop((frame*cell,row*cell,(frame+1)*cell,(row+1)*cell))
                assert crop.getbbox(),(name,row,frame)
        assert len({im.crop((i*cell,5*cell,(i+1)*cell,6*cell)).tobytes() for i in range(6)})>=2,name
    checks.append("432 actor cells are nonempty; each role has distinct walking poses")
    for a in ASSETS.values():
        if a.category=="building" and a.name not in ["townhall","factory"]:
            assert a.meta["footprint"][2:]==[32,32]
    checks.append("Every ordinary building has a 32×32 ground footprint (2×2 tiles)")
    atlas=Image.open(ART/"atlas.png").convert("RGBA")
    for rec in manifest["assets"]:
        x,y,w,h=rec["atlas_rect"]
        assert atlas.crop((x,y,x+w,y+h)).tobytes()==IMAGES[rec["id"]].tobytes(),rec["id"]
    checks.append("Every atlas rectangle exactly reproduces its individual PNG")
    # Compare the independently rendered assembled SVG to the PNG composition.
    svg=renderer.render((PREVIEW/"scene.svg").read_text())
    png=Image.open(PREVIEW/"scene.png").convert("RGBA")
    assert svg.tobytes()==png.tobytes(),"Scene SVG and PNG differ"
    checks.append("Assembled scene SVG renders pixel-for-pixel identically to PNG texture composition")
    report=dict(status="passed",assets=len(ASSETS),actor_cells=432,checks=checks,
                counts={cat:sum(a.category==cat for a in ASSETS.values()) for cat in sorted({a.category for a in ASSETS.values()})})
    (ART/"validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    return report


def review_html():
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>战国 · 山河与人间 / 新素材第一批</title><style>
*{box-sizing:border-box}body{margin:0;background:#e9e9da;color:#304b43;font:16px/1.7 system-ui,sans-serif}
header{background:#223d3d;color:#f4eedb;padding:32px max(24px,calc((100vw - 1280px)/2))}h1{font-size:30px;margin:0}header p{color:#b9cdc0;margin:8px 0 0}
main{max-width:1328px;margin:auto;padding:24px}section{margin:0 0 30px}h2{font-size:22px}img{max-width:100%;height:auto;image-rendering:pixelated;display:block}
.scroll{overflow:auto;background:#739357;border:1px solid #bbc3ad}.scroll img{max-width:none}button{font:inherit;color:inherit;border:1px solid #a4b09b;background:#f7f3e7;padding:6px 14px;cursor:pointer;margin:0 8px 12px 0}a{color:#356b71}.note{color:#657365;font-size:14px}
</style><header><h1>战国 / 山河与人间</h1><p>原创 SVG 素材 · 第一批　｜　格状山体、明确岩壁、统一人物与建筑比例</p></header><main>
<section><h2>一片小世界</h2><p>合成美术样板，展示素材组合；尚未接入游戏，不代表真实对局。</p><button onclick="setScale(1)">原始 1×</button><button onclick="setScale(2)">放大 2×</button><button onclick="setScale(3)">放大 3×</button><div class="scroll"><img id="scene" src="scene.png" width="1280" alt="同尺度山体、树林、城镇与军队"></div></section>
<section><h2>整批素材与比例</h2><img src="overview.png" alt="新素材总览"></section>
<section><h2>组件动作样片</h2><img src="motion.gif" alt="树干固定、叶簇轻摆及人物动作"><p class="note">居民与步兵共用10像素身体基准。树动画只移动叶簇。人物图集含四方向待机、行走与劳动/攻击；本批尚未制作受击、阵亡等完整动作。</p></section>
<section><h2>完整图录</h2><img src="catalog.png" alt="十二种建筑、全部山体和岸线连接块、人物四方向"></section>
<p><a href="../../../mods/ui/desktop/assets/unified-v1/manifest.json">尺寸、锚点与帧表清单</a> · <a href="../../../mods/ui/desktop/assets/unified-v1/README.md">素材说明</a></p>
</main><script>function setScale(s){document.getElementById('scene').width=640*s;}</script></html>'''
    (PREVIEW/"index.html").write_text(page,encoding="utf-8")


def package():
    destination=ROOT/"archive/design/zhanguo-unified-art-v1.zip"
    destination.parent.mkdir(parents=True,exist_ok=True)
    files=sorted(p for folder in [ART,PREVIEW] for p in folder.rglob("*") if p.is_file() and not p.name.endswith(".import"))
    files.append(Path(__file__).resolve())
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as archive:
        for p in files:
            info=zipfile.ZipInfo(str(p.relative_to(ROOT)),(2026,10,24,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o644<<16
            archive.writestr(info,p.read_bytes())
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip",action="store_true",help="Also package sources, textures and previews")
    args=parser.parse_args()
    renderer=Rasterizer()
    terrain();nature();buildings();characters()
    export(renderer)
    sample_scene();board();animations();catalog();review_html()
    manifest=atlas_and_manifest(renderer)
    report=validate(manifest,renderer)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.zip:print(package())


if __name__=="__main__":main()
