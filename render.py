# -*- coding: utf-8 -*-
"""确定性的玩家地图：图给空间，随图文本给图例和精确身份。

Pillow 可选；画图失败返回 None。默认全世界、不裁剪，坐标均为 1-based。
(snapshot → render_snapshot) 支持 JSON 事实快照重绘；快照只含当时可见信息，
不存 PNG/base64，不引用可变 World。渲染版本/字体相同的快照逐字节重绘。
"""
from __future__ import annotations

import io
import os

TERRAIN_FILL = {
    "平原": (63, 70, 54),
    "森林": (44, 58, 46),
    "丘陵": (74, 68, 54),
    "山地": (69, 66, 62),
    "沙漠": (77, 68, 51),
}
#: 视野外：近黑（"看不见"在图上就是**没有像素**，不是 `?.`）
FOG_FILL = (18, 25, 35)
BG = (12, 19, 29)
GRID = (34, 43, 55)
INK = (232, 232, 228)          # 主文字
INK_DIM = (138, 140, 138)      # 次要文字（地形字母、刻度）
BARB = (150, 148, 142)         # 野人

# 国家色沿用现有色盘；国土不加字母，精确国别由随图文本核对。
NATION_COLORS = [
    (218, 185, 66),    # 黄
    (58, 174, 184),    # 青
    (188, 84, 170),    # 品红
    (193, 202, 216),   # 银白
    (226, 139, 60),    # 橙（不再与黄只差亮度）
    (116, 94, 184),    # 紫（不再与青只差亮度）
    (229, 151, 181),   # 粉（避免韩/楚同色相两档亮度）
    (94, 145, 205),    # 蓝
]

#: 兵牌的阵营色 —— **固定四色，不随国家变**（用户 2026-10-12：「不用形状区分，用颜色，
#: 友军蓝色，自己军队绿色，敌人红色，中立灰色」）。画在兵牌**外层那一圈**上。
SIDE_COLORS = {
    "self": (58, 196, 96),       # 自己：绿
    "ally": (58, 132, 232),      # 友军：蓝
    "enemy": (224, 62, 58),      # 敌人：红
    "neutral": (142, 144, 142),  # 中立：灰
}

#: 国土色的**中文色名**（写进 `legend_text` 给模型对号用，不画在图里）
COLOR_NAMES = ["黄", "青", "品红", "银白", "橙", "紫", "粉", "蓝"]

RES_COLORS = {
    "矿石": (150, 150, 158),
    "黄金": (222, 178, 62),
    "耕地": (126, 176, 84),
    "石油": (108, 106, 128),
    "木头": (140, 106, 66),
}

# 图中文字仅用 ASCII，字体不必有中文；国家名和完整图例随文本发送。
_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
SNAPSHOT_VERSION = 1
_warned = False


def _font(size):
    from PIL import ImageFont
    for path in [os.environ.get("ZHANGUO_MAP_FONT", ""), *_FONT_CANDIDATES]:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Pillow 自带字体仍可画 ASCII，不因为系统没装 CJK 而放弃整个地图。
    return ImageFont.load_default(size=size)


def available() -> tuple[bool, str]:
    try:
        from PIL import Image, ImageDraw  # noqa: F401
        _font(12)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _warn_once(emit, msg):
    global _warned
    if not _warned:
        _warned = True
        if emit:
            emit(f"🖼 地图图片不可用：{msg}（退回文本面板）")


def _sides(world, name):
    sides = {nm: "neutral" for nm in world.order}
    if name is None:
        return sides
    bloc = world.bloc_of(name)
    if bloc:
        for nm in bloc["members"]:
            sides[nm] = "ally"
    for war in world.wars:
        attackers, defenders = world._war_sides(war)
        opponents = defenders if name in attackers else attackers if name in defenders else ()
        for nm in opponents:
            sides[nm] = "enemy"
    sides[name] = "self"
    return sides


def snapshot(world, name: str | None) -> dict:
    """冻结当前玩家可见事实。没有 fog 外格子的归属/军队/资源，也不读邻格秘密画边界。"""
    import mp_ai
    from balance import ARMY_MAX_HP

    n = world.size
    observer = name is None
    vis = ({(x, y) for y in range(n) for x in range(n)} if observer
           else mp_ai._visible_cells(world, name))
    reach = set() if observer else mp_ai._reach_cells(world, name) & vis
    sides = _sides(world, name)
    cells = []
    for x, y in sorted(vis, key=lambda xy: (xy[1], xy[0])):
        tile = world.tiles.get((x, y)) or {}
        owner = world.owned_by(x, y)
        buildings = tile.get("buildings", {})
        # 他国内政仍未探明；公开地标仅厅/城，资源点只画自己和无主废墟。
        resources = tile.get("resources", {}) if observer or owner in (None, name) else {}
        cells.append({"x": x, "y": y, "owner": owner,
                      "terrain": world.tile_terrain(x, y),
                      "townhall": buildings.get("市政厅", 0),
                      "castle": buildings.get("城堡", 0),
                      "resources": [r for r in RES_COLORS if resources.get(r, 0) > 0],
                      "reach": (x, y) in reach})
    armies = [{"x": a["x"], "y": a["y"], "owner": a["owner"],
               "kind": a.get("type", "步"), "hp": a.get("hp", 0)}
              for a in world.armies if (a["x"], a["y"]) in vis and a.get("hp", 0) > 0]
    armies.sort(key=lambda a: (a["y"], a["x"], a["owner"], a["kind"], a["hp"]))
    return {"version": SNAPSHOT_VERSION, "size": n, "turn": world.turn,
            "player": name, "order": list(world.order), "sides": sides,
            "army_max_hp": ARMY_MAX_HP, "cells": cells, "armies": armies}


def legend_text(world, name) -> str:
    parts = [f"{nm}={COLOR_NAMES[i % len(COLOR_NAMES)]}" + ("（你）" if nm == name else "")
             for i, nm in enumerate(world.order)]
    return ("国别颜色对照：" + "　".join(parts) + "。坐标(x,y)从1开始：x向右、y向下。"
            "深蓝黑格=视野外（不是无主）；草线平原、树森林、圆丘丘陵、尖峰山地、沙纹沙漠。"
            "国土整格国色；浅斜纹=无厅/城/军队。白线=可见国界；白虚线=本回合可及。"
            "房子=市政厅；城垛=城堡，数字=等级；两者同时存在时都显示。"
            "兵牌外圈绿自己/蓝盟友/红敌人/灰中立；内芯X步兵、/骑兵、点民兵，"
            "混合兵种并列；数字=该阵营支数，血条=合计HP/合计最大HP。"
            "兵种符号取该阵营最高HP部队的国家色，不代表全组只有该国；确切国别、番号、HP查文本。"
            "空心圆=野人。资源点只显示自己及可见无主废墟；世界图外圈改用国家色。")


def render(world, name: str, *, max_px: int = 960, emit=None, bounds=None) -> bytes | None:
    """默认完整世界；bounds=(x0,x1,y0,y1) 可显式请求局部细图（0-based，含端点）。

    max_px 是网格边长预算，不含四边刻度；格宽最小24、最大72。
    全图不因飞地或联盟改变取景，模型可以跨回合比较同一坐标。
    """
    try:
        return render_snapshot(snapshot(world, name), max_px=max_px, emit=emit, bounds=bounds)
    except Exception as exc:
        _warn_once(emit, f"{type(exc).__name__}: {str(exc)[:160]}")
        return None


def render_world(world, *, max_px: int = 1500, emit=None) -> bytes | None:
    return render(world, None, max_px=max_px, emit=emit)


def render_snapshot(state: dict, *, max_px: int = 960, emit=None, bounds=None) -> bytes | None:
    """从 JSON 可序列化的可见快照画图；历史重放无需读取当前世界。"""
    try:
        return _render_snapshot(state, max_px=max_px, bounds=bounds)
    except Exception as exc:
        _warn_once(emit, f"{type(exc).__name__}: {str(exc)[:160]}")
        return None


def _hatch(d, box, color, step=15, width=5):
    x0, y0, x1, y1 = box
    for c in range(x0 - y1, x1 - y0 + 1, step):
        points = [(c + y0, y0), (c + y1, y1), (x0, x0 - c), (x1, x1 - c)]
        points = sorted(set((x, y) for x, y in points if x0 <= x <= x1 and y0 <= y <= y1))
        if len(points) >= 2:
            d.line([points[0], points[-1]], fill=color, width=width)


def _house(d, cx, cy, s):
    ink = (248, 246, 238)
    d.polygon([(cx-s, cy), (cx+s, cy), (cx, cy-s)], fill=ink, outline=BG)
    d.rectangle((cx-s*.7, cy, cx+s*.7, cy+s), fill=ink, outline=BG)
    d.rectangle((cx-s*.2, cy+s*.4, cx+s*.2, cy+s), fill=BG)


def _castle(d, cx, cy, s):
    d.rectangle((cx-s, cy-s*.3, cx+s, cy+s), fill=(185, 190, 200), outline=BG)
    for k in (-1, 0, 1):
        tx = cx+k*s*.65
        d.rectangle((tx-s*.23, cy-s, tx+s*.23, cy), fill=(185, 190, 200), outline=BG)
    d.rectangle((cx-s*.2, cy+s*.3, cx+s*.2, cy+s), fill=BG)


def _unit_glyph(d, cx, cy, w, h, kind, col):
    width = max(1, min(w, h)//2)
    if kind == "民":
        r = max(1, min(w, h))
        d.ellipse((cx-r, cy-r, cx+r, cy+r), fill=col)
    else:
        d.line((cx-w, cy+h, cx+w, cy-h), fill=col, width=width)
        if kind != "骑":
            d.line((cx-w, cy-h, cx+w, cy+h), fill=col, width=width)


def _terrain(d, terrain, cell, base, *, busy=False):
    """抽象地貌：宽阔而低对比，不与亮色地标和兵牌争注意力。"""
    ink=tuple(max(10,int(v*.5)) for v in base)
    cx,cy=cell//2,cell//2
    radius=max(5,cell//4)
    if busy:
        # 军队/地标占中央时，地貌退到左下，仍能看出树/山的轮廓。
        cx,cy=7,cell-6
        radius=4
    if terrain=="森林":
        for dx,dy in ((-radius//2,1),(radius//2,0),(0,-radius//2)):
            tx,ty=cx+dx,cy+dy
            d.polygon(((tx-radius//2,ty+radius//2),(tx+radius//2,ty+radius//2),(tx,ty-radius)),fill=ink)
            d.line((tx,ty,tx,ty+radius),fill=ink,width=2)
    elif terrain=="山地":
        for dx in (-radius//2,radius//2):
            tx=cx+dx
            d.polygon(((tx-radius,cy+radius),(tx+radius,cy+radius),(tx,cy-radius)),fill=ink)
            d.polygon(((tx-radius//3,cy-radius//3),(tx+radius//3,cy-radius//3),(tx,cy-radius)),
                      fill=tuple(min(255,int(v*.7+60)) for v in base))
    elif terrain=="丘陵":
        for dx in (-radius//2,radius//2):
            d.arc((cx+dx-radius,cy-radius//2,cx+dx+radius,cy+radius),180,355,fill=ink,width=2)
    elif terrain=="沙漠":
        for offset in (-radius//2,radius//2):
            d.arc((cx-radius,cy+offset-radius//2,cx+radius,cy+offset+radius//2),185,355,fill=ink,width=2)
    else:
        for dx,dy in ((-radius//2,0),(radius//2,radius//2),(0,-radius//2)):
            d.line((cx+dx-2,cy+dy,cx+dx+2,cy+dy),fill=ink,width=1)


def _counter(d, box, side, nation_color, armies, max_hp):
    """所有内容在槽位矩形内；四阵营/三位支数也不能溢入隔壁。"""
    x0, y0, x1, y1 = box
    if x1-x0 < 12 or y1-y0 < 11:
        # 极小格密集阵营只画紧凑色框，精确支数交给文本/局部放大图。
        d.rectangle(box, fill=side, outline=BG)
        if x1-x0 >= 6 and y1-y0 >= 5:
            d.rectangle((x0+2,y0+2,x1-2,y1-2),fill=BG)
            _unit_glyph(d,(x0+x1)//2,(y0+y1)//2,1,1,armies[0]["kind"],nation_color)
        return
    d.rectangle(box, fill=BG)
    d.rectangle((x0+1, y0+1, x1-1, y1-1), fill=side)
    d.rectangle((x0+3, y0+3, x1-3, y1-4), fill=INK)
    d.rectangle((x0+4, y0+4, x1-4, y1-5), fill=BG)
    kinds = [k for k in ("步", "骑", "民") if any(a["kind"] == k for a in armies)]
    glyph_right = x1-4
    if len(armies) > 1:
        size = max(6, min(11, (y1-y0)//2))
        f = _font(size)
        count = str(len(armies))
        while f.getlength(count) > (x1-x0)*.45 and size > 5:
            size -= 1
            f = _font(size)
        d.text((x1-3, (y0+y1)//2-1), count, font=f, anchor="rm", fill=INK,
               stroke_width=1, stroke_fill=BG)
        glyph_right -= int(f.getlength(count))+1
    left = x0+5
    cy = (y0+y1)//2-1
    gw = max(1, (glyph_right-left)//max(1,len(kinds)))
    for i, k in enumerate(kinds):
        _unit_glyph(d, left+gw*i+gw//2, cy, max(1,gw//2-1), max(1,(y1-y0-10)//2), k, nation_color)
    hp = sum(a["hp"] for a in armies)
    ratio = max(0., min(1., hp/(max_hp*len(armies))))
    d.rectangle((x0+2, y1-2, x1-2, y1-1), fill=(48, 48, 48))
    if ratio:
        d.rectangle((x0+2, y1-2, x0+2+int((x1-x0-4)*ratio), y1-1),
                    fill=(126,226,126) if ratio>.5 else (238,176,62))


def _render_snapshot(state, *, max_px, bounds):
    from PIL import Image, ImageDraw

    if state["version"] != SNAPSHOT_VERSION:
        raise ValueError("不支持的地图快照版本")
    n = state["size"]
    bounds = (0, n-1, 0, n-1) if bounds is None else tuple(bounds)
    xlo, xhi, ylo, yhi = bounds
    if not (0 <= xlo <= xhi < n and 0 <= ylo <= yhi < n):
        raise ValueError("取景坐标越界")
    if max_px <= 0:
        raise ValueError("max_px 必须为正")
    cell = max(24, min(72, int(max_px/max(xhi-xlo+1, yhi-ylo+1))))
    margin, top, bottom = 38, 54, 36
    gw, gh = (xhi-xlo+1)*cell, (yhi-ylo+1)*cell
    img = Image.new("RGB", (gw+2*margin, gh+top+bottom), BG)
    d = ImageDraw.Draw(img)
    cells = {(c["x"],c["y"]):c for c in state["cells"]}
    colors = {nm:NATION_COLORS[i%len(NATION_COLORS)] for i,nm in enumerate(state["order"])}
    army_at = {}
    for a in state["armies"]:
        army_at.setdefault((a["x"],a["y"]), []).append(a)
    observer = state["player"] is None
    f_tick = _font(max(11,min(15,cell//3)))
    f_small = _font(max(8,min(12,cell//4)))

    for y in range(ylo,yhi+1):
        for x in range(xlo,xhi+1):
            c = cells.get((x,y))
            # 逐格独立画层再贴图；线宽、数字、地标都不可能污染相邻格。
            tile = Image.new("RGB", (cell,cell), FOG_FILL)
            td = ImageDraw.Draw(tile)
            if c:
                owner = c["owner"]
                col = colors.get(owner, TERRAIN_FILL.get(c["terrain"], (60,60,60)))
                td.rectangle((1,1,cell-2,cell-2), fill=col)
                here = army_at.get((x,y),[])
                has_counter = any(a["owner"] != "野人" for a in here)
                if owner and not (c["townhall"] or c["castle"] or here):
                    _hatch(td, (1,1,cell-2,cell-2), tuple(int(v*.84) for v in col), step=20, width=2)
                # 用地貌图形传达空间，不要求模型逐格解码细小字母/资源点。
                _terrain(td, c["terrain"], cell, col, busy=bool(has_counter or c["townhall"] or c["castle"]))
                # 地标留在顶栏，不被中部兵牌遮掉；厅与城并存不再二选一。
                s = max(3,min(7,cell//8)) if has_counter else max(6,cell//5)
                cy = s+2 if has_counter else cell//2
                if c["townhall"]:
                    _house(td, cell//3 if c["castle"] else cell//2, cy, s)
                if c["castle"]:
                    cx = 2*cell//3 if c["townhall"] else cell//2
                    _castle(td,cx,cy,s)
                    td.text((cell-3,2),str(c["castle"]),font=f_small,anchor="rt",fill=INK,
                            stroke_width=1,stroke_fill=BG)
                groups = {}
                for a in here:
                    if a["owner"] == "野人":
                        continue
                    key = a["owner"] if observer else state["sides"].get(a["owner"],"neutral")
                    groups.setdefault(key,[]).append(a)
                seq = ([nm for nm in state["order"] if nm in groups] if observer else
                       [k for k in ("self","ally","enemy","neutral") if k in groups])
                # 观察者若超过四国同格，保留最强三组并显示 +N，绝不静默丢掉其他国家。
                overflow = max(0,len(seq)-4)
                if overflow:
                    seq = sorted(seq,key=lambda k:(-sum(a["hp"] for a in groups[k]),k))[:3]
                    seq.append(None)
                cols = 1 if len(seq)==1 else 2
                rows = (len(seq)+cols-1)//cols
                if rows:
                    ya, yb = (2*s+5 if c["townhall"] or c["castle"] else 3), cell-12
                    # 密集格顶栏图标紧凑，留至少两排兵牌。
                    ya = min(ya, yb-rows*11)
                    ya = max(2,ya)
                    sw = (cell-4)//cols
                    sh = (yb-ya)//rows
                    for i,key in enumerate(seq):
                        bx, by = 2+(i%cols)*sw, ya+(i//cols)*sh
                        box = (bx,by,bx+sw-2,by+sh-1)
                        if key is None:
                            td.rectangle(box, fill=BG, outline=INK_DIM)
                            td.text(((box[0]+box[2])//2,(box[1]+box[3])//2),
                                    "+"+str(overflow+1),font=f_small,anchor="mm",fill=INK)
                            continue
                        arr = groups[key]
                        lead = max(arr,key=lambda a:(a["hp"],a["owner"]))
                        ring = colors[key] if observer else SIDE_COLORS[key]
                        _counter(td,box,ring,colors.get(lead["owner"],INK),arr,state["army_max_hp"])
                if any(a["owner"]=="野人" for a in here):
                    td.ellipse((cell-9,2,cell-3,8),outline=BARB,width=2)
                if c["reach"]:
                    for i in range(3,cell-3,7):
                        j=min(i+3,cell-3)
                        for line in [(i,2,j,2),(i,cell-3,j,cell-3),(2,i,2,j),(cell-3,i,cell-3,j)]:
                            td.line(line,fill=INK,width=1)
            px, py = margin+(x-xlo)*cell, top+(y-ylo)*cell
            img.paste(tile,(px,py))
            if c is None:
                # 迷雾中每五格一条低对比定位线；只有坐标，不含世界事实。
                if x % 5 == 0:
                    d.line((px,py,px,py+cell-1),fill=(25,34,45))
                if y % 5 == 0:
                    d.line((px,py,px+cell-1,py),fill=(25,34,45))

    # 国界仅比较两边已知格；不可用 fog 外归属决定可见像素。
    for (x,y),c in cells.items():
        if not (xlo<=x<=xhi and ylo<=y<=yhi):
            continue
        px, py = margin+(x-xlo)*cell, top+(y-ylo)*cell
        for dx,dy in [(1,0),(0,1)]:
            neighbor=cells.get((x+dx,y+dy))
            if neighbor is None or neighbor["owner"]==c["owner"]:
                continue
            if dx and x<xhi:
                line=(px+cell-1,py,px+cell-1,py+cell-1)
            elif dy and y<yhi:
                line=(px,py+cell-1,px+cell-1,py+cell-1)
            else:
                continue
            d.line(line,fill=BG,width=3)
            d.line(line,fill=INK,width=1)

    for x in range(xlo,xhi+1):
        cx=margin+(x-xlo)*cell+cell//2
        for cy in (top-14,top+gh+14):
            d.text((cx,cy),str(x+1),font=f_tick,fill=INK,anchor="mm")
    for y in range(ylo,yhi+1):
        cy=top+(y-ylo)*cell+cell//2
        for cx in (margin-17,margin+gw+17):
            d.text((cx,cy),str(y+1),font=f_tick,fill=INK,anchor="mm")
    scope="World" if bounds==(0,n-1,0,n-1) else f"x{xlo+1}-{xhi+1} y{ylo+1}-{yhi+1}"
    title=f"{'Observer' if observer else 'Player'}  /  Turn {state['turn']}  /  {scope}  {n} x {n}"
    d.text((margin,9),title,font=_font(16),fill=INK)
    if not observer:
        d.rectangle((img.width-30,12,img.width-16,26),fill=colors.get(state["player"],INK))
    buf=io.BytesIO()
    img.save(buf,format="PNG",optimize=True)
    return buf.getvalue()


def data_url(png: bytes, *, detail: str | None = None) -> dict:
    """PNG → OpenAI 兼容图像块；detail 是否生效取决于提供方。"""
    import base64
    image={"url":"data:image/png;base64,"+base64.b64encode(png).decode("ascii")}
    if detail:
        image["detail"]=detail
    return {"type":"image_url","image_url":image}
