import sys, json; sys.path.insert(0,"/root")
import scen_new
def script(turn, actor, P):
    n0 = actor.endswith("n0"); n1 = actor.endswith("n1")
    if turn == 3 and n0:
        P.do("declare_war", {"target": "scenario:n1"})
        P.do("offer_peace", {"target": "scenario:n1", "kind": "white"})     # 同回合：战争未生效
        P.do("declare_war", {"target": "scenario:n1"})                       # 同回合重复提交
    if turn == 3 and n1: P.do("declare_war", {"target": "scenario:n0"})      # 互相宣战
    if turn == 4 and n1:
        P.do("offer_peace", {"target": "scenario:n0", "kind": "demand", "gold": 30, "truce": 3})
    if turn == 4 and n0: P.do("offer_peace", {"target": "scenario:n1", "kind": "pay", "gold": 99999})  # 付不起
    if turn == 5 and n0:
        for o in P.q("diplomacy")["offers"]:
            if o.get("can_accept"): P.do("accept_peace", {"offer": o["id"]})
    if turn == 5 and n1: P.do("declare_war", {"target": "scenario:n0"})      # 休战内
    if turn == 6 and n0: P.do("accept_peace", {"offer": 999})
scen_new.run(7005, 16, 2, script, 12, "/root/scB.json")
