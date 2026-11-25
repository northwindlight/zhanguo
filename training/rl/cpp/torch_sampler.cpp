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

using torch::Tensor;
using Batch = c10::Dict<std::string, Tensor>;
using Clock = std::chrono::steady_clock;

template <size_t D, typename T, typename Member>
void collate_rows(Batch& batch, const char* key, const std::vector<rl::Observation*>& obs,
                  Member member, c10::ScalarType dtype, bool pinned, T pad, bool mask = true) {
    int64_t width = 1;
    for (const auto* o : obs) width = std::max(width, static_cast<int64_t>((o->*member).size()));
    auto rows = at::empty({static_cast<int64_t>(obs.size()), width, static_cast<int64_t>(D)},
                          at::TensorOptions().dtype(dtype).pinned_memory(pinned));
    auto valid = at::zeros({static_cast<int64_t>(obs.size()), width},
                           at::TensorOptions().dtype(at::kBool).pinned_memory(pinned));
    std::fill_n(rows.data_ptr<T>(), rows.numel(), pad);
    for (size_t i = 0; i < obs.size(); ++i) {
        const auto& src = obs[i]->*member;
        if (!src.empty()) std::memcpy(rows.data_ptr<T>() + i * width * D, src.data(), src.size() * D * sizeof(T));
        std::fill_n(valid.data_ptr<bool>() + i * width, src.size(), true);
    }
    batch.insert(key, std::move(rows));
    if (mask) batch.insert(std::string(key) + "_mask", std::move(valid));
}

Batch collate(const std::vector<rl::Observation*>& obs, bool pinned) {
    using O = rl::Observation;
    Batch batch;
    const auto count = static_cast<int64_t>(obs.size());
    auto globals = at::empty({count, 1, rl::g_dim}, at::TensorOptions().dtype(at::kFloat).pinned_memory(pinned));
    for (size_t i = 0; i < obs.size(); ++i)
        std::copy(obs[i]->globals.begin(), obs[i]->globals.end(), globals.data_ptr<float>() + i * rl::g_dim);
    batch.insert("globals", std::move(globals));
    batch.insert("globals_mask", at::ones({count, 1}, at::TensorOptions().dtype(at::kBool).pinned_memory(pinned)));
    collate_rows<rl::m_dim>(batch, "market", obs, &O::market, at::kFloat, pinned, 0.0f);
    collate_rows<rl::n_dim>(batch, "nations", obs, &O::nations, at::kFloat, pinned, 0.0f);
    collate_rows<rl::t_dim>(batch, "tiles", obs, &O::tiles, at::kFloat, pinned, 0.0f);
    collate_rows<rl::a_dim>(batch, "armies", obs, &O::armies, at::kFloat, pinned, 0.0f);
    collate_rows<rl::c_dim>(batch, "cand", obs, &O::cand, at::kFloat, pinned, 0.0f);
    collate_rows<3>(batch, "cand_ptr", obs, &O::cand_ptr, at::kInt, pinned, int32_t{-1}, false);
    return batch;
}

struct ForwardBatch {
    Batch inputs;
    Tensor rows;  // Original slot indices when split.
};

std::vector<ForwardBatch> make_forward_batches(const std::vector<rl::Observation*>& observations,
                                               size_t minimum, bool pinned) {
    const size_t active = observations.size();
    if (minimum && minimum <= active / 2) {
        std::vector<int64_t> order(active);
        std::iota(order.begin(), order.end(), int64_t{0});
        auto width = [&](int64_t row) { return observations[row]->cand.size(); };
        std::stable_sort(order.begin(), order.end(), [&](int64_t a, int64_t b) { return width(a) < width(b); });
        const size_t peak = width(order.back());
        const size_t unsplit = active * peak;
        size_t best_cost = unsplit;
        size_t cut = 0;
        for (size_t left = minimum; left <= active - minimum; ++left) {
            const size_t cost = left * width(order[left - 1]) + (active - left) * peak;
            if (cost < best_cost) {
                best_cost = cost;
                cut = left;
            }
        }
        // Avoid a second forward for a marginal reduction in padded candidates.
        if (cut && best_cost <= 0.95 * unsplit) {
            std::vector<ForwardBatch> groups;
            for (auto [first, last] : {std::pair{size_t{0}, cut}, std::pair{cut, active}}) {
                auto rows = at::empty({static_cast<int64_t>(last - first)},
                                      at::TensorOptions().dtype(at::kLong).pinned_memory(pinned));
                std::copy(order.begin() + first, order.begin() + last, rows.data_ptr<int64_t>());
                std::vector<rl::Observation*> subset;
                subset.reserve(last - first);
                for (size_t i = first; i < last; ++i) subset.push_back(observations[order[i]]);
                groups.push_back({collate(subset, pinned), std::move(rows)});
            }
            return groups;
        }
    }
    return {{collate(observations, pinned), Tensor{}}};
}

void upload(Batch& inputs, c10::Device device) {
    for (auto& item : inputs) {
        const auto tensor = item.value();
        const auto dtype = tensor.scalar_type() == at::kInt ? at::kLong : tensor.scalar_type();
        item.setValue(tensor.to(device, dtype, device.is_cuda()));
    }
}

struct BatchTotals {
    uint64_t forwards = 0;
    uint64_t split_steps = 0;
    uint64_t candidates = 0;
    uint64_t tokens = 0;
    int64_t max_tokens = 0;

    void record(const std::vector<ForwardBatch>& groups) {
        forwards += groups.size();
        split_steps += groups.size() > 1;
        for (const auto& group : groups) {
            const auto cand = group.inputs.at("cand");
            int64_t width = 2 + cand.size(1);  // Includes global and recurrent-state tokens.
            for (const auto* key : {"market", "nations", "tiles", "armies"})
                width += group.inputs.at(key).size(1);
            candidates += cand.size(0) * cand.size(1);
            tokens += cand.size(0) * width;
            max_tokens = std::max(max_tokens, width);
        }
    }
};

class SamplingProfile {
public:
    enum Phase { Collate, Upload, Recurrent, Forward, Draw, Readback, Count };

    explicit SamplingProfile(c10::Device device) : device_(device) {}

    void start(Clock::time_point start, size_t active, size_t capacity) {
        start_ = start;
        ++steps_;
        full_steps_ += (active == capacity);
        min_active_ = std::min(min_active_, active);
    }

    void finish(Phase phase) {
        if (device_.is_cuda() && phase != Collate && phase != Readback) {
            c10::impl::VirtualGuardImpl guard(device_.type());
            const auto device = device_.has_index() ? device_ : guard.getDevice();
            guard.synchronizeStream(guard.getStream(device));
        }
        const auto now = Clock::now();
        seconds_[phase] += std::chrono::duration<double>(now - start_).count();
        start_ = now;
    }

    void print(uint64_t decisions, const BatchTotals& totals) const {
        std::cout << "profile_steps=" << steps_ << " profile_full_steps=" << full_steps_
                  << " profile_min_batch=" << min_active_
                  << " profile_mean_batch=" << static_cast<double>(decisions) / steps_
                  << " profile_mean_padded_tokens=" << static_cast<double>(totals.tokens) / decisions
                  << " profile_max_padded_tokens=" << totals.max_tokens;
        constexpr std::array labels{"collate", "upload", "recurrent", "forward", "draw", "readback"};
        for (size_t i = 0; i < labels.size(); ++i)
            std::cout << " profile_" << labels[i] << "_seconds=" << seconds_[i];
        std::cout << '\n';
    }

private:
    c10::Device device_;
    Clock::time_point start_;
    std::array<double, Count> seconds_{};
    size_t steps_ = 0;
    size_t full_steps_ = 0;
    size_t min_active_ = std::numeric_limits<size_t>::max();
};

class Autocast {
public:
    explicit Autocast(bool enabled) : enabled_(enabled) {
        if (!enabled_) return;
        at::autocast::set_autocast_dtype(at::kCUDA, at::kBFloat16);
        at::autocast::set_autocast_enabled(at::kCUDA, true);
    }
    ~Autocast() {
        if (!enabled_) return;
        at::autocast::set_autocast_enabled(at::kCUDA, false);
    }
private:
    bool enabled_;
};

class AutocastCache {
public:
    explicit AutocastCache(bool enabled) : enabled_(enabled) {
        if (enabled_) at::autocast::increment_nesting();
    }
    ~AutocastCache() {
        if (enabled_ && at::autocast::decrement_nesting() == 0) at::autocast::clear_cache();
    }
private:
    bool enabled_;
};

std::pair<Tensor, Tensor> predict(torch::jit::Module& policy, Batch& inputs, const Tensor& memory, bool bf16) {
    inputs.insert("mem", memory);
    Autocast autocast(bf16);
    const auto out = policy.forward({inputs}).toTuple();
    return {out->elements()[0].toTensor().to(at::kFloat), out->elements()[2].toTensor().to(at::kFloat)};
}

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

struct Slot {
    Slot(uint64_t seed, int size, int turns, int width, c10::Device device,
         const std::filesystem::path& output, const rl::OpponentPool& opponents)
        : episode(seed, size, turns, opponents) {
        hidden = at::zeros({width}, at::TensorOptions().device(device).dtype(at::kFloat));
        if (!output.empty()) trajectory = std::make_shared<rl::Trajectory>(output, seed);
    }

    rl::Episode episode;
    rl::View view;
    Tensor hidden;
    std::shared_ptr<rl::Trajectory> trajectory;
};

const char* usage =
    "usage:\n"
    "  zhanguo_torch verify MODEL FIXTURE cpu|cuda [bf16|native]\n"
    "  zhanguo_torch sample MODEL cpu|cuda GAMES BATCH SIZE TURNS SEED THREADS [fp32|bf16|native] [TRAJECTORY_DIR] [--opps name:weight,...]\n";

int sample(torch::jit::Module& policy, c10::Device device, int games, int batch_size,
           int size, int turns, uint64_t seed, int threads, bool bf16,
           const std::filesystem::path& output, const rl::OpponentPool& opponents) {
    if (games <= 0 || batch_size <= 0 || size < 8 || turns <= 0 || threads <= 0)
        throw std::invalid_argument("invalid rollout dimensions");
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
    const auto start = std::chrono::steady_clock::now();
    auto make_slot = [&] {
        return std::make_unique<Slot>(seed + started++, size, turns, width, device, output, opponents);
    };
    for (int i = 0; i < std::min(games, batch_size); ++i) batch.push_back(make_slot());

    AutocastCache cache(bf16);
    int completed = 0;
    uint64_t decisions = 0;
    double reward_sum = 0;
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
                previous.push_back(slot->hidden);
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
            for (size_t i = 0; i < new_turn.size(); ++i) new_turn[i]->hidden = next[i];
        }
        std::vector<Tensor> hidden;
        for (auto& slot : batch) hidden.push_back(slot->hidden);
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

        // The Python worker records the last sampled decision even when the move is forced to end the turn.
        for (size_t i = 0; i < count; ++i) {
            auto& s = *batch[i];
            if (!s.trajectory) continue;
            rl::View record;
            record.obs = std::move(s.view.obs);
            record.new_turn = s.view.new_turn;
            record.summary = s.view.summary;
            record.lost = s.view.lost;
            writer->submit([trajectory = s.trajectory, view = std::move(record), turn = s.episode.turn_index,
                            action = static_cast<int32_t>(p[i]), lp = p[i + count], value = p[i + count * 2]] {
                trajectory->step(view, turn, action, lp, value);
            });
        }
        #pragma omp parallel for schedule(static) num_threads(threads)
        for (int i = 0; i < static_cast<int>(count); ++i) {
            auto& s = *batch[i];
            s.episode.act(s.view.choices.at(static_cast<size_t>(p[i])));
        }

        for (auto& slot : batch) {
            const auto& e = slot->episode;
            if (!e.done) continue;
            const double reward = e.reward();
            if (slot->trajectory) {
                writer->submit([trajectory = slot->trajectory, reward, winner = e.winner(), seat = e.seat,
                                turn = e.world.turn, opponent = e.opponent] {
                    trajectory->finish(reward, winner, seat, turn, opponent);
                });
            }
            ++completed;
            reward_sum += reward;
            std::cout << "game seed=" << e.world.seed << " opp=" << rl::opponent_name(e.opponent)
                      << " seat=" << e.seat << " winner=" << e.winner() << " turns=" << e.world.turn
                      << " reward=" << e.reward() << " decisions=" << e.decisions << " invalid=" << e.invalid << '\n';
            slot = started < games ? make_slot() : nullptr;
        }
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
              << " decisions_per_second=" << decisions / seconds << " reward_mean=" << reward_sum / completed << '\n';
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
        for (int i = 10; i < argc; ++i) {
            const std::string_view arg = argv[i];
            if (arg == "--opps") {
                if (!configured.empty() || i + 1 == argc)
                    throw std::invalid_argument("--opps requires one opponent pool");
                configured = rl::parse_opponent_pool(argv[++i]);
            } else {
                if (arg.substr(0, 2) == "--" || positional.size() == 2)
                    throw std::invalid_argument("unexpected sample argument " + std::string(arg));
                positional.push_back(arg);
            }
        }
        if (configured.empty()) {
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
                      opponents);
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
