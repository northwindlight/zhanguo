import sys, json; sys.path.insert(0,"/root")
import scen_new
N = lambda i: f"scenario:n{i}"
def offers(P): return [o for o in P.q("diplomacy")["offers"] if o.get("can_accept")]
def script(turn, actor, P):
    i = int(actor[-1])
    if turn == 1 and i == 0: P.do("propose_vassalage", {"target": N(1)})
    if turn == 2 and i == 1:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
    if turn == 3 and i == 2: P.do("submit_vassalage", {"target": N(0)})
    if turn == 4 and i == 0:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
scen_new.run(7003, 20, 3, script, 70, "/root/scG.json")
