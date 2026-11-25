// 央行、外交费用、礼物/信件/换图/谍报。
#include "engine.hpp"

int World::nation_bld_count(int n, int b, bool include_pending) const {
    int c = 0;
    for (const auto& t : tiles)
        if (t.owner == n) c += t.bld[b] + (include_pending ? t.pending[b] : 0);
    return c;
}

int World::diplo_fee(int n) const {
    int fee = Num::FEE;
    int centers = nation_bld_count(n, DIPLO);
    for (int k = 0; k < centers && fee > 1; k++) fee = std::max(1, fee / 2);   // main 是向下取整
    return std::max(1, fee);
}

bool World::pay_fee(int n) {
    if (!alive(n)) return false;
    int fee = diplo_fee(n);
    if (nations[n].res[GOLD] < fee) return false;
    nations[n].res[GOLD] -= fee;
    return true;
}

int World::base_fee(int n, int target, bool free_if_target_center) const {
    if (alive(target) && (entity(n) == entity(target) ||
        (free_if_target_center && nation_bld_count(target, DIPLO) > 0))) return 0;
    return diplo_fee(n);
}

int World::diplo_charge(int n, int target, bool free_if_target_center, int fixed) {
    if (no_fee || !alive(n)) return 0;
    const int fee = fixed >= 0 ? fixed : base_fee(n, target, free_if_target_center);
    if (fee && res(n, GOLD) < fee) return -1;
    add_res(n, GOLD, -fee);
    return fee;
}

bool World::entity_at_war(int ent) const {
    for (auto& w : wars) {
        if (std::find(w.atk_ents.begin(), w.atk_ents.end(), ent) != w.atk_ents.end()) return true;
        if (std::find(w.def_ents.begin(), w.def_ents.end(), ent) != w.def_ents.end()) return true;
    }
    return false;
}

bool World::vassal_gated(int from, int to) const {
    auto it = vassals.find(from);
    return it != vassals.end() && it->second.kind == 0 && alive(to)
        && entity(from) != entity(to);
}

bool World::gift(int from, int to, int good, int amt) {
    if (!alive(from) || !alive(to) || from == to || amt <= 0) return false;
    if (good < 0 || good >= RES_N) return false;   // main：赠礼可含黄金
    if (vassal_gated(from, to)) return false;
    const int fee = base_fee(from, to);
    if ((fee && res(from, GOLD) < fee) || res(from, good) - (good == GOLD ? fee : 0) < amt)
        return false;
    add_res(from, GOLD, -fee);
    add_res(from, good, -amt);
    gift_pending.push_back({from, to, good, amt, turn + 1});
    return true;
}

bool World::send_letter(int from, int to, int textlen) {
    if (!alive(from) || !alive(to) || from == to) return false;
    if (vassal_gated(from, to)) return false;
    // main letter_cost：起步价（联盟 10 / 非联盟 20，每座外交中心 -5，下限 5）+ 超出 20 字每 10 字 1 金
    int base = allied(from, to) ? 10 : 20;
    int centers = nation_bld_count(from, DIPLO);
    base = std::max(5, base - 5 * centers);
    int over = std::max(0, textlen - 20);
    int fee = base + (over + 9) / 10;
    if (nations[from].res[GOLD] < fee) return false;
    nations[from].res[GOLD] -= fee;
    return true;
}

bool World::share_map(int from, int to) {
    if (!alive(from) || !alive(to) || from == to) return false;
    if (vassal_gated(from, to) || diplo_charge(from, to, false, -1) < 0) return false;
    map_pending.push_back({to, map_snapshot(from, turn + 1, false)});
    return true;
}

bool World::spy_send(int from, int to) {
    if (!alive(from) || !alive(to) || from == to) return false;
    if (vassal_gated(from, to) || res(from, GOLD) < Num::SPY_COST) return false;
    add_res(from, GOLD, -Num::SPY_COST);
    spy_pending.push_back({from, to, turn + Num::SPY_TURNS});
    return true;
}

double World::nation_gdp(int n) const {
    if (n < 0 || n >= (int)nations.size()) return 0.0;
    return nations[n].gdp_last;
}

int World::bank_credit(int n) const {
    return (int)std::max(0.0, nation_gdp(n) * 5.0);
}

bool World::bank_set_rate(double r) {
    if (!bank_on) return false;
    const double rate = std::max(-0.5, std::min(0.5, r));
    if (std::abs(rate - bank_rate_v) < 1e-9) return false;
    bank_rate_v = rate;
    return true;
}

bool World::bank_loan(int n) {
    if (!bank_on || !alive(n)) return false;
    if (loans.count(n)) return false;
    int fee = diplo_charge(n, -1, false, -1);
    if (fee < 0) return false;
    int credit = bank_credit(n);
    if (credit <= 0) {
        if (fee > 0) add_res(n, GOLD, fee);
        return false;
    }
    nations[n].res[GOLD] += credit;
    Loan l;
    l.principal = credit;
    l.due = credit;
    l.turns_left = 5;
    l.taken_turn = turn;
    l.rate = bank_rate_v + 0.02;
    loans[n] = l;
    return true;
}

void World::bank_settle() {
    if (!bank_on) return;
    const double loan_rate = bank_rate_v + 0.02;
    for (int n = 0; n < (int)nations.size(); n++) {
        if (!alive(n)) continue;
        double gold = nations[n].res[GOLD];
        double d = gold > 0 ? std::trunc(gold * bank_rate_v) : 0;
        if (d < 0 && gold + d < 0) d = -gold;   // 储蓄扣不到负
        if (d) nations[n].res[GOLD] += d;
        auto it = loans.find(n);
        if (it == loans.end()) continue;
        Loan& l = it->second;
        if (loan_rate != 0) l.due = std::max(0.0, std::nearbyint(l.due * (1 + loan_rate)));
        l.turns_left--;
        if (l.turns_left <= 0) {
            nations[n].res[GOLD] -= (int)l.due;   // 到期强制扣款（可为负）
            loans.erase(it);
        }
    }
}

namespace {
template <class T>
void append_recent(std::vector<T>& store, T entry, size_t limit) {
    if (store.size() >= limit) store.erase(store.begin());
    store.push_back(std::move(entry));
}
}

bool World::bank_sell_report(int from, int to) {
    constexpr int cost = 20;
    if (!bank_on || !alive(from) || !alive(to) || from == to ||
        res(from, GOLD) < cost || econ_reports[to].empty()) return false;
    add_res(from, GOLD, -cost);
    append_recent(econ_intel[from], EconIntel{to, turn, econ_reports[to].back().report_turn}, 2);
    return true;
}

MapIntel World::map_snapshot(int source, int arrival, bool stolen) const {
    MapIntel result{source, arrival, stolen, {}};
    std::vector<char> frontier(tiles.size());
    for (int cell = 0; cell < static_cast<int>(tiles.size()); ++cell) {
        const auto& tile = tiles[cell];
        if (tile.owner != source) continue;
        result.tiles.push_back({cell, tile.terrain, tile.bld[CASTLE],
                                std::accumulate(tile.bld.begin(), tile.bld.end(), 0), true});
        for (int x = cell / size - 1; x <= cell / size + 1; ++x) {
            for (int y = cell % size - 1; y <= cell % size + 1; ++y) {
                if (in_bounds(x, y) && !materialized[x * size + y])
                    frontier[x * size + y] = true;
            }
        }
    }
    for (int cell = 0; cell < static_cast<int>(tiles.size()); ++cell)
        if (frontier[cell]) result.tiles.push_back({cell, tiles[cell].terrain});
    return result;
}

EconIntel World::econ_snapshot(int source, int arrival) const {
    EconIntel result{source, arrival};
    const auto& nation = nations[source];
    result.resources = nation.res;
    result.blackout = nation.grid_short;
    result.energy = nation.energy_out;
    result.demand = nation.energy_use;
    for (const auto& army : armies)
        if (army.owner == source && army.hp > 0) ++result.armies[army.kind];
    for (int cell = 0; cell < static_cast<int>(tiles.size()); ++cell) {
        const auto& tile = tiles[cell];
        if (tile.owner != source) continue;
        result.tiles.push_back({cell, tile.terrain, tile.bld[CASTLE],
                                std::accumulate(tile.bld.begin(), tile.bld.end(), 0),
                                true, tile.bld, std::accumulate(tile.pending.begin(), tile.pending.end(), 0)});
    }
    return result;
}

void World::deliver_intel() {
    auto due = [this](int arrival) { return arrival <= turn; };
    for (const auto& gift : gift_pending) {
        if (!due(gift.arrive)) continue;
        const int recipient = alive(gift.to) ? gift.to : gift.from;
        if (alive(recipient)) add_res(recipient, gift.good, gift.amount);
    }
    gift_pending.erase(std::remove_if(gift_pending.begin(), gift_pending.end(),
                        [&](const Gift& gift) { return due(gift.arrive); }), gift_pending.end());
    for (auto& pending : map_pending)
        if (due(pending.intel.turn) && alive(pending.to))
            append_recent(maps[pending.to], std::move(pending.intel), 3);
    map_pending.erase(std::remove_if(map_pending.begin(), map_pending.end(),
                      [&](const MapDelivery& map) { return due(map.intel.turn); }), map_pending.end());
    for (const auto& spy : spy_pending) {
        if (!due(spy.arrive) || !alive(spy.from) || !alive(spy.to)) continue;
        append_recent(econ_intel[spy.from], econ_snapshot(spy.to, spy.arrive), 2);
        append_recent(maps[spy.from], map_snapshot(spy.to, spy.arrive, true), 3);
    }
    spy_pending.erase(std::remove_if(spy_pending.begin(), spy_pending.end(),
                      [&](const Spy& spy) { return due(spy.arrive); }), spy_pending.end());
}
