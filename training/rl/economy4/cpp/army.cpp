// 军队：招募、逐格移动、战斗裁决、补给/回血、占领、遣返。
#include "engine.hpp"

static int gid_of(int nation, int id) { return (nation + 1) * 100000000 + id; }

void World::spawn_guardian(int x, int y) {
    int aid = x * size + y + 1;
    Army a;
    a.id = next_army_id++;
    a.gid = aid; a.aid = aid; a.owner = -1; a.kind = INF;
    a.hp = Num::U_HP[INF]; a.x = x; a.y = y; a.moved_turn = -1;
    armies.push_back(a);
    guard_once.push_back({x, y});
}

void World::ensure_guardian_at(int x, int y) {
    int i = x * size + y;
    if (tiles[i].owner >= 0) return;
    for (auto& p : guard_once) if (p.first == x && p.second == y) return;
    for (auto& a : armies) if (a.owner < 0 && a.x == x && a.y == y) return;
    int t = tiles[i].terrain;
    if (t == HIGH_MTN || t == OCEAN) return;
    spawn_guardian(x, y);
}

std::vector<Army*> World::defs_at(int n, int x, int y) {
    std::vector<Army*> out;
    bool free = tiles[x * size + y].owner < 0;
    if (free)
        for (auto& a : armies)
            if (a.owner < 0 && a.hp > 0 && a.x == x && a.y == y) out.push_back(&a);
    for (auto& a : armies) {
        if (a.owner < 0 || a.hp <= 0) continue;
        if (a.x != x || a.y != y) continue;
        if (a.owner == n) continue;
        if (at_war(n, a.owner)) out.push_back(&a);
    }
    return out;
}

bool World::movement_blocked(int n, int x, int y) const {
    int owner = owner_of(x, y);
    if (owner >= 0) return owner != n && !allied(n, owner);
    return std::any_of(armies.begin(), armies.end(), [&](const Army& a) {
        return a.owner >= 0 && a.owner != n && a.hp > 0 && a.x == x && a.y == y && at_war(n, a.owner);
    });
}

bool World::retreat_legal(int n, int x, int y) const {
    return in_bounds(x, y) && Num::move_cost(INF, static_cast<Terrain>(tiles[x * size + y].terrain)) >= 0
        && !movement_blocked(n, x, y);
}

std::vector<std::pair<std::pair<int, int>, int>> World::reachable(int n, const Army& a, bool for_attack) const {
    int N = size * size;
    std::vector<int> best(N, 255);
    int budget = Num::U_SPEED[a.kind];
    std::vector<std::vector<int>> buckets(budget + 1);
    int start = a.x * size + a.y;
    best[start] = 0;
    buckets[0].push_back(start);
    std::vector<char> touched(N, 0);
    touched[start] = 1;
    std::vector<std::pair<std::pair<int, int>, int>> out;
    for (int d = 0; d <= budget; d++) {
        while (!buckets[d].empty()) {
            int i = buckets[d].back();
            buckets[d].pop_back();
            if (best[i] != d) continue;
            int x = i / size, y = i % size;   // 解码：i = x*size+y
            int here = Num::move_cost((Kind)a.kind, (Terrain)tiles[i].terrain);
            if (here < 0) continue;
            int nb[8][2] = {{x - 1, y - 1}, {x - 1, y}, {x - 1, y + 1}, {x, y - 1},
                            {x, y + 1}, {x + 1, y - 1}, {x + 1, y}, {x + 1, y + 1}};
            for (auto& p : nb) {
                int jx = p[0], jy = p[1];
                if (jx < 0 || jx >= size || jy < 0 || jy >= size) continue;
                int j = jx * size + jy;   // 引擎统一索引 x*size+y
                int cj = Num::move_cost((Kind)a.kind, (Terrain)tiles[j].terrain);
                if (cj < 0) continue;
                int ncost = d + std::max(here, cj);
                if (ncost > budget || ncost >= best[j]) continue;
                if (movement_blocked(n, jx, jy)) {
                    int owner = tiles[j].owner;
                    if (for_attack && (owner < 0 || at_war(n, owner))) {
                        best[j] = ncost;
                        if (!touched[j]) { touched[j] = 1; out.push_back({{jx, jy}, ncost}); }
                    }
                    continue;
                }
                best[j] = ncost;
                if (!touched[j]) { touched[j] = 1; out.push_back({{jx, jy}, ncost}); }
                buckets[ncost].push_back(j);
            }
        }
    }
    out.push_back({{a.x, a.y}, 0});
    return out;
}

int World::recruit_count(int n, int x, int y, int q, int kind) const {
    if (!alive(n) || !in_bounds(x, y) || kind < 0 || kind >= KIND_N || q <= 0) return 0;
    const Tile& t = tiles[x * size + y];
    if (t.owner != n) return 0;
    const int reserved=deferred_orders?std::count_if(recruit_reservations.begin(),recruit_reservations.end(),[&](const Army& a){return a.owner==n && a.x==x && a.y==y && (a.kind==MIL)==(kind==MIL);}):0;
    int count = 0;
    if (kind == MIL) {
        int local = t.bld[MILITIA] * Num::B_MIL_TURN - std::max<int>(t.militia_rec,reserved);
        if (local <= 0) return 0;
        int quota = nation_bld_count(n, MILITIA) * Num::B_MIL_CAP;
        int existing = std::count_if(armies.begin(), armies.end(), [n](const Army& a) {
            return a.owner == n && a.kind == MIL && a.hp > 0;
        });
        if (deferred_orders) existing += std::count_if(recruit_reservations.begin(),recruit_reservations.end(),[n](const Army& a){return a.owner==n && a.kind==MIL;});
        count = std::min(local, quota - existing);
    } else {
        if (nations[n].grid_short) return 0;
        count = t.bld[BARRACKS] * Num::B_RECRUIT_CAP - std::max<int>(t.recruited,reserved);
    }
    const int costs[] = {Num::U_REC_FOOD[kind], Num::U_REC_EQUIP[kind], Num::U_REC_GOLD[kind]};
    const int resources[] = {FOOD, EQUIP, GOLD};
    count = std::min(count, q);
    for (int i = 0; i < 3; ++i)
        if (costs[i]) count = std::min(count, static_cast<int>(std::floor(res(n, resources[i]) / costs[i])));
    return std::max(0, count);
}

bool World::recruit(int n, int x, int y, int q, int kind) {
    int count = recruit_count(n, x, y, q, kind);
    if (!count) return false;
    Tile& t = tile(x, y);
    if (kind == MIL) t.militia_rec += count;
    else t.recruited += count;
    add_res(n, FOOD, -count * Num::U_REC_FOOD[kind]);
    add_res(n, EQUIP, -count * Num::U_REC_EQUIP[kind]);
    add_res(n, GOLD, -count * Num::U_REC_GOLD[kind]);
    const auto previous=armies.size();
    spawn_recruited(n, x, y, count, kind);
    if (deferred_orders) {
        recruit_reservations.insert(recruit_reservations.end(),armies.begin()+previous,armies.end());
        armies.resize(previous);
    }
    return true;
}

void World::spawn_recruited(int n, int x, int y, int q, int kind) {
    for (int k = 0; k < q; k++) {
        Army a;
        a.id = next_army_id++;
        a.aid = ++next_aid[n];
        a.gid = gid_of(n, a.aid);
        a.owner = n; a.kind = kind; a.hp = Num::U_HP[kind];
        a.x = x; a.y = y; a.moved_turn = turn;
        armies.push_back(a);
    }
}

bool World::move_army(int n, int id, int x, int y) {
    Army* a = nullptr;
    for (auto& b : armies) if (b.owner == n && b.id == id && b.hp > 0) { a = &b; break; }
    if (!a) return false;
    if (x < 0 || x >= size || y < 0 || y >= size) return false;
    if (a->engaged) return false;
    if (a->moved_turn == turn) return false;
    for (const auto& attacker : armies)
        if (attacker.engaged && attacker.x == a->x && attacker.y == a->y && at_war(n, attacker.owner)) return false;
    if (movement_blocked(n, x, y)) {
        if (!visible_to(n, x, y)) a->moved_turn = turn;
        return false;
    }
    const auto reach = reachable(n, *a);
    if (std::none_of(reach.begin(), reach.end(), [&](const auto& entry) { return entry.first == std::pair{x, y}; }))
        return false;
    a->x = x; a->y = y; a->moved_turn = turn;
    return true;
}

static int defense_pct(World& w, int x, int y, int def_owner) {
    Tile& t = w.tile(x, y);
    int td = Num::T_DEF[t.terrain];
    int cd = (t.owner == def_owner) ? t.bld[CASTLE] * Num::B_DEF_LV : 0;
    return 100 - ((100 - td) * (100 - cd)) / 100;
}

bool World::attack(int n, const std::vector<int>& ids, int x, int y) {
    if (x < 0 || x >= size || y < 0 || y >= size) return false;
    // 注意：ensure_guardian_at 会 push_back 导致 Army* 失效，故全程按 id 重新定位
    std::vector<int> tids;
    for (auto& a : armies)
        if (a.owner == n && a.hp > 0 && std::find(ids.begin(), ids.end(), a.id) != ids.end())
            tids.push_back(a.id);
    if (tids.empty()) return false;
    auto find_a = [&](int id) -> Army* {
        for (auto& a : armies) if (a.id == id && a.hp > 0) return &a;
        return nullptr;
    };
    int own = owner_of(x, y);
    bool blocked = own >= 0 && (allied(n, own) || !at_war(n, own));
    if (own < 0) {
        bool busy = false, involved = false;
        for (const auto& other : armies) {
            if (other.owner < 0 || other.owner == n || !other.engaged || other.x != x || other.y != y || other.hp <= 0)
                continue;
            busy = true;
            involved |= allied(n, other.owner) || at_war(n, other.owner);
        }
        blocked = busy && !involved;
    }
    if (blocked) {
        if (!visible_to(n, x, y))
            for (int id : tids) find_a(id)->moved_turn = turn;
        return false;
    }
    if (own < 0) ensure_guardian_at(x, y);
    for (int id : tids) {
        const Army* a = find_a(id);
        if (!a) return false;
        if (a->x == x && a->y == y) continue;
        if (a->engaged || a->moved_turn == turn) return false;
        const auto reach = reachable(n, *a, true);
        if (std::none_of(reach.begin(), reach.end(), [&](const auto& entry) { return entry.first == std::pair{x, y}; }))
            return false;
    }
    for (int id : tids) {
        Army* a = find_a(id);
        if (!a) return false;
        if (a->x != x || a->y != y) { a->x = x; a->y = y; a->moved_turn = turn; }
    }
    auto defs = defs_at(n, x, y);
    if (!defs.empty()) {
        for (int id : tids) {
            Army* a = find_a(id);
            if (a) { a->engaged = true; a->engage_seq = next_engage_seq++; }
        }
        return true;
    }
    // 无守军 → 直接占领
    if (own >= 0) {
        for (const auto& b : armies)
            if (b.owner != n && b.hp > 0 && b.x == x && b.y == y) return false;
    }
    conquer(x, y, n);
    return true;
}

bool World::retreat(int n, int id, int x, int y) {
    Army* a = nullptr;
    for (auto& b : armies) if (b.owner == n && b.id == id && b.hp > 0) { a = &b; break; }
    if (!a || (a->x == x && a->y == y)) return false;
    bool attacked = false;
    bool side_attacks = false;
    for (const auto& unit : armies) {
        if (!unit.engaged || unit.x != a->x || unit.y != a->y) continue;
        if (unit.owner == n) side_attacks = true;
        if (at_war(n, unit.owner)) attacked = true;
    }
    if (!a->engaged && !attacked) return false;
    if (std::max(std::abs(x - a->x), std::abs(y - a->y)) > Num::RETREAT_RANGE) return false;
    if (a->moved_turn == turn || !retreat_legal(n, x, y)) return false;
    a->moved_turn = turn;
    int own0 = tiles[a->x * size + a->y].owner;
    a->retreat_cover = (own0 == n || !side_attacks) ? Num::RETREAT_DEF_COVER : 100;   // 守方撤退只吃一半
    a->has_retreat = true; a->rx = x; a->ry = y;
    return true;
}

bool World::disband(int n, int id) {
    if (deferred_orders) for (auto it=recruit_reservations.begin();it!=recruit_reservations.end();++it)
        if (it->owner==n && it->id==id) {
            add_res(n,FOOD,Num::U_REC_FOOD[it->kind]);add_res(n,EQUIP,Num::U_REC_EQUIP[it->kind]);add_res(n,GOLD,Num::U_REC_GOLD[it->kind]);
            auto& t=tile(it->x,it->y);if(it->kind==MIL){if(t.militia_rec)--t.militia_rec;}else if(t.recruited)--t.recruited;
            recruit_reservations.erase(it);return true;
        }
    for (size_t i = 0; i < armies.size(); i++)
        if (armies[i].owner == n && armies[i].id == id) {
            if (armies[i].engaged) return false;   // main：交战中不可遣散
            armies.erase(armies.begin() + i);
            return true;   // 立即删除；不返还资源；当回合起不再吃补给
        }
    return false;
}

int World::claim_battle(int x, int y, const std::vector<int>& factions, bool all_occupants) const {
    int winner = -1;
    int sequence = 1000000000;
    for (const auto& a : armies) {
        if (a.owner < 0 || a.has_retreat || a.hp <= 0 || a.x != x || a.y != y) continue;
        if (!std::count(factions.begin(), factions.end(), a.owner)) continue;
        if (std::any_of(armies.begin(), armies.end(), [&](const Army& opponent) {
            return opponent.x == x && opponent.y == y && opponent.hp > 0 && at_war(a.owner, opponent.owner)
                && (all_occupants || std::count(factions.begin(), factions.end(), opponent.owner));
        })) continue;
        int order = a.engage_seq > 0 ? a.engage_seq : 1000000000;
        if (winner < 0 || order < sequence) {
            winner = a.owner;
            sequence = order;
        }
    }
    return winner;
}

std::vector<std::pair<int, int>> World::resolve_battles() {
    std::vector<std::pair<int, int>> fought;
    std::vector<std::pair<int, int>> cells;
    for (auto& a : armies)
        if (a.owner >= 0 && a.engaged && a.hp > 0) cells.push_back({a.x, a.y});
    std::sort(cells.begin(), cells.end());
    cells.erase(std::unique(cells.begin(), cells.end()), cells.end());
    for (auto& c : cells) {
        int X = c.first, Y = c.second;
        auto* tt = &tile(X, Y);
        bool free_land = tt->owner < 0;
        // 分组
        std::vector<int> owners;
        std::unordered_map<int, std::vector<Army*>> forces;
        for (auto& a : armies) {
            if (a.hp <= 0 || a.x != X || a.y != Y) continue;
            if (a.owner < 0 && !free_land) continue;
            if (forces.find(a.owner) == forces.end()) owners.push_back(a.owner);
            forces[a.owner].push_back(&a);
        }
        // main：`for owner in sorted(groups)`，野人排最后 → C++ 按 (野人=+inf) 排序
        std::sort(owners.begin(), owners.end(), [](int a, int b) {
            if (a < 0) return false;
            if (b < 0) return true;
            return a < b;
        });
        std::vector<int> attacker;
        for (int F : owners) {
            if (F < 0) continue;
            for (auto* a : forces[F]) if (a->engaged) { attacker.push_back(F); break; }
        }
        if (forces.empty() || attacker.empty()) continue;
        auto has_war = [&](int a, int b) { return at_war(a, b); };
        std::unordered_map<int, std::vector<int>> enemies;
        for (int F : owners) {
            std::vector<int> en;
            if (F < 0) {
                en = attacker;
            } else {
                for (int G : owners)
                    if (G != F && G >= 0 && has_war(F, G)) en.push_back(G);
                if (std::find(attacker.begin(), attacker.end(), F) != attacker.end() &&
                    forces.count(-1)) en.push_back(-1);
            }
            enemies[F] = en;
        }
        std::unordered_map<int, int> dmg;
        for (int F : owners) {
            if (enemies[F].empty()) continue;
            int d = pyrng.randint_1_6();   // 与 main 的 random.Random(seed).randint(1,6) 同序列
            if (getenv("DBG_DICE")) std::printf("[dice] cell=(%d,%d) owner=%d d=%d mti=%d\n", X, Y, F, d, pyrng.mti);
            int mod = Num::DIE_MOD[d - 1];
            int atk = 0;
            for (auto* a : forces[F]) {
                int base = Num::U_ATK[a->kind];
                atk += a->has_retreat ? std::max(1, base * (100 - Num::RETREAT_PENALTY) / 100) : base;
            }
            int power = std::max(1, atk * (100 - 0) / 100);
            power = std::max(1, power * (100 + mod) / 100);
            double share = static_cast<double>(power) / enemies[F].size();
            for (int G : enemies[F]) {
                Tile& tg = tile(X, Y);
                int soak = (!std::count(attacker.begin(), attacker.end(), G) || G == tg.owner)
                               ? defense_pct(*this, X, Y, G) : 0;
                dmg[G] += std::max(1, (int)std::nearbyint(share * (100 - soak) / 100.0));
            }
        }
        for (int F : owners) {
            if (!dmg.count(F)) continue;
            auto& units = forces[F];
            // main 口径：浮点水填充。ratios=retreat_cover/100；caps=hp/ratio；
            // 均分、封顶者摘出、余量重分；最后 hp -= share*ratio（可为小数）。
            std::vector<double> ratios, caps, alloc(units.size(), 0.0);
            for (auto* a : units) ratios.push_back(a->retreat_cover / 100.0);
            for (size_t k = 0; k < units.size(); k++)
                caps.push_back(ratios[k] == 0 ? 1e18 : units[k]->hp / ratios[k]);
            double remaining = (double)dmg[F];
            std::vector<char> done(units.size(), 0);
            while (remaining > 1e-12) {
                int active = 0;
                for (size_t k = 0; k < units.size(); k++) if (!done[k] && caps[k] > 0) active++;
                if (!active) break;
                double share = remaining / active;
                bool capped_any = false;
                for (size_t k = 0; k < units.size(); k++) {
                    if (done[k]) continue;
                    if (caps[k] - alloc[k] <= share + 1e-12) {
                        double take = std::min(share, std::max(0.0, caps[k] - alloc[k]));
                        alloc[k] += take; remaining -= take; done[k] = 1; capped_any = true;
                    }
                }
                if (!capped_any) {
                    for (size_t k = 0; k < units.size(); k++) if (!done[k]) alloc[k] += share;
                    break;
                }
            }
            for (size_t k = 0; k < units.size(); k++) units[k]->hp -= alloc[k] * ratios[k];
        }
        armies.erase(std::remove_if(armies.begin(), armies.end(),
                                    [](const Army& a) { return a.hp <= 0; }), armies.end());
        // 脱离交战 + 占地
        for (auto& a : armies) {
            if (a.x != X || a.y != Y || a.owner < 0) continue;
            bool any_enemy = false;
            for (auto& b : armies)
                if (b.hp > 0 && b.x == X && b.y == Y && b.owner != a.owner &&
                    (b.owner < 0 ? a.engaged : at_war(a.owner, b.owner))) { any_enemy = true; break; }
            if (!any_enemy) a.engaged = false;
        }
        std::vector<int> surviving_attackers;
        bool guardian = false;
        for (const auto& a : armies) {
            if (a.x != X || a.y != Y || a.hp <= 0) continue;
            if (a.owner < 0) guardian = true;
            else if (std::count(attacker.begin(), attacker.end(), a.owner)) surviving_attackers.push_back(a.owner);
        }
        if (!guardian) {
            int winner = claim_battle(X, Y, surviving_attackers, true);
            if (winner >= 0 && std::count(attacker.begin(), attacker.end(), winner) && winner != tt->owner)
                conquer(X, Y, winner);
        }
        fought.push_back(c);
    }
    return fought;
}

void World::conquer(int x, int y, int by) {
    int i = x * size + y;
    Tile& t = tiles[i];
    int old = t.owner;
    if (old == by) return;
    if (deferred_orders) cancel_site_reservations(x,y);
    guard_once.erase(std::remove(guard_once.begin(), guard_once.end(), std::pair{x, y}), guard_once.end());
    armies.erase(std::remove_if(armies.begin(), armies.end(), [&](const Army& a) {
        return a.owner < 0 && a.x == x && a.y == y;
    }), armies.end());
    materialize(i);
    t.owner = by;
    if (old < 0) t.core = by;
    // 同实体盟友的核心地：归还（简化 main 的 _return_core：盟友核心且本国在战）
    if (t.core >= 0 && t.core != by && allied(by, t.core) && t.core < (int)nations.size() && alive(t.core)) {
        t.owner = t.core;
    }
}

std::vector<size_t> World::supply_consumers(int n) const {
    std::vector<size_t> fed;
    std::unordered_map<int, int> mil_free;
    for (size_t i = 0; i < armies.size(); ++i) {
        const auto& a = armies[i];
        if (a.owner != n || a.hp <= 0) continue;
        const int cell = a.x * size + a.y;
        const auto& t = tiles[cell];
        if (a.kind == MIL && t.owner == n) {
            auto& used = mil_free[cell];
            if (used < t.bld[MILITIA] * Num::B_MIL_CAP) {
                ++used;
                continue;
            }
        }
        fed.push_back(i);
    }
    return fed;
}

int World::supply_need(int n) const {
    int need = 0;
    for (auto i : supply_consumers(n)) need += Num::U_SUPPLY[armies[i].kind];
    return need;
}

void World::supply_phase() {
    for (int n = 0; n < (int)nations.size(); n++) {
        if (!alive(n)) continue;
        const auto fed = supply_consumers(n);
        int need = 0;
        for (auto i : fed) need += Num::U_SUPPLY[armies[i].kind];
        double paid = std::min(static_cast<double>(need), res(n, SUPPLY));
        add_res(n, SUPPLY, -paid);
        ledger(n).supply += paid;
        double short_ = need - paid;
        bool starved = short_ > 0 && need > 0;
        int per = starved ? std::max(1, static_cast<int>(Num::STARVE * short_ / need)) : 0;
        if (starved) {
            ++nations[n].supply_short_turns;
            for (auto i : fed) {
                armies[i].hp = std::max(0.0, armies[i].hp - per);
                nations[n].starved_units += armies[i].hp <= 0;
            }
        }
        armies.erase(std::remove_if(armies.begin(), armies.end(),
                                    [](const Army& a) { return a.owner >= 0 && a.hp <= 0; }), armies.end());
        if (starved) continue;
        // 回血：与 main `_healing_plan` 一致 —— 每支 cap=min(HEAL_PER_TURN, max-hp) 为浮点，
        // 正规兵按 main `equal_allocation` 浮点水填充共享装备池（每 30HP 一件），民兵免费
        std::vector<std::pair<Army*, double>> healable;
        for (size_t k = 0; k < armies.size(); k++) {
            Army& a = armies[k];
            if (a.owner != n || a.hp <= 0) continue;
            // main `_healing_plan`：同格**任意** engaged 单位（含自己/同国）都不回血
            bool in_battle = false;
            for (auto& b : armies)
                if (b.hp > 0 && b.x == a.x && b.y == a.y && b.engaged) { in_battle = true; break; }
            if (in_battle) continue;
            double cap = std::min((double)Num::HEAL_PER_TURN, (double)Num::U_HP[a.kind] - a.hp);
            if (cap <= 0) continue;
            if (a.kind == MIL) a.hp += cap;
            else healable.push_back({&a, cap});
        }
        double pool = res(n, EQUIP) * (double)Num::HEAL_PER_TURN;
        std::vector<double> alloc(healable.size(), 0.0);
        {
            std::vector<char> done(healable.size(), 0);
            double remaining = pool;
            while (remaining > 1e-12) {
                int active = 0;
                for (size_t k = 0; k < healable.size(); k++)
                    if (!done[k] && healable[k].second - alloc[k] > 0) active++;
                if (!active) break;
                double share = remaining / active;
                bool capped_any = false;
                for (size_t k = 0; k < healable.size(); k++) {
                    if (done[k]) continue;
                    double avail = healable[k].second - alloc[k];
                    if (avail <= share + 1e-12) {
                        double take = std::min(share, std::max(0.0, avail));
                        alloc[k] += take; remaining -= take; done[k] = 1; capped_any = true;
                    }
                }
                if (!capped_any) {
                    for (size_t k = 0; k < healable.size(); k++)
                        if (!done[k]) alloc[k] += share;
                    remaining = 0;
                }
            }
        }
        double used_eq = 0.0;
        for (size_t k = 0; k < healable.size(); k++) {
            if (alloc[k] <= 0) continue;
            healable[k].first->hp += alloc[k];
            used_eq += alloc[k] / (double)Num::HEAL_PER_TURN;
        }
        if (used_eq > 0) {
            double equip = std::min(std::ceil(used_eq - 1e-12), res(n, EQUIP));
            add_res(n, EQUIP, -equip);
            value_flow(n, &Ledger::heal, EQUIP, equip);
        }
    }
}

void World::withdraw_illegal() {
    for (auto& a : armies) {
        if (a.owner < 0 || a.hp <= 0) continue;
        int own = tiles[a.x * size + a.y].owner;
        if (own < 0 || own == a.owner || allied(a.owner, own)) continue;
        if (at_war(a.owner, own)) continue;   // 交战区滞留合法
        // 遣返：朝本国任一格走 1 步（有路走就撤）
        auto reach = reachable(a.owner, a);
        int bestd = 1 << 30, bx = a.x, by = a.y;
        for (auto& p : reach) {
            int x = p.first.first, y = p.first.second;
            int o = tiles[x * size + y].owner;
            if (o >= 0 && (o == a.owner || allied(a.owner, o))) {
                int d = std::max(std::abs(x - a.x), std::abs(y - a.y));
                if (d < bestd) { bestd = d; bx = x; by = y; }
            }
        }
        if (bx != a.x || by != a.y) { a.x = bx; a.y = by; }
    }
}

void World::clear_disengaged() {
    for (auto& a : armies) {
        if (a.owner < 0 || !a.engaged) continue;
        bool enemy = false;
        for (auto& b : armies)
            if (b.hp > 0 && b.x == a.x && b.y == a.y && b.owner != a.owner &&
                (b.owner < 0 ? a.engaged : at_war(a.owner, b.owner))) { enemy = true; break; }
        if (!enemy) a.engaged = false;
    }
}
