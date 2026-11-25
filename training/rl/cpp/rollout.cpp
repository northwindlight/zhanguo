#include "rollout.hpp"
#include <charconv>
#include <stdexcept>

namespace rl {

const OpponentPool& default_opponents() {
    static const OpponentPool pool{{Opponent::Rule, 0.25},
        {Opponent::W1_25_2, 0.25 + 0.30}, {Opponent::W1_40_3, 0.25 + 0.30 + 0.25},
        {Opponent::W2, 1.0}};
    return pool;
}

OpponentPool parse_opponent_pool(std::string_view specification) {
    OpponentPool pool;
    double total = 0;
    for (;;) {
        const auto comma = specification.find(',');
        const auto entry = specification.substr(0, comma);
        const auto colon = entry.find(':');
        if (colon == std::string_view::npos)
            throw std::invalid_argument("opponents must be specified as name:weight");
        const auto opponent = parse_opponent(entry.substr(0, colon));
        if (!opponent) throw std::invalid_argument("unknown opponent " + std::string(entry.substr(0, colon)));
        if (std::any_of(pool.begin(), pool.end(), [&](const auto& row) { return row.opponent == *opponent; }))
            throw std::invalid_argument("duplicate opponent " + std::string(opponent_name(*opponent)));
        double weight = 0;
        const auto token = entry.substr(colon + 1);
        const char* end = token.data() + token.size();
        const auto [rest, error] = std::from_chars(token.data(), end, weight);
        if (error != std::errc{} || rest != end || !std::isfinite(weight) || weight <= 0 ||
            !std::isfinite(total + weight) || total + weight <= total)
            throw std::invalid_argument("opponent weights must be finite and positive");
        total += weight;
        pool.push_back({*opponent, total});
        if (comma == std::string_view::npos) break;
        specification.remove_prefix(comma + 1);
    }
    return pool;
}

Episode::Episode(uint64_t seed, int size, int max_turns, const OpponentPool& opponents) {
    if (opponents.empty()) throw std::invalid_argument("opponent pool is empty");
    PyRandom random;
    random.seed(seed);
    auto a = random.getrandbits(27), b = random.getrandbits(26);
    double draw = (a * 67108864.0 + b) / 9007199254740992.0;
    const double scaled = draw * opponents.back().cumulative_weight;
    auto choice = std::upper_bound(opponents.begin(), opponents.end(), scaled,
        [](double point, const auto& row) { return point < row.cumulative_weight; });
    opponent = choice == opponents.end() ? opponents.back().opponent : choice->opponent;
    uint32_t chosen;
    do { chosen = random.getrandbits(2); } while (chosen >= 2);
    seat = chosen;
    world.seed = seed;
    world.size = size;
    world.max_turns = max_turns;
    world.setup(2, {});
    world.begin_turn();
    advance(true);
}

void Episode::advance(bool starting) {
    const int other = 1 - seat;
    auto play_opponent = [&] {
        if (!world.alive(other)) return;
        opponent_turn(world, other, opponent, opponent_state_);
        world.end_actions(other);
    };
    if (!starting) {
        if (seat == 0) play_opponent();
        world.resolve_turn();
        if (!world.alive(seat) || !world.alive(other) || world.turn >= world.max_turns) {
            done = true;
            return;
        }
        world.begin_turn();
    }
    if (seat == 1) play_opponent();
}

View Episode::observe() {
    View view;
    view.choices = candidates(world, seat, true, &diplo_);
    view.obs = observer_.encode(world, seat, view.choices);
    view.new_turn = (step_ == 0);
    if (view.new_turn) {
        ++turn_index;
        view.summary = turn_features(view.obs, hist_, turn_invalid_, last_steps_);
        view.lost = aggregate_lost(previous_sight_, observer_.sight(), world.size);
        previous_sight_ = observer_.sight();
        hist_.fill(0);
        turn_invalid_ = 0;
    }
    return view;
}

void Episode::act(const Candidate& candidate) {
    ++decisions;
    ++hist_[static_cast<int>(candidate.type)];
    if (candidate.type == Type::EndTurn || step_ >= 30) {
        last_steps_ = step_;
        step_ = 0;
        world.end_actions(seat);
        advance(false);
        return;
    }
    ++step_;
    ++steps;
    if (!apply(world, seat, candidate)) {
        ++invalid;
        ++turn_invalid_;
    }
}

int Episode::winner() const {
    if (world.alive(0) == world.alive(1)) return -1;
    return world.alive(0) ? 0 : 1;
}

double Episode::reward() const {
    double own = power_score(world, seat);
    double top = std::max(own, power_score(world, 1 - seat));
    return own / (top == 0 ? 1 : top);
}

}  // namespace rl
