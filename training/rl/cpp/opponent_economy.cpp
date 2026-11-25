#include "opponents.hpp"

#include <tuple>

namespace rl {
namespace {

constexpr int extract_resource[] = {-1, TR_WOOD, TR_FARM, TR_ORE, TR_OIL, TR_GOLD};
constexpr int extract_output[] = {-1, WOOD, FOOD, ORE, OIL, GOLD};
// Python uses sorted(BUILDINGS) before a stable sort by payback, density and site.
constexpr int roi_order[] = {FARM, ACADEMY, TOWNHALL, LUMBER, OILWELL, MINE, SUPPLYFAC, EQUIPFAC, GOLDMINE};

int slots(const Tile& t) {
    return std::accumulate(t.bld.begin(), t.bld.end(), 0) + std::accumulate(t.pending.begin(), t.pending.end(), 0);
}

bool free_at(const Tile& t) { return !t.built && slots(t) < Num::MAX_SLOTS; }

int supply_units(const World& w, int seat) {
    int result = 0;
    std::vector<int> free(w.tiles.size());
    for (const auto& a : w.armies) {
        if (a.owner != seat || a.hp <= 0) continue;
        int i = a.x * w.size + a.y;
        const auto& t = w.tiles[i];
        if (a.kind == MIL && t.owner == seat && free[i] < t.bld[MILITIA]) {
            ++free[i];
        } else {
            result += Num::U_SUPPLY[a.kind];
        }
    }
    return result;
}

double income(const World& w, int seat, const std::vector<int>& sites) {
    const auto& nation = w.nations[seat];
    const bool power_ok = nation.grid_short || (nation.energy_out == 0 && nation.energy_use == 0);
    double total = 0;
    for (int i : sites) {
        const auto& t = w.tiles[i];
        for (int b = LUMBER; b <= GOLDMINE; ++b) {
            int r = extract_output[b];
            total += t.bld[b] * (r == GOLD ? 10 : Num::M_BASE[r - 1]);
        }
        if (power_ok && w.res(seat, FOOD) >= 2 * t.bld[SUPPLYFAC] && w.res(seat, ORE) >= t.bld[SUPPLYFAC])
            total += 2 * Num::M_BASE[SUPPLY - 1] * t.bld[SUPPLYFAC];
        if (power_ok && w.res(seat, OIL) >= t.bld[EQUIPFAC] && w.res(seat, ORE) >= t.bld[EQUIPFAC])
            total += 2 * Num::M_BASE[EQUIP - 1] * t.bld[EQUIPFAC];
        total += (Num::B_GOLD_BASE + Num::B_GOLD_SLOT * std::accumulate(t.bld.begin(), t.bld.end(), 0)) * t.bld[TOWNHALL];
    }
    return total;
}

struct ReturnRates {
    std::array<double, 6> buy, sell;

    explicit ReturnRates(const World& w) {
        for (int r = 0; r < 6; ++r) {
            buy[r] = w.market_walk(r, 1, 0).first;
            sell[r] = w.market_walk(r, 1, 1).first;
        }
    }

    double per_turn(const Tile& t, int b) const {
        if (b >= LUMBER && b <= OILWELL) return sell[extract_output[b] - 1];
        if (b == GOLDMINE) return 10;
        const double energy = buy[WOOD - 1] / 2;
        if (b == SUPPLYFAC) return 2 * buy[SUPPLY - 1] - (2 * buy[FOOD - 1] + buy[ORE - 1]) - energy;
        if (b == EQUIPFAC) return (2 * sell[EQUIP - 1] - (buy[ORE - 1] + buy[OIL - 1]) - energy) * 0.85;
        const int completed = std::accumulate(t.bld.begin(), t.bld.end(), 0);
        if (b == TOWNHALL) return Num::B_GOLD_BASE + (completed + 3) * Num::B_GOLD_SLOT - energy;
        if (b == ACADEMY && completed) {
            int gold = 0;
            for (int k = 0; k < BLD_N; ++k) gold += Num::B_GOLD[k] * t.bld[k];
            double discount = Num::B_DISCOUNT / 100.0;
            double average = static_cast<double>(gold) / completed;
            double density = (t.bld[TOWNHALL] || completed >= Num::B_MIN_SLOTS[TOWNHALL])
                ? discount * completed * Num::B_GOLD_SLOT : 0;
            return discount * completed * average / 30 + density;
        }
        return 0;
    }
};

}  // namespace

void rule_economy(World& w, int seat) {
    std::vector<int> own, free, plants;
    std::array<int, BLD_N> count{};
    int recruits = 0;
    int n_armies = std::count_if(w.armies.begin(), w.armies.end(), [seat](const Army& a) { return a.owner == seat && a.hp > 0; });
    for (int i = 0; i < static_cast<int>(w.tiles.size()); ++i) {
        const auto& t = w.tiles[i];
        if (t.owner != seat) continue;
        own.push_back(i);
        if (free_at(t)) free.push_back(i);
        for (int b = 0; b < BLD_N; ++b) count[b] += t.bld[b];
        recruits += std::max(0, t.bld[BARRACKS] - t.recruited);
    }
    if (own.empty()) return;
    auto build = [&](int i, int b) { return w.build(seat, i / w.size, i % w.size, b); };
    auto stock = [&](int r) { return static_cast<int>(w.res(seat, r)); };
    auto price = [&](int r) { return std::max(1, static_cast<int>(w.price[r - 1])); };
    int power = 2 * count[WOODPOWER] + 8 * count[OILPOWER];
    int demand = 0;
    for (int b = 0; b < BLD_N; ++b) demand += count[b] * Num::B_ENERGY_USE[b];
    int gen_add = 0, dem_add = 0;
    int supply_cap = count[SUPPLYFAC] * 2;
    int want_barracks = std::clamp(supply_cap / 2 + 2, 2, 4);
    const int supply = supply_units(w, seat);
    double inc = income(w, seat, own);
    bool military_ok = inc <= 0.5 || supply * w.price[SUPPLY - 1] / inc < 0.30;
    int army_cap = military_ok ? std::max(n_armies + 1, supply_cap) : n_armies;

    struct Roi { int site, building, density; double payback; };
    std::vector<Roi> roi;
    ReturnRates rates(w);
    for (int p : free) {
        const auto& t = w.tiles[p];
        const int used = slots(t);
        for (int b : roi_order) {
            int effective = t.bld[b] + t.pending[b];
            if (b <= GOLDMINE && effective >= t.tres[extract_resource[b]]) continue;
            if (Num::B_SITE_LIMIT[b] && effective >= Num::B_SITE_LIMIT[b]) continue;
            if (used < Num::B_MIN_SLOTS[b]) continue;
            double net = rates.per_turn(t, b);
            if (net <= 0) continue;
            int cost = Num::B_GOLD[b] * (100 + Num::T_BUILDP[t.terrain]) / 100;
            if (t.bld[ACADEMY] && b != ACADEMY) cost = cost * (100 - Num::B_DISCOUNT) / 100;
            roi.push_back({p, b, used, (cost + Num::B_WOOD[b] * w.price[WOOD - 1]) / net});
        }
    }
    std::stable_sort(roi.begin(), roi.end(), [](const Roi& a, const Roi& b) {
        return std::tuple{a.payback, -a.density, a.site} < std::tuple{b.payback, -b.density, b.site};
    });
    plants = free;
    std::sort(plants.begin(), plants.end(), [&](int a, int b) {
        return std::pair{-slots(w.tiles[a]), a} < std::pair{-slots(w.tiles[b]), b};
    });
    std::vector<Roi> best;
    std::vector<char> planned(w.tiles.size());
    for (const auto& item : roi) {
        if (planned[item.site]) continue;
        planned[item.site] = true;
        best.push_back(item);
    }
    int n_barracks = std::min(std::max(0, want_barracks - count[BARRACKS]),
        static_cast<int>(std::count_if(free.begin(), free.end(), [&](int p) { return slots(w.tiles[p]) >= 3; })));
    int n_plants = std::min(std::max(0, static_cast<int>(free.size()) - n_barracks), std::max(0, (demand - power + 1) / 2));
    int n_recruits = std::min(army_cap - n_armies, recruits);

    std::array<int, RES_N> need{};
    need[SUPPLY] = supply;
    need[FOOD] = count[SUPPLYFAC] * 2;
    need[ORE] = count[SUPPLYFAC] + count[EQUIPFAC];
    need[OIL] = count[EQUIPFAC] + count[OILPOWER];
    need[WOOD] = count[WOODPOWER] + n_plants * Num::B_WOOD[WOODPOWER] + n_barracks * Num::B_WOOD[BARRACKS];
    int budget = stock(GOLD);
    for (int r = FOOD; r < RES_N; ++r)
        if (r != WOOD && stock(r) > need[r]) budget += static_cast<int>((stock(r) - need[r]) * 0.9 * price(r));
    for (const auto& item : best) {
        int cost = Num::B_GOLD[item.building] + Num::B_WOOD[item.building] * price(WOOD);
        if (cost > budget) continue;
        budget -= cost;
        need[WOOD] += Num::B_WOOD[item.building];
    }
    need[FOOD] += Num::U_REC_FOOD[INF] * n_recruits;
    need[EQUIP] += Num::U_REC_EQUIP[INF] * n_recruits;
    for (int r = FOOD; r < RES_N; ++r)
        if (stock(r) > need[r]) w.sell(seat, r - 1, stock(r) - need[r]);
    for (int r = FOOD; r < RES_N; ++r) {
        int qty = std::min(need[r] - stock(r), std::max(0, stock(GOLD) / price(r)));
        if (qty > 0) w.buy(seat, r - 1, qty);
    }

    int top = n_plants ? WOODPOWER : n_barracks ? BARRACKS : best.empty() ? -1 : best.front().building;
    int saving = top < 0 ? 0 : std::max(0, Num::B_GOLD[top] - stock(GOLD));
    auto afford = [&](int p, int b) {
        return w.res(seat, GOLD) >= Num::B_GOLD[b] && w.res(seat, WOOD) >= Num::B_WOOD[b] && free_at(w.tiles[p]);
    };
    auto spend_ok = [&](int b) {
        return !saving || (b >= LUMBER && b <= GOLDMINE) || b == SUPPLYFAC || b == EQUIPFAC
            || stock(GOLD) - saving >= Num::B_GOLD[b];
    };
    auto place = [&](int p, int b) {
        if (Num::B_ENERGY_USE[b] && power + gen_add < demand + dem_add + 1) {
            if (afford(p, WOODPOWER) && spend_ok(WOODPOWER) && build(p, WOODPOWER)) gen_add += 2;
            return true;
        }
        if (b == TOWNHALL && !w.tiles[p].bld[ACADEMY] && afford(p, ACADEMY) && spend_ok(ACADEMY) && build(p, ACADEMY))
            return true;
        if (!afford(p, b) || !spend_ok(b) || !build(p, b)) return false;
        dem_add += Num::B_ENERGY_USE[b];
        return true;
    };

    if (military_ok && !w.nations[seat].grid_short) {
        for (int p : own) {
            if (n_armies >= army_cap) break;
            if (w.tiles[p].bld[BARRACKS] <= w.tiles[p].recruited) continue;
            if (w.res(seat, FOOD) < Num::U_REC_FOOD[INF] || w.res(seat, EQUIP) < Num::U_REC_EQUIP[INF]) continue;
            if (w.recruit(seat, p / w.size, p % w.size, 1, INF)) ++n_armies;
        }
    }
    for (int p : free) {
        if (!free_at(w.tiles[p])) continue;
        if (want_barracks > count[BARRACKS] && slots(w.tiles[p]) >= 3 && afford(p, BARRACKS)
            && spend_ok(BARRACKS) && place(p, BARRACKS)) {
            // The Python counter reads only completed buildings. A new order is still pending.
            continue;
        }
        if (power + gen_add < demand + dem_add) {
            auto it = std::find_if(plants.begin(), plants.end(), [&](int q) { return afford(q, WOODPOWER) && spend_ok(WOODPOWER); });
            if (it != plants.end() && build(*it, WOODPOWER)) gen_add += 2;
        }
    }
    for (const auto& item : best)
        if (free_at(w.tiles[item.site])) place(item.site, item.building);
}

}  // namespace rl
