// 回放器：读 ref 转换文本（init + 动作）→ 重放 → 精简 dump（与参照对局逐回合可比）。
#include "engine.hpp"
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>

struct ActLine {
    int turn = 0, actor = 0;
    std::string name;
    std::vector<std::string> args;
    int expected = -1;
};
struct InitNation {
    int cx = 0, cy = 0;
    std::array<double, RES_N> res{};
};

static std::vector<std::string> split_ws(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) {
        if (c == ' ' || c == '\t' || c == '\r') {
            if (!cur.empty()) { out.push_back(cur); cur.clear(); }
        } else cur += c;
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

static int find_aid(World& w, int actor, int aid, int& idx) {
    for (size_t k = 0; k < w.armies.size(); k++)
        if (w.armies[k].owner == actor && w.armies[k].aid == aid && w.armies[k].hp > 0) {
            idx = (int)k;
            return 0;
        }
    return -1;
}

static bool run_action(World& w, int actor, const std::string& nm,
                       const std::vector<std::string>& a, std::string& note) {
    auto I = [&](size_t i) { return a.size() > i ? std::stoi(a[i]) : 0; };
    note = "";
    if (nm == "build") {
        bool ok = w.build(actor, I(0), I(1), I(2));
        if (!ok && w.in_bounds(I(0), I(1))) {
            Tile& t = w.tile(I(0), I(1));
            note = "build rejected x=" + std::to_string(I(0)) + " y=" + std::to_string(I(1)) + " b=" + std::to_string(I(2)) + " own=" + std::to_string(t.owner) + " built=" + std::to_string((int)t.built)
                 + " slots=" + std::to_string(t.bld[0] + t.bld[1] + t.bld[2] + t.bld[3]) 
                 + " g=" + std::to_string(w.nations[actor].res[GOLD]) + " w=" + std::to_string(w.nations[actor].res[WOOD]);
        }
        return ok;
    }
    if (nm == "recruit") return w.recruit(actor, I(0), I(1), I(2), I(3));
    if (nm == "demolish") return w.demolish(actor, I(0), I(1), I(2));
    if (nm == "buy") return w.buy(actor, I(0), I(1));
    if (nm == "sell") return w.sell(actor, I(0), I(1));
    if (nm == "move" || nm == "attack" || nm == "retreat") {
        // move/retreat: "<aid> <x> <y>"；attack: "<aid...> <x> <y>"（最后两个是坐标）
        int X = 0, Y = 0;
        std::vector<int> aids;
        if (nm == "attack") {
            if (a.size() < 3) return false;
            X = std::stoi(a[a.size() - 2]);
            Y = std::stoi(a[a.size() - 1]);
            for (size_t i = 0; i + 2 < a.size(); i++) aids.push_back(std::stoi(a[i]));
        } else {
            if (a.size() < 3) return false;
            X = std::stoi(a[1]);
            Y = std::stoi(a[2]);
            aids.push_back(std::stoi(a[0]));
        }
        std::vector<int> lids;
        for (int aid : aids) {
            int idx = -1;
            if (find_aid(w, actor, aid, idx) != 0) { note = "aid not found"; return false; }
            lids.push_back(w.armies[idx].id);
        }
        if (nm == "move") {
            bool ok = w.move_army(actor, lids[0], X, Y);
            if (!ok) {
                for (auto& x : w.armies) if (x.id == lids[0])
                    note = "eng=" + std::to_string((int)x.engaged) + " mv=" + std::to_string(x.moved_turn)
                         + " turn=" + std::to_string(w.turn) + " own=" + std::to_string(w.owner_of(X, Y))
                         + " hp=" + std::to_string((int)x.hp);
            }
            return ok;
        }
        if (nm == "attack") {
            bool ok = w.attack(actor, lids, X, Y);
            if (!ok) {
                for (auto& x : w.armies) if (x.id == lids[0]) {
                    bool reach = false;
                    for (auto& p : w.reachable(actor, x, true))
                        if (p.first.first == X && p.first.second == Y) reach = true;
                    note = "eng=" + std::to_string((int)x.engaged) + " mv=" + std::to_string(x.moved_turn)
                         + " turn=" + std::to_string(w.turn) + " at=" + std::to_string(x.x) + "," + std::to_string(x.y)
                         + " reach=" + std::to_string((int)reach) + " own=" + std::to_string(w.owner_of(X, Y));
                }
            }
            if (ok) {
                for (auto& x : w.armies) if (x.id == lids[0])
                    note = "post=" + std::to_string(x.x) + "," + std::to_string(x.y) + " eng=" + std::to_string((int)x.engaged);
            }
            return ok;
        }
        return w.retreat(actor, lids[0], X, Y);
    }
    if (nm == "disband") {
        int aid = I(0), idx = -1;
        if (find_aid(w, actor, aid, idx) != 0) { note = "aid not found"; return false; }
        return w.disband(actor, w.armies[idx].id);
    }
    // 外交动作走命令队列：提交时干跑校验+预扣费，回合 relations 挂点统一重验生效（失败退款）
    if (nm == "declare_war" || nm == "offer_peace" || nm == "accept_peace" || nm == "reject_peace" ||
        nm == "declare_guarantee" || nm == "cancel_guarantee" || nm == "propose_pact" ||
        nm == "accept_pact" || nm == "reject_pact" || nm == "propose_vassalage" ||
        nm == "submit_vassalage" || nm == "accept_vassalage" || nm == "reject_vassalage" ||
        nm == "release_vassal" || nm == "independence_war" || nm == "propose_bloc" ||
        nm == "bloc_join" || nm == "accept_founding" || nm == "bloc_leave" || nm == "bloc_transfer" || nm == "bloc_rename")
        return w.submit_diplo(actor, nm, a);
    if (nm == "vote") return w.vote(actor, I(0), I(1));
    if (nm == "bank_rate") return w.bank_set_rate(a.size() > 0 ? std::stod(a[0]) : 0.0);
    if (nm == "bank_loan") return w.bank_loan(actor);
    if (nm == "gift") return w.gift(actor, I(0), I(1), I(2));
    if (nm == "send_letter") return w.send_letter(actor, I(0), I(1));
    if (nm == "share_map") return w.share_map(actor, I(0));
    if (nm == "spy") return w.spy_send(actor, I(0));
    note = "unknown action";
    return false;
}

static void dump_turn(const World& w, std::ostream& o) {
    int t = w.turn;
    o << std::defaultfloat << std::setprecision(17);
    for (int n = 0; n < (int)w.nations.size(); n++) {
        const Nation& x = w.nations[n];
        o << "S " << t << " " << n;
        for (int r = 0; r < RES_N; r++) o << " " << x.res[r];
        o << "\n";
        int land = 0;
        for (int i = 0; i < w.size * w.size; i++) if (w.tiles[i].owner == n) land++;
        o << "N " << t << " " << n << " " << x.alive << "\n";
        o << "L " << t << " " << n << " " << land << "\n";
        o << "G " << t << " " << n << " " << x.gdp_last << "\n";
    }
    for (int x = 0; x < w.size; x++)
        for (int y = 0; y < w.size; y++) {
            const Tile& t2 = w.tiles[x * w.size + y];
            if (t2.owner < 0) continue;
            for (int b = 0; b < BLD_N; b++)
                for (int k = 0; k < t2.bld[b]; k++) o << "B " << t << " " << t2.owner << " " << b << " " << x << " " << y << "\n";
        }
    for (auto& a : w.armies) {
        if (a.owner < 0 || a.hp <= 0) continue;
        o << "A " << t << " " << a.owner << " " << a.aid << " " << a.kind << " " << a.x << " " << a.y << " "
          << a.hp << " " << (int)a.engaged << "\n";
    }
    for (const auto& [n, loan] : w.loans)
        o << "K " << n << " " << loan.principal << " " << loan.due << " " << loan.turns_left << "\n";
    for (const auto& [n, v] : w.vassals)
        o << "V " << n << " " << v.overlord << " " << v.tribute << "\n";
    for (const auto& b : w.blocs) {
        o << "C " << b.id << " " << b.chief << " " << std::quoted(b.name);
        for (int member : b.members) o << " " << member;
        o << "\n";
    }
    for (const auto& p : w.pacts) o << "P " << p.kind << " " << p.a << " " << p.b << "\n";
    for (const auto& tr : w.truces)
        if (tr.until > w.turn) o << "T " << tr.a << " " << tr.b << " " << tr.until << "\n";
    for (const auto& war : w.wars) {
        o << "W";
        for (const auto* side : {&war.atk_ents, &war.def_ents}) {
            std::vector<int> seats;
            for (int e : *side) {
                auto members = w.entity_members(e);
                seats.insert(seats.end(), members.begin(), members.end());
            }
            o << " " << seats.size();
            for (int n : seats) o << " " << n;
        }
        o << "\n";
    }
    o << "M " << t;
    for (int g = 0; g < 6; g++) o << " " << w.price[g];
    for (int g = 0; g < 6; g++) o << " " << w.minv[g];
    for (int g = 0; g < 6; g++) o << " " << w.mref[g];
    o << "\n---\n";
}

int run_replay(const std::string& script, const std::string& dump_path) {
    std::ifstream in(script);
    if (!in) { std::printf("cannot open %s\n", script.c_str()); return 1; }
    uint64_t seed = 42;
    int size = 60, nations = 3, max_turn = 0;
    std::vector<InitNation> init;
    std::vector<ActLine> acts;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        if (line[0] == '#') {
            for (auto& tk : split_ws(line)) {
                auto eq = tk.find('=');
                if (eq == std::string::npos) continue;
                std::string k = tk.substr(0, eq), v = tk.substr(eq + 1);
                if (k == "seed") seed = std::stoull(v);
                else if (k == "size") size = std::stoi(v);
                else if (k == "nations") nations = std::stoi(v);
                else if (k == "turns") max_turn = std::stoi(v);
            }
            continue;
        }
        auto p = split_ws(line);
        if (p.empty()) continue;
        if (p[0] == "i") {
            InitNation n;
            n.cx = std::stoi(p[2]);
            n.cy = std::stoi(p[3]);
            for (int r = 0; r < RES_N && (size_t)(4 + r) < p.size(); r++) n.res[r] = std::stod(p[4 + r]);
            init.push_back(n);
            continue;
        }
        if (p.size() < 3) continue;
        if (p[0] == "y") continue;   // 保留
        ActLine a;
        a.turn = std::stoi(p[0]);
        a.actor = std::stoi(p[1]);
        a.name = p[2];
        for (size_t i = 3; i < p.size(); i++) {
            if (p[i] == "|" && i + 1 < p.size()) {
                a.expected = std::stoi(p[i + 1]);
                break;
            }
            a.args.push_back(p[i]);
        }
        acts.push_back(a);
        max_turn = std::max(max_turn, a.turn);
    }
    World w;
    w.seed = seed;
    w.size = size;
    w.setup(nations, {});
    w.gen_map();   // setup 的 spawn 会改动其环形起点（改平原/重算资源），这里恢复纯净地图
    // 用 ref 的初始状态覆盖：清空再按 init 注入（地形/资源保持 mapgen 结果）
    for (auto& t : w.tiles) { t.owner = -1; t.core = -1; t.bld = {}; t.pending = {}; t.recruited = t.built = t.militia_rec = 0; }
    static const int CROSS[5][2] = {{0, 0}, {0, -1}, {0, 1}, {-1, 0}, {1, 0}};
    for (int n = 0; n < (int)init.size(); n++) {
        if (n >= (int)w.nations.size()) break;
        w.nations[n].res = init[n].res;
        w.nations[n].alive = true;
        for (auto& d : CROSS) {
            int x = init[n].cx + d[0], y = init[n].cy + d[1];
            if (x < 0 || x >= size || y < 0 || y >= size) continue;
            Tile& t = w.tile(x, y);
            w.materialize(x * size + y);
            t.owner = n;
            t.core = n;
            if (d[0] == 0 && d[1] == 0) {
                t.terrain = PLAIN;                 // main：中心强制平原并重算资源（territory.create 传 terrain）
                t.tres = w.roll_res(x, y, PLAIN);
                t.bld[TOWNHALL] = 1;
            }
        }
    }
    std::ofstream out(dump_path);
    if (!out) { std::printf("cannot open %s\n", dump_path.c_str()); return 1; }
    size_t ai = 0;
    for (int t = 1; t <= max_turn; t++) {
        if (t == 1) dump_turn(w, out);
        while (ai < acts.size() && acts[ai].turn == t) {
            std::string note;
            double dbg_exact = 0.0, dbg_carry = 0.0;
            if ((acts[ai].name == "buy" || acts[ai].name == "sell") && acts[ai].args.size() >= 2) {
                int g = std::stoi(acts[ai].args[0]), q = std::stoi(acts[ai].args[1]);
                auto wv = w.market_walk(g, q, acts[ai].name == "buy" ? 0 : 1, acts[ai].actor);
                dbg_exact = wv.second;
                auto it = w.carry.find(acts[ai].actor);
                dbg_carry = it == w.carry.end() ? 0.0 : it->second;
            }
            bool ok = run_action(w, acts[ai].actor, acts[ai].name, acts[ai].args, note);
            out << "R " << t << " " << acts[ai].actor << " " << acts[ai].name << " "
                << (ok ? 1 : 0) << " " << w.nations[acts[ai].actor].res[GOLD] << " "
                << std::fixed << std::setprecision(6) << dbg_exact << " " << dbg_carry
                << " " << acts[ai].expected << " " << note << "\n";
            ai++;
        }
        w.resolve_turn();
        w.begin_turn();
        dump_turn(w, out);
    }
    std::printf("replay done: turns=%d acts=%zu size=%d nations=%d\n", max_turn, acts.size(), size, nations);
    return 0;
}
