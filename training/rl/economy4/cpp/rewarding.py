"""Training-only territory GDP. Never changes game GDP or observation features."""
import math


def territory_gdp(extra_land):
    if type(extra_land) is not int or extra_land < 0:
        raise ValueError('extra territory must be a nonnegative integer')
    return sum((10,5,3,2)[:extra_land]) + max(0,extra_land-4)


def reward_turns(gdp,land,initial_land,mode='holding'):
    if len(gdp)!=len(land) or not gdp or type(initial_land) is not int or initial_land<=0:
        raise ValueError('GDP, territory and initial ownership must align')
    if mode not in ('holding','gain'):raise ValueError('Unknown territory reward mode')
    if any(type(n) is not int or n<0 for n in land):raise ValueError('Invalid held territory')
    if any(not math.isfinite(float(x)) for x in gdp):raise ValueError('Nonfinite actual GDP')
    bonus=[];peak=0
    for count in land:
        extra=max(0,count-initial_land)
        if mode=='holding':bonus.append(territory_gdp(extra))
        else:
            now=max(peak,extra);bonus.append(territory_gdp(now)-territory_gdp(peak));peak=now
    return [float(g)+b for g,b in zip(gdp,bonus,strict=True)],bonus
