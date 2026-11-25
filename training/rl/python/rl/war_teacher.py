"""W3, a fog-limited scripted teacher for the Python RL environment."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import math
import re
from weakref import WeakKeyDictionary

import mp
from balance import BUILDINGS, MARKET, MAX_SLOTS, SPY_COST, TERRAIN_STATS, UNIT_TYPES

from .obs import visible_set
from .opponents import _can_declare
from .score import REF, TILE_VALUE, army_value, building_value


EXTRACTORS = tuple(name for name, spec in BUILDINGS.items() if spec["kind"] in ("extract", "gold"))
BLOCKED = {"高山", "海洋"}
EPS = 1e-8
INTEL_REFRESH = 10


@dataclass
class Forecast:
    income: float
    supply: float
    power: float
    demand: int


def inventory_value(world, name):
    return sum(world.res(name, good) * price for good, price in REF.items())


def forecast(world, name, stock=None):
    """Fixed-price net output from completed buildings, in engine settlement order."""
    stocks = dict(stock) if stock is not None else {good: world.res(name, good) for good in REF}
    output = inputs = power = demand = 0
    halls = []
    plants = []
    factories = []
    for tile in world.tiles.values():
        if tile["owner"] != name:
            continue
        built = tile["buildings"]
        for building, count in built.items():
            if not count:
                continue
            spec = BUILDINGS[building]
            kind = spec["kind"]
            if kind == "extract":
                for good, quantity in spec["outputs"].items():
                    stocks[good] += count * quantity
                    output += count * quantity * REF[good]
            elif kind == "gold":
                output += count * spec["outputs"]["黄金"] * MARKET["黄金"]
            elif kind == "energy":
                plants.append((spec, count))
            elif kind == "factory":
                demand += count
                factories.append((spec, count))
            elif kind == "barracks":
                demand += count
            elif kind == "townhall":
                demand += count
                halls.append((count, sum(built.values()) - count))

    def consume(needs, count):
        nonlocal inputs
        batches = min([count, *(int(stocks[g] // q) for g, q in needs.items())])
        for good, quantity in needs.items():
            stocks[good] -= quantity * batches
            inputs += quantity * batches * REF[good]
        return batches

    for spec, count in plants:
        power += consume(spec["fuel"], count) * spec["energy_out"]
    if power >= demand:
        for spec, count in factories:
            batches = consume(spec["inputs"], count)
            for good, quantity in spec["outputs"].items():
                stocks[good] += quantity * batches
                output += quantity * batches * REF[good]
        for count, other in halls:
            output += count * (mp.building_effect("市政厅", "gold_base")
                               + other * mp.building_effect("市政厅", "gold_per_slot"))
    return Forecast(output - inputs, stocks["补给"], power, demand)


def upkeep(world, name):
    return world.supply_shortfall(name)[0]


def projected_ratio(world, name):
    income = forecast(world, name).income
    fee = upkeep(world, name) * REF["补给"]
    return fee / income if income > EPS else (math.inf if fee else 0.0)


class TurnEconomy:
    def __init__(self, world, name, limit, hoard_turns):
        self.w = world
        self.name = name
        self.limit = limit
        self.hoard_turns = hoard_turns
        self.own = world.own_tiles(name)
        self.opening_stock = inventory_value(world, name)

    def count(self, building, pending=False):
        return self.w.nation_building_count(self.name, building, include_pending=pending)

    def resource(self, good):
        return self.w.res(self.name, good)

    def buy_cost(self, good, quantity):
        if not quantity:
            return 0
        if quantity > self.w.market_inventory(good):
            return math.inf
        _, exact = self.w.market_walk(good, quantity, "buy", self.name)
        return self.w._market_cash(self.name, exact, "buy")[0]

    def buy_to(self, good, minimum, keep_gold=0):
        missing = max(0, math.ceil(minimum - self.resource(good) - EPS))
        if not missing:
            return True
        if self.buy_cost(good, missing) > self.resource("黄金") - keep_gold:
            return False
        return self.w.buy(self.name, good, missing)[0]

    def build_cost(self, site, building):
        tile = self.w.tiles[site]
        spec = BUILDINGS[building]
        cost = spec["cost"]
        if isinstance(cost, list):
            rank = tile["buildings"][building] + tile["pending"][building]
            if rank >= len(cost):
                return math.inf
            cost = cost[rank]
        cost = cost * (100 + TERRAIN_STATS[tile["terrain"]]["build_penalty"]) // 100
        if tile["buildings"].get("工程院") and building != "工程院":
            cost = cost * (100 - mp.building_effect("工程院", "build_discount")) // 100
        return cost

    def build(self, site, building, keep_gold=0):
        tile = self.w.tiles[site]
        spec = BUILDINGS[building]
        used = sum(tile["buildings"].values()) + sum(tile["pending"].values())
        count = tile["buildings"][building] + tile["pending"][building]
        resource_key = spec.get("cap_resource")
        if (tile["built_this_turn"] or used >= MAX_SLOTS or used < spec.get("min_slots", 0)
                or spec.get("limit", MAX_SLOTS) <= count
                or (resource_key is not None and tile["resources"][resource_key] <= count)):
            return False
        if not self.buy_to("木头", spec["wood"], self.build_cost(site, building) + keep_gold):
            return False
        return self.w.build(self.name, *site, building)[0]

    def capacity(self):
        supply = sum(self.count(b, True) * BUILDINGS[b]["energy_out"] for b in ("木材能源厂", "石油能源厂"))
        demand = sum(self.count(b, True) for b in BUILDINGS if BUILDINGS[b]["kind"] in ("factory", "barracks", "townhall"))
        return supply, demand

    def build_power(self, extra_demand=0, avoid=None):
        available, demand = self.capacity()
        if available >= demand + extra_demand:
            return True
        oil = demand + extra_demand >= 5 and self.count("石油厂", True) > self.count("石油能源厂", True)
        buildings = ("石油能源厂", "木材能源厂") if oil else ("木材能源厂",)
        for building in buildings:
            for site in sorted(self.own, key=lambda p: (self.build_cost(p, building), p == avoid,
                                                         self.w.tiles[p]["buildings"]["市政厅"] == 0, p)):
                if site != avoid and self.build(site, building):
                    return self.capacity()[0] >= self.capacity()[1] + extra_demand
        return False

    def place(self, building, sites, keep_gold=0):
        extra = int(BUILDINGS[building]["kind"] in ("factory", "barracks", "townhall"))
        for site in sites:
            tile = self.w.tiles[site]
            if tile["built_this_turn"]:
                continue
            minimum = BUILDINGS[building].get("min_slots", 0)
            if sum(tile["buildings"].values()) + sum(tile["pending"].values()) < minimum:
                continue
            if not extra or self.build_power(extra, avoid=site):
                if self.build(site, building, keep_gold):
                    return True
        return False

    def stock_for_essentials(self, need):
        """Account for local extraction before buying fuel, inputs and rations."""
        extracted = defaultdict(int)
        for site in self.own:
            built = self.w.tiles[site]["buildings"]
            for building in EXTRACTORS:
                for good, count in BUILDINGS[building]["outputs"].items():
                    extracted[good] += built[building] * count
        for building in ("木材能源厂", "石油能源厂"):
            for good, units in BUILDINGS[building]["fuel"].items():
                self.buy_to(good, units * self.count(building) - extracted[good])
                extracted[good] -= units * self.count(building)
        # Use the public market first for an emergency ration. Existing factories
        # keep the army supplied when the public stock runs down.
        current = forecast(self.w, self.name)
        if current.supply < need:
            self.buy_to("补给", self.resource("补给") + need - current.supply)
        if self.w.at_war(self.name):
            # Preserve one ration if a supplying factory is captured before settlement.
            self.buy_to("补给", need)
        for building in ("补给厂", "装备厂"):
            count = self.count(building)
            for good, units in BUILDINGS[building]["inputs"].items():
                self.buy_to(good, count * units - extracted[good])
                extracted[good] -= units * count

    def preferred_sites(self, building):
        return sorted(self.own, key=lambda p: (self.build_cost(p, building),
                                               -self.w.tiles[p]["buildings"]["市政厅"], p))

    def has_tower(self, site):
        tile = self.w.tiles[site]
        return bool(tile["buildings"]["瞭望塔"] or tile["pending"]["瞭望塔"])

    def invest(self, defend_site, capital_tower, frontier_tower):
        capital = next((p for p in self.own if self.w.tiles[p]["buildings"]["市政厅"]), None)
        # Capturing a city also takes over its power-consuming buildings.
        # Keep some installed headroom before opening that front.
        spare = 4 if self.w.turn >= 48 or self.w.at_war(self.name) else 0
        self.build_power(extra_demand=spare, avoid=defend_site)
        # Secure a protected recruiting site and build inside its city to raise
        # guaranteed fixed-price income. Cash protects rations and repairs.
        if capital is not None and self.w.turn <= 45 and self.count("军屯", True) == 0:
            self.place("军屯", [capital, *self.preferred_sites("军屯")], keep_gold=40)
        if defend_site is not None:
            city = self.w.tiles[defend_site]
            level = city["buildings"]["城堡"] + city["pending"]["城堡"]
            if level < 3:
                self.build(defend_site, "城堡", keep_gold=60)
        for site in (capital_tower, frontier_tower):
            if site is not None and not self.has_tower(site):
                self.build(site, "瞭望塔", keep_gold=40)

        goal_barracks = 1 if self.w.turn < 18 else (2 if self.w.turn < 51 else 4)
        if self.count("兵营", True) < goal_barracks:
            sites = sorted(self.own, key=lambda p: (-sum(self.w.tiles[p]["buildings"].values()),
                                                    self.build_cost(p, "兵营"), p))
            self.place("兵营", sites, keep_gold=45)
        need = upkeep(self.w, self.name)
        expected_supply = min(need + 2, max(2, math.floor(max(0, forecast(self.w, self.name).income)
                                                           * self.limit / REF["补给"])))
        if 2 * self.count("补给厂", True) < expected_supply:
            self.place("补给厂", self.preferred_sites("补给厂"), keep_gold=45)
        if self.w.turn >= 14 and self.count("装备厂", True) < (2 if self.w.turn < 50 else 4):
            self.place("装备厂", self.preferred_sites("装备厂"), keep_gold=60)
        if self.w.turn >= 16 and self.count("市政厅", True) < min(len(self.own), 4):
            sites = sorted(self.own, key=lambda p: (-sum(self.w.tiles[p]["buildings"].values()), p))
            self.place("市政厅", [p for p in sites if not self.w.tiles[p]["buildings"]["市政厅"]], keep_gold=115)

        projects = []
        for site in self.own:
            tile = self.w.tiles[site]
            if tile["built_this_turn"]:
                continue
            for building in EXTRACTORS:
                spec = BUILDINGS[building]
                if tile["resources"][spec["cap_resource"]] <= tile["buildings"][building] + tile["pending"][building]:
                    continue
                value = sum(quantity * (REF[good] if good in REF else MARKET["黄金"])
                            for good, quantity in spec["outputs"].items())
                value += int(bool(tile["buildings"]["市政厅"]))
                payback = (self.build_cost(site, building) + spec["wood"] * REF["木头"]) / value
                projects.append((payback, site, building))
        for payback, site, building in sorted(projects):
            if payback <= 36 or self.w.turn < 16:
                reserve = 165 if self.w.turn >= 37 else (75 if self.w.turn >= 12 else 30)
                self.build(site, building, keep_gold=reserve)

    def recruit(self, sites, kind):
        w, name = self.w, self.name
        if kind == "步" and w.grid_short.get(name):
            return
        cost = w.recruit_cost(name, kind)
        building = "兵营" if kind == "步" else "军屯"
        for site in sites:
            tile = w.tiles[site]
            if not tile["buildings"][building]:
                continue
            if kind == "步" and tile["recruited_this_turn"] >= tile["buildings"][building]:
                continue
            if kind == "民":
                if tile.get("militia_recruited_this_turn", 0):
                    continue
                stationed = [a for a in w.nation_armies(name) if (a["x"], a["y"]) == site and a["type"] == "民"]
                if len(stationed) >= tile["buildings"][building] * mp.building_effect("军屯", "militia_cap"):
                    continue
            else:
                future_need = upkeep(w, name) + UNIT_TYPES[kind]["supply"]
                budget = self.limit * forecast(w, name).income
                if w.at_war(name) and w.turn <= 50:
                    budget *= 0.9
                if future_need * REF["补给"] > budget + EPS:
                    break
                self.stock_for_essentials(future_need)
                if forecast(w, name).supply < future_need:
                    break
            if sum(self.buy_cost(g, max(0, math.ceil(q - self.resource(g)))) for g, q in cost.items() if g != "黄金") > self.resource("黄金") - cost.get("黄金", 0):
                continue
            if not all(self.buy_to(g, q, keep_gold=cost.get("黄金", 0))
                       for g, q in cost.items() if g != "黄金"):
                continue
            stock = {good: self.resource(good) - cost.get(good, 0) for good in REF}
            next_turn = forecast(w, name, stock)
            need = upkeep(w, name) + (UNIT_TYPES[kind]["supply"] if kind == "步" else 0)
            if (next_turn.supply >= need and next_turn.power >= next_turn.demand
                    and need * REF["补给"] <= self.limit * next_turn.income + EPS):
                w.recruit(name, *site, 1, kind)

    def trim(self):
        w, name = self.w, self.name
        need = upkeep(w, name)
        self.stock_for_essentials(need)
        if w.turn >= 2 and forecast(w, name).power < forecast(w, name).demand:
            self.shed_load(need)
        # Spare goods are sold at the market. Recompute the production forecast
        # after each proposed sale because some stocks also feed this period.
        for good in ("石油", "木头", "粮食", "矿石", "装备", "补给"):
            stock = {key: self.resource(key) for key in REF}
            old = forecast(w, name, stock)
            value = sum(stock[g] * REF[g] for g in REF)
            cap = self.hoard_turns * max(0, old.income)
            if 2 <= w.turn <= 15:
                cap = max(cap, self.opening_stock)
            kept = (0.30 if self.resource("黄金") > 500 else 0.12) * cap
            desired = math.ceil(max(0, value - kept) / REF[good] - EPS)
            if w.turn == 1 and good == "木头":
                desired = max(0, min(desired, int(stock[good]) - 60))
            held = min(need, stock[good]) if good == "补给" and w.at_war(name) else 0
            low, high = 0, min(desired, int(stock[good] - held))
            while low < high:
                step = (low + high + 1) // 2
                trial = dict(stock, **{good: stock[good] - step})
                future = forecast(w, name, trial)
                if (future.power >= min(old.power, old.demand)
                        and future.supply >= min(old.supply, need)
                        and future.income + EPS >= old.income):
                    low = step
                else:
                    high = step - 1
            if low:
                w.sell(name, good, low)

    def shed_load(self, need):
        """Keep the hall powered after a lost generator; construction takes a turn."""
        w, name = self.w, self.name
        for building in ("装备厂", "兵营", "补给厂"):
            for site in w.own_tiles(name):
                tile = w.tiles[site]
                while tile["buildings"][building] and not tile["pending"][building]:
                    current = forecast(w, name)
                    if current.power >= current.demand:
                        return
                    if building == "补给厂" and self.resource("补给") < need:
                        break
                    if not w.demolish(name, *site, building)[0]:
                        break


@dataclass
class Report:
    turn: int
    strength: float
    tiles: set = field(default_factory=set)
    halls: set = field(default_factory=set)


@dataclass
class Intelligence:
    territory: dict = field(default_factory=dict)
    terrain: dict = field(default_factory=dict)
    halls: dict = field(default_factory=dict)
    reports: dict = field(default_factory=dict)
    dispatched: dict = field(default_factory=dict)
    economic_reports: set = field(default_factory=set)
    blocked: set = field(default_factory=set)
    initiated: set = field(default_factory=set)
    capital: tuple | None = None
    frontier_tower: tuple | None = None
    tower_target: str | None = None

    def update(self, w, name, visible):
        for point in sorted(visible):
            self.terrain[point] = w.tile_terrain(*point)
            self.territory[point] = w.owned_by(*point)
            buildings = w.visible_buildings(name, *point)
            if buildings.get("市政厅"):
                self.halls[point] = self.territory[point]
            else:
                self.halls.pop(point, None)
        for row in w.econ_intel.get(name, ()):
            enemy = row["from"]
            text = row["text"]
            line = re.search(r"军情（仅数量，位置未知）: ([^\n]+)", text)
            if not line or (enemy in self.reports and self.reports[enemy].turn >= row["turn"]):
                continue
            report = Report(row["turn"], 0.0)
            for kind, quantity in re.findall(r"([步骑民])×(\d+)", line[1]):
                report.strength += int(quantity) * army_value({"type": kind, "hp": UNIT_TYPES[kind]["hp"]})
            for terrain, x, y, buildings in re.findall(
                    r"^\s+\S+ (\S+)\((\d+),(\d+)\) 城L\d+ 位\d+/\d+ 建筑\[(.*)\]$", text, re.M):
                point = (int(x) - 1, int(y) - 1)
                report.tiles.add(point)
                if "市政厅×" in buildings:
                    report.halls.add(point)
                if point not in visible:
                    self.terrain[point] = terrain
                    self.territory[point] = enemy
                    if point in report.halls:
                        self.halls[point] = enemy
            self.reports[enemy] = report
        for row in w.maps.get(name, ()):
            # The purchased spy map includes the far side's visible frontier.
            if not row["from"].endswith("(间谍)"):
                continue
            _, _, frontier = row["text"].partition("边界外可见（未占）:")
            for terrain, x, y in re.findall(r"(\S+)\((\d+),(\d+)\)", frontier):
                point = (int(x) - 1, int(y) - 1)
                self.terrain.setdefault(point, terrain)
                self.territory.setdefault(point, None)


def distance(own, enemy):
    return min((abs(a - x) + abs(b - y) for a, b in own for x, y in enemy), default=math.inf)


def visible_armies(w, visible, name):
    # Filter positions before inspecting type or HP. Out-of-sight units cannot
    # contribute to a decision, including when their owner is already at war.
    return [a for a in w.armies if (a["x"], a["y"]) in visible and a["owner"] != name]


def next_step(w, state, start, goals, passable, explore=False):
    if start in goals:
        return None
    queue = deque([(start, None)])
    seen = {start}
    while queue:
        point, first = queue.popleft()
        for dst in w.neighbors(*point):
            if dst in seen or not passable(dst) or dst in state.blocked or state.terrain.get(dst) in BLOCKED:
                continue
            seen.add(dst)
            hop = first or dst
            if dst in goals:
                return hop
            if explore or dst in state.terrain:
                queue.append((dst, hop))
    return None


def choose_target(w, name, state, own, visible, observed):
    choices = []
    for enemy in w.alive():
        if not _can_declare(w, name, enemy):
            continue
        land = {p for p, owner in state.territory.items() if owner == enemy}
        seen_force = sum(army_value(a) for a in observed if a["owner"] == enemy)
        report = state.reports.get(enemy)
        strength = report.strength if report else 1.5 * seen_force
        disclosed_assets = sum(
            TILE_VALUE + sum(building_value(b, n) for b, n in w.visible_buildings(name, *p).items())
            for p in land & visible
        )
        near = distance(own, land)
        choices.append((strength, near, disclosed_assets, enemy, land))
    return min(choices, key=lambda row: (not bool(row[4]), row[0], row[1], row[2], row[3]), default=None)


def send_intelligence(w, name, enemy, state):
    if w.bank_on() and w.res(name, "黄金") >= mp.BUY_REPORT_COST:
        # The scenario normally runs with banking disabled. This branch buys
        # the latest issued report through the same engine method as the UI.
        reports = w.econ_reports.get(enemy, ())
        if reports:
            latest = reports[-1]
            key = enemy, latest["report_turn"]
            if key not in state.economic_reports:
                contents = "\n".join(f"{k}: {v}" for k, v in latest.items())
                if w.bank_sell_report(name, enemy, latest["report_turn"], contents)[0]:
                    state.economic_reports.add(key)
    if w.turn - state.dispatched.get(enemy, -INTEL_REFRESH) >= INTEL_REFRESH and w.res(name, "黄金") >= SPY_COST + 30:
        if w.spy(name, enemy)[0]:
            state.dispatched[enemy] = w.turn


def defense_site(economy, state, observed, enemies):
    if not enemies:
        return None
    w = economy.w
    halls = [p for p in economy.own if w.tiles[p]["buildings"]["市政厅"]]
    pressure = {(a["x"], a["y"]) for a in observed if a["owner"] in enemies}
    borders = {p for p, owner in state.territory.items() if owner in enemies}
    return min(halls, key=lambda p: (distance([p], pressure or borders), p), default=None)


def valid_tower_site(economy, site):
    if site is None or economy.w.owned_by(*site) != economy.name:
        return False
    tile = economy.w.tiles[site]
    return economy.has_tower(site) or sum(tile["buildings"].values()) + sum(tile["pending"].values()) < MAX_SLOTS


def on_frontier(economy, state, site):
    return any(state.territory.get(q) != economy.name and state.terrain.get(q) not in BLOCKED
               for q in economy.w.neighbors(*site))


def frontier_tower(economy, state, enemy_land):
    sites = [p for p in economy.own if p != state.capital and valid_tower_site(economy, p)
             and on_frontier(economy, state, p)]
    return min(sites, key=lambda p: (distance([p], enemy_land), p), default=None)


def maneuver(economy, state, observed, aim, defend_site):
    w, name = economy.w, economy.name
    armies = w.nation_armies(name)
    enemies = {enemy for enemy in w.alive() if w.war_between(name, enemy)}
    mobilized = upkeep(w, name)
    goals = {p for p, owner in state.halls.items() if owner in enemies}
    if not goals:
        goals = {p for p, owner in state.territory.items() if owner in enemies}
    infantry = [a for a in armies if a["type"] == "步"]
    if enemies:
        hostile_positions = {(a["x"], a["y"]) for a in observed if a["owner"] in enemies}
        if defend_site is not None and (distance([defend_site], hostile_positions) <= 4
                                        or not goals or len(infantry) < 7):
            goals = {defend_site}
        groups = defaultdict(list)
        for a in infantry:
            if a["moved_turn"] != w.turn:
                groups[a["x"], a["y"]].append(a)
        for point, group in groups.items():
            ready = [a for a in group if a.get("engaged")]
            if ready:
                if state.territory.get(point) in enemies or state.territory.get(point) is None:
                    w.attack(name, [a["id"] for a in ready], *point)
                continue
            dst = next_step(w, state, point, goals,
                            lambda p: state.territory.get(p) in {None, name, *enemies}, explore=True)
            if dst is None:
                continue
            ids = [a["id"] for a in group]
            owner = state.territory.get(dst)
            if owner in enemies or (dst in hostile_positions and owner != name):
                w.attack(name, ids, *dst)
            else:
                for unit in ids:
                    success, why = w.move(name, unit, *dst)
                    if not success and ("高山" in why or "海洋" in why):
                        state.blocked.add(dst)
                        break
        return

    # Neutral expansion grows the fixed-price economy and brings foreign
    # borders into legal sight. Keep militia stationed unless there is room
    # for their new supply bill.
    frontier = {p for p, owner in state.territory.items() if owner is None and p in state.terrain
                and state.terrain[p] not in BLOCKED
                and any(state.territory.get(q) == name for q in w.neighbors(*p))}
    if not frontier or (w.turn >= 51 and len(economy.own) >= 18 and len(infantry) < 10):
        return
    home = next((p for p in economy.own if w.tiles[p]["buildings"]["市政厅"]), economy.own[0])
    taken = set()
    for a in armies:
        if a["moved_turn"] == w.turn:
            continue
        point = (a["x"], a["y"])
        if a.get("engaged"):
            if state.territory.get(point) is None:
                w.attack(name, [a["id"]], *point)
            continue
        free_militia = a["type"] == "民" and state.territory.get(point) == name and w.tiles[point]["buildings"]["军屯"]
        if free_militia:
            bill = (mobilized + 1) * REF["补给"]
            if bill > economy.limit * forecast(w, name).income + EPS:
                continue
            economy.stock_for_essentials(mobilized + 1)
            if forecast(w, name).supply < mobilized + 1:
                continue
        # Fields with local gold and oil support the army budget fastest.
        def value(p):
            res = w.tile_resources(*p)
            return sum(res[g] * (REF.get(g, MARKET["黄金"])) for g in ("黄金", "石油", "木头", "矿石", "耕地")
                       if g != "耕地") + res["耕地"] * REF["粮食"]

        known_frontier = frontier - taken
        if not known_frontier:
            break
        dst = min(known_frontier, key=lambda p: (distance([point], [p]), -value(p), distance([p], aim or [home]), p))
        step = next_step(w, state, point, {dst}, lambda p: state.territory.get(p) in (None, name))
        if step is None:
            continue
        if state.territory.get(step) is None:
            co_located = [d["id"] for d in armies if d["type"] == "步" and d["moved_turn"] != w.turn
                          and not d.get("engaged") and (d["x"], d["y"]) == point]
            ids = co_located if a["type"] == "步" else [a["id"]]
            ok = w.attack(name, ids, *step)[0]
        else:
            ok = w.move(name, a["id"], *step)[0]
        if ok:
            taken.add(step)
            if free_militia:
                mobilized = upkeep(w, name)


def make_w3(*, low_share=0.15, hoard_turns=3.0, near=6, on_declare=None):
    """Build a per-episode W3 opponent; military spending steps to 50% on turn 51."""
    if not 0 <= low_share <= 0.5 or hoard_turns <= 0 or near < 0:
        raise ValueError("invalid W3 budget or distance")
    states = WeakKeyDictionary()

    def w3(w, name, rng):
        states_by_name = states.setdefault(w, {})
        state = states_by_name.setdefault(name, Intelligence())
        limit = low_share if w.turn <= 50 else 0.5
        economy = TurnEconomy(w, name, limit, hoard_turns)
        if not economy.own:
            return
        visible = visible_set(w, name)
        state.update(w, name, visible)
        observed = visible_armies(w, visible, name)
        # Sell idle stocks first once power is online, then recheck fuel and
        # rations after building, recruiting, and moving.
        if w.turn > 1 and w.res(name, "黄金") < 350:
            economy.trim()
        enemies = {enemy for enemy in w.alive() if w.war_between(name, enemy)}
        state.initiated.intersection_update(enemies)
        incoming = enemies - state.initiated
        if (state.capital not in economy.own
                or not w.tiles[state.capital]["buildings"]["市政厅"]):
            state.capital = next((p for p in economy.own if w.tiles[p]["buildings"]["市政厅"]), None)
        defend = defense_site(economy, state, observed, incoming)
        target = choose_target(w, name, state, economy.own, visible, observed)
        enemy = target[3] if target else None
        site = state.frontier_tower
        if (not valid_tower_site(economy, site) or site == state.capital
                or (enemy is not None and state.tower_target != enemy)
                or (not economy.has_tower(site) and not on_frontier(economy, state, site))):
            state.tower_target = state.frontier_tower = None
        economy.invest(defend, state.capital, state.frontier_tower if not enemies and enemy else None)
        economy.stock_for_essentials(upkeep(w, name))
        economy.recruit(economy.own, "民")
        economy.recruit(economy.own, "步")

        aim = set()
        infantry = sum(a["type"] == "步" for a in w.nation_armies(name))
        if target is not None and not w.at_war(name):
            strength, separation, _, enemy, aim = target
            if infantry > 6:
                send_intelligence(w, name, enemy, state)
                report = state.reports.get(enemy)
                waiting = w.turn - state.dispatched.get(enemy, -INTEL_REFRESH) < mp.SPY_TURNS
                stale = report is not None and w.turn - report.turn >= INTEL_REFRESH
                force = sum(army_value(a) for a in w.nation_armies(name))
                if not waiting and not stale and separation <= near and force + EPS >= 1.3 * strength:
                    if state.tower_target != enemy:
                        state.tower_target = enemy
                        state.frontier_tower = frontier_tower(economy, state, aim)
                    site = state.frontier_tower
                    ready = site is None or economy.has_tower(site) or economy.build(site, "瞭望塔", keep_gold=40)
                    if ready and w.declare_war(name, enemy)[0]:
                        state.initiated.add(enemy)
                        if on_declare is not None:
                            on_declare(w, name, enemy, force, strength, separation, report.turn if report else None)
        maneuver(economy, state, observed, aim, defend)
        economy.trim()

    return w3
