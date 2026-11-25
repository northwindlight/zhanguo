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

enum class Scenario { Duel, Market4, Market4SelfPlay };

struct SeatProgress {
    int decisions = 0, steps = 0, invalid = 0, turn_index = -1;
    int recruits = 0, attacks = 0, disbands = 0, first_recruit = 0, first_attack = 0;
    int initial_land = 0, peak_land = 0, first_capture = 0, first_two = 0;
    double last_fixed_gdp = 0, last_current_gdp = 0;
    double total_fixed_gdp = 0, total_current_gdp = 0;
    int blackout_turns = 0;

private:
    friend class Episode;
    int step = 0, turn_invalid = 0, last_steps = 0;
    std::array<int, type_count> hist{};
    Observer observer;
    DiplomacyCache diplo;
    std::vector<EnemySighting> previous_sight;
};

class Episode {
public:
    Episode(uint64_t seed, int size, int max_turns, const OpponentPool& opponents = default_opponents(),
            Scenario scenario = Scenario::Duel);
    View observe();
    void act(const Candidate& candidate);
    bool takeover_action(const Candidate& candidate);
    void suppress_action(const Candidate& candidate);
    void enable_dynamic();
    std::vector<int> ready_actors() const;
    View observe(int actor);
    void act(int actor,const Candidate& candidate);
    static std::vector<std::vector<int>> action_groups(const World& world,int first);

    World world;
    int seat = 0;
    Opponent opponent = Opponent::Rule;
    bool done = false;
    bool market4() const { return scenario_ != Scenario::Duel; }
    bool self_play() const { return scenario_ == Scenario::Market4SelfPlay; }
    const SeatProgress& player(int index) const { return players_.at(index); }
    int first_player(int turn) const;
    int winner() const;
    double reward() const;
    double reward(int index) const;

private:
    void advance(bool starting);
    void settle();
    bool dynamic_ = false;
    std::vector<std::vector<int>> groups_;
    std::array<bool,4> ended_{};
    Scenario scenario_;
    std::array<SeatProgress, 4> players_;
    std::array<Opponent, 4> opponents_{};
    std::array<OpponentState, 4> opponent_state_{};
};

}  // namespace rl
