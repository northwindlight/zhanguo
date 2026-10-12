from __future__ import annotations
from ..core import *
from ..rules import *
from ..ui_constants import *
from .. import presentation as P
import copy,json,re,threading
from pathlib import Path
from ctx import est_tokens
from console import dw as _dw, pad as _pad


def _visible_cells(world, name) -> set:
    """name 看得见的全部格（自己/盟友的地 + 其相邻一圈 + 己方与盟友瞭望塔圆内）。

    ★ 与 `World.visible_to` **同口径**，但**从地块侧反推**而不是逐格去问：
    `visible_to` 每次调用都要遍历全表找瞭望塔，按格问 60×60 就是 3600 次 × 全表 ——
    实测 29ms（地块越多越慢），反推只要 0.1ms（**280 倍**）。
    `tests/test_map_panel.py::test_visible_cells_matches_visible_to` 逐格钉住两者等价。
    """
    bloc = world.bloc_of(name)
    allies = set(bloc['members']) if bloc is not None else {name}
    out: set = set()
    for (x, y), t in world.tiles.items():
        if t['owner'] in allies:
            out.add((x, y))
            out.update(world.neighbors(x, y))
    r = int(building_effect('瞭望塔', 'vision_radius') or 0)
    if r > 0:
        for (x, y), t in world.tiles.items():
            if t['owner'] not in allies or not t['buildings'].get('瞭望塔'):
                continue
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if dx * dx + dy * dy <= r * r:
                        out.add((x + dx, y + dy))
    return {(x, y) for x, y in out if 0 <= x < world.size and 0 <= y < world.size}

def _has_barb(world, x: int, y: int) -> bool:
    return any((a['owner'] == '野人' and (a['x'], a['y']) == (x, y) for a in world.armies))

def _fmt_atlas(world, name) -> str:
    """**坐标地图**（常驻默认）：视野内逐格一行文字，**按势力分段**。

    格式：`(x,y)归属地形，[L2城][，地名][，番号…]`（番号段**不加标签、垫在最后**，用户 2026-09-19：
    「驻军两个字删了」——有数字的就是军队番号，没有的就是地名，不必再点名）
    · **归属** = 我 / 野(无主) / 国名；**只列视野内的格**。
    · **番号 = 短兵种 + 军队番号**：`步1、步2、楚骑3`（番号各国独立编号、阵亡不回收），
      一眼能对上【军队】面板的 `#n`；格主的部队省归属前缀并排前面；野人守军写作「野人」。
    · 城堡 `L2城` 一律公开（视野内）。
    · **分段**：我 → 野人(无主) → 其他国（每国一段，空行隔开；用户 2026-09-19：
      「按自己，野人，其他国排开，要一个回车分割势力」）。

    为什么要文字、为什么分段：地图是给 LLM 读的——网格要配图例、还得把 2 字符格对齐成坐标；
    而这一行自带语义、零解码；分段则让"谁的地盘"一眼分块，不用在 500 行里找边界。
    网格版仍在（`_fmt_map` / `_fmt_mil_map`），走 `query panel=grid` 按需取。
    """
    vis = _visible_cells(world, name)
    if not vis:
        return '（你还没有国土，也没有视野）'
    own = set(world.own_tiles(name))
    order = {n: i for i, n in enumerate(world.order)}
    army_at: dict = {}
    for a in world.armies:
        army_at.setdefault((a['x'], a['y']), []).append(a)

    def line_of(x: int, y: int) -> str:
        t = world.tiles.get((x, y))
        o = world.owned_by(x, y)
        who = '我' if (x, y) in own else o if o else '野'
        line = f'({x + 1},{y + 1}){who}{world.tile_terrain(x, y)}'
        cl = t['buildings'].get('城堡', 0) if t else 0
        if cl:
            line += f'，L{cl}城'
        th = world.visible_buildings(name, x, y).get('市政厅', 0)
        if th:
            line += f'，厅×{th}'
        if t and t.get('name'):
            line += f"，{t['name']}"
        here = army_at.get((x, y), ())
        if here:

            def _key(a: dict):
                return (0 if a['owner'] == o else 1, order.get(a['owner'], 99), a['id'])
            tags = []
            for a in sorted(here, key=_key):
                if a['owner'] == '野人':
                    tags.append('野人')
                    continue
                pre = '' if a['owner'] == o else '我' if a['owner'] == name else a['owner']
                tags.append(f"{pre}{a.get('type', '步')}{a['id']}")
            line += '，' + '、'.join(tags)
        return line
    buckets: dict = {}
    for p in vis:
        o = world.owned_by(*p)
        key = '我' if p in own else '野人' if not o else o
        buckets.setdefault(key, []).append(p)
    seq = [k for k in ('我', '野人') if k in buckets]
    seq += sorted((k for k in buckets if k not in ('我', '野人')), key=lambda n: order.get(n, 99))
    parts = []
    for k in seq:
        parts.append(f'【{k}】\n' + '\n'.join((line_of(*p) for p in sorted(buckets[k]))))
    lines = ['\n\n'.join(parts)]
    lines.append(f'国土 {len(own)} 块 · 视野内 {len(vis)} 格（非自家 {len(vis - own)}）· 可拓荒地 {len(world.frontier_of(name))} 块')
    reach = sorted((p for p in P._reach_cells(world, name) if p in vis))
    if reach:
        head = reach[:24]
        txt = ' '.join((f"({x + 1},{y + 1}){world.tile_terrain(x, y)}{('野人' if _has_barb(world, x, y) else '空地')}" for x, y in head))
        more = f' …另 {len(reach) - len(head)} 块' if len(reach) > len(head) else ''
        lines.append(f'本回合可及（有军队够得着，可直接 atk 入驻）: {txt}{more}')
    lines.append(ATLAS_LEGEND)
    return '\n'.join(lines)

def _nation_letters(world) -> dict:
    """国别字母（A/B/C…，按世界顺序、跳过已亡国）——与看海大地图同款口径，两张地图共用。"""
    out = {}
    for i, n in enumerate(world.order):
        if n in world.nations:
            out[n] = chr(65 + i) if i < 26 else str(i - 25)
    return out

def _map_frame(world, name, vis):
    """两张地图**共用的取景框**：视野包围盒；过大（被远方飞地/盟友撑爆）时退回"本土+邻圈"。
    同框是为了上下对照着读——地形图与军事图的行列必须一一对应。"""
    own = set(world.own_tiles(name))
    x0, x1 = (min((p[0] for p in vis)), max((p[0] for p in vis)))
    y0, y1 = (min((p[1] for p in vis)), max((p[1] for p in vis)))
    cropped = False
    if (x1 - x0 + 1) * (y1 - y0 + 1) > MAP_MAX_CELLS:
        near = set(own)
        for x, y in own:
            near |= set(world.neighbors(x, y))
        near &= vis
        if near:
            x0, x1 = (min((p[0] for p in near)), max((p[0] for p in near)))
            y0, y1 = (min((p[1] for p in near)), max((p[1] for p in near)))
            cropped = True
    return (x0, x1, y0, y1, cropped)

def _axis_lines(x0, x1, y0, y1) -> list:
    """列头用**两行**（十位/个位），与 2 字符格严格对齐：单行写会在 9→10 处变成
    "9101112…"，模型有读错坐标的风险（读错 = 白烧一个动作）。"""
    lab = [f'{(x + 1) % 100:02d}' for x in range(x0, x1 + 1)]
    return ['      ' + ''.join((d[0] + ' ' for d in lab)), '      ' + ''.join((d[1] + ' ' for d in lab))]

def _fmt_map(world, name) -> str:
    """**地形/国土图**（常驻）。只画自己国土 + 视野内的格。

    · **军队不在这张图上**（见 `_fmt_mil_map`）：军队每回合都在动，混在一起既挤又乱。
    · 视野内的**他国领地**：地形照给（小写），第 2 位放该国的**国别字母** ——
      即"既显示地形、也显示归属"，两者不冲突（地形占第 1 位、归属占第 2 位）。
    · 只用纯 ASCII：`■`/`·` 的宽度是 Ambiguous，CJK 等宽字体下按全角渲染会整张错位。
    """
    own = set(world.own_tiles(name))
    vis = _visible_cells(world, name)
    if not vis:
        return '（你还没有国土，也没有视野）'
    x0, x1, y0, y1, cropped = _map_frame(world, name, vis)
    letters = _nation_letters(world)

    def cell(x: int, y: int) -> str:
        """第 1 位地形（统一小写）、第 2 位归属。**只有一条规则**，没有例外分支：
        自家的地和别国的地写法完全一样，只是代码不同（用户 2026-09-18：「和外国一样，
        只是换成本国代码」）。建筑不再占位——要找建筑走 `land filter=<建筑名>`。"""
        o = world.owned_by(x, y)
        if o != name and (x, y) not in vis:
            return '?.'
        c = world.ter_char(x, y).lower()
        if o:
            return c + letters.get(o, 'o')
        if _has_barb(world, x, y):
            return c + '*'
        return c + '.'
    lines = [f'地形/国土图 x {x0 + 1}→{x1 + 1}、y {y0 + 1}→{y1 + 1}（坐标 1-based，每格 2 字符；列头上下两行拼起来就是 x）']
    lines += _axis_lines(x0, x1, y0, y1)
    for y in range(y0, y1 + 1):
        lines.append(f'  {y + 1:3d} ' + ''.join((cell(x, y) for x in range(x0, x1 + 1))))
    lines.append(f'国土 {len(own)} 块 · 视野内 {len(vis)} 格（非自家 {len(vis - own)}）· 可拓荒地 {len(world.frontier_of(name))} 块')
    forts, halls = ([], [])
    for fx, fy in sorted(vis):
        t2 = world.tiles.get((fx, fy))
        if not t2 or t2['owner'] == name:
            continue
        lv = t2['buildings'].get('城堡', 0)
        if lv:
            forts.append(((fx, fy), lv))
        th = t2['buildings'].get('市政厅', 0)
        if th:
            halls.append(((fx, fy), th))
    if forts:
        head = forts[:12]
        txt = ' '.join((f'L{lv}@({fx + 1},{fy + 1})' for (fx, fy), lv in head))
        more = f' …另 {len(forts) - len(head)} 座' if len(forts) > len(head) else ''
        lines.append(f'视野内城堡（看得见的要塞，攻它之前先掂量）：{txt}{more}')
    if halls:
        head = halls[:12]
        txt = ' '.join((f"×{n}@({fx + 1},{fy + 1})[{world.owned_by(fx, fy) or '无主'}]" for (fx, fy), n in head))
        more = f' …另 {len(halls) - len(head)} 处' if len(halls) > len(head) else ''
        lines.append(f'视野内市政厅（各家的国祚——拔光即亡国；[无主]=废墟白捡）：{txt}{more}')
    lines.append('国别代码：' + '  '.join((f'{letters[n]}={n}' + ('(你)' if n == name else '') for n in world.order if n in world.nations)))
    reach = sorted((p for p in P._reach_cells(world, name) if p in vis))
    if reach:
        head = reach[:24]
        txt = ' '.join((f"({x + 1},{y + 1}){world.ter_char(x, y)}{('野人' if _has_barb(world, x, y) else '空地')}" for x, y in head))
        more = f' …另 {len(reach) - len(head)} 块' if len(reach) > len(head) else ''
        lines.append(f'本回合可及（有军队够得着，可直接 atk 入驻）: {txt}{more}')
    if cropped:
        lines.append('（视野里有远离本土的格，未画进图——用 query panel=land 看全清单）')
    lines.append(MAP_LEGEND)
    lines.append('逐格明细：query panel=land（cap= 列几块 / offset= 从第几块起 / filter= 只看含某建筑或某资源的格）；单格全明细：query panel=tile x= y=（或 at=地名）')
    return '\n'.join(lines)

def _fmt_land(world, name, cap=LAND_CAP, offset=0, filter_='') -> str:
    """国土**逐格明细**（按需查询；常驻只画 `_fmt_map`）。

    cap=一次列几块；offset=从第几块开始（翻页）；filter_=只看含某**建筑**或某**资源**的格
    （只认这两类名字，别的会被拒并列出全部可选项）。
    """
    own = world.own_tiles(name)
    flt = str(filter_ or '').strip()
    if flt:
        if flt in BUILDINGS:
            own = [p for p in own if world.tiles[p]['buildings'].get(flt) or (world.tiles[p].get('pending') or {}).get(flt)]
        elif flt in RESOURCES:
            own = [p for p in own if world.tiles[p]['resources'].get(flt, 0) > 0]
        else:
            return f"filter 只认**建筑名**或**地块资源名**，别的查不了：\n  建筑：{' '.join(BUILDINGS)}\n  资源：{' '.join(RESOURCES)}\n（想按别的条件找格，用 query panel=tile 逐格看）"
    total = len(own)
    head = f'筛「{flt}」命中 {total} 块（原国土 {len(world.own_tiles(name))} 块）' if flt else f'国土 {total} 块'
    off = max(0, min(int(offset or 0), max(0, total - 1))) if total else 0
    page = own[off:off + max(1, int(cap or LAND_CAP))]
    span = f'第 {off + 1}–{off + len(page)} 块' if page else '0 块'
    lines = [f'{head}（按坐标排序；本次列 {span}，cap={cap} offset={off}）:']
    for x, y in page:
        t = world.tiles[x, y]
        b = t['buildings']
        used = sum(b.values()) + sum((t.get('pending') or {}).values())
        res = ' '.join((f"{k}x{t['resources'][k]}" for k in RESOURCES))
        pend = ' '.join((f'{bn}在建' for bn, n in (t.get('pending') or {}).items() if n))
        built = ' '.join((f'{bn}×{n}' for bn, n in b.items() if n)) or '无'
        free = '可建' if not t['built_this_turn'] else '本回合已下单'
        gar = ' '.join((f"军{a['id']}({a['hp']})" for a in world.armies if a['owner'] == name and (a['x'], a['y']) == (x, y)))
        extra = f' 在建:{pend}' if pend else ''
        core = '♥' if t.get('core') == name else ''
        lines.append(f"  {core}{t['name']} ({x + 1},{y + 1}){t['terrain']} 城L{b['城堡']} 位{used}/{MAX_SLOTS} [资源 {res}] 建筑:{built}{extra} {free}{(' 驻:' + gar if gar else '')}")
    if off + len(page) < total:
        lines.append(f'  …还有 {total - off - len(page)} 块，用 offset={off + len(page)} 接着看')
    if not flt:
        fr = sorted(world.frontier_of(name))
        vis = _visible_cells(world, name)
        reach = P._reach_cells(world, name)
        lines.append(f'可拓荒地 {len(fr)} 块（[野人]=有守军需 atk 打赢；[空地]=无守军，atk 进驻即占；可及=本回合有军队够得着）:')
        frs = []
        for x, y in fr:
            tag = '[野人]' if _has_barb(world, x, y) else '[空地]'
            ok = '可及' if (x, y) in reach and (x, y) in vis else ''
            frs.append(f'({x + 1},{y + 1}){world.ter_char(x, y)}{tag}{ok}')
        for i in range(0, len(frs), 8):
            lines.append('  ' + ' '.join(frs[i:i + 8]))
        lines.append(f'  地形挡路：{P._move_cost_text()}。')
    return '\n'.join(lines)

def _fmt_tile(world, name, xy) -> str:
    """**单格全明细**（精确查询）。三条纪律，缺一不可：

    · **视野外** → 一律不答（与地图 / 威胁面板同一套口径）；
    · **自家地** → 全明细：资源 / 建筑位 / 已建成与在建 / 可否下单 / 驻军；
    · **非自家地**（无主野地 或 他国领土）→ **只给地形与归属**（+ 看得见的军队、能否够着）。
      资源与建筑**不报**——那是"占下来才知道"的东西，也是 spy / 换图才拿得到的底细；
      顺手给"本回合可否建造"更是一种误导：那不是你的地。
      （2026-09-18 用户：「野地不是没有资源视野吗，只有地形」。）
    · **例外：无主故土**（前朝废墟——亡国留下的、`owner is None` 且已物化的格，2026-09-21）：
      **全报**（资源 + 存留建筑）。那条纪律护的是"别国的内政"，而这里已经没有国了；
      不报就等于让"建筑保留"这条规则谁也看不见、没人去捡。
    """
    x, y = xy
    if not (0 <= x < world.size and 0 <= y < world.size):
        return f'({x + 1},{y + 1}) 超出地图范围（1~{world.size}）'
    if (x, y) not in _visible_cells(world, name):
        return f'({x + 1},{y + 1}) 不在你视野内——你只看得见自己国土 + 相邻一圈（联盟共享视野；瞭望塔再往外扩）。'
    t = world.tiles.get((x, y))
    owner = world.owned_by(x, y)
    mine = owner == name
    terrain = world.tile_terrain(x, y)
    st = TERRAIN_STATS[terrain]
    if mine:
        who = '你的国土'
    elif owner is None and _has_barb(world, x, y):
        who = '无主（有野人守军，atk 打赢才能占）'
    elif owner is None and t is not None:
        who = '无主故土·前朝废墟（atk 进驻即占，存留建筑归你）'
    elif owner is None:
        who = '无主空地（atk 进驻即占）'
    else:
        who = f'{owner} 的领土'
    lines = [f'({x + 1},{y + 1}) {terrain}｜{who}' + (f"｜{t['name']}" if t and t.get('name') else '') + ('｜♥ 你的核心领土' if t and t.get('core') == name else '')]
    lines.append(f"  地形：防御 {st['defense']:+d}%、建设惩罚 {st['build_penalty']:+d}%")
    if mine:
        res = t['resources'] if t else world.tile_resources(x, y)
        lines.append('  资源：' + ' '.join((f'{k}x{res.get(k, 0)}' for k in RESOURCES)))
        if t:
            b, pend = (t['buildings'], t.get('pending') or {})
            used = sum(b.values()) + sum(pend.values())
            built = ' '.join((f'{bn}×{n}' for bn, n in b.items() if n)) or '无'
            lines.append(f'  建筑位 {used}/{MAX_SLOTS}；已建成：{built}' + ('；在建：' + ' '.join((f'{bn}×{n}' for bn, n in pend.items() if n)) if pend else ''))
            lines.append('  本回合可下令建造：' + ('可以' if not t['built_this_turn'] else '不行（本回合已下过单）'))
    elif owner is None and t is not None and any(t['buildings'].values()):
        b = t['buildings']
        used = sum(b.values())
        lines.append('  资源：' + ' '.join((f"{k}x{t['resources'].get(k, 0)}" for k in RESOURCES)))
        lines.append(f'  建筑位 {used}/{MAX_SLOTS}；存留建筑：' + (' '.join((f'{bn}×{n}' for bn, n in b.items() if n)) or '无'))
        lines.append('  （无主之地：atk 进驻即占，**存留建筑归占领者**；谁先到谁得）')
    else:
        for bn, cnt in world.visible_buildings(name, x, y).items():
            if bn == '城堡':
                cd = cnt * building_effect('城堡', 'defense_per_level')
                lines.append(f'  城堡：L{cnt}（该格再加 {cd:+d}% 防御，与地形**相乘**叠加）')
            elif bn == '市政厅':
                lines.append(f'  市政厅：{cnt} 座 —— **此国之祚**：拔光即亡国，它剩下的地会全成无主之地（建筑留原地）')
        lines.append('  资源与其它建筑：**未探明**（那是别人的地/无主地——占下来才知道，别国的建设底细只能靠 spy / 换图）')
    mine_army = [a for a in world.armies if a['owner'] == name and (a['x'], a['y']) == (x, y)]
    if mine_army:
        lines.append('  你的驻军：' + ' '.join((f"{a['name']}({a['hp']}HP)" for a in mine_army)))
    outside = [a for a in world.armies if a['owner'] not in (name, '野人') and (a['x'], a['y']) == (x, y)]
    if outside:
        lines.append('  他国军队：' + ' '.join((f"{a['name']}({a['owner']},{a['hp']}HP)" for a in outside)))
    lines.append('  本回合有军队够得着：' + ('是' if (x, y) in P._reach_cells(world, name) else '否'))
    return '\n'.join(lines)

def _tile_xy(world, actor, ref: str) -> tuple[int, int] | None:
    """ref 可形如 '5 6'（1-based 坐标）、地块名、或『名字 (x,y)』混合串。返回 0-based 坐标或 None。"""
    s = ref.strip()
    s = ' '.join(s.replace('（', ' ').replace('）', ' ').replace('(', ' ').replace(')', ' ').replace(',', ' ').split())
    toks = s.split()
    if len(toks) >= 2 and toks[0].isdigit() and toks[1].isdigit():
        return (int(toks[0]) - 1, int(toks[1]) - 1)
    best, bl = (None, -1)
    for (x, y), t in world.tiles.items():
        nm = t.get('name')
        if nm and nm in s and (len(nm) > bl):
            best, bl = ((x, y), len(nm))
    return best

EXPORTS = {n: v for n,v in globals().copy().items() if callable(v) and getattr(v,"__module__",None)==__name__}


def panel_land(world,actor,**args):
    def integer(value,default):
        try:return int(value)
        except (TypeError,ValueError):return default
    return _fmt_land(world,actor,cap=integer(args.get('cap'),LAND_CAP),offset=integer(args.get('offset'),0),filter_=str(args.get('filter') or ''))

def panel_tile(world,actor,**args):
    ref=str(args.get('at') or '').strip() or f"{args.get('x','')} {args.get('y','')}"
    xy=_tile_xy(world,actor,ref)
    if xy is None:return '用法：query panel=tile x= 5 y= 6（或 at=地名）。只查得到你视野内的格。'
    return _fmt_tile(world,actor,xy)

def panel_grid(world,actor,**args):
    return _fmt_map(world,actor)+'\n\n'+P._fmt_mil_map(world,actor)+'\n\n（这是网格版；默认面板给的是坐标地图。两者信息等价。）'
