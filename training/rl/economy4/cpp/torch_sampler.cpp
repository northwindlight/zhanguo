#include "torch_inference.hpp"
#include "rollout.hpp"
#include "trajectory.hpp"
#include <ATen/Context.h>
#include <ATen/Parallel.h>
#include <ATen/autocast_mode.h>
#include <ATen/ops/cat.h>
#include <ATen/ops/empty.h>
#include <ATen/ops/full.h>
#include <ATen/ops/log.h>
#include <ATen/ops/multinomial.h>
#include <ATen/ops/ones.h>
#include <ATen/ops/softmax.h>
#include <ATen/ops/stack.h>
#include <ATen/ops/zeros.h>
#include <c10/core/impl/VirtualGuardImpl.h>
#include <torch/script.h>
#include <sstream>

#include <array>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <numeric>
#include <string_view>

namespace {

using namespace rl_torch;

int verify(torch::jit::Module& policy, const char* path, c10::Device device, bool bf16, bool native = false) {
    auto fixture = torch::jit::load(path, device);
    Batch batch;
    for (const auto* key : {"globals", "market", "nations", "tiles", "armies", "cand",
                            "globals_mask", "market_mask", "nations_mask", "tiles_mask", "armies_mask",
                            "cand_mask", "cand_ptr", "mem"})
        batch.insert(key, fixture.attr(key).toTensor());
    auto memory = policy.run_method("mem_step", fixture.attr("summary"), fixture.attr("hidden")).toTensor();
    auto check = [&](const char* name, const Tensor& actual, float tolerance) {
        auto difference = (actual.to(at::kFloat) - fixture.attr(name).toTensor()).abs();
        if (std::string(name) == "logits") difference = difference.masked_select(batch.at("cand_mask"));
        const double error = difference.max().item<float>();
        std::cout << name << " max_abs_error=" << error << '\n';
        return std::isfinite(error) && error < tolerance;
    };
    bool ok = check("memory", memory, 1e-3f);
    AutocastCache cache(bf16);
    Autocast autocast(bf16);
    const auto out = policy.forward({batch}).toTuple();
    float tolerance = (bf16 || native) ? 0.1f : 1e-3f;
    ok &= check("logits", out->elements()[0].toTensor(), tolerance);
    ok &= check("executable", out->elements()[1].toTensor(), tolerance);
    ok &= check("value", out->elements()[2].toTensor(), tolerance);
    return ok ? 0 : 1;
}

struct ExpansionProbe {
    int first, last;
    bool intervene;
    bool mixed = false;
};

struct ExpansionProgress {
    rl::W3State teacher;
    rl::IndustryTutorState industry;
    bool economic_takeover = true;
    int economic_attempts = 0;
    int land_before = -1, short_before = 0, starved_before = 0, first_gain = 0;
    int ended_turn = 0;
    int teacher_short_turns = -1, teacher_deaths = -1;
    double gdp_before = 0;
    std::string trace;
};

int owned_tiles(const World& world, int seat) {
    return std::count_if(world.tiles.begin(), world.tiles.end(), [seat](const Tile& t) { return t.owner == seat; });
}

struct Slot {
    Slot(uint64_t seed, int size, int turns, int width, c10::Device device,
         const std::filesystem::path& output, const rl::OpponentPool& opponents, rl::Scenario scenario,
         bool mixed)
        : episode(seed, size, turns, opponents, scenario) {
        for (int seat = first_seat(); seat < end_seat(); ++seat) {
            memory[seat] = at::zeros({width}, at::TensorOptions().device(device).dtype(at::kFloat));
            if (!output.empty() && (!mixed || seat == static_cast<int>(seed % 4)))
                tracks[seat] = std::make_shared<rl::Trajectory>(output, seed, episode.market4(),
                                                               episode.self_play() ? seat : -1, mixed);
        }
    }
    int first_seat() const { return episode.self_play() ? 0 : episode.seat; }
    int end_seat() const { return episode.self_play() ? 4 : episode.seat + 1; }
    Tensor& hidden() { return memory[episode.seat]; }

    rl::Episode episode;
    rl::View view;
    std::array<Tensor, 4> memory;
    std::array<std::shared_ptr<rl::Trajectory>, 4> tracks;
    ExpansionProgress expansion;
};

std::optional<size_t> teacher_choice(const rl::Episode& episode, const rl::View& view,
                                     const rl::Candidate& request, double keep_cash) {
    std::optional<size_t> best;
    const auto& world = episode.world;
    const bool buy = request.type == rl::Type::Buy;
    for (size_t index = 0; index < view.choices.size(); ++index) {
        const auto& choice = view.choices[index];
        if (!choice.executable || choice.type != request.type) continue;
        if (choice.type != rl::Type::Buy && choice.type != rl::Type::Sell) {
            if (choice.args == request.args) return index;
            continue;
        }
        if (choice.args[0] != request.args[0]) continue;
        const int amount = choice.args[1], wanted = request.args[1];
        if (buy) {
            const auto cost = world.market_walk(choice.args[0], amount, 0, episode.seat).second;
            if (world.res(episode.seat, GOLD) - world.market_cash(episode.seat, cost, 0).first < keep_cash) continue;
            auto rank = [wanted](int qty) { return std::pair{qty < wanted, std::abs(qty - wanted)}; };
            if (!best || rank(amount) < rank(view.choices[*best].args[1])) best = index;
        } else if (amount <= wanted && (!best || amount > view.choices[*best].args[1])) {
            best = index;
        }
    }
    return best;
}

void teaching_turn(Slot& slot, rl::TrajectoryWriter& writer) {
    auto& episode = slot.episode;
    const int seat = episode.seat;
    const int turn = episode.player(seat).turn_index;
    const auto& trajectory = slot.tracks[seat];
    const char* mode = std::getenv("ZHANGUO_TEACHER_PRODUCTION_ONLY");
    const char* curriculum = std::getenv("ZHANGUO_TEACHER_INDUSTRY");
    const bool industry = curriculum && std::string_view(curriculum) == "1";
    const bool production_only = industry || (mode && std::string_view(mode) == "1");
    slot.expansion.teacher.production_only = production_only;
    std::optional<rl::View> view(std::move(slot.view));
    auto record = [&](int action) {
        writer.submit([trajectory, view = std::move(*view), turn, action] {
            trajectory->step(view, turn, action, 0, 0, true);
        });
        view.reset();
    };
    rl::W3Executor execute = [&](const rl::Candidate& request, double keep_cash) {
        if (production_only && request.type == rl::Type::Buy && request.args[0] == SUPPLY - 1) return false;
        if (!view) view = episode.observe();
        const auto index = teacher_choice(episode, *view, request, keep_cash);
        if (!index || !episode.takeover_action(view->choices[*index])) return false;
        record(static_cast<int>(*index));
        return true;
    };
    if(industry)rl::industry_turn(episode.world,seat,slot.expansion.industry,slot.expansion.economic_takeover,execute);
    else rl::w3_turn(episode.world, seat, slot.expansion.teacher, nullptr, &execute);
    const int reserve = static_cast<int>(std::ceil(3 * episode.world.supply_need(seat) -
                                                  episode.world.res(seat, SUPPLY)));
    if (reserve > 0 && !production_only) execute({rl::Type::Buy, {SUPPLY - 1, reserve}}, 0);
    if (!view) view = episode.observe();
    const auto end = view->choices.front();
    record(0);
    episode.act(end);
}

const char* usage =
    "usage:\n"
    "  zhanguo_torch verify MODEL FIXTURE cpu|cuda [bf16|native]\n"
    "  zhanguo_torch sample MODEL cpu|cuda GAMES BATCH SIZE TURNS SEED THREADS [fp32|bf16|native] [TRAJECTORY_DIR] [--opps name:weight,... | --market4 [--stop-gdp VALUE] | --market4-selfplay [--stop-gdp VALUE] [--expansion-probe FIRST LAST policy|w3 | --mixed-bc FIRST LAST]]\n";

int sample(torch::jit::Module& policy, c10::Device device, int games, int batch_size,
           int size, int turns, uint64_t seed, int threads, bool bf16,
           const std::filesystem::path& output, const rl::OpponentPool& opponents, rl::Scenario scenario,
           std::optional<double> stop_gdp, std::optional<ExpansionProbe> probe) {
    if (games <= 0 || batch_size <= 0 || size < 8 || turns <= 0 || threads <= 0)
        throw std::invalid_argument("invalid rollout dimensions");
    const bool write_trajectories = !output.empty();
    const char* curriculum_mode = std::getenv("ZHANGUO_TEACHER_INDUSTRY");
    const bool industry_mode = curriculum_mode && std::string_view(curriculum_mode) == "1";
    const char* land_log_flag=std::getenv("ZHANGUO_REWARD_LAND_LOG");
    const bool land_log=land_log_flag&&std::string_view(land_log_flag)=="1";
    const char* import_flag=std::getenv("ZHANGUO_TEACHER_ALLOW_ORE_IMPORT");
    const bool industry_import=import_flag&&std::string_view(import_flag)=="1";
    const char* production_mode = std::getenv("ZHANGUO_TEACHER_PRODUCTION_ONLY");
    const bool production_only = industry_mode || (production_mode && std::string_view(production_mode) == "1");
    const int sustain_turns = production_only && !industry_mode ? 10 : 0;
    if (production_only && (!probe || !probe->mixed))
        throw std::invalid_argument("production-only tutoring requires mixed BC");
    if (probe && (scenario != rl::Scenario::Market4SelfPlay || stop_gdp || probe->mixed != write_trajectories ||
                  probe->first <= 1 || probe->last <= probe->first ||
                  probe->last > turns - (probe->mixed && !industry_mode ? 3 + sustain_turns : 0)))
        throw std::invalid_argument("invalid expansion schedule or trajectory output");
    const bool mixed = probe && probe->mixed;
    std::unique_ptr<rl::TrajectoryWriter> writer;
    if (!output.empty()) {
        std::filesystem::create_directories(output);
        writer = std::make_unique<rl::TrajectoryWriter>();
    }
    const int width = policy.run_method("memory_width").toTensor().item<int>();
    const char* profile_flag = std::getenv("ZHANGUO_SAMPLE_PROFILE");
    const bool profile_enabled = profile_flag && std::string_view(profile_flag) == "1";
    size_t bucket_minimum = 0;
    if (const char* flag = std::getenv("ZHANGUO_SAMPLE_BUCKET_MIN")) {
        const char* end = flag + std::char_traits<char>::length(flag);
        const auto [rest, error] = std::from_chars(flag, end, bucket_minimum);
        if (error != std::errc{} || rest != end)
            throw std::invalid_argument("ZHANGUO_SAMPLE_BUCKET_MIN must be a nonnegative integer");
    }
    const char* verify_flag = std::getenv("ZHANGUO_SAMPLE_VERIFY_BUCKETS");
    const bool verify_buckets = verify_flag && std::string_view(verify_flag) == "1";
    if (verify_buckets && !bucket_minimum)
        throw std::invalid_argument("bucket comparison requires ZHANGUO_SAMPLE_BUCKET_MIN");
    SamplingProfile profile(device);
    BatchTotals batch_totals;
    double max_bucket_probability_error = 0;
    double max_bucket_value_error = 0;
    std::vector<std::unique_ptr<Slot>> batch;
    int started = 0;
    uint64_t next_seed=seed;
    const auto start = std::chrono::steady_clock::now();
    auto make_slot = [&] {
        for(int trial=0;trial<10000;++trial){
            if(industry_mode&&!industry_import)
                while(next_seed%4!=(seed+started)%4)++next_seed;
            const uint64_t candidate=next_seed++;
            if(industry_mode&&!industry_import){
                World probe_world;probe_world.seed=candidate;probe_world.size=size;probe_world.max_turns=turns;probe_world.setup(4);
                int farms=0,mines=0;
                for(const auto& tile:probe_world.tiles)if(tile.owner==int(candidate%4)){
                    farms+=tile.tres[TR_FARM];mines+=tile.tres[TR_ORE];
                }
                if(farms<4||mines<2){std::cout<<"curriculum_skip seed="<<candidate<<" farm_capacity="<<farms<<" ore_capacity="<<mines<<'\n';continue;}
            }
            ++started;
            return std::make_unique<Slot>(candidate,size,turns,width,device,output,opponents,scenario,mixed);
        }
        throw std::runtime_error("Cannot find a map meeting industrial prerequisites");
    };
    for (int i = 0; i < std::min(games, batch_size); ++i) batch.push_back(make_slot());

    AutocastCache cache(bf16);
    int completed = 0;
    uint64_t decisions = 0;
    double reward_sum = 0;
    bool stopped = false;
    double observation_seconds = 0, inference_seconds = 0, action_seconds = 0;
    while (!batch.empty()) {
        const auto prepared_start = std::chrono::steady_clock::now();
        #pragma omp parallel for schedule(static) num_threads(threads)
        for (int i = 0; i < static_cast<int>(batch.size()); ++i)
            batch[i]->view = batch[i]->episode.observe();
        const auto inference_start = std::chrono::steady_clock::now();
        observation_seconds += std::chrono::duration<double>(inference_start - prepared_start).count();
        if (profile_enabled) profile.start(inference_start, batch.size(), std::min(games, batch_size));

        std::vector<rl::Observation*> observations;
        std::vector<Tensor> previous;
        std::vector<Slot*> new_turn;
        for (auto& slot : batch) {
            observations.push_back(&slot->view.obs);
            if (slot->view.new_turn) {
                new_turn.push_back(slot.get());
                previous.push_back(slot->hidden());
            }
        }
        Tensor features;
        if (!new_turn.empty()) {
            features = at::empty({static_cast<int64_t>(new_turn.size()), rl::f_dim}, at::kFloat);
            for (size_t i = 0; i < new_turn.size(); ++i)
                std::copy(new_turn[i]->view.summary.begin(), new_turn[i]->view.summary.end(),
                          features.data_ptr<float>() + i * rl::f_dim);
        }
        auto groups = make_forward_batches(observations, bucket_minimum, device.is_cuda());
        batch_totals.record(groups);
        if (profile_enabled) profile.finish(SamplingProfile::Collate);

        for (auto& group : groups) {
            upload(group.inputs, device);
            if (group.rows.defined()) group.rows = group.rows.to(device, at::kLong, device.is_cuda());
        }
        if (features.defined()) features = features.to(device);
        if (profile_enabled) profile.finish(SamplingProfile::Upload);

        if (features.defined()) {
            auto next = policy.run_method("mem_step", features, at::stack(previous)).toTensor();
            for (size_t i = 0; i < new_turn.size(); ++i) new_turn[i]->hidden() = next[i];
        }
        std::vector<Tensor> hidden;
        for (auto& slot : batch) hidden.push_back(slot->hidden());
        const auto memory = at::stack(hidden);
        if (profile_enabled) profile.finish(SamplingProfile::Recurrent);

        const auto count = batch.size();
        Tensor logits, value;
        if (groups.size() == 1) {
            std::tie(logits, value) = predict(policy, groups.front().inputs, memory, bf16);
        } else {
            const auto rows = static_cast<int64_t>(count);
            const auto width = groups.back().inputs.at("cand").size(1);
            const auto options = at::TensorOptions().dtype(at::kFloat).device(device);
            logits = at::full({rows, width}, -1e9, options);
            value = at::empty({rows}, options);
            for (auto& group : groups) {
                const auto [group_logits, group_value] =
                    predict(policy, group.inputs, memory.index_select(0, group.rows), bf16);
                logits.narrow(1, 0, group_logits.size(1)).index_copy_(0, group.rows, group_logits);
                value.index_copy_(0, group.rows, group_value);
            }
            if (verify_buckets) {
                auto full = collate(observations, device.is_cuda());
                upload(full, device);
                const auto [expected_logits, expected_value] = predict(policy, full, memory, bf16);
                const double probability_error =
                    (at::softmax(logits, -1) - at::softmax(expected_logits, -1)).abs().max().item<float>();
                const double value_error = (value - expected_value).abs().max().item<float>();
                if (!std::isfinite(probability_error) || !std::isfinite(value_error))
                    throw std::runtime_error("non-finite bucket comparison");
                max_bucket_probability_error = std::max(max_bucket_probability_error, probability_error);
                max_bucket_value_error = std::max(max_bucket_value_error, value_error);
            }
        }
        if (profile_enabled) profile.finish(SamplingProfile::Forward);
        const auto probs = at::softmax(logits, -1);
        const auto picks = at::multinomial(probs, 1);
        auto logp = at::log(probs.gather(1, picks).squeeze(1) + 1e-12);
        // One device-to-host transfer for the batch's actions, log probabilities and values.
        auto packed = at::stack({picks.squeeze(1).to(at::kFloat), logp, value});
        if (profile_enabled) profile.finish(SamplingProfile::Draw);
        auto outputs = packed.cpu().contiguous();
        if (profile_enabled) profile.finish(SamplingProfile::Readback);
        const auto* p = outputs.data_ptr<float>();
        const auto action_start = std::chrono::steady_clock::now();
        inference_seconds += std::chrono::duration<double>(action_start - inference_start).count();
        decisions += count;

        std::vector<int> prior_turn;
        std::vector<char> takeover;
        std::vector<char> hybrid_finish(count),hybrid_suppress(count),hybrid_expert(count);
        if (probe) {
            takeover.resize(count);
            for (size_t i = 0; i < count; ++i) {
                auto& slot = *batch[i];
                const auto& e = slot.episode;
                const auto& w = e.world;
                const int actor = static_cast<int>(w.seed % 4);
                auto& progress = slot.expansion;
                if(industry_mode && actor==e.seat && w.turn>=probe->first){
                    progress.industry.allow_ore_import=industry_import;
                    if(slot.view.new_turn){
                        progress.economic_attempts=0;
                        progress.economic_takeover=rl::industry_takeover(slot.episode.world,actor,progress.industry);
                    }
                    if(!progress.economic_takeover){
                        ++progress.economic_attempts;
                        const auto& chosen=slot.view.choices.at(static_cast<size_t>(p[i]));
                        const int kind=static_cast<int>(chosen.type);
                        const bool military=(kind==3||(kind>=6&&kind<=10))||
                            (kind==1&&(chosen.args[2]==BARRACKS||chosen.args[2]==MILITIA||
                                       chosen.args[2]==CASTLE||chosen.args[2]==WATCHTOWER));
                        hybrid_finish[i]=chosen.type==rl::Type::EndTurn||progress.economic_attempts>=20;
                        hybrid_suppress[i]=!hybrid_finish[i]&&(military||
                            (chosen.type==rl::Type::Buy&&chosen.args[0]==SUPPLY-1));
                        hybrid_expert[i]=!hybrid_finish[i]&&!hybrid_suppress[i]&&chosen.executable;
                    }
                }
                const int final_teacher = progress.first_gain ? progress.first_gain + sustain_turns : probe->last;
                if (!slot.view.new_turn || actor != e.seat || w.turn < probe->first ||
                    w.turn > std::max(probe->last, final_teacher)) continue;
                if (progress.land_before < 0) {
                    const auto& nation = w.nations[actor];
                    progress.land_before = owned_tiles(w, actor);
                    progress.short_before = nation.supply_short_turns;
                    progress.starved_before = nation.starved_units;
                    progress.gdp_before = e.player(actor).total_fixed_gdp;
                    std::cout << "expansion_start seed=" << w.seed << " seat=" << actor << " turn=" << w.turn
                              << " land=" << progress.land_before << " cash=" << w.res(actor, GOLD)
                              << " supply=" << w.res(actor, SUPPLY) << " need=" << w.supply_need(actor)
                              << " barracks=" << w.nation_bld_count(actor, BARRACKS)
                              << " gdp_before=" << progress.gdp_before << '\n';
                }
                takeover[i] = industry_mode ? progress.economic_takeover :
                    probe->intervene && (!progress.first_gain || w.turn <= final_teacher);
            }
        }
        if (scenario != rl::Scenario::Duel) {
            prior_turn.reserve(batch.size());
            for (const auto& slot : batch) prior_turn.push_back(slot->episode.world.turn);
        }
        for (size_t i = 0; i < count; ++i) {
            auto& s = *batch[i];
            const int actor = s.episode.seat;
            if (!s.tracks[actor]) continue;
            if (mixed && (takeover[i] || hybrid_finish[i] ||
                (!hybrid_expert[i]&&!s.view.new_turn&&s.episode.player(actor).decisions%11!=0)))
                continue;
            rl::View record;
            record.obs = std::move(s.view.obs);
            record.new_turn = s.view.new_turn;
            record.summary = s.view.summary;
            record.lost = s.view.lost;
            writer->submit([trajectory = s.tracks[actor], view = std::move(record), turn = s.episode.player(actor).turn_index,
                            action = static_cast<int32_t>(p[i]), lp = p[i + count], value = p[i + count * 2],
                            expert = bool(hybrid_expert[i])] {
                trajectory->step(view, turn, action, lp, value, expert, expert);
            });
        }
        #pragma omp parallel for schedule(static) num_threads(threads)
        for (int i = 0; i < static_cast<int>(count); ++i) {
            auto& s = *batch[i];
            if(hybrid_suppress[i]){
                ++s.expansion.industry.suppressed_actions;
                s.episode.suppress_action(s.view.choices.at(static_cast<size_t>(p[i])));
            } else if (probe && (takeover[i] || hybrid_finish[i])) {
                if (mixed) {
                    teaching_turn(s, *writer);
                } else {
                    std::ostringstream trace;
                    rl::w3_turn(s.episode.world, s.episode.seat, s.expansion.teacher, &trace);
                    s.expansion.trace = trace.str();
                    s.episode.act(s.view.choices.front());
                }
            } else {
                if(hybrid_expert[i])++s.expansion.industry.economic_actions;
                s.episode.act(s.view.choices.at(static_cast<size_t>(p[i])));
            }
        }

        for (size_t i = 0; i < prior_turn.size(); ++i) {
            auto& slot = batch[i];
            auto& e = slot->episode;
            const int turn = prior_turn[i];
            if (probe && !mixed && takeover[i]) {
                std::istringstream actions(std::move(slot->expansion.trace));
                for (std::string line; std::getline(actions, line);)
                    std::cout << "expansion_action seed=" << e.world.seed << " seat=" << e.world.seed % 4
                              << " turn=" << turn << ' ' << line << '\n';
            }
            if (!e.done && e.world.turn == turn) continue;
            const int first = e.first_player(turn);
            auto& progress = slot->expansion;
            if (probe && progress.land_before >= 0 && turn >= probe->first &&
                turn <= probe->last + (mixed ? 3 + sustain_turns : 0)) {
                const int actor = static_cast<int>(e.world.seed % 4);
                const int land = owned_tiles(e.world, actor);
                const bool gain = !progress.first_gain && land >= (industry_mode?12:progress.land_before+(mixed?2:1));
                if (gain) progress.first_gain = turn;
                const bool end = industry_mode ? (gain||turn>=probe->last) : mixed ? turn == (progress.first_gain ? progress.first_gain + sustain_turns + 3 : probe->last)
                                       : turn == probe->last;
                if (mixed && ((progress.first_gain && turn == progress.first_gain + sustain_turns) ||
                              (!progress.first_gain && turn == probe->last))) {
                    progress.teacher_short_turns = e.world.nations[actor].supply_short_turns - progress.short_before;
                    progress.teacher_deaths = e.world.nations[actor].starved_units - progress.starved_before;
                }
                if (mixed && end) {
                    e.done = true;
                    progress.ended_turn = turn;
                }
                if (gain || end) {
                    const auto& nation = e.world.nations[actor];
                    std::cout << (end ? "expansion_end" : "expansion_gain")
                              << " seed=" << e.world.seed << " seat=" << actor << " turn=" << turn
                              << " land=" << land << " first_gain=" << progress.first_gain
                              << " cash=" << e.world.res(actor, GOLD) << " supply=" << e.world.res(actor, SUPPLY)
                              << " need=" << e.world.supply_need(actor)
                              << " short_turns=" << nation.supply_short_turns - progress.short_before
                              << " factories=" << e.world.nation_bld_count(actor, SUPPLYFAC)
                              << " sustain_turns=" << sustain_turns
                              << " teacher_short_turns=" << progress.teacher_short_turns
                              << " teacher_deaths=" << progress.teacher_deaths
                              << " industry=" << industry_mode << " setup_turn=" << progress.industry.setup_turn
                              << " allow_ore_import=" << industry_import
                              << " economic_actions=" << progress.industry.economic_actions
                              << " suppressed_actions=" << progress.industry.suppressed_actions
                              << " equipment_takeovers=" << progress.industry.equipment_takeovers
                              << " heal_takeovers=" << progress.industry.heal_takeovers
                              << " disbands=" << progress.industry.disbands
                              << " blocked_resource_turns=" << progress.industry.blocked_resource_turns
                              << " farms=" << e.world.nation_bld_count(actor,FARM)
                              << " mines=" << e.world.nation_bld_count(actor,MINE)
                              << " lumber=" << e.world.nation_bld_count(actor,LUMBER)
                              << " oilwells=" << e.world.nation_bld_count(actor,OILWELL)
                              << " equipment_factories=" << e.world.nation_bld_count(actor,EQUIPFAC)
                              << " supply_output=" << nation.supply_output_last
                              << " equipment_output=" << nation.equipment_output_last
                              << " starved_units=" << nation.starved_units - progress.starved_before
                              << " gdp_since_start=" << e.player(actor).total_fixed_gdp - progress.gdp_before << '\n';
                }
            }
            for (int seat = slot->first_seat(); seat < slot->end_seat(); ++seat) {
                const auto& p = e.player(seat);
                if (slot->tracks[seat]) {
                    writer->submit([trajectory = slot->tracks[seat], index = p.turn_index, first,
                                    fixed = p.last_fixed_gdp, current = p.last_current_gdp] {
                        trajectory->gdp_turn(index, fixed, current, first);
                    });
                }
                std::cout << "market seed=" << e.world.seed << " turn=" << turn << " first=" << first
                          << " fixed=" << p.last_fixed_gdp << " current=" << p.last_current_gdp;
                for (double price : e.world.price) std::cout << ' ' << price;
                if (e.self_play()) std::cout << " seat=" << seat;
                if (land_log) std::cout << " land=" << owned_tiles(e.world,seat) << " initial_land=" << p.initial_land;
                std::cout << '\n';
            }
        }

        for (auto& slot : batch) {
            const auto& e = slot->episode;
            if (!e.done) continue;
            ++completed;
            const int finished_turns = mixed ? slot->expansion.ended_turn : e.world.turn;
            double highest = -std::numeric_limits<double>::infinity();
            int best_seat = e.seat;
            const auto opponent = e.self_play() ? "market4_selfplay" :
                                  e.market4() ? "market4" : rl::opponent_name(e.opponent);
            for (int seat = slot->first_seat(); seat < slot->end_seat(); ++seat) {
                const auto& p = e.player(seat);
                const double reward = e.reward(seat);
                reward_sum += reward;
                if (reward > highest) {
                    highest = reward;
                    best_seat = seat;
                }
                if (slot->tracks[seat]) {
                    writer->submit([trajectory = slot->tracks[seat], reward, winner = e.winner(), seat,
                                    turn = finished_turns, opponent = e.opponent] {
                        trajectory->finish(reward, winner, seat, turn, opponent);
                    });
                }
                std::cout << "game seed=" << e.world.seed << " opp=" << opponent
                          << " seat=" << seat << " winner=" << e.winner() << " turns=" << finished_turns
                          << " reward=" << reward << " decisions=" << p.decisions << " invalid=" << p.invalid;
                if (e.market4()) {
                    int regular = 0, militia = 0, land = 0;
                    for (const auto& a : e.world.armies) {
                        if (a.owner != seat || a.hp <= 0) continue;
                        if (a.kind == MIL) ++militia;
                        else ++regular;
                    }
                    for (const auto& tile : e.world.tiles) land += tile.owner == seat;
                    std::cout << " current_gdp=" << p.total_current_gdp << " blackout=" << p.blackout_turns
                              << " regular=" << regular << " militia=" << militia << " land=" << land
                              << " supply_short_turns=" << e.world.nations[seat].supply_short_turns
                              << " starved_units=" << e.world.nations[seat].starved_units
                              << " cash=" << e.world.res(seat, GOLD) << " supply=" << e.world.res(seat, SUPPLY)
                              << " recruits=" << p.recruits << " attacks=" << p.attacks
                              << " disbands=" << p.disbands
                              << " first_recruit=" << p.first_recruit << " first_attack=" << p.first_attack
                              << " initial_land=" << p.initial_land << " peak_land=" << p.peak_land
                              << " first_capture=" << p.first_capture << " first_two=" << p.first_two
                              << " first=" << e.first_player(1);
                }
                std::cout << '\n';
            }
            if (e.market4()) std::cout.flush();
            const double threshold = stop_gdp.value_or(std::numeric_limits<double>::infinity()) * e.world.turn;
            if (e.self_play() ? highest >= threshold : highest > threshold) {
                std::cout << "stop_gdp seed=" << e.world.seed << " seat=" << best_seat
                          << " average=" << highest / e.world.turn << std::endl;
                stopped = true;
                break;
            }
            slot = started < games ? make_slot() : nullptr;
        }
        if (stopped) break;
        batch.erase(std::remove(batch.begin(), batch.end(), nullptr), batch.end());
        action_seconds += std::chrono::duration<double>(std::chrono::steady_clock::now() - action_start).count();
    }
    if (writer) writer->join();
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    std::cout << "observation_seconds=" << observation_seconds << " inference_seconds=" << inference_seconds
              << " action_seconds=" << action_seconds << '\n';
    std::cout << "forward_batches=" << batch_totals.forwards << " bucket_split_steps=" << batch_totals.split_steps
              << " padded_candidate_rows=" << batch_totals.candidates
              << " padded_token_rows=" << batch_totals.tokens << '\n';
    if (profile_enabled) profile.print(decisions, batch_totals);
    if (verify_buckets)
        std::cout << "bucket_compared_steps=" << batch_totals.split_steps
                  << " bucket_max_probability_error=" << max_bucket_probability_error
                  << " bucket_max_value_error=" << max_bucket_value_error << '\n';
    std::cout << "completed=" << completed << " decisions=" << decisions << " seconds=" << seconds
              << " decisions_per_second=" << decisions / seconds
              << " reward_mean=" << reward_sum / (completed * (scenario == rl::Scenario::Market4SelfPlay ? 4.0 : 1.0)) << '\n';
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string command = argc > 1 ? argv[1] : "";
    if ((command == "verify" && argc < 5) || (command == "sample" && argc < 10)
        || (command != "verify" && command != "sample")) {
        std::cerr << usage;
        return 2;
    }
    try {
        c10::InferenceMode inference;
        at::set_num_threads(command == "sample" && std::string(argv[3]) == "cpu" ? std::max(1, std::stoi(argv[9])) : 1);
        at::set_num_interop_threads(1);
        c10::Device device(argv[command == "verify" ? 4 : 3]);
        auto policy = torch::jit::load(argv[2], device);
        policy.eval();
        if (command == "verify") {
            bool bf16 = argc > 5 && std::string(argv[5]) == "bf16" && device.is_cuda();
            return verify(policy, argv[3], device, bf16, argc > 5 && std::string(argv[5]) == "native");
        }
        const auto seed = std::stoull(argv[8]);
        at::manual_seed(seed);
        std::vector<std::string_view> positional;
        rl::OpponentPool configured;
        rl::Scenario scenario = rl::Scenario::Duel;
        std::optional<double> stop_gdp;
        std::optional<ExpansionProbe> probe;
        for (int i = 10; i < argc; ++i) {
            const std::string_view arg = argv[i];
            if (arg == "--market4" || arg == "--market4-selfplay") {
                if (scenario != rl::Scenario::Duel)
                    throw std::invalid_argument("select one four-country mode");
                scenario = arg == "--market4-selfplay" ? rl::Scenario::Market4SelfPlay : rl::Scenario::Market4;
            } else if (arg == "--stop-gdp") {
                if (stop_gdp || i + 1 == argc)
                    throw std::invalid_argument("--stop-gdp requires one threshold");
                const std::string_view value = argv[++i];
                double target = 0;
                const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), target);
                if (error != std::errc{} || end != value.data() + value.size() || !std::isfinite(target))
                    throw std::invalid_argument("--stop-gdp requires a finite number");
                stop_gdp = target;
            } else if (arg == "--opps") {
                if (!configured.empty() || i + 1 == argc)
                    throw std::invalid_argument("--opps requires one opponent pool");
                configured = rl::parse_opponent_pool(argv[++i]);
            } else if (arg == "--expansion-probe") {
                if (probe || i + 3 >= argc)
                    throw std::invalid_argument("--expansion-probe requires FIRST LAST policy|w3");
                const int first = std::stoi(argv[++i]);
                const int last = std::stoi(argv[++i]);
                const std::string_view mode = argv[++i];
                if (mode != "w3" && mode != "policy")
                    throw std::invalid_argument("expansion probe mode must be policy or w3");
                probe = ExpansionProbe{first, last, mode == "w3"};
            } else if (arg == "--mixed-bc") {
                if (probe || i + 2 >= argc)
                    throw std::invalid_argument("--mixed-bc requires FIRST LAST");
                const int first = std::stoi(argv[++i]);
                const int last = std::stoi(argv[++i]);
                probe = ExpansionProbe{first, last, true, true};
            } else {
                if (arg.substr(0, 2) == "--" || positional.size() == 2)
                    throw std::invalid_argument("unexpected sample argument " + std::string(arg));
                positional.push_back(arg);
            }
        }
        if (scenario != rl::Scenario::Duel && !configured.empty())
            throw std::invalid_argument("--opps applies to duels only");
        if (stop_gdp && scenario == rl::Scenario::Duel)
            throw std::invalid_argument("--stop-gdp requires a four-country mode");
        if (configured.empty() && scenario == rl::Scenario::Duel) {
            if (const char* pool = std::getenv("ZHANGUO_SAMPLE_OPPS"); pool && *pool)
                configured = rl::parse_opponent_pool(pool);
        }
        const auto& opponents = configured.empty() ? rl::default_opponents() : configured;
        const std::string_view precision = positional.empty() ? (device.is_cuda() ? "bf16" : "fp32") : positional[0];
        if (precision != "fp32" && precision != "native" && (precision != "bf16" || !device.is_cuda()))
            throw std::invalid_argument("bf16 sampling requires CUDA");
        return sample(policy, device, std::stoi(argv[4]), std::stoi(argv[5]), std::stoi(argv[6]),
                      std::stoi(argv[7]), seed, std::stoi(argv[9]), precision == "bf16",
                      positional.size() == 2 ? std::filesystem::path(positional[1]) : std::filesystem::path{},
                      opponents, scenario, stop_gdp, probe);
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
