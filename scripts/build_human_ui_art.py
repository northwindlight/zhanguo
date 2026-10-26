#!/usr/bin/env python3
"""Build original SVG UI assets and synthetic previews; no game imports or calls."""
from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

from PIL import Image
from build_unified_art import Rasterizer

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "mods/ui/desktop/assets/human-ui-v1"
PREVIEW = ROOT / "docs/design/human-ui-v1"
WORLD = ROOT / "mods/ui/desktop/assets/unified-v1/svg"
NS = "http://www.w3.org/2000/svg"
P = dict(bg="#111f20", panel="#1d302e", raised="#293e37", line="#61735a",
         gold="#d3b965", paper="#e5dfbd", muted="#a7b39b", shade="#0d1b1d",
         blue="#7bbdd4", blue_dark="#38697f", red="#e09b80", red_dark="#914e43",
         green="#a8c889", ink="#303d3b", wood="#ae8757")


def rect(x, y, w, h, fill, **attrs):
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" {extra}/>'


def poly(points, fill):
    return '<polygon points="' + " ".join(f"{x},{y}" for x, y in points) + f'" fill="{fill}"/>'


def group(body, x=0, y=0, scale=1, **attrs):
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<g transform="translate({x} {y}) scale({scale})" {extra}>{body}</g>'


def svg(w, h, body, title):
    return (f'<svg xmlns="{NS}" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'shape-rendering="crispEdges"><title>{html.escape(title)}</title>{body}</svg>\n')


def text(x, y, value, size=18, fill=None):
    return (f'<text x="{x}" y="{y}" fill="{fill or P["paper"]}" font-size="{size}" '
            f'font-family="Noto Sans CJK SC,sans-serif" shape-rendering="auto">{html.escape(value)}</text>')


def wrap_label(value, budget=19):
    lines=[]; line=""; width=0
    for char in value:
        char_width=2 if unicodedata.east_asian_width(char) in ("W","F") else 1
        if width+char_width>budget:
            lines.append(line); line=""; width=0
        line+=char; width+=char_width
    if line: lines.append(line)
    return lines


def box(x, y, w, h, fill=None, border=None):
    return rect(x, y, w, h, border or P["line"]) + rect(x+2, y+2, w-4, h-4, fill or P["panel"])


def icon(name):
    a, b, c = P["paper"], P["gold"], P["wood"]
    parts = []
    def r(x, y, w, h, fill=a): parts.append(rect(x, y, w, h, fill))
    def p(points, fill=a): parts.append(poly(points, fill))
    def sheet():
        r(4, 3, 16, 19, P["ink"]); r(5, 2, 14, 18, a)
        r(7, 6, 10, 2, c); r(7, 10, 8, 2, c); r(7, 14, 6, 2, c)
    def flag(x, y):
        r(x, y, 2, 16, c); p([(x+2,y),(x+10,y),(x+8,y+4),(x+10,y+8),(x+2,y+8)], b)
    if name == "build":
        p([(3,3),(7,1),(15,9),(11,13)], b); p([(10,13),(13,10),(22,19),(19,22)], c)
        r(3,19,9,3,a); r(5,16,5,3,a)
    elif name == "army":
        p([(5,4),(19,4),(19,13),(16,18),(12,22),(8,18),(5,13)], b)
        p([(8,7),(16,7),(16,13),(12,18),(8,13)], P["blue_dark"])
        r(11,7,2,10,a)
    elif name == "market":
        r(3,6,18,5,c); r(4,12,2,10,c); r(18,12,2,10,c); r(2,18,20,4,c)
        p([(5,2),(19,2),(22,6),(2,6)], b)
        for x in (3,9,15): r(x,7,3,4,a)
        r(8,15,5,3,b); r(13,13,3,5,a)
    elif name == "diplomacy":
        r(4,4,16,16,a); r(7,3,2,18,c); r(15,3,2,18,c)
        r(2,5,4,14,b); r(18,5,4,14,b)
        r(10,8,4,2,c); r(10,12,4,2,c); r(10,16,4,2,c)
    elif name == "alliance":
        flag(2,3); flag(13,5); r(5,20,15,2,a); r(10,17,2,4,a)
    elif name in ("intelligence", "spy"):
        p([(2,11),(6,6),(18,6),(22,11),(18,16),(6,16)], a)
        r(9,7,6,8,P["blue_dark"]); r(11,9,2,4,b); r(3,19,7,2,c); r(14,19,7,2,c)
        if name == "spy": r(6,2,12,3,c)
    elif name == "nation":
        p([(2,8),(12,1),(22,8)], b); r(3,8,18,3,a)
        for x in (4,10,16): r(x,12,3,7,a)
        r(2,20,20,3,b)
    elif name in ("end-turn", "move"):
        r(2,10,13,4, P["green"] if name == "end-turn" else P["blue"])
        p([(13,4),(22,12),(13,20)], P["green"] if name == "end-turn" else P["blue"])
        if name == "end-turn": r(22,4,2,16,b)
    elif name == "attack":
        p([(4,2),(7,2),(20,15),(17,18)],a); p([(17,2),(21,2),(8,18),(5,15)],b)
        p([(2,15),(5,12),(12,19),(9,22)],c); p([(13,18),(16,15),(22,21),(19,24)],c)
    elif name == "retreat":
        p([(2,11),(10,3),(10,8),(21,8),(21,19),(17,19),(17,12),(10,12),(10,17)],P["blue"])
    elif name == "disband":
        p([(5,3),(19,3),(19,12),(12,20),(5,12)],P["muted"])
        p([(2,19),(19,2),(22,5),(5,22)],P["red"])
    elif name == "letter":
        r(2,5,20,15,c); r(3,6,18,12,a)
        p([(3,6),(12,13),(21,6),(18,6),(12,10),(6,6)],c)
        r(10,12,4,4,P["red_dark"])
    elif name in ("map", "buy-map"):
        p([(2,5),(8,2),(16,5),(22,2),(22,19),(16,22),(8,19),(2,22)], a)
        r(7,4,2,14,c); r(15,6,2,14,c)
        p([(3,15),(5,11),(7,14)], P["green"]); p([(10,13),(12,8),(15,13)],P["green"])
        if name == "buy-map": r(17,15,7,7,b); r(19,17,3,3,c)
    elif name in ("report", "snapshot"):
        sheet()
        if name == "snapshot": r(12,12,9,9,P["blue_dark"]); r(16,13,1,5,a); r(16,17,3,1,a)
    elif name == "gift":
        r(4,10,16,12,b); r(2,7,20,5,a); r(10,7,4,15,c)
        p([(12,7),(3,5),(4,1),(9,2),(12,7),(15,2),(20,1),(21,5)], b)
    elif name == "vote":
        r(3,12,18,10,c); r(6,10,12,3,a); r(7,2,10,10,a); r(10,5,4,2,P["green"])
    elif name == "live":
        for x,y,w,h in ((10,1,4,4),(10,19,4,4),(1,10,4,4),(19,10,4,4),(7,7,10,10)): r(x,y,w,h,b)
    elif name == "unknown":
        r(6,3,12,3,a); r(15,6,3,5,a); r(10,9,8,3,a); r(10,12,3,4,a); r(10,19,3,3,b)
    elif name == "close":
        p([(4,2),(22,20),(20,22),(2,4)],a); p([(20,2),(22,4),(4,22),(2,20)],a)
    elif name == "check":
        p([(2,11),(5,8),(10,13),(19,3),(22,6),(10,20)],P["green"])
    elif name == "pin":
        p([(7,2),(17,2),(21,6),(21,12),(12,23),(3,12),(3,6)],b); r(9,6,6,6,P["panel"])
    elif name == "clock":
        r(5,2,14,2,a); r(5,20,14,2,a); r(2,5,2,14,a); r(20,5,2,14,a)
        r(11,6,2,7,b); r(11,11,6,2,b)
    else: raise ValueError(name)
    return "".join(parts)


def chrome(state, panel=False):
    fill = {"normal":P["panel"], "hover":P["raised"], "pressed":"#142723",
            "selected":"#30463c", "disabled":"#26312e", "danger":"#432d2a"}[state]
    edge = {"hover":P["gold"], "selected":P["gold"], "danger":P["red_dark"],
            "disabled":"#4c5850"}.get(state,P["line"])
    return rect(0,0,48,48,P["shade"]) + rect(1,1,46,46,edge) + rect(3,3,42,42,fill) + rect(4,4,40,1,P["muted"] if state=="hover" else P["line"])


def ring(attack=False, uncertain=False):
    color = P["red"] if attack else P["blue"]
    dark = P["red_dark"] if attack else P["blue_dark"]
    body=[]
    for y in range(64):
        for x in range(64):
            d=math.hypot(x-31.5,y-31.5)
            if 25 <= d < 28:
                segment=int((math.atan2(y-31.5,x-31.5)+math.pi)*16/math.tau)
                if not uncertain or segment%2==0: body.append(rect(x,y,1,1,color if d<27 else dark))
    if attack:
        for x,y,w,h in ((30,0,4,12),(30,52,4,12),(0,30,12,4),(52,30,12,4)):
            body.append(rect(x,y,w,h,color))
        body.append(rect(29,29,6,6,color))
    return "".join(body)


ASSETS = {}


def add(name, title, size, body, category, **meta):
    ASSETS[name] = dict(id=name,title=title,size=list(size),category=category,
                        anchor=[size[0]//2,size[1]//2],body=body)
    ASSETS[name].update(meta)


def make_assets():
    titles = {"build":"建设", "army":"军队", "market":"市场", "diplomacy":"外交", "alliance":"联盟",
              "intelligence":"情报", "nation":"国家", "end-turn":"结束回合", "move":"移动", "attack":"攻击",
              "retreat":"撤退", "disband":"遣散", "letter":"信件", "map":"共享地图", "buy-map":"购买地图",
              "spy":"派遣间谍", "report":"经济报表", "snapshot":"历史快照", "gift":"赠礼", "vote":"联盟投票",
              "live":"实时视野", "unknown":"未知", "close":"关闭", "check":"确认", "pin":"定位", "clock":"采样时间"}
    for name,title in titles.items(): add("icon-"+name,title,(24,24),icon(name),"icon")
    for state in ("normal","hover","pressed","selected","disabled","danger"):
        add("button-"+state,"按钮 "+state,(48,48),chrome(state),"chrome",nine_patch=[4,4,4,4])
    add("panel","面板底板",(48,48),chrome("normal",True),"chrome",nine_patch=[4,4,4,4])
    for state in ("off","on","disabled"):
        body=box(1,1,18,18,border=P["line"] if state!="on" else P["gold"])
        if state=="on": body+=group(icon("check"),3,3,.6)
        if state=="disabled": body+=rect(5,9,10,2,P["muted"])
        add("checkbox-"+state,"军队复选 "+state,(20,20),body,"control")
    add("pending-badge","待办数量底板",(16,16),rect(0,2,16,12,P["red_dark"])+rect(2,0,12,16,P["red_dark"]),"control",text_safe=[3,3,10,10])
    for attack in (False,True):
        for uncertain in (False,True):
            name=("attack" if attack else "move")+("-uncertain" if uncertain else "-known")
            add("range-"+name,("攻击" if attack else "移动")+("尝试范围" if uncertain else "已知范围"),(64,64),ring(attack,uncertain),"range",tint="semantic; do not tint by nation")
    body=""
    for x,y,dx,dy in ((0,0,1,1),(64,0,-1,1),(0,64,1,-1),(64,64,-1,-1)):
        body+=rect(x if dx>0 else x-17,y if dy>0 else y-3,17,3,P["gold"])
        body+=rect(x if dx>0 else x-3,y if dy>0 else y-17,3,17,P["gold"])
    add("tile-selected","点选地块框",(64,64),body,"range")
    for name,color in (("own",P["blue"]),("enemy",P["red"]),("last-seen",P["muted"])):
        body=poly([(16,1),(30,15),(16,30),(2,15)],P["shade"])+poly([(16,3),(28,15),(16,27),(4,15)],color)
        body+=group(icon("army" if name!="last-seen" else "unknown"),8,7,.65)
        add("army-"+name,"军团标记 "+name,(32,32),body,"marker",anchor=[16,30],tint="replace blue cloth only" if name=="own" else "none")
    for name in ("map","letter","snapshot"):
        add("prop-"+name,"地图世界贴图 "+name,(32,32),group(icon(name),4,4),"prop")
    add("fog-unknown","未知区域不透明底纹",(16,16),rect(0,0,16,16,P["shade"])+rect(3,4,2,1,"#142427")+rect(11,12,2,1,"#142427"),"fog",anchor=[0,0],tileable=True)
    add("fog-memory","历史区域压暗覆盖层",(16,16),rect(0,0,16,16,P["shade"],fill_opacity="0.62"),"fog",anchor=[0,0],tileable=True,requires="Desaturate source separately; render saved snapshot only")
    for mask in range(16):
        body=[]
        for y in range(16):
            for x in range(16):
                depth=min([99]+([y] if mask&1 else [])+([15-x] if mask&2 else [])+([15-y] if mask&4 else [])+([x] if mask&8 else []))
                if depth<2 or depth<4 and (x+y)%2==0: body.append(rect(x,y,1,1,P["shade"]))
        add(f"fog-edge-{mask:02d}",f"未知邻格边缘 {mask}",(16,16),"".join(body),"fog-edge",anchor=[0,0],unknown_neighbor_mask=mask)


def asset(name,x,y,scale=1):
    return group(ASSETS[name]["body"],x,y,scale)


def world(name,x,y,scale=1):
    node=ET.fromstring((WORLD/(name+".svg")).read_text(encoding="utf-8"))
    body="".join(ET.tostring(child,encoding="unicode") for child in node if child.tag!=f"{{{NS}}}title")
    prefix=f"world-{name}-{x}-{y}-"
    body=re.sub(r'\bid="([^"]+)"',lambda m:f'id="{prefix}{m[1]}"',body)
    body=re.sub(r'url\(#([^\)]+)\)',lambda m:f'url(#{prefix}{m[1]})',body)
    return group(body,x,y,scale)


def button(x,y,w,label,name,selected=False,danger=False):
    fill=P["raised"] if selected else P["panel"]
    body=box(x,y,w,46,fill,P["red_dark"] if danger else P["gold"] if selected else P["line"])
    body+=asset("icon-"+name,x+10,y+11)
    body+=text(x+42,y+30,label,18)
    return body


def base_screen(active):
    body=rect(0,0,1440,900,P["bg"])
    body+=text(24,39,"战国",26,P["gold"])+text(106,37,"秦国 · 真人玩家",18)
    body+=text(476,37,"第 15 回合  /  轮到你执政",19)
    body+=text(996,36,"合成设计样板 · 未接入游戏",16,P["muted"])
    body+=box(16,66,1080,716)
    for row in range(6):
        for col in range(10):
            x,y=18+col*107,68+row*118
            if col>=7:
                body+=rect(x,y,107,118,P["shade"])
                for i in range(4): body+=rect(x+12+i*23,y+20+(col*13+row*7+i*19)%76,3,2,"#17282a")
                continue
            body+=rect(x,y,107,118,"#739357" if (col+row)%3 else "#6f8c54")
            for i in range(7): body+=rect(x+(i*23+row*17)%100,y+(i*17+col*11)%112,3,2,"#86a365")
            body+=rect(x+47,y,9,118,"#b4a075")+rect(x,y+86,107,8,"#b4a075")
            if (col+row)%3==0: body+=world("tree-oak-0",x+4,y+14)+world("tree-pine",x+68,y+40)
            if (col,row)==(2,2): body+=world("townhall",x+18,y+9)
            elif (col,row) in ((4,2),(5,3),(6,2)): body+=world("factory",x+17,y+15)
            elif (col+row)%4==0: body+=world("house-1",x+24,y+22)
            if col<4 and row in (2,3):
                for i in range(3):
                    px,py=x+22+i*26,y+83+i%2*10
                    body+=rect(px,py,3,3,"#e5c399")+rect(px-1,py+3,5,5,"#63868a")+rect(px,py+8,1,3,P["ink"])+rect(px+2,py+8,1,3,P["ink"])
            if col>=4: body+=rect(x,y,107,118,P["shade"],fill_opacity="0.62")
            body+=rect(x,y,107,1,"#475b47")+rect(x,y,1,118,"#475b47")
    body+=box(32,84,302,154)
    body+=text(48,111,"秦  /  国库",20,P["gold"])+text(48,140,"金币  1,320",18)+text(204,140,"粮食  86",18)
    body+=text(48,167,"木材  148",18)+text(204,167,"装备  12",18)+text(48,196,"上期生产 +18   军需 −6",16,P["green"])
    body+=text(48,219,"已结算第 14 回合 · 示例数据",13,P["muted"])
    body+=asset("army-own",371,457,1.5)
    body+=asset("prop-map",584,115,1.4)+text(566,180,"第 12 回合快照",16,P["paper"])
    body+=asset("icon-unknown",921,351,2)+text(891,427,"尚未探明",18,P["muted"])
    body+=asset("icon-live",34,749,.75)+text(58,765,"实时视野",15)
    body+=asset("icon-snapshot",190,749,.75)+text(214,765,"暗区：保留采样建筑，无人物活动",15)
    body+=asset("icon-unknown",594,749,.75)+text(618,765,"未知区：仍可尝试军令",15)
    names=[("建设","build"),("军队","army"),("市场","market"),("外交","diplomacy"),("联盟","alliance"),("情报","intelligence"),("国家","nation")]
    for i,(label,name) in enumerate(names):
        body+=button(16+i*145,810,135,label,name,selected=name==active)
        if name in ("diplomacy","alliance","intelligence"):
            body+=asset("pending-badge",122+i*145,816)+text(126+i*145,829,"2" if name=="diplomacy" else "1",11)
    body+=button(1194,810,230,"结束本国回合","end-turn")
    body+=text(20,886,"点地块查看  ·  面板勾选军队  ·  Esc 取消目标 / 菜单  ·  滚轮缩放",15,P["muted"])
    return body


def army_preview(attack=False):
    body=base_screen("army")
    body+=asset("tile-selected",18+3*107,68+3*118,107/64)
    for col,row in ((3,2),(4,2),(5,2),(4,3),(5,3),(6,3),(7,3),(4,4),(5,4),(6,4),(7,4)):
        if attack and col<4: continue
        uncertain=col>=4
        body+=asset("range-"+("attack" if attack else "move")+("-uncertain" if uncertain else "-known"),18+col*107+21,68+row*118+27)
    x=1112
    body+=box(x,66,312,716)+text(x+20,101,"地块驻军",24,P["gold"])
    body+=text(x+20,129,"河西  (3, 3)  ·  秦国",16,P["muted"])
    body+=text(x+20,164,"选择参与本次命令的军队",17)
    rows=[("秦军 07 · 骑兵","HP 18 / 20  ·  可以行动",True,False),
          ("秦军 12 · 骑兵","HP 20 / 20  ·  可以行动",True,False),
          ("秦军 09 · 步兵","HP 12 / 12  ·  该目标超出范围",True,True),
          ("秦军 16 · 步兵","新募，本回合无法行动",False,True)]
    for i,(title,sub,checked,disabled) in enumerate(rows):
        y=182+i*89
        body+=box(x+14,y,284,77,fill="#25322d" if disabled else P["raised"])
        body+=asset("checkbox-disabled" if not checked and disabled else "checkbox-on" if checked else "checkbox-off",x+26,y+17)
        body+=text(x+58,y+31,title,17,P["muted"] if disabled else P["paper"])
        body+=text(x+26,y+60,sub,14,P["muted"] if disabled else P["green"])
    body+=button(x+16,562,132,"移动","move",not attack)+button(x+164,562,132,"攻击","attack",attack,True)
    body+=text(x+20,635,"目标 (7, 3)  ·  未知区域",17)
    body+=text(x+20,664,"参与 2 / 已选 3 支",18,P["gold"])
    body+=text(x+20,691,"骑兵参与，步兵保留勾选",15,P["muted"])
    body+=text(x+20,726,"虚线：路线或目标尚未确认",15,P["red"] if attack else P["blue"])
    body+=text(x+20,753,"点目标格提交；模式不会自动互换",14,P["muted"])
    return svg(1440,900,body,"真人军务合成样板 / "+("攻击" if attack else "移动"))


def intelligence_preview():
    body=base_screen("intelligence")
    x=1112
    body+=box(x,66,312,716)+text(x+20,102,"情报",24,P["gold"])
    for i,(label,name) in enumerate((("购买地图","buy-map"),("派遣间谍","spy"),("经济报表","report"),("共享地图","map"))):
        body+=button(x+16,127+i*57,280,label,name)
    body+=text(x+20,388,"已取得的地图",21,P["gold"])
    body+=box(x+16,410,280,238,P["raised"])
    body+=asset("prop-map",x+30,430,1.6)+text(x+94,456,"齐国地图",21)
    body+=text(x+32,500,"采样：第 12 回合",17)+text(x+32,532,"取得：第 15 回合",17)
    body+=text(x+32,566,"暗区建筑停留在采样时点",15,P["muted"])
    body+=button(x+30,585,250,"定位世界地图","pin",True)
    body+=text(x+20,687,"新地图自动合入世界地图",17,P["green"])
    body+=text(x+20,720,"旧快照不随后台建设、易帜更新",14,P["muted"])
    body+=text(x+20,749,"所有价格、回合与数量均为示意",14,P["muted"])
    return svg(1440,900,body,"情报和地图快照合成样板")


def alliance_preview():
    body=base_screen("alliance")
    body+=box(236,254,954,482,P["panel"],P["gold"])
    body+=asset("icon-diplomacy",262,276,1.5)+text(308,306,"外交与联盟",25,P["gold"])
    body+=text(268,342,"独立入口、列表选目标、待办直接处理；下列状态均为布局示例。",16,P["muted"])
    body+=box(258,365,206,348,P["raised"])+text(278,398,"列国",20)
    for i,name in enumerate(("秦 · 本国","齐 · 和平","赵 · 已交战","楚 · 共同防御")):
        body+=box(272,417+i*63,178,48,P["panel"],P["gold"] if i==1 else P["line"])+text(286,449+i*63,name,16)
    body+=text(490,398,"齐国 · 外交",21,P["gold"])
    for i,(label,name) in enumerate((("写信","letter"),("赠送物资","gift"),("宣战","attack"),("共同防御","diplomacy"))):
        body+=button(490,420+i*62,256,label,name,danger=name=="attack")
    body+=text(778,398,"联盟 · 合纵盟",21,P["gold"])
    body+=text(778,440,"盟主：秦",16)+text(896,440,"成员：秦、楚",16)
    body+=button(778,464,382,"处理入盟申请  1","alliance",True)
    body+=button(778,526,184,"联盟投票","vote")+button(976,526,184,"移交盟主","alliance")
    body+=button(778,588,184,"联盟改名","alliance")+button(976,588,184,"解散联盟","disband",danger=True)
    body+=text(490,695,"执行前展示费用与生效时机，结果以正式规则回执为准。",16,P["muted"])
    return svg(1440,900,body,"外交与联盟按钮合成样板")


def main():
    make_assets()
    for folder in (ART/"svg",ART/"png",PREVIEW): folder.mkdir(parents=True,exist_ok=True)
    raster=Rasterizer(); atlas=Image.new("RGBA",(1024,1024)); entries=[]; x=y=2; row_h=0
    checks=[]
    for name,item in ASSETS.items():
        w,h=item["size"]
        document=svg(w,h,item["body"],item["title"])
        ET.fromstring(document)
        assert "<image" not in document and "href=" not in document and "<script" not in document
        image=raster.render(document)
        assert image.size==(w,h)
        assert image.getbbox() is not None or name=="fog-edge-00"
        alpha=set(image.getchannel("A").tobytes())
        if name!="fog-memory": assert alpha <= {0,255}, (name,alpha)
        if name=="fog-unknown": assert alpha=={255}
        if name=="fog-memory": assert all(0<a<255 for a in alpha)
        if x+w+2>1024: x=2; y+=row_h+4; row_h=0
        assert y+h+2<=1024
        atlas.alpha_composite(image,(x,y))
        svg_path=ART/"svg"/(name+".svg"); png_path=ART/"png"/(name+".png")
        svg_path.write_text(document,encoding="utf-8"); image.save(png_path)
        entry={key:value for key,value in item.items() if key!="body"}
        entry.update(svg="svg/"+name+".svg",png="png/"+name+".png",atlas_rect=[x,y,w,h],
                     svg_sha256=hashlib.sha256(svg_path.read_bytes()).hexdigest(),png_sha256=hashlib.sha256(png_path.read_bytes()).hexdigest())
        entries.append(entry); checks.append(dict(id=name,alpha=sorted(alpha),size=[w,h]))
        x+=w+4; row_h=max(row_h,h)
    atlas.save(ART/"atlas.png")
    for entry in entries:
        x,y,w,h=entry["atlas_rect"]
        assert atlas.crop((x,y,x+w,y+h)).tobytes()==Image.open(ART/entry["png"]).convert("RGBA").tobytes()
    documents={"military-move":army_preview(),"military-attack":army_preview(True),
               "intelligence-map":intelligence_preview(),"diplomacy-alliance":alliance_preview()}
    catalog=rect(0,0,1200,((len(entries)+7)//8)*130+100,P["bg"])+text(24,38,"战国 · 真人 GUI 补充素材",25,P["gold"])+text(24,72,"SVG 母版 / 最近邻纹理 / 未接入游戏",16,P["muted"])
    for i,e in enumerate(entries):
        x=16+(i%8)*148; y=100+(i//8)*130
        catalog+=box(x,y,140,120,P["raised"])
        scale=min(2,72/max(e["size"]))
        catalog+=asset(e["id"],x+(140-e["size"][0]*scale)/2,y+10,scale)
        lines=wrap_label(e["title"])
        for j,line in enumerate(lines): catalog+=text(x+7,y+97-(len(lines)-1-j)*14,line,13)
        catalog+=text(x+7,y+114,e["id"],9,P["muted"])
    documents["catalog"]=svg(1200,((len(entries)+7)//8)*130+100,catalog,"真人 GUI 素材图录")
    for name,document in documents.items():
        node=ET.fromstring(document)
        ids=[n.attrib["id"] for n in node.iter() if "id" in n.attrib]
        assert len(ids)==len(set(ids)), f"Duplicate SVG IDs in {name}"
        (PREVIEW/(name+".svg")).write_text(document,encoding="utf-8")
        raster.render(document).save(PREVIEW/(name+".png"))
    manifest=dict(format="zhanguo.human-ui-art/1",status="design-assets-not-connected",license="MPL-2.0",
                  authored="Original SVG shapes; previews reuse repository unified-v1 SVGs",filter="nearest",palette=P,
                  atlas=dict(file="atlas.png",size=[1024,1024],padding=2,sha256=hashlib.sha256((ART/"atlas.png").read_bytes()).hexdigest()),
                  fog_mask_bits=dict(north=1,east=2,south=4,west=8),assets=entries,
                  preview={name:dict(svg=name+".svg",png=name+".png",synthetic=True) for name in documents})
    (ART/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    validation=dict(renderer=raster.backend,assets=len(entries),xml_valid=True,self_contained=True,
                    binary_alpha_except_memory=True,unknown_opaque=True,memory_translucent=True,atlas_roundtrip=True,
                    synthetic_previews=len(documents),preview_unique_ids=True,checks=checks)
    (ART/"validation.json").write_text(json.dumps(validation,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    labels={"military-move":"移动与地块选军","military-attack":"攻击与迷雾尝试","intelligence-map":"情报与地图快照","diplomacy-alliance":"外交与联盟","catalog":"素材图录"}
    html_body='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>战国 · 真人 GUI 素材</title><style>body{margin:0;background:#111f20;color:#e5dfbd;font:16px system-ui}header,nav,p{margin:20px 24px}h1{font-size:25px;color:#d3b965}button,a{background:#293e37;color:#e5dfbd;border:1px solid #61735a;padding:10px;cursor:pointer;margin:4px}button[aria-pressed=true]{border-color:#d3b965}img{display:block;width:min(1440px,100%);height:auto;margin:auto;image-rendering:pixelated}a{display:inline-block}small{color:#a7b39b}</style><header><h1>战国 · 真人 GUI 与情报地图</h1><small>合成设计样板，所有军队、国库、回合和外交状态均为示意；尚未接入游戏。</small></header><nav>'''
    for name,label in labels.items(): html_body+=f'<button data-page="{name}" aria-pressed="{str(name=="military-move").lower()}">{label}</button>'
    html_body+='</nav><img id="preview" src="military-move.png" alt="移动与地块选军合成样板"><p><a id="source" href="military-move.svg">查看 / 保存 SVG 母版</a></p><script>for(const b of document.querySelectorAll("button")){b.onclick=()=>{for(const n of document.querySelectorAll("button"))n.setAttribute("aria-pressed",n===b);document.querySelector("img").src=b.dataset.page+".png";document.querySelector("img").alt=b.textContent+"合成样板";document.querySelector("#source").href=b.dataset.page+".svg"}}</script></html>'
    (PREVIEW/"index.html").write_text(html_body,encoding="utf-8")
    print(json.dumps({k:v for k,v in validation.items() if k!="checks"},ensure_ascii=False))


if __name__=="__main__": main()
