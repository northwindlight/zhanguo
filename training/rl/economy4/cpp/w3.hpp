#pragma once

#include "engine.hpp"
#include <functional>
#include <iosfwd>

namespace rl {

struct Candidate;
using W3Executor = std::function<bool(const Candidate&, double)>;

struct W3State {
    // Opt-in BC tutor. Ordinary W3 keeps its original market-purchase behavior.
    bool production_only = false;
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

void w3_turn(World& world, int seat, W3State& state, std::ostream* trace = nullptr,
             const W3Executor* executor = nullptr);

struct IndustryTutorState {
    W3State intelligence;
    bool initialized = false, setup_done = false, equipment_takeover = false, healing = false;
    bool require_equipment = false;
    bool allow_ore_import = false;
    bool allow_capacity_import = false; // Pure-script curriculum: expand before completing impossible local capacity.
    bool rebuilding = false;
    int repair_turns = 0, disbands = 0;
    int known_oil_capacity = 0, equipment_good_turns = 0, target = -1;
    int setup_turn = 0, equipment_takeovers = 0, heal_takeovers = 0, economic_actions = 0;
    int suppressed_actions = 0, blocked_resource_turns = 0, last_observed_turn = -1;
};
bool industry_takeover(World& world, int seat, IndustryTutorState& state);
void industry_turn(World& world, int seat, IndustryTutorState& state, bool economic_takeover,
                   const W3Executor& execute);
// Pure scripted BC opening: economic actions only, no recruitment or military orders.
void script_economy_turn(World& world,int seat,const W3Executor& execute);

}  // namespace rl
