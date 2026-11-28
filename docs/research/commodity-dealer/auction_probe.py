"""Small sealed-window examples. All prices are integer ticks, with reserved orders."""
from collections import Counter, defaultdict
from itertools import permutations
from typing import NamedTuple


class Order(NamedTuple):
    owner: str
    side: str
    limit: int
    quantity: int


def allocate(orders, price, volume, buyer, rotation):
    levels = defaultdict(Counter)
    for o in orders:
        if o.side == ("buy" if buyer else "sell") and (o.limit >= price if buyer else o.limit <= price):
            levels[o.limit][o.owner] += o.quantity
    fills = Counter()
    for limit in sorted(levels, reverse=buyer):
        orders_at_price = levels[limit]
        total = sum(orders_at_price.values())
        filled = min(total, volume)
        shares = {owner: (filled * q) // total for owner, q in orders_at_price.items()}
        remainder = filled - sum(shares.values())
        rank = sorted(orders_at_price,
                      key=lambda owner: (-(filled * orders_at_price[owner] % total), rotation.index(owner)))
        for owner in rank[:remainder]:
            shares[owner] += 1
        fills.update(shares)
        volume -= filled
        if volume == 0:
            break
    return +fills


def clear(orders, anchor, rotation):
    # Fixtures already reserve cash and goods, and have no self-crossing orders.
    # The exhaustive grid is an independent oracle for these small examples.
    def objective(p):
        demand = sum(o.quantity for o in orders if o.side == "buy" and o.limit >= p)
        supply = sum(o.quantity for o in orders if o.side == "sell" and o.limit <= p)
        return min(demand, supply), -abs(demand-supply), -abs(p-anchor), -p

    prices = range(min(o.limit for o in orders), max(o.limit for o in orders)+1)
    price = max(prices, key=objective)
    volume = objective(price)[0]
    if not volume:
        return None, {}, {}
    buys = allocate(orders, price, volume, True, rotation)
    sells = allocate(orders, price, volume, False, rotation)
    assert sum(buys.values()) == sum(sells.values()) == volume
    return price, buys, sells


if __name__ == "__main__":
    bids = [Order("dealer", "buy", p, 10) for p in (5, 4, 3)]
    sells = [Order(n, "sell", 1, 10) for n in "ABC"]
    rotation = ["B", "C", "A", "dealer", "factory"]
    for n, p in [(1, 5), (2, 4), (3, 3)]:
        for group in permutations(sells, n):
            price, bought, sold = clear([*bids, *group], 5, rotation)
            assert price == p and bought["dealer"] == n*10
            assert all(sold[o.owner] == 10 for o in group)
    market = [Order("factory", "buy", 5, 20), *bids, *sells]
    assert clear(market, 5, rotation)[0] == 5
    # Partial fills with a tie and an extra unit use the same rotation after splitting.
    partial = [Order("dealer", "buy", 3, 11), *sells]
    ref = clear(partial, 3, rotation)
    split = [*partial[:-1], Order("C", "sell", 1, 4), Order("C", "sell", 1, 6)]
    assert ref == clear(split, 3, rotation) and ref[2] == {"A": 3, "B": 4, "C": 4}
    print("No outside factory: 10/20/30 offered clear at 5/4/3.")
    print("With 20 units of real factory demand: 30 offered clear at 5.")
    print("Arrival permutations, split orders, partial fills and cash/quantity balance passed.")
