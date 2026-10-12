from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import GameMod, ModError, Stage
from .contracts import Listing
from .objects import Market


class MarketMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration, path)
        self.currency = content_id(declaration['Currency'])
        self.listings = tuple(Listing(row['Resource'], row['BasePrice'], row['Depth'])
                              for row in declaration['Listings'])
        if len({item.resource for item in self.listings}) != len(self.listings):
            raise ModError('Duplicate market listing')
        if self.currency in {item.resource for item in self.listings}:
            raise ModError('Market currency cannot be a commodity')

    def mixins(self):return ()
    def state_keys(self):return ('market',)
    def needs(self):return ('native:resource-exchange', 'market:pricing')
    def provides(self):return ('native:market',)

    def initialize_world(self, world):
        for key in (self.currency, *(item.resource for item in self.listings)):
            try:
                cls = self.runtime.content.definition(key)
            except KeyError as exc:
                raise ModError(f'Market resource is not registered: {key}') from exc
            if not isinstance(cls, type) or not issubclass(cls, Resource):
                raise ModError(f'Market listing is not a resource: {key}')
            if key != self.currency and not cls.TRADEABLE:
                raise ModError(f'Resource is not tradeable: {key}')
        world.market = Market(self.listings, self.currency,
                              lambda method, *args: self.runtime.object_call(world, 'market:pricing', method, *args),
                              lambda *args: self.runtime.object_call(world, 'native:resource-exchange', 'exchange', *args))

    def objects(self, world):return {'native:market': world.market}
    def save_state(self, world):return world.market.to_save()
    def load_state(self, world, state):world.market.restore(state)

    def stages(self):
        return tuple(Stage(row['Id'], self._settle, hook=row['Hook'],
                           needs=tuple(row.get('Needs', ())), provides=tuple(row.get('Provides', ())),
                           requires=tuple(row.get('Requires', ())),after=tuple(row.get('After', ())),
                           before=tuple(row.get('Before', ())))
                     for row in self.declaration.get('Stages', ()))

    @staticmethod
    def _settle(world, context):
        # Production/consumption are explicit period inputs, never inferred
        # from purchases, transfers or arbitrary resource credits.
        data = context.get('market', {})
        context['market_trades'] = world.market.settle(
            data.get('production', {}), data.get('consumption', {}),
            participants=data.get('participants', world.market.participants))
