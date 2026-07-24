#import "SoundboxDeck.h"

#import <AppKit/AppKit.h>
#import <AudioToolbox/AudioToolbox.h>
#import <AVFAudio/AVFAudio.h>
#import <CoreAudio/CoreAudio.h>
#import <CoreFoundation/CoreFoundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalDisplayLink.h>
#import <QuartzCore/CAMetalLayer.h>
#import <mach/mach_time.h>
#import <pthread/qos.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <limits>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

constexpr uint32_t kRingFrames = 1u << 20;
constexpr uint32_t kRingMask = kRingFrames - 1;
constexpr uint32_t kDecodeChunk = 8192;
constexpr uint32_t kStartThreshold = 4096;
constexpr float kPixelsPerSecond = 92.0f;
constexpr float kOverviewHeight = 28.0f;
constexpr float kRulerTop = 31.0f;
constexpr float kZoomTop = 49.0f;
constexpr size_t kOverviewColumns = 4096;
constexpr size_t kAudioDetailMipLevels = 8;

struct WaveSample {
    float height;
    float r;
    float g;
    float b;
};

struct AudioEnvelopeSample {
    uint8_t height;
};

struct GridVertex {
    uint32_t songMillis;
    float songMillisPhase;
    float xOffsetPixels;
    float yPixels;
    float r;
    float g;
    float b;
    float a;
    uint32_t overview;
};

struct BeatPosition {
    double seconds;
    uint32_t bar;
    uint8_t beat;
};

struct Uniforms {
    float currentSeconds;
    uint32_t currentMillis;
    float currentMillisPhase;
    float pixelsPerSecond;
    float widthPoints;
    float heightPoints;
    float samplesPerSecond;
    uint32_t sampleCount;
    float drawableScale;
    float durationSeconds;
    uint32_t overviewSampleCount;
    float detailSamplesPerSecond;
    uint32_t detailSampleCount;
    float audioDetailSamplesPerSecond;
    uint32_t audioDetailSampleCount;
    uint32_t audioDetailCurrentIndex;
    float audioDetailCurrentPhase;
    uint32_t audioDetailMipCount;
    uint32_t audioDetailMipOffsets[kAudioDetailMipLevels];
    uint32_t audioDetailMipCounts[kAudioDetailMipLevels];
};

struct AudioRing {
    std::vector<float> left = std::vector<float>(kRingFrames);
    std::vector<float> right = std::vector<float>(kRingFrames);
    std::atomic<uint64_t> read{0};
    std::atomic<uint64_t> write{0};

    uint64_t available() const {
        return write.load(std::memory_order_acquire)
             - read.load(std::memory_order_acquire);
    }

    uint64_t freeSpace() const {
        return kRingFrames - available();
    }

    void clear() {
        read.store(0, std::memory_order_release);
        write.store(0, std::memory_order_release);
    }
};

class SBDeck;

@interface SBMetalView : NSView
@property(nonatomic, strong) CAMetalLayer *deckLayer;
- (void)syncDrawableGeometry;
@end

@implementation SBMetalView
- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (self) {
        self.wantsLayer = YES;
        _deckLayer = [CAMetalLayer layer];
        self.layer = _deckLayer;
        self.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    }
    return self;
}
- (NSView *)hitTest:(NSPoint)point {
    (void)point;
    return nil;
}
- (void)layout {
    [super layout];
    [self syncDrawableGeometry];
}
- (void)syncDrawableGeometry {
    CGFloat scale = self.window.backingScaleFactor;
    if (scale <= 0) scale = 2.0;
    const NSSize backing = [self convertSizeToBacking:self.bounds.size];
    const CGSize drawable = CGSizeMake(backing.width, backing.height);
    self.deckLayer.contentsScale = scale;
    if (!CGSizeEqualToSize(self.deckLayer.drawableSize, drawable)) {
        self.deckLayer.drawableSize = drawable;
    }
}
@end

@interface SBDisplayDelegate : NSObject<CAMetalDisplayLinkDelegate>
@property(nonatomic, assign) SBDeck *deck;
@end

class SBDeck {
public:
    explicit SBDeck(NSView *parent) : parentView_(parent) {
        ring_.clear();
        createMetalView();
        startAudioUnit();
        installDeviceListener();
        decodeThread_ = std::thread([this] { decodeLoop(); });
        startDisplayThread();
    }

    ~SBDeck() {
        stopRequested_.store(true, std::memory_order_release);
        decodeWake_.notify_all();
        if (decodeThread_.joinable()) {
            decodeThread_.join();
        }
        removeDeviceListener();
        stopDisplayThread();
        stopAudioUnit();
        removeMetalView();
    }

    bool load(const char *path) {
        if (!path || !*path) {
            setError(-1, "Empty audio path");
            return false;
        }
        playing_.store(false, std::memory_order_release);
        playRequested_.store(false, std::memory_order_release);
        requestedSeek_.store(0, std::memory_order_release);
        positionFrames_.store(0, std::memory_order_release);
        durationFrames_.store(0, std::memory_order_release);
        loaded_.store(false, std::memory_order_release);
        ended_.store(false, std::memory_order_release);
        visualClockValid_.store(false, std::memory_order_release);
        const uint64_t generation =
            generation_.fetch_add(1, std::memory_order_acq_rel) + 1;
        {
            std::lock_guard<std::mutex> lock(commandMutex_);
            pendingPath_ = path;
            pendingGeneration_ = generation;
            pathPending_ = true;
        }
        clearError();
        decodeWake_.notify_all();
        requestDisplay();
        return true;
    }

    bool beginTrack(uint64_t generation) {
        renderGeneration_.store(generation, std::memory_order_release);
        previewActive_.store(false, std::memory_order_release);
        std::lock_guard<std::mutex> lock(renderMutex_);
        waveBuffer_ = nil;
        detailBuffer_ = nil;
        audioDetailBuffer_ = nil;
        overviewBuffer_ = nil;
        gridBuffer_ = nil;
        cueBuffer_ = nil;
        sessionCueBuffer_ = nil;
        waveSampleCount_ = 0;
        detailSampleCount_ = 0;
        audioDetailSampleCount_ = 0;
        overviewSampleCount_ = 0;
        gridVertexCount_ = 0;
        cueVertexCount_ = 0;
        sessionCueVertexCount_ = 0;
        cueMarkerCount_ = 0;
        waveSamplesPerSecond_ = 0;
        detailSamplesPerSecond_ = 0;
        audioDetailSamplesPerSecond_ = 0;
        audioDetailMipCount_ = 0;
        audioDetailMipOffsets_.fill(0);
        audioDetailMipCounts_.fill(0);
        beatPositions_.clear();
        return true;
    }

    void play() {
        if (ended_.load(std::memory_order_acquire)) {
            seek(0);
        }
        playRequested_.store(true, std::memory_order_release);
        visualClockValid_.store(false, std::memory_order_release);
        if (loaded_.load(std::memory_order_acquire)
                && decodedSeekSerial_.load(std::memory_order_acquire)
                    == seekSerial_.load(std::memory_order_acquire)
                && ring_.available() >= kStartThreshold) {
            playing_.store(true, std::memory_order_release);
        }
        decodeWake_.notify_all();
        requestDisplay();
    }

    void pause() {
        playRequested_.store(false, std::memory_order_release);
        playing_.store(false, std::memory_order_release);
        visualClockValid_.store(false, std::memory_order_release);
        requestDisplay();
    }

    void stop() {
        pause();
        seek(0);
    }

    void seek(uint64_t frame) {
        const uint64_t duration = durationFrames_.load(std::memory_order_acquire);
        frame = duration ? std::min(frame, duration) : frame;
        playing_.store(false, std::memory_order_release);
        requestedSeek_.store(frame, std::memory_order_release);
        positionFrames_.store(frame, std::memory_order_release);
        ended_.store(false, std::memory_order_release);
        visualClockValid_.store(false, std::memory_order_release);
        seekSerial_.fetch_add(1, std::memory_order_acq_rel);
        decodeWake_.notify_all();
        requestDisplay();
    }

    void setGain(float gain) {
        gain = std::clamp(gain, 0.0f, 1.0f);
        currentGain_.store(gain, std::memory_order_release);
        targetGain_.store(gain, std::memory_order_release);
        rampFramesRemaining_.store(0, std::memory_order_release);
        const uint64_t serial = rampSerial_.fetch_add(1) + 1;
        rampCompleted_.store(serial, std::memory_order_release);
    }

    uint64_t rampGain(float gain, double durationMs) {
        gain = std::clamp(gain, 0.0f, 1.0f);
        targetGain_.store(gain, std::memory_order_release);
        const double rate = std::max(1.0, sampleRate_.load());
        uint64_t frames = static_cast<uint64_t>(
            std::max(1.0, durationMs * rate / 1000.0));
        rampFramesRemaining_.store(frames, std::memory_order_release);
        rampTotalFrames_.store(frames, std::memory_order_release);
        rampStartGain_.store(currentGain_.load(), std::memory_order_release);
        return rampSerial_.fetch_add(1, std::memory_order_acq_rel) + 1;
    }

    void setPreview(uint64_t frame, bool active) {
        previewFrame_.store(frame, std::memory_order_release);
        previewActive_.store(active, std::memory_order_release);
        requestDisplay();
    }

    void setZoom(float pixelsPerSecond) {
        zoomPixelsPerSecond_.store(
            std::clamp(pixelsPerSecond, 64.0f, 1536.0f),
            std::memory_order_release);
        requestDisplay();
    }

    void requestDisplay() {
        displayDirty_.store(true, std::memory_order_release);
        CFRunLoopRef runLoop = nullptr;
        {
            std::lock_guard<std::mutex> lock(displayRunLoopMutex_);
            runLoop = displayRunLoop_;
            if (runLoop) {
                CFRetain(runLoop);
            }
        }
        if (!runLoop) {
            return;
        }
        CFRunLoopPerformBlock(runLoop, kCFRunLoopCommonModes, ^{
            if (!displayStop_.load(std::memory_order_acquire)
                    && displayLink_) {
                displayLink_.paused = NO;
            }
        });
        CFRunLoopWakeUp(runLoop);
        CFRelease(runLoop);
    }

    void syncViewGeometry() {
        auto sync = ^{
            if (!parentView_ || !metalView_) {
                return;
            }
            const NSRect frame = parentView_.bounds;
            if (!NSEqualRects(metalView_.frame, frame)) {
                metalView_.frame = frame;
            }
            // Qt has already committed the host NSView's new bounds when its
            // Resize event is delivered. Updating the child frame and backing
            // drawable here avoids one AppKit layout/display-link turn at the
            // old size during maximize and live resize.
            [metalView_ layoutSubtreeIfNeeded];
            [metalView_ syncDrawableGeometry];
        };
        if (NSThread.isMainThread) {
            sync();
        } else {
            dispatch_sync(dispatch_get_main_queue(), sync);
        }
        requestDisplay();
    }

    int viewGeometry(
            double *parentWidth,
            double *viewWidth,
            double *drawableWidth) {
        if (!parentWidth || !viewWidth || !drawableWidth) {
            return 0;
        }
        __block double parent = 0.0;
        __block double view = 0.0;
        __block double drawable = 0.0;
        auto read = ^{
            if (parentView_ && metalView_ && metalLayer_) {
                parent = parentView_.bounds.size.width;
                view = metalView_.frame.size.width;
                drawable = metalLayer_.drawableSize.width;
            }
        };
        if (NSThread.isMainThread) {
            read();
        } else {
            dispatch_sync(dispatch_get_main_queue(), read);
        }
        *parentWidth = parent;
        *viewWidth = view;
        *drawableWidth = drawable;
        return parent > 0.0 && view > 0.0 && drawable > 0.0;
    }

    double pixelsPerSecond() const {
        return zoomPixelsPerSecond_.load(std::memory_order_acquire);
    }

    int snapshot(SBDeckSnapshot *out) {
        if (!out) {
            return 0;
        }
        std::memset(out, 0, sizeof(*out));
        out->abi_version = SB_DECK_ABI_VERSION;
        if (playing_.load(std::memory_order_acquire)) {
            out->playback_state = SB_DECK_PLAYING;
        } else if (loaded_.load(std::memory_order_acquire)) {
            out->playback_state = SB_DECK_PAUSED;
        } else {
            out->playback_state = SB_DECK_STOPPED;
        }
        out->position_frames = positionFrames_.load(std::memory_order_acquire);
        out->duration_frames = durationFrames_.load(std::memory_order_acquire);
        out->sample_rate = sampleRate_.load(std::memory_order_acquire);
        out->underruns = underruns_.load(std::memory_order_acquire);
        out->generation = generation_.load(std::memory_order_acquire);
        out->ramp_completed = rampCompleted_.load(std::memory_order_acquire);
        {
            std::lock_guard<std::mutex> lock(errorMutex_);
            out->error_code = errorCode_;
            std::strncpy(
                out->error_message, errorMessage_.c_str(),
                sizeof(out->error_message) - 1);
        }
        return 1;
    }

    uint64_t visualFrameNow() const {
        return static_cast<uint64_t>(
            std::max(0.0, predictedFrame(CACurrentMediaTime())));
    }

    uint64_t analysisSampleCount() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return waveSampleCount_;
    }

    double analysisSampleRate() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return waveSamplesPerSecond_;
    }

    uint64_t detailSampleCount() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return audioDetailSampleCount_
            ? audioDetailSampleCount_ : detailSampleCount_;
    }

    double detailSampleRate() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return audioDetailSampleCount_
            ? audioDetailSamplesPerSecond_ : detailSamplesPerSecond_;
    }

    double detailPeakHeight() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        id<MTLBuffer> buffer =
            audioDetailSampleCount_ ? audioDetailBuffer_ : detailBuffer_;
        const NSUInteger count =
            audioDetailSampleCount_ ? audioDetailSampleCount_ : detailSampleCount_;
        if (!buffer || !count) {
            return 0.0;
        }
        float peak = 0.0f;
        if (audioDetailSampleCount_) {
            const auto *samples =
                static_cast<const AudioEnvelopeSample *>(buffer.contents);
            for (NSUInteger i = 0; i < count; ++i) {
                peak = std::max(peak, samples[i].height / 255.0f);
            }
        } else {
            const auto *samples =
                static_cast<const WaveSample *>(buffer.contents);
            for (NSUInteger i = 0; i < count; ++i) {
                peak = std::max(peak, samples[i].height);
            }
        }
        return peak;
    }

    uint64_t audioDetailMipCount() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return audioDetailMipCount_;
    }

    uint64_t audioDetailBufferBytes() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return audioDetailBuffer_ ? audioDetailBuffer_.length : 0;
    }

    uint64_t cueMarkerCount() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return cueMarkerCount_;
    }

    uint64_t displayCallbackCount() const {
        return displayCallbackCount_.load(std::memory_order_acquire);
    }

    double displayAverageFps() const {
        const uint64_t count =
            displayCallbackCount_.load(std::memory_order_acquire);
        const uint64_t first =
            displayFirstTargetNs_.load(std::memory_order_acquire);
        const uint64_t last =
            displayLastTargetNs_.load(std::memory_order_acquire);
        return count > 1 && last > first
            ? (count - 1) * 1e9 / static_cast<double>(last - first)
            : 0.0;
    }

    double displayMaxGapMs() const {
        return displayMaxGapNs_.load(std::memory_order_acquire) / 1e6;
    }

    void resetDisplayStats() {
        displayCallbackCount_.store(0, std::memory_order_release);
        displayFirstTargetNs_.store(0, std::memory_order_release);
        displayLastTargetNs_.store(0, std::memory_order_release);
        displayMaxGapNs_.store(0, std::memory_order_release);
    }

    bool setAnalysis(
        const float *heights,
        const uint8_t *rgb,
        size_t count,
        double columnsPerSecond,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount,
        uint64_t expectedGeneration = 0) {
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        if (!heights || !rgb || !count || columnsPerSecond <= 0) {
            std::lock_guard<std::mutex> lock(renderMutex_);
            waveBuffer_ = nil;
            detailBuffer_ = nil;
            audioDetailBuffer_ = nil;
            overviewBuffer_ = nil;
            waveSampleCount_ = 0;
            detailSampleCount_ = 0;
            audioDetailSampleCount_ = 0;
            overviewSampleCount_ = 0;
            waveSamplesPerSecond_ = 0;
            detailSamplesPerSecond_ = 0;
            audioDetailSamplesPerSecond_ = 0;
            audioDetailMipCount_ = 0;
            audioDetailMipOffsets_.fill(0);
            audioDetailMipCounts_.fill(0);
            gridBuffer_ = nil;
            gridVertexCount_ = 0;
            cueBuffer_ = nil;
            cueVertexCount_ = 0;
            cueMarkerCount_ = 0;
            beatPositions_.clear();
            return false;
        }
        std::vector<WaveSample> wave;
        wave.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            const float h = std::clamp(heights[i], 0.0f, 1.0f);
            const float r = rgb[i * 3] / 255.0f;
            const float g = rgb[i * 3 + 1] / 255.0f;
            const float b = rgb[i * 3 + 2] / 255.0f;
            wave.push_back({h, r, g, b});
        }
        return installAnalysis(
            std::move(wave), columnsPerSecond, beatSeconds, beatInBar,
            beatCount, expectedGeneration);
    }

    bool installAnalysis(
        std::vector<WaveSample> wave,
        double columnsPerSecond,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount,
        uint64_t expectedGeneration) {
        const size_t count = wave.size();
        std::vector<WaveSample> overview;
        const size_t overviewCount = std::min(count, kOverviewColumns);
        overview.reserve(overviewCount);
        for (size_t column = 0; column < overviewCount; ++column) {
            const size_t start = column * count / overviewCount;
            const size_t end = std::min(
                count, std::max(start + 1, (column + 1) * count / overviewCount));
            const auto peak = std::max_element(
                wave.begin() + start, wave.begin() + end,
                [](const WaveSample &a, const WaveSample &b) {
                    return a.height < b.height;
                });
            overview.push_back(*peak);
        }

        std::vector<GridVertex> grid;
        grid.reserve(beatCount * 6);
        std::vector<BeatPosition> beatPositions;
        beatPositions.reserve(beatCount);
        uint32_t bar = 0;
        for (size_t i = 0; i < beatCount; ++i) {
            uint8_t beat = beatInBar ? beatInBar[i]
                                     : static_cast<uint8_t>(i % 4 + 1);
            if (beat < 1 || beat > 4) {
                beat = static_cast<uint8_t>(i % 4 + 1);
            }
            const bool downbeat = beat == 1;
            if (downbeat) {
                ++bar;
            } else if (bar == 0) {
                // A truncated imported grid can begin mid-bar. Give that
                // partial opening bar a useful number without rewriting the
                // source grid.
                bar = 1;
            }
            const double t = beatSeconds[i];
            const uint32_t tMillis = static_cast<uint32_t>(std::clamp(
                std::llround(t * 1000.0),
                0LL,
                static_cast<long long>(
                    std::numeric_limits<uint32_t>::max())));
            beatPositions.push_back({t, bar, beat});
            const float r = downbeat ? 1.0f : 0.47f;
            const float g = downbeat ? 0.22f : 0.53f;
            const float b = downbeat ? 0.34f : 0.62f;
            const float a = downbeat ? 0.82f : 0.32f;
            addGridRect(grid, tMillis, 0.0f, -0.5f, kZoomTop,
                        0.5f, 4096.0f, r, g, b, a);
        }

        id<MTLBuffer> waveBuffer = [device_ newBufferWithBytes:wave.data()
            length:wave.size() * sizeof(WaveSample)
            options:MTLResourceStorageModeShared];
        id<MTLBuffer> overviewBuffer =
            [device_ newBufferWithBytes:overview.data()
                length:overview.size() * sizeof(WaveSample)
                options:MTLResourceStorageModeShared];
        id<MTLBuffer> gridBuffer = grid.empty() ? nil
            : [device_ newBufferWithBytes:grid.data()
                length:grid.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!waveBuffer || !overviewBuffer) {
            setError(-40, "Metal waveform buffer allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        waveBuffer_ = waveBuffer;
        detailBuffer_ = waveBuffer;
        audioDetailBuffer_ = nil;
        overviewBuffer_ = overviewBuffer;
        waveSampleCount_ = wave.size();
        detailSampleCount_ = wave.size();
        audioDetailSampleCount_ = 0;
        overviewSampleCount_ = overview.size();
        waveSamplesPerSecond_ = static_cast<float>(columnsPerSecond);
        detailSamplesPerSecond_ = static_cast<float>(columnsPerSecond);
        audioDetailSamplesPerSecond_ = 0.0f;
        audioDetailMipCount_ = 0;
        audioDetailMipOffsets_.fill(0);
        audioDetailMipCounts_.fill(0);
        gridBuffer_ = gridBuffer;
        gridVertexCount_ = grid.size();
        cueBuffer_ = nil;
        cueVertexCount_ = 0;
        cueMarkerCount_ = 0;
        beatPositions_ = std::move(beatPositions);
        return true;
    }

    bool setPackedAnalysis(
        uint64_t expectedGeneration,
        const uint8_t *heights,
        const uint8_t *rgb,
        size_t count,
        double columnsPerSecond,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount) {
        if (!heights || !rgb || !count) {
            return false;
        }
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        std::vector<WaveSample> wave;
        wave.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            int values[3] = {
                std::min(255, static_cast<int>(rgb[i * 3]) * 18),
                std::min(255, static_cast<int>(rgb[i * 3 + 1]) * 36),
                std::min(255, static_cast<int>(rgb[i * 3 + 2]) * 36),
            };
            const int brightest = std::max({values[0], values[1], values[2]});
            if (brightest > 0 && brightest < 155) {
                const double boost = 155.0 / brightest;
                for (int channel = 0; channel < 3; ++channel) {
                    values[channel] = std::min(
                        255, static_cast<int>(values[channel] * boost));
                }
            }
            wave.push_back({
                heights[i] / 255.0f,
                values[0] / 255.0f,
                values[1] / 255.0f,
                values[2] / 255.0f,
            });
        }
        return installAnalysis(
            std::move(wave), columnsPerSecond, beatSeconds, beatInBar,
            beatCount, expectedGeneration);
    }

    bool setPackedOverview(
        uint64_t expectedGeneration,
        const uint8_t *heights,
        const uint8_t *rgb,
        size_t count,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount) {
        if (!heights || !rgb || !count
                || (expectedGeneration
                    && renderGeneration_.load(std::memory_order_acquire)
                        != expectedGeneration)) {
            return false;
        }
        const size_t overviewCount = std::min(count, kOverviewColumns);
        std::vector<WaveSample> overview;
        overview.reserve(overviewCount);
        for (size_t column = 0; column < overviewCount; ++column) {
            const size_t start = column * count / overviewCount;
            const size_t end = std::min(
                count, std::max(start + 1, (column + 1) * count / overviewCount));
            size_t peak = start;
            for (size_t i = start + 1; i < end; ++i) {
                if (heights[i] > heights[peak]) peak = i;
            }
            int values[3] = {
                std::min(255, static_cast<int>(rgb[peak * 3]) * 18),
                std::min(255, static_cast<int>(rgb[peak * 3 + 1]) * 36),
                std::min(255, static_cast<int>(rgb[peak * 3 + 2]) * 36),
            };
            const int brightest = std::max({values[0], values[1], values[2]});
            if (brightest > 0 && brightest < 155) {
                const double boost = 155.0 / brightest;
                for (int channel = 0; channel < 3; ++channel) {
                    values[channel] = std::min(
                        255, static_cast<int>(values[channel] * boost));
                }
            }
            overview.push_back({
                heights[peak] / 255.0f,
                values[0] / 255.0f,
                values[1] / 255.0f,
                values[2] / 255.0f,
            });
        }
        std::vector<GridVertex> grid;
        std::vector<BeatPosition> beatPositions;
        grid.reserve(beatCount * 6);
        beatPositions.reserve(beatCount);
        uint32_t bar = 0;
        for (size_t i = 0; i < beatCount; ++i) {
            uint8_t beat = beatInBar ? beatInBar[i]
                                     : static_cast<uint8_t>(i % 4 + 1);
            if (beat < 1 || beat > 4) beat = static_cast<uint8_t>(i % 4 + 1);
            const bool downbeat = beat == 1;
            if (downbeat) ++bar;
            else if (bar == 0) bar = 1;
            const double t = beatSeconds[i];
            const uint32_t millis = static_cast<uint32_t>(std::clamp(
                std::llround(t * 1000.0), 0LL,
                static_cast<long long>(
                    std::numeric_limits<uint32_t>::max())));
            beatPositions.push_back({t, bar, beat});
            addGridRect(
                grid, millis, 0.0f, -0.5f, kZoomTop, 0.5f, 4096.0f,
                downbeat ? 1.0f : 0.47f,
                downbeat ? 0.22f : 0.53f,
                downbeat ? 0.34f : 0.62f,
                downbeat ? 0.82f : 0.32f);
        }
        id<MTLBuffer> overviewBuffer =
            [device_ newBufferWithBytes:overview.data()
                length:overview.size() * sizeof(WaveSample)
                options:MTLResourceStorageModeShared];
        id<MTLBuffer> gridBuffer = grid.empty() ? nil
            : [device_ newBufferWithBytes:grid.data()
                length:grid.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!overviewBuffer) {
            setError(-40, "Metal overview buffer allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        overviewBuffer_ = overviewBuffer;
        overviewSampleCount_ = overview.size();
        gridBuffer_ = gridBuffer;
        gridVertexCount_ = grid.size();
        beatPositions_ = std::move(beatPositions);
        return true;
    }

    bool setSessionCue(uint32_t positionMillis, bool active) {
        std::vector<GridVertex> marker;
        if (active) {
            constexpr float r = 0.941f;
            constexpr float g = 0.541f;
            constexpr float b = 0.098f;
            // One transient upward orange arrow in both scales. It remains
            // separate from imported hot/memory cue storage and deliberately
            // has no vertical stem through either waveform.
            addGridTriangle(
                marker, positionMillis, 0.0f,
                0.0f, kOverviewHeight - 10.0f,
                -5.5f, kOverviewHeight,
                5.5f, kOverviewHeight,
                r, g, b, 1.0f, true);
            addGridTriangle(
                marker, positionMillis, 0.0f,
                0.0f, -10.0f,
                -5.5f, -0.01f,
                5.5f, -0.01f,
                r, g, b, 1.0f);
        }
        id<MTLBuffer> buffer = marker.empty() ? nil
            : [device_ newBufferWithBytes:marker.data()
                length:marker.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!marker.empty() && !buffer) {
            setError(-45, "Metal session-cue buffer allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        sessionCueBuffer_ = buffer;
        sessionCueVertexCount_ = marker.size();
        return true;
    }

    bool setCues(
        const uint32_t *positions,
        const uint8_t *kinds,
        const uint8_t *slots,
        const uint8_t *rgb,
        size_t count) {
        if (!positions || !kinds || !slots || !rgb || !count) {
            std::lock_guard<std::mutex> lock(renderMutex_);
            cueBuffer_ = nil;
            cueVertexCount_ = 0;
            cueMarkerCount_ = 0;
            return true;
        }
        std::vector<GridVertex> markers;
        markers.reserve(count * 420);
        size_t markerCount = 0;
        for (size_t i = 0; i < count; ++i) {
            const uint32_t t = positions[i];
            const bool hot = kinds[i] == 1;
            const uint8_t slot = slots[i];
            if (hot && slot > 7) {
                continue;
            }
            const float r = hot ? rgb[i * 3] / 255.0f : 1.0f;
            const float g = hot ? rgb[i * 3 + 1] / 255.0f : 0.204f;
            const float b = hot ? rgb[i * 3 + 2] / 255.0f : 0.204f;
            if (hot) {
                // Whole-track marker: cue-colored identity, black one-point
                // box, and a song-absolute stem into the mini waveform.
                addGridRect(markers, t, 0.0f, -0.5f, 15.0f,
                            0.5f, kOverviewHeight - 1.0f,
                            r, g, b, 1.0f, true);
                addGridRect(markers, t, 0.0f, -9.0f, 0.0f,
                            9.0f, 15.0f, 0.02f, 0.024f, 0.031f, 1.0f, true);
                addGridRect(markers, t, 0.0f, -8.0f, 1.0f,
                            8.0f, 14.0f, r, g, b, 1.0f, true);
                addCueLetter(
                    markers, t, slot, -2.5f, 3.5f,
                    0.02f, 0.024f, 0.031f, 1.0f, true);

                // Scrolling marker: the same identity box plus an exact cue
                // line through the main waveform and beat grid.
                addGridRect(markers, t, 0.0f, -0.5f, kZoomTop + 17.0f,
                            0.5f, 4096.0f, r, g, b, 1.0f);
                addGridRect(markers, t, 0.0f, -11.0f, kZoomTop,
                            11.0f, kZoomTop + 17.0f,
                            0.02f, 0.024f, 0.031f, 1.0f);
                addGridRect(markers, t, 0.0f, -10.0f, kZoomTop + 1.0f,
                            10.0f, kZoomTop + 16.0f, r, g, b, 1.0f);
                addCueLetter(
                    markers, t, slot, -2.5f, kZoomTop + 5.0f,
                    0.02f, 0.024f, 0.031f, 1.0f, false);
            } else {
                // Memory cues intentionally share one red visual identity.
                addGridTriangle(
                    markers, t, 0.0f,
                    0.0f, kOverviewHeight - 11.0f,
                    -6.0f, kOverviewHeight,
                    6.0f, kOverviewHeight,
                    r, g, b, 1.0f, true);
                // Negative y coordinates are resolved from the bottom by the
                // shader. Keep the base effectively on the final pixel edge
                // so a coincident red downbeat cannot peek out underneath.
                addGridTriangle(
                    markers, t, 0.0f,
                    0.0f, -11.0f, -6.0f, -0.01f, 6.0f, -0.01f,
                    r, g, b, 1.0f, false);
            }
            ++markerCount;
        }
        id<MTLBuffer> buffer = markers.empty() ? nil
            : [device_ newBufferWithBytes:markers.data()
                length:markers.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!markers.empty() && !buffer) {
            setError(-44, "Metal cue-marker buffer allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        cueBuffer_ = buffer;
        cueVertexCount_ = markers.size();
        cueMarkerCount_ = markerCount;
        return true;
    }

    bool setDetailAnalysis(
        const float *heights,
        const uint8_t *rgb,
        size_t count,
        double samplesPerSecond) {
        if (!heights || !rgb || !count || samplesPerSecond <= 0) {
            return false;
        }
        std::vector<WaveSample> detail;
        detail.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            detail.push_back({
                std::clamp(heights[i], 0.0f, 1.0f),
                rgb[i * 3] / 255.0f,
                rgb[i * 3 + 1] / 255.0f,
                rgb[i * 3 + 2] / 255.0f,
            });
        }
        id<MTLBuffer> buffer = [device_ newBufferWithBytes:detail.data()
            length:detail.size() * sizeof(WaveSample)
            options:MTLResourceStorageModeShared];
        if (!buffer) {
            setError(-41, "Metal detail waveform buffer allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        detailBuffer_ = buffer;
        detailSampleCount_ = detail.size();
        detailSamplesPerSecond_ = static_cast<float>(samplesPerSecond);
        return true;
    }

    bool setDetailBands(const uint8_t *bands, size_t count) {
        if (!bands || !count) {
            return false;
        }
        std::vector<WaveSample> detail;
        {
            std::lock_guard<std::mutex> lock(renderMutex_);
            if (!detailBuffer_ || detailSampleCount_ != count) {
                return false;
            }
            const auto *source = static_cast<const WaveSample *>(
                detailBuffer_.contents);
            detail.assign(source, source + count);
        }
        for (size_t i = 0; i < count; ++i) {
            const uint8_t energy = std::max({
                static_cast<uint8_t>(bands[i * 3] & 0x7f),
                static_cast<uint8_t>(bands[i * 3 + 1] & 0x7f),
                static_cast<uint8_t>(bands[i * 3 + 2] & 0x7f),
            });
            detail[i].height = energy / 127.0f;
        }
        id<MTLBuffer> buffer = [device_ newBufferWithBytes:detail.data()
            length:detail.size() * sizeof(WaveSample)
            options:MTLResourceStorageModeShared];
        if (!buffer) {
            setError(-42, "Metal three-band waveform allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        detailBuffer_ = buffer;
        return true;
    }

    bool setSpectralAnalysis(
        const uint8_t *,
        size_t,
        size_t,
        double) {
        // Kept as a no-op ABI slot for older Python adapters. The normalized
        // 24-band master is persisted analysis, not a display-color source:
        // remapping equal band thirds here destroys the dedicated RGB
        // filterbank's true local energy relationship.
        return false;
    }

    bool setAudioDetail(
        const uint8_t *heights, size_t count, double samplesPerSecond,
        uint64_t expectedGeneration = 0) {
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        if (!heights || !count || samplesPerSecond <= 0) {
            return false;
        }
        std::vector<AudioEnvelopeSample> detail;
        detail.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            detail.push_back({heights[i]});
        }
        // A single peak-preserving silhouette keeps the old SoundBox/Rekordbox
        // visual language while the V4 ultra stream supplies close-zoom detail.
        // Mips are derived at load time, so this renderer change needs no recook.
        std::array<uint32_t, kAudioDetailMipLevels> mipOffsets{};
        std::array<uint32_t, kAudioDetailMipLevels> mipCounts{};
        std::vector<AudioEnvelopeSample> pyramid = detail;
        mipCounts[0] = static_cast<uint32_t>(detail.size());
        size_t mipCount = 1;
        std::vector<AudioEnvelopeSample> level = std::move(detail);
        while (mipCount < kAudioDetailMipLevels && level.size() > 1) {
            std::vector<AudioEnvelopeSample> next;
            next.reserve((level.size() + 1) / 2);
            for (size_t i = 0; i < level.size(); i += 2) {
                const AudioEnvelopeSample &a = level[i];
                const bool hasB = i + 1 < level.size();
                const AudioEnvelopeSample &b = hasB ? level[i + 1] : a;
                next.push_back({std::max(a.height, b.height)});
            }
            mipOffsets[mipCount] = static_cast<uint32_t>(pyramid.size());
            mipCounts[mipCount] = static_cast<uint32_t>(next.size());
            pyramid.insert(pyramid.end(), next.begin(), next.end());
            level = std::move(next);
            ++mipCount;
        }
        id<MTLBuffer> buffer = [device_ newBufferWithBytes:pyramid.data()
            length:pyramid.size() * sizeof(AudioEnvelopeSample)
            options:MTLResourceStorageModeShared];
        if (!buffer) {
            setError(-43, "Metal audio-detail waveform allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        audioDetailBuffer_ = buffer;
        audioDetailSampleCount_ = count;
        audioDetailSamplesPerSecond_ = static_cast<float>(samplesPerSecond);
        audioDetailMipCount_ = mipCount;
        audioDetailMipOffsets_ = mipOffsets;
        audioDetailMipCounts_ = mipCounts;
        return true;
    }

    void draw(CAMetalDisplayLinkUpdate *update) {
        if (!pipeline_ || !update.drawable) {
            return;
        }
        const bool continuous =
            playRequested_.load(std::memory_order_acquire)
            || previewActive_.load(std::memory_order_acquire);
        if (!continuous
                && !displayDirty_.exchange(false, std::memory_order_acq_rel)) {
            displayLink_.paused = YES;
            return;
        }
        const uint64_t targetNs = static_cast<uint64_t>(
            std::llround(update.targetPresentationTimestamp * 1e9));
        uint64_t expectedFirst = 0;
        displayFirstTargetNs_.compare_exchange_strong(
            expectedFirst, targetNs, std::memory_order_acq_rel);
        const uint64_t previous = displayLastTargetNs_.exchange(
            targetNs, std::memory_order_acq_rel);
        if (previous && targetNs > previous) {
            const uint64_t gap = targetNs - previous;
            uint64_t current = displayMaxGapNs_.load(
                std::memory_order_acquire);
            while (gap > current
                    && !displayMaxGapNs_.compare_exchange_weak(
                        current, gap, std::memory_order_acq_rel)) {
            }
        }
        displayCallbackCount_.fetch_add(1, std::memory_order_acq_rel);
        const CGSize size = metalLayer_.bounds.size;
        if (size.width <= 1 || size.height <= 1) {
            return;
        }
        id<MTLCommandBuffer> commands = [commandQueue_ commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = update.drawable.texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(
            13.0 / 255.0, 14.0 / 255.0, 18.0 / 255.0, 1.0);
        id<MTLRenderCommandEncoder> encoder =
            [commands renderCommandEncoderWithDescriptor:pass];

        const double exactFrame = previewActive_.load(std::memory_order_acquire)
            ? static_cast<double>(
                previewFrame_.load(std::memory_order_acquire))
            : predictedFrame(update.targetPresentationTimestamp);
        const double rate = std::max(1.0, sampleRate_.load(std::memory_order_acquire));
        const float drawableScale = static_cast<float>(
            update.drawable.texture.width / std::max(1.0, size.width));
        Uniforms uniforms{};
        const double currentSeconds = exactFrame / rate;
        const double currentMillis = currentSeconds * 1000.0;
        const double currentMillisWhole = std::floor(
            std::max(0.0, currentMillis));
        uniforms.currentSeconds = static_cast<float>(currentSeconds);
        uniforms.currentMillis = static_cast<uint32_t>(std::min(
            currentMillisWhole,
            static_cast<double>(std::numeric_limits<uint32_t>::max())));
        uniforms.currentMillisPhase = static_cast<float>(
            currentMillis - currentMillisWhole);
        uniforms.pixelsPerSecond =
            zoomPixelsPerSecond_.load(std::memory_order_acquire);
        uniforms.widthPoints = static_cast<float>(size.width);
        uniforms.heightPoints = static_cast<float>(size.height);
        uniforms.drawableScale = drawableScale;
        uniforms.durationSeconds = static_cast<float>(
            durationFrames_.load(std::memory_order_acquire) / rate);
        BeatPosition currentBeat{};
        bool hasCurrentBeat = false;
        {
            std::lock_guard<std::mutex> lock(renderMutex_);
            uniforms.samplesPerSecond = waveSamplesPerSecond_;
            uniforms.sampleCount = static_cast<uint32_t>(
                std::min<NSUInteger>(
                    waveSampleCount_, std::numeric_limits<uint32_t>::max()));
            uniforms.overviewSampleCount = static_cast<uint32_t>(
                std::min<NSUInteger>(
                    overviewSampleCount_,
                    std::numeric_limits<uint32_t>::max()));
            uniforms.detailSamplesPerSecond = detailSamplesPerSecond_;
            uniforms.detailSampleCount = static_cast<uint32_t>(
                std::min<NSUInteger>(
                    detailSampleCount_,
                    std::numeric_limits<uint32_t>::max()));
            uniforms.audioDetailSamplesPerSecond =
                audioDetailSamplesPerSecond_;
            uniforms.audioDetailSampleCount = static_cast<uint32_t>(
                std::min<NSUInteger>(
                    audioDetailSampleCount_,
                    std::numeric_limits<uint32_t>::max()));
            const double audioDetailPosition =
                exactFrame
                * static_cast<double>(audioDetailSamplesPerSecond_)
                / rate;
            const double audioDetailWhole = std::floor(
                std::max(0.0, audioDetailPosition));
            uniforms.audioDetailCurrentIndex = static_cast<uint32_t>(
                std::min(
                    audioDetailWhole,
                    static_cast<double>(
                        std::numeric_limits<uint32_t>::max())));
            uniforms.audioDetailCurrentPhase = static_cast<float>(
                audioDetailPosition - audioDetailWhole);
            uniforms.audioDetailMipCount = static_cast<uint32_t>(
                audioDetailMipCount_);
            std::copy(
                audioDetailMipOffsets_.begin(),
                audioDetailMipOffsets_.end(),
                uniforms.audioDetailMipOffsets);
            std::copy(
                audioDetailMipCounts_.begin(),
                audioDetailMipCounts_.end(),
                uniforms.audioDetailMipCounts);
            if (waveBuffer_ && waveSampleCount_) {
                [encoder setRenderPipelineState:pipeline_];
                [encoder setFragmentBuffer:waveBuffer_ offset:0 atIndex:0];
                [encoder setFragmentBuffer:overviewBuffer_ offset:0 atIndex:2];
                [encoder setFragmentBuffer:detailBuffer_ offset:0 atIndex:3];
                [encoder setFragmentBuffer:
                    (audioDetailBuffer_ ? audioDetailBuffer_ : detailBuffer_)
                    offset:0 atIndex:4];
                [encoder setFragmentBytes:&uniforms
                                   length:sizeof(uniforms)
                                  atIndex:1];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                            vertexStart:0 vertexCount:3];
            }
            if (gridBuffer_ && gridVertexCount_) {
                [encoder setRenderPipelineState:gridPipeline_];
                [encoder setVertexBuffer:gridBuffer_ offset:0 atIndex:0];
                [encoder setVertexBytes:&uniforms
                                 length:sizeof(uniforms)
                                atIndex:1];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                            vertexStart:0 vertexCount:gridVertexCount_];
            }
            if (sessionCueBuffer_ && sessionCueVertexCount_) {
                [encoder setRenderPipelineState:gridPipeline_];
                [encoder setVertexBuffer:sessionCueBuffer_ offset:0 atIndex:0];
                [encoder setVertexBytes:&uniforms
                                 length:sizeof(uniforms)
                                atIndex:1];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                            vertexStart:0
                            vertexCount:sessionCueVertexCount_];
            }
            if (cueBuffer_ && cueVertexCount_) {
                [encoder setRenderPipelineState:gridPipeline_];
                [encoder setVertexBuffer:cueBuffer_ offset:0 atIndex:0];
                [encoder setVertexBytes:&uniforms
                                 length:sizeof(uniforms)
                                atIndex:1];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                            vertexStart:0 vertexCount:cueVertexCount_];
            }
            auto beat = std::upper_bound(
                beatPositions_.begin(), beatPositions_.end(),
                currentSeconds,
                [](double seconds, const BeatPosition &position) {
                    return seconds < position.seconds;
                });
            if (beat != beatPositions_.begin()) {
                --beat;
                currentBeat = *beat;
                hasCurrentBeat = true;
            }
        }
        [encoder setVertexBytes:&uniforms
                         length:sizeof(uniforms)
                        atIndex:1];
        drawPlayhead(
            encoder, uniforms, hasCurrentBeat ? &currentBeat : nullptr);
        [encoder endEncoding];
        // CAMetalDisplayLink already owns the presentation schedule for this
        // drawable; an explicit presentAtTime is invalid in this mode.
        [commands presentDrawable:update.drawable];
        [commands commit];
        if (!continuous
                && !displayDirty_.load(std::memory_order_acquire)) {
            displayLink_.paused = YES;
        }
    }

private:
    static void addGridRect(
        std::vector<GridVertex> &v, uint32_t tMillis, float tPhase,
        float x0, float y0, float x1, float y1,
        float r, float g, float b, float a, bool overview = false) {
        const uint32_t absolute = overview ? 1u : 0u;
        GridVertex p0{tMillis, tPhase, x0, y0, r, g, b, a, absolute};
        GridVertex p1{tMillis, tPhase, x1, y0, r, g, b, a, absolute};
        GridVertex p2{tMillis, tPhase, x1, y1, r, g, b, a, absolute};
        GridVertex p3{tMillis, tPhase, x0, y1, r, g, b, a, absolute};
        v.insert(v.end(), {p0, p1, p2, p0, p2, p3});
    }

    static void addGridTriangle(
        std::vector<GridVertex> &v, uint32_t tMillis, float tPhase,
        float x0, float y0, float x1, float y1, float x2, float y2,
        float r, float g, float b, float a, bool overview = false) {
        const uint32_t absolute = overview ? 1u : 0u;
        v.insert(v.end(), {
            {tMillis, tPhase, x0, y0, r, g, b, a, absolute},
            {tMillis, tPhase, x1, y1, r, g, b, a, absolute},
            {tMillis, tPhase, x2, y2, r, g, b, a, absolute},
        });
    }

    static void addCueLetter(
        std::vector<GridVertex> &v, uint32_t tMillis, uint8_t slot,
        float baseX, float baseY,
        float r, float g, float b, float a, bool overview) {
        static constexpr uint8_t glyphs[8][7] = {
            {0x0e,0x11,0x11,0x1f,0x11,0x11,0x11}, // A
            {0x1e,0x11,0x11,0x1e,0x11,0x11,0x1e}, // B
            {0x0f,0x10,0x10,0x10,0x10,0x10,0x0f}, // C
            {0x1e,0x11,0x11,0x11,0x11,0x11,0x1e}, // D
            {0x1f,0x10,0x10,0x1e,0x10,0x10,0x1f}, // E
            {0x1f,0x10,0x10,0x1e,0x10,0x10,0x10}, // F
            {0x0f,0x10,0x10,0x17,0x11,0x11,0x0f}, // G
            {0x11,0x11,0x11,0x1f,0x11,0x11,0x11}, // H
        };
        const uint8_t glyph = std::min<uint8_t>(slot, 7);
        for (uint8_t row = 0; row < 7; ++row) {
            for (uint8_t column = 0; column < 5; ++column) {
                if (!(glyphs[glyph][row] & (1u << (4 - column)))) {
                    continue;
                }
                addGridRect(
                    v, tMillis, 0.0f,
                    baseX + column, baseY + row,
                    baseX + column + 1.0f, baseY + row + 1.0f,
                    r, g, b, a, overview);
            }
        }
    }

    static void addGridText(
        std::vector<GridVertex> &v,
        uint32_t tMillis, float tPhase, const std::string &text,
        float baseX, float baseY,
        float r, float g, float b, float a) {
        static constexpr uint8_t masks[10] = {
            0x3f, 0x06, 0x5b, 0x4f, 0x66,
            0x6d, 0x7d, 0x07, 0x7f, 0x6f
        };
        for (char ch : text) {
            if (ch == '.') {
                addGridRect(
                    v, tMillis, tPhase,
                    baseX, baseY + 9, baseX + 2, baseY + 11,
                    r, g, b, a);
                baseX += 3.0f;
                continue;
            }
            if (ch < '0' || ch > '9') {
                continue;
            }
            const uint8_t mask = masks[ch - '0'];
            if (mask & 0x01) addGridRect(v, tMillis, tPhase, baseX + 1, baseY,
                baseX + 5, baseY + 1, r,g,b,a);
            if (mask & 0x02) addGridRect(v, tMillis, tPhase, baseX + 5, baseY + 1,
                baseX + 6, baseY + 5, r,g,b,a);
            if (mask & 0x04) addGridRect(v, tMillis, tPhase, baseX + 5, baseY + 6,
                baseX + 6, baseY + 10, r,g,b,a);
            if (mask & 0x08) addGridRect(v, tMillis, tPhase, baseX + 1, baseY + 10,
                baseX + 5, baseY + 11, r,g,b,a);
            if (mask & 0x10) addGridRect(v, tMillis, tPhase, baseX, baseY + 6,
                baseX + 1, baseY + 10, r,g,b,a);
            if (mask & 0x20) addGridRect(v, tMillis, tPhase, baseX, baseY + 1,
                baseX + 1, baseY + 5, r,g,b,a);
            if (mask & 0x40) addGridRect(v, tMillis, tPhase, baseX + 1, baseY + 5,
                baseX + 5, baseY + 6, r,g,b,a);
            baseX += 8.0f;
        }
    }

    void drawPlayhead(
        id<MTLRenderCommandEncoder> encoder, const Uniforms &uniforms,
        const BeatPosition *beat) {
        const uint32_t tMillis = uniforms.currentMillis;
        const float tPhase = uniforms.currentMillisPhase;
        std::vector<GridVertex> v;
        v.reserve(48);
        // Quiet dividers keep overview, ruler, and detail visually separate.
        addGridRect(
            v, tMillis, tPhase,
            -uniforms.widthPoints * 0.5f, kOverviewHeight,
            uniforms.widthPoints * 0.5f, kOverviewHeight + 1.0f,
            0.18f, 0.20f, 0.24f, 0.72f);
        addGridRect(
            v, tMillis, tPhase,
            -uniforms.widthPoints * 0.5f, kZoomTop - 1.0f,
            uniforms.widthPoints * 0.5f, kZoomTop,
            0.18f, 0.20f, 0.24f, 0.72f);
        if (uniforms.durationSeconds > 0.0f) {
            const float overviewX = std::clamp(
                uniforms.currentSeconds / uniforms.durationSeconds,
                0.0f, 1.0f) * uniforms.widthPoints;
            const float offset = overviewX - uniforms.widthPoints * 0.5f;
            addGridRect(
                v, tMillis, tPhase, offset - 0.6f, 1.0f,
                offset + 0.6f, kOverviewHeight - 1.0f,
                0.09f, 0.78f, 1.0f, 1.0f);
        }
        addGridRect(v, tMillis, tPhase, -0.6f, kZoomTop, 0.6f,
                    uniforms.heightPoints,
                    0.09f, 0.78f, 1.0f, 1.0f);
        GridVertex a{
            tMillis, tPhase, -5, kZoomTop, .09f,.78f,1,1,0};
        GridVertex b{
            tMillis, tPhase, 5, kZoomTop, .09f,.78f,1,1,0};
        GridVertex c{
            tMillis, tPhase, 0, kZoomTop + 7, .09f,.78f,1,1,0};
        v.insert(v.end(), {a,b,c});
        if (beat) {
            const std::string barText = std::to_string(beat->bar);
            const std::string label =
                barText + "." + std::to_string(beat->beat);
            const float labelX =
                -(static_cast<float>(barText.size()) * 8.0f + 1.0f);
            addGridText(
                v, tMillis, tPhase, label, labelX, kRulerTop + 3.0f,
                0.09f, 0.78f, 1.0f, 1.0f);
        }
        const auto addButton = [&](float x0, bool plus, bool enabled) {
            const float left = x0 - uniforms.widthPoints * 0.5f;
            const float top = kRulerTop + 1.0f;
            const float bottom = kZoomTop - 1.0f;
            const float dim = enabled ? 1.0f : 0.38f;
            addGridRect(v, tMillis, tPhase, left, top, left + 26, bottom,
                        .067f, .075f, .098f, .94f);
            addGridRect(v, tMillis, tPhase, left, top, left + 26, top + 1,
                        .32f, .35f, .40f, dim);
            addGridRect(v, tMillis, tPhase, left, bottom - 1, left + 26, bottom,
                        .32f, .35f, .40f, dim);
            addGridRect(v, tMillis, tPhase, left, top, left + 1, bottom,
                        .32f, .35f, .40f, dim);
            addGridRect(v, tMillis, tPhase, left + 25, top, left + 26, bottom,
                        .32f, .35f, .40f, dim);
            addGridRect(v, tMillis, tPhase,
                        left + 9, top + 7, left + 17, top + 8,
                        .82f, .84f, .88f, dim);
            if (plus) {
                addGridRect(v, tMillis, tPhase,
                            left + 12.5f, top + 3.5f,
                            left + 13.5f, top + 11.5f,
                            .82f, .84f, .88f, dim);
            }
        };
        addButton(0.0f, false, uniforms.pixelsPerSecond > 64.1f);
        addButton(30.0f, true, uniforms.pixelsPerSecond < 1535.9f);
        [encoder setRenderPipelineState:gridPipeline_];
        [encoder setVertexBytes:v.data() length:v.size() * sizeof(GridVertex) atIndex:0];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                    vertexStart:0 vertexCount:v.size()];
    }

    double predictedFrame(CFTimeInterval presentationTime) const {
        const double frame = static_cast<double>(
            positionFrames_.load(std::memory_order_acquire));
        if (!playing_.load(std::memory_order_acquire)) {
            return frame;
        }
        if (!visualClockValid_.load(std::memory_order_acquire)) {
            return frame;
        }
        uint64_t sequence = 0;
        uint64_t confirmed = 0;
        uint64_t host = 0;
        double anchor = frame;
        double clockRate = sampleRate_.load(std::memory_order_acquire);
        do {
            sequence =
                visualClockSequence_.load(std::memory_order_acquire);
            if (sequence & 1u) {
                continue;
            }
            host = visualAnchorHostTime_.load(std::memory_order_relaxed);
            anchor = visualAnchorFrame_.load(std::memory_order_relaxed);
            clockRate =
                visualEffectiveRate_.load(std::memory_order_relaxed);
            confirmed =
                visualClockSequence_.load(std::memory_order_acquire);
        } while (sequence != confirmed || (confirmed & 1u));
        mach_timebase_info_data_t info{};
        mach_timebase_info(&info);
        const double hostSeconds =
            host * static_cast<double>(info.numer) / info.denom / 1e9;
        // CoreAudio commonly timestamps a buffer for its future hardware
        // presentation time. Extrapolate backward from that exact point
        // instead of holding the image until the timestamp becomes "now."
        const double delta = presentationTime - hostSeconds;
        const double predicted = anchor + delta * clockRate;
        const double duration = static_cast<double>(
            durationFrames_.load(std::memory_order_acquire));
        return std::max(0.0, std::min(predicted, duration));
    }

    void updateVisualClock(uint64_t sourceFrame, uint64_t hostTime) {
        const uint64_t now = hostTime ? hostTime : mach_absolute_time();
        const double nominalRate =
            std::max(1.0, sampleRate_.load(std::memory_order_acquire));
        if (!visualClockValid_.load(std::memory_order_acquire)) {
            visualClockSequence_.fetch_add(1, std::memory_order_acq_rel);
            visualAnchorFrame_.store(
                static_cast<double>(sourceFrame), std::memory_order_relaxed);
            visualAnchorHostTime_.store(now, std::memory_order_relaxed);
            visualBaseRate_.store(nominalRate, std::memory_order_relaxed);
            visualEffectiveRate_.store(nominalRate, std::memory_order_relaxed);
            visualClockSequence_.fetch_add(1, std::memory_order_release);
            visualLastSourceFrame_.store(
                sourceFrame, std::memory_order_release);
            visualLastHostTime_.store(now, std::memory_order_release);
            visualClockValid_.store(true, std::memory_order_release);
            return;
        }
        const uint64_t anchorHost =
            visualAnchorHostTime_.load(std::memory_order_acquire);
        const double anchorFrame =
            visualAnchorFrame_.load(std::memory_order_acquire);
        const double effectiveRate =
            visualEffectiveRate_.load(std::memory_order_acquire);
        const uint64_t lastHost =
            visualLastHostTime_.load(std::memory_order_acquire);
        const uint64_t lastSource =
            visualLastSourceFrame_.load(std::memory_order_acquire);
        mach_timebase_info_data_t info{};
        mach_timebase_info(&info);
        const double secondsPerTick =
            static_cast<double>(info.numer) / info.denom / 1e9;
        const double anchorElapsed = (
            static_cast<double>(now)
            - static_cast<double>(anchorHost)) * secondsPerTick;
        const double expected =
            anchorFrame + anchorElapsed * effectiveRate;
        const double error = static_cast<double>(sourceFrame) - expected;
        const double observationSeconds =
            now > lastHost ? (now - lastHost) * secondsPerTick : 0.0;
        double baseRate =
            visualBaseRate_.load(std::memory_order_acquire);
        if (observationSeconds > 0.0 && sourceFrame >= lastSource) {
            const double observedRate =
                (sourceFrame - lastSource) / observationSeconds;
            // CoreAudio's presentation timestamps expose the output device's
            // real clock. Follow that clock with a half-second low-pass so
            // scheduling noise cannot modulate the visual velocity.
            const double boundedRate = std::clamp(
                observedRate, nominalRate * 0.995, nominalRate * 1.005);
            const double alpha =
                1.0 - std::exp(-observationSeconds / 0.5);
            baseRate += (boundedRate - baseRate) * alpha;
        }
        // Remove phase error by an inaudibly small velocity bias rather than
        // changing position at an audio callback boundary. The old hard
        // correction could jump almost half a Retina pixel at maximum zoom.
        const double phaseRate = std::clamp(
            error * 3.0, -nominalRate * 0.00075,
            nominalRate * 0.00075);
        const double nextRate = baseRate + phaseRate;

        // Rebase at the already-predicted position: position is continuous
        // while the PLL changes only its slope. A sequence lock lets the
        // display thread consume the four clock values as one coherent state
        // without placing a mutex on CoreAudio's real-time callback.
        visualClockSequence_.fetch_add(1, std::memory_order_acq_rel);
        visualAnchorFrame_.store(expected, std::memory_order_relaxed);
        visualAnchorHostTime_.store(now, std::memory_order_relaxed);
        visualBaseRate_.store(baseRate, std::memory_order_relaxed);
        visualEffectiveRate_.store(nextRate, std::memory_order_relaxed);
        visualClockSequence_.fetch_add(1, std::memory_order_release);
        visualLastSourceFrame_.store(sourceFrame, std::memory_order_release);
        visualLastHostTime_.store(now, std::memory_order_release);
    }

    void createMetalView() {
        auto create = ^{
            metalView_ = [[SBMetalView alloc] initWithFrame:parentView_.bounds];
            metalLayer_ = metalView_.deckLayer;
            metalLayer_.pixelFormat = MTLPixelFormatBGRA8Unorm;
            metalLayer_.framebufferOnly = YES;
            metalLayer_.maximumDrawableCount = 3;
            [parentView_ addSubview:metalView_ positioned:NSWindowAbove relativeTo:nil];
            [metalView_ layoutSubtreeIfNeeded];
            device_ = MTLCreateSystemDefaultDevice();
            metalLayer_.device = device_;
            commandQueue_ = [device_ newCommandQueue];
            buildPipelines();
        };
        if (NSThread.isMainThread) {
            create();
        } else {
            dispatch_sync(dispatch_get_main_queue(), create);
        }
    }

    void removeMetalView() {
        auto remove = ^{
            [metalView_ removeFromSuperview];
            metalView_ = nil;
            metalLayer_ = nil;
        };
        if (NSThread.isMainThread) {
            remove();
        } else {
            dispatch_sync(dispatch_get_main_queue(), remove);
        }
    }

    void buildPipelines() {
        static NSString *source = @R"METAL(
#include <metal_stdlib>
using namespace metal;
struct WaveSample { float height; float r; float g; float b; };
struct AudioEnvelopeSample { uchar height; };
struct GridVertex { uint songMillis; float songMillisPhase;
                    float xOffsetPixels; float yPixels;
                    float r; float g; float b; float a;
                    uint overview; };
struct Uniforms { float currentSeconds;
                  uint currentMillis; float currentMillisPhase;
                  float pixelsPerSecond;
                  float widthPoints; float heightPoints;
                  float samplesPerSecond; uint sampleCount;
                  float drawableScale; float durationSeconds;
                  uint overviewSampleCount;
                  float detailSamplesPerSecond; uint detailSampleCount;
                  float audioDetailSamplesPerSecond;
                  uint audioDetailSampleCount;
                  uint audioDetailCurrentIndex;
                  float audioDetailCurrentPhase;
                  uint audioDetailMipCount;
                  uint audioDetailMipOffsets[8];
                  uint audioDetailMipCounts[8]; };
struct Out { float4 position [[position]]; float4 color; };
struct FilteredWave { float coverage; float3 premultiplied; };
struct FilteredEnvelope { float coverage; };
inline float vivid_wave_coverage(float coverage) {
    // Preserve the exact silhouette mask while preventing a one-physical-pixel
    // quiet passage from becoming a muddy blend with the dark deck background.
    // This changes chromatic visibility only; no sample height is expanded.
    return coverage > 0.0 ? max(coverage, 0.72) : 0.0;
}
vertex Out wave_surface_vertex(uint id [[vertex_id]]) {
    const float2 positions[3] = {
        float2(-1.0, -1.0), float2(3.0, -1.0), float2(-1.0, 3.0)
    };
    Out o;
    o.position = float4(positions[id], 0.0, 1.0);
    o.color = float4(0.0);
    return o;
}
inline FilteredWave filter_wave_pair(
        WaveSample a, WaveSample b, float phase,
        float distance, float heightScale, float antialias) {
    const float coverageA = 1.0 - smoothstep(
        a.height * heightScale - antialias,
        a.height * heightScale + antialias, distance);
    const float coverageB = 1.0 - smoothstep(
        b.height * heightScale - antialias,
        b.height * heightScale + antialias, distance);
    FilteredWave result;
    result.coverage = mix(coverageA, coverageB, phase);
    result.premultiplied = mix(
        float3(a.r, a.g, a.b) * coverageA,
        float3(b.r, b.g, b.b) * coverageB,
        phase);
    return result;
}
inline FilteredWave filter_wave_pixel(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount,
        int centerIndex, float centerPhase, float footprint,
        float distance, float heightScale, float antialias) {
    FilteredWave result;
    result.coverage = 0.0;
    result.premultiplied = float3(0.0);
    // Four midpoint taps integrate the one selected mip across the physical
    // output pixel. This removes residual horizontal phase sparkle without
    // paying the fullscreen-fragment cost of evaluating a second LOD.
    const float offsets[4] = {-0.375, -0.125, 0.125, 0.375};
    for (uint tap = 0; tap < 4; ++tap) {
        const float local =
            centerPhase + offsets[tap] * footprint;
        const int whole = int(floor(local));
        const int left = centerIndex + whole;
        if (left < 0 || left >= int(levelCount)) {
            continue;
        }
        const uint aIndex = levelOffset + uint(left);
        const uint bIndex = levelOffset + uint(min(
            left + 1, int(levelCount) - 1));
        const FilteredWave sample = filter_wave_pair(
            samples[aIndex], samples[bIndex],
            local - float(whole),
            distance, heightScale, antialias);
        result.coverage += sample.coverage * 0.25;
        result.premultiplied += sample.premultiplied * 0.25;
    }
    return result;
}
inline float4 composite_wave(
        float3 background, FilteredWave filtered) {
    if (filtered.coverage <= 0.0) {
        discard_fragment();
    }
    const float3 color =
        filtered.premultiplied / max(filtered.coverage, 0.0001);
    const float visible = vivid_wave_coverage(filtered.coverage);
    return float4(
        mix(background, color, visible), 1.0);
}
inline FilteredEnvelope filter_envelope_pair(
        AudioEnvelopeSample a, AudioEnvelopeSample b, float phase,
        float distance, float heightScale, float antialias) {
    const float aPeak = float(a.height) / 255.0;
    const float bPeak = float(b.height) / 255.0;
    const float peakA = 1.0 - smoothstep(
        aPeak * heightScale - antialias,
        aPeak * heightScale + antialias, distance);
    const float peakB = 1.0 - smoothstep(
        bPeak * heightScale - antialias,
        bPeak * heightScale + antialias, distance);
    return {mix(peakA, peakB, phase)};
}
inline FilteredEnvelope filter_envelope_pixel(
        const device AudioEnvelopeSample *samples,
        uint levelOffset, uint levelCount,
        int centerIndex, float centerPhase, float footprint,
        float distance, float heightScale, float antialias) {
    FilteredEnvelope result{0.0};
    const float offsets[4] = {-0.375, -0.125, 0.125, 0.375};
    for (uint tap = 0; tap < 4; ++tap) {
        const float local = centerPhase + offsets[tap] * footprint;
        const int whole = int(floor(local));
        const int left = centerIndex + whole;
        if (left < 0 || left >= int(levelCount)) {
            continue;
        }
        const uint aIndex = levelOffset + uint(left);
        const uint bIndex = levelOffset + uint(min(
            left + 1, int(levelCount) - 1));
        const FilteredEnvelope sample = filter_envelope_pair(
            samples[aIndex], samples[bIndex], local - float(whole),
            distance, heightScale, antialias);
        result.coverage += sample.coverage * 0.25;
    }
    return result;
}
inline float4 composite_envelope(
        float3 background, float3 color, FilteredEnvelope filtered) {
    if (filtered.coverage <= 0.0) {
        discard_fragment();
    }
    const float visible = vivid_wave_coverage(filtered.coverage);
    return float4(
        mix(background, color, visible),
        1.0);
}
fragment float4 wave_surface_fragment(
        Out in [[stage_in]],
        const device WaveSample *samples [[buffer(0)]],
        constant Uniforms &u [[buffer(1)]],
        const device WaveSample *overviewSamples [[buffer(2)]],
        const device WaveSample *detailSamples [[buffer(3)]],
        const device AudioEnvelopeSample *audioDetailSamples [[buffer(4)]]) {
    const float scale = max(1.0, u.drawableScale);
    const float xPoints = in.position.x / scale;
    const float yPoints = in.position.y / scale;
    const float3 background = float3(
        13.0 / 255.0, 14.0 / 255.0, 18.0 / 255.0);
    const float antialias = 0.5 / scale;
    if (yPoints < 28.0) {
        if (u.overviewSampleCount == 0) {
            discard_fragment();
        }
        const uint index = min(
            uint(clamp(xPoints / max(1.0, u.widthPoints), 0.0, 0.999999)
                 * float(u.overviewSampleCount)),
            u.overviewSampleCount - 1);
        const WaveSample sample = overviewSamples[index];
        const float distance = abs(yPoints - 14.0);
        const float coverage = 1.0 - smoothstep(
            sample.height * 11.0 - antialias,
            sample.height * 11.0 + antialias, distance);
        if (coverage <= 0.0) {
            discard_fragment();
        }
        float3 color = float3(sample.r, sample.g, sample.b);
        if (u.detailSampleCount > 0) {
            const uint colorIndex = min(
                uint(clamp(
                    xPoints / max(1.0, u.widthPoints), 0.0, 0.999999)
                    * float(u.detailSampleCount)),
                u.detailSampleCount - 1);
            const WaveSample colorSample = detailSamples[colorIndex];
            color = float3(
                colorSample.r, colorSample.g, colorSample.b);
        }
        return float4(mix(
            background, color, vivid_wave_coverage(coverage)), 1.0);
    }
    if (yPoints < 49.0) {
        discard_fragment();
    }
    const float songSeconds = u.currentSeconds
        + (xPoints - u.widthPoints * 0.5) / u.pixelsPerSecond;
    const float waveCenter = 49.0 + (u.heightPoints - 49.0) * 0.5;
    const float distance = abs(yPoints - waveCenter);
    const float heightScale =
        max(4.0, (u.heightPoints - 49.0) * 0.5 - 7.0);
    if (u.audioDetailSampleCount > 0 && u.audioDetailMipCount > 0) {
        const float detailOffsetSamples =
            (xPoints - u.widthPoints * 0.5)
            / u.pixelsPerSecond
            * u.audioDetailSamplesPerSecond;
        const float baseLocal =
            u.audioDetailCurrentPhase + detailOffsetSamples;
        const int baseIndex =
            int(u.audioDetailCurrentIndex) + int(floor(baseLocal));
        if (baseIndex < 0
                || baseIndex >= int(u.audioDetailSampleCount)) {
            discard_fragment();
        }
        // One peak-preserving pyramid is the source at every main-waveform
        // zoom. Select the finest song-anchored level that is no more than
        // ~1.1 samples per physical output pixel. Staying on the magnification
        // side of the minification boundary removes temporal aliasing and is
        // dramatically cheaper than evaluating two LODs for every y fragment.
        // Filtering remains in final silhouette coverage space: raw heights
        // never grow/shrink as a peak translates across fractional pixels.
        const float sampleFootprint = u.audioDetailSamplesPerSecond
            / max(1.0, u.pixelsPerSecond * scale);
        const uint level = min(
            uint(ceil(log2(max(1.0, sampleFootprint / 1.1)))),
            u.audioDetailMipCount - 1);
        const uint levelCount = u.audioDetailMipCounts[level];
        const uint levelOffset = u.audioDetailMipOffsets[level];
        const uint divisor = 1u << level;
        const uint anchorIndex =
            u.audioDetailCurrentIndex / divisor;
        const uint anchorRemainder =
            u.audioDetailCurrentIndex % divisor;
        const float levelLocal =
            (float(anchorRemainder)
             + u.audioDetailCurrentPhase
             + detailOffsetSamples)
            / float(divisor);
        const int levelWhole = int(floor(levelLocal));
        const int levelCenterIndex =
            int(anchorIndex) + levelWhole;
        if (levelCenterIndex < 0
                || levelCenterIndex >= int(levelCount)) {
            discard_fragment();
        }
        const float levelFootprint =
            sampleFootprint / float(divisor);
        float3 color = float3(0.36, 0.72, 1.0);
        const float colorPosition =
            songSeconds * u.detailSamplesPerSecond;
        if (u.detailSampleCount > 0
                && colorPosition >= 0.0
                && colorPosition < float(u.detailSampleCount)) {
            const uint colorLeft = min(
                uint(floor(colorPosition)), u.detailSampleCount - 1);
            const WaveSample colorA = detailSamples[colorLeft];
            const WaveSample colorB = detailSamples[
                min(colorLeft + 1, u.detailSampleCount - 1)];
            color = mix(
                float3(colorA.r, colorA.g, colorA.b),
                float3(colorB.r, colorB.g, colorB.b),
                fract(colorPosition));
        }
        return composite_envelope(
            background, color,
            filter_envelope_pixel(
                audioDetailSamples,
                levelOffset, levelCount,
                levelCenterIndex,
                levelLocal - float(levelWhole),
                levelFootprint,
                distance, heightScale, antialias));
    }
    if (u.pixelsPerSecond > 110.0 && u.detailSampleCount > 0) {
        const float position = songSeconds * u.detailSamplesPerSecond;
        if (position < 0.0 || position >= float(u.detailSampleCount)) {
            discard_fragment();
        }
        const uint left = min(
            uint(floor(position)), u.detailSampleCount - 1);
        const WaveSample a = detailSamples[left];
        const WaveSample b =
            detailSamples[min(left + 1, u.detailSampleCount - 1)];
        return composite_wave(
            background,
            filter_wave_pair(
                a, b, fract(position),
                distance, heightScale, antialias));
    }
    const float centerSample = songSeconds * u.samplesPerSecond;
    const float sampleFootprint =
        u.samplesPerSecond / (u.pixelsPerSecond * scale);
    const float offsets[4] = {-0.375, -0.125, 0.125, 0.375};
    float3 premultiplied = float3(0.0);
    float coverage = 0.0;
    for (uint tap = 0; tap < 4; ++tap) {
        const float position =
            centerSample + offsets[tap] * sampleFootprint;
        if (position < 0.0 || position >= float(u.sampleCount)) {
            continue;
        }
        const uint left = min(uint(floor(position)), u.sampleCount - 1);
        const uint right = min(left + 1, u.sampleCount - 1);
        const float phase = fract(position);
        const WaveSample a = samples[left];
        const WaveSample b = samples[right];
        // Filter fixed raster coverage, not height. Interpolating heights made
        // every fine peak grow/shrink vertically as its horizontal phase moved.
        const FilteredWave filteredTap = filter_wave_pair(
            a, b, phase, distance, heightScale, antialias);
        premultiplied += filteredTap.premultiplied;
        coverage += filteredTap.coverage;
    }
    coverage *= 0.25;
    if (coverage <= 0.0) {
        discard_fragment();
    }
    premultiplied *= 0.25;
    const float3 color = premultiplied / max(coverage, 0.0001);
    // Composite once in the shader and write an opaque result. This removes a
    // second blend/raster rounding step from the high-contrast moving contour.
    return float4(mix(
        background, color, vivid_wave_coverage(coverage)), 1.0);
}
vertex Out grid_vertex(const device GridVertex *v [[buffer(0)]],
                       constant Uniforms &u [[buffer(1)]],
                       uint id [[vertex_id]]) {
    GridVertex p = v[id];
    const float deltaMillis =
        float(int(p.songMillis) - int(u.currentMillis))
        + p.songMillisPhase - u.currentMillisPhase;
    float x = p.overview != 0
        ? (float(p.songMillis) / max(1.0, u.durationSeconds * 1000.0)
           * u.widthPoints + p.xOffsetPixels)
        : (u.widthPoints * 0.5
           + deltaMillis * (u.pixelsPerSecond / 1000.0)
           + p.xOffsetPixels);
    float y = p.yPixels < 0.0
        ? u.heightPoints + p.yPixels
        : min(p.yPixels, u.heightPoints);
    Out o;
    o.position = float4(x / u.widthPoints * 2.0 - 1.0,
                        1.0 - y / u.heightPoints * 2.0, 0, 1);
    o.color = float4(p.r, p.g, p.b, p.a);
    return o;
}
fragment float4 deck_fragment(Out in [[stage_in]]) { return in.color; }
)METAL";
        NSError *error = nil;
        id<MTLLibrary> library = [device_ newLibraryWithSource:source
                                                      options:nil
                                                        error:&error];
        if (!library) {
            const char *message = error.localizedDescription.UTF8String;
            setError(-41, message ? message : "Metal shader failed");
            return;
        }
        MTLRenderPipelineDescriptor *wave = [MTLRenderPipelineDescriptor new];
        wave.vertexFunction =
            [library newFunctionWithName:@"wave_surface_vertex"];
        wave.fragmentFunction =
            [library newFunctionWithName:@"wave_surface_fragment"];
        wave.colorAttachments[0].pixelFormat = metalLayer_.pixelFormat;
        wave.colorAttachments[0].blendingEnabled = NO;
        pipeline_ = [device_ newRenderPipelineStateWithDescriptor:wave error:&error];

        MTLRenderPipelineDescriptor *grid = [wave copy];
        grid.vertexFunction = [library newFunctionWithName:@"grid_vertex"];
        grid.fragmentFunction = [library newFunctionWithName:@"deck_fragment"];
        grid.colorAttachments[0].blendingEnabled = YES;
        grid.colorAttachments[0].sourceRGBBlendFactor =
            MTLBlendFactorSourceAlpha;
        grid.colorAttachments[0].destinationRGBBlendFactor =
            MTLBlendFactorOneMinusSourceAlpha;
        gridPipeline_ = [device_ newRenderPipelineStateWithDescriptor:grid error:&error];
        if (!pipeline_ || !gridPipeline_) {
            const char *message = error.localizedDescription.UTF8String;
            setError(-42, message ? message : "Metal pipeline failed");
        }
    }

    void startDisplayThread() {
        displayDelegate_ = [SBDisplayDelegate new];
        displayDelegate_.deck = this;
        displayThread_ = std::thread([this] {
            pthread_set_qos_class_self_np(
                QOS_CLASS_USER_INTERACTIVE, 0);
            @autoreleasepool {
                CAMetalDisplayLink *link =
                    [[CAMetalDisplayLink alloc] initWithMetalLayer:metalLayer_];
                displayLink_ = link;
                {
                    std::lock_guard<std::mutex> lock(displayRunLoopMutex_);
                    displayRunLoop_ = CFRunLoopGetCurrent();
                    CFRetain(displayRunLoop_);
                }
                link.delegate = displayDelegate_;
                // Windowed macOS composition may need more than one refresh to
                // finish GPU work. A two-frame budget keeps delivery even while
                // targetPresentationTimestamp still places motion on the exact
                // future display time supplied by Core Animation.
                link.preferredFrameLatency = 2.0f;
                link.preferredFrameRateRange =
                    CAFrameRateRangeMake(120.0, 120.0, 120.0);
                NSRunLoop *runLoop = NSRunLoop.currentRunLoop;
                [link addToRunLoop:runLoop forMode:NSRunLoopCommonModes];
                while (!displayStop_.load(std::memory_order_acquire)) {
                    @autoreleasepool {
                        [runLoop runMode:NSDefaultRunLoopMode
                             beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
                    }
                }
                [link invalidate];
                displayLink_ = nil;
                {
                    std::lock_guard<std::mutex> lock(displayRunLoopMutex_);
                    CFRunLoopRef runLoop = displayRunLoop_;
                    displayRunLoop_ = nullptr;
                    if (runLoop) {
                        CFRelease(runLoop);
                    }
                }
            }
        });
    }

    void stopDisplayThread() {
        displayStop_.store(true, std::memory_order_release);
        requestDisplay();
        if (displayThread_.joinable()) {
            displayThread_.join();
        }
        displayDelegate_.deck = nullptr;
        displayDelegate_ = nil;
    }

    static OSStatus renderCallback(
        void *refCon,
        AudioUnitRenderActionFlags *,
        const AudioTimeStamp *timestamp,
        UInt32,
        UInt32 frames,
        AudioBufferList *data) {
        return static_cast<SBDeck *>(refCon)->render(timestamp, frames, data);
    }

    OSStatus render(
        const AudioTimeStamp *timestamp,
        UInt32 frames,
        AudioBufferList *data) {
        renderCallbacks_.fetch_add(1, std::memory_order_acq_rel);
        for (UInt32 i = 0; i < data->mNumberBuffers; ++i) {
            std::memset(data->mBuffers[i].mData, 0,
                        data->mBuffers[i].mDataByteSize);
        }
        if (!playing_.load(std::memory_order_acquire)
                || data->mNumberBuffers < 2) {
            renderCallbacks_.fetch_sub(1, std::memory_order_acq_rel);
            return noErr;
        }
        const uint64_t available = ring_.available();
        const uint32_t count = static_cast<uint32_t>(
            std::min<uint64_t>(frames, available));
        const uint64_t startPosition =
            positionFrames_.load(std::memory_order_acquire);
        float *left = static_cast<float *>(data->mBuffers[0].mData);
        float *right = static_cast<float *>(data->mBuffers[1].mData);
        uint64_t read = ring_.read.load(std::memory_order_relaxed);
        float gain = currentGain_.load(std::memory_order_relaxed);
        uint64_t rampRemaining =
            rampFramesRemaining_.load(std::memory_order_relaxed);
        const uint64_t rampTotal =
            std::max<uint64_t>(1, rampTotalFrames_.load(std::memory_order_relaxed));
        const float startGain = rampStartGain_.load(std::memory_order_relaxed);
        const float targetGain = targetGain_.load(std::memory_order_relaxed);
        for (uint32_t i = 0; i < count; ++i) {
            if (rampRemaining) {
                const float phase =
                    1.0f - static_cast<float>(rampRemaining) / rampTotal;
                gain = startGain + (targetGain - startGain) * phase;
                --rampRemaining;
            } else {
                gain = targetGain;
            }
            const uint32_t index = static_cast<uint32_t>(read + i) & kRingMask;
            left[i] = ring_.left[index] * gain;
            right[i] = ring_.right[index] * gain;
        }
        currentGain_.store(gain, std::memory_order_relaxed);
        rampFramesRemaining_.store(rampRemaining, std::memory_order_release);
        if (!rampRemaining) {
            currentGain_.store(targetGain, std::memory_order_release);
            rampCompleted_.store(
                rampSerial_.load(std::memory_order_acquire),
                std::memory_order_release);
        }
        ring_.read.store(read + count, std::memory_order_release);
        const uint64_t position =
            positionFrames_.fetch_add(count, std::memory_order_acq_rel) + count;
        // Anchor to CoreAudio's presentation host time when it supplies one.
        // Falling back to callback-entry mach time is safe, but the timestamp
        // removes callback scheduling jitter from the display clock.
        const uint64_t hostTime = (
            timestamp
            && (timestamp->mFlags & kAudioTimeStampHostTimeValid))
            ? timestamp->mHostTime : mach_absolute_time();
        updateVisualClock(startPosition, hostTime);
        if (count < frames && !decoderEnded_.load(std::memory_order_acquire)) {
            underruns_.fetch_add(1, std::memory_order_relaxed);
        }
        const uint64_t duration = durationFrames_.load(std::memory_order_acquire);
        if ((duration && position >= duration)
                || (decoderEnded_.load(std::memory_order_acquire)
                    && ring_.available() == 0)) {
            playing_.store(false, std::memory_order_release);
            playRequested_.store(false, std::memory_order_release);
            ended_.store(true, std::memory_order_release);
            positionFrames_.store(duration, std::memory_order_release);
        }
        decodeWake_.notify_one();
        renderCallbacks_.fetch_sub(1, std::memory_order_acq_rel);
        return noErr;
    }

    static OSStatus deviceChanged(
        AudioObjectID,
        UInt32,
        const AudioObjectPropertyAddress *,
        void *clientData) {
        auto *deck = static_cast<SBDeck *>(clientData);
        deck->deviceChangeRequested_.store(true, std::memory_order_release);
        deck->decodeWake_.notify_one();
        return noErr;
    }

    void installDeviceListener() {
        AudioObjectPropertyAddress address{
            kAudioHardwarePropertyDefaultOutputDevice,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyElementMain,
        };
        AudioObjectAddPropertyListener(
            kAudioObjectSystemObject, &address, deviceChanged, this);
    }

    void removeDeviceListener() {
        AudioObjectPropertyAddress address{
            kAudioHardwarePropertyDefaultOutputDevice,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyElementMain,
        };
        AudioObjectRemovePropertyListener(
            kAudioObjectSystemObject, &address, deviceChanged, this);
    }

    void restartAudioUnitForDefaultDevice() {
        const bool requested = playRequested_.load(std::memory_order_acquire);
        playing_.store(false, std::memory_order_release);
        visualClockValid_.store(false, std::memory_order_release);
        stopAudioUnit();
        startAudioUnit(sampleRate_.load(std::memory_order_acquire));
        if (requested && ring_.available() >= kStartThreshold) {
            playing_.store(true, std::memory_order_release);
        }
    }

    void startAudioUnit(double sampleRate = 44100.0) {
        AudioComponentDescription description{};
        description.componentType = kAudioUnitType_Output;
        description.componentSubType = kAudioUnitSubType_DefaultOutput;
        description.componentManufacturer = kAudioUnitManufacturer_Apple;
        AudioComponent component = AudioComponentFindNext(nullptr, &description);
        if (!component
                || AudioComponentInstanceNew(component, &audioUnit_) != noErr) {
            setError(-10, "Unable to create default output Audio Unit");
            return;
        }
        AURenderCallbackStruct callback{renderCallback, this};
        AudioUnitSetProperty(audioUnit_, kAudioUnitProperty_SetRenderCallback,
                             kAudioUnitScope_Input, 0,
                             &callback, sizeof(callback));
        configureAudioUnit(sampleRate);
        AudioUnitInitialize(audioUnit_);
        AudioOutputUnitStart(audioUnit_);
    }

    bool configureAudioUnit(double sampleRate) {
        if (!audioUnit_) {
            return false;
        }
        AudioStreamBasicDescription format{};
        format.mSampleRate = sampleRate;
        format.mFormatID = kAudioFormatLinearPCM;
        format.mFormatFlags = static_cast<AudioFormatFlags>(
            kAudioFormatFlagsNativeFloatPacked)
            | static_cast<AudioFormatFlags>(kAudioFormatFlagIsNonInterleaved);
        format.mBytesPerPacket = sizeof(float);
        format.mFramesPerPacket = 1;
        format.mBytesPerFrame = sizeof(float);
        format.mChannelsPerFrame = 2;
        format.mBitsPerChannel = 32;
        const OSStatus status = AudioUnitSetProperty(
            audioUnit_, kAudioUnitProperty_StreamFormat,
            kAudioUnitScope_Input, 0, &format, sizeof(format));
        if (status != noErr) {
            setError(status, "Unable to configure output sample format");
            return false;
        }
        sampleRate_.store(sampleRate, std::memory_order_release);
        return true;
    }

    void stopAudioUnit() {
        if (!audioUnit_) {
            return;
        }
        AudioOutputUnitStop(audioUnit_);
        AudioUnitUninitialize(audioUnit_);
        AudioComponentInstanceDispose(audioUnit_);
        audioUnit_ = nullptr;
    }

    void decodeLoop() {
        AVAudioFile *file = nil;
        uint64_t activeGeneration = 0;
        uint64_t observedSeek = 0;
        while (!stopRequested_.load(std::memory_order_acquire)) {
            if (deviceChangeRequested_.exchange(
                    false, std::memory_order_acq_rel)) {
                restartAudioUnitForDefaultDevice();
            }
            std::string path;
            uint64_t generation = 0;
            {
                std::lock_guard<std::mutex> lock(commandMutex_);
                if (pathPending_) {
                    path = pendingPath_;
                    generation = pendingGeneration_;
                    pathPending_ = false;
                }
            }
            if (!path.empty()) {
                @autoreleasepool {
                    NSError *error = nil;
                    NSURL *url = [NSURL fileURLWithPath:
                        [NSString stringWithUTF8String:path.c_str()]];
                    file = [[AVAudioFile alloc] initForReading:url error:&error];
                    if (generation != generation_.load(std::memory_order_acquire)) {
                        continue;
                    }
                    waitForRenderIdle();
                    ring_.clear();
                    decoderEnded_.store(false, std::memory_order_release);
                    if (!file) {
                        const char *message = error.localizedDescription.UTF8String;
                        setError(-20, message ? message : "Unable to open audio file");
                        loaded_.store(false, std::memory_order_release);
                        continue;
                    }
                    const double rate = file.processingFormat.sampleRate;
                    if (std::abs(
                            rate - sampleRate_.load(std::memory_order_acquire))
                            > 0.5) {
                        AudioOutputUnitStop(audioUnit_);
                        AudioUnitUninitialize(audioUnit_);
                        if (!configureAudioUnit(rate)) {
                            continue;
                        }
                        AudioUnitInitialize(audioUnit_);
                        AudioOutputUnitStart(audioUnit_);
                    }
                    if (generation
                            != generation_.load(std::memory_order_acquire)) {
                        continue;
                    }
                    durationFrames_.store(
                        static_cast<uint64_t>(file.length),
                        std::memory_order_release);
                    positionFrames_.store(0, std::memory_order_release);
                    requestedSeek_.store(0, std::memory_order_release);
                    observedSeek = seekSerial_.load(std::memory_order_acquire);
                    decodedSeekSerial_.store(
                        observedSeek, std::memory_order_release);
                    activeGeneration = generation;
                    loaded_.store(true, std::memory_order_release);
                    ended_.store(false, std::memory_order_release);
                }
            }

            if (!file || activeGeneration != generation_.load()) {
                waitForDecodeWork();
                continue;
            }

            const uint64_t seekSerial =
                seekSerial_.load(std::memory_order_acquire);
            if (seekSerial != observedSeek) {
                playing_.store(false, std::memory_order_release);
                waitForRenderIdle();
                ring_.clear();
                file.framePosition = static_cast<AVAudioFramePosition>(
                    requestedSeek_.load(std::memory_order_acquire));
                decoderEnded_.store(false, std::memory_order_release);
                observedSeek = seekSerial;
                decodedSeekSerial_.store(
                    observedSeek, std::memory_order_release);
            }

            if (ring_.freeSpace() < kDecodeChunk) {
                waitForDecodeWork();
                continue;
            }

            @autoreleasepool {
                if (file.framePosition >= file.length) {
                    decoderEnded_.store(true, std::memory_order_release);
                    waitForDecodeWork();
                    continue;
                }
                AVAudioFrameCount requested = kDecodeChunk;
                AVAudioPCMBuffer *buffer = [[AVAudioPCMBuffer alloc]
                    initWithPCMFormat:file.processingFormat
                    frameCapacity:requested];
                NSError *error = nil;
                const BOOL ok = [file readIntoBuffer:buffer
                                          frameCount:requested
                                               error:&error];
                if (!ok) {
                    if (file.framePosition >= file.length) {
                        decoderEnded_.store(true, std::memory_order_release);
                        waitForDecodeWork();
                        continue;
                    }
                    const char *message = error.localizedDescription.UTF8String;
                    setError(-21, message ? message : "Audio decode failed");
                    decoderEnded_.store(true, std::memory_order_release);
                    waitForDecodeWork();
                    continue;
                }
                const uint32_t count = buffer.frameLength;
                if (!count) {
                    decoderEnded_.store(true, std::memory_order_release);
                    waitForDecodeWork();
                    continue;
                }
                float *const *channels = buffer.floatChannelData;
                if (!channels) {
                    setError(-22, "Audio decoder did not provide float PCM");
                    decoderEnded_.store(true, std::memory_order_release);
                    continue;
                }
                uint64_t write = ring_.write.load(std::memory_order_relaxed);
                const uint32_t channelCount = buffer.format.channelCount;
                for (uint32_t i = 0; i < count; ++i) {
                    const uint32_t index = static_cast<uint32_t>(write + i) & kRingMask;
                    ring_.left[index] = channels[0][i];
                    ring_.right[index] =
                        channelCount > 1 ? channels[1][i] : channels[0][i];
                }
                ring_.write.store(write + count, std::memory_order_release);
                if (playRequested_.load(std::memory_order_acquire)
                        && decodedSeekSerial_.load(std::memory_order_acquire)
                            == seekSerial_.load(std::memory_order_acquire)
                        && ring_.available() >= kStartThreshold) {
                    playing_.store(true, std::memory_order_release);
                }
            }
        }
    }

    void waitForDecodeWork() {
        std::unique_lock<std::mutex> lock(wakeMutex_);
        decodeWake_.wait_for(lock, std::chrono::milliseconds(3));
    }

    void waitForRenderIdle() {
        while (renderCallbacks_.load(std::memory_order_acquire) != 0
                && !stopRequested_.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
    }

    void clearError() {
        std::lock_guard<std::mutex> lock(errorMutex_);
        errorCode_ = 0;
        errorMessage_.clear();
    }

    void setError(int code, const char *message) {
        std::lock_guard<std::mutex> lock(errorMutex_);
        errorCode_ = code;
        errorMessage_ = message ? message : "Native deck error";
    }

    NSView *__weak parentView_ = nil;
    SBMetalView *metalView_ = nil;
    CAMetalLayer *metalLayer_ = nil;
    id<MTLDevice> device_ = nil;
    id<MTLCommandQueue> commandQueue_ = nil;
    id<MTLRenderPipelineState> pipeline_ = nil;
    id<MTLRenderPipelineState> gridPipeline_ = nil;
    id<MTLBuffer> waveBuffer_ = nil;
    id<MTLBuffer> detailBuffer_ = nil;
    id<MTLBuffer> audioDetailBuffer_ = nil;
    id<MTLBuffer> overviewBuffer_ = nil;
    id<MTLBuffer> gridBuffer_ = nil;
    id<MTLBuffer> cueBuffer_ = nil;
    id<MTLBuffer> sessionCueBuffer_ = nil;
    NSUInteger waveSampleCount_ = 0;
    NSUInteger detailSampleCount_ = 0;
    NSUInteger audioDetailSampleCount_ = 0;
    NSUInteger audioDetailMipCount_ = 0;
    std::array<uint32_t, kAudioDetailMipLevels> audioDetailMipOffsets_{};
    std::array<uint32_t, kAudioDetailMipLevels> audioDetailMipCounts_{};
    NSUInteger overviewSampleCount_ = 0;
    float waveSamplesPerSecond_ = 0.0f;
    float detailSamplesPerSecond_ = 0.0f;
    float audioDetailSamplesPerSecond_ = 0.0f;
    NSUInteger gridVertexCount_ = 0;
    NSUInteger cueVertexCount_ = 0;
    NSUInteger sessionCueVertexCount_ = 0;
    NSUInteger cueMarkerCount_ = 0;
    std::vector<BeatPosition> beatPositions_;
    std::mutex renderMutex_;
    SBDisplayDelegate *displayDelegate_ = nil;
    CAMetalDisplayLink *displayLink_ = nil;
    std::thread displayThread_;
    std::atomic<bool> displayStop_{false};
    std::atomic<bool> displayDirty_{true};
    std::mutex displayRunLoopMutex_;
    CFRunLoopRef displayRunLoop_ = nullptr;
    std::atomic<uint64_t> displayCallbackCount_{0};
    std::atomic<uint64_t> displayFirstTargetNs_{0};
    std::atomic<uint64_t> displayLastTargetNs_{0};
    std::atomic<uint64_t> displayMaxGapNs_{0};
    std::atomic<float> zoomPixelsPerSecond_{kPixelsPerSecond};

    AudioUnit audioUnit_ = nullptr;
    AudioRing ring_;
    std::thread decodeThread_;
    std::atomic<bool> stopRequested_{false};
    std::atomic<bool> deviceChangeRequested_{false};
    std::mutex wakeMutex_;
    std::condition_variable decodeWake_;
    std::mutex commandMutex_;
    std::string pendingPath_;
    uint64_t pendingGeneration_ = 0;
    bool pathPending_ = false;

    std::atomic<bool> loaded_{false};
    std::atomic<bool> playing_{false};
    std::atomic<bool> playRequested_{false};
    std::atomic<bool> decoderEnded_{false};
    std::atomic<bool> ended_{false};
    std::atomic<uint32_t> renderCallbacks_{0};
    std::atomic<uint64_t> generation_{0};
    std::atomic<uint64_t> renderGeneration_{0};
    std::atomic<uint64_t> positionFrames_{0};
    std::atomic<uint64_t> durationFrames_{0};
    std::atomic<double> sampleRate_{44100.0};
    std::atomic<uint64_t> underruns_{0};
    std::atomic<uint64_t> requestedSeek_{0};
    std::atomic<uint64_t> seekSerial_{0};
    std::atomic<uint64_t> decodedSeekSerial_{0};
    std::atomic<bool> visualClockValid_{false};
    std::atomic<uint64_t> visualClockSequence_{0};
    std::atomic<uint64_t> visualAnchorHostTime_{0};
    std::atomic<double> visualAnchorFrame_{0.0};
    std::atomic<double> visualBaseRate_{44100.0};
    std::atomic<double> visualEffectiveRate_{44100.0};
    std::atomic<uint64_t> visualLastHostTime_{0};
    std::atomic<uint64_t> visualLastSourceFrame_{0};

    std::atomic<float> currentGain_{0.9f};
    std::atomic<float> targetGain_{0.9f};
    std::atomic<float> rampStartGain_{0.9f};
    std::atomic<uint64_t> rampFramesRemaining_{0};
    std::atomic<uint64_t> rampTotalFrames_{1};
    std::atomic<uint64_t> rampSerial_{0};
    std::atomic<uint64_t> rampCompleted_{0};

    std::atomic<bool> previewActive_{false};
    std::atomic<uint64_t> previewFrame_{0};

    std::mutex errorMutex_;
    int errorCode_ = 0;
    std::string errorMessage_;
};

@implementation SBDisplayDelegate
- (void)metalDisplayLink:(CAMetalDisplayLink *)link
             needsUpdate:(CAMetalDisplayLinkUpdate *)update {
    (void)link;
    if (self.deck) {
        self.deck->draw(update);
    }
}
@end

extern "C" {

uint32_t sb_deck_abi_version(void) {
    return SB_DECK_ABI_VERSION;
}

int sb_deck_is_supported(void) {
    if (@available(macOS 14.0, *)) {
        return MTLCreateSystemDefaultDevice() != nil;
    }
    return 0;
}

SBDeckRef sb_deck_create(void *parent_nsview) {
    if (!parent_nsview || !sb_deck_is_supported()) {
        return nullptr;
    }
    @try {
        return new SBDeck((__bridge NSView *)parent_nsview);
    } @catch (NSException *) {
        return nullptr;
    }
}

void sb_deck_destroy(SBDeckRef deck) {
    delete static_cast<SBDeck *>(deck);
}

int sb_deck_load(SBDeckRef deck, const char *utf8_path) {
    return deck ? static_cast<SBDeck *>(deck)->load(utf8_path) : 0;
}

int sb_deck_begin_track(SBDeckRef deck, uint64_t generation) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->beginTrack(generation);
    instance->requestDisplay();
    return result;
}

void sb_deck_play(SBDeckRef deck) {
    if (deck) static_cast<SBDeck *>(deck)->play();
}

void sb_deck_pause(SBDeckRef deck) {
    if (deck) static_cast<SBDeck *>(deck)->pause();
}

void sb_deck_stop(SBDeckRef deck) {
    if (deck) static_cast<SBDeck *>(deck)->stop();
}

void sb_deck_seek_frames(SBDeckRef deck, uint64_t frame) {
    if (deck) static_cast<SBDeck *>(deck)->seek(frame);
}

void sb_deck_set_gain(SBDeckRef deck, float gain) {
    if (deck) static_cast<SBDeck *>(deck)->setGain(gain);
}

uint64_t sb_deck_ramp_gain(SBDeckRef deck, float gain, double duration_ms) {
    return deck
        ? static_cast<SBDeck *>(deck)->rampGain(gain, duration_ms)
        : 0;
}

void sb_deck_set_preview_frame(
    SBDeckRef deck, uint64_t frame, int preview_active) {
    if (deck) {
        static_cast<SBDeck *>(deck)->setPreview(frame, preview_active != 0);
    }
}

void sb_deck_set_zoom(SBDeckRef deck, float pixels_per_second) {
    if (deck) {
        static_cast<SBDeck *>(deck)->setZoom(pixels_per_second);
    }
}

void sb_deck_request_display(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->requestDisplay();
    }
}

void sb_deck_sync_view_geometry(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->syncViewGeometry();
    }
}

int sb_deck_set_analysis(
    SBDeckRef deck,
    const float *heights,
    const uint8_t *rgb,
    size_t column_count,
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAnalysis(
        heights, rgb, column_count, columns_per_second,
        beat_seconds, beat_in_bar, beat_count);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_packed_analysis_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint8_t *heights,
    const uint8_t *rgb,
    size_t column_count,
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setPackedAnalysis(
        generation, heights, rgb, column_count, columns_per_second,
        beat_seconds, beat_in_bar, beat_count);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_packed_overview_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint8_t *heights,
    const uint8_t *rgb,
    size_t column_count,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setPackedOverview(
        generation, heights, rgb, column_count,
        beat_seconds, beat_in_bar, beat_count);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_detail_analysis(
    SBDeckRef deck,
    const float *heights,
    const uint8_t *rgb,
    size_t sample_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setDetailAnalysis(
        heights, rgb, sample_count, samples_per_second);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_detail_bands(
    SBDeckRef deck, const uint8_t *bands, size_t sample_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setDetailBands(bands, sample_count);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_spectral_analysis(
    SBDeckRef deck,
    const uint8_t *bands,
    size_t frame_count,
    size_t bands_per_frame,
    double frames_per_second) {
    return deck && static_cast<SBDeck *>(deck)->setSpectralAnalysis(
        bands, frame_count, bands_per_frame, frames_per_second);
}

int sb_deck_set_audio_detail(
    SBDeckRef deck,
    const uint8_t *heights,
    size_t sample_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAudioDetail(
        heights, sample_count, samples_per_second);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_audio_detail_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint8_t *heights,
    size_t sample_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAudioDetail(
        heights, sample_count, samples_per_second, generation);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_cues(
    SBDeckRef deck,
    const uint32_t *position_millis,
    const uint8_t *kinds,
    const uint8_t *slots,
    const uint8_t *rgb,
    size_t cue_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setCues(
        position_millis, kinds, slots, rgb, cue_count);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_session_cue(
    SBDeckRef deck, uint32_t position_millis, int active) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setSessionCue(
        position_millis, active != 0);
    instance->requestDisplay();
    return result;
}

int sb_deck_snapshot(SBDeckRef deck, SBDeckSnapshot *out_snapshot) {
    return deck
        ? static_cast<SBDeck *>(deck)->snapshot(out_snapshot)
        : 0;
}

uint64_t sb_deck_visual_frame(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->visualFrameNow()
        : 0;
}

uint64_t sb_deck_analysis_sample_count(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->analysisSampleCount()
        : 0;
}

double sb_deck_analysis_sample_rate(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->analysisSampleRate()
        : 0.0;
}

uint64_t sb_deck_detail_sample_count(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->detailSampleCount()
        : 0;
}

double sb_deck_detail_sample_rate(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->detailSampleRate()
        : 0.0;
}

double sb_deck_detail_peak_height(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->detailPeakHeight()
        : 0.0;
}

uint64_t sb_deck_audio_detail_mip_count(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->audioDetailMipCount()
        : 0;
}

uint64_t sb_deck_audio_detail_buffer_bytes(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->audioDetailBufferBytes()
        : 0;
}

uint64_t sb_deck_cue_marker_count(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->cueMarkerCount()
        : 0;
}

double sb_deck_pixels_per_second(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->pixelsPerSecond()
        : 0.0;
}

uint64_t sb_deck_display_callback_count(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->displayCallbackCount()
        : 0;
}

double sb_deck_display_average_fps(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->displayAverageFps()
        : 0.0;
}

double sb_deck_display_max_gap_ms(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->displayMaxGapMs()
        : 0.0;
}

void sb_deck_reset_display_stats(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->resetDisplayStats();
    }
}

int sb_deck_view_geometry(
        SBDeckRef deck,
        double *parent_width_points,
        double *view_width_points,
        double *drawable_width_pixels) {
    return deck
        ? static_cast<SBDeck *>(deck)->viewGeometry(
            parent_width_points, view_width_points, drawable_width_pixels)
        : 0;
}

}  // extern "C"
