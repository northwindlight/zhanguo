// 经济：建筑生产、电网、建造/拆除、落成、央行。
#include "engine.hpp"
#include <charconv>

namespace {
double decimal_round(double value, int digits) {
    char buffer[64];
    const auto end = std::to_chars(buffer, buffer + sizeof(buffer), value,
                                   std::chars_format::fixed, digits).ptr;
    double rounded = value;
    std::from_chars(buffer, end, rounded);
    return rounded;
}

std::optional<double> growth(double value, double previous) {
    if (previous <= 0) return std::nullopt;
    return (value - previous) / previous;
}
}

Ledger& World::ledger(int n) {
    auto& out = ledgers[n];
    if (!out.since) out.since = std::max(1, turn);
    return out;
}

void World::value_flow(int n, double Ledger::*field, int resource, double amount) {
    if (amount <= 0) return;
    const double fixed = amount * (resource == GOLD ? 1.0 : Num::M_BASE[resource - 1]);
    if (field == &Ledger::prod || field == &Ledger::gold) nations[n].fixed_gdp_last += fixed;
    if (field == &Ledger::mid || field == &Ledger::fuel) nations[n].fixed_gdp_last -= fixed;
    double value = amount * (resource == GOLD ? 1.0 : price[resource - 1]);
    ledger(n).*field += value;
    if (resource != GOLD)
        pending_valuations.push_back({n, resource, field, amount, value});
}

double World::nation_assets(int n) const {
    double value = 0;
    for (int i : tile_order) {
        const auto& t = tiles[i];
        if (t.owner != n) continue;
        for (int level = 0; level < t.bld[CASTLE]; ++level)
            value += Num::B_CASTLE_COST[level];
        for (int b = LUMBER; b < BLD_N; ++b)
            if (t.bld[b]) value += t.bld[b] * (Num::B_GOLD[b] + Num::B_WOOD[b] * price[WOOD - 1]);
    }
    return value;
}

void World::close_report_period(const std::vector<Ledger>& before) {
    const bool report_due = turn && turn % Num::REPORT_EVERY == 0;
    for (int n = 0; n < static_cast<int>(nations.size()); ++n) {
        if (!alive(n)) continue;
        const auto& l = ledgers[n];
        const auto& start = before[n];
        const double gdp = (l.prod - start.prod) - (l.mid - start.mid)
                         - (l.fuel - start.fuel) + (l.gold - start.gold);
        nations[n].gdp_last = gdp;
        if (!report_due) continue;
        const double military = (l.supply - start.supply) * price[SUPPLY - 1];
        const double invest = l.invest_gold + l.invest_wood;
        const double assets = nation_assets(n);
        const double supply_total = l.prod + l.imports;
        auto& reports = econ_reports[n];
        const Report* previous = reports.empty() ? nullptr : &reports.back();
        Report out{};
        out.period_end = turn;
        out.report_turn = turn + 1;
        out.period_start = l.since;
        out.span = std::clamp(turn - l.since + 1, 1, Num::REPORT_EVERY);
        out.gdp = decimal_round(gdp, 1);
        out.military = decimal_round(military, 1);
        if (gdp > 0) out.military_ratio = decimal_round(military / gdp, 4);
        out.invest = decimal_round(invest, 1);
        out.assets = decimal_round(assets, 1);
        out.import_gold = decimal_round(l.imports, 1);
        out.export_gold = decimal_round(l.exports, 1);
        out.supply_eaten = static_cast<int>(l.supply);
        out.supply_eaten_turn = static_cast<int>(l.supply - start.supply);
        out.trade_ratio = decimal_round(supply_total > 0 ? (l.exports + l.imports) / supply_total : 0, 4);
        if (previous) {
            out.gdp_growth = growth(gdp, previous->gdp);
            out.invest_growth = growth(invest, previous->invest);
            out.assets_growth = growth(assets, previous->assets);
        }
        reports.push_back(out);
    }
    if (report_due) std::fill(ledgers.begin(), ledgers.end(), Ledger{});
}

// TR 枚举：ORE=0 GOLD=1 FARM=2 OIL=3 WOOD=4
static const int SITE_RES[BLD_N] = {-1, 4, 2, 0, 3, 1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 2};
// 采集建筑 -> 产出的资源（TR->RES 映射：木/粮/矿/油/金）
static const int SITE_RUN[BLD_N][2] = {
    {-1, 0}, {WOOD, 1}, {FOOD, 1}, {ORE, 1}, {OIL, 1}, {GOLD, 10}, {0,0},{0,0},
    {0,0},{0,0},{0,0},{0,0},{0,0},{0,0},{0,0},{0,0}};

void World::production_phase() {
    for (int n = 0; n < static_cast<int>(nations.size()); ++n) {
        nations[n].fixed_gdp_last = 0.0;
        nations[n].supply_output_last = nations[n].equipment_output_last = 0;
        if (!alive(n)) continue;
        auto output = [&](int resource, int amount) {
            const int delivered = amount - tribute_take(n, resource, amount);
            add_res(n, resource, delivered);
            if (resource == SUPPLY) nations[n].supply_output_last += delivered;
            if (resource == EQUIP) nations[n].equipment_output_last += delivered;
            value_flow(n, resource == GOLD ? &Ledger::gold : &Ledger::prod, resource, amount);
        };
        std::vector<std::pair<int, int>> plants, factories;
        int use = 0;
        for (int i : tile_order) {
            const auto& t = tiles[i];
            if (t.owner != n) continue;
            for (int b : {LUMBER, FARM, MINE, OILWELL, GOLDMINE})
                if (t.bld[b]) output(SITE_RUN[b][0], t.bld[b] * SITE_RUN[b][1]);
            for (int b : {WOODPOWER, OILPOWER})
                if (t.bld[b]) plants.emplace_back(b, t.bld[b]);
            for (int b : {SUPPLYFAC, EQUIPFAC})
                if (t.bld[b]) factories.emplace_back(b, t.bld[b]);
            use += t.bld[SUPPLYFAC] + t.bld[EQUIPFAC] + t.bld[BARRACKS] + t.bld[TOWNHALL];
        }
        int out = 0;
        for (const auto& [plant, count] : plants) {
            const int r = plant == WOODPOWER ? WOOD : OIL;
            const int batches = std::min(count, static_cast<int>(std::floor(res(n, r))));
            add_res(n, r, -batches);
            value_flow(n, &Ledger::fuel, r, batches);
            out += batches * Num::B_ENERGY_OUT[plant];
        }
        grid_short = out < use;
        energy_out = out;
        energy_use = use;
        nations[n].energy_out = out;
        nations[n].energy_use = use;
        nations[n].grid_short = grid_short;
        if (grid_short) continue;
        for (const auto& [factory, count] : factories) {
            const bool supply = factory == SUPPLYFAC;
            const int r1 = supply ? FOOD : ORE, r2 = supply ? ORE : OIL;
            const int need1 = supply ? 2 : 1;
            const int batches = std::min(count, static_cast<int>(std::floor(
                std::min(res(n, r1) / need1, res(n, r2)))));
            if (!batches) continue;
            add_res(n, r1, -need1 * batches);
            value_flow(n, &Ledger::mid, r1, need1 * batches);
            add_res(n, r2, -batches);
            value_flow(n, &Ledger::mid, r2, batches);
            output(supply ? SUPPLY : EQUIP, 2 * batches);
        }
        for (int i : tile_order) {
            const auto& t = tiles[i];
            if (t.owner != n || !t.bld[TOWNHALL]) continue;
            const int others = std::accumulate(t.bld.begin(), t.bld.end(), 0) - t.bld[TOWNHALL];
            output(GOLD, (Num::B_GOLD_BASE + others * Num::B_GOLD_SLOT) * t.bld[TOWNHALL]);
        }
    }
    tribute_flush();
}

int World::tribute_take(int n, int r, int v) {
    auto it = vassals.find(n);
    if (it == vassals.end() || !alive(it->second.overlord) || v <= 0) return 0;
    int o = it->second.overlord;
    long long key = (long long)n * RES_N + r;
    long long tot = (long long)v * it->second.tribute + (long long)std::llround(tribute_carry[key]);
    int give = (int)(tot / 100);
    tribute_carry[key] = (double)(tot % 100);
    if (give > 0) tribute_out.push_back({n, o, r, give});
    return give;
}

void World::tribute_flush() {
    for (auto& t : tribute_out)
        if (alive(t.s) && alive(t.o)) add_res(t.o, t.r, t.v);
    tribute_out.clear();
}

int World::build_price(const Tile& tile, int b) const {
    const int count = tile.bld[b] + tile.pending[b];
    if (b == CASTLE && count >= Num::B_MAXLV[b]) return -1;
    int cost = Num::build_base_cost(b, count);
    cost = cost * (100 + Num::T_BUILDP[tile.terrain]) / 100;
    if (tile.bld[ACADEMY] && b != ACADEMY) cost = cost * (100 - Num::B_DISCOUNT) / 100;
    return cost;
}

int World::build_cost(int n, int x, int y, int b) const {
    if (!alive(n) || !in_bounds(x, y) || b < 0 || b >= BLD_N) return -1;
    const Tile& t = tiles[x * size + y];
    if (t.owner != n || t.built || (deferred_orders && std::accumulate(t.pending.begin(),t.pending.end(),0))) return -1;
    int count = t.bld[b] + t.pending[b];
    if (Num::B_SITE_LIMIT[b] && count >= Num::B_SITE_LIMIT[b]) return -1;
    if (b == CASTLE && count >= Num::B_MAXLV[b]) return -1;
    const int slots = std::accumulate(t.bld.begin(), t.bld.end(), 0)
        + std::accumulate(t.pending.begin(), t.pending.end(), 0);
    if (slots < Num::B_MIN_SLOTS[b] || slots >= Num::MAX_SLOTS) return -1;
    if (b == DIPLO && nations[n].built_diplomacy_center) return -1;
    int sr = SITE_RES[b];
    int resource_used=count;
    if(deferred_orders && sr>=0){resource_used=0;for(int other=0;other<BLD_N;++other)if(SITE_RES[other]==sr)resource_used+=t.bld[other]+t.pending[other];}
    if (sr >= 0 && resource_used >= t.tres[sr]) return -1;
    const int cost = build_price(t, b);
    if (res(n, GOLD) < cost || res(n, WOOD) < Num::B_WOOD[b]) return -1;
    return cost;
}

bool World::build(int n, int x, int y, int b) {
    const int cost = build_cost(n, x, y, b);
    if (cost < 0) return false;
    Tile& t = tile(x, y);
    add_res(n, GOLD, -cost);
    add_res(n, WOOD, -Num::B_WOOD[b]);
    if (!deferred_orders) {
        ledger(n).invest_gold += cost;
        value_flow(n, &Ledger::invest_wood, WOOD, Num::B_WOOD[b]);
    }
    t.pending[b] += 1;
    t.pending_gold = cost;
    t.pending_owner = n;
    t.pending_started = !deferred_orders;
    t.built = 1;
    if (b == DIPLO) nations[n].built_diplomacy_center = true;
    return true;
}

bool World::demolish(int n, int x, int y, int b) {
    if (!alive(n) || !in_bounds(x, y) || b < 0 || b >= BLD_N) return false;
    Tile& t = tile(x, y);
    if (t.owner != n) return false;
    int cnt = t.bld[b], pend = t.pending[b];
    if (!cnt && !pend) return false;
    if (pend) {
        t.pending[b] -= 1;
        const double fraction=deferred_orders && !t.pending_started ? 1.0 : .5;
        add_res(n, GOLD, (t.pending_gold ? t.pending_gold : Num::B_GOLD[b]) * fraction);
        add_res(n, WOOD, Num::B_WOOD[b] * fraction);
        t.pending_gold = 0;
        t.pending_owner = -1;
        if (deferred_orders && !t.pending_started) t.built = 0;
        if (deferred_orders && !t.pending_started && b==DIPLO) nations[n].built_diplomacy_center=false;
        return true;
    }
    t.bld[b] -= 1;
    return true;
}

void World::completion_phase() {
    for (int i : tile_order) {
        auto& t = tiles[i];
        if (deferred_orders && !t.pending_started) continue;
        for (int b = 0; b < BLD_N; b++) {
            t.bld[b] += t.pending[b];
            t.pending[b] = 0;
        }
        t.pending_gold = 0;
        t.pending_owner = -1;
        t.pending_started = false;
    }
}


