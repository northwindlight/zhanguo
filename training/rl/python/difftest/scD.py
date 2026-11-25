import sys, json; sys.path.insert(0,"/root")
import scen_new
N = lambda i: f"scenario:n{i}"
def offers(P): return [o for o in P.q("diplomacy")["offers"] if o.get("can_accept")]
def script(turn, actor, P):
    i = int(actor[-1])
    if turn == 2 and i == 0: P.do("propose_vassalage", {"target": N(1)})
    if turn == 3 and i == 1:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
    if turn == 5 and i == 1: P.do("gift", {"target": N(2), "resource": "native:wood", "quantity": 3})   # 附庸对外赠礼（应被拒）
    if turn == 5 and i == 0: P.do("gift", {"target": N(2), "resource": "native:gold", "quantity": 30})
    if turn == 6 and i == 2: P.do("send_letter", {"target": N(0), "text": "你好"*30})
    if turn == 6 and i == 0: P.do("share_map", {"target": N(2)}); P.do("spy", {"target": N(2)})
    if turn == 9 and i == 2: P.do("submit_vassalage", {"target": N(0)})        # 向已有附庸的宗主称臣
    if turn == 10 and i == 0:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
    if turn == 12 and i == 0: P.do("release_vassal", {"target": N(1)})
    if turn == 13 and i == 2: P.do("independence_war", {})
    if turn == 14 and i == 2: P.do("declare_war", {"target": N(0)})
    if turn == 16 and i == 1: P.do("submit_vassalage", {"target": N(2)})
    if turn == 17 and i == 2:
        for o in offers(P): P.do("accept_proposal", {"offer": o["id"]})
scen_new.run(7005, 16, 3, script, 30, "/root/scD.json")
