#pragma once
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

namespace rl_torch {
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

inline Batch collate(const std::vector<rl::Observation*>& obs, bool pinned) {
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

inline std::vector<ForwardBatch> make_forward_batches(const std::vector<rl::Observation*>& observations,
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

inline void upload(Batch& inputs, c10::Device device) {
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

inline std::pair<Tensor, Tensor> predict(torch::jit::Module& policy, Batch& inputs, const Tensor& memory, bool bf16) {
    inputs.insert("mem", memory);
    Autocast autocast(bf16);
    const auto out = policy.forward({inputs}).toTuple();
    return {out->elements()[0].toTensor().to(at::kFloat), out->elements()[2].toTensor().to(at::kFloat)};
}

}
