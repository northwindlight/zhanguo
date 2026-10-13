"""Distribution-injected research forecasts; native query remains the default."""
def assess(player, cell, units, *, forecast=None):
    if forecast is not None:return forecast(player,cell,units)
    return player.query('battle_estimate',{'ids':[u['id'] for u in units],'x':cell[0],'y':cell[1]})

def needed(player, cell, units, *, forecast=None):
    for count in range(1,len(units)+1):
        result=assess(player,cell,units[:count],forecast=forecast)
        if result['known'] and result['winnable']:return count,True
    return len(units)+1,False
