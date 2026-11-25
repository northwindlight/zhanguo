#include "rollout.hpp"
#include <charconv>
#include <stdexcept>
#include <set>

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

Episode::Episode(uint64_t seed, int size, int max_turns, const OpponentPool& opponents, Scenario scenario)
    : scenario_(scenario) {
    world.seed = seed;
    world.size = size;
    world.max_turns = max_turns;
    world.national_wars = !market4();
    world.setup(market4() ? 4 : 2, {});
    world.begin_turn();
    if (self_play()) {
        for (int i = 0; i < static_cast<int>(world.nations.size()); ++i) {
            auto& p = players_[i];
            p.initial_land = std::count_if(world.tiles.begin(), world.tiles.end(),
                                          [i](const Tile& tile) { return tile.owner == i; });
            p.peak_land = p.initial_land;
        }
        seat = first_player(world.turn);
        return;
    }
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
    opponents_.fill(opponent);
    if (market4()) {
        seat = static_cast<int>(seed % 4);
        constexpr std::array styles{Opponent::Rule, Opponent::RuleEconomy, Opponent::W3};
        for (int offset = 1; offset < 4; ++offset)
            opponents_[(seat + offset) % 4] = styles[(offset - 1 + seed / 16) % styles.size()];
    }
    for (int i = 0; i < static_cast<int>(world.nations.size()); ++i) {
        auto& p = players_[i];
        p.initial_land = std::count_if(world.tiles.begin(), world.tiles.end(),
                                      [i](const Tile& tile) { return tile.owner == i; });
        p.peak_land = p.initial_land;
    }
    advance(true);
}

int Episode::first_player(int turn) const {
    if (!market4()) return 0;
    return static_cast<int>((world.seed / 4 + turn - 1) % 4);
}

void Episode::advance(bool starting) {
    if (dynamic_) {
        ended_[seat]=true;
        if (std::all_of(ended_.begin(),ended_.end(),[](bool done){return done;})) {
            for(int offset=0;offset<4;++offset)world.end_actions((first_player(world.turn)+offset)%4);
            settle();
            if(!done) {ended_.fill(false);groups_=action_groups(world,first_player(world.turn));}
        }
        return;
    }
    const int count = static_cast<int>(world.nations.size());
    if (self_play()) {
        seat = (seat + 1) % count;
        if (seat == first_player(world.turn)) {
            settle();
            if (!done) seat = first_player(world.turn);
        }
        return;
    }
    auto play_opponents = [&](bool before) {
        const int first = first_player(world.turn);
        const int position = (seat + count - first) % count;
        for (int i = before ? 0 : position + 1; i < (before ? position : count); ++i) {
            const int actor = (first + i) % count;
            if (!world.alive(actor)) continue;
            opponent_turn(world, actor, opponents_[actor], opponent_state_[actor]);
            world.end_actions(actor);
        }
    };
    if (!starting) {
        play_opponents(false);
        settle();
        if (done) return;
    }
    play_opponents(true);
}

void Episode::settle() {
    world.resolve_turn();
    if (market4()) {
        for (int i = 0; i < static_cast<int>(world.nations.size()); ++i) {
            if (!self_play() && i != seat) continue;
            const auto& nation = world.nations[i];
            auto& p = players_[i];
            p.last_fixed_gdp = world.alive(i) ? nation.fixed_gdp_last : 0.0;
            p.last_current_gdp = world.alive(i) ? nation.gdp_last : 0.0;
            p.total_fixed_gdp += p.last_fixed_gdp;
            p.total_current_gdp += p.last_current_gdp;
            p.blackout_turns += world.alive(i) && nation.grid_short;
            const int land = std::count_if(world.tiles.begin(), world.tiles.end(),
                                          [i](const Tile& tile) { return tile.owner == i; });
            p.peak_land = std::max(p.peak_land, land);
            if (!p.first_capture && land > p.initial_land) p.first_capture = world.turn;
            if (!p.first_two && land >= p.initial_land + 2) p.first_two = world.turn;
        }
    }
    done = world.turn >= world.max_turns || (!market4() && (!world.alive(seat) || !world.alive(1 - seat)));
    if (!done) world.begin_turn();
}

View Episode::observe() {
    auto& p = players_[seat];
    View view;
    const bool reserved=world.deferred_orders && std::any_of(world.recruit_reservations.begin(),world.recruit_reservations.end(),[&](const Army& a){return a.owner==seat;});
    if (reserved) {
        World projected=world;projected.project_recruits(seat);
        view.choices=candidates(projected,seat,true,&p.diplo);
        if(p.step>=game_action_budget)view.choices.resize(1);
        for(auto& choice:view.choices)if(choice.type==Type::Move||choice.type==Type::Attack||choice.type==Type::Retreat)
            if(std::any_of(world.recruit_reservations.begin(),world.recruit_reservations.end(),[&](const Army& a){return a.owner==seat && a.aid==choice.args[0];})) {
                choice.executable=false;choice.features[0]=0;
            }
        view.obs=p.observer.encode(projected,seat,view.choices);
    } else {
        view.choices = candidates(world, seat, true, &p.diplo);
        if(p.step>=game_action_budget)view.choices.resize(1);
        view.obs = p.observer.encode(world, seat, view.choices);
    }
    // Reserved global channels: current remaining/used fractions and explicit
    // absolute cap (scaled by 100). These update after every attempted action,
    // including failed or suppressed actions, rather than only at settlement.
    const int used=std::clamp(p.step,0,game_action_budget);
    view.obs.globals[budget_remaining_feature]=float(game_action_budget-used)/game_action_budget;
    view.obs.globals[budget_used_feature]=float(used)/game_action_budget;
    view.obs.globals[budget_limit_feature]=game_action_budget/100.0f;
    view.new_turn = (p.step == 0);
    if (view.new_turn) {
        ++p.turn_index;
        view.summary = turn_features(view.obs, p.hist, p.turn_invalid, p.last_steps);
        view.lost = aggregate_lost(p.previous_sight, p.observer.sight(), world.size);
        p.previous_sight = p.observer.sight();
        p.hist.fill(0);
        p.turn_invalid = 0;
    }
    return view;
}

void Episode::act(const Candidate& candidate) {
    auto& p = players_[seat];
    ++p.decisions;
    ++p.hist[static_cast<int>(candidate.type)];
    if (candidate.type == Type::EndTurn || p.step >= game_action_budget) {
        p.last_steps = p.step;
        p.step = 0;
        if (!dynamic_) world.end_actions(seat);
        advance(false);
        return;
    }
    ++p.step;
    ++p.steps;
    if (!apply(world, seat, candidate)) {
        ++p.invalid;
        ++p.turn_invalid;
    } else {
        if (candidate.type == Type::Disband) ++p.disbands;
        if (candidate.type == Type::Recruit) {
            ++p.recruits;
            if (!p.first_recruit) p.first_recruit = world.turn;
        }
        if (candidate.type == Type::Attack) {
            ++p.attacks;
            if (!p.first_attack) p.first_attack = world.turn;
        }
    }
}

std::vector<std::vector<int>> Episode::action_groups(const World& w,int first) {
    std::array<std::set<int>,4> envelope;
    for(const auto& a:w.armies) if(a.owner>=0 && a.owner<4 && a.hp>0) {
        auto& cells=envelope[a.owner];cells.insert(a.x*w.size+a.y);
        for(bool attack:{false,true})for(const auto& [p,cost]:w.reachable(a.owner,a,attack))cells.insert(p.first*w.size+p.second);
    }
    std::array<int,4> parent{0,1,2,3};
    auto root=[&](int x){while(parent[x]!=x)x=parent[x];return x;};
    for(int a=0;a<4;++a)for(int b=a+1;b<4;++b) {
        bool overlap=false;for(int cell:envelope[a])if(envelope[b].count(cell)){overlap=true;break;}
        if(overlap)parent[root(b)]=root(a);
    }
    std::vector<std::vector<int>> result;std::vector<int> roots;
    for(int i=0;i<4;++i){int actor=(first+i)%4,r=root(actor);auto it=std::find(roots.begin(),roots.end(),r);
        if(it==roots.end()){roots.push_back(r);result.push_back({actor});}else result[it-roots.begin()].push_back(actor);}
    return result;
}

void Episode::enable_dynamic() {
    if(!self_play())throw std::invalid_argument("dynamic sampling requires four-seat self-play");
    dynamic_=true;world.deferred_orders=true;ended_.fill(false);groups_=action_groups(world,first_player(world.turn));
}

std::vector<int> Episode::ready_actors() const {
    std::vector<int> result;if(done)return result;
    for(const auto& group:groups_)for(int actor:group)if(!ended_[actor]){result.push_back(actor);break;}
    return result;
}

View Episode::observe(int actor) {
    const auto ready=ready_actors();if(!std::count(ready.begin(),ready.end(),actor))throw std::logic_error("actor is blocked");
    const int previous=seat;seat=actor;auto result=observe();seat=previous;return result;
}

void Episode::act(int actor,const Candidate& candidate) {
    const auto ready=ready_actors();if(!std::count(ready.begin(),ready.end(),actor))throw std::logic_error("actor is blocked");
    const int previous=seat;seat=actor;act(candidate);seat=previous;
}

bool Episode::takeover_action(const Candidate& candidate) {
    auto& p = players_[seat];
    if (p.step >= game_action_budget || candidate.type == Type::EndTurn || !apply(world, seat, candidate))
        return false;
    ++p.step;
    ++p.steps;
    ++p.decisions;
    ++p.hist[static_cast<int>(candidate.type)];
    if (candidate.type == Type::Disband) ++p.disbands;
    if (candidate.type == Type::Recruit) {
        ++p.recruits;
        if (!p.first_recruit) p.first_recruit = world.turn;
    }
    if (candidate.type == Type::Attack) {
        ++p.attacks;
        if (!p.first_attack) p.first_attack = world.turn;
    }
    return true;
}

void Episode::suppress_action(const Candidate& candidate) {
    auto& p=players_[seat];
    ++p.decisions;++p.step;++p.steps;
    ++p.hist[static_cast<int>(candidate.type)];
}

int Episode::winner() const {
    if (market4()) return -1;
    if (world.alive(0) == world.alive(1)) return -1;
    return world.alive(0) ? 0 : 1;
}

double Episode::reward() const {
    return reward(seat);
}

double Episode::reward(int index) const {
    if (market4()) return players_.at(index).total_fixed_gdp;
    double own = power_score(world, index);
    double top = std::max(own, power_score(world, 1 - index));
    return own / (top == 0 ? 1 : top);
}

}  // namespace rl
