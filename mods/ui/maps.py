"""Deterministic PNG/SVG views of player-visible facts. No World access."""
from copy import deepcopy
import base64
import hashlib
import colorsys
import io

COLORS = ('#dab942','#3aaeb8','#bc54aa','#c1cad8','#e28b3c','#745eb8','#e597b5','#5e91cd')
TERRAIN = {'native:plain':'P','native:forest':'F','native:hill':'H','native:mountain':'M','native:desert':'D'}
SIDES = {'self':'#3ac460','ally':'#3a84e8','enemy':'#e03e3a','neutral':'#8e908e'}


def owner_color(owner):
    digest=hashlib.sha256(owner.encode()).digest()
    hue=int.from_bytes(digest[:4],'big')/2**32
    rgb=colorsys.hsv_to_rgb(hue,.40+digest[4]/255*.20,.72+digest[5]/255*.15)
    return '#'+''.join(f'{round(c*255):02x}' for c in rgb)


def snapshot(observation):
    facts=observation['facts'];tiles=deepcopy(facts.get('view',[]))
    visible={tuple(t['position']) for t in tiles}
    armies=[deepcopy(a) for a in facts.get('army',[]) if (a['x'],a['y']) in visible]
    diplomacy=facts.get('diplomacy',{})
    return {'version':'zhanguo.ui.map/1','actor':observation['actor'],'period':observation['period'],
            'tiles':tiles,'armies':armies,'alliances':deepcopy(diplomacy.get('alliances',[])),
            'wars':deepcopy(diplomacy.get('wars',[]))}


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
    if type(max_px) is not int or not 128<=max_px<=4096:raise ValueError('map_max_px must be between 128 and 4096')
    tiles=state['tiles']
    if not tiles:return None
    points={tuple(t['position']):t for t in tiles}
    xlo=min(x for x,y in points);xhi=max(x for x,y in points)
    ylo=min(y for x,y in points);yhi=max(y for x,y in points)
    width,height=xhi-xlo+1,yhi-ylo+1
    cell=max(1,min(96,(max_px-48)//max(width,height)))
    image=Image.new('RGB',(width*cell+48,height*cell+48),'#121923');draw=ImageDraw.Draw(image)
    font=ImageFont.load_default(size=max(7,min(14,cell//5)))
    armies={}
    for army in state['armies']:
        if army['hp']>0:armies.setdefault((army['x'],army['y']),[]).append(army)
    for (x,y),tile in sorted(points.items()):
        left,top=24+(x-xlo)*cell,24+(y-ylo)*cell
        color=owner_color(tile['owner']) if tile['owner'] else '#455044'
        draw.rectangle((left,top,left+cell-1,top+cell-1),fill=color,outline='#222b33')
        if cell<28:continue
        buildings=dict(tile['buildings']);label=TERRAIN.get(tile['terrain'],'?')
        if buildings.get('native:town-hall'):label+=' T'
        if buildings.get('native:castle'):label+=' C'+str(buildings['native:castle'])
        draw.text((left+2,top+cell-16),label,font=font,fill='#111111')
        groups={}
        for army in armies.get((x,y),[]):groups.setdefault(side(state,army['owner']),[]).append(army)
        for i,(kind,units) in enumerate(sorted(groups.items())):
            row,col=divmod(i,2);bx,by=left+3+col*cell//2,top+3+row*cell//3
            draw.rectangle((bx,by,bx+cell//2-6,by+cell//3-4),fill='#ffffff',outline=SIDES[kind],width=3)
            label=f'{len(units)}/{sum(a["hp"] for a in units):g}'
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
        try:png=render_snapshot(payload['facts'],max_px=payload['max_px'])
        except ImportError:pass
    text=str(result.get('content') or '')
    result['content']=([{'type':'text','text':text},image_block(png)] if png else text+'\n'+payload.get('fallback',''))
    return result


def render_svg(state):
    import html
    if state.get('version')!='zhanguo.ui.map/1':raise ValueError('Unsupported UI map snapshot')
    tiles=state['tiles'];points={tuple(t['position']):t for t in tiles}
    xlo=min((x for x,y in points),default=0);xhi=max((x for x,y in points),default=0)
    ylo=min((y for x,y in points),default=0);yhi=max((y for x,y in points),default=0)
    cell=96;width=(xhi-xlo+1)*cell+48;height=(yhi-ylo+1)*cell+48
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',f'<rect width="{width}" height="{height}" fill="#121923"/>']
    def text(x,y,value,color='#eeeeee'):
        return f'<text x="{x}" y="{y}" fill="{color}" font-family="monospace" font-size="12">{html.escape(str(value))}</text>'
    for (x,y),tile in sorted(points.items()):
        left,top=24+(x-xlo)*cell,24+(y-ylo)*cell
        color=owner_color(tile['owner']) if tile['owner'] else '#455044'
        label=html.escape(f'{x},{y} {tile["owner"] or ""}',quote=True)
        parts.append(f'<rect x="{left}" y="{top}" width="{cell}" height="{cell}" fill="{color}" stroke="#222b33"><title>{label}</title></rect>')
        buildings=dict(tile['buildings']);marks=TERRAIN.get(tile['terrain'],'?')
        if buildings.get('native:town-hall'):marks+=' T'
        if buildings.get('native:castle'):marks+=' C'+str(buildings['native:castle'])
        parts.append(text(left+3,top+cell-3,marks,'#111111'))
        groups={}
        for army in state['armies']:
            if (army['x'],army['y'])==(x,y) and army['hp']>0:groups.setdefault(side(state,army['owner']),[]).append(army)
        for i,(kind,units) in enumerate(sorted(groups.items())):
            row,col=divmod(i,2);bx,by=left+3+col*cell//2,top+3+row*cell//3
            parts.append(f'<rect x="{bx}" y="{by}" width="42" height="28" fill="white" stroke="{SIDES[kind]}" stroke-width="3"/>')
            parts.append(text(bx+3,by+17,f'{len(units)}/{sum(a["hp"] for a in units):g}','#111111'))
    for x in range(xlo,xhi+1):parts.append(text(24+(x-xlo)*cell+cell//2,16,x))
    for y in range(ylo,yhi+1):parts.append(text(2,24+(y-ylo)*cell+cell//2,y))
    parts.append('</svg>');return ''.join(parts)
