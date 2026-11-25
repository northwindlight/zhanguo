import sys, json; sys.path.insert(0,"/root")
import scen_new
def script(turn, actor, P):
    n0 = actor.endswith("n0"); n1 = actor.endswith("n1")
    if turn == 3 and n0: P.do("declare_war", {"target": "scenario:n1"})
    if turn == 4 and n0: P.do("declare_war", {"target": "scenario:n1"})   # 重复宣战（应被拒）
    if turn == 12 and n1: P.do("offer_peace", {"target": "scenario:n0", "kind": "pay", "gold": 20, "truce": 6, "note": "x"})
    if turn == 13 and n0:
        offers = P.q("diplomacy")["offers"]
        for o in offers:
            if o.get("can_accept"): P.do("accept_peace", {"offer": o["id"]})
    if turn == 14 and n0: P.do("declare_war", {"target": "scenario:n1"})   # 休战期内宣战（应被拒）
    if turn == 25 and n0: P.do("declare_war", {"target": "scenario:n1"})   # 休战到期后宣战
    if turn == 27 and n1: P.do("offer_peace", {"target": "scenario:n0", "kind": "white"})
    if turn == 28 and n0: P.do("reject_peace", {"offer": P.q("diplomacy")["offers"][0]["id"]}) if P.q("diplomacy")["offers"] else None
    if turn == 5 and n1: P.do("gift", {"target": "scenario:n0", "resource": "native:wood", "quantity": 5})
    if turn == 6 and n0: P.do("borrow", {})
scen_new.run(7005, 16, 2, script, 40, "/root/scA.json")
