"""把新引擎录制的参照对局逐动作回放到 legacy-rl 引擎，逐回合比对状态。"""
import sys, json, random
root, ref = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
import mp
D = json.load(open(ref)); log = D['log']
RES = {'food': '粮食', 'wood': '木头', 'ore': '矿石', 'oil': '石油', 'equipment': '装备', 'supply': '补给', 'gold': '黄金'}
BLD = {'lumber-camp': '林场', 'farm': '农场', 'mine': '矿场', 'oil-well': '石油厂', 'gold-mine': '黄金矿场',
       'wood-power-plant': '木材能源厂', 'oil-power-plant': '石油能源厂', 'supply-factory': '补给厂',
       'equipment-factory': '装备厂', 'barracks': '兵营', 'town-hall': '市政厅', 'watchtower': '瞭望塔',
       'diplomatic-center': '外交中心', 'academy': '工程院', 'castle': '城堡', 'garrison-farm': '军屯'}
UNIT = {'infantry': '步', 'cavalry': '骑', 'militia': '民'}
UNIT_R = {v: k for k, v in UNIT.items()}
S = lambda s: s.split(':')[-1]
seed, size = int(sys.argv[3]), int(sys.argv[4])
nat = {}
for a, v in D['init']['actors'].items():
    tw = [b for b in v['bld'] if b[0] == 'town-hall'][0]
    nat[a] = ('国' + a[-1], (int(tw[1][0]), int(tw[1][1])))
w = mp.World(size=size, seed=seed, nations=[n for n, _ in nat.values()], starts={n: p for n, p in nat.values()}, max_turns=200)
AID = {}   # 新引擎军队 id -> 旧引擎 id
unmapped = set(); mism = []
import re
def pos(site): m = re.search(r'(\d+)-(\d+)$', site); return int(m.group(1)), int(m.group(2))
def legacy_snap(name):
    stocks = {k: w.res(name, v) for k, v in RES.items()}
    bld = sorted((k, p) for p, t in w.tiles.items() if t['owner'] == name for k, c in t['buildings'].items() for _ in range(c) for k in [k])
    return stocks, bld
def new_snap(a, snap):
    st = {S(x['id']): x['amount'] for x in snap['actors'][a]['stocks']}
    return st
def compare(turn, tag):
    out = []
    after = log[turn - 1]['after'] if turn >= 1 else D['init']
    for a, (name, _) in nat.items():
        ns = new_snap(a, after); ls, lb = legacy_snap(name)
        for k in RES:
            if ns.get(k, 0) != ls[k]: out.append(f"{name}.{k}: new {ns.get(k,0)} legacy {ls[k]}")
        nb = sorted((BLD.get(k, k), (int(p[0]), int(p[1]))) for k, p in after['actors'][a]['bld'])
        lbs = sorted((k, p) for k, p in lb)
        if nb != lbs: out.append(f"{name}.bld: new-only {[x for x in nb if x not in lbs]} legacy-only {[x for x in lbs if x not in nb]}")
        from collections import defaultdict
        gn, gl = defaultdict(lambda: [0, 0.0]), defaultdict(lambda: [0, 0.0])
        for k, x_, y_, hp in after['actors'][a]['army']: gn[(k, x_, y_)][0] += 1; gn[(k, x_, y_)][1] += hp
        for u in w.nation_armies(name): gl[(UNIT_R[u['type']], u['x'], u['y'])][0] += 1; gl[(UNIT_R[u['type']], u['x'], u['y'])][1] += u['hp']
        if set(gn) != set(gl) or any(gn[k][0] != gl[k][0] or abs(gn[k][1] - gl[k][1]) > 1e-6 for k in gn):
            out.append(f"{name}.army: new {dict(gn)} legacy {dict(gl)}")
        if after['actors'][a]['land'] != len(w.own_tiles(name)): out.append(f"{name}.land new {after['actors'][a]['land']} legacy {len(w.own_tiles(name))}")
    for g in ('food', 'wood', 'ore', 'oil', 'equipment', 'supply'):
        gn = RES[g]
        np_, lp = after['market']['prices'][g], w.prices[gn]
        if abs(np_ - lp) > 1e-6: out.append(f"price.{g}: new {np_:.5f} legacy {lp:.5f}")
        if after['market']['inventory'][g] != w.market_inv[gn]: out.append(f"inv.{g}: new {after['market']['inventory'][g]} legacy {w.market_inv[gn]}")
        if abs(after['market']['reference'][g] - w.market_ref[gn]) > 1e-6: out.append(f"ref.{g}: new {after['market']['reference'][g]:.4f} legacy {w.market_ref[gn]:.4f}")
    return out
print('init', compare(0, 'init') or 'OK')
first_bad = None; tradediff = []
for r in log:
    w.begin_turn()
    for x in r['actions']:
        a, act, args = x['actor'], x['action'], x['args']; name = nat[a][0]
        if act in ('plan', 'end_turn'): continue
        if act in ('buy', 'sell'):
            g0 = w.res(name, '黄金'); ok, msg = getattr(w, act)(name, RES[S(args['resource'])], args['quantity'])
            gold = abs(w.res(name, '黄金') - g0); nd = x['res']['data']
            if not ok or gold != nd['gold']: tradediff.append((r['turn'], act, S(args['resource']), args['quantity'], nd['gold'], gold if ok else msg))
        elif act == 'build':
            ok, msg = w.build(name, *pos(args['site']), BLD[S(args['kind'])])
        elif act == 'recruit':
            before = {u['id'] for u in w.nation_armies(name)}
            ok, msg = w.recruit(name, *pos(args['site']), args['count'], UNIT[S(args['kind'])])
            newids = [u['id'] for u in w.nation_armies(name) if u['id'] not in before]
            for nid, lid in zip([d['id'] for d in x['res']['data']], newids): AID[nid] = lid
        elif act == 'move':
            ok, msg = w.move(name, AID.get(args['aid'], -1), args['x'], args['y'])
        elif act == 'attack':
            ok, msg = w.attack(name, [AID.get(i, -1) for i in args['ids']], args['x'], args['y'])
        else:
            unmapped.add(act); continue
        if not ok: mism.append((r['turn'], act, a, str(args)[:80], msg[:80]))
    w.resolve_turn()
    d = compare(r['turn'], 'after')
    if d and first_bad is None:
        first_bad = r['turn']
    if d and r['turn'] <= first_bad + 1: print('turn', r['turn'], d[:12])
print('final', {n: (w.res(n,'黄金'), len(w.own_tiles(n)), len(w.nation_armies(n))) for n,_ in nat.values()}, 'wars', len(w.wars))
print('first_bad_turn', first_bad, 'unmapped', unmapped)
print('action failures in legacy', len(mism), mism[:6])
print('trade gold diffs', len(tradediff), tradediff[:8])
