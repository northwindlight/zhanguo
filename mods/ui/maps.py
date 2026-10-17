"""Deterministic PNG/SVG views of player-visible facts. No World access."""
from copy import deepcopy
from collections import Counter
import base64
import hashlib
import colorsys
import io

from clients.context import map_presentation

from .facts import flatten

COLORS = ('#dab942','#3aaeb8','#bc54aa','#c1cad8','#e28b3c','#745eb8','#e597b5','#5e91cd')
SIDES = {'self':'#3ac460','ally':'#3a84e8','enemy':'#e03e3a','neutral':'#8e908e'}
# 图幅夹取范围（0.1 的 map_max_px 就是夹到这一段，越界不报错只夹）。
MAP_MIN_PX, MAP_MAX_PX = 240, 1800


def owner_color(owner):
    digest=hashlib.sha256(owner.encode()).digest()
    hue=int.from_bytes(digest[:4],'big')/2**32
    rgb=colorsys.hsv_to_rgb(hue,.40+digest[4]/255*.20,.72+digest[5]/255*.15)
    return '#'+''.join(f'{round(c*255):02x}' for c in rgb)


def snapshot(observation):
    facts=flatten(observation['facts']);tiles=deepcopy(facts.get('view',[]))
    visible={tuple(t['position']) for t in tiles}
    armies=[deepcopy(a) for a in facts.get('army',[]) if (a['x'],a['y']) in visible]
    diplomacy=facts.get('diplomacy',{});glyphs,role_kinds=map_presentation(facts)
    glyphs=facts.get('glyphs') or glyphs;role_kinds=facts.get('role_kinds') or role_kinds
    return {'version':'zhanguo.ui.map/1','actor':observation['actor'],'period':observation['period'],
            'tiles':tiles,'armies':armies,'alliances':deepcopy(diplomacy.get('alliances',[])),
            'wars':deepcopy(diplomacy.get('wars',[])),'glyphs':glyphs,'role_kinds':role_kinds}


def side(state,owner):
    actor=state['actor']
    if owner==actor:return 'self'
    for war in state['wars']:
        if (actor in war['attackers'] and owner in war['defenders']) or (actor in war['defenders'] and owner in war['attackers']):return 'enemy'
    if any(actor in a['members'] and owner in a['members'] for a in state['alliances']):return 'ally'
    return 'neutral'


def render_snapshot(state, *, max_px=960):
    from PIL import Image, ImageDraw, ImageFont
    if state.get('version')!='zhanguo.ui.map/1':raise ValueError('Unsupported UI map snapshot')
    # 图幅是观感参数，不是玩家动作：越界夹取即可，不该把整局拖下水。
    if type(max_px) is not int or isinstance(max_px,bool):max_px=960
    max_px=max(MAP_MIN_PX,min(MAP_MAX_PX,max_px))
    tiles=state['tiles']
    if not tiles:return None
    points={tuple(t['position']):t for t in tiles}
    xlo=min(x for x,y in points);xhi=max(x for x,y in points)
    ylo=min(y for x,y in points);yhi=max(y for x,y in points)
    width,height=xhi-xlo+1,yhi-ylo+1
    cell=max(1,min(96,(max_px-48)//max(width,height)))
    image=Image.new('RGB',(width*cell+48,height*cell+48),'#121923');draw=ImageDraw.Draw(image)
    font=ImageFont.load_default(size=max(7,min(14,cell//5)))
    owners=sorted({t['owner'] for t in tiles if t['owner']});symbols={owner:chr(65+i) for i,owner in enumerate(owners)}
    glyphs=state.get('glyphs') or {};roles=state.get('role_kinds') or {}
    capitals=roles.get('capital') or [];castles=roles.get('castle') or []
    armies={}
    for army in state['armies']:
        if army['hp']>0:armies.setdefault((army['x'],army['y']),[]).append(army)
    for (x,y),tile in sorted(points.items()):
        left,top=24+(x-xlo)*cell,24+(y-ylo)*cell
        color=owner_color(tile['owner']) if tile['owner'] else '#455044'
        draw.rectangle((left,top,left+cell-1,top+cell-1),fill=color,outline='#222b33')
        buildings=dict(tile['buildings']);label=symbols.get(tile['owner'],'-')+' '+glyphs.get(tile['terrain'],'?')
        if any(buildings.get(kind) for kind in capitals):label+=' T'
        towers=sum(buildings.get(kind,0) for kind in castles)
        if towers:label+=' C'+str(towers)
        draw.text((left+1,top+max(0,cell-16)),label,font=font,fill='#111111')
        groups={}
        for army in armies.get((x,y),[]):groups.setdefault(side(state,army['owner']),[]).append(army)
        for i,(kind,units) in enumerate(sorted(groups.items())):
            row,col=divmod(i,2);bx,by=left+3+col*cell//2,top+3+row*cell//3
            if cell>=28:draw.rectangle((bx,by,bx+cell//2-6,by+cell//3-4),fill='#ffffff',outline=SIDES[kind],width=3)
            label=' '.join(glyphs.get(k,k.split(':')[-1][:1].upper())+str(n) for k,n in sorted(Counter(a['kind'] for a in units).items()))
            draw.text((bx+3,by+3),label,font=font,fill='#111111')
    for x in range(xlo,xhi+1):draw.text((24+(x-xlo)*cell+cell//2,4),str(x),font=font,fill='#eeeeee',anchor='mt')
    for y in range(ylo,yhi+1):draw.text((3,24+(y-ylo)*cell+cell//2),str(y),font=font,fill='#eeeeee',anchor='lm')
    buffer=io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue()


def image_block(png):
    return {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png).decode('ascii')}}


def expand_message(message, *, vision=True):
    from ui import MAP_KEY
    payload=message.get(MAP_KEY)
    if not payload:return message
    result=deepcopy({k:v for k,v in message.items() if k!=MAP_KEY})
    png=None
    if vision:
        # 渲染是锦上添花：装不上 Pillow、图幅离谱、画到一半炸了，都退回文本地图。
        try:png=render_snapshot(payload['facts'],max_px=payload['max_px'])
        except Exception:png=None
    text=str(result.get('content') or '')
    result['content']=([{'type':'text','text':text},image_block(png)] if png else text+'\n'+payload.get('fallback',''))
    return result


def render_svg(state):
    import html
    if state.get('version')!='zhanguo.ui.map/1':raise ValueError('Unsupported UI map snapshot')
    tiles=state['tiles'];points={tuple(t['position']):t for t in tiles}
    xlo=min((x for x,y in points),default=0);xhi=max((x for x,y in points),default=0)
    ylo=min((y for x,y in points),default=0);yhi=max((y for x,y in points),default=0)
    glyphs=state.get('glyphs') or {};roles=state.get('role_kinds') or {}
    capitals=roles.get('capital') or [];castles=roles.get('castle') or []
    cell=96;width=(xhi-xlo+1)*cell+48;height=(yhi-ylo+1)*cell+48
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',f'<rect width="{width}" height="{height}" fill="#121923"/>']
    def text(x,y,value,color='#eeeeee'):
        return f'<text x="{x}" y="{y}" fill="{color}" font-family="monospace" font-size="12">{html.escape(str(value))}</text>'
    for (x,y),tile in sorted(points.items()):
        left,top=24+(x-xlo)*cell,24+(y-ylo)*cell
        color=owner_color(tile['owner']) if tile['owner'] else '#455044'
        label=html.escape(f'{x},{y} {tile["owner"] or ""}',quote=True)
        parts.append(f'<rect x="{left}" y="{top}" width="{cell}" height="{cell}" fill="{color}" stroke="#222b33"><title>{label}</title></rect>')
        buildings=dict(tile['buildings']);marks=glyphs.get(tile['terrain'],'?')
        if any(buildings.get(kind) for kind in capitals):marks+=' T'
        towers=sum(buildings.get(kind,0) for kind in castles)
        if towers:marks+=' C'+str(towers)
        parts.append(text(left+3,top+cell-3,marks,'#111111'))
        groups={}
        for army in state['armies']:
            if (army['x'],army['y'])==(x,y) and army['hp']>0:groups.setdefault(side(state,army['owner']),[]).append(army)
        for i,(kind,units) in enumerate(sorted(groups.items())):
            row,col=divmod(i,2);bx,by=left+3+col*cell//2,top+3+row*cell//3
            parts.append(f'<rect x="{bx}" y="{by}" width="42" height="28" fill="white" stroke="{SIDES[kind]}" stroke-width="3"/>')
            parts.append(text(bx+3,by+17,' '.join(glyphs.get(k,k.split(':')[-1][:1].upper())+str(n) for k,n in sorted(Counter(a['kind'] for a in units).items())),'#111111'))
    for x in range(xlo,xhi+1):parts.append(text(24+(x-xlo)*cell+cell//2,16,x))
    for y in range(ylo,yhi+1):parts.append(text(2,24+(y-ylo)*cell+cell//2,y))
    parts.append('</svg>');return ''.join(parts)
