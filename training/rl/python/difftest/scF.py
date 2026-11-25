import sys, json; sys.path.insert(0,"/root")
import scen_new
N = lambda i: f"scenario:n{i}"
def script(turn, actor, P):
    i = int(actor[-1]); f = P.obs()
    mine = [u for u in f['army'] if u['owner'] == actor]
    if turn == 2 and i == 0: P.do("build", {"site": f['building_sites']['sites'][0]['id'], "kind": "native:militia-camp"})
    if turn == 3 and i == 0: P.do("declare_war", {"target": N(1)})
    if turn >= 6 and i == 0:
        for s in f['building_sites']['sites']:
            for kind in ("native:cavalry", "native:militia", "native:infantry"):
                r = P.do("recruit", {"site": s['id'], "kind": kind, "count": 1})
    if turn >= 8 and i == 1:
        for u in mine:
            if u.get('engaged'):
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    r = P.do("retreat", {"aid": u['id'], "x": u['x'] + dx, "y": u['y'] + dy})
                    if r.get('ok'): break
    if turn in (12, 20, 28) and i == 1 and mine:
        P.do("disband", {"ids": [mine[0]['id']]})
    if turn in (14, 30) and i == 0 and mine:
        P.do("disband", {"ids": [mine[-1]['id']]})
    if turn >= 10 and i == 0 and turn % 2 == 0:
        for u in mine[:2]:
            P.do("move", {"aid": u['id'], "x": u['x'] + 1, "y": u['y']})
scen_new.run(7005, 16, 2, script, 45, "/root/scF.json")
