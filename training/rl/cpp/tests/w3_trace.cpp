#include "opponents.hpp"

#include <iomanip>
#include <iostream>

namespace {
void snapshot(const World& world) {
    std::cout << "settled " << world.turn << '\n';
    for (int n = 0; n < static_cast<int>(world.nations.size()); ++n) {
        const auto& nation = world.nations[n];
        std::cout << "nation " << n << ' ' << nation.alive;
        if (nation.alive) {
            for (double amount : nation.res) std::cout << ' ' << amount;
            std::cout << ' ' << nation.energy_out << ' ' << nation.energy_use << ' ' << nation.grid_short;
        }
        std::cout << '\n';
        for (const auto& report : world.econ_reports[n]) {
            if (report.period_end != world.turn) continue;
            std::cout << "bank " << n << ' ' << report.report_turn << ' ' << report.gdp << ' ' << report.invest
                      << ' ' << report.assets << ' ' << report.military_ratio.has_value()
                      << ' ' << report.military_ratio.value_or(0) << '\n';
        }
    }
    for (const auto& a : world.armies)
        if (a.owner >= 0)
            std::cout << "army " << a.owner << ' ' << a.aid << ' ' << a.kind << ' ' << a.x * world.size + a.y
                      << ' ' << a.hp << ' ' << a.engaged << '\n';
    for (int i = 0; i < static_cast<int>(world.tiles.size()); ++i) {
        const auto& t = world.tiles[i];
        if (t.owner < 0) continue;
        std::cout << "tile " << i << ' ' << t.owner;
        for (int count : t.bld) std::cout << ' ' << count;
        std::cout << '\n';
    }
    for (const auto& spy : world.spy_pending)
        std::cout << "pending_spy " << spy.from << ' ' << spy.to << ' ' << spy.arrive << '\n';
    std::cout << "war " << world.at_war(0, 1) << '\n';
}
}

int main(int argc, char** argv) {
    if (argc != 6) return 2;
    const auto left = rl::parse_opponent(argv[3]), right = rl::parse_opponent(argv[4]);
    if (!left || !right) return 2;
    const rl::Opponent players[] = {*left, *right};
    rl::OpponentState states[2];
    World world;
    world.seed = std::stoull(argv[1]);
    world.size = 13;
    world.max_turns = std::stoi(argv[2]);
    world.setup(2);
    world.bank_on = std::string_view(argv[5]) == "1";
    std::cout << std::setprecision(17);
    do {
        world.begin_turn();
        for (int n = 0; n < 2; ++n) {
            if (players[n] == rl::Opponent::W3) {
                std::cout << "actions " << world.turn << ' ' << n << '\n';
                rl::w3_turn(world, n, states[n].w3, &std::cout);
            } else {
                rl::opponent_turn(world, n, players[n], states[n]);
            }
            world.end_actions(n);
        }
        world.resolve_turn();
        snapshot(world);
    } while (world.turn < world.max_turns && world.alive(0) && world.alive(1));
    const int winner = world.alive(0) == world.alive(1) ? -1 : world.alive(0) ? 0 : 1;
    std::cout << "done " << winner << ' ' << world.turn << '\n';
}
