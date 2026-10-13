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
