# -*- coding: utf-8 -*-
"""确定性的玩家地图：图给空间，游戏上下文给图例和精确身份。

Pillow 可选；画图失败返回 None。**默认自动裁到视野框**（实测差 4 倍 token，见 `_auto_bounds`），
坐标均为 1-based。
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
    from mods.native.balance import ARMY_MAX_HP

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
    """图例 —— **一律走文本**，不画进图里。

    ★ 两条实测支撑（2026-10-12）：
      ① 同一张图、同一个模型，只换对照表的位置：**画在图里 3/12、放进文本 9/12**；
      ② 用户：「现代模型都是 ocr 特化的」——对**字**的读取能力远强于对**色块/图形**的。
    ⇒ 原则：**图里能写成字的都写成字，颜色只保留"大块分区"这一个用途。**
      所以地形/厅/城全是字母，图例全在这里，图里不再有任何需要"解码"的东西。
    """
    parts = [f"{nm}={COLOR_NAMES[i % len(COLOR_NAMES)]}" + ("（你）" if nm == name else "")
             for i, nm in enumerate(world.order)]
    return ("【图片地图读法】国别颜色对照：" + "　".join(parts) + "。"
            "坐标(x,y)从1开始：x向右、y向下，四边都标了刻度。"
            "★ 图上所有标记都是**字母**：地形 P平原 F森林 H丘陵 M山地 D沙漠（格左下）、"
            "T=市政厅(国祚，拔光某国全部即灭其国)、C+数字=城堡等级（格右下）。"
            "深蓝黑格=视野外(不是无主)。国土整格铺国色；有斜纹=该格没有厅/城/军队。"
            "兵牌：白底，外圈绿=你自己、蓝=友军(同盟)、红=敌人(交战方)、灰=中立；"
            "内芯 X=步兵 / =骑兵 ·=民兵；右侧数字=该阵营在这一格有几支；"
            "牌下数字=该阵营在这一格的总血量(不是百分比)。空心圆=野人守军。"
            "★ 图上不写国名/番号/兵种明细：拿坐标去文本面板查（每格一行、带番号）。")


def render(world, name: str, *, max_px: int = 960, emit=None, bounds=None) -> bytes | None:
    """**默认自动裁**到视野框（理由与实测见 `_auto_bounds`）；
    `bounds=(x0,x1,y0,y1)` 可显式指定取景（0-based，含端点），传全世界能拿到整图。

    max_px 是网格边长预算，不含四边刻度；格宽最小80、最大96；预算低于可读下限时优先保证符号尺寸。
    ★ 注意：**80px 的下限会盖过 max_px** —— 取景大到一定程度时，实际边长由格数×80 决定。
      有了自动裁，取景通常只有几格到十几格，这个下限基本不会顶到。
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


# 地标与兵牌共用暖白底、细深色轮廓。
SYMBOL_FACE = (248, 247, 241)
SYMBOL_EDGE = (39, 49, 59)


def _unit_glyph(d, cx, cy, w, h, kind, col):
    # 兵种是线符号，不能随牌宽膨胀成实心大叉。
    width = 1 if min(w,h)<10 else 2
    if kind == "民":
        r = max(1, min(3, min(w,h)))
        d.ellipse((cx-r, cy-r, cx+r, cy+r), fill=col)
    else:
        d.line((cx-w, cy+h, cx+w, cy-h), fill=col, width=width)
        if kind != "骑":
            d.line((cx-w, cy-h, cx+w, cy+h), fill=col, width=width)


def _ink_on(rgb):
    """在这个底色上能读的字色（亮底用深字、暗底用浅字）。"""
    lum = 0.30 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]
    return (16, 17, 20) if lum > 130 else (246, 246, 241)


def _terrain(d, terrain, cell, base, *, busy=False):
    """地形**只画字母**（P平原 F森林 H丘陵 M山地 D沙漠）——用户 2026-10-12：

        「我建议也换字母，厅也用字母，城堡也用字母算了」

    ★ 为什么不是图形（这版原先画的是树/山/沙丘）：字母与**文本面板的 `_fmt_atlas`
      同一套词汇**（`game.TERRAIN_CHARS`），图与文能逐字对读，模型不必再从"图形像什么"
      猜"这是哪种地形"；而且图形在小格子里会糊成色块。字母一律锚在**底栏左端**，
      位置固定 —— 固定位置本身也是可学的信息。
    """
    from game import TERRAIN_CHARS
    ch = TERRAIN_CHARS.get(terrain, "?")
    d.text((3, cell - 2), ch, font=_font(max(10, int(cell * 0.22))),
           anchor="ls", fill=_ink_on(base))


def _landmarks(d, cell, base, *, townhall=False, castle=0):
    """厅与城堡**也只用字母**：`T`=市政厅(国祚)、`C`+数字=城堡等级。

    ★ 与地形同处底栏：左=地形、中=厅、右=城堡。三个位置全固定。
    ★ `T`/`C` 与地形字母 `P/F/H/M/D` 不重叠；国别这一版**不用字母**（纯颜色），
      所以也不会撞国别代码。
    """
    ink = _ink_on(base)
    if townhall:
        d.text((cell // 2, cell - 2), "T", font=_font(max(12, int(cell * 0.28))),
               anchor="ms", fill=ink)
    if castle:
        d.text((cell - 3, cell - 2), f"C{castle}", font=_font(max(11, int(cell * 0.24))),
               anchor="rs", fill=ink)


def _counter_slots(cell):
    """同一缩放级别下固定四个槽；不根据支数或同格阵营数改变牌宽高。"""
    gap, pad = 3, 3
    width = (cell-2*pad-gap)//2
    height = (cell*3)//8-3
    return [(pad+col*(width+gap), pad+row*(height+gap),
             pad+col*(width+gap)+width-1, pad+row*(height+gap)+height-1)
            for row in range(2) for col in range(2)]


def _counter(d, box, side, nation_color, armies, max_hp):
    """固定白底牌面：兵种左栏、支数右栏；总HP独立下栏，直接比较兵力。"""
    x0,y0,x1,y1=box
    hp_height=max(7,(y1-y0+1)//4)
    face_bottom=y1-hp_height-2
    d.rectangle((x0,y0,x1,face_bottom),fill=side,outline=SYMBOL_EDGE,width=1)
    left,right,top,bottom=x0+3,x1-3,y0+3,face_bottom-3
    d.rectangle((left,top,right,bottom),fill=SYMBOL_FACE)
    count=str(len(armies))
    size=max(7,min(11,(bottom-top)+1))
    font=_font(size)
    # 常规数量固定右栏；极大堆叠压缩数字，牌与兵种都不缩放。
    count_width=max(7,int((right-left)*.38))
    while font.getlength(count)>count_width and size>5:
        size-=1
        font=_font(size)
    if font.getlength(count)>count_width:
        count="99+"
        font=_font(5)
    split=right-count_width
    d.line((split,top,split,bottom),fill=(206,209,207),width=1)
    cy=(top+bottom)//2
    d.text((right,cy),count,font=font,anchor="rm",fill=SYMBOL_EDGE)
    kinds=[k for k in ("步","骑","民") if any(a["kind"]==k for a in armies)]
    span=max(1,split-left-1)
    step=span/max(1,len(kinds))
    ink=tuple(int(v*.48) for v in nation_color)
    for i,kind in enumerate(kinds):
        cx=round(left+(i+.5)*step)
        _unit_glyph(d,cx,cy,max(1,int(step*.30)),max(1,int((bottom-top)*.35)),kind,ink)
    # 总量比百分比更有用：100与400必须在图上明确区分。
    hp_top=face_bottom+2
    d.rectangle((x0,hp_top,x1,y1),fill=SYMBOL_FACE,outline=SYMBOL_EDGE,width=1)
    label=str(sum(a["hp"] for a in armies))
    hp_size=max(7,min(11,hp_height-1))
    hp_font=_font(hp_size)
    while hp_font.getlength(label)>x1-x0-3 and hp_size>4:
        hp_size-=1
        hp_font=_font(hp_size)
    d.text(((x0+x1)//2,(hp_top+y1)//2),label,font=hp_font,anchor="mm",fill=SYMBOL_EDGE)



def _auto_bounds(state, n):
    """**自动取景**：裁到快照里可见格（∪ 国土）的包围盒 + 一圈，夹在世界内。

    ★★ 2026-10-12 实测**为什么必须裁**（这是本模块最贵的一个数）：

        | 取景 | 尺寸 | 图净耗 token |
        |---|---|---|
        | 全世界 | 1996×2010 = 4.01 Mpx | **2512** |
        | **裁到视野框**（8×7 格） | 844×762 = 0.64 Mpx | **636** |

      差 **4 倍**，而两者读字母的成绩**一模一样**（都答对 D,F,H,M,P,T）。
      更要命的是 2512 **比它要替代的文字 atlas（T110 = 2056 tok）还贵** ——
      不裁剪等于把"用图代替文字"这件事的意义整个抹掉。

      为什么不裁也"说得通"（上一版的理由："全图不因飞地或联盟改变取景，模型可以跨回合
      比较同一坐标"）：那个好处是"坐标系稳定"，但它买到的那点稳定性，代价是 4 倍 token
      和一整屏 95% 的空白（24×24 的图里玩家只看得到中间 6×6）。
      ⇒ 判据是**每 token 买到多少信息**，不是坐标系稳不稳。坐标由四边刻度保证，
        裁了照样读得出绝对坐标。
    """
    cells = state.get("cells") or []
    if not cells:
        return (0, n - 1, 0, n - 1)
    xs = [c["x"] for c in cells]
    ys = [c["y"] for c in cells]
    b = (max(0, min(xs) - 1), min(n - 1, max(xs) + 1),
         max(0, min(ys) - 1), min(n - 1, max(ys) + 1))
    # 裁完跟整图差不多大就别裁了 —— 多那一条黑边没有信息，只是把名字从"全世界"换成"取景"
    if (b[1] - b[0] + 1) * (b[3] - b[2] + 1) > 0.6 * n * n:
        return (0, n - 1, 0, n - 1)
    return b


def _render_snapshot(state, *, max_px, bounds):
    from PIL import Image, ImageDraw

    if state["version"] != SNAPSHOT_VERSION:
        raise ValueError("不支持的地图快照版本")
    n = state["size"]
    bounds = _auto_bounds(state, n) if bounds is None else tuple(bounds)
    xlo, xhi, ylo, yhi = bounds
    if not (0 <= xlo <= xhi < n and 0 <= ylo <= yhi < n):
        raise ValueError("取景坐标越界")
    if max_px <= 0:
        raise ValueError("max_px 必须为正")
    cell = max(80, min(96, int(max_px/max(xhi-xlo+1, yhi-ylo+1))))
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
                # 地形/厅/城一律走**底栏字母**，位置固定（左/中/右），兵牌槽位不动。
                _terrain(td, c["terrain"], cell, col, busy=bool(has_counter or c["townhall"] or c["castle"]))
                _landmarks(td, cell, col, townhall=bool(c["townhall"]), castle=int(c["castle"] or 0))
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
                for box,key in zip(_counter_slots(cell),seq):
                    if key is None:
                        td.rectangle(box,fill=SYMBOL_FACE,outline=SYMBOL_EDGE)
                        td.text(((box[0]+box[2])//2,(box[1]+box[3])//2),
                                "+"+str(overflow+1),font=f_small,anchor="mm",fill=SYMBOL_EDGE)
                        continue
                    arr=groups[key]
                    lead=max(arr,key=lambda a:(a["hp"],a["owner"]))
                    ring=colors[key] if observer else SIDE_COLORS[key]
                    _counter(td,box,ring,colors.get(lead["owner"],INK),arr,state["army_max_hp"])
                if any(a["owner"]=="野人" for a in here):
                    # 野人标记：右上角小空心圆。**不能用 `cy`** —— 那个变量是上一版给
                    # "地标与地貌共用底栏"算的中点，这一版改字母后已经不存在了
                    # （2026-10-12：删掉那段时漏了这个引用，渲染直接抛 UnboundLocalError，
                    #  而外层 try 把它吞成"返回 None"，症状只是"图不出来"）。
                    td.ellipse((cell-9,3,cell-3,9),outline=BARB,width=1)
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
