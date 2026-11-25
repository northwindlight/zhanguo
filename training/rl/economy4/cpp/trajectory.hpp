#pragma once

#include "rollout.hpp"

#include <c10/util/Half.h>
#include <condition_variable>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <functional>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <thread>

namespace rl {

// Little-endian rollout records. Rename publishes a completed trajectory.
class Trajectory {
public:
    Trajectory(const std::filesystem::path& dir, uint64_t seed, bool market4 = false, int self_play_seat = -1,
               bool mixed = false)
        : path_(dir / (std::to_string(seed) +
                       (self_play_seat < 0 ? "" : "-" + std::to_string(self_play_seat)) + ".zgrl")),
          partial_(path_.string() + ".partial"),
          out_(partial_, std::ios::binary | std::ios::trunc), market4_(market4) {
        if (std::filesystem::exists(path_)) throw std::runtime_error("trajectory already exists: " + path_.string());
        if (!out_) throw std::runtime_error("cannot open " + partial_.string());
        const char* curriculum=std::getenv("ZHANGUO_TEACHER_INDUSTRY");
        const bool industry=curriculum&&std::string_view(curriculum)=="1";
        out_.write(mixed ? (industry?"ZGRL8\0\0\0":"ZGRL7\0\0\0") : self_play_seat >= 0 ? "ZGRL6\0\0\0" :
                   market4_ ? "ZGRL5\0\0\0" : "ZGRL3\0\0\0", 8);
        write(seed);
    }

    void step(const View& view, int turn_index, int32_t action, float logp, float value, bool teacher = false,
              bool economic_expert = false) {
        if (view.new_turn) {
            write(uint8_t{1});
            write(static_cast<int32_t>(turn_index));
            buffer(view.summary);
            buffer(view.lost);
        }
        write(static_cast<uint8_t>(economic_expert ? 6 : teacher ? 5 : 2));
        write(static_cast<int32_t>(turn_index));
        write(action);
        write(logp);
        write(value);
        const auto& o = view.obs;
        for (size_t rows : {o.nations.size(), o.tiles.size(), o.armies.size(), o.cand.size()})
            write(static_cast<uint32_t>(rows));
        half(o.globals);
        half_rows(o.market);
        // Nearly zero GDP can yield a finite military-to-GDP ratio beyond FP16 range.
        if (market4_) {
            for (const auto& row : o.nations) buffer(row);
        } else {
            half_rows(o.nations);
        }
        half_rows(o.tiles);
        half_rows(o.armies);
        half_rows(o.cand);
        for (const auto& p : o.cand_ptr) buffer(p);
        buffer(o.cand_type);
        if (!out_) throw std::runtime_error("cannot write " + partial_.string());
    }

    void gdp_turn(int turn, float fixed_gdp, float current_gdp, int first) {
        write(uint8_t{4});
        write(static_cast<int32_t>(turn));
        write(fixed_gdp);
        write(current_gdp);
        write(static_cast<int32_t>(first));
    }

    void finish(float reward, int winner, int seat, int turn, Opponent opponent) {
        write(uint8_t{3});
        write(reward);
        write(static_cast<int32_t>(winner));
        write(static_cast<int32_t>(seat));
        write(static_cast<int32_t>(turn));
        write(static_cast<int32_t>(opponent));
        out_.close();
        if (!out_) throw std::runtime_error("cannot finish " + partial_.string());
        std::filesystem::rename(partial_, path_);
    }

private:
    template <typename T>
    void write(T v) { out_.write(reinterpret_cast<const char*>(&v), sizeof(v)); }

    template <typename Range>
    void buffer(const Range& r) {
        if (!r.empty()) out_.write(reinterpret_cast<const char*>(r.data()), r.size() * sizeof(r[0]));
    }

    template <size_t D>
    void half(const std::array<float, D>& row) {
        std::array<c10::Half, D> values;
        for (size_t i = 0; i < D; ++i) values[i] = c10::Half(row[i]);
        buffer(values);
    }

    template <typename Rows>
    void half_rows(const Rows& rows) { for (const auto& row : rows) half(row); }

    std::filesystem::path path_, partial_;
    std::ofstream out_;
    bool market4_;
};

// One writer preserves source order. The queue stays bounded even on slower disks.
class TrajectoryWriter {
public:
    TrajectoryWriter() : worker_([this] { write_pending(); }) {}

    ~TrajectoryWriter() {
        stop();
        if (worker_.joinable()) worker_.join();
    }

    void submit(std::function<void()> work) {
        std::unique_lock lock(mutex_);
        changed_.wait(lock, [&] { return pending_.size() < queue_capacity || failure_; });
        if (failure_) std::rethrow_exception(failure_);
        pending_.push_back(std::move(work));
        changed_.notify_all();
    }

    void join() {
        stop();
        worker_.join();
        if (failure_) std::rethrow_exception(failure_);
    }

private:
    void stop() {
        std::unique_lock lock(mutex_);
        stopping_ = true;
        changed_.notify_all();
    }

    void write_pending() {
        try {
            for (;;) {
                std::function<void()> work;
                {
                    std::unique_lock lock(mutex_);
                    changed_.wait(lock, [&] { return stopping_ || !pending_.empty(); });
                    if (pending_.empty()) return;
                    work = std::move(pending_.front());
                    pending_.pop_front();
                    changed_.notify_all();
                }
                work();
            }
        } catch (...) {
            std::unique_lock lock(mutex_);
            failure_ = std::current_exception();
            pending_.clear();
            changed_.notify_all();
        }
    }

    static constexpr size_t queue_capacity = 512;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<std::function<void()>> pending_;
    std::exception_ptr failure_;
    bool stopping_ = false;
    std::thread worker_;
};

}  // namespace rl
