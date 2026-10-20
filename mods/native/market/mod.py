from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import GameMod, ModError, RuleSection, Stage
from .contracts import BufferPolicy, Listing
from .objects import Market


class MarketMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration, path)
        self.currency = content_id(declaration['Currency'])
        self.listings = tuple(Listing(row['Resource'], row['BasePrice'], row['Depth'], row.get('InitialInventory',0))
                              for row in declaration['Listings'])
        if len({item.resource for item in self.listings}) != len(self.listings):
            raise ModError('Duplicate market listing')
        if self.currency in {item.resource for item in self.listings}:
            raise ModError('Market currency cannot be a commodity')
        buffer = dict(declaration['Buffer'])
        if set(buffer) != {'Elasticity','Flow','Refill','Spread','MinPrice','MaxPrice'}:
            raise ModError('Invalid buffer policy')
        try:
            self.policy = BufferPolicy(buffer['Elasticity'], buffer['Flow'], buffer['Refill'],
                                       buffer['Spread'], buffer['MinPrice'], buffer['MaxPrice'])
        except ValueError as exc:
            raise ModError(f'Invalid buffer policy: {exc}') from exc
        for listing in self.listings:
            if not self.policy.low <= listing.base_price <= self.policy.high:
                raise ModError('Opening price outside numerical limits')

    def numbers(self):return {'buffer':dict(self.declaration['Buffer']), 'listings':{r.resource:{'base_price':r.base_price,'depth':r.depth,'initial':r.initial} for r in self.listings}}
    def mixins(self):return ()

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
        world.market = Market(self.listings, self.currency, self.policy,
                              lambda method, *args: self.runtime.object_call(world, 'market:pricing', method, *args),
                              lambda *args: self.runtime.object_call(world, 'native:resource-exchange', 'exchange', *args))

    def objects(self, world):return {'native:market': world.market}
    def save_state(self, world):return world.market.to_save()
    def load_state(self, world, state):world.market.restore(state)

    def rule_sections(self, world=None):
        depth = ', '.join(f'{item.resource}={item.depth}' for item in self.listings)
        opening = ', '.join(f'{item.resource}={item.initial}' for item in self.listings)
        restock = (f'外部每期按到基准库存的缺口回补 {self.policy.refill:.0%}，只补不抽，积压不减；'
                   if self.policy.refill else
                   '外部不回补：开局库存是市场唯一一次发货，之后库存只由买卖改变，积压也不会被消化；')
        return (RuleSection('库存市场',
            f'市场是有限商品缓冲，名义深度：{depth}；开局库存：{opening}。'
            '市场有两个会变的量：库存（买卖加减）和基准库存（每期按本期净流量偏移 '
            f'{self.policy.flow} 倍，从名义深度起步）。'
            f'每期开盘按这两个量给一个公开报价：p=基准价×exp(弹性×(基准库存−库存)/基准库存)，弹性={self.policy.elasticity}。'
            '同一期内所有国家拿到同一报价，报价与先后手无关；单笔订单越大均价越高（卖出越低），'
            '因为大单扫过的缓冲更深。'
            '大额买入会把基准库存推高（市场要维持更大的书），大额卖出会把它压低，'
            '所以价格随流量逐步偏移，不会只靠挤压残余库存而失控。'
            '买入扣市场库存，卖出补市场库存；库存不足整单拒绝，不部分成交。'
            + restock +
            '生产、消费、转账不等于交付市场，不直接改变市场库存。'
            f'买卖分别在报价加减 {self.policy.spread/2:.0%} 价差，金币零头逐账户累积。'
            f'绝对数值边界为 {self.policy.low} 到 {self.policy.high}。'
            '金币结算沿用外部市场流动性，不设市场金币储备；商品交付严格受库存限制。',
            ('市场','价格','库存','基准库存','交易','market','depth'), '库存与流量共同定价'),)

    def stages(self):
        return tuple(Stage(row['Id'], self._settle, hook=row['Hook'],
                           needs=tuple(row.get('Needs', ())), provides=tuple(row.get('Provides', ())),
                           requires=tuple(row.get('Requires', ())),after=tuple(row.get('After', ())),
                           before=tuple(row.get('Before', ())))
                     for row in self.declaration.get('Stages', ()))

    @staticmethod
    def _settle(world, context):
        # The buffer alone sets the price: national production and consumption
        # are facts about a nation, not deliveries to the market.
        churn=[]
        trades=world.market._period_trades()
        for owner,resource in sorted({(r.owner,r.resource) for r in trades}):
            row=world.market.churn(owner,resource)
            if row.quantity:churn.append((owner,resource,row.quantity,row.loss))
        context['market_churn']=churn
        context['market_trades'] = world.market.settle()
