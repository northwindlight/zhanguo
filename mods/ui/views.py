"""Text and vector views, sharing the public observation snapshot with PNG."""
from .i18n import Translator
from .maps import snapshot,render_svg

TERRAIN={'native:plain':'·','native:forest':'♣','native:hill':'^','native:mountain':'▲','native:desert':'~'}


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
