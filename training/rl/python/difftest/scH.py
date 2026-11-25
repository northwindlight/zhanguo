import sys, json; sys.path.insert(0,"/root")
import scen_new
def script(turn, actor, P):
    i = int(actor[-1])
    if turn == 1 and i == 0:
        P.do("auto_trade", {"resource": "native:wood", "sell_above": 60, "sell_limit": 20})
        P.do("auto_trade", {"resource": "native:food", "buy_below": 40, "buy_limit": 10, "reserve_gold": 300, "max_buy_price": 1.7})
        P.do("auto_trade", {"resource": "native:oil", "buy_below": 30, "sell_above": 10})
        P.do("auto_trade", {"resource": "native:ore", "buy_below": 50, "sell_above": 20})        # 非法：buy>sell? 50>20
        P.do("auto_trade", {"resource": "native:gold", "buy_below": 5})                        # 不可交易
    if turn == 1 and i == 1:
        P.do("auto_trade", {"resource": "native:equipment", "sell_above": 2, "min_sell_price": 7.8})
        P.do("auto_trade", {"resource": "native:supply", "buy_below": 25, "max_buy_price": 6.0, "buy_limit": 5})
        P.do("auto_trade", {"resource": "native:wood", "enabled": False, "sell_above": 10})
    if turn == 12 and i == 0: P.do("remove_auto_trade", {"resource": "native:wood"})
    if turn == 15 and i == 1: P.do("auto_trade", {"resource": "native:supply", "buy_below": 25, "sell_above": 40, "min_sell_price": 9.0})
scen_new.run(7005, 16, 2, script, 30, "/root/scH.json")
