"""Window-level actor and critic for a six-commodity dealer."""
from dataclasses import dataclass
import math

import torch
from torch import nn
from torch.distributions import Categorical


GOODS = ("food", "wood", "ore", "oil", "equipment", "supply")
GLOBAL = ("turn", "window", "turns_left", "active_nations", "cash", "credit_available",
          "debt", "funding_cost", "realized_margin", "drawdown", "fill_buy", "fill_sell")
LOCAL = ("base_price", "reference", "reference_age", "inventory", "reserved", "unit_cost",
         "inventory_age", "last_bid", "last_ask", "last_price", "last_volume",
         "dealer_bought", "dealer_sold", "unfilled_buy", "unfilled_sell", "buyers", "sellers",
         "buy_ema", "sell_ema", "input_cost_index", "substitute_price", "forecast_confidence")
CASH_SHARE = (0, .25, .5, 1)
CENTER = (.5, .75, 1., 1.4, 2.)
SPREAD = (.02, .05, .10, .20)
DEPTH_STEP = (.04, .12, .24)
BUY_WEIGHT = (0, 1, 2, 4)
SELL_SHARE = (0, .125, .25, .5, 1)
HEAD_SIZES = tuple(map(len, (CENTER, SPREAD, DEPTH_STEP, BUY_WEIGHT, SELL_SHARE)))


class DealerPolicy(nn.Module):
    """One shared commodity encoder and one persistent memory per market/world."""
    def __init__(self):
        super().__init__()
        self.item = nn.Linear(len(LOCAL), 96)
        self.good_id = nn.Embedding(len(GOODS), 96)
        self.context = nn.TransformerEncoderLayer(96, 4, 192, dropout=0.,
                                                  batch_first=True, norm_first=True)
        self.memory = nn.GRUCell(96 + len(GLOBAL), 128)
        self.readout = nn.Sequential(nn.Linear(96 + 128, 96), nn.SiLU())
        self.order_heads = nn.ModuleList(nn.Linear(96, n) for n in HEAD_SIZES)
        self.cash_head = nn.Linear(128, len(CASH_SHARE))
        self.value = nn.Sequential(nn.Linear(128, 64), nn.SiLU(), nn.Linear(64, 1))
        self.flow = nn.Linear(96, 6)  # log1p expected volumes, 3 horizons times 2 sides

    def forward(self, global_obs, local_obs, previous=None):
        """Observations are normalized using the units defined in the training contract."""
        items = self.context(self.item(local_obs) + self.good_id.weight)
        state = self.memory(torch.cat((items.mean(dim=1), global_obs), dim=-1), previous)
        context = state[:, None].expand(-1, len(GOODS), -1)
        item_state = self.readout(torch.cat((items, context), dim=-1))
        logits = [head(item_state) for head in self.order_heads]
        return self.cash_head(state), logits, self.value(state).squeeze(-1), self.flow(item_state), state

    @staticmethod
    def decide(cash_logits, order_logits, chosen=None):
        """Log probability of the raw joint instruction, before integer conversion."""
        cash = Categorical(logits=cash_logits)
        heads = [Categorical(logits=logit) for logit in order_logits]
        if chosen is None:
            cash_index = cash.sample()
            items = torch.stack([head.sample() for head in heads], dim=-1)
        else:
            cash_index, items = chosen
        lp = cash.log_prob(cash_index)
        ent = cash.entropy()
        for i, head in enumerate(heads):
            lp = lp + head.log_prob(items[..., i]).sum(dim=-1)
            ent = ent + head.entropy().sum(dim=-1)
        return (cash_index, items), lp, ent


@dataclass(frozen=True)
class Order:
    good: str
    side: str
    price_ticks: int   # hundredths of a coin
    quantity: int


def tiers(total):
    first, second = total // 2, 3 * total // 10
    return first, second, total-first-second


def orders(cash_index, instructions, anchors_ticks, free_stock, spendable_coins):
    """Build a feasible public book before any private orders arrive."""
    if len(instructions) != len(GOODS) or len(anchors_ticks) != len(GOODS) or len(free_stock) != len(GOODS):
        raise ValueError("Expected six commodities")
    if not 0 <= cash_index < len(CASH_SHARE) or type(spendable_coins) is not int or spendable_coins < 0:
        raise ValueError("Invalid cash budget")
    for row, anchor, stock in zip(instructions, anchors_ticks, free_stock, strict=True):
        if len(row) != len(HEAD_SIZES) or any(not 0 <= i < size for i, size in zip(row, HEAD_SIZES)):
            raise ValueError("Invalid action index")
        if type(anchor) is not int or anchor < 1 or type(stock) is not int or stock < 0:
            raise ValueError("Expected positive anchor ticks and nonnegative integer inventory")
    weights = [BUY_WEIGHT[row[3]] for row in instructions]
    total_weight = sum(weights)
    budget = math.floor(spendable_coins * CASH_SHARE[cash_index])
    result = []
    for g, (row, weight) in enumerate(zip(instructions, weights, strict=True)):
        center, spread, step, _, supply = row
        mid = anchors_ticks[g] * CENTER[center]
        width, spacing = SPREAD[spread], DEPTH_STEP[step]
        cash_limit = budget * weight // total_weight if total_weight else 0
        sale_limit = math.floor(free_stock[g] * SELL_SHARE[supply])
        for k, (coins, units) in enumerate(zip(tiers(cash_limit), tiers(sale_limit), strict=True)):
            bid = max(1, math.floor(mid * (1-width) / (1+spacing)**k))
            ask = max(2, math.ceil(mid * (1+width) * (1+spacing)**k))
            buy_quantity = coins * 100 // bid
            if buy_quantity:
                result.append(Order(GOODS[g], "buy", bid, buy_quantity))
            if units:
                result.append(Order(GOODS[g], "sell", ask, units))
    return result
