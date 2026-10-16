"""Text and vector views, sharing the public observation snapshot with PNG."""
from .i18n import Translator
from .maps import snapshot,render_svg

TERRAIN={'native:plain':'.','native:forest':'f','native:hill':'^','native:mountain':'M','native:desert':'~'}


def player_map(observation,text=None):
    text=text or Translator();tiles=observation['facts'].get('view',[])
    if not tiles:return text('map.empty')
    points={tuple(t['position']):t for t in tiles}
    lo_x=min(x for x,y in points);hi_x=max(x for x,y in points)
    lo_y=min(y for x,y in points);hi_y=max(y for x,y in points)
    return '\n'.join(''.join(TERRAIN.get(points[(x,y)]['terrain'],'?') if (x,y) in points else ' ' for x in range(lo_x,hi_x+1)) for y in range(lo_y,hi_y+1))


def player_svg(observation,text=None):
    return render_svg(snapshot(observation))


def observer_map(state,text=None):
    text=text or Translator();obs=state['map'];names=state['names']
    owners=list(names);symbols={a:chr(65+i) if i<26 else str(i) for i,a in enumerate(owners)}
    points={tuple(t['position']):t for t in obs['facts']['view']}
    if not points:return text('map.empty')
    terrain={'native:plain':'p','native:forest':'f','native:hill':'h','native:mountain':'m','native:desert':'d'}
    xmax=max(x for x,y in points);ymax=max(y for x,y in points)
    lines=[text('console.world_map',period=state['period']), '   '+''.join(str(x%10) for x in range(xmax+1))]
    for y in range(ymax+1):
        lines.append(f'{y:2} '+''.join(symbols.get(points[x,y]['owner'],terrain.get(points[x,y]['terrain'],'?')) for x in range(xmax+1)))
    legend=' '.join(f'{symbols[a]}={names[a]}' for a in owners)
    lines.append(text('console.world_legend',owners=legend))
    return '\n'.join(lines)


def player_atlas(observation, text, key=lambda value:value):
    """坐标地图：视野内逐格一行，**按势力分段**（我 → 无主 → 各国）。

    格式沿用 0.1：`(x,y)归属地形，[L2城][，厅×N][，地名][，番号…]`——番号垫在最后、
    不加标签（有数字的就是番号）。行自带语义、零解码；分段让"谁的地盘"一眼分块。
    单格全明细仍走 tile，本国明细仍走 land；网格图走 view 的原样数据。
    """
    facts=observation['facts'];actor=observation['actor']
    rows=facts.get('view') or []
    if not rows:return text('map.empty')
    names={row['id']:row['name'] for row in (facts.get('nations') or []) if isinstance(row,dict)}
    # 自家格子打国名（秦），不用「我」：地图上每个势力都用名字，一眼对齐。
    us=names.get(actor,actor)
    army={}
    for unit in facts.get('army') or []:
        if unit.get('hp',1)<=0:continue
        army.setdefault((unit['x'],unit['y']),[]).append(unit)
    terrain=lambda value:text.resolve('atlas.terrain.'+value,key(value))
    glyph={'native:infantry':'步','native:cavalry':'骑','native:militia':'民'}

    def line(row):
        x,y=row['position'];owner=row['owner']
        who=text('atlas.unowned') if owner is None else names.get(owner,owner)
        out=[f'({x},{y}){who}{terrain(row["terrain"])}']
        kinds=dict(row.get('buildings') or ())
        castle=kinds.get('native:castle',0)
        if castle:out.append(f'L{castle}城')
        halls=kinds.get('native:town-hall',0)
        if halls:out.append(f'厅×{halls}')
        if row.get('name'):out.append(row['name'])
        here=army.get((x,y),())
        if here:
            tags=[]
            for unit in sorted(here,key=lambda u:(0 if u.get('owner')==owner else 1,0 if u.get('owner')==actor else 1,u.get('id',0))):
                owner_of=unit.get('owner')
                if owner_of=='野人':tags.append('野人');continue
                prefix='' if owner_of==owner else names.get(owner_of,owner_of)
                tags.append(f"{prefix}{glyph.get(unit.get('kind'),key(unit.get('kind',''))[:1])}{unit.get('id','')}")
            out.append('、'.join(tags))
        return '，'.join(out)

    buckets={}
    for row in rows:
        owner=row['owner']
        buckets.setdefault(actor if owner==actor else '野人' if owner is None else owner,[]).append(row)
    order=[k for k in (actor,'野人') if k in buckets]
    order+=sorted((k for k in buckets if k not in (actor,'野人')))
    parts=[f'【{names.get(k,k)}】\n'+'\n'.join(line(r) for r in sorted(buckets[k],key=lambda r:r['position']))
           for k in order]
    own=sum(1 for row in rows if row['owner']==actor)
    parts.append(text('atlas.footer',own=own,seen=len(rows),other=len(rows)-own))
    return '\n\n'.join(parts)
