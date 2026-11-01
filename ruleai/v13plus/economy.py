"""Defer discretionary sales and finance complete recipes from usable surplus.

This keeps v13's market scenarios and preference weights. Planning and funding
use the same executable transaction package, so a candidate cannot win a score
and then fail merely because an earlier surplus sale forbids buying its input.
"""
import math

from ruleai.v11plus import apimap
from ruleai.v13.economy import (Planner as BasePlanner, GOLD, WOOD, SUPPLY,
    EQUIPMENT, stocks, quote, operating_targets)


class Planner(BasePlanner):
    def recruit(self):
        super().recruit()
        # Investment must reserve the upkeep of troops just commissioned too.
        self.military=self.ledger.query('econ')['preview'].get('military',{})

    def release_surplus(self, targets):
        # Selling is postponed until actual construction and recruitment are
        # known. A funding package may sell only what that package can spare.
        pass

    def candidates(self):
        result=[]
        for site in self.sites['sites']:
            plan=self.player.call_api('native:buildings','planning',{
                'x':site['position'][0], 'y':site['position'][1]})
            for row in plan['buildings']:
                definition=self.catalog[row['kind']]
                if row['reason'] is not None or row['price'] is None:
                    continue
                if definition['outputs'] or definition['energy_output'] or 'gold_base' in definition['effects']:
                    result.append((site,row['kind'],dict(row['price']['costs'])))
        return result

    def _traded(self, side):
        return {r['data']['resource'] for r in self.ledger.acts
                if r['ok'] and r['action']==side and isinstance(r.get('data'),dict)}

    def funding(self, targets, reserve=0):
        """Pure preflight, including a conservative coin for each transaction."""
        inventory=stocks(self.ledger)
        gaps={r:math.ceil(n-inventory.get(r,0)) for r,n in targets.items()
              if r!=GOLD and n>inventory.get(r,0)}
        if self._traded('sell').intersection(gaps):
            return None
        need=targets.get(GOLD,0)+reserve
        for resource,quantity in gaps.items():
            price=quote(self.ledger,resource,quantity,'buy')
            if price is None:
                return None
            need+=price+1
        cash=inventory.get(GOLD,0)
        sales={}
        if cash<need:
            # Preserve actual current obligations and completed/pending plants
            # before proposing a financing sale. Additional project inputs are
            # already included in targets by the parent planner.
            military=self.ledger.query('econ')['preview'].get('military',{})
            keep=operating_targets(self.catalog,self.buildings(),military,
                                   self.prices,self.profile.reserve)
            bought=self._traded('buy')
            for resource in self.sell:
                if resource in gaps or resource in bought:
                    continue
                excess=math.floor(inventory.get(resource,0)-max(targets.get(resource,0),keep.get(resource,0)))
                cap=max(0,math.ceil(excess*self.profile.release))
                if not cap:
                    continue
                revenue=quote(self.ledger,resource,cap,'sell')
                if revenue is None or revenue<=1:
                    continue
                quantity=cap
                if cash+revenue-1>=need:
                    low,high=1,cap
                    while low<high:
                        mid=(low+high)//2
                        if cash+quote(self.ledger,resource,mid,'sell')-1>=need:
                            high=mid
                        else:
                            low=mid+1
                    quantity=low
                    revenue=quote(self.ledger,resource,quantity,'sell')
                sales[resource]=quantity
                cash+=revenue-1
                if cash>=need:
                    break
        if cash<need:
            return None
        if self.ledger.max_actions is not None and len(self.ledger.acts)+len(sales)+len(gaps)>self.ledger.max_actions:
            return None
        return sales,gaps

    def affordable(self, recipe, reserve):
        return self.funding(recipe,reserve) is not None

    def fund(self, targets, reserve=0):
        package=self.funding(targets,reserve)
        if package is None:
            return False
        for side,orders in zip(('sell','buy'),package):
            for resource,quantity in orders.items():
                if not self.ledger.do(side,resource=resource,quantity=quantity):
                    return False
        self.model.order_offset=dict(self.own_net)
        return True

    def finish(self):
        try:
            self.sites=apimap.query(self.player,'building_sites')
            military=self.ledger.query('econ')['preview'].get('military',{})
            keep=operating_targets(self.catalog,self.buildings(),military,
                                   self.prices,self.profile.reserve)
            # Keep next period's largest legal construction wood requirement,
            # as v13 does, but use actual official quotes and occupied quotas.
            for site in self.sites['sites']:
                plan=self.player.call_api('native:buildings','planning',{
                    'x':site['position'][0],'y':site['position'][1],'queued':True})
                wood=max((row['price']['costs'].get(WOOD,0) for row in plan['buildings']
                          if row['price'] is not None and row['reason'] is None),default=0)
                keep[WOOD]=keep.get(WOOD,0)+wood
            infantry=apimap.query(self.player,'unit_catalog').get('native:infantry',{})
            recruiters=sum(b['kind']=='native:barracks' for b in self.buildings())
            for resource,amount in infantry.get('recruit',{}).items():
                if resource!=GOLD:
                    keep[resource]=keep.get(resource,0)+recruiters*amount
            bought=self._traded('buy')
            for resource,amount in stocks(self.ledger).items():
                if resource not in self.sell or resource in bought or self.ledger.full():
                    continue
                excess=max(0,math.floor(amount-keep.get(resource,0)))
                quantity=math.ceil(excess*self.profile.release)
                if quantity:
                    self.ledger.do('sell',resource=resource,quantity=quantity)
        finally:
            super().finish()


def run(ledger, state):
    planner=Planner(ledger,state)
    try:
        planner.operations()
        planner.recruit()
        planner.invest()
    finally:
        planner.finish()
