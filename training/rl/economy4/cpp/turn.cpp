// 回合结算（main hooks 顺序）与回合推进。
#include "engine.hpp"

namespace {
template<class T, class Pred>
void erase_if(std::vector<T>& values, Pred pred) {
    values.erase(std::remove_if(values.begin(), values.end(), pred), values.end());
}
}

void World::settle_deaths() {
    for (int n = 0; n < (int)nations.size(); n++)
        if (alive(n)) eliminate_if_dead(n);
}

void World::eliminate_if_dead(int n) {
    if (!alive(n)) return;
    bool hall = false;
    for (int i = 0; i < size * size; i++)
        if (tiles[i].owner == n && tiles[i].bld[TOWNHALL] > 0) { hall = true; break; }
    if (hall) return;
    for (auto& a : armies) if (a.owner >= 0 && tiles[a.x * size + a.y].owner >= 0) a.engaged = false;
    for (int i = 0; i < size * size; i++)
        if (tiles[i].owner == n) { if(deferred_orders)cancel_site_reservations(i/size,i%size);tiles[i].owner = -1; tiles[i].core = -1; }
    nations[n].alive = false;
    ledgers[n] = {};
    econ_intel[n].clear();
    maps[n].clear();
    erase_if(gift_pending, [n](const Gift& g) { return g.from == n || g.to == n; });
    erase_if(map_pending, [n](const MapDelivery& m) { return m.intel.source == n || m.to == n; });
    erase_if(spy_pending, [n](const Spy& s) { return s.from == n || s.to == n; });
    erase_if(pending_valuations, [n](const Valuation& v) { return v.nation == n; });
    armies.erase(std::remove_if(armies.begin(), armies.end(),
                                [n](const Army& a) { return a.owner == n; }), armies.end());
    wars.clear();
    proposals.clear();
    peace.clear();
    votes.clear();
    pacts.erase(std::remove_if(pacts.begin(), pacts.end(), [&](const Pact& p) {
        return p.a == n || p.b == n;
    }), pacts.end());
    // 附庸释放
    for (auto it = vassals.begin(); it != vassals.end();) {
        if (it->first == n || it->second.overlord == n) it = vassals.erase(it);
        else ++it;
    }
    // 全幸存者两两强制休战
    std::vector<int> live;
    for (int m = 0; m < (int)nations.size(); m++) if (alive(m)) live.push_back(m);
    for (size_t i = 0; i < live.size(); i++)
        for (size_t j = i + 1; j < live.size(); j++)
            add_truce(live[i], live[j], turn + Num::FALL_TRUCE);
}

void World::snapshot_cores(const std::vector<int>& ns) {
    for (int i = 0; i < size * size; i++) {
        int o = tiles[i].owner;
        if (o >= 0 && std::find(ns.begin(), ns.end(), o) != ns.end()) tiles[i].core = o;
    }
}

void World::resolve_turn() {
    for (int n = 0; n < static_cast<int>(nations.size()); ++n)
        if (alive(n)) ledger(n);
    const auto before = ledgers;
    if (deferred_orders) advance_production_orders();
    // 0) 配额刷新
    for (auto& t : tiles) { t.recruited = 0; t.built = 0; t.militia_rec = 0; }
    // 1) cycle.start：计票
    tally_votes();
    // 2) 生产（采集/电网/工厂/市政厅）
    production_phase();
    // 3) 附庸上贡已在生产段逐笔扣下（tribute_take → tribute_flush 划给宗主）

    // 4) 外交结算（main relations 挂点：生产之后、补给与战斗之前）
    resolve_diplomacy();
    // 4.5) 未回应的提议自动拒绝（main end_turn 行为）
    proposals.erase(std::remove_if(proposals.begin(), proposals.end(),
                                    [&](const Proposal& p) { return p.turn < turn; }), proposals.end());
    peace.erase(std::remove_if(peace.begin(), peace.end(),
                               [&](const PeaceOffer& p) { return p.turn < turn; }), peace.end());
    // 5) 补给与回血
    supply_phase();
    // 5) 战斗
    const auto battle_cells = resolve_battles();
    // 6) 撤退落地
    for (auto& a : armies) {
        if (!a.has_retreat || a.hp <= 0) continue;
        const int ox = a.x, oy = a.y;
        a.has_retreat = false;
        a.retreat_cover = 100;
        auto retreat_to = [&](int x, int y) {
            if (!retreat_legal(a.owner, x, y)) return false;
            a.x = x;
            a.y = y;
            a.engaged = false;
            return true;
        };
        if (retreat_to(a.rx, a.ry)) continue;
        bool moved = false;
        for (int x = ox - 1; x <= ox + 1 && !moved; ++x) {
            for (int y = oy - 1; y <= oy + 1 && !moved; ++y) {
                if (x != ox || y != oy) moved = retreat_to(x, y);
            }
        }
    }
    for (const auto& [x, y] : battle_cells) {
        int owner = owner_of(x, y);
        if (!alive(owner)) continue;
        std::vector<int> foes;
        bool guarded = false;
        for (const auto& a : armies) {
            if (a.x != x || a.y != y || a.hp <= 0) continue;
            guarded |= a.owner == owner;
            if (at_war(a.owner, owner)) foes.push_back(a.owner);
        }
        if (guarded) continue;
        int winner = claim_battle(x, y, foes, false);
        if (winner >= 0) conquer(x, y, winner);
    }
    // 7) 亡国与强制和平（succession）
    settle_deaths();
    // 8) 遣返 + 脱离清扫
    withdraw_illegal();
    clear_disengaged();
    // 9) 市场结算
    market_settle();
    for (const auto& v : pending_valuations)
        ledgers[v.nation].*(v.field) += v.amount * price[v.resource - 1] - v.initial;
    pending_valuations.clear();
    // 10) 施工落成
    completion_phase();
    // 11) 央行
    bank_settle();
    close_report_period(before);
    // 12) 事件
    logbuf.clear();
}

void World::begin_turn() {
    ++turn;
    deliver_intel();
}
