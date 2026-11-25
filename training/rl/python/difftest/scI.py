import sys, json; sys.path.insert(0,"/root")
import scen_new
N = lambda i: f"scenario:n{i}"
def offers(P): return [o for o in P.q("diplomacy")["offers"] if o.get("can_accept")]
def script(turn, actor, P):
    i = int(actor[-1])
    if turn == 3 and i == 0: P.do("declare_war", {"target": N(1)})
    if turn == 5 and i == 1: P.do("offer_peace", {"target": N(0), "kind": "submit", "gold": 5})      # 带金：应被拒
    if turn == 5 and i == 1: P.do("offer_peace", {"target": N(0), "kind": "submit", "truce": 4})
    if turn == 6 and i == 0:
        for o in offers(P): P.do("accept_peace", {"offer": o["id"]})
    if turn == 12 and i == 2: P.do("bloc_found", {"bloc": "scenario:b", "name": "丙盟", "tos": [N(0)]})
    if turn == 13 and i == 0:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
    if turn == 20 and i == 2: P.do("declare_war", {"target": N(3)})
    if turn == 24 and i == 3: P.do("offer_peace", {"target": N(2), "kind": "subjugate"})
    if turn == 25 and i == 2:
        for o in offers(P): P.do("accept_peace", {"offer": o["id"]})
scen_new.run(7003, 20, 4, script, 34, "/root/scI.json")
