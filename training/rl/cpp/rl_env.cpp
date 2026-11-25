#include "rl_env.hpp"

namespace rl {

void Observer::update_intelligence(const World& w, int seat) {
    if (spy_.size() != w.nations.size()) {
        spy_.resize(w.nations.size());
        reports_.resize(w.nations.size());
        map_turn_.resize(w.nations.size(), -1);
    }
    recon_.resize(w.tiles.size());
    for (const auto& entry : w.econ_intel[seat]) {
        if (entry.report_turn) {
            auto& last = reports_[entry.source];
            if (last && last->acquired >= entry.turn) continue;
            const auto& history = w.econ_reports[entry.source];
            auto match = std::find_if(history.begin(), history.end(), [&](const Report& report) {
                return report.report_turn == entry.report_turn;
            });
            if (match != history.end()) last = KnownReport{entry.turn, *match};
            continue;
        }
        auto& last = spy_[entry.source];
        if (last && last->turn >= entry.turn) continue;
        last = entry;
        for (const auto& tile : entry.tiles)
            if (recon_[tile.cell].turn <= entry.turn)
                recon_[tile.cell] = {entry.turn, entry.source, true, tile};
    }
    for (const auto& entry : w.maps[seat]) {
        map_turn_[entry.source] = std::max(map_turn_[entry.source], entry.turn);
        for (const auto& tile : entry.tiles)
            if (tile.owned && recon_[tile.cell].turn < entry.turn)
                recon_[tile.cell] = {entry.turn, entry.source, false, tile};
    }
}

void Observer::encode_intelligence(const World& w, int seat, int target,
                                  std::array<float, n_dim>& row) const {
    const auto age = [&](int turn) { return std::min(1.0, (w.turn - turn) / 50.0); };
    if (const auto& spy = spy_[target]) {
        double stock = 0, army_value = 0;
        int castles = 0, slots = 0;
        std::array<int, BLD_N> buildings{};
        for (int r = FOOD; r < RES_N; ++r) stock += spy->resources[r] * Num::M_BASE[r - 1];
        for (const auto& tile : spy->tiles) {
            castles += tile.castle;
            slots += tile.slots;
            for (int b = 0; b < BLD_N; ++b) buildings[b] += tile.built[b];
        }
        for (int k = 0; k < KIND_N; ++k) {
            row[21 + k] = spy->armies[k] / 10.0;
            double cost = Num::U_REC_FOOD[k] * Num::M_BASE[FOOD - 1] +
                          Num::U_REC_EQUIP[k] * Num::M_BASE[EQUIP - 1] + Num::U_REC_GOLD[k];
            army_value += spy->armies[k] * cost;
        }
        row[13] = 1;
        row[14] = age(spy->turn);
        row[15] = spy->resources[GOLD] / 1000;
        row[16] = stock / 1000;
        row[17] = spy->blackout;
        row[18] = spy->tiles.size() / double(w.size * w.size);
        row[19] = castles / 10.0;
        row[20] = slots / 50.0;
        row[24] = army_value / 1000;
        row[25] = buildings[TOWNHALL] / 3.0;
        row[26] = buildings[BARRACKS] / 5.0;
        row[27] = buildings[WATCHTOWER] / 3.0;
    }
    int pending = -1;
    for (const auto& spy : w.spy_pending)
        if (spy.from == seat && spy.to == target && (pending < 0 || spy.arrive < pending)) pending = spy.arrive;
    if (pending >= 0) row[28] = (pending - w.turn) / 3.0;
    if (const auto& last = reports_[target]) {
        row[29] = 1;
        row[30] = age(last->acquired);
        row[31] = last->report.gdp / 100;
        row[32] = last->report.military_ratio.value_or(0);
        row[33] = last->report.assets / 5000;
        row[34] = last->report.invest / 500;
    }
    if (map_turn_[target] >= 0) {
        row[35] = 1;
        row[36] = age(map_turn_[target]);
    }
    row[37] = !w.econ_reports[target].empty();
}

std::vector<char> visible_set(const World& w, int seat) {
    std::vector<char> visible(w.tiles.size(), false);
    const Bloc* bloc = w.bloc_of(seat);
    for (int i = 0; i < static_cast<int>(w.tiles.size()); ++i) {
        const Tile& t = w.tiles[i];
        if (t.owner != seat && (!bloc || std::find(bloc->members.begin(), bloc->members.end(), t.owner) == bloc->members.end()))
            continue;
        const int x = i / w.size, y = i % w.size;
        const bool tower = t.bld[WATCHTOWER] > 0;
        const int range = tower ? Num::B_VISION : 1;
        for (int dx = -range; dx <= range; ++dx)
            for (int dy = -range; dy <= range; ++dy)
                if ((!tower || dx * dx + dy * dy <= range * range) && w.in_bounds(x + dx, y + dy))
                    visible[(x + dx) * w.size + y + dy] = true;
    }
    return visible;
}

Observation Observer::encode(const World& w, int seat, const std::vector<Candidate>& candidates) {
    Observation result;
    update_intelligence(w, seat);
    memory_.resize(w.tiles.size());
    sight_.clear();
    result.tiles.resize(w.tiles.size());
    result.nations.resize(w.nations.size());
    const auto visible = visible_set(w, seat);
    const auto& nation = w.nations.at(seat);
    const Bloc* bloc = w.bloc_of(seat);
    const float size = w.size;
    const float area = size * size;
    const float nation_count = w.nations.size();
    const int entity = w.entity(seat);

    std::vector<int> land(w.nations.size(), 0), troops(w.nations.size(), 0);
    int own_land = 0, own_troops = 0;
    for (size_t i = 0; i < w.tiles.size(); ++i) {
        const auto& t = w.tiles[i];
        auto& mem = memory_[i];
        if (t.owner == seat) ++own_land;
        auto& row = result.tiles[i];
        row[0] = (i / w.size) / size;
        row[1] = (i % w.size) / size;
        if (visible[i]) {
            if (mem.last_turn < 0) {
                mem.terrain = t.terrain;
                mem.resources = t.map_resources;
            }
            mem.last_turn = w.turn;
            mem.owner_class = t.owner < 0 ? 0 : t.owner == seat ? 1 : 2;
            mem.enemy_hp = 0;
            mem.hall = mem.hall || t.bld[TOWNHALL] > 0;
            row[15] = row[16 + mem.owner_class] = 1;
            row[19] = mem.owner_class == 2 && w.entity(t.owner) == entity;
            if (t.owner >= 0) {
                row[20] = t.owner / nation_count;
                ++land[t.owner];
            }
            for (int b = 0; b < BLD_N; ++b)
                if (mem.owner_class != 2 || b == CASTLE || b == TOWNHALL)
                    row[21 + b] = t.bld[b] / 5.0f;
            if (t.owner == seat)
                row[37] = std::accumulate(t.pending.begin(), t.pending.end(), 0) / 3.0f;
            row[38] = t.core == seat;
        } else if (mem.last_turn >= 0) {
            row[41] = 1;
            row[42] = std::min(1.0f, (w.turn - mem.last_turn) / 50.0f);
            row[43 + mem.owner_class] = 1;
            row[46] = mem.enemy_hp / 100;
        }
        if (mem.last_turn >= 0) {
            row[2] = row[3 + mem.terrain] = 1;
            for (int r = 0; r < TR_N; ++r) row[10 + r] = mem.resources[r] / 5.0f;
        }
        row[47] = mem.hall;
        const auto& intel = recon_[i];
        if (intel.turn >= 0) {
            row[48] = 1;
            row[49] = std::min(1.0, (w.turn - intel.turn) / 50.0);
            row[50] = (intel.source + 1) / double(w.nations.size());
            row[51] = intel.tile.castle / 5.0;
            row[52] = intel.tile.slots / 20.0;
            if (intel.buildings) {
                row[53] = 1;
                for (int b = 0; b < BLD_N; ++b) row[54 + b] = intel.tile.built[b] / 5.0f;
                row[70] = intel.tile.pending / 3.0;
            }
        }
    }

    std::unordered_map<int, int> own_army_rows;
    for (const auto& army : w.armies) {
        if (army.hp <= 0) continue;
        int i = army.x * w.size + army.y;
        bool own = army.owner == seat;
        if (!own && !visible[i]) continue;
        if (army.owner < 0) {
            result.tiles[i][39] = 1;
            result.tiles[i][40] = army.hp / 100;
            continue;
        }
        ++troops[army.owner];
        if (own) {
            own_army_rows[army.aid] = result.armies.size();
            ++own_troops;
        } else {
            sight_.push_back({army.owner, army.aid, army.x, army.y, army.kind, army.hp});
            memory_[i].enemy_hp += army.hp;
        }
        auto& row = result.armies.emplace_back(std::array<float, a_dim>{});
        row[army.kind] = 1;
        row[3] = army.hp / Num::U_HP[army.kind];
        row[4] = army.x / size;
        row[5] = army.y / size;
        row[6] = own;
        row[7] = !own && w.at_war(seat, army.owner);
        row[8] = army.engaged;
        row[9] = army.moved_turn == w.turn;
        row[10] = army.has_retreat;
        row[11] = army.owner / nation_count;
    }

    auto& g = result.globals;
    g[0] = static_cast<float>(w.turn) / w.max_turns;
    g[1] = static_cast<float>(w.max_turns - w.turn) / w.max_turns;
    g[2] = std::count_if(w.nations.begin(), w.nations.end(), [](const Nation& n) { return n.alive; }) / nation_count;
    g[3] = nation.res[GOLD] / 1000.0f;
    for (int r = FOOD; r < RES_N; ++r) g[3 + r] = nation.res[r] / 100.0f;
    g[10] = w.nation_gdp(seat) / 100;
    g[11] = w.bank_credit(seat) / 500.0f;
    if (auto loan = w.loans.find(seat); loan != w.loans.end()) g[12] = loan->second.principal / 500;
    g[13] = own_land / area;
    g[14] = own_troops / 10.0f;
    g[15] = w.at_war_any(seat);
    g[16] = bloc != nullptr;
    g[17] = w.vassals.count(seat);
    g[18] = std::any_of(w.vassals.begin(), w.vassals.end(), [seat](const auto& v) { return v.second.overlord == seat; });
    g[19] = w.nation_bld_count(seat, TOWNHALL) / 3.0f;
    g[20] = nation.grid_short;
    g[21] = nation.energy_out / 10.0f;
    g[22] = nation.energy_use / 10.0f;
    g[23] = std::count_if(w.peace.begin(), w.peace.end(), [seat](const PeaceOffer& p) { return p.b == seat; }) / 3.0f;
    g[24] = w.proposals.size() / 3.0f;
    if (bloc)
        g[25] = std::count_if(w.votes.begin(), w.votes.end(), [bloc](const Vote& v) { return v.bloc == bloc->id; }) / 3.0f;
    g[26] = w.diplo_q.size() / 3.0f;
    g[27] = w.bank_on;
    g[28] = std::count_if(w.spy_pending.begin(), w.spy_pending.end(),
        [seat](const World::Spy& s) { return s.from == seat; }) / 3.0;

    for (int r = 0; r < 6; ++r) {
        auto& row = result.market[r];
        row[0] = w.price[r] / Num::M_BASE[r];
        row[1] = static_cast<float>(w.minv[r]) / Num::M_DEPTH[r];
        row[2] = w.mref[r] / Num::M_DEPTH[r];
        row[3] = nation.res[r + 1] / 100.0f;
        row[4] = r / 6.0f;
        row[5] = 1;
    }

    for (int n = 0; n < static_cast<int>(w.nations.size()); ++n) {
        auto& row = result.nations[n];
        row[0] = n == seat;
        row[1] = w.alive(n);
        if (n == seat || !w.alive(n)) continue;
        const int other = w.entity(n);
        row[2] = w.at_war(seat, n);
        row[3] = w.has_truce(seat, n);
        row[4] = entity == other;
        row[5] = std::any_of(w.pacts.begin(), w.pacts.end(), [&](const Pact& p) {
            return p.kind == 1 && ((p.a == entity && p.b == other) || (p.a == other && p.b == entity));
        });
        for (const auto& p : w.pacts) {
            if (p.kind != 0) continue;
            if (p.a == other && p.b == entity) row[6] = 1;
            if (p.a == entity && p.b == other) row[7] = 1;
        }
        if (auto v = w.vassals.find(n); v != w.vassals.end()) row[8] = v->second.overlord == seat;
        if (auto v = w.vassals.find(seat); v != w.vassals.end()) row[9] = v->second.overlord == n;
        row[10] = land[n] / area;
        row[11] = troops[n] / 10.0f;
        row[12] = w.bloc_of(n) != nullptr;
        encode_intelligence(w, seat, n, row);
    }

    result.cand.reserve(candidates.size());
    result.cand_ptr.reserve(candidates.size());
    result.cand_type.reserve(candidates.size());
    result.cand_exec.reserve(candidates.size());
    for (const auto& c : candidates) {
        result.cand.push_back(c.features);
        result.cand_type.push_back(static_cast<int32_t>(c.type));
        result.cand_exec.push_back(c.features[0]);
        auto& ptr = result.cand_ptr.emplace_back(std::array<int32_t, 3>{-1, -1, -1});
        const auto [a, b, d] = c.args;
        switch (c.type) {
        case Type::Build: case Type::Demolish: case Type::Recruit:
            ptr[0] = a * w.size + b;
            break;
        case Type::Move: case Type::Attack: case Type::Retreat:
            ptr[0] = b * w.size + d;
            [[fallthrough]];
        case Type::Disband:
            if (auto it = own_army_rows.find(a); it != own_army_rows.end()) ptr[1] = it->second;
            break;
        case Type::DeclareWar: case Type::OfferPeace: case Type::Guarantee: case Type::ProposeDefense:
        case Type::ProposeVassalage: case Type::SubmitVassalage:
        case Type::ReleaseVassal: case Type::BlocFound: case Type::Gift:
        case Type::Spy: case Type::BuyReport: case Type::ShareMap:
        case Type::CancelGuarantee: case Type::BreakDefense:
            ptr[2] = a;
            break;
        default:
            break;
        }
    }
    return result;
}

std::array<float, f_dim> turn_features(const Observation& o,
                                      const std::array<int, type_count>& previous,
                                      int invalid, int steps) {
    std::array<float, f_dim> out{};
    std::copy(o.globals.begin(), o.globals.end(), out.begin());
    for (int i = 0; i < type_count; ++i) out[g_dim + i] = std::min(previous[i], 20) / 5.0f;
    const int base = g_dim + type_count;
    int enemies = 0;
    float hp = 0, wars = 0, distance = 2;
    bool has_own_tile = false;
    for (const auto& army : o.armies) {
        if (army[6]) continue;
        ++enemies;
        hp += army[3];
        wars += army[7];
        for (const auto& t : o.tiles) {
            if (!t[17]) continue;
            const float d = std::abs(army[4] - t[0]) + std::abs(army[5] - t[1]);
            has_own_tile = true;
            distance = std::min(distance, d);
        }
    }
    if (enemies) {
        out[base] = enemies / 10.0f;
        out[base + 1] = hp / enemies;
        out[base + 2] = has_own_tile ? distance : 1;
        out[base + 3] = wars / enemies;
    }
    for (const auto& t : o.tiles) {
        if (!t[18] || !t[15]) continue;
        ++out[base + 4];
        out[base + 5] += t[21];
        out[base + 6] += t[32];
    }
    out[base + 4] /= o.tiles.size();
    out[base + 5] /= 5;
    out[base + 6] /= 3;
    out[base + 7] = invalid / 30.0f;
    out[base + 8] = steps / 30.0f;
    int intel = 0;
    for (const auto& nation : o.nations)
        intel += (nation[13] > 0 && nation[14] == 0) + (nation[35] > 0 && nation[36] == 0);
    out[base + 9] = intel / 3.0;
    return out;
}

std::array<float, aux_dim> aggregate_lost(const std::vector<EnemySighting>& prev,
                                         const std::vector<EnemySighting>& cur, int size) {
    std::array<float, aux_dim> out{};
    for (const auto& a : prev) {
        if (std::any_of(cur.begin(), cur.end(), [&](const EnemySighting& b) { return a.owner == b.owner && a.aid == b.aid; }))
            continue;
        out[0] += 0.25f;
        out[1] += a.hp / 400;
        out[2] += (a.x - size / 2.0f) / size / 4;
        out[3] += (a.y - size / 2.0f) / size / 4;
        out[4 + a.kind] = std::min(1.0f, out[4 + a.kind] + 0.25f);
    }
    out[0] = std::min(1.0f, out[0]);
    out[1] = std::min(1.0f, out[1]);
    return out;
}

double power_score(const World& w, int seat) {
    if (!w.alive(seat)) return 0;
    double result = w.res(seat, GOLD);
    for (int r = FOOD; r < RES_N; ++r) result += w.res(seat, r) * Num::M_BASE[r - 1];
    for (const auto& t : w.tiles) {
        if (t.owner != seat) continue;
        result += 50;
        for (int b = 0; b < BLD_N; ++b) {
            const int count = t.bld[b];
            result += count * Num::B_WOOD[b] * Num::M_BASE[1];
            if (b == CASTLE) {
                for (int i = 0; i < count; ++i) result += Num::build_base_cost(b, i);
            } else {
                result += Num::B_GOLD[b] * count;
            }
        }
    }
    for (const auto& a : w.armies) {
        if (a.owner != seat || a.hp <= 0) continue;
        const double cost = Num::U_REC_FOOD[a.kind] * Num::M_BASE[0]
            + Num::U_REC_EQUIP[a.kind] * Num::M_BASE[4] + Num::U_REC_GOLD[a.kind];
        result += cost * a.hp / Num::U_HP[a.kind];
    }
    if (auto loan = w.loans.find(seat); loan != w.loans.end()) result -= loan->second.principal;
    return result;
}

}  // namespace rl
