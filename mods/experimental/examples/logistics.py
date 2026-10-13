# Save this source once; invoke script_run with this turn's decision parameters.
# The ruler chooses the reserve target and expenditure limit.
market = api.query('market')
buy = dict(market['buy_prices'])
stocks = {s['id']: s['amount'] for s in api.query('econ')['stocks']}
food_target = inputs.get('food_target', 40)
budget = inputs.get('budget', 100)
quantity = int(min(max(0, food_target - stocks.get('native:food', 0)),
                   dict(market['inventory'])['native:food']))
if quantity > 0:
    estimate = buy['native:food'] * quantity
    if estimate <= min(budget, stocks.get('native:gold', 0)):
        receipt = api.submit('buy', {'resource': 'native:food', 'quantity': quantity})
        print('粮食采购', quantity, '支出', receipt['data']['gold'] if receipt['ok'] else receipt['message'])
    else:
        print('资金限制，保留决策给君主')
else:
    print('无需采购或市场缺货')
