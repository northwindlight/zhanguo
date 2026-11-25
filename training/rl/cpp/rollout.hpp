#pragma once

#include "opponents.hpp"
#include "rl_env.hpp"

namespace rl {

struct OpponentWeight {
    Opponent opponent;
    double cumulative_weight;
};
using OpponentPool = std::vector<OpponentWeight>;
OpponentPool parse_opponent_pool(std::string_view specification);
const OpponentPool& default_opponents();

struct View {
    std::vector<Candidate> choices;
    Observation obs;
    bool new_turn = false;
    std::array<float, f_dim> summary{};
    std::array<float, aux_dim> lost{};
};

class Episode {
public:
    Episode(uint64_t seed, int size, int max_turns, const OpponentPool& opponents = default_opponents());
    View observe();
    void act(const Candidate& candidate);

    World world;
    int seat = 0;
    Opponent opponent = Opponent::Rule;
    bool done = false;
    int decisions = 0, steps = 0, invalid = 0, turn_index = -1;
    int winner() const;
    double reward() const;

private:
    void advance(bool starting);
    int step_ = 0, turn_invalid_ = 0, last_steps_ = 0;
    std::array<int, type_count> hist_{};
    Observer observer_;
    DiplomacyCache diplo_;
    OpponentState opponent_state_;
    std::vector<EnemySighting> previous_sight_;
};

}  // namespace rl
