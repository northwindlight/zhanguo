import sys, json; sys.path.insert(0,"/root")
import scen_new
def script(turn, actor, P):
    i = int(actor[-1]); f = P.obs()
    inst = f['building_sites']['instances']
    if turn == 3 and i == 0:
        # 施工中取消：先造再拆
        for s in f['building_sites']['sites']:
            r = P.do("build", {"site": s['id'], "kind": "native:lumber-camp"})
            if r.get('ok'):
                P.tool("demolish", {"id": r['data']['id']}); break
        done = [b for b in inst if b['kind'].endswith(('lumber-camp', 'mine', 'oil-well', 'farm'))]
        if done: P.tool("demolish", {"id": done[0]['id']})            # 已完工 0 返还
        cas = [b for b in inst if b['kind'].endswith('castle')]
        if cas:
            top = max(cas, key=lambda b: (b.get('level') or 0)); P.tool("demolish", {"id": top['id']})
    if turn == 4 and i == 0:
        cas = [b for b in inst if b['kind'].endswith('castle')]
        for s in f['building_sites']['sites']:
            r = P.do("build", {"site": s['id'], "kind": "native:castle"})
            if r.get('ok'): break
    if turn == 6 and i == 0:
        cas = [b for b in inst if b['kind'].endswith('castle')]
        if cas: P.tool("demolish", {"id": max(cas, key=lambda b: (b.get('level') or 0))['id']})
    if turn == 9 and i == 1:
        th = [b for b in inst if b['kind'].endswith('town-hall')]
        if th: P.tool("demolish", {"id": th[0]['id']})                # 拆最后市政厅
scen_new.run(7005, 16, 2, script, 16, "/root/scE2.json")
