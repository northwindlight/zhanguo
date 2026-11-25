#include "rl_env.hpp"

#include <iomanip>
#include <iostream>

namespace {

template <typename Container>
void read_numbers(Container& values) {
    for (auto& v : values) std::cin >> v;
}

template <typename T, typename Reader>
void read_list(std::vector<T>& values, Reader reader) {
    size_t count;
    std::cin >> count;
    values.resize(count);
    for (auto& value : values) reader(value);
}

template <typename Row>
void write_row(const Row& row) {
    for (auto v : row) std::cout << ' ' << v;
}

template <typename Rows>
void write_array(const char* key, const Rows& rows) {
    std::cout << key;
    for (const auto& row : rows) write_row(row);
    std::cout << '\n';
}

World read_world() {
    World w;
    std::cin >> w.size >> w.turn >> w.max_turns >> w.bank_on;
    read_list(w.nations, [&](Nation& n) {
        double principal;
        std::cin >> n.id >> n.alive >> n.gdp_last >> n.energy_out >> n.energy_use
                 >> n.grid_short >> n.built_diplomacy_center >> principal;
        read_numbers(n.res);
        if (principal) w.loans[n.id].principal = principal;
    });
    w.econ_reports.resize(w.nations.size());
    w.econ_intel.resize(w.nations.size());
    w.maps.resize(w.nations.size());
    read_list(w.tiles, [](Tile& t) {
        int built, recruited, militia;
        std::cin >> t.owner >> t.core >> t.terrain >> built >> recruited >> militia;
        t.built = built;
        t.recruited = recruited;
        t.militia_rec = militia;
        read_numbers(t.tres);
        read_numbers(t.map_resources);
        read_numbers(t.bld);
        read_numbers(t.pending);
    });
    read_list(w.armies, [](Army& a) {
        std::cin >> a.id >> a.aid >> a.owner >> a.kind >> a.x >> a.y >> a.hp >> a.moved_turn >> a.engaged >> a.has_retreat;
    });
    read_numbers(w.price);
    read_numbers(w.minv);
    read_numbers(w.mref);
    int count;
    std::cin >> count;
    for (int i = 0; i < count; ++i) {
        int n;
        double carry;
        std::cin >> n >> carry;
        w.carry[n] = carry;
    }
    std::cin >> count;
    for (int i = 0; i < count; ++i) {
        int n, side, good, qty;
        std::cin >> n >> side >> good >> qty;
        w.mvolume[n * 16 + side * 8 + good] = qty;
    }
    read_list(w.blocs, [](Bloc& b) {
        int count;
        std::cin >> b.id >> b.chief >> count;
        b.members.resize(count);
        read_numbers(b.members);
    });
    read_list(w.wars, [](War& war) {
        for (auto* side : {&war.atk_ents, &war.def_ents}) {
            size_t count;
            std::cin >> count;
            side->resize(count);
            read_numbers(*side);
        }
    });
    read_list(w.pacts, [](Pact& p) { std::cin >> p.kind >> p.a >> p.b; });
    read_list(w.truces, [](Truce& t) { std::cin >> t.a >> t.b >> t.until; });
    read_list(w.peace, [](PeaceOffer& p) { std::cin >> p.id >> p.a >> p.b >> p.kind >> p.gold; });
    read_list(w.proposals, [](Proposal& p) {
        std::cin >> p.id >> p.kind >> p.a >> p.b >> p.asker;
        read_list(p.invitees, [](int& n) { std::cin >> n; });
        read_list(p.accepted, [](int& n) { std::cin >> n; });
    });
    read_list(w.votes, [](Vote& v) { std::cin >> v.id >> v.bloc >> v.deadline; });
    read_list(w.diplo_q, [](World::DiploCmd&) {});
    std::cin >> count;
    for (int i = 0; i < count; ++i) {
        int subject, overlord;
        std::cin >> subject >> overlord;
        w.vassals[subject] = {overlord, 0, Num::TRIBUTE};
    }
    read_list(w.spy_pending, [](World::Spy& s) { std::cin >> s.from >> s.to >> s.arrive; });
    const auto recon = [](ReconTile& t) {
        std::cin >> t.cell >> t.castle >> t.slots >> t.pending;
        read_numbers(t.built);
        t.owned = true;
    };
    for (size_t n = 0; n < w.nations.size(); ++n) {
        read_list(w.econ_reports[n], [](Report& r) {
            bool has_ratio;
            double ratio;
            std::cin >> r.report_turn >> r.gdp >> r.assets >> r.invest >> has_ratio >> ratio;
            if (has_ratio) r.military_ratio = ratio;
        });
        read_list(w.econ_intel[n], [&](EconIntel& e) {
            std::cin >> e.source >> e.turn >> e.report_turn >> e.blackout >> e.energy >> e.demand;
            read_numbers(e.resources);
            read_numbers(e.armies);
            read_list(e.tiles, recon);
        });
        read_list(w.maps[n], [&](MapIntel& m) {
            std::cin >> m.source >> m.turn >> m.stolen;
            read_list(m.tiles, recon);
        });
    }
    return w;
}

}  // namespace

int main() {
    std::cout << std::setprecision(9);
    std::unordered_map<int, rl::Observer> observers;
    std::unordered_map<int, rl::DiplomacyCache> diplomacy;
    std::unordered_map<int, std::vector<rl::EnemySighting>> sight;
    int seat, reset, use_cache;
    while (std::cin >> seat >> reset >> use_cache) {
        if (reset) {
            observers.clear();
            diplomacy.clear();
            sight.clear();
        }
        World w = read_world();
        std::array<int, rl::type_count> hist{};
        read_numbers(hist);
        int invalid, steps;
        if (!(std::cin >> invalid >> steps)) return 1;
        const auto cs = rl::candidates(w, seat, true, use_cache ? &diplomacy[seat] : nullptr);
        auto& observer = observers[seat];
        const auto o = observer.encode(w, seat, cs);
        const auto summary = rl::turn_features(o, hist, invalid, steps);
        const auto lost = rl::aggregate_lost(sight[seat], observer.sight(), w.size);
        sight[seat] = observer.sight();

        std::cout << "candidates";
        for (const auto& c : cs) {
            std::cout << ' ' << static_cast<int>(c.type) << ' ' << c.executable;
            write_row(c.args);
        }
        std::cout << "\nglobals";
        write_row(o.globals);
        std::cout << '\n';
        write_array("market", o.market);
        write_array("nations", o.nations);
        write_array("tiles", o.tiles);
        write_array("armies", o.armies);
        write_array("cand", o.cand);
        write_array("cand_ptr", o.cand_ptr);
        std::cout << "cand_exec";
        write_row(o.cand_exec);
        std::cout << "\ncand_type";
        write_row(o.cand_type);
        std::cout << "\nsummary";
        write_row(summary);
        std::cout << "\nlost";
        write_row(lost);
        std::cout << "\nscore " << rl::power_score(w, seat) << '\n';
    }
}
