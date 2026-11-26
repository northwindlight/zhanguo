// Native game adapter for model evaluation without linking LibTorch.
#include "rollout.hpp"

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>

namespace {

struct ObservationView {
    int turn;
    int is_new_turn;
    int tiles, nations, armies, candidates;
    const float *global, *market, *nation, *tile, *army, *candidate, *executable;
    const int32_t *pointer, *type;
};

struct Result {
    int done, turn, seat, alive, opponents_alive, initial_land, land, decisions;
    int invalid, recruits, attacks, starved, shortage, blackouts;
    double nominal_gdp, fixed_gdp, total_nominal_gdp;
};

thread_local std::string error_message;

int land(const World& world, int seat) {
    return std::count_if(world.tiles.begin(), world.tiles.end(),
                         [seat](const Tile& tile) { return tile.owner == seat; });
}

class Match {
public:
    Match(uint64_t seed, int size, int turns)
        : episode(seed, size, turns, {}, rl::Scenario::Market4SelfPlay),
          learner(static_cast<int>(seed % 4)) {
        episode.enable_dynamic();
        seek_learner();
    }

    bool done() const {
        return episode.done || !episode.world.alive(learner);
    }

    void seek_learner() {
        while (!done()) {
            const auto ready = episode.ready_actors();
            if (std::find(ready.begin(), ready.end(), learner) != ready.end()) return;
            if (ready.empty()) throw std::logic_error("evaluation scheduler is blocked");
            script_wave();
        }
    }

    const rl::View& observe() {
        if (done()) throw std::logic_error("completed match has no observation");
        if (!current) current = episode.observe(learner);
        return *current;
    }

    void play(int index) {
        if (!current || index < 0 || index >= static_cast<int>(current->choices.size()))
            throw std::out_of_range("action index is outside current observation");
        const int period = episode.world.mperiod;
        episode.act(learner, current->choices[index]);
        current.reset();
        if (!done() && episode.world.mperiod == period) script_wave();
        seek_learner();
    }

    Result result() const {
        const World& world = episode.world;
        const auto& p = episode.player(learner);
        const auto& nation = world.nations[learner];
        int opponents_alive = 0;
        for (int seat=0; seat<4; ++seat)
            if (seat != learner && world.alive(seat)) ++opponents_alive;
        return {done(), world.mperiod, learner, world.alive(learner), opponents_alive,
                p.initial_land, land(world, learner), p.decisions, p.invalid, p.recruits, p.attacks,
                nation.starved_units, nation.supply_short_turns, p.blackout_turns,
                p.last_current_gdp, p.last_fixed_gdp, p.total_current_gdp};
    }

    rl::Episode episode;
    const int learner;

private:
    void script_wave() {
        const auto ready = episode.ready_actors();
        const int period = episode.world.mperiod;
        for (int seat : ready) {
            if (seat == learner) continue;
            auto view = episode.observe(seat);
            if (episode.world.alive(seat))
                rl::opponent_turn(episode.world, seat, rl::Opponent::W3, opponents.at(seat));
            episode.act(seat, view.choices.at(0));
            if (episode.world.mperiod != period || done()) break;
        }
    }

    std::optional<rl::View> current;
    std::array<rl::OpponentState, 4> opponents;
};

class SelfPlay {
public:
    SelfPlay(uint64_t seed, int size, int turns)
        : episode(seed, size, turns, {}, rl::Scenario::Market4SelfPlay) {
        episode.enable_dynamic();
    }

    int actor() {
        if (episode.done) return -1;
        if (cursor == wave.size()) {
            wave = episode.ready_actors();
            if (wave.empty()) throw std::logic_error("self-play scheduler is blocked");
            for (int seat : wave) views[seat] = episode.observe(seat);
            cursor = 0;
        }
        return wave[cursor];
    }

    void play(int index) {
        const int seat = actor();
        if (seat < 0 || index < 0 || index >= static_cast<int>(views[seat].choices.size()))
            throw std::out_of_range("action index is outside the self-play observation");
        episode.act(seat, views[seat].choices[index]);
        ++cursor;
    }

    Result result(int seat) const {
        const World& w = episode.world;
        const auto& p = episode.player(seat);
        int opponents_alive = 0;
        for (int other = 0; other < 4; ++other)
            if (other != seat && w.alive(other)) ++opponents_alive;
        const auto& nation = w.nations[seat];
        return {episode.done, w.mperiod, seat, w.alive(seat), opponents_alive,
                p.initial_land, land(w, seat), p.decisions, p.invalid, p.recruits, p.attacks,
                nation.starved_units, nation.supply_short_turns, p.blackout_turns,
                p.last_current_gdp, p.last_fixed_gdp, p.total_current_gdp};
    }

    rl::Episode episode;
    std::array<rl::View, 4> views;

private:
    std::vector<int> wave;
    size_t cursor = 0;
};

template <class Function>
int handle_error(Function&& f) {
    try {
        f();
        return 0;
    } catch (const std::exception& e) {
        error_message = e.what();
        return -1;
    }
}

template <class T>
auto vector_data(const std::vector<T>& rows) {
    return rows.empty() ? nullptr : rows.front().data();
}

void write_view(ObservationView* out, const rl::View& view, int turn) {
    const auto& o = view.obs;
    *out = {turn, view.new_turn, int(o.tiles.size()), int(o.nations.size()), int(o.armies.size()),
            int(o.cand.size()), o.globals.data(), o.market.front().data(), vector_data(o.nations),
            vector_data(o.tiles), vector_data(o.armies), vector_data(o.cand), o.cand_exec.data(),
            vector_data(o.cand_ptr), o.cand_type.data()};
}

}  // namespace

extern "C" {

const char* zhanguo_eval_error() { return error_message.c_str(); }

Match* zhanguo_eval_create(uint64_t seed, int size, int turns) {
    Match* match = nullptr;
    handle_error([&] {
        if (size < 8 || size > 40 || turns < 1)
            throw std::invalid_argument("invalid evaluation dimensions");
        match = new Match(seed, size, turns);
    });
    return match;
}

void zhanguo_eval_destroy(Match* match) { delete match; }

int zhanguo_eval_observe(Match* match, ObservationView* out) {
    return handle_error([&] {
        write_view(out, match->observe(), match->episode.world.turn);
    });
}

int zhanguo_eval_play(Match* match, int index) {
    return handle_error([&] { match->play(index); });
}

void zhanguo_eval_result(const Match* match, Result* out) { *out = match->result(); }

SelfPlay* zhanguo_eval_self_create(uint64_t seed, int size, int turns) {
    SelfPlay* match = nullptr;
    handle_error([&] {
        if (size < 8 || size > 40 || turns < 1)
            throw std::invalid_argument("invalid self-play dimensions");
        match = new SelfPlay(seed, size, turns);
    });
    return match;
}

void zhanguo_eval_self_destroy(SelfPlay* match) { delete match; }

int zhanguo_eval_self_observe(SelfPlay* match, ObservationView* out, int* actor) {
    return handle_error([&] {
        *actor = match->actor();
        if (*actor >= 0)
            write_view(out, match->views[*actor], match->episode.world.turn);
    });
}

int zhanguo_eval_self_play(SelfPlay* match, int index) {
    return handle_error([&] { match->play(index); });
}

int zhanguo_eval_self_advance(SelfPlay* match, int index, int* period) {
    return handle_error([&] {
        match->play(index);
        *period = match->episode.world.mperiod;
    });
}

void zhanguo_eval_self_result(const SelfPlay* match, Result* out) {
    for (int seat = 0; seat < 4; ++seat) out[seat] = match->result(seat);
}

}
