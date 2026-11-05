"""Exercise the production decoder wait primitive without audio or AppKit."""
import subprocess
import tempfile
import unittest
from pathlib import Path


class DecoderWakeRaces(unittest.TestCase):
    def test_control_epochs_park_resume_and_cannot_lose_commands(self):
        source = r'''
#include "DecodeWake.h"
#include <cassert>
#include <future>
#include <thread>
using namespace std::chrono_literals;
int main() {
    SBDecodeWake wake;
    // A control command between the state observation and wait must return
    // without sleeping. This also models shutdown before wait registration.
    for (int i = 0; i < 1000; ++i) {
        auto observed = wake.snapshot();
        wake.notify_all();
        wake.wait(observed, false);
    }
    assert(wake.waitReturns() == 0);
    for (int i = 0; i < 100; ++i) {
        auto observed = wake.snapshot();
        std::promise<void> entered;
        auto ready = entered.get_future();
        auto parked = std::async(std::launch::async, [&] {
            entered.set_value();
            wake.wait(observed, false);
        });
        ready.wait();
        wake.notify_all();
        assert(parked.wait_for(1s) == std::future_status::ready);
        parked.get();
    }
    auto observed = wake.snapshot();
    auto parked = std::async(std::launch::async, [&] { wake.wait(observed, false); });
    // Audio-consumer notifications and spurious wakeups cannot revive idle work.
    for (int i = 0; i < 10; ++i) wake.notify_one();
    assert(parked.wait_for(40ms) == std::future_status::timeout);
    auto before = wake.waitReturns();
    wake.notify_all();
    assert(parked.wait_for(1s) == std::future_status::ready);
    parked.get();
    assert(wake.waitReturns() == before + 1);
    // Active playback retains its refill fallback even with no notification.
    before = wake.waitReturns();
    wake.wait(wake.snapshot(), true);
    assert(wake.waitReturns() == before + 1);
    // A cancelled cloud read uses the same notification without changing its
    // retry deadline or allowing play/pause to masquerade as cancellation.
    std::atomic<bool> cancelled{false};
    auto cloud = std::async(std::launch::async, [&] {
        return wake.waitForCancellation(3s, [&] { return cancelled.load(); });
    });
    wake.notify_all();
    assert(cloud.wait_for(20ms) == std::future_status::timeout);
    cancelled.store(true);
    wake.notify_all();
    assert(cloud.wait_for(1s) == std::future_status::ready);
    assert(cloud.get());
}
'''
        with tempfile.TemporaryDirectory(prefix='sb-decode-wake-') as area:
            path = Path(area)
            (path / 'wait.cpp').write_text(source)
            subprocess.run(['c++', '-std=c++20', '-pthread', '-Wall', '-Wextra', '-Werror',
                            '-I', str(Path(__file__).resolve().parents[1] / 'native'),
                            str(path / 'wait.cpp'), '-o', str(path / 'wait')],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(path / 'wait')], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
