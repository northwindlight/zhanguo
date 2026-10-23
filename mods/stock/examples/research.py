# Store in slot="research", unlock it with compute(kind="research"), then run.
quotes = api.call('stock:market', 'quotes')
book = api.call('stock:portfolio', 'holdings')
print('现金 / 可用现金 / 银行贷款：', book['cash'], book['available_cash'], book['debt'])
for row in quotes:
    history = api.call('stock:market', 'history', symbol=row['symbol'], limit=60)
    total = 0.0
    for price in history:
        total = total + price
    print(row['symbol'], '已知收盘均价', total / len(history), '当前标记价', row['price'])
reports = api.call('stock:intelligence', 'status')['reports']
for report in reports:
    print('已购谍报：', report['target'], report['round'], report['tick'], report['snapshot'])
