#pragma once

#include "engine.hpp"
#include "w3.hpp"
#include <map>
#include <string_view>

namespace rl {

enum class Opponent { Rule, W1_25_2, W1_40_3, W2, W3, RuleEconomy };

struct OpponentState {
    std::map<int, int> targets;  // C++ army ID -> tile index, reset each episode.
    W3State w3;
};

const char* opponent_name(Opponent opponent);
std::optional<Opponent> parse_opponent(std::string_view name);

void rule_economy(World& world, int seat);
void rule_military(World& world, int seat, OpponentState& state);
void opponent_turn(World& world, int seat, Opponent opponent, OpponentState& state);

}  // namespace rl
