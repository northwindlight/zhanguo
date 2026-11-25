"""情报记忆：把引擎写进 `econ_intel` / `maps` 的情报文本解析成结构化字段（观测用）。

引擎（mp.py）只存文本、不能改（契约），所以这里按引擎的固定排版解析：
- 间谍回报 `_econ_snapshot`：「【X 情报】国库/储备: 黄金1490 粮食20 … | 电网: 停摆|产a/需b」
  + 每块地「名 地形(x,y) 城Lc 位s/20 建筑[厅×1 …(在建)]」（坐标 1 起）+「军情（仅数量，位置未知）: 步×3、骑×1」；
- 央行售出的报表：「🏦【央行售出】X 第 k 回合经济报表」——正文是调用方排版，不解析，
  结构化数值取 `w.econ_reports[X]` 里 report_turn==k 的那期（就是买到的那张表）；
- 地图（换图/间谍偷图）`_map_snapshot`：「名 地形(x,y) 城Lc 位s」= 来源国当时的国土。

记忆口径：每个来源国只记**最近一次收到的**每类情报（不受引擎 KEEP_SAVES/KEEP_MAPS 截断影响：
玩家看过就记得）。C++ 采样器必须产出逐位相同的字段（见 `claude-intel-spec` 信件）。
"""
from __future__ import annotations

import re

import mp
from balance import BUILDINGS, UNIT_TYPES

BLD = list(BUILDINGS)
UNITS = list(UNIT_TYPES)
_HEAD = re.compile(r"【(.+?) 情报】国库/储备: (.*?) \| 电网: (\S+)")
_KV = re.compile(r"([^\d\s.-]+)(-?\d+(?:\.\d+)?)")
_GRID = re.compile(r"产(-?\d+(?:\.\d+)?)/需(-?\d+(?:\.\d+)?)")
_TILE = re.compile(r"\((\d+),(\d+)\) 城L(\d+) 位(\d+)(?:/20 建筑\[(.*?)\])?\s*$")
_BLDTOK = re.compile(r"([^\s×、\[\]]+?)×(\d+)(\(在建\))?")
_ARMY = re.compile(r"军情（仅数量，位置未知）: (.*)$")
_SALE = re.compile(r"🏦【央行售出】(.+?) 第 (\d+) 回合经济报表")


def parse_spy(text: str) -> dict | None:
    lines = text.split("\n")
    m = _HEAD.match(lines[0])
    if not m:
        return None
    res = {k: float(v) for k, v in _KV.findall(m.group(2))}
    g = _GRID.match(m.group(3))
    short = m.group(3) == "停摆"
    et, mt = (float(g.group(1)), float(g.group(2))) if g else (0.0, 0.0)
    tiles, armies = {}, {}
    for ln in lines[1:]:
        t = _TILE.search(ln)
        if t and t.group(5) is not None:
            bld, pend = [0] * len(BLD), 0
            for name, c, p in _BLDTOK.findall(t.group(5)):
                if p:
                    pend += int(c)
                elif name in BLD:
                    bld[BLD.index(name)] += int(c)
            tiles[(int(t.group(1)) - 1, int(t.group(2)) - 1)] = (int(t.group(3)), int(t.group(4)), bld, pend)
            continue
        a = _ARMY.search(ln)
        if a:
            for name, c, _ in _BLDTOK.findall(a.group(1)):
                armies[name] = armies.get(name, 0) + int(c)
    return dict(res=res, short=short, et=et, mt=mt, tiles=tiles, armies=armies)


def parse_map(text: str) -> dict:
    tiles = {}
    for ln in text.split("\n")[1:]:
        t = _TILE.search(ln)
        if t:
            tiles[(int(t.group(1)) - 1, int(t.group(2)) - 1)] = (int(t.group(3)), int(t.group(4)))
    return tiles


class IntelMemory:
    """一个座位的情报记忆。每回合 `update(w)` 一次（回合开头编码前）。"""

    def __init__(self, me: str):
        self.me = me
        self.spy: dict[str, tuple[int, dict]] = {}        # 来源国 → (到手回合, parse_spy)
        self.report: dict[str, tuple[int, dict]] = {}     # 来源国 → (买到的回合, econ_reports 那一期)
        self.maps: dict[str, tuple[int, dict]] = {}       # 来源国 → (到手回合, parse_map)
        self.tile: dict[tuple[int, int], tuple] = {}      # 格 → (回合, 来源国, 城, 位, bld|None, 在建)
        self._seen: set = set()

    def update(self, w: mp.World) -> None:
        for e in w.econ_intel.get(self.me, []):
            key = ("e", e["from"], e["turn"], len(e["text"]), e["text"][:24])
            if key in self._seen:
                continue
            self._seen.add(key)
            src = e["from"]
            sm = _SALE.match(e["text"])
            if sm:
                k = int(sm.group(2))
                rep = next((r for r in (w.econ_reports.get(src) or []) if r.get("report_turn") == k), None)
                if rep is not None:
                    self.report[src] = (e["turn"], dict(rep))
                continue
            p = parse_spy(e["text"])
            if p is None:
                continue
            self.spy[src] = (e["turn"], p)
            for xy, (c, s, bld, pend) in p["tiles"].items():
                if xy not in self.tile or self.tile[xy][0] <= e["turn"]:
                    self.tile[xy] = (e["turn"], src, c, s, bld, pend)
        for e in w.maps.get(self.me, []):
            key = ("m", e["from"], e["turn"], len(e["text"]), e["text"][:24])
            if key in self._seen:
                continue
            self._seen.add(key)
            src = e["from"][:-4] if e["from"].endswith("(间谍)") else e["from"]
            tiles = parse_map(e["text"])
            self.maps[src] = (e["turn"], tiles)
            for xy, (c, s) in tiles.items():
                old = self.tile.get(xy)
                if old is None or old[0] < e["turn"]:
                    self.tile[xy] = (e["turn"], src, c, s, None, 0)
