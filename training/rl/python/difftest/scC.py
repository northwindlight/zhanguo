import sys, json; sys.path.insert(0,"/root")
import scen_new
N = lambda i: f"scenario:n{i}"
def me(actor): return int(actor[-1])
def offers(P): return [o for o in P.q("diplomacy")["offers"] if o.get("can_accept")]
def votes(P): return P.q("diplomacy")["votes"]
def script(turn, actor, P):
    i = me(actor)
    if turn == 2 and i == 0: P.do("guarantee", {"target": N(1)})
    if turn == 3 and i == 0: P.do("propose_defense", {"target": N(2)})
    if turn == 4 and i == 2:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
    if turn == 5 and i == 1: P.do("cancel_guarantee", {"target": N(0)})        # 非保障方撤销（应被拒）
    if turn == 6 and i == 3: P.do("propose_defense", {"target": N(1)})
    if turn == 7 and i == 1:
        for o in offers(P): P.do("reject_proposal", {"offer": o["id"]})
    if turn == 8 and i == 0: P.do("bloc_found", {"bloc": "scenario:alpha", "name": "甲盟", "tos": [N(1)]})
    if turn == 9 and i == 1:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
    if turn == 11 and i == 2: P.do("bloc_join", {"bloc": "scenario:alpha"})
    if turn == 12 and i in (0, 1):
        for v in votes(P): P.do("vote", {"vote": v["id"], "accept": True})
    if turn == 14 and i == 3: P.do("bloc_join", {"bloc": "scenario:alpha"})
    if turn == 15 and i in (0, 1, 2) and (i == 2 or True):
        for v in votes(P): P.do("vote", {"vote": v["id"], "accept": {0: False, 1: True, 2: None}[i]})
    if turn == 17 and i == 2: P.do("bloc_leave", {})
    if turn == 18 and i == 0: P.do("bloc_transfer", {"target": N(1)})
    if turn == 19 and i == 1: P.do("bloc_rename", {"name": "乙盟"})
    if turn == 20 and i == 0: P.do("bloc_leave", {})                           # 现任非盟主退盟
    if turn == 21 and i == 1: P.do("declare_war", {"target": N(3)})              # 联盟宣战→投票
    if turn == 22 and i in (0, 1):
        for v in votes(P): P.do("vote", {"vote": v["id"], "accept": True})
    if turn == 24 and i == 3: P.do("offer_peace", {"target": N(1), "kind": "white", "truce": 4})
scen_new.run(7005, 20, 4, script, 30, "/root/scC.json")
