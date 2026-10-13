"""Forecasts are native queries, not client copies of damage formulas."""
def assess(player, cell, units):
    return player.query('battle_estimate',{'ids':[u['id'] for u in units],'x':cell[0],'y':cell[1]})

def needed(player, cell, units):
    for count in range(1,len(units)+1):
        forecast=assess(player,cell,units[:count])
        if forecast['known'] and forecast['winnable']:return count,True
    return len(units)+1,False
