"""Explicit valuation policy: cash face value, market, declared price, unpriced."""
from collections.abc import Mapping
from types import MappingProxyType

from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import GameMod, ModError


from mods.native.economy_reports.contracts import Valuation


class ValuationService:
    def __init__(self, currency, prices, call):
        self._currency,self._prices,self._call = currency,MappingProxyType(dict(prices)),call

    def quote(self, amounts, side='mid'):
        if not isinstance(amounts,Mapping):raise ValueError('Expected valuation amounts')
        if side not in ('mid','buy','sell'):raise ValueError('Invalid valuation side')
        market = self._call('native:market','snapshot')
        prices = dict(market.prices)
        if market.currency!=self._currency:raise ValueError('Valuation and market currencies differ')
        result = []
        for key,amount in amounts.items():
            content_id(key);Resource.finite_number(amount)
            # Even an unpriced resource must be a registered concrete type.
            self._call('native:resource-inspection','value',key,0,0)
            if key==self._currency:price,source = 1,'cash'
            elif key in prices:
                price=prices[key] if side=='mid' else self._call('native:market','unit_price',key,side)
                source='market' if side=='mid' else 'market_'+side
            elif key in self._prices:price,source = self._prices[key],'declared'
            else:price,source = None,'unpriced'
            if price is None:value = 0 if amount==0 else None
            else:
                value = self._call('native:resource-inspection','value',key,abs(amount),price)
                if amount<0:value = -value
                Resource.finite_number(value)
            result.append(Valuation(key,amount,price,value,source))
        return tuple(result)


class ValuationMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration,path)
        self.currency = content_id(declaration['Currency'])
        self.prices = dict(declaration.get('Prices',{}))
        for key,value in self.prices.items():
            content_id(key);Resource.finite_number(value)
            if value<0:raise ModError('Negative declared valuation price')
        if self.currency in self.prices:raise ModError('Currency uses face value, not a declared commodity price')

    def mixins(self):return ()
    def needs(self):return ('native:resource-inspection','native:market')
    def provides(self):return ('economy:valuation',)

    def initialize_world(self, world):
        for key in (self.currency,*self.prices):
            try:definition = self.runtime.content.definition(key)
            except KeyError as exc:raise ModError(f'Valuation resource is not registered: {key}') from exc
            if not isinstance(definition,type) or not issubclass(definition,Resource):
                raise ModError(f'Valuation definition is not a resource: {key}')
    def objects(self, world):
        return {'economy:valuation':ValuationService(self.currency,self.prices,
                lambda capability,method,*args:self.runtime.object_call(world,capability,method,*args))}
