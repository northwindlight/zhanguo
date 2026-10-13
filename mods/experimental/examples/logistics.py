# Save this source once; invoke script_run with this turn's decision parameters.
# The ruler chooses the reserve target and expenditure limit.
market = api.query('market')
stocks = {s['id']: s['amount'] for s in api.query('econ')['stocks']}
food_target = inputs.get('food_target', 40)
budget = inputs.get('budget', 100)
quantity = int(min(max(0, food_target - stocks.get('native:food', 0)),
                   dict(market['inventory'])['native:food']))
if quantity > 0:
    args = {'resource': 'native:food', 'quantity': quantity, 'side': 'buy'}
    quote = api.query('market_quote', args)
    if quote['gold'] <= min(budget, stocks.get('native:gold', 0)):
        api.submit('buy', {'resource': 'native:food', 'quantity': quantity})
        print('粮食采购', quantity, '支出', quote['gold'])
    else:
        print('资金限制，保留决策给君主')
else:
    print('无需采购或市场缺货')
