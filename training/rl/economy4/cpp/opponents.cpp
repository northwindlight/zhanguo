#include "opponents.hpp"

#include <deque>
#include <limits>

namespace rl {
namespace {

constexpr const char* opponent_names[] = {"rule", "w1_25_2", "w1_40_3", "w2", "w3", "rule_economy"};

int army_count(const World& w, int seat) {
    return std::count_if(w.armies.begin(), w.armies.end(), [seat](const Army& a) { return a.owner == seat && a.hp > 0; });
}

int nearest_enemy(const World& w, int seat) {
    for (const auto& cmd : w.diplo_q)
        if (cmd.actor == seat && cmd.action == "declare_war") return -1;
    std::vector<std::vector<int>> land(w.nations.size());
    for (int i = 0; i < static_cast<int>(w.tiles.size()); ++i)
        if (w.tiles[i].owner >= 0) land[w.tiles[i].owner].push_back(i);
    int best = std::numeric_limits<int>::max(), result = -1;
    for (int n = 0; n < static_cast<int>(w.nations.size()); ++n) {
        if (!w.alive(n) || n == seat || w.at_war(seat, n) || w.has_truce(seat, n) || w.entity(seat) == w.entity(n))
            continue;
        int distance = 1000000000;
        for (int a : land[seat])
            for (int b : land[n])
                distance = std::min(distance, std::abs(a / w.size - b / w.size) + std::abs(a % w.size - b % w.size));
        if (distance < best) {
            best = distance;
            result = n;
        }
    }
    return result;
}

int advance_step(const World& w, int from, const std::vector<char>& goals) {
    if (goals[from]) return -1;
    std::vector<int> previous(w.tiles.size(), -1);
    previous[from] = from;
    std::deque<int> queue{from};
    while (!queue.empty()) {
        int current = queue.front();
        queue.pop_front();
        for (int x = std::max(0, current / w.size - 1); x <= std::min(w.size - 1, current / w.size + 1); ++x) {
            for (int y = std::max(0, current % w.size - 1); y <= std::min(w.size - 1, current % w.size + 1); ++y) {
                int i = x * w.size + y;
                if (previous[i] != -1 || w.tiles[i].terrain >= HIGH_MTN) continue;
                previous[i] = current;
                if (goals[i]) {
                    while (previous[i] != from) i = previous[i];
                    return i;
                }
                queue.push_back(i);
            }
        }
    }
    return -1;
}

void war_moves(World& w, int seat) {
    if (!w.at_war_any(seat)) return;
    std::vector<char> goals(w.tiles.size());
    std::vector<char> has_hall(w.nations.size());
    for (const auto& t : w.tiles)
        if (t.owner >= 0 && t.bld[TOWNHALL]) has_hall[t.owner] = true;
    for (size_t i = 0; i < w.tiles.size(); ++i) {
        const Tile& t = w.tiles[i];
        if (w.at_war(seat, t.owner) && (t.bld[TOWNHALL] || !has_hall[t.owner])) goals[i] = true;
    }
    if (std::none_of(goals.begin(), goals.end(), [](char x) { return x; })) return;
    struct Group {
        int cell;
        bool engaged;
        std::vector<int> ids;
    };
    std::vector<Group> groups;
    for (const auto& a : w.armies) {
        if (a.owner != seat || a.hp <= 0 || a.moved_turn == w.turn) continue;
        int cell = a.x * w.size + a.y;
        auto it = std::find_if(groups.begin(), groups.end(), [cell](const Group& g) { return g.cell == cell; });
        if (it == groups.end()) {
            groups.push_back({cell, a.engaged, {a.id}});
        } else {
            it->engaged |= a.engaged;
            it->ids.push_back(a.id);
        }
    }
    for (const auto& g : groups) {
        if (g.engaged) {
            w.attack(seat, g.ids, g.cell / w.size, g.cell % w.size);
            continue;
        }
        int step = advance_step(w, g.cell, goals);
        if (step < 0) continue;
        const int x = step / w.size, y = step % w.size;
        const bool hostile = w.at_war(seat, w.tiles[step].owner)
            || std::any_of(w.armies.begin(), w.armies.end(), [&](const Army& a) {
                return a.x == x && a.y == y && a.hp > 0 && w.at_war(seat, a.owner);
            });
        if (hostile) {
            w.attack(seat, g.ids, x, y);
        } else {
            for (int id : g.ids) w.move_army(seat, id, x, y);
        }
    }
}

void boost_recruit(World& w, int seat) {
    int count = army_count(w, seat);
    int remaining = 2;
    for (int i = 0; remaining && i < static_cast<int>(w.tiles.size()); ++i) {
        if (w.res(seat, SUPPLY) < 4 * count + 6) break;
        if (w.tiles[i].owner == seat && w.recruit(seat, i / w.size, i % w.size, 1, INF)) {
            --remaining;
            ++count;
        }
    }
}

}  // namespace

const char* opponent_name(Opponent opponent) {
    return opponent_names[static_cast<int>(opponent)];
}

std::optional<Opponent> parse_opponent(std::string_view name) {
    if (name == "w1b") return Opponent::W1_40_3;
    for (int i = 0; i < static_cast<int>(std::size(opponent_names)); ++i)
        if (name == opponent_names[i]) return static_cast<Opponent>(i);
    return std::nullopt;
}

void opponent_turn(World& w, int seat, Opponent opponent, OpponentState& state) {
    if (!w.alive(seat)) return;
    if (opponent == Opponent::W3) {
        w3_turn(w, seat, state.w3);
        return;
    }
    if (opponent == Opponent::Rule || opponent == Opponent::RuleEconomy) {
        rule_economy(w, seat);
        if (opponent == Opponent::Rule) rule_military(w, seat, state);
        return;
    }
    const bool w2 = opponent == Opponent::W2;
    const int start = w2 ? 20 : opponent == Opponent::W1_25_2 ? 25 : 40;
    const int minimum = w2 ? 4 : opponent == Opponent::W1_25_2 ? 2 : 3;
    if (w.turn >= start && !w.at_war_any(seat) && army_count(w, seat) >= minimum) {
        int enemy = nearest_enemy(w, seat);
        if (enemy >= 0 && (!w2 || army_count(w, seat) >= 1.5 * std::max(1, army_count(w, enemy))))
            w.submit_diplo(seat, "declare_war", {std::to_string(enemy)});
    }
    rule_economy(w, seat);
    if (!w.at_war_any(seat)) rule_military(w, seat, state);
    if (!w2 && w.turn >= start - 15) boost_recruit(w, seat);
    war_moves(w, seat);
}

}  // namespace rl
