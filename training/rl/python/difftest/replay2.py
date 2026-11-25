"""通用回放：新引擎录制（含外交/银行/拆除等脚本动作）→ legacy-rl，逐回合比对 + 逐动作比对成败。"""
import sys, json, re, collections
root, ref, seed, size = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
VERBOSE = int(sys.argv[5]) if len(sys.argv) > 5 else 3
sys.path.insert(0, root)
import mp
D = json.load(open(ref)); log = D['log']
RES = {'food': '粮食', 'wood': '木头', 'ore': '矿石', 'oil': '石油', 'equipment': '装备', 'supply': '补给', 'gold': '黄金'}
BLD = {'lumber-camp': '林场', 'farm': '农场', 'mine': '矿场', 'oil-well': '石油厂', 'gold-mine': '黄金矿场',
       'wood-power-plant': '木材能源厂', 'oil-power-plant': '石油能源厂', 'supply-factory': '补给厂',
       'equipment-factory': '装备厂', 'barracks': '兵营', 'town-hall': '市政厅', 'watchtower': '瞭望塔',
       'diplomacy-center': '外交中心', 'academy': '工程院', 'castle': '城堡', 'militia-camp': '军屯'}
UNIT = {'infantry': '步', 'cavalry': '骑', 'militia': '民'}
UNIT_R = {v: k for k, v in UNIT.items()}
S = lambda s: s.split(':')[-1]
def pos(site): m = re.search(r'(\d+)-(\d+)$', site); return int(m.group(1)), int(m.group(2))
def nm(a):
    """scenario:n1 / nation/scenario:n1 → 国1"""
    m = re.search(r'scenario:n(\d+)', str(a)); return '国' + m.group(1) if m else a
nat = {}
for a, v in D['init']['actors'].items():
    tw = [b for b in v['bld'] if b[0] == 'town-hall'][0]
    nat[a] = (nm(a), (int(tw[1][0]), int(tw[1][1])))
w = mp.World(size=size, seed=seed, nations=[n for n, _ in nat.values()], starts={n: p for n, p in nat.values()}, max_turns=200)
w.bank_enable()
AID = {}; OFFER = {}; VOTE = {}; BLOCNAME = {}; OKIND = {}
BQ = {}
RQ = {}
SKIPFEE = {'declare_war','offer_peace','guarantee','cancel_guarantee','propose_defense','break_defense','bloc_found','bloc_join','bloc_leave','propose_vassalage','submit_vassalage','independence_war','gift','send_letter','share_map','spy','borrow'}
BID = {}
amis = []; FEES = collections.Counter()   # 动作成败不一致
def legacy_state():
    wars = sorted((tuple(sorted(w._war_sides(x)[0])), tuple(sorted(w._war_sides(x)[1]))) for x in w.wars)
    return dict(wars=wars, truce={tuple(sorted(k)): v for k, v in w.truce.items()}, pacts=w.pacts, blocs=[dict(name=b['name'], chief=b.get('chief'), members=b['members']) for b in w.blocs],
                vassals=w.vassals, loans=w.bank.get('loans'))
def new_dip(after):
    d = after['diplo']
    wars = sorted((tuple(sorted(nm(x) for x in x_['attackers'])), tuple(sorted(nm(x) for x in x_['defenders']))) for x_ in d['wars'])
    return dict(wars=wars, truces=d['truces'], pacts=d['pacts'], alliances=d['alliances'], vassals=d['vassals'], offers=d['offers'], votes=d['votes'])
def compare(turn):
    out = []
    after = log[turn - 1]['after'] if turn >= 1 else D['init']
    for a, (name, _) in nat.items():
        if a not in after['actors']:
            if name in w.alive(): out.append(f"{name}: new eliminated, legacy alive")
            continue
        if name not in w.alive(): out.append(f"{name}: legacy eliminated, new alive"); continue
        st = {S(x['id']): x['amount'] for x in after['actors'][a]['stocks']}
        for k in RES:
            if st.get(k, 0) != w.res(name, RES[k]): out.append(f"{name}.{k}: new {st.get(k,0)} legacy {w.res(name, RES[k])}")
        nb = sorted((BLD.get(k, k), (int(p[0]), int(p[1]))) for k, p in after['actors'][a]['bld'])
        lb = sorted((k, p) for p, t in w.tiles.items() if t['owner'] == name for k, c in t['buildings'].items() for _ in range(c))
        if nb != lb: out.append(f"{name}.bld: new-only {[x for x in nb if x not in lb]} legacy-only {[x for x in lb if x not in nb]}")
        gn, gl = collections.defaultdict(lambda: [0, 0.0]), collections.defaultdict(lambda: [0, 0.0])
        for k, x_, y_, hp in after['actors'][a]['army']: gn[(k, x_, y_)][0] += 1; gn[(k, x_, y_)][1] += hp
        for u in w.nation_armies(name): gl[(UNIT_R[u['type']], u['x'], u['y'])][0] += 1; gl[(UNIT_R[u['type']], u['x'], u['y'])][1] += u['hp']
        if set(gn) != set(gl) or any(gn[k][0] != gl[k][0] or abs(gn[k][1] - gl[k][1]) > 1e-6 for k in gn): out.append(f"{name}.army: new {dict(gn)} legacy {dict(gl)}")
        if after['actors'][a]['land'] != len(w.own_tiles(name)): out.append(f"{name}.land new {after['actors'][a]['land']} legacy {len(w.own_tiles(name))}")
        bk = after['actors'][a].get('bank')
        if bk:
            ln = w.bank.get('loans', {}).get(name)
            nl = bk.get('loan')
            if bool(nl) != bool(ln) or (nl and (nl['principal'] != ln['principal'])): out.append(f"{name}.loan new {nl} legacy {ln}")
    for g in ('food', 'wood', 'ore', 'oil', 'equipment', 'supply'):
        gn_ = RES[g]
        if abs(after['market']['prices'][g] - w.prices[gn_]) > 1e-6: out.append(f"price.{g}: new {after['market']['prices'][g]:.5f} legacy {w.prices[gn_]:.5f}")
        if after['market']['inventory'][g] != w.market_inv[gn_]: out.append(f"inv.{g}: new {after['market']['inventory'][g]} legacy {w.market_inv[gn_]}")
        if abs(after['market']['reference'][g] - w.market_ref[gn_]) > 1e-6: out.append(f"ref.{g}: new {after['market']['reference'][g]:.4f} legacy {w.market_ref[gn_]:.4f}")
    if 'diplo' in after and isinstance(after['diplo'], dict):
        nd, ld = new_dip(after), legacy_state()
        if nd['wars'] != ld['wars']: out.append(f"wars: new {nd['wars']} legacy {ld['wars']}")
        nb = sorted((a['name'], a['chief'] and nm(a['chief']), tuple(sorted(nm(m) for m in a['members']))) for a in nd['alliances'])
        lb = sorted((b['name'], b.get('chief'), tuple(sorted(b['members']))) for b in w.blocs)
        if nb != lb: out.append(f"blocs: new {nb} legacy {lb}")
        def ent_new(e):
            if e.startswith('nation/'): return (nm(e),)
            al = next((a for a in nd['alliances'] if 'bloc/' + a['id'] == e), None)
            return tuple(sorted(nm(m) for m in al['members'])) if al else (e,)
        def ent_old(e): return tuple(sorted(w.entity_members(e))) if e.startswith('盟') else (e.split(':')[-1],)
        npc = sorted((p['kind'], ) + tuple(sorted([ent_new(p['a']), ent_new(p['b'])])) for p in nd['pacts'])
        lpc = sorted(({'保障': 'guarantee', '共同防御': 'defense'}[p['kind']], ) + tuple(sorted([ent_old(p['a']), ent_old(p['b'])] if p['kind'] == '共同防御' else [ent_old(p['a']), ent_old(p['b'])])) for p in w.pacts)
        if npc != lpc: out.append(f"pacts: new {npc} legacy {lpc}")
    return out
def tgt(x): return nm(x)
def apply(name, act, args, x):
    """返回 (ok, msg)；None 表示跳过。"""
    if act == 'end_turn':
        q, keep, used = BQ.get(name, []), [], set()
        for (px, py, k, costs) in q:                 # 队列按提交序：每格本回合开工一单
            if (px, py) in used: keep.append((px, py, k, costs)); continue
            for g_, v in costs.items(): w.add_res(name, g_, v)
            r = w.build(name, px, py, k)
            if r[0]: used.add((px, py))
            else:
                for g_, v in costs.items(): w.add_res(name, g_, -v)
                keep.append((px, py, k, costs))
        BQ[name] = keep
        return w.end_turn(name)
    if act in ('plan', 'explain_plan'): return None
    if act in ('buy', 'sell'):
        return getattr(w, act)(name, RES[S(args['resource'])], args['quantity'])
    auto = x['res'].get('automatic') if isinstance(x.get('res'), dict) else None
    if act == 'build' and auto == 'production_queue':
        return None                                  # main 回合末自动开工的回执：提交时已在 legacy 执行（legacy 无队列）
    if act == 'military' and args.get('action') == 'recruit' and auto == 'production_queue':
        # main 回合末统一出兵：退回提交时的预付，按 main 时点在 legacy 征兵
        q = RQ.get(name, [])
        px, py = args['x'], args['y']
        k = next((i for i, it in enumerate(q) if it[0] == (px, py) and it[1] == UNIT[S(args['kind'])]), None)
        if k is not None:
            (_p, kind_, cnt_, costs) = q.pop(k)
            for g_, v in costs.items(): w.add_res(name, g_, v)
        before = {u['id'] for u in w.nation_armies(name)}
        r = w.recruit(name, px, py, args.get('count', 1), UNIT[S(args['kind'])])
        d = x['res'].get('data') or []
        newids = [u['id'] for u in w.nation_armies(name) if u['id'] not in before]
        for n_, l_ in zip([q_['id'] for q_ in d] if isinstance(d, list) else [], newids): AID[n_] = l_
        return r
    if act == 'recruit' and isinstance(x['res'].get('data'), dict) and x['res']['data'].get('status') == 'queued':
        # main 提交征兵：预付并排队（legacy 无队列，测试里模拟预付；出兵在上面的自动回执处）
        dd = x['res']['data']; _c = dd.get('paid') or {}
        costs = {RES[S(k)]: v for k, v in (_c.items() if isinstance(_c, dict) else _c)}
        for g_, v in costs.items(): w.add_res(name, g_, -v)
        RQ.setdefault(name, []).append((tuple(dd['position']), UNIT[S(args['kind'])], args.get('count', 1), costs))
        return True, '模拟征兵队列'
    if act == 'build':
        site = args.get('site') or args.get('place')
        px, py = pos(site) if site else (args['x'], args['y'])
        r = w.build(name, px, py, BLD[S(args['kind'])])
        dd = x['res'].get('data') if isinstance(x.get('res'), dict) else None
        if not r[0] and x['ok'] and '每地块每回合限建' in r[1] and isinstance(dd, dict):
            # 仅测试用：模拟 main 的跨回合建造队列（提交即预付，之后每回合每格开工一单）——legacy 不做该功能
            _c = dd.get('costs') or {}; costs = {RES[S(k)]: v for k, v in (_c.items() if isinstance(_c, dict) else _c)}
            for g_, v in costs.items(): w.add_res(name, g_, -v)
            BQ.setdefault(name, []).append((px, py, BLD[S(args['kind'])], costs))
            return True, '模拟队列'
        return r
    if act == 'recruit':
        before = {u['id'] for u in w.nation_armies(name)}
        px, py = pos(args['site']); r = w.recruit(name, px, py, args.get('count', 1), UNIT[S(args['kind'])])
        d = x['res'].get('data') or []
        newids = [u['id'] for u in w.nation_armies(name) if u['id'] not in before]
        for n_, l_ in zip([q['id'] for q in d] if isinstance(d, list) else [], newids): AID[n_] = l_
        return r
    if act == 'move': return w.move(name, AID.get(args.get('aid', args.get('id')), -1), args['x'], args['y'])
    if act == 'attack': return w.attack(name, [AID.get(i, -1) for i in args['ids']], args['x'], args['y'])
    if act == 'retreat': return w.retreat(name, AID.get(args.get('aid', args.get('id')), -1), args['x'], args['y'])
    if act == 'disband': return w.disband(name, [AID.get(i, -1) for i in (args.get('ids') or [args.get('id')])])
    if act == 'demolish':
        info = BID.get(args['id'])
        if info is None: return (False, '未知建筑 id（新引擎已拒绝）') if not x['ok'] else (False, 'unknown id')
        return w.demolish(name, info[1], info[2], BLD[info[0]])
    if act == 'auto_trade':
        kw = {k: v for k, v in args.items() if k not in ('resource', 'action')}
        return w.set_auto_trade(name, RES[S(args['resource'])], **kw)
    if act == 'remove_auto_trade': return w.remove_auto_trade(name, RES[S(args['resource'])])
    if act == 'borrow': return w.bank_loan(name)
    if act == 'declare_war': return w.declare_war(name, tgt(args['target']))
    if act == 'offer_peace': return w.offer_peace(name, tgt(args['target']), args['kind'], args.get('gold', 0), args.get('note', ''), args.get('truce', 0))
    if act == 'accept_peace': return w.accept_peace(name, OFFER.get(args['offer'], args['offer']))
    if act == 'reject_peace': return w.reject_peace(name, OFFER.get(args['offer'], args['offer']))
    if act == 'gift': return w.gift(name, tgt(args['target']), RES[S(args['resource'])], args['quantity'])
    if act == 'send_letter': return w.send_mail(name, tgt(args['target']), args['text'])
    if act == 'share_map': return w.share_map(name, tgt(args['target']))
    if act == 'spy': return w.spy(name, tgt(args['target']))
    if act == 'guarantee': return w.declare_guarantee(name, tgt(args['target']))
    if act == 'cancel_guarantee': return w.cancel_guarantee(name, tgt(args['target']))
    if act == 'propose_defense': return w.propose_pact('共同防御', name, tgt(args['target']))
    if act == 'break_defense': return w.break_pact('共同防御', name, tgt(args['target']))
    if act == 'accept_proposal':
        oid = OFFER.get(args['offer'], -args['offer'])
        return w.accept_pact(name, oid)
    if act == 'reject_proposal': return w.reject_pact(name, OFFER.get(args['offer'], -args['offer']))
    if act == 'bloc_found': return w.propose_bloc(name, args['name'], [tgt(t) for t in args['tos']])
    if act == 'bloc_join': return w.bloc_join(name, BLOCNAME[args['bloc']]) if args['bloc'] in BLOCNAME else (False, '联盟不存在')
    if act == 'bloc_leave': return w.bloc_leave(name)
    if act == 'bloc_transfer': return w.bloc_transfer(name, tgt(args['target']))
    if act == 'bloc_rename': return w.bloc_rename(name, args['name'])
    if act == 'bloc_dissolve': return w.bloc_dissolve(name)
    if act == 'vote': return w.cast_vote(name, VOTE.get(args['vote'], -args['vote']), args.get('accept'))
    if act == 'propose_vassalage': return w.propose_vassalage(name, tgt(args['target']))
    if act == 'submit_vassalage': return w.submit_vassalage(name, tgt(args['target']))
    if act == 'release_vassal': return w.release_vassal(name, tgt(args['target']))
    if act == 'independence_war': return w.independence_war(name)
    return ('UNMAPPED', act)
print('init', compare(0) or 'OK')
first_bad = None; unmapped = collections.Counter(); shown = 0
pend = None
for r in log:
    w.begin_turn()
    if pend is not None:
        d = compare(pend)
        if d and first_bad is None: first_bad = pend
        if d and shown < VERBOSE: print('turn', pend, [s[:300] for s in d[:8]]); shown += 1
    for x in r['actions']:
        a, act, args = x['actor'], x['action'], x['args']; name = nat[a][0]
        if act == 'build' and x['ok'] and isinstance(x['res'].get('data'), dict) and 'id' in x['res']['data']:
            site = args.get('site') or args.get('place')
            BID[x['res']['data']['id']] = [S(args['kind']), *(pos(site) if site else (args['x'], args['y']))]
        if pend is not None:
            for _a in log[pend - 1]['after']['actors'].values():
                for _i, _b in _a.get('bid', {}).items(): BID.setdefault(int(_i), _b)
        res = apply(name, act, args, x)
        if res is None: continue
        dd = x['res'].get('data') if isinstance(x.get('res'), dict) else None
        if isinstance(dd, dict) and dd.get('fee') and act not in SKIPFEE and res[0] != 'UNMAPPED' and bool(res[0]) and bool(x['ok']):
            FEES[(act, dd['fee'])] += 1
            w.add_res(name, '黄金', -dd['fee'])      # 费用模拟：legacy 尚无外交手续费，先扣上以免掩盖其他差异
        if res[0] == 'UNMAPPED': unmapped[act] += 1; continue
        if bool(res[0]) != bool(x['ok']) and not (act in ('declare_war',) and False):
            amis.append((r['turn'], name, act, str(args)[:70], f"new={x['ok']}:{x.get('code')}:{(x.get('msg') or '')[:50]}", f"legacy={res[0]}:{res[1][:70]}"))
    w.resolve_turn()
    aft = r['after']['diplo'] if isinstance(r['after'].get('diplo'), dict) else {}
    for o in aft.get('offers', []):
        if o['id'] not in OFFER:
            used = set(OFFER.values())
            cand = sorted(x['id'] for x in (w.proposals + w.peace_offers) if x['id'] not in used)
            if cand: OFFER[o['id']] = cand[0]; OKIND[o['id']] = o.get('action')
    for v in aft.get('votes', []):
        if v['id'] not in VOTE:
            used = set(VOTE.values()); cand = sorted(x['id'] for x in w.votes if x['id'] not in used)
            if cand: VOTE[v['id']] = cand[0]
    for al in aft.get('alliances', []): BLOCNAME[al['id']] = al['name']
    pend = r['turn']
w.begin_turn(); d = compare(pend)
if d and first_bad is None: first_bad = pend
if d and shown < VERBOSE: print('turn', pend, [s[:300] for s in d[:8]])
print('final', {n: (w.res(n, '黄金'), len(w.own_tiles(n)), len(w.nation_armies(n))) for n, _ in nat.values() if n in w.nations}, 'wars', len(w.wars))
print('first_bad_turn', first_bad, 'unmapped', dict(unmapped))
print('simulated fees (action, fee) -> count', dict(FEES))
print('action success mismatches', len(amis))
for m in amis[:12]: print('  ', m)
