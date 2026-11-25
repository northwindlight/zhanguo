import sys, json; sys.path.insert(0,"/root")
import scen_new
def try_build(P, kind, tiles=None):
    f = P.obs()
    for s in f['building_sites']['sites']:
        r = P.do("build", {"site": s['id'], "kind": kind})
        if r.get('ok'): return r
    return None
def script(turn, actor, P):
    i = int(actor[-1])
    if i != 0 and not (i == 1 and turn in (3, 8)): return
    if turn == 1 and i == 0:
        for k in ("native:academy", "native:castle", "native:diplomacy-center", "native:watchtower", "native:militia-camp"): try_build(P, k)
    if turn == 2 and i == 0:
        for k in ("native:castle", "native:farm", "native:lumber-camp"): try_build(P, k)
    if turn == 3 and i == 0:
        r = try_build(P, "native:mine")
        if r:
            bid = r['data']['id']
            print('pending id', bid, json.dumps(P.tool("demolish", {"id": bid}), ensure_ascii=False)[:300])
        inst = P.obs()['building_sites']['instances']
        print('instances', [(b['id'], b['kind'], b.get('remaining')) for b in inst][:12])
        done = [b for b in inst if b.get('remaining', 0) == 0 and not b['kind'].endswith('town-hall')]
        if done: print('demolish done', json.dumps(P.tool("demolish", {"id": done[0]['id']}), ensure_ascii=False)[:300])
        P.tool("demolish", {"id": 99999})
    if turn == 4 and i == 0:
        inst = P.obs()['building_sites']['instances']
        cas = [b for b in inst if b['kind'].endswith('castle')]
        print('castles', [(b['id'], b.get('level'), b.get('remaining')) for b in cas])
        if cas: print(json.dumps(P.tool("demolish", {"id": cas[0]['id']}), ensure_ascii=False)[:300])
    if turn == 5 and i == 0:
        for k in ("native:castle", "native:castle", "native:oil-well"): try_build(P, k)
    if turn == 6 and i == 0:
        inst = P.obs()['building_sites']['instances']
        th = [b for b in inst if b['kind'].endswith('town-hall')]
        print('hall', [(b['id'], b['site']) for b in th])
scen_new.run(7005, 16, 2, script, 14, "/root/scE.json")
d = json.load(open("/root/scE.json"))
for r in d['log'][:6]:
    print(r['turn'], [(x['actor'][-2:], x['action'], x['ok'], (x['msg'] or '')[:40]) for x in r['actions'] if x['action'] in ('demolish','build','cancel_build_order')][:14])
