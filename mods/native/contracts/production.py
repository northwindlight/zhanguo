"""Resource assets held by formal production orders, separate from spendable stock."""
from dataclasses import replace


def reserved_costs(runtime, call, owner):
    result={}
    for capability in ('native:buildings','native:army'):
        if capability not in runtime.capabilities:continue
        for key,amount in call(capability,'reserved_costs',owner):
            result[key]=result.get(key,0)+amount
    return result


def inventory_assets(runtime, call, owner):
    stocks=call('native:resource-inspection','snapshot',owner)
    prepaid=reserved_costs(runtime,call,owner)
    return tuple(replace(row,amount=row.amount+prepaid.get(row.id,0)) for row in stocks)
