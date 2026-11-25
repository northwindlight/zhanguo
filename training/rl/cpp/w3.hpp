#pragma once

#include "engine.hpp"
#include <iosfwd>

namespace rl {

struct W3State {
    struct Cell {
        int terrain = -1;
        int owner = -1;
        int hall_owner = -1;
        bool blocked = false;
    };
    struct Rival {
        int spy_arrived = -1;
        int spy_sent = -10;
        int report_bought = 0;
        double strength = 0;
        bool initiated_war = false;
    };
    std::vector<Cell> known;
    std::vector<Rival> rivals;
    int capital = -1;
    int frontier_tower = -1;
    int tower_target = -1;
};

void w3_turn(World& world, int seat, W3State& state, std::ostream* trace = nullptr);

}  // namespace rl
