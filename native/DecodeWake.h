#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>

// Control commands carry an epoch so a command arriving between the decoder's
// state inspection and wait cannot be lost. The audio consumer keeps its old
// lock-free notification; only an actively consumed full ring uses timed waits.
class SBDecodeWake {
public:
    uint64_t snapshot() {
        std::lock_guard<std::mutex> lock(mutex_);
        return epoch_;
    }

    void notify_all() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            ++epoch_;
        }
        condition_.notify_all();
    }

    void notify_one() noexcept {
        condition_.notify_one();
    }

    void wait(uint64_t observed, bool activeConsumer) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (epoch_ != observed) return;
        if (activeConsumer) {
            // Preserve consumer notifications and the bounded refill fallback.
            condition_.wait_for(lock, std::chrono::milliseconds(3));
        } else {
            condition_.wait(lock, [&] { return epoch_ != observed; });
        }
        returns_.fetch_add(1, std::memory_order_relaxed);
    }

    template<class Predicate>
    bool waitForCancellation(std::chrono::seconds delay, Predicate cancelled) {
        std::unique_lock<std::mutex> lock(mutex_);
        return condition_.wait_for(lock, delay, cancelled);
    }

    uint64_t waitReturns() const {
        return returns_.load(std::memory_order_relaxed);
    }

private:
    std::mutex mutex_;
    std::condition_variable condition_;
    uint64_t epoch_ = 0;
    std::atomic<uint64_t> returns_{0};
};
