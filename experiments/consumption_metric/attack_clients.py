"""刷分攻击客户端（消费指标 E1）。

与 `ruleai.v11plus.RuleClient` 走**同一个 Player 契约**：只换策略，不换接口，
因此攻击与基线在同一宿主、同一观察权限、同一动作词汇下比较。

按发行版声明注册为 `ClientFactories.attack`，逐国用配置键 `attack` 选策略：

    {'id':'scenario:qin','name':'秦','client':'attack','attack':'costly'}

四条攻击对应论文 §1.3：
  costly       攻击 build：永远挑总价最高的建筑建
  arbitrage    攻击流通：买进立刻卖回，从不消耗
  hoard        攻击持有：能买就买，永不消耗
  recruit_spam 攻击 recruit+supply：同一格堆够建筑位开兵营，之后疯狂征兵

只使用只读查询（econ / market / building_catalog / building_sites），
**不调用 observe()** ——攻击不需要视野，也用不到，顺带避开全图视野的重复计算。
"""
from __future__ import annotations

TRADEABLE = ('native:food', 'native:wood', 'native:ore', 'native:oil',
             'native:equipment', 'native:supply')
MAX_ACT = 60                     # 单回合最多提交几个动作（防死循环，不是游戏规则）
BOOTSTRAP = ('native:watchtower', 'native:wood-power-plant')   # 堆建筑位用不烧燃料的
PLAN = '本回合按既定策略执行，不追求最优：只做本策略允许的动作，其余一概不做。'


# --------------------------------------------------------------------------
# 只读小工具
# --------------------------------------------------------------------------

def stocks(player):
    return {s['id']: s['amount'] for s in player.query('econ')['stocks']}


def prices(player):
    return dict(player.query('market')['prices'])


def sites(player):
    return player.query('building_sites')['sites']


def catalog(player):
    return {row['id']: row for row in player.query('building_catalog')}


def unit_kind(player):
    return next(iter(player.query('unit_catalog')))


def act(player, action, **arguments):
    receipt = player.submit(action, arguments)
    return receipt, receipt.get('ok', False)


def ensure_plan(player):
    """end_turn 要求先有有效国策；攻击客户端只提交一次通用国策。"""
    if not player.query('government')['plan']:
        act(player, 'plan', text=PLAN)


def end_turn(player):
    ensure_plan(player)
    act(player, 'end_turn', summary='按本策略执行完毕')


# --------------------------------------------------------------------------
# 攻击策略
# --------------------------------------------------------------------------

def total_price(kind_row, wood_price):
    """一座建筑的真实总价：金 + 木折金（挑"最贵"时要连木一起算）。"""
    cost = kind_row['costs_by_level'][0]
    return cost.get('native:gold', 0) + wood_price * cost.get('native:wood', 0)


def try_build(player, kinds):
    """按给定优先级，在任意自有地块上试建成功一座就返回。"""
    owned = sites(player)
    for kind in kinds:
        for site in owned:
            _receipt, ok = act(player, 'build', kind=kind, x=site['position'][0], y=site['position'][1])
            if ok:
                return True
    return False


def play_costly(player):
    """最贵的先建。预期死法：最贵的建筑有建筑位门槛，够不着，
    实际只能建城堡/瞭望塔这类不产出的东西 → 无产能 → 再无收入。"""
    rows = catalog(player)
    order = sorted(rows, key=lambda k: -total_price(rows[k], prices(player)['native:wood']))
    for _ in range(MAX_ACT):
        if not try_build(player, order):
            break


def buy_max(player, resource, cap=400):
    """在当前行情下尽量买入：先按单价估上限，失败就折半重试。"""
    gold = stocks(player).get('native:gold', 0)
    price = max(prices(player)[resource], 1e-9)
    quantity = min(cap, max(0, int(gold / (price * 1.15))))
    while quantity > 0:
        _receipt, ok = act(player, 'buy', resource=resource, quantity=quantity)
        if ok:
            return quantity
        quantity //= 2
    return 0


def play_arbitrage(player):
    """低买高卖、反复形式变换，从不消耗任何东西。"""
    for _ in range(MAX_ACT):
        did = False
        for resource in TRADEABLE:
            quantity = buy_max(player, resource, cap=60)
            if quantity:
                act(player, 'sell', resource=resource, quantity=quantity)
                did = True
        if not did:
            break


def play_hoard(player):
    """疯狂买入囤积，永不消耗。"""
    for _ in range(MAX_ACT):
        if not any(buy_max(player, resource) for resource in TRADEABLE):
            break


def play_recruit_spam(player):
    """先在同一格堆够建筑位开兵营，之后疯狂征兵，养不起就饿死。"""
    owned = sorted(sites(player), key=lambda s: tuple(s['position']))
    if not owned:
        return
    tile = owned[0]                      # 固定一格：建筑位门槛按格累计
    x, y = tile['position']
    used = sum(tile.get('buildings', {}).values()) + sum(tile.get('pending', {}).values())
    if used >= 3:
        _receipt, ok = act(player, 'build', kind='native:barracks', x=x, y=y)
        if not ok:
            return                        # 兵营没搭起来＝没让它攻击，不算攻击失败
    else:
        act(player, 'build', kind=BOOTSTRAP[min(used, len(BOOTSTRAP) - 1)], x=x, y=y)
        return
    kind = unit_kind(player)
    for _ in range(MAX_ACT):
        for resource in ('native:food', 'native:equipment'):
            buy_max(player, resource, cap=200)
        _receipt, ok = act(player, 'recruit', kind=kind, x=x, y=y)
        if not ok:
            break


POLICIES = {'costly': play_costly, 'arbitrage': play_arbitrage,
            'hoard': play_hoard, 'recruit_spam': play_recruit_spam}


# --------------------------------------------------------------------------

class AttackClient:
    """发行版声明的 attack 客户端：契约与 RuleClient 相同。"""
    kind = 'attack'
    version = 'attack-1'

    def __init__(self, cfg, *, record=None, services=None):
        self.policy = (cfg or {}).get('attack')
        if self.policy not in POLICIES:
            raise ValueError(f'Unknown attack policy: {self.policy!r}')
        if record is not None and record.get('state', {}).get('policy') != self.policy:
            raise ValueError('Attack client state does not match declared policy')
        self.turns = 0

    def to_save(self):
        return {'kind': self.kind, 'version': self.version,
                'state': {'policy': self.policy, 'turns': self.turns}}

    def play(self, player, *, emit=None, head=None, on_call=None, **kwargs):
        POLICIES[self.policy](player)
        end_turn(player)
        self.turns += 1
        return []


def normalize_config(cfg, *, defaults=None, overrides=None, path='config', runtime=None):
    """发行版声明用的配置模式：只多一个 `attack` 键。"""
    from engine.config import Field, Schema
    return Schema({'attack': Field((str,), choices=tuple(POLICIES))}).normalize(
        cfg, defaults=defaults, overrides=overrides, path=path)


normalize_config.fields = ('attack',)

