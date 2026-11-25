#include "opponents.hpp"
#include "rl_env.hpp"

#include <set>
#include <tuple>

namespace rl {
namespace {

constexpr int field_max = 12;
constexpr int no_path = 1 << 20;
constexpr int max_candidates = 40;
constexpr int max_group = 8;
constexpr int near_targets = 4;
constexpr int solver_node_cap = 20000;
constexpr int shortfall_penalty = 500;

template <typename Visit>
void neighbors(const World& w, int cell, Visit visit) {
    int x = cell / w.size, y = cell % w.size;
    for (int nx = std::max(x - 1, 0); nx <= std::min(x + 1, w.size - 1); ++nx)
        for (int ny = std::max(y - 1, 0); ny <= std::min(y + 1, w.size - 1); ++ny)
            if (nx != x || ny != y) visit(nx * w.size + ny);
}

int cell_of(const World& w, const Army& a) { return a.x * w.size + a.y; }

int chebyshev(const World& w, int a, int b) {
    return std::max(std::abs(a / w.size - b / w.size), std::abs(a % w.size - b % w.size));
}

bool hostile_site(const World& w, int seat, int cell) {
    const int owner = w.tiles[cell].owner;
    return owner >= 0 && owner != seat && !w.allied(seat, owner) && w.at_war(seat, owner);
}

bool attackable(const World& w, int seat, int cell, const std::vector<char>& vision) {
    if (!vision[cell]) return false;
    if (w.tiles[cell].owner >= 0) return hostile_site(w, seat, cell);
    bool busy = false;
    for (const auto& a : w.armies) {
        if (!a.engaged || cell_of(w, a) != cell) continue;
        if (w.at_war(seat, a.owner) || w.allied(seat, a.owner)) return true;
        busy = true;
    }
    return !busy;
}

struct CombatUnit { double hp; int attack; };

bool simulate(std::vector<CombatUnit> attack, std::vector<CombatUnit> defense, int defense_pct) {
    auto power = [](const std::vector<CombatUnit>& units) {
        int sum = 0;
        for (const auto& u : units) if (u.hp > 0) sum += u.attack;
        return sum;
    };
    auto spread = [](std::vector<CombatUnit>& units, int damage) {
        int n = std::count_if(units.begin(), units.end(), [](const CombatUnit& u) { return u.hp > 0; });
        int i = 0;
        for (auto& u : units) {
            if (u.hp <= 0) continue;
            u.hp -= damage / n + (i++ < damage % n);
        }
    };
    for (int round = 0; round < 12; ++round) {
        int a = power(attack), d = power(defense);
        if (!d) return true;
        if (!a) return false;
        spread(attack, d);
        spread(defense, std::max(1, static_cast<int>(std::nearbyint(a * (100 - defense_pct) / 100.0))));
        if (!power(attack)) return false;
        if (!power(defense)) return true;
    }
    return false;
}

int need_for(const World& w, int seat, int cell, const std::vector<Army>& units) {
    const auto& t = w.tiles[cell];
    std::vector<CombatUnit> def;
    for (const auto& a : w.armies) {
        if (a.hp <= 0 || a.owner == seat || cell_of(w, a) != cell) continue;
        if (a.owner < 0 ? t.owner < 0 : w.at_war(a.owner, seat)) def.push_back({a.hp, Num::U_ATK[a.kind]});
    }
    if (def.empty()) return 1;
    std::stable_sort(def.begin(), def.end(), [](const CombatUnit& a, const CombatUnit& b) {
        return std::pair{-a.attack, -a.hp} < std::pair{-b.attack, -b.hp};
    });
    const int pct = 100 - (100 - Num::T_DEF[t.terrain]) * (100 - t.bld[CASTLE] * Num::B_DEF_LV) / 100;
    auto sorted = units;
    std::sort(sorted.begin(), sorted.end(), [](const Army& a, const Army& b) {
        return std::tuple{-Num::U_ATK[a.kind], -a.hp, a.aid} < std::tuple{-Num::U_ATK[b.kind], -b.hp, b.aid};
    });
    std::vector<CombatUnit> attack;
    int cap = std::min(12, static_cast<int>(sorted.size()));
    for (int i = 0; i < cap; ++i) {
        attack.push_back({sorted[i].hp, Num::U_ATK[sorted[i].kind]});
        if (simulate(attack, def, pct)) return i + 1;
    }
    return cap + 1;
}

class Fields {
public:
    Fields(const World& w, int seat, const std::vector<char>& visible) : w_(w), visible_(visible) {
        walls_.resize(w.tiles.size());
        for (size_t i = 0; i < w.tiles.size(); ++i) {
            int n = w.tiles[i].owner;
            walls_[i] = visible[i] && (w.tiles[i].terrain >= HIGH_MTN || (n >= 0 && n != seat && !w.allied(seat, n)));
        }
        for (const auto& a : w.armies)
            if (a.hp > 0 && w.at_war(seat, a.owner) && visible_[cell_of(w, a)]) walls_[cell_of(w, a)] = true;
    }

    const std::vector<int>& get(int target, int kind) {
        auto [it, inserted] = fields_.try_emplace({target, kind});
        if (!inserted) return it->second;
        auto& value = it->second;
        value.assign(w_.tiles.size(), no_path);
        value[target] = 0;
        std::array<std::vector<int>, field_max + 1> queue;
        queue[0].push_back(target);
        auto cost = [&](int i) {
            if (kind != CAV || !visible_[i]) return 1;
            const int c = Num::move_cost(CAV, static_cast<Terrain>(w_.tiles[i].terrain));
            return c < 0 ? 99 : c;
        };
        for (int d = 0; d < field_max; ++d) {
            for (int i : queue[d]) {
                if (value[i] != d) continue;
                neighbors(w_, i, [&](int j) {
                    if (walls_[j]) return;
                    int next = d + std::max(cost(i), cost(j));
                    if (next <= field_max && value[j] > next) {
                        value[j] = next;
                        queue[next].push_back(j);
                    }
                });
            }
        }
        return value;
    }
private:
    const World& w_;
    const std::vector<char>& visible_;
    std::vector<char> walls_;
    std::map<std::pair<int, int>, std::vector<int>> fields_;
};

struct Target { int cell, need, committed; };

std::vector<Target> targets(const World& w, int seat, const std::vector<char>& vision, const std::vector<Army>& troops,
                            const OpponentState& state) {
    std::vector<int> anchors;
    std::set<int> cells;
    const auto* bloc = w.bloc_of(seat);
    for (int i = 0; i < static_cast<int>(w.tiles.size()); ++i) {
        int owner = w.tiles[i].owner;
        if (owner == seat || (bloc && std::count(bloc->members.begin(), bloc->members.end(), owner)))
            anchors.push_back(i);
        if (owner == seat) {
            neighbors(w, i, [&](int j) {
                if (vision[j] && !w.materialized[j]) cells.insert(j);
            });
        } else if (vision[i] && w.at_war(seat, owner)) {
            cells.insert(i);
        }
    }
    for (const auto& a : troops) anchors.push_back(cell_of(w, a));
    for (const auto& a : w.armies)
        if (a.owner < 0 && a.hp > 0 && vision[cell_of(w, a)]) cells.insert(cell_of(w, a));
    std::vector<std::pair<int, int>> nearest;
    for (int cell : cells) {
        int distance = anchors.empty() ? 0 : 11;
        for (int a : anchors) distance = std::min(distance, chebyshev(w, a, cell));
        if (distance <= 10) nearest.push_back({distance, cell});
    }
    std::sort(nearest.begin(), nearest.end());
    if (nearest.size() > max_candidates) nearest.resize(max_candidates);

    std::vector<Target> out;
    for (auto [distance, cell] : nearest) {
        int committed = 0;
        for (const auto& a : troops)
            if (auto t = state.targets.find(a.id); t != state.targets.end() && t->second == cell) ++committed;
        out.push_back({cell, need_for(w, seat, cell, troops), committed});
    }
    std::sort(out.begin(), out.end(), [](const Target& a, const Target& b) { return a.cell < b.cell; });
    return out;
}

class GroupSolver {
public:
    GroupSolver(const World& w, const std::vector<Target>& targets, const std::vector<Army>& pool, Fields& fields)
        : w_(w), targets_(targets), pool_(pool) {
        distance_.resize(pool.size());
        for (size_t i = 0; i < pool.size(); ++i) {
            const auto& a = pool[i];
            for (const auto& t : targets) {
                int d = fields.get(t.cell, a.kind)[cell_of(w, a)];
                distance_[i].push_back(d == no_path ? chebyshev(w, cell_of(w, a), t.cell) : d);
            }
        }
        memo_[{}] = 0;
    }

    void solve(OpponentState& state) {
        std::vector<int> rest(pool_.size());
        std::iota(rest.begin(), rest.end(), 0);
        try {
            best(rest);
            std::vector<std::pair<int, int>> solution;
            while (!rest.empty()) {
                std::tuple<int, int, int, int> key{no_path, no_path, no_path, no_path};
                std::vector<int> chosen, next;
                int goal = -1;
                options(rest, [&](const std::vector<int>& group, int cell, int cost, std::vector<int> left) {
                    auto rank = std::tuple{cost + best(left), cost, cell, static_cast<int>(group.size())};
                    if (rank >= key) return;
                    key = rank;
                    chosen = group;
                    next = std::move(left);
                    goal = cell;
                });
                if (chosen.empty()) return;
                for (int i : chosen) solution.push_back({pool_[i].id, goal});
                rest = std::move(next);
            }
            for (auto [id, goal] : solution) state.targets[id] = goal;
        } catch (const BudgetExceeded&) {
            // Python leaves unassigned units without targets if the exact search exceeds its node budget.
        }
    }

private:
    struct BudgetExceeded {};

    template <typename Visit>
    void options(const std::vector<int>& rest, Visit visit) {
        const int first = rest.front();
        std::vector<int> near(targets_.size());
        std::iota(near.begin(), near.end(), 0);
        auto rank = [&](int t) { return std::pair{distance_[first][t], targets_[t].cell}; };
        std::sort(near.begin(), near.end(), [&](int a, int b) { return rank(a) < rank(b); });
        if (near.size() > near_targets) near.resize(near_targets);
        std::vector<int> others(rest.begin() + 1, rest.end());
        for (int t : near) {
            std::sort(others.begin(), others.end(), [&](int a, int b) {
                return std::pair{distance_[a][t], a} < std::pair{distance_[b][t], b};
            });
            std::vector<int> group{first};
            int spread = 0;
            int reach = distance_[first][t];
            const auto& target = targets_[t];
            for (int k = 1; k <= std::min(max_group, static_cast<int>(rest.size())); ++k) {
                if (k > 1) {
                    int i = others[k - 2];
                    reach = std::max(reach, distance_[i][t]);
                    for (int j : group) spread += chebyshev(w_, cell_of(w_, pool_[i]), cell_of(w_, pool_[j]));
                    group.push_back(i);
                }
                const int cost = spread + reach + shortfall_penalty * std::max(0, target.need - k - target.committed);
                std::vector<int> left;
                left.reserve(rest.size() - group.size());
                for (int i : rest)
                    if (std::find(group.begin(), group.end(), i) == group.end()) left.push_back(i);
                visit(group, target.cell, cost, std::move(left));
            }
        }
    }

    int best(const std::vector<int>& rest) {
        if (auto it = memo_.find(rest); it != memo_.end()) return it->second;
        if (++nodes_ > solver_node_cap) throw BudgetExceeded{};
        int result = no_path;
        options(rest, [&](const std::vector<int>&, int, int cost, const std::vector<int>& left) {
            if (cost < result) result = std::min(result, cost + best(left));
        });
        return memo_[rest] = result;
    }

    const World& w_;
    const std::vector<Target>& targets_;
    const std::vector<Army>& pool_;
    std::vector<std::vector<int>> distance_;
    std::map<std::vector<int>, int> memo_;
    int nodes_ = 0;
};

int best_step(const World& w, int seat, const Army& a, const std::vector<int>& field, int goal) {
    int start = cell_of(w, a);
    auto reachable = w.reachable(seat, a);
    std::tuple<int, int, int> pick{no_path, no_path, -1};
    for (const auto& [xy, cost] : reachable) {
        const int cell = xy.first * w.size + xy.second;
        if (field[cell] < field[start]) pick = std::min(pick, std::tuple{field[cell], cost, cell});
    }
    if (std::get<2>(pick) != -1 || field[start] != no_path) return std::get<2>(pick);
    const int distance = chebyshev(w, start, goal);
    for (const auto& [xy, cost] : reachable) {
        const int cell = xy.first * w.size + xy.second;
        int d = chebyshev(w, cell, goal);
        if (d < distance) pick = std::min(pick, std::tuple{d, cost, cell});
    }
    return std::get<2>(pick);
}

}  // namespace

void rule_military(World& w, int seat, OpponentState& state) {
    const auto vision = visible_set(w, seat);
    std::vector<Army> troops, pool, unassigned;
    for (const auto& a : w.armies) {
        if (a.owner != seat || a.hp <= 0) continue;
        troops.push_back(a);
        if (a.engaged) {
            state.targets[a.id] = cell_of(w, a);
        } else {
            auto t = state.targets.find(a.id);
            if (t != state.targets.end() && !attackable(w, seat, t->second, vision)) state.targets.erase(t);
            if (a.moved_turn != w.turn) pool.push_back(a);
        }
        if (!state.targets.count(a.id)) unassigned.push_back(a);
    }
    if (pool.empty()) return;
    Fields fields(w, seat, vision);
    if (!unassigned.empty()) {
        auto candidates = targets(w, seat, vision, troops, state);
        if (!candidates.empty()) GroupSolver(w, candidates, unassigned, fields).solve(state);
    }
    std::map<int, std::vector<Army>> groups;
    for (const auto& a : pool)
        if (auto t = state.targets.find(a.id); t != state.targets.end()) groups[t->second].push_back(a);
    for (const auto& [cell, squad] : groups) {
        if (w.tiles[cell].owner >= 0 && !hostile_site(w, seat, cell)) continue;
        std::vector<Army> able;
        int x = cell / w.size, y = cell % w.size;
        for (const auto& a : squad) {
            if (a.hp < Num::U_HP[a.kind]) continue;
            const auto reach = w.reachable(seat, a, true);
            if (std::any_of(reach.begin(), reach.end(), [&](const auto& p) { return p.first == std::pair{x, y}; }))
                able.push_back(a);
        }
        if (!able.empty() && need_for(w, seat, cell, able) <= static_cast<int>(able.size())) {
            std::vector<int> ids;
            for (const auto& a : able) ids.push_back(a.id);
            if (w.attack(seat, ids, x, y)) continue;
        }
        for (const auto& a : squad) {
            int step = best_step(w, seat, a, fields.get(cell, a.kind), cell);
            if (step >= 0) w.move_army(seat, a.id, step / w.size, step % w.size);
        }
    }
}

}  // namespace rl
