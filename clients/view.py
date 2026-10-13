"""Presentation over public player observations; no domain implementation imports."""
import html

TERRAIN={'native:plain':'·','native:forest':'♣','native:hill':'^','native:mountain':'▲','native:desert':'~'}


def player_map(observation):
    tiles=observation['facts']['view']
    if not tiles:return '暂无可见地图'
    points={tuple(t['position']):t for t in tiles}
    lo_x=min(x for x,y in points);hi_x=max(x for x,y in points)
    lo_y=min(y for x,y in points);hi_y=max(y for x,y in points)
    return '\n'.join(''.join(TERRAIN.get(points[(x,y)]['terrain'],'?') if (x,y) in points else ' ' for x in range(lo_x,hi_x+1)) for y in range(lo_y,hi_y+1))


def player_svg(observation):
    tiles=observation['facts']['view'];parts=['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="960" viewBox="0 0 960 960">']
    colors={'native:plain':'#d6d2a7','native:forest':'#4f8957','native:hill':'#b7a278','native:mountain':'#777777','native:desert':'#e1c078'}
    size=max([1,*[max(t['position'])+1 for t in tiles]]);scale=960/size
    for tile in tiles:
        x,y=tile['position'];label=html.escape(str(tile['owner'] or '无主'),quote=True)
        parts.append(f'<rect x="{x*scale}" y="{y*scale}" width="{scale}" height="{scale}" fill="{colors.get(tile["terrain"],"#aaaaaa")}"><title>{x},{y} {label}</title></rect>')
    parts.append('</svg>');return ''.join(parts)
