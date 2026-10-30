#include "MP3Origin.h"
#include "CloudReadiness.h"
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
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

constexpr uint32_t kRingFrames = 1u << 20;
constexpr uint32_t kRingMask = kRingFrames - 1;
constexpr uint32_t kDecodeChunk = 8192;
constexpr uint32_t kStartThreshold = 4096;
constexpr float kPixelsPerSecond = 92.0f;
constexpr std::array<float, 13> kZoomLevels = {
    24.0f, 32.0f, 48.0f, 64.0f, 92.0f, 128.0f, 184.0f,
    256.0f, 384.0f, 576.0f, 864.0f, 1152.0f, 1500.0f,
};
constexpr float kOverviewHeight = 28.0f;
constexpr float kRulerTop = 31.0f;
constexpr float kZoomTop = 49.0f;
constexpr size_t kWaveMipLevels = 32;
constexpr std::array<uint32_t, 18> kAudioDetailMipDivisors = {
    1, 2, 3, 4, 5, 6, 8, 9, 13,
    18, 25, 35, 50, 66, 99, 132, 192, 256,
};
constexpr float kWaveAttributeSmoothSamplesPerPixel = 4.0f;
constexpr NSUInteger kWaveAttributeMarginPixels = 4096;
constexpr NSUInteger kWaveAttributeClockBlock = 128;
constexpr uint64_t kMaxFrameTraceBytes = 512ull * 1024ull * 1024ull;
constexpr uint32_t kMaxFrameTraceFrames = 600;
// Persisted current geometry remains linear. A contrast-preserving display
// curve keeps narrow attacks and decay valleys distinct, while a small
// continuous shoulder resolves the last nonzero uint16 values as a
// physical-pixel hairline. The
// shoulder is zero at digital zero and is applied before spatial
// reconstruction, so entering/leaving true silence remains continuous.
// Preserve the shoulders of real attacks. Squaring the linear envelope made
// ordinary mid-level samples collapse toward the center line, leaving only
// needle-like maxima even though the polarity cache retained the detail.
// Linear signed amplitudes preserve the measured close contour.
constexpr float kWaveformDisplayExponent = 1.0f;
constexpr float kWaveformDisplayScale = 0.84f;
constexpr float kWaveformVisibilityShoulder = 1.0f / 64.0f;
constexpr float kWaveformVisibilityKnee = 4.0f / 65535.0f;

static float displayWaveformHeight(uint16_t encodedHeight) {
    if (encodedHeight == 0) {
        return 0.0f;
    }
    const float linear = encodedHeight / 65535.0f;
    const float companded = std::pow(
        std::clamp(linear, 0.0f, 1.0f),
        kWaveformDisplayExponent);
    const float visibility = 1.0f - std::exp(
        -linear / kWaveformVisibilityKnee);
    return std::clamp(
        kWaveformDisplayScale * (companded
            + kWaveformVisibilityShoulder
                * (1.0f - companded) * visibility),
        0.0f, 1.0f);
}

static float displayWaveformHeight(float linearHeight) {
    const float bounded = std::clamp(linearHeight, 0.0f, 1.0f);
    return displayWaveformHeight(static_cast<uint16_t>(std::lround(
        bounded * 65535.0f)));
}

struct WaveSample {
    float height;
    float r;
    float g;
    float b;
};

struct DisplayWaveform {
    id<MTLBuffer> buffer = nil;
    NSUInteger sampleCount = 0;
    float samplesPerSecond = 0.0f;
    std::array<uint32_t, kWaveMipLevels> mipOffsets{};
    std::array<uint32_t, kWaveMipLevels> mipCounts{};
    size_t mipCount = 0;
};

struct AudioEnvelopeSample {
    uint16_t upper;
    uint16_t lower;
    int16_t minimum;
    int16_t maximum;
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
    float lineDistancePoints;
    float lineHalfWidthPoints;
    float beatSpacingSeconds = 0.0f;
    uint32_t beatKind = 0;
};

struct BeatPosition {
    double seconds;
    uint32_t bar;
    uint8_t beat;
};

struct PresentedFrameState {
    std::mutex mutex;
    uint64_t generation = 0;
    CFTimeInterval latestTime = 0.0;
    uint64_t latestFrame = 0;
    CFTimeInterval scheduledTarget = 0.0;
    uint64_t scheduledFrame = 0;
    std::atomic<uint64_t> count{0};
    std::atomic<uint64_t> firstTimeNs{0};
    std::atomic<uint64_t> lastTimeNs{0};
    std::atomic<uint64_t> maxGapNs{0};
    // Position is predicted for the target presentation timestamp, but a
    // variable-refresh display may put the drawable on screen at a different
    // instant. At 1536 px/s a 4 ms error is about twelve Retina pixels of
    // positional jitter, so measure the spread before trusting the target.
    std::atomic<uint64_t> errorCount{0};
    std::atomic<int64_t> errorSumUs{0};
    std::atomic<int64_t> errorSumSqUs{0};
    std::atomic<int64_t> errorMaxAbsUs{0};
};

struct FrameTraceRecord {
    uint32_t sequence = 0;
    uint64_t generation = 0;
    uint64_t drawableID = 0;
    uint64_t sourceFrame = 0;
    double callbackTime = 0.0;
    double targetTimestamp = 0.0;
    double targetPresentationTimestamp = 0.0;
    double presentedCallbackTime = 0.0;
    double actualPresentedTime = 0.0;
    double gpuStartTime = 0.0;
    double gpuEndTime = 0.0;
    float pixelsPerSecond = 0.0f;
    float drawableScale = 0.0f;
    float attributeOffset = 0.0f;
    uint32_t waveformMode = 0;
    uint32_t textureWidth = 0;
    uint32_t textureHeight = 0;
    uint32_t cropOriginX = 0;
    uint32_t cropOriginY = 0;
    bool gpuCompleted = false;
    bool presentationCompleted = false;
};

struct FrameTraceState {
    std::mutex mutex;
    std::condition_variable completed;
    bool active = true;
    uint32_t maxFrames = 0;
    uint32_t submittedFrames = 0;
    uint32_t gpuCompletedFrames = 0;
    uint32_t presentationCompletedFrames = 0;
    uint32_t cropWidth = 0;
    uint32_t cropHeight = 0;
    uint32_t bytesPerRow = 0;
    uint64_t frameStride = 0;
    id<MTLBuffer> pixels = nil;
    std::vector<FrameTraceRecord> records;
};

struct WaveAttributeClock {
    uint32_t waveIndex;
    float wavePhase;
    uint32_t detailIndex;
    float detailPhase;
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
    uint32_t waveCurrentIndex;
    float waveCurrentPhase;
    uint32_t waveMipCount;
    uint32_t waveMipOffsets[kWaveMipLevels];
    uint32_t waveMipCounts[kWaveMipLevels];
    float drawableScale;
    uint32_t drawableWidthPixels;
    float durationSeconds;
    float detailSamplesPerSecond;
    uint32_t detailSampleCount;
    float audioDetailSamplesPerSecond;
    uint32_t audioDetailSampleCount;
    uint32_t audioDetailCurrentIndex;
    float audioDetailCurrentPhase;
    uint32_t audioDetailMipCount;
    uint32_t audioDetailMipOffsets[kWaveMipLevels];
    uint32_t audioDetailMipCounts[kWaveMipLevels];
    uint32_t audioDetailMipDivisors[kWaveMipLevels];
    uint32_t waveAttributeCount;
    float waveAttributeCurrentOffset;
    float waveAttributeSamplesPerPixel;
    uint32_t waveAttributeValid;
    uint32_t threeBandActive;
    uint32_t waveformMode;
    uint32_t frameTraceMarkerActive;
    uint32_t frameTraceSequence;
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
        cancelFrameTrace();
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

    bool load(const char *path, uint64_t leadFrames = 0,
              uint64_t expectedFrames = 0, double expectedRate = 0) {
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
            pendingLeadFrames_ = leadFrames;
            pendingExpectedFrames_ = expectedFrames;
            pendingExpectedRate_ = expectedRate;
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
        {
            std::lock_guard<std::mutex> lock(presentedFrames_->mutex);
            presentedFrames_->generation = generation;
            presentedFrames_->latestTime = 0.0;
            presentedFrames_->latestFrame = 0;
            presentedFrames_->scheduledTarget = 0.0;
            presentedFrames_->scheduledFrame = 0;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        waveBuffer_ = nil;
        detailBuffer_ = nil;
        audioDetailBuffer_ = nil;
        gridBuffer_ = nil;
        gridOverrideActive_ = false;
        cueBuffer_ = nil;
        sessionCueBuffer_ = nil;
        waveSampleCount_ = 0;
        detailSampleCount_ = 0;
        audioDetailSampleCount_ = 0;
        gridVertexCount_ = 0;
        cueVertexCount_ = 0;
        sessionCueVertexCount_ = 0;
        cueMarkerCount_ = 0;
        memoryCueSeconds_.clear();
        waveSamplesPerSecond_ = 0;
        waveMipCount_ = 0;
        waveMipOffsets_.fill(0);
        waveMipCounts_.fill(0);
        detailSamplesPerSecond_ = 0;
        audioDetailSamplesPerSecond_ = 0;
        audioDetailMipCount_ = 0;
        audioDetailMipOffsets_.fill(0);
        audioDetailMipCounts_.fill(0);
        audioDetailMipDivisors_.fill(0);
        threeBandActive_ = false;
        displayWaveformActive_ = false;
        for (DisplayWaveform &waveform : displayWaveforms_) {
            waveform = DisplayWaveform{};
        }
        beatPositions_.clear();
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    void play() {
        if (ended_.load(std::memory_order_acquire)) {
            seek(0);
        }
        playRequested_.store(true, std::memory_order_release);
        visualStartFrame_.store(positionFrames_.load(std::memory_order_acquire),
                                std::memory_order_release);
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
        visualStartFrame_.store(frame, std::memory_order_release);
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
        if (active) {
            previewFrame_.store(frame, std::memory_order_release);
            previewActive_.store(true, std::memory_order_release);
            requestDisplay();
            return;
        }
        // PlayerBar republishes its coarse transport text position while
        // ordinary playback is running. Once preview is already off, those
        // updates must not wake or enqueue work on the Metal display run loop:
        // CAMetalDisplayLink and the CoreAudio presentation clock own motion.
        if (previewActive_.exchange(false, std::memory_order_acq_rel)) {
            requestDisplay();
        }
    }

    uint64_t beginPreviewHold() {
        // Never rewind behind a drawable Metal already committed. The latest
        // confirmed presentation is what the hand saw, but one latency-one
        // drawable may already be irreversible. Freezing that newest scheduled
        // source frame lets the final normal step land once and then hold;
        // selecting the older visible frame would present the queued frame and
        // visibly snap backward afterward.
        uint64_t frame = 0;
        const CFTimeInterval now = CACurrentMediaTime();
        constexpr CFTimeInterval kFreshPresentationWindow = 1.0 / 15.0;
        {
            std::lock_guard<std::mutex> lock(presentedFrames_->mutex);
            if (presentedFrames_->latestTime > 0.0
                    && presentedFrames_->latestTime <= now
                    && now - presentedFrames_->latestTime
                        <= kFreshPresentationWindow) {
                frame = presentedFrames_->latestFrame;
            }
            if (presentedFrames_->scheduledTarget > 0.0
                    && presentedFrames_->scheduledTarget
                        <= now + kFreshPresentationWindow
                    && now - presentedFrames_->scheduledTarget
                        <= kFreshPresentationWindow) {
                if (presentedFrames_->scheduledFrame >= frame) {
                    frame = presentedFrames_->scheduledFrame;
                }
            }
        }
        if (frame == 0) {
            frame = visualFrameNow();
        }
        previewFrame_.store(frame, std::memory_order_release);
        previewActive_.store(true, std::memory_order_release);
        requestDisplay();
        return frame;
    }

    void beginWaveformUpdate() {
        waveformUpdateDepth_.fetch_add(1, std::memory_order_acq_rel);
    }

    void endWaveformUpdate() {
        uint32_t depth =
            waveformUpdateDepth_.load(std::memory_order_acquire);
        while (depth > 0
                && !waveformUpdateDepth_.compare_exchange_weak(
                    depth, depth - 1, std::memory_order_acq_rel)) {
        }
        requestDisplay();
    }

    float setZoom(float pixelsPerSecond) {
        const auto nearest = std::min_element(
            kZoomLevels.begin(),
            kZoomLevels.end(),
            [pixelsPerSecond](float left, float right) {
                return std::abs(left - pixelsPerSecond)
                    < std::abs(right - pixelsPerSecond);
            });
        const float applied = *nearest;
        zoomPixelsPerSecond_.store(
            applied, std::memory_order_release);
        requestDisplay();
        return applied;
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

    bool hasEnded() const {
        return ended_.load(std::memory_order_acquire);
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

    uint64_t latestPresentedFrame() {
        std::lock_guard<std::mutex> lock(presentedFrames_->mutex);
        return presentedFrames_->latestFrame;
    }

    uint64_t latestScheduledFrame() {
        std::lock_guard<std::mutex> lock(presentedFrames_->mutex);
        return presentedFrames_->scheduledFrame;
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
                peak = std::max(
                    peak,
                    std::max(samples[i].upper, samples[i].lower) / 65535.0f);
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
        return presentedFrames_->count.load(std::memory_order_acquire);
    }

    double displayAverageFps() const {
        const uint64_t count =
            presentedFrames_->count.load(std::memory_order_acquire);
        const uint64_t first =
            presentedFrames_->firstTimeNs.load(std::memory_order_acquire);
        const uint64_t last =
            presentedFrames_->lastTimeNs.load(std::memory_order_acquire);
        return count > 1 && last > first
            ? (count - 1) * 1e9 / static_cast<double>(last - first)
            : 0.0;
    }

    double displayMaxGapMs() const {
        return presentedFrames_->maxGapNs.load(
            std::memory_order_acquire) / 1e6;
    }

    double presentationErrorMeanMs() const {
        const uint64_t n = presentedFrames_->errorCount.load(
            std::memory_order_acquire);
        if (n == 0) {
            return 0.0;
        }
        return presentedFrames_->errorSumUs.load(
            std::memory_order_acquire) / static_cast<double>(n) / 1000.0;
    }

    double presentationErrorStdDevMs() const {
        const uint64_t n = presentedFrames_->errorCount.load(
            std::memory_order_acquire);
        if (n < 2) {
            return 0.0;
        }
        const double mean = presentedFrames_->errorSumUs.load(
            std::memory_order_acquire) / static_cast<double>(n);
        const double meanSquare = presentedFrames_->errorSumSqUs.load(
            std::memory_order_acquire) / static_cast<double>(n);
        return std::sqrt(std::max(0.0, meanSquare - mean * mean)) / 1000.0;
    }

    double presentationErrorMaxMs() const {
        return presentedFrames_->errorMaxAbsUs.load(
            std::memory_order_acquire) / 1000.0;
    }

    void resetDisplayStats() {
        presentedFrames_->count.store(0, std::memory_order_release);
        presentedFrames_->firstTimeNs.store(0, std::memory_order_release);
        presentedFrames_->lastTimeNs.store(0, std::memory_order_release);
        presentedFrames_->maxGapNs.store(0, std::memory_order_release);
        presentedFrames_->errorCount.store(0, std::memory_order_release);
        presentedFrames_->errorSumUs.store(0, std::memory_order_release);
        presentedFrames_->errorSumSqUs.store(0, std::memory_order_release);
        presentedFrames_->errorMaxAbsUs.store(
            0, std::memory_order_release);
    }

    bool beginFrameTrace(uint32_t maxFrames, uint32_t cropWidthPixels) {
        if (!metalLayer_ || maxFrames == 0
                || maxFrames > kMaxFrameTraceFrames) {
            return false;
        }
        const CGSize drawable = metalLayer_.drawableSize;
        const uint32_t textureWidth = static_cast<uint32_t>(std::max(
            0.0, std::floor(drawable.width)));
        const uint32_t textureHeight = static_cast<uint32_t>(std::max(
            0.0, std::floor(drawable.height)));
        const double widthPoints = std::max(1.0, metalLayer_.bounds.size.width);
        const double drawableScale = textureWidth / widthPoints;
        const uint32_t cropOriginY = static_cast<uint32_t>(std::clamp(
            std::llround(kZoomTop * drawableScale),
            0ll,
            static_cast<long long>(textureHeight)));
        if (textureWidth < 64 || cropOriginY >= textureHeight) {
            return false;
        }
        uint32_t cropWidth = cropWidthPixels
            ? std::min(cropWidthPixels, textureWidth)
            : std::min<uint32_t>(768, textureWidth);
        // A 64-pixel multiple makes each BGRA row naturally satisfy Metal's
        // 256-byte texture-to-buffer alignment and keeps the exported raw
        // sequence directly consumable by ffmpeg without repacking.
        cropWidth = std::max<uint32_t>(64, cropWidth / 64 * 64);
        cropWidth = std::min(cropWidth, textureWidth / 64 * 64);
        const uint32_t cropHeight = textureHeight - cropOriginY;
        const uint32_t bytesPerRow = cropWidth * 4;
        const uint64_t frameStride =
            static_cast<uint64_t>(bytesPerRow) * cropHeight;
        const uint64_t captureBytes = frameStride * maxFrames;
        if (frameStride == 0 || captureBytes > kMaxFrameTraceBytes) {
            return false;
        }
        id<MTLBuffer> pixels = [device_
            newBufferWithLength:static_cast<NSUInteger>(captureBytes)
            options:MTLResourceStorageModeShared];
        if (!pixels) {
            return false;
        }
        auto trace = std::make_shared<FrameTraceState>();
        trace->maxFrames = maxFrames;
        trace->cropWidth = cropWidth;
        trace->cropHeight = cropHeight;
        trace->bytesPerRow = bytesPerRow;
        trace->frameStride = frameStride;
        trace->pixels = pixels;
        trace->records.resize(maxFrames);
        {
            std::lock_guard<std::mutex> lock(frameTraceMutex_);
            if (frameTrace_) {
                return false;
            }
            frameTrace_ = trace;
            frameTraceArmed_.store(true, std::memory_order_release);
        }
        return true;
    }

    bool frameTraceStatus(SBDeckFrameTraceStatus *out) {
        if (!out) {
            return false;
        }
        std::memset(out, 0, sizeof(*out));
        out->abi_version = SB_DECK_ABI_VERSION;
        std::shared_ptr<FrameTraceState> trace;
        {
            std::lock_guard<std::mutex> lock(frameTraceMutex_);
            trace = frameTrace_;
        }
        if (!trace) {
            return true;
        }
        std::lock_guard<std::mutex> lock(trace->mutex);
        out->active = trace->active ? 1u : 0u;
        out->max_frames = trace->maxFrames;
        out->submitted_frames = trace->submittedFrames;
        out->gpu_completed_frames = trace->gpuCompletedFrames;
        out->presentation_completed_frames =
            trace->presentationCompletedFrames;
        out->crop_width_pixels = trace->cropWidth;
        out->crop_height_pixels = trace->cropHeight;
        out->bytes_per_row = trace->bytesPerRow;
        out->capture_bytes =
            trace->frameStride * trace->maxFrames;
        return true;
    }

    bool finishFrameTrace(const char *outputDirectory) {
        if (!outputDirectory || !*outputDirectory) {
            return false;
        }
        if (playRequested_.load(std::memory_order_acquire)
                || playing_.load(std::memory_order_acquire)) {
            return false;
        }
        std::shared_ptr<FrameTraceState> trace;
        {
            std::lock_guard<std::mutex> lock(frameTraceMutex_);
            trace = frameTrace_;
        }
        if (!trace) {
            return false;
        }

        std::vector<FrameTraceRecord> records;
        uint32_t submitted = 0;
        uint32_t gpuCompleted = 0;
        uint32_t presentationCompleted = 0;
        {
            std::unique_lock<std::mutex> lock(trace->mutex);
            trace->active = false;
            trace->completed.wait_for(
                lock,
                std::chrono::seconds(3),
                [&trace] {
                    return trace->gpuCompletedFrames
                            >= trace->submittedFrames
                        && trace->presentationCompletedFrames
                            >= trace->submittedFrames;
                });
            submitted = trace->submittedFrames;
            if (trace->gpuCompletedFrames < submitted) {
                return false;
            }
            gpuCompleted = trace->gpuCompletedFrames;
            presentationCompleted =
                trace->presentationCompletedFrames;
            records.assign(
                trace->records.begin(),
                trace->records.begin() + submitted);
        }

        namespace fs = std::filesystem;
        const fs::path directory(outputDirectory);
        std::error_code pathError;
        fs::create_directories(directory, pathError);
        if (pathError || !fs::is_directory(directory)) {
            return false;
        }
        const fs::path rawPath = directory / "frames.bgra";
        const fs::path csvPath = directory / "frames.csv";
        const fs::path manifestPath = directory / "manifest.json";
        if (fs::exists(rawPath) || fs::exists(csvPath)
                || fs::exists(manifestPath)) {
            return false;
        }

        std::ofstream raw(rawPath, std::ios::binary | std::ios::out);
        if (!raw) {
            return false;
        }
        const uint64_t rawBytes = trace->frameStride * submitted;
        raw.write(
            static_cast<const char *>(trace->pixels.contents),
            static_cast<std::streamsize>(rawBytes));
        raw.close();
        if (!raw) {
            return false;
        }

        std::ofstream csv(csvPath, std::ios::out);
        if (!csv) {
            return false;
        }
        csv << "sequence,generation,drawable_id,source_frame,"
               "callback_time,target_timestamp,target_presentation_timestamp,"
               "presented_callback_time,actual_presented_time,"
               "gpu_start_time,gpu_end_time,pixels_per_second,"
               "drawable_scale,attribute_offset,waveform_mode,"
               "texture_width,texture_height,crop_origin_x,crop_origin_y,"
               "gpu_completed,presentation_completed\n";
        csv << std::setprecision(17);
        for (const FrameTraceRecord &record : records) {
            csv << record.sequence << ','
                << record.generation << ','
                << record.drawableID << ','
                << record.sourceFrame << ','
                << record.callbackTime << ','
                << record.targetTimestamp << ','
                << record.targetPresentationTimestamp << ','
                << record.presentedCallbackTime << ','
                << record.actualPresentedTime << ','
                << record.gpuStartTime << ','
                << record.gpuEndTime << ','
                << record.pixelsPerSecond << ','
                << record.drawableScale << ','
                << record.attributeOffset << ','
                << record.waveformMode << ','
                << record.textureWidth << ','
                << record.textureHeight << ','
                << record.cropOriginX << ','
                << record.cropOriginY << ','
                << (record.gpuCompleted ? 1 : 0) << ','
                << (record.presentationCompleted ? 1 : 0)
                << '\n';
        }
        csv.close();
        if (!csv) {
            return false;
        }

        std::ofstream manifest(manifestPath, std::ios::out);
        if (!manifest) {
            return false;
        }
        manifest
            << "{\n"
            << "  \"format\": \"soundbox-metal-frame-trace-v1\",\n"
            << "  \"pixel_format\": \"bgra8unorm_srgb\",\n"
            << "  \"frame_order\": \"display_link_submission\",\n"
            << "  \"camera_sync_marker\": "
               "\"dual-edge-gray10-plus-parity-v1\",\n"
            << "  \"submitted_frames\": " << submitted << ",\n"
            << "  \"gpu_completed_frames\": "
            << gpuCompleted << ",\n"
            << "  \"presentation_completed_frames\": "
            << presentationCompleted << ",\n"
            << "  \"crop_width_pixels\": " << trace->cropWidth << ",\n"
            << "  \"crop_height_pixels\": " << trace->cropHeight << ",\n"
            << "  \"bytes_per_row\": " << trace->bytesPerRow << ",\n"
            << "  \"frame_stride_bytes\": " << trace->frameStride << ",\n"
            << "  \"raw_file\": \"frames.bgra\",\n"
            << "  \"metadata_file\": \"frames.csv\"\n"
            << "}\n";
        manifest.close();
        if (!manifest) {
            return false;
        }

        {
            std::lock_guard<std::mutex> lock(frameTraceMutex_);
            if (frameTrace_ == trace) {
                frameTrace_.reset();
                frameTraceArmed_.store(false, std::memory_order_release);
            }
        }
        return true;
    }

    void cancelFrameTrace() {
        std::shared_ptr<FrameTraceState> trace;
        {
            std::lock_guard<std::mutex> lock(frameTraceMutex_);
            trace = std::move(frameTrace_);
            frameTraceArmed_.store(false, std::memory_order_release);
        }
        if (trace) {
            std::lock_guard<std::mutex> lock(trace->mutex);
            trace->active = false;
        }
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
            waveSampleCount_ = 0;
            detailSampleCount_ = 0;
            audioDetailSampleCount_ = 0;
            waveSamplesPerSecond_ = 0;
            waveMipCount_ = 0;
            waveMipOffsets_.fill(0);
            waveMipCounts_.fill(0);
            detailSamplesPerSecond_ = 0;
            audioDetailSamplesPerSecond_ = 0;
            audioDetailMipCount_ = 0;
            audioDetailMipOffsets_.fill(0);
            audioDetailMipDivisors_.fill(0);
            waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
            audioDetailMipCounts_.fill(0);
            gridBuffer_ = nil;
            gridVertexCount_ = 0;
            cueBuffer_ = nil;
            cueVertexCount_ = 0;
            cueMarkerCount_ = 0;
            memoryCueSeconds_.clear();
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

    static std::vector<WaveSample> buildWavePyramid(
            const std::vector<WaveSample> &base,
            std::array<uint32_t, kWaveMipLevels> &offsets,
            std::array<uint32_t, kWaveMipLevels> &counts,
            size_t &mipCount) {
        offsets.fill(0);
        counts.fill(0);
        mipCount = 0;
        if (base.empty()) {
            return {};
        }
        std::vector<WaveSample> pyramid = base;
        std::vector<WaveSample> level = base;
        std::vector<float> levelEnergy;
        levelEnergy.reserve(base.size());
        for (const WaveSample &sample : base) {
            levelEnergy.push_back(sample.height);
        }
        counts[0] = static_cast<uint32_t>(level.size());
        mipCount = 1;
        while (mipCount < kWaveMipLevels && level.size() > 1) {
            std::vector<WaveSample> next;
            std::vector<float> nextEnergy;
            next.reserve((level.size() + 1) / 2);
            nextEnergy.reserve((level.size() + 1) / 2);
            for (size_t i = 0; i < level.size(); i += 2) {
                const WaveSample &a = level[i];
                const WaveSample &b =
                    i + 1 < level.size() ? level[i + 1] : a;
                const float aEnergy = levelEnergy[i];
                const float bEnergy = i + 1 < levelEnergy.size()
                    ? levelEnergy[i + 1] : 0.0f;
                const float energy = aEnergy + bEnergy;
                const float r = energy > 0.0001f
                    ? (a.r * aEnergy + b.r * bEnergy) / energy
                    : (a.r + b.r) * 0.5f;
                const float g = energy > 0.0001f
                    ? (a.g * aEnergy + b.g * bEnergy) / energy
                    : (a.g + b.g) * 0.5f;
                const float blue = energy > 0.0001f
                    ? (a.b * aEnergy + b.b * bEnergy) / energy
                    : (a.b + b.b) * 0.5f;
                // Peak geometry and integrated color are deliberately
                // different reductions of the same time bin. Peaks retain
                // transients; amplitude-weighted RGB energy prevents adjacent
                // bands from fighting as a minified pixel moves.
                next.push_back({
                    std::max(a.height, b.height),
                    r, g, blue,
                });
                nextEnergy.push_back(energy);
            }
            offsets[mipCount] =
                static_cast<uint32_t>(pyramid.size());
            counts[mipCount] = static_cast<uint32_t>(next.size());
            pyramid.insert(pyramid.end(), next.begin(), next.end());
            level = std::move(next);
            levelEnergy = std::move(nextEnergy);
            ++mipCount;
        }
        return pyramid;
    }

    static std::vector<WaveSample> buildThreeBandPyramid(
            const std::vector<WaveSample> &base,
            std::array<uint32_t, kWaveMipLevels> &offsets,
            std::array<uint32_t, kWaveMipLevels> &counts,
            size_t &mipCount) {
        offsets.fill(0);
        counts.fill(0);
        mipCount = 0;
        if (base.empty()) {
            return {};
        }
        std::vector<WaveSample> pyramid = base;
        std::vector<WaveSample> level = base;
        counts[0] = static_cast<uint32_t>(level.size());
        mipCount = 1;
        while (mipCount < kWaveMipLevels && level.size() > 1) {
            std::vector<WaveSample> next;
            next.reserve((level.size() + 1) / 2);
            for (size_t i = 0; i < level.size(); i += 2) {
                const WaveSample &a = level[i];
                const WaveSample &b =
                    i + 1 < level.size() ? level[i + 1] : a;
                const float low = std::max(
                    a.r * a.height, b.r * b.height);
                const float mid = std::max(
                    a.g * a.height, b.g * b.height);
                const float high = std::max(
                    a.b * a.height, b.b * b.height);
                const float height = std::max({low, mid, high});
                next.push_back({
                    height,
                    height > 0.0f ? low / height : 0.0f,
                    height > 0.0f ? mid / height : 0.0f,
                    height > 0.0f ? high / height : 0.0f,
                });
            }
            offsets[mipCount] =
                static_cast<uint32_t>(pyramid.size());
            counts[mipCount] = static_cast<uint32_t>(next.size());
            pyramid.insert(pyramid.end(), next.begin(), next.end());
            level = std::move(next);
            ++mipCount;
        }
        return pyramid;
    }

    static WaveSample packedWaveSample(
            uint8_t height, const uint8_t *rgb) {
        int values[3] = {
            std::min(255, static_cast<int>(rgb[0]) * 18),
            std::min(255, static_cast<int>(rgb[1]) * 36),
            std::min(255, static_cast<int>(rgb[2]) * 36),
        };
        const int brightest = std::max({values[0], values[1], values[2]});
        if (brightest > 0 && brightest < 155) {
            const double boost = 155.0 / brightest;
            for (int channel = 0; channel < 3; ++channel) {
                values[channel] = std::min(
                    255, static_cast<int>(values[channel] * boost));
            }
        }
        return {
            height / 255.0f,
            values[0] / 255.0f,
            values[1] / 255.0f,
            values[2] / 255.0f,
        };
    }

    static bool buildBeatGeometry(
        const double *beatSeconds, const uint8_t *beatInBar, size_t beatCount,
        std::vector<GridVertex> &grid, std::vector<BeatPosition> &positions) {
        if (beatCount && !beatSeconds) return false;
        grid.reserve(beatCount * 6);
        positions.reserve(beatCount);
        uint32_t bar = 0;
        double previous = -1.0;
        for (size_t i = 0; i < beatCount; ++i) {
            const double t = beatSeconds[i];
            if (!std::isfinite(t) || t < 0.0 || t <= previous) return false;
            previous = t;
            uint8_t beat = beatInBar ? beatInBar[i]
                                     : static_cast<uint8_t>(i % 4 + 1);
            if (beat < 1 || beat > 4) beat = static_cast<uint8_t>(i % 4 + 1);
            const bool downbeat = beat == 1;
            // Native rekordbox numbering: opening partial bar 0; first full
            // downbeat starts bar 1. Preserve all preceding source beats.
            if (downbeat) ++bar;
            const uint32_t millis = static_cast<uint32_t>(std::clamp(
                std::llround(t * 1000.0), 0LL,
                static_cast<long long>(std::numeric_limits<uint32_t>::max())));
            positions.push_back({t, bar, beat});
            addBeatGrid(grid, millis, downbeat, beatSeconds, beatCount, i);
        }
        return true;
    }

    // renderMutex_ must be held. Both the drawable and diagnostic ABI use
    // this exact RAM-only resolver; fractional beats support off-grid cues.
    bool rulerPosition(double seconds, BeatPosition &result) const {
        if (!std::isfinite(seconds)) return false;
        auto found = std::upper_bound(beatPositions_.begin(), beatPositions_.end(), seconds,
            [](double t, const BeatPosition &p) { return t < p.seconds; });
        if (found == beatPositions_.begin()) return false;
        result = *std::prev(found);
        if (!beatCountMode_) return true;
        if (beatPositions_.size() < 2 || seconds > beatPositions_.back().seconds) return false;
        const auto cue = std::upper_bound(memoryCueSeconds_.begin(), memoryCueSeconds_.end(), seconds);
        if (cue == memoryCueSeconds_.end() || *cue > beatPositions_.back().seconds) return false;
        const auto phase = [this](double t) {
            auto next = std::upper_bound(beatPositions_.begin(), beatPositions_.end(), t,
                [](double value, const BeatPosition &p) { return value < p.seconds; });
            auto current = std::prev(next);
            const double index = std::distance(beatPositions_.begin(), current);
            return next == beatPositions_.end() ? index
                : index + (t-current->seconds)/(next->seconds-current->seconds);
        };
        const uint32_t remaining = static_cast<uint32_t>(std::max(0.0, std::ceil(phase(*cue)-phase(seconds)-1e-9)));
        result.bar = beatCountMode_ == 2 ? remaining : remaining / 4;
        result.beat = beatCountMode_ == 2 ? 255 : remaining % 4;
        return true;
    }

    bool gridPosition(double seconds, uint32_t *bar, uint8_t *beat) {
        if (!bar || !beat) return false;
        std::lock_guard<std::mutex> lock(renderMutex_);
        BeatPosition result{};
        if (!rulerPosition(seconds, result)) return false;
        *bar = result.bar;
        *beat = result.beat;
        return true;
    }

    void setBeatCountMode(uint32_t mode) {
        std::lock_guard<std::mutex> lock(renderMutex_);
        beatCountMode_ = std::min(mode, 2u);
    }

    bool setGrid(
        uint64_t expectedGeneration, const double *beatSeconds,
        const uint8_t *beatInBar, size_t beatCount, bool pinned) {
        std::vector<GridVertex> grid;
        std::vector<BeatPosition> positions;
        if (!buildBeatGeometry(beatSeconds, beatInBar, beatCount, grid, positions)) {
            return false;
        }
        id<MTLBuffer> buffer = grid.empty() ? nil
            : [device_ newBufferWithBytes:grid.data()
                length:grid.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!grid.empty() && !buffer) return false;
        std::lock_guard<std::mutex> lock(renderMutex_);
        if (expectedGeneration && renderGeneration_.load(std::memory_order_acquire)
                != expectedGeneration) return false;
        gridBuffer_ = buffer;
        gridVertexCount_ = grid.size();
        beatPositions_ = std::move(positions);
        gridOverrideActive_ = pinned;
        // Wave buffers, cached attributes, cues and the audio clock are intact.
        return true;
    }

    bool installAnalysis(
        std::vector<WaveSample> wave,
        double columnsPerSecond,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount,
        uint64_t expectedGeneration) {
        std::vector<GridVertex> grid;
        std::vector<BeatPosition> beatPositions;
        if (!buildBeatGeometry(beatSeconds, beatInBar, beatCount, grid, beatPositions)) return false;

        std::array<uint32_t, kWaveMipLevels> waveMipOffsets{};
        std::array<uint32_t, kWaveMipLevels> waveMipCounts{};
        size_t waveMipCount = 0;
        std::vector<WaveSample> wavePyramid = buildWavePyramid(
            wave, waveMipOffsets, waveMipCounts, waveMipCount);
        id<MTLBuffer> waveBuffer = [device_
            newBufferWithBytes:wavePyramid.data()
            length:wavePyramid.size() * sizeof(WaveSample)
            options:MTLResourceStorageModeShared];
        id<MTLBuffer> gridBuffer = grid.empty() ? nil
            : [device_ newBufferWithBytes:grid.data()
                length:grid.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!waveBuffer) {
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
        waveSampleCount_ = wave.size();
        waveMipOffsets_ = waveMipOffsets;
        waveMipCounts_ = waveMipCounts;
        waveMipCount_ = waveMipCount;
        detailSampleCount_ = wave.size();
        audioDetailSampleCount_ = 0;
        waveSamplesPerSecond_ = static_cast<float>(columnsPerSecond);
        detailSamplesPerSecond_ = static_cast<float>(columnsPerSecond);
        audioDetailSamplesPerSecond_ = 0.0f;
        audioDetailMipCount_ = 0;
        audioDetailMipOffsets_.fill(0);
        audioDetailMipCounts_.fill(0);
        audioDetailMipDivisors_.fill(0);
        if (!gridOverrideActive_) {
            gridBuffer_ = gridBuffer;
            gridVertexCount_ = grid.size();
            beatPositions_ = std::move(beatPositions);
        }
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        cueBuffer_ = nil;
        cueVertexCount_ = 0;
        cueMarkerCount_ = 0;
        memoryCueSeconds_.clear();
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
            wave.push_back(packedWaveSample(heights[i], rgb + i * 3));
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
        double columnsPerSecond,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount) {
        if (!heights || !rgb || !count || columnsPerSecond <= 0
                || (expectedGeneration
                    && renderGeneration_.load(std::memory_order_acquire)
                        != expectedGeneration)) {
            return false;
        }
        std::vector<WaveSample> wave;
        wave.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            wave.push_back(packedWaveSample(heights[i], rgb + i * 3));
        }
        std::array<uint32_t, kWaveMipLevels> waveMipOffsets{};
        std::array<uint32_t, kWaveMipLevels> waveMipCounts{};
        size_t waveMipCount = 0;
        std::vector<WaveSample> wavePyramid = buildWavePyramid(
            wave, waveMipOffsets, waveMipCounts, waveMipCount);

        std::vector<GridVertex> grid;
        std::vector<BeatPosition> beatPositions;
        if (!buildBeatGeometry(beatSeconds, beatInBar, beatCount, grid, beatPositions)) return false;
        id<MTLBuffer> waveBuffer =
            [device_ newBufferWithBytes:wavePyramid.data()
                length:wavePyramid.size() * sizeof(WaveSample)
                options:MTLResourceStorageModeShared];
        id<MTLBuffer> gridBuffer = grid.empty() ? nil
            : [device_ newBufferWithBytes:grid.data()
                length:grid.size() * sizeof(GridVertex)
                options:MTLResourceStorageModeShared];
        if (!waveBuffer) {
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
        waveSampleCount_ = wave.size();
        detailSampleCount_ = wave.size();
        waveSamplesPerSecond_ = static_cast<float>(columnsPerSecond);
        detailSamplesPerSecond_ = static_cast<float>(columnsPerSecond);
        waveMipOffsets_ = waveMipOffsets;
        waveMipCounts_ = waveMipCounts;
        waveMipCount_ = waveMipCount;
        if (!gridOverrideActive_) {
            gridBuffer_ = gridBuffer;
            gridVertexCount_ = grid.size();
            beatPositions_ = std::move(beatPositions);
        }
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    bool setPackedOverview16(
        uint64_t expectedGeneration,
        const uint16_t *heights,
        const uint8_t *rgb,
        size_t count,
        double columnsPerSecond,
        const double *beatSeconds,
        const uint8_t *beatInBar,
        size_t beatCount) {
        if (!heights || !rgb || !count) {
            return false;
        }
        // The overview is only 22 physical pixels tall after padding, so one
        // display byte already exceeds its raster resolution. Persisted u16
        // remains linear/high-precision; this short-lived upload applies the
        // same smooth visibility curve as ultra geometry.
        std::vector<uint8_t> displayed;
        displayed.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            const float height = displayWaveformHeight(heights[i]);
            displayed.push_back(static_cast<uint8_t>(std::clamp(
                std::lround(height * 255.0f), 0L, 255L)));
        }
        return setPackedOverview(
            expectedGeneration, displayed.data(), rgb, count,
            columnsPerSecond, beatSeconds, beatInBar, beatCount);
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
            memoryCueSeconds_.clear();
            return true;
        }
        std::vector<GridVertex> markers;
        markers.reserve(count * 420);
        size_t markerCount = 0;
        std::vector<double> memoryCues;
        for (size_t i = 0; i < count; ++i) {
            const uint32_t t = positions[i];
            const bool hot = kinds[i] == 1;
            if (!hot) memoryCues.push_back(t / 1000.0);
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
        std::sort(memoryCues.begin(), memoryCues.end());
        memoryCueSeconds_ = std::move(memoryCues);
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
        std::array<uint32_t, kWaveMipLevels> mipOffsets{};
        std::array<uint32_t, kWaveMipLevels> mipCounts{};
        size_t mipCount = 0;
        std::vector<WaveSample> pyramid = buildWavePyramid(
            detail, mipOffsets, mipCounts, mipCount);
        id<MTLBuffer> buffer = [device_ newBufferWithBytes:pyramid.data()
            length:pyramid.size() * sizeof(WaveSample)
            options:MTLResourceStorageModeShared];
        if (!buffer) {
            setError(-41, "Metal detail waveform buffer allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        waveBuffer_ = buffer;
        waveSampleCount_ = detail.size();
        waveSamplesPerSecond_ = static_cast<float>(samplesPerSecond);
        waveMipOffsets_ = mipOffsets;
        waveMipCounts_ = mipCounts;
        waveMipCount_ = mipCount;
        detailBuffer_ = buffer;
        detailSampleCount_ = detail.size();
        detailSamplesPerSecond_ = static_cast<float>(samplesPerSecond);
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    bool setDetailBands(const uint8_t *bands, size_t count) {
        if (!bands || !count) {
            return false;
        }
        std::vector<WaveSample> detail;
        {
            std::lock_guard<std::mutex> lock(renderMutex_);
            if (detailSamplesPerSecond_ <= 0.0f) {
                return false;
            }
        }
        detail.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            const float low = std::clamp(
                (bands[i * 3] & 0x7f) * 0.40f / 64.0f,
                0.0f, 1.0f);
            const float mid = std::clamp(
                (bands[i * 3 + 1] & 0x7f) * 0.43f / 64.0f,
                0.0f, 1.0f);
            const float high = std::clamp(
                (bands[i * 3 + 2] & 0x7f) * 0.40f / 64.0f,
                0.0f, 1.0f);
            const float height = std::max({mid, high, low});
            detail.push_back({
                height,
                height > 0.0f ? low / height : 0.0f,
                height > 0.0f ? mid / height : 0.0f,
                height > 0.0f ? high / height : 0.0f,
            });
        }
        std::array<uint32_t, kWaveMipLevels> mipOffsets{};
        std::array<uint32_t, kWaveMipLevels> mipCounts{};
        size_t mipCount = 0;
        std::vector<WaveSample> pyramid = buildThreeBandPyramid(
            detail, mipOffsets, mipCounts, mipCount);
        id<MTLBuffer> buffer = [device_ newBufferWithBytes:pyramid.data()
            length:pyramid.size() * sizeof(WaveSample)
            options:MTLResourceStorageModeShared];
        if (!buffer) {
            setError(-42, "Metal three-band waveform allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        waveBuffer_ = buffer;
        detailBuffer_ = buffer;
        waveSampleCount_ = detail.size();
        detailSampleCount_ = detail.size();
        waveSamplesPerSecond_ = detailSamplesPerSecond_;
        waveMipOffsets_ = mipOffsets;
        waveMipCounts_ = mipCounts;
        waveMipCount_ = mipCount;
        threeBandActive_ = true;
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    static WaveSample blueWaveSample(uint8_t packed) {
        const float height = displayWaveformHeight(
            (packed & 0x1f) / 31.0f);
        const float luminance = ((packed >> 5) & 0x07) / 7.0f;
        // Preserve PWV3 geometry. Its upper three bits select the full
        // blue-to-cyan edge range measured in the October 1 reference.
        // The fragment shader resolves the cyan/white interior separately.
        return {
            height,
            0.08f,
            0.07f + 0.78f * luminance,
            0.86f,
        };
    }

    static WaveSample rgbWaveSample(const uint8_t *packed) {
        const uint16_t value =
            static_cast<uint16_t>(packed[0]) << 8
            | static_cast<uint16_t>(packed[1]);
        const uint8_t raw[3] = {
            static_cast<uint8_t>((value & 0xe000) >> 12),
            static_cast<uint8_t>((value & 0x1c00) >> 10),
            static_cast<uint8_t>((value & 0x0380) >> 7),
        };
        // PWV5 stores one compact RGB display color, not three independent
        // band heights. Expand each channel through its own encoded range:
        // red uses the even 0..14 codes while green and blue use 0..7.
        return {
            displayWaveformHeight(
                ((value & 0x007c) >> 2) / 31.0f),
            raw[0] / 14.0f,
            raw[1] / 7.0f,
            raw[2] / 7.0f,
        };
    }

    static WaveSample threeBandWaveSample(const uint8_t *packed) {
        // The supplied Rekordbox pixels establish the stored display-layer
        // order as blue-low, amber-mid, white-high. Each layer also has its
        // own measured vertical transfer.
        const float low = std::clamp(
            (packed[0] & 0x7f) * 0.40f / 64.0f, 0.0f, 1.0f);
        const float mid = std::clamp(
            (packed[1] & 0x7f) * 0.43f / 64.0f, 0.0f, 1.0f);
        const float high = std::clamp(
            (packed[2] & 0x7f) * 0.40f / 64.0f, 0.0f, 1.0f);
        const float height = std::max({low, mid, high});
        return {
            height,
            height > 0.0f ? low / height : 0.0f,
            height > 0.0f ? mid / height : 0.0f,
            height > 0.0f ? high / height : 0.0f,
        };
    }

    bool activateWaveformModeLocked() {
        const size_t index = waveformMode_ - 1;
        if (index >= displayWaveforms_.size()) {
            return false;
        }
        const DisplayWaveform &selected = displayWaveforms_[index];
        if (!selected.buffer || !selected.sampleCount || !selected.mipCount) {
            return false;
        }
        waveBuffer_ = selected.buffer;
        detailBuffer_ = selected.buffer;
        waveSampleCount_ = selected.sampleCount;
        detailSampleCount_ = selected.sampleCount;
        waveSamplesPerSecond_ = selected.samplesPerSecond;
        detailSamplesPerSecond_ = selected.samplesPerSecond;
        waveMipOffsets_ = selected.mipOffsets;
        waveMipCounts_ = selected.mipCounts;
        waveMipCount_ = selected.mipCount;
        // Only PWV7 3BAND carries three independent band heights. PWV5 RGB
        // carries one color plus one compact height and must use the ordinary
        // single-color contour compositor.
        threeBandActive_ = waveformMode_ == 3;
        displayWaveformActive_ = true;
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    bool setWaveformStreams(
            const uint8_t *blueRows, size_t blueCount,
            const uint8_t *rgbRows, size_t rgbCount,
            const uint8_t *threeBandRows, size_t threeBandCount,
            double samplesPerSecond, uint64_t expectedGeneration = 0) {
        if (expectedGeneration && expectedGeneration !=
                renderGeneration_.load(std::memory_order_acquire)) {
            return false;
        }
        if (!blueRows || !blueCount || !rgbRows || !rgbCount
                || !threeBandRows || !threeBandCount
                || samplesPerSecond <= 0.0) {
            return false;
        }
        std::array<std::vector<WaveSample>, 3> base;
        base[0].reserve(blueCount);
        base[1].reserve(rgbCount);
        base[2].reserve(threeBandCount);
        for (size_t i = 0; i < blueCount; ++i) {
            base[0].push_back(blueWaveSample(blueRows[i]));
        }
        for (size_t i = 0; i < rgbCount; ++i) {
            base[1].push_back(rgbWaveSample(rgbRows + i * 2));
        }
        for (size_t i = 0; i < threeBandCount; ++i) {
            base[2].push_back(threeBandWaveSample(
                threeBandRows + i * 3));
        }

        std::array<DisplayWaveform, 3> prepared;
        for (size_t mode = 0; mode < prepared.size(); ++mode) {
            std::vector<WaveSample> pyramid = mode == 2
                ? buildThreeBandPyramid(
                    base[mode],
                    prepared[mode].mipOffsets,
                    prepared[mode].mipCounts,
                    prepared[mode].mipCount)
                : buildWavePyramid(
                    base[mode],
                    prepared[mode].mipOffsets,
                    prepared[mode].mipCounts,
                    prepared[mode].mipCount);
            prepared[mode].buffer = [device_
                newBufferWithBytes:pyramid.data()
                    length:pyramid.size() * sizeof(WaveSample)
                    options:MTLResourceStorageModeShared];
            if (!prepared[mode].buffer) {
                setError(-46, "Metal waveform-mode buffer allocation failed");
                return false;
            }
            prepared[mode].sampleCount = base[mode].size();
            prepared[mode].samplesPerSecond =
                static_cast<float>(samplesPerSecond);
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        if (expectedGeneration && expectedGeneration !=
                renderGeneration_.load(std::memory_order_acquire)) {
            return false;
        }
        displayWaveforms_ = std::move(prepared);
        return activateWaveformModeLocked();
    }

    bool setWaveformMode(uint32_t mode) {
        if (mode < 1 || mode > 3) {
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        waveformMode_ = mode;
        // Settings load can select a mode before this track's streams arrive.
        // Preserve the intent; setWaveformStreams activates it atomically.
        return !displayWaveforms_[mode - 1].buffer
            || activateWaveformModeLocked();
    }

    uint32_t waveformMode() {
        std::lock_guard<std::mutex> lock(renderMutex_);
        return waveformMode_;
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
            const uint16_t displayed =
                static_cast<uint16_t>(
                    static_cast<uint16_t>(heights[i]) * 257u);
            const int16_t signedDisplayed = static_cast<int16_t>(
                std::lround(displayed / 65535.0 * 32767.0));
            detail.push_back({
                displayed, displayed,
                static_cast<int16_t>(-signedDisplayed),
                signedDisplayed,
            });
        }
        return installAudioDetail(
            std::move(detail), count, samplesPerSecond, expectedGeneration);
    }

    bool setAudioDetail16(
        const uint16_t *heights, size_t count, double samplesPerSecond,
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
            const float displayed = displayWaveformHeight(heights[i]);
            const int16_t signedDisplayed = static_cast<int16_t>(std::clamp(
                std::lround(displayed * 32767.0f), 0L, 32767L));
            detail.push_back({
                static_cast<uint16_t>(std::clamp(
                    std::lround(displayed * 65535.0f), 0L, 65535L)),
                static_cast<uint16_t>(std::clamp(
                    std::lround(displayed * 65535.0f), 0L, 65535L)),
                static_cast<int16_t>(-signedDisplayed),
                signedDisplayed,
            });
        }
        return installAudioDetail(
            std::move(detail), count, samplesPerSecond, expectedGeneration);
    }

    bool setAudioDetailPolarity16(
        const uint16_t *upper, const uint16_t *lower,
        size_t count, double samplesPerSecond,
        uint64_t expectedGeneration = 0,
        const int16_t *minimum = nullptr,
        const int16_t *maximum = nullptr) {
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        if (!upper || !lower || !count || samplesPerSecond <= 0) {
            return false;
        }
        const auto displaySigned = [](int16_t encoded) {
            const float magnitude = std::abs(
                static_cast<int>(encoded)) / 32767.0f;
            const float displayed = displayWaveformHeight(magnitude);
            return static_cast<int16_t>(std::clamp(
                std::lround(std::copysign(
                    displayed * 32767.0f,
                    static_cast<float>(encoded))),
                -32767L, 32767L));
        };
        if (minimum && maximum) {
            std::vector<AudioEnvelopeSample> detail;
            detail.reserve(count);
            for (size_t i = 0; i < count; ++i) {
                const float displayedUpper =
                    displayWaveformHeight(upper[i]);
                const float displayedLower =
                    displayWaveformHeight(lower[i]);
                detail.push_back({
                    static_cast<uint16_t>(std::clamp(
                        std::lround(displayedUpper * 65535.0f),
                        0L, 65535L)),
                    static_cast<uint16_t>(std::clamp(
                        std::lround(displayedLower * 65535.0f),
                        0L, 65535L)),
                    displaySigned(minimum[i]),
                    displaySigned(maximum[i]),
                });
            }
            return installAudioDetail(
                std::move(detail), count, samplesPerSecond,
                expectedGeneration);
        }
        // Compatibility for V3 records that predate signed range sections:
        // build a short coherent crest instead of exposing their raw,
        // independent maxima as a hairy trace. The result is only a temporary
        // symmetric interval until the rolling V4 recook provides the actual
        // signed minimum and maximum used by BLUE and RGB.
        const size_t crestWindow = std::max<size_t>(
            1, static_cast<size_t>(std::lround(
                samplesPerSecond * 0.001)));
        std::vector<float> crestUpper(count);
        std::vector<float> crestLower(count);
        const auto buildCrest = [&](const uint16_t *source,
                                    std::vector<float> &target) {
            const int64_t half =
                static_cast<int64_t>(crestWindow / 2);
            for (size_t i = 0; i < count; ++i) {
                double squareSum = 0.0;
                uint16_t peak = 0;
                for (size_t tap = 0; tap < crestWindow; ++tap) {
                    const int64_t rawIndex =
                        static_cast<int64_t>(i)
                        + static_cast<int64_t>(tap) - half;
                    const size_t index = static_cast<size_t>(std::clamp(
                        rawIndex, int64_t{0},
                        static_cast<int64_t>(count - 1)));
                    const double value = source[index];
                    squareSum += value * value;
                    peak = std::max(peak, source[index]);
                }
                const double rms = std::sqrt(
                    squareSum / static_cast<double>(crestWindow));
                target[i] = static_cast<float>(std::sqrt(
                    rms * static_cast<double>(peak)));
            }
        };
        buildCrest(upper, crestUpper);
        buildCrest(lower, crestLower);

        std::vector<float> positive;
        positive.reserve(count * 2);
        for (const auto *side : {&crestUpper, &crestLower}) {
            for (const float value : *side) {
                if (value > 0.0f) {
                    positive.push_back(value);
                }
            }
        }
        float ceiling = 1.0f;
        if (!positive.empty()) {
            const double rank =
                0.999 * static_cast<double>(positive.size() - 1);
            const size_t below = static_cast<size_t>(std::floor(rank));
            const size_t above = static_cast<size_t>(std::ceil(rank));
            std::nth_element(
                positive.begin(), positive.begin() + below, positive.end());
            const float lowerValue = positive[below];
            float upperValue = lowerValue;
            if (above != below) {
                std::nth_element(
                    positive.begin(), positive.begin() + above,
                    positive.end());
                upperValue = positive[above];
            }
            ceiling = std::max(
                1.0f,
                lowerValue + static_cast<float>(rank - below)
                    * (upperValue - lowerValue));
        }
        std::vector<AudioEnvelopeSample> detail;
        detail.reserve(count);
        for (size_t i = 0; i < count; ++i) {
            const float displayedUpper = displayWaveformHeight(
                std::clamp(crestUpper[i] / ceiling, 0.0f, 1.0f));
            const float displayedLower = displayWaveformHeight(
                std::clamp(crestLower[i] / ceiling, 0.0f, 1.0f));
            const int16_t fallbackMinimum = static_cast<int16_t>(std::clamp(
                -std::lround(displayedLower * 32767.0f),
                -32767L, 0L));
            const int16_t fallbackMaximum = static_cast<int16_t>(std::clamp(
                std::lround(displayedUpper * 32767.0f),
                0L, 32767L));
            detail.push_back({
                static_cast<uint16_t>(std::clamp(
                    std::lround(displayedUpper * 65535.0f), 0L, 65535L)),
                static_cast<uint16_t>(std::clamp(
                    std::lround(displayedLower * 65535.0f), 0L, 65535L)),
                minimum ? displaySigned(minimum[i]) : fallbackMinimum,
                maximum ? displaySigned(maximum[i]) : fallbackMaximum,
            });
        }
        return installAudioDetail(
            std::move(detail), count, samplesPerSecond, expectedGeneration);
    }

    bool setAudioDetailRangeMips16(
        const int16_t *minimum, const int16_t *maximum,
        const uint32_t *levelCounts, const uint32_t *levelDivisors,
        size_t levelCount, size_t baseCount, double samplesPerSecond,
        uint64_t expectedGeneration = 0) {
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        if (!minimum || !maximum || !levelCounts || !levelDivisors
                || !levelCount || levelCount > kWaveMipLevels
                || !baseCount || samplesPerSecond <= 0) {
            return false;
        }
        std::array<uint32_t, kWaveMipLevels> mipOffsets{};
        std::array<uint32_t, kWaveMipLevels> mipCounts{};
        std::array<uint32_t, kWaveMipLevels> mipDivisors{};
        size_t totalCount = 0;
        for (size_t level = 0; level < levelCount; ++level) {
            const uint32_t divisor = levelDivisors[level];
            const size_t expectedCount =
                divisor ? (baseCount + divisor - 1) / divisor : 0;
            if (!divisor || !levelCounts[level]
                    || levelCounts[level] != expectedCount
                    || totalCount > std::numeric_limits<uint32_t>::max()
                        - levelCounts[level]) {
                return false;
            }
            mipOffsets[level] = static_cast<uint32_t>(totalCount);
            mipCounts[level] = levelCounts[level];
            mipDivisors[level] = divisor;
            totalCount += levelCounts[level];
        }
        const auto displaySigned = [](int16_t encoded) {
            const float magnitude = std::abs(
                static_cast<int>(encoded)) / 32767.0f;
            const float displayed = displayWaveformHeight(magnitude);
            return static_cast<int16_t>(std::clamp(
                std::lround(std::copysign(
                    displayed * 32767.0f,
                    static_cast<float>(encoded))),
                -32767L, 32767L));
        };
        std::vector<AudioEnvelopeSample> pyramid;
        pyramid.reserve(totalCount);
        for (size_t index = 0; index < totalCount; ++index) {
            if (minimum[index] > maximum[index]) {
                return false;
            }
            const int16_t displayedMinimum = displaySigned(minimum[index]);
            const int16_t displayedMaximum = displaySigned(maximum[index]);
            const uint16_t upper = static_cast<uint16_t>(std::clamp(
                static_cast<int>(displayedMaximum) * 2, 0, 65535));
            const uint16_t lower = static_cast<uint16_t>(std::clamp(
                -static_cast<int>(displayedMinimum) * 2, 0, 65535));
            pyramid.push_back({
                upper, lower, displayedMinimum, displayedMaximum,
            });
        }
        id<MTLBuffer> buffer = [device_ newBufferWithBytes:pyramid.data()
            length:pyramid.size() * sizeof(AudioEnvelopeSample)
            options:MTLResourceStorageModeShared];
        if (!buffer) {
            setError(-43, "Metal audio-detail mip allocation failed");
            return false;
        }
        std::lock_guard<std::mutex> lock(renderMutex_);
        if (expectedGeneration
                && renderGeneration_.load(std::memory_order_acquire)
                    != expectedGeneration) {
            return false;
        }
        audioDetailBuffer_ = buffer;
        audioDetailSampleCount_ = baseCount;
        audioDetailSamplesPerSecond_ =
            static_cast<float>(samplesPerSecond);
        audioDetailMipCount_ = levelCount;
        audioDetailMipOffsets_ = mipOffsets;
        audioDetailMipCounts_ = mipCounts;
        audioDetailMipDivisors_ = mipDivisors;
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    bool installAudioDetail(
        std::vector<AudioEnvelopeSample> detail,
        size_t count, double samplesPerSecond,
        uint64_t expectedGeneration) {
        // Upper and lower polarity peaks stay independent through every
        // song-anchored mip. Use footprint-matched integer divisors instead of
        // power-of-two-only levels: choosing 8 source cells for a 4.2-cell
        // physical pixel visibly rounds away nearly half the contour detail.
        // The denser ladder always stays at or just above the pixel footprint,
        // retaining the anti-aliasing contract without that 2x oversmoothing.
        // Legacy one-height inputs are duplicated by their setters until the
        // rolling v3 cache migration replaces them.
        std::array<uint32_t, kWaveMipLevels> mipOffsets{};
        std::array<uint32_t, kWaveMipLevels> mipCounts{};
        std::array<uint32_t, kWaveMipLevels> mipDivisors{};
        std::vector<AudioEnvelopeSample> pyramid;
        size_t mipCount = 0;
        for (const uint32_t divisor : kAudioDetailMipDivisors) {
            if (mipCount >= kWaveMipLevels) {
                break;
            }
            const size_t levelCount =
                (detail.size() + divisor - 1) / divisor;
            std::vector<AudioEnvelopeSample> level;
            level.reserve(levelCount);
            for (size_t start = 0; start < detail.size();
                    start += divisor) {
                AudioEnvelopeSample peak{
                    0, 0,
                    std::numeric_limits<int16_t>::max(),
                    std::numeric_limits<int16_t>::min(),
                };
                const size_t end = std::min(
                    detail.size(), start + divisor);
                for (size_t i = start; i < end; ++i) {
                    peak.upper = std::max(peak.upper, detail[i].upper);
                    peak.lower = std::max(peak.lower, detail[i].lower);
                    peak.minimum = std::min(
                        peak.minimum, detail[i].minimum);
                    peak.maximum = std::max(
                        peak.maximum, detail[i].maximum);
                }
                level.push_back(peak);
            }
            mipOffsets[mipCount] = static_cast<uint32_t>(pyramid.size());
            mipCounts[mipCount] = static_cast<uint32_t>(level.size());
            mipDivisors[mipCount] = divisor;
            pyramid.insert(pyramid.end(), level.begin(), level.end());
            ++mipCount;
            if (levelCount <= 1) {
                break;
            }
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
        audioDetailMipDivisors_ = mipDivisors;
        waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
        return true;
    }

    void setUniformPosition(
            Uniforms &uniforms, double exactFrame, double rate) {
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

        const double wavePosition =
            exactFrame
            * static_cast<double>(uniforms.samplesPerSecond)
            / rate;
        const double waveWhole = std::floor(std::max(0.0, wavePosition));
        uniforms.waveCurrentIndex = static_cast<uint32_t>(std::min(
            waveWhole,
            static_cast<double>(std::numeric_limits<uint32_t>::max())));
        uniforms.waveCurrentPhase = static_cast<float>(
            wavePosition - waveWhole);

        // Legacy caches were reduced on the trimmed decoder clock. Compact
        // supplements already include the validated source lead-in.
        const double detailFrame = exactFrame
            - (uniforms.audioDetailSamplesPerSecond > 1000.5
               ? static_cast<double>(sourceLeadFrames_.load()) : 0.0);
        const double detailPosition =
            detailFrame
            * static_cast<double>(uniforms.audioDetailSamplesPerSecond)
            / rate;
        const double detailWhole = std::floor(
            std::max(0.0, detailPosition));
        uniforms.audioDetailCurrentIndex = static_cast<uint32_t>(std::min(
            detailWhole,
            static_cast<double>(std::numeric_limits<uint32_t>::max())));
        uniforms.audioDetailCurrentPhase = static_cast<float>(
            detailPosition - detailWhole);
    }

    void draw(CAMetalDisplayLinkUpdate *update) {
        if (!pipeline_ || !attributePipeline_ || !update.drawable) {
            return;
        }
        const CFTimeInterval displayCallbackTime = CACurrentMediaTime();
        // Overview and ultra buffers are prepared independently but become
        // one visible waveform. Skip presentation while a grouped upload is
        // in flight so a long set cannot briefly paint the coarse silhouette
        // and then visibly change size when ultra geometry arrives.
        if (waveformUpdateDepth_.load(std::memory_order_acquire) > 0) {
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
        const CGSize size = metalLayer_.bounds.size;
        if (size.width <= 1 || size.height <= 1) {
            return;
        }
        id<MTLCommandBuffer> commands = [commandQueue_ commandBuffer];

        const bool previewActive =
            previewActive_.load(std::memory_order_acquire);
        const double exactFrame = previewActive
            ? static_cast<double>(
                previewFrame_.load(std::memory_order_acquire))
            : predictedFrame(update.targetPresentationTimestamp);
        const uint64_t submittedFrame = static_cast<uint64_t>(
            std::llround(std::max(0.0, exactFrame)));
        const uint64_t submittedGeneration =
            renderGeneration_.load(std::memory_order_acquire);
        const double rate = std::max(1.0, sampleRate_.load(std::memory_order_acquire));
        const float drawableScale = static_cast<float>(
            update.drawable.texture.width / std::max(1.0, size.width));
        Uniforms uniforms{};
        const double currentSeconds = exactFrame / rate;
        uniforms.pixelsPerSecond =
            zoomPixelsPerSecond_.load(std::memory_order_acquire);
        uniforms.widthPoints = static_cast<float>(size.width);
        uniforms.heightPoints = static_cast<float>(size.height);
        uniforms.drawableScale = drawableScale;
        {
            std::lock_guard<std::mutex> lock(presentedFrames_->mutex);
            if (presentedFrames_->generation == submittedGeneration) {
                presentedFrames_->scheduledTarget =
                    update.targetPresentationTimestamp;
                presentedFrames_->scheduledFrame = submittedFrame;
            }
        }
        uniforms.durationSeconds = static_cast<float>(
            durationFrames_.load(std::memory_order_acquire) / rate);
        BeatPosition currentBeat{};
        bool hasCurrentBeat = false;
        id<MTLBuffer> waveBuffer = nil;
        id<MTLBuffer> audioDetailBuffer = nil;
        id<MTLBuffer> gridBuffer = nil;
        id<MTLBuffer> sessionCueBuffer = nil;
        id<MTLBuffer> cueBuffer = nil;
        NSUInteger gridVertexCount = 0;
        NSUInteger sessionCueVertexCount = 0;
        NSUInteger cueVertexCount = 0;
        {
            std::lock_guard<std::mutex> lock(renderMutex_);
            uniforms.samplesPerSecond = waveSamplesPerSecond_;
            uniforms.sampleCount = static_cast<uint32_t>(
                std::min<NSUInteger>(
                    waveSampleCount_, std::numeric_limits<uint32_t>::max()));
            uniforms.waveMipCount = static_cast<uint32_t>(waveMipCount_);
            std::copy(
                waveMipOffsets_.begin(),
                waveMipOffsets_.end(),
                uniforms.waveMipOffsets);
            std::copy(
                waveMipCounts_.begin(),
                waveMipCounts_.end(),
                uniforms.waveMipCounts);
            uniforms.drawableWidthPixels = static_cast<uint32_t>(
                std::min<NSUInteger>(
                    update.drawable.texture.width,
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
            std::copy(
                audioDetailMipDivisors_.begin(),
                audioDetailMipDivisors_.end(),
                uniforms.audioDetailMipDivisors);
            uniforms.threeBandActive = threeBandActive_ ? 1u : 0u;
            uniforms.waveformMode =
                displayWaveformActive_ ? waveformMode_ : 0u;
            waveBuffer = waveBuffer_;
            audioDetailBuffer = audioDetailBuffer_;
            gridBuffer = gridBuffer_;
            sessionCueBuffer = sessionCueBuffer_;
            cueBuffer = cueBuffer_;
            gridVertexCount = gridVertexCount_;
            sessionCueVertexCount = sessionCueVertexCount_;
            cueVertexCount = cueVertexCount_;
            hasCurrentBeat = rulerPosition(currentSeconds, currentBeat);
        }
        setUniformPosition(uniforms, exactFrame, rate);

        std::shared_ptr<FrameTraceState> frameTrace;
        uint32_t frameTraceIndex = std::numeric_limits<uint32_t>::max();
        if (frameTraceArmed_.load(std::memory_order_acquire)) {
            {
                std::lock_guard<std::mutex> lock(frameTraceMutex_);
                frameTrace = frameTrace_;
            }
            if (frameTrace) {
                std::lock_guard<std::mutex> lock(frameTrace->mutex);
                const uint32_t textureWidth = static_cast<uint32_t>(
                    update.drawable.texture.width);
                const uint32_t textureHeight = static_cast<uint32_t>(
                    update.drawable.texture.height);
                if (frameTrace->active
                        && frameTrace->submittedFrames
                            < frameTrace->maxFrames
                        && textureWidth >= frameTrace->cropWidth
                        && textureHeight >= frameTrace->cropHeight) {
                    frameTraceIndex = frameTrace->submittedFrames++;
                    FrameTraceRecord &record =
                        frameTrace->records[frameTraceIndex];
                    record.sequence = frameTraceIndex;
                    record.generation = submittedGeneration;
                    record.drawableID = update.drawable.drawableID;
                    record.sourceFrame = submittedFrame;
                    record.callbackTime = displayCallbackTime;
                    record.targetTimestamp = update.targetTimestamp;
                    record.targetPresentationTimestamp =
                        update.targetPresentationTimestamp;
                    record.pixelsPerSecond = uniforms.pixelsPerSecond;
                    record.drawableScale = drawableScale;
                    record.waveformMode = uniforms.waveformMode;
                    record.textureWidth = textureWidth;
                    record.textureHeight = textureHeight;
                    record.cropOriginX =
                        (textureWidth - frameTrace->cropWidth) / 2;
                    record.cropOriginY =
                        textureHeight - frameTrace->cropHeight;
                    uniforms.frameTraceMarkerActive = 1;
                    uniforms.frameTraceSequence = frameTraceIndex;
                    if (frameTrace->submittedFrames
                            >= frameTrace->maxFrames) {
                        frameTrace->active = false;
                        frameTraceArmed_.store(
                            false, std::memory_order_release);
                    }
                }
            }
        }

        // Decide the high-detail silhouette and hue once on a fixed global
        // song lattice, then translate those attributes smoothly. The old
        // direct path re-integrated a different screen-pixel window every
        // frame; song-locking the supplied recording measured about three
        // physical pixels of contour change after its motion was removed.
        // Four song-space attributes per physical pixel preserve continuous
        // subpixel travel at every zoom. A one-attribute close mode retained
        // the exact same raster after integer motion alignment, but dense
        // real-track spikes visibly strobed as that raster advanced in 25/26
        // pixel steps at maximum zoom. Oversampling height/hue (never alpha)
        // keeps the same footprint-safe detail while allowing the final
        // drawable to reconstruct its continuously moving contour.
        const float attributeSamplesPerPixel =
            kWaveAttributeSmoothSamplesPerPixel;
        const NSUInteger visibleAttributeSamples =
            update.drawable.texture.width
            * static_cast<NSUInteger>(attributeSamplesPerPixel);
        // Both the center and left edge must lie on canonical clock blocks.
        const NSUInteger attributeAlignment = 2 * kWaveAttributeClockBlock;
        const NSUInteger desiredAttributeCount =
            ((visibleAttributeSamples
                + kWaveAttributeMarginPixels
                    * static_cast<NSUInteger>(attributeSamplesPerPixel)
                + attributeAlignment - 1)
                / attributeAlignment) * attributeAlignment;
        if (!waveAttributeBuffer_
                || waveAttributeCount_ != desiredAttributeCount) {
            waveAttributeBuffer_ = [device_
                newBufferWithLength:
                    desiredAttributeCount * sizeof(float) * 8
                options:MTLResourceStorageModePrivate];
            waveAttributeCount_ = waveAttributeBuffer_
                ? desiredAttributeCount : 0;
            waveAttributeRevision_ = 0;
        }
        const uint64_t waveformRevision =
            waveformRevision_.load(std::memory_order_acquire);
        const double exactAttributeCenter =
            exactFrame / rate
            * static_cast<double>(uniforms.pixelsPerSecond)
            * static_cast<double>(drawableScale)
            * static_cast<double>(attributeSamplesPerPixel);
        const double availableMargin = waveAttributeCount_
            > visibleAttributeSamples
            ? static_cast<double>(
                waveAttributeCount_ - visibleAttributeSamples) * 0.5
            : 0.0;
        const bool attributeNeedsPrint =
            waveAttributeBuffer_
            && waveBuffer
            && uniforms.sampleCount
            && (waveAttributeRevision_ != waveformRevision
                || waveAttributeZoom_ != uniforms.pixelsPerSecond
                || waveAttributeScale_ != drawableScale
                || waveAttributeSamplesPerPixel_
                    != attributeSamplesPerPixel
                || std::abs(
                    exactAttributeCenter
                    - static_cast<double>(waveAttributeCenter_))
                    > availableMargin * 0.5);
        if (attributeNeedsPrint) {
            // Every overlap uses the same canonical 128-attribute clock blocks.
            // Recomputing a held source cell from different floating-point
            // tile origins can select opposite sides of an exact cell boundary.
            const int64_t nextAttributeCenter = static_cast<int64_t>(
                std::floor(exactAttributeCenter / kWaveAttributeClockBlock))
                * static_cast<int64_t>(kWaveAttributeClockBlock);
            Uniforms attributeUniforms = uniforms;
            attributeUniforms.waveAttributeCount =
                static_cast<uint32_t>(std::min<NSUInteger>(
                    waveAttributeCount_,
                    std::numeric_limits<uint32_t>::max()));
            attributeUniforms.waveAttributeSamplesPerPixel =
                attributeSamplesPerPixel;
            std::vector<WaveAttributeClock> clocks(
                (waveAttributeCount_ + kWaveAttributeClockBlock - 1)
                    / kWaveAttributeClockBlock);
            const int64_t firstAttribute = nextAttributeCenter
                - static_cast<int64_t>(waveAttributeCount_ / 2);
            const double attributesPerSecond =
                static_cast<double>(uniforms.pixelsPerSecond)
                * drawableScale * attributeSamplesPerPixel;
            for (size_t block = 0; block < clocks.size(); ++block) {
                const int64_t songAttribute = firstAttribute
                    + static_cast<int64_t>(block * kWaveAttributeClockBlock);
                Uniforms clockUniforms = uniforms;
                setUniformPosition(clockUniforms,
                    static_cast<double>(songAttribute) / attributesPerSecond * rate,
                    rate);
                clocks[block] = {clockUniforms.waveCurrentIndex,
                    clockUniforms.waveCurrentPhase,
                    clockUniforms.audioDetailCurrentIndex,
                    clockUniforms.audioDetailCurrentPhase};
            }
            id<MTLBuffer> clockBuffer = [device_
                newBufferWithBytes:clocks.data()
                length:clocks.size() * sizeof(WaveAttributeClock)
                options:MTLResourceStorageModeShared];
            if (!clockBuffer) {
                setError(-43, "Metal waveform clock allocation failed");
                return;
            }
            waveAttributeCenter_ = nextAttributeCenter;
            id<MTLComputeCommandEncoder> compute =
                [commands computeCommandEncoder];
            [compute setComputePipelineState:attributePipeline_];
            [compute setBuffer:waveBuffer offset:0 atIndex:0];
            [compute setBytes:&attributeUniforms
                       length:sizeof(attributeUniforms)
                      atIndex:1];
            [compute setBuffer:
                (audioDetailBuffer ? audioDetailBuffer : waveBuffer)
                offset:0 atIndex:4];
            [compute setBuffer:waveAttributeBuffer_ offset:0 atIndex:5];
            [compute setBuffer:clockBuffer offset:0 atIndex:6];
            const NSUInteger threads = std::min<NSUInteger>(
                256, attributePipeline_.maxTotalThreadsPerThreadgroup);
            [compute dispatchThreads:MTLSizeMake(
                    waveAttributeCount_, 1, 1)
                threadsPerThreadgroup:MTLSizeMake(threads, 1, 1)];
            [compute endEncoding];
            waveAttributeRevision_ = waveformRevision;
            waveAttributeZoom_ = uniforms.pixelsPerSecond;
            waveAttributeScale_ = drawableScale;
            waveAttributeSamplesPerPixel_ = attributeSamplesPerPixel;
        }
        uniforms.waveAttributeCount =
            static_cast<uint32_t>(std::min<NSUInteger>(
                waveAttributeCount_,
                std::numeric_limits<uint32_t>::max()));
        // The native Gunslinger reference retains one logical-pixel raster
        // phase (two physical pixels on Retina). Use that motion unit at
        // RGB 576/864; other RGB stops and 3BAND remain continuous. BLUE keeps
        // physical-pixel steps wide and logical-point steps close.
        const bool continuousWaveform = uniforms.waveformMode == 3
            || (uniforms.waveformMode == 2
                && uniforms.pixelsPerSecond != 576.0f
                && uniforms.pixelsPerSecond != 864.0f);
        const double displayPixelStep = uniforms.pixelsPerSecond <= 384.0f
            ? 1.0 : drawableScale;
        uniforms.waveAttributeCurrentOffset = static_cast<float>(
            (continuousWaveform
                ? exactAttributeCenter
                : std::round(exactAttributeCenter
                    / (attributeSamplesPerPixel * displayPixelStep))
                    * attributeSamplesPerPixel * displayPixelStep)
            - static_cast<double>(waveAttributeCenter_));
        uniforms.waveAttributeSamplesPerPixel =
            attributeSamplesPerPixel;
        uniforms.waveAttributeValid =
            waveAttributeBuffer_
            && waveAttributeRevision_ == waveformRevision
            && waveAttributeZoom_ == uniforms.pixelsPerSecond
            && waveAttributeScale_ == drawableScale
            && waveAttributeSamplesPerPixel_
                == attributeSamplesPerPixel
            ? 1u : 0u;
        if (frameTraceIndex != std::numeric_limits<uint32_t>::max()) {
            std::lock_guard<std::mutex> lock(frameTrace->mutex);
            frameTrace->records[frameTraceIndex].attributeOffset =
                uniforms.waveAttributeCurrentOffset;
        }

        MTLRenderPassDescriptor *pass =
            [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = update.drawable.texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        // Clear values for an sRGB attachment are linear-light inputs.
        // These are the exact sRGB decodes of display codes (13, 14, 18), so
        // untouched pixels retain the established #0d0e12 appearance.
        pass.colorAttachments[0].clearColor = MTLClearColorMake(
            0.004024717, 0.004391442, 0.006048833, 1.0);
        id<MTLRenderCommandEncoder> encoder =
            [commands renderCommandEncoderWithDescriptor:pass];
        if (waveBuffer && uniforms.sampleCount) {
            [encoder setRenderPipelineState:pipeline_];
            [encoder setFragmentBuffer:waveBuffer offset:0 atIndex:0];
            [encoder setFragmentBuffer:
                (audioDetailBuffer ? audioDetailBuffer : waveBuffer)
                offset:0 atIndex:4];
            [encoder setFragmentBuffer:
                (waveAttributeBuffer_ ? waveAttributeBuffer_ : waveBuffer)
                offset:0 atIndex:5];
            [encoder setFragmentBytes:&uniforms
                               length:sizeof(uniforms)
                              atIndex:1];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                        vertexStart:0 vertexCount:3];
        }
        if (gridBuffer && gridVertexCount) {
            [encoder setRenderPipelineState:gridPipeline_];
            [encoder setVertexBuffer:gridBuffer offset:0 atIndex:0];
            [encoder setVertexBytes:&uniforms
                             length:sizeof(uniforms)
                            atIndex:1];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                        vertexStart:0 vertexCount:gridVertexCount];
        }
        if (sessionCueBuffer && sessionCueVertexCount) {
            [encoder setRenderPipelineState:gridPipeline_];
            [encoder setVertexBuffer:sessionCueBuffer offset:0 atIndex:0];
            [encoder setVertexBytes:&uniforms
                             length:sizeof(uniforms)
                            atIndex:1];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                        vertexStart:0
                        vertexCount:sessionCueVertexCount];
        }
        if (cueBuffer && cueVertexCount) {
            [encoder setRenderPipelineState:gridPipeline_];
            [encoder setVertexBuffer:cueBuffer offset:0 atIndex:0];
            [encoder setVertexBytes:&uniforms
                             length:sizeof(uniforms)
                            atIndex:1];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangle
                        vertexStart:0 vertexCount:cueVertexCount];
        }
        [encoder setVertexBytes:&uniforms
                         length:sizeof(uniforms)
                        atIndex:1];
        drawPlayhead(
            encoder, uniforms, hasCurrentBeat ? &currentBeat : nullptr);
        [encoder endEncoding];

        if (frameTraceIndex != std::numeric_limits<uint32_t>::max()) {
            FrameTraceRecord record;
            {
                std::lock_guard<std::mutex> lock(frameTrace->mutex);
                record = frameTrace->records[frameTraceIndex];
            }
            id<MTLBlitCommandEncoder> blit =
                [commands blitCommandEncoder];
            [blit copyFromTexture:update.drawable.texture
                      sourceSlice:0
                      sourceLevel:0
                     sourceOrigin:MTLOriginMake(
                         record.cropOriginX, record.cropOriginY, 0)
                       sourceSize:MTLSizeMake(
                           frameTrace->cropWidth,
                           frameTrace->cropHeight,
                           1)
                         toBuffer:frameTrace->pixels
                destinationOffset:
                    frameTrace->frameStride * frameTraceIndex
           destinationBytesPerRow:frameTrace->bytesPerRow
         destinationBytesPerImage:frameTrace->frameStride];
            [blit endEncoding];
            [commands addCompletedHandler:^(id<MTLCommandBuffer> buffer) {
                std::lock_guard<std::mutex> lock(frameTrace->mutex);
                FrameTraceRecord &completed =
                    frameTrace->records[frameTraceIndex];
                if (!completed.gpuCompleted) {
                    completed.gpuStartTime = buffer.GPUStartTime;
                    completed.gpuEndTime = buffer.GPUEndTime;
                    completed.gpuCompleted = true;
                    ++frameTrace->gpuCompletedFrames;
                    frameTrace->completed.notify_all();
                }
            }];
        }
        // CAMetalDisplayLink already owns the presentation schedule for this
        // drawable; an explicit presentAtTime is invalid in this mode.
        const std::shared_ptr<PresentedFrameState> presented =
            presentedFrames_;
        const CFTimeInterval submittedTarget =
            update.targetPresentationTimestamp;
        [update.drawable addPresentedHandler:^(id<MTLDrawable> drawable) {
            const CFTimeInterval actualTime = drawable.presentedTime;
            if (frameTraceIndex != std::numeric_limits<uint32_t>::max()) {
                std::lock_guard<std::mutex> lock(frameTrace->mutex);
                FrameTraceRecord &completed =
                    frameTrace->records[frameTraceIndex];
                if (!completed.presentationCompleted) {
                    completed.presentedCallbackTime = CACurrentMediaTime();
                    completed.actualPresentedTime = actualTime;
                    completed.presentationCompleted = true;
                    ++frameTrace->presentationCompletedFrames;
                    frameTrace->completed.notify_all();
                }
            }
            if (actualTime <= 0.0) {
                return;  // Metal documents zero for a dropped drawable.
            }
            {
                std::lock_guard<std::mutex> lock(presented->mutex);
                if (presented->generation != submittedGeneration) {
                    return;
                }
                if (actualTime < presented->latestTime) {
                    return;
                }
                presented->latestTime = actualTime;
                presented->latestFrame = submittedFrame;
            }
            const uint64_t actualNs = static_cast<uint64_t>(
                std::llround(actualTime * 1e9));
            uint64_t expectedFirst = 0;
            presented->firstTimeNs.compare_exchange_strong(
                expectedFirst, actualNs, std::memory_order_acq_rel);
            const uint64_t previous = presented->lastTimeNs.exchange(
                actualNs, std::memory_order_acq_rel);
            if (previous && actualNs > previous) {
                const uint64_t gap = actualNs - previous;
                uint64_t current = presented->maxGapNs.load(
                    std::memory_order_acquire);
                while (gap > current
                        && !presented->maxGapNs.compare_exchange_weak(
                            current, gap, std::memory_order_acq_rel)) {
                }
            }
            presented->count.fetch_add(1, std::memory_order_acq_rel);
            if (submittedTarget > 0.0) {
                const int64_t errorUs = static_cast<int64_t>(std::llround(
                    (actualTime - submittedTarget) * 1e6));
                presented->errorCount.fetch_add(
                    1, std::memory_order_acq_rel);
                presented->errorSumUs.fetch_add(
                    errorUs, std::memory_order_acq_rel);
                presented->errorSumSqUs.fetch_add(
                    errorUs * errorUs, std::memory_order_acq_rel);
                const int64_t magnitude = std::abs(errorUs);
                int64_t peak = presented->errorMaxAbsUs.load(
                    std::memory_order_acquire);
                while (magnitude > peak
                        && !presented->errorMaxAbsUs.compare_exchange_weak(
                            peak, magnitude, std::memory_order_acq_rel)) {
                }
            }
        }];
        [commands presentDrawable:update.drawable];
        [commands commit];
        if (!continuous
                && !displayDirty_.load(std::memory_order_acquire)) {
            displayLink_.paused = YES;
        }
    }

private:
    static void addBeatGrid(
        std::vector<GridVertex> &v, uint32_t millis, bool downbeat,
        const double *beats, size_t count, size_t index) {
        // Use local spacing, not a guessed constant BPM: variable-tempo grids
        // get the same visual density policy without changing any timestamp.
        double spacing = count > 1
            ? (index + 1 < count ? beats[index + 1] - beats[index]
                                : beats[index] - beats[index - 1])
            : 0.0;
        if (!std::isfinite(spacing) || spacing < 0.0) spacing = 0.0;
        const size_t first = v.size();
        const float halfWidth = downbeat ? 0.55f : 0.45f;
        addGridRect(v, millis, 0.0f, -halfWidth, kZoomTop,
                    halfWidth, 4096.0f,
                    downbeat ? 1.0f : 0.47f,
                    downbeat ? 0.22f : 0.53f,
                    downbeat ? 0.34f : 0.62f,
                    1.0f, false, true);
        for (size_t i = first; i < v.size(); ++i) {
            v[i].beatSpacingSeconds = static_cast<float>(spacing);
            v[i].beatKind = downbeat ? 2u : 1u;
        }
        if (downbeat) {
            addGridTriangle(v, millis, 0.0f,
                            -2.0f, kZoomTop, 2.0f, kZoomTop,
                            0.0f, kZoomTop + 2.5f,
                            1.0f, 0.22f, 0.34f, 1.0f);
            addGridTriangle(v, millis, 0.0f,
                            -2.0f, -0.01f, 2.0f, -0.01f,
                            0.0f, -2.5f,
                            1.0f, 0.22f, 0.34f, 1.0f);
        }
    }

    static void addGridRect(
        std::vector<GridVertex> &v, uint32_t tMillis, float tPhase,
        float x0, float y0, float x1, float y1,
        float r, float g, float b, float a, bool overview = false,
        bool analyticVertical = false) {
        const uint32_t absolute = overview ? 1u : 0u;
        const float center = (x0 + x1) * 0.5f;
        const float halfWidth = (x1 - x0) * 0.5f;
        // The ordinary triangle rasterizer tests only pixel centers, so a
        // one-point timing line otherwise snaps between whole Retina pixels
        // while the waveform underneath moves fractionally. Expand the
        // geometry by one physical-pixel-safe half point and carry its signed
        // local coordinate to the fragment shader for exact box coverage.
        const float fringe = analyticVertical ? 0.5f : 0.0f;
        const float drawX0 = x0 - fringe;
        const float drawX1 = x1 + fringe;
        const float lineHalf = analyticVertical ? halfWidth : 0.0f;
        const float lineX0 = analyticVertical ? drawX0 - center : 0.0f;
        const float lineX1 = analyticVertical ? drawX1 - center : 0.0f;
        GridVertex p0{
            tMillis, tPhase, drawX0, y0, r, g, b, a, absolute,
            lineX0, lineHalf};
        GridVertex p1{
            tMillis, tPhase, drawX1, y0, r, g, b, a, absolute,
            lineX1, lineHalf};
        GridVertex p2{
            tMillis, tPhase, drawX1, y1, r, g, b, a, absolute,
            lineX1, lineHalf};
        GridVertex p3{
            tMillis, tPhase, drawX0, y1, r, g, b, a, absolute,
            lineX0, lineHalf};
        v.insert(v.end(), {p0, p1, p2, p0, p2, p3});
    }

    static void addGridTriangle(
        std::vector<GridVertex> &v, uint32_t tMillis, float tPhase,
        float x0, float y0, float x1, float y1, float x2, float y2,
        float r, float g, float b, float a, bool overview = false) {
        const uint32_t absolute = overview ? 1u : 0u;
        v.insert(v.end(), {
            {tMillis, tPhase, x0, y0, r, g, b, a, absolute, 0.0f, 0.0f},
            {tMillis, tPhase, x1, y1, r, g, b, a, absolute, 0.0f, 0.0f},
            {tMillis, tPhase, x2, y2, r, g, b, a, absolute, 0.0f, 0.0f},
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
        std::vector<GridVertex> &v = overlayScratch_;
        v.clear();
        if (v.capacity() < 512) {
            v.reserve(512);
        }
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
            tMillis, tPhase, -5, kZoomTop, .09f,.78f,1,1,0,0,0};
        GridVertex b{
            tMillis, tPhase, 5, kZoomTop, .09f,.78f,1,1,0,0,0};
        GridVertex c{
            tMillis, tPhase, 0, kZoomTop + 7, .09f,.78f,1,1,0,0,0};
        v.insert(v.end(), {a,b,c});
        if (beat) {
            const std::string barText = std::to_string(beat->bar);
            const std::string label =
                beat->beat == 255 ? barText : barText + "." + std::to_string(beat->beat);
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
        addButton(0.0f, false, uniforms.pixelsPerSecond > 24.1f);
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
        // A future hardware timestamp may extrapolate before the launch/seek
        // point. Hold that point until audible time catches up; steady playback
        // keeps the same continuous PLL and must not jump backward on Play.
        const double start = static_cast<double>(
            visualStartFrame_.load(std::memory_order_acquire));
        return std::min(duration, std::max(start, predicted));
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
            // Rekordbox's captured waveform anchors use the display's P3
            // primaries directly (red 255/0/0, high blue 127/162/255).
            // Without an explicit layer color space, macOS treated these as
            // sRGB and converted them to 234/51/35 and 134/162/248 on the
            // built-in P3 panel. Keep the sRGB transfer attachment for linear
            // coverage blending, but declare the authored output primaries.
            metalLayer_.pixelFormat = MTLPixelFormatBGRA8Unorm_sRGB;
            CGColorSpaceRef displayP3 =
                CGColorSpaceCreateWithName(kCGColorSpaceDisplayP3);
            metalLayer_.colorspace = displayP3;
            CGColorSpaceRelease(displayP3);
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
struct AudioEnvelopeSample {
    ushort upper;
    ushort lower;
    short minimum;
    short maximum;
};
struct GridVertex { uint songMillis; float songMillisPhase;
                    float xOffsetPixels; float yPixels;
                    float r; float g; float b; float a;
                    uint overview;
                    float lineDistancePoints;
                    float lineHalfWidthPoints;
                    float beatSpacingSeconds; uint beatKind; };
struct Uniforms { float currentSeconds;
                  uint currentMillis; float currentMillisPhase;
                  float pixelsPerSecond;
                  float widthPoints; float heightPoints;
                  float samplesPerSecond; uint sampleCount;
                  uint waveCurrentIndex; float waveCurrentPhase;
                  uint waveMipCount;
                  uint waveMipOffsets[32];
                  uint waveMipCounts[32];
                  float drawableScale; uint drawableWidthPixels;
                  float durationSeconds;
                  float detailSamplesPerSecond; uint detailSampleCount;
                  float audioDetailSamplesPerSecond;
                  uint audioDetailSampleCount;
                  uint audioDetailCurrentIndex;
                  float audioDetailCurrentPhase;
                  uint audioDetailMipCount;
                  uint audioDetailMipOffsets[32];
                  uint audioDetailMipCounts[32];
                  uint audioDetailMipDivisors[32];
                  uint waveAttributeCount;
                  float waveAttributeCurrentOffset;
                  float waveAttributeSamplesPerPixel;
                  uint waveAttributeValid;
                  uint threeBandActive;
                  uint waveformMode;
                  uint frameTraceMarkerActive;
                  uint frameTraceSequence;
                  };
constant uint kAttributeProfileCapacity = 7u;
struct Out {
    float4 position [[position]];
    float4 color;
    float lineDistancePixels;
    float lineHalfWidthPixels;
    float laneY;
    float laneHeight [[flat]];
    float2 beatStyle [[flat]];
};
struct WaveTap {
    float height;
    float3 weightedColor;
    float colorWeight;
    float valid;
};
struct EnvelopeTap {
    float upper;
    float lower;
    float minimum;
    float maximum;
    float valid;
};
struct WaveSurface {
    float coverage;
    float3 weightedColor;
    float colorWeight;
    float valid;
};
struct CoverageMoments {
    float area;
    float firstMoment;
};
struct IntervalSurface {
    float coverage;
    float3 premultipliedColor;
};
constant uint kWaveAttributeClockBlock = 128u;
struct WaveAttributeClock {
    uint waveIndex; float wavePhase;
    uint detailIndex; float detailPhase;
};
struct WaveAttribute {
    float4 colorAndUpper;
    // Keep the premultiplication weight independent of signed geometry.
    float4 lowerAndColorWeight;
};
inline float vivid_wave_coverage(float coverage) {
    // Keep thin contours bright without a nonzero visibility floor. A hard
    // floor makes a subpixel edge jump from background straight to vivid
    // color as it crosses a pixel, which reads as shimmer and shaking during
    // otherwise continuous motion. This cubic ease-out is continuous at both
    // ends, preserves full interior color, and changes only compositing—not
    // waveform height, timing, LOD, or hue.
    const float value = saturate(coverage);
    const float inverse = 1.0 - value;
    return 1.0 - inverse * inverse * inverse;
}
inline float3 display_srgb_to_linear(float3 value) {
    const float3 lower = value / 12.92;
    const float3 upper = pow(
        (value + 0.055) / 1.055, float3(2.4));
    return select(upper, lower, value <= float3(0.04045));
}
inline float3 three_band_color(float3 bands) {
    // PWV7 channels arrive as low/mid/high geometry. The compact overview
    // cannot show independent vertical layers, so use the color belonging to
    // the strongest native band. The scrolling lane performs the complete
    // layered Pioneer compositor below.
    const float3 lowColor = float3(0.0, 85.0 / 255.0, 225.0 / 255.0);
    const float3 midColor = float3(1.0, 166.0 / 255.0, 0.0);
    const float3 highColor = float3(1.0);
    if (bands.b >= bands.r && bands.b >= bands.g) {
        return highColor;
    }
    return bands.g >= bands.r ? midColor : lowColor;
}
vertex Out wave_surface_vertex(uint id [[vertex_id]]) {
    const float2 positions[3] = {
        float2(-1.0, -1.0), float2(3.0, -1.0), float2(-1.0, 3.0)
    };
    Out o;
    o.position = float4(positions[id], 0.0, 1.0);
    o.color = float4(0.0);
    o.lineDistancePixels = 0.0;
    o.lineHalfWidthPixels = 0.0;
    o.laneY = 0.0;
    o.laneHeight = 0.0;
    o.beatStyle = float2(0.0);
    return o;
}

inline uint mip_divisor(uint level) {
    return 1u << min(level, 30u);
}

inline float4 cubic_bspline_weights(float phase) {
    // Positive cubic B-spline reconstruction is deliberately spatial, not
    // temporal. Linear interpolation left a hard slope change at every
    // song-anchored mip sample; those corners were the "wiggly ends" visible
    // while the waveform moved. These weights retain one stable lattice,
    // never overshoot its positive envelope, and make both silhouette and
    // premultiplied RGB continuously differentiable as song phase advances.
    const float t = saturate(phase);
    const float t2 = t * t;
    const float t3 = t2 * t;
    return float4(
        (1.0 - 3.0 * t + 3.0 * t2 - t3) / 6.0,
        (4.0 - 6.0 * t2 + 3.0 * t3) / 6.0,
        (1.0 + 3.0 * t + 3.0 * t2 - 3.0 * t3) / 6.0,
        t3 / 6.0);
}

inline WaveTap wave_tap(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint level) {
    WaveTap result{0.0, float3(0.0), 0.0, 0.0};
    if (levelCount == 0) {
        return result;
    }
    const uint divisor = mip_divisor(level);
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal) / float(divisor);
    const int whole = int(floor(local));
    const int left = int(anchor) + whole;
    if (left < 0 || left >= int(levelCount)) {
        return result;
    }
    const float4 weights = cubic_bspline_weights(local - float(whole));
    for (int support = -1; support <= 2; ++support) {
        const uint index = levelOffset + uint(clamp(
            left + support, 0, int(levelCount) - 1));
        const WaveSample sample = samples[index];
        const float weight = weights[uint(support + 1)];
        result.height += sample.height * weight;
        result.weightedColor +=
            float3(sample.r, sample.g, sample.b)
            * sample.height * weight;
        result.colorWeight += sample.height * weight;
    }
    result.valid = 1.0;
    return result;
}

inline EnvelopeTap envelope_tap(
        const device AudioEnvelopeSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint divisor) {
    EnvelopeTap result{0.0, 0.0, 0.0, 0.0, 0.0};
    if (levelCount == 0) {
        return result;
    }
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal) / float(divisor);
    const int whole = int(floor(local));
    const int left = int(anchor) + whole;
    if (left < 0 || left >= int(levelCount)) {
        return result;
    }
    const float4 weights = cubic_bspline_weights(local - float(whole));
    for (int support = -1; support <= 2; ++support) {
        const uint index = levelOffset + uint(clamp(
            left + support, 0, int(levelCount) - 1));
        const float weight = weights[uint(support + 1)];
        result.upper += float(samples[index].upper) / 65535.0 * weight;
        result.lower += float(samples[index].lower) / 65535.0 * weight;
        result.minimum +=
            float(samples[index].minimum) / 32767.0 * weight;
        result.maximum +=
            float(samples[index].maximum) / 32767.0 * weight;
    }
    result.valid = 1.0;
    return result;
}

inline float2 envelope_support_max(
        const device AudioEnvelopeSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint divisor) {
    if (levelCount == 0) {
        return float2(0.0);
    }
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal) / float(divisor);
    const int left = int(anchor) + int(floor(local));
    if (left < 0 || left >= int(levelCount)) {
        return float2(0.0);
    }
    float2 result = float2(0.0);
    for (int support = -1; support <= 2; ++support) {
        const uint index = levelOffset + uint(clamp(
            left + support, 0, int(levelCount) - 1));
        result = max(
            result,
            float2(samples[index].upper, samples[index].lower) / 65535.0);
    }
    return result;
}

inline uint detail_lod(float footprint, uint mipCount) {
    // Keep the footprint-safe peak mip: selecting the sharper floor mip looked
    // detailed in a still but exposed a hairy, aliased contour in motion on
    // the exact real track. Fixed translation and an interpolating monotone
    // kernel remove phase redraw and slope corners; neither can make
    // above-Nyquist source detail displayable.
    return uint(clamp(
        ceil(log2(max(1.0, footprint))),
        0.0, float(mipCount - 1)));
}

inline uint audio_detail_lod(
        float footprint, constant uint *divisors, uint mipCount) {
    if (mipCount == 0) {
        return 0;
    }
    // A 6% tolerance lets a 2-cell level serve a 2.1-cell Retina pixel
    // instead of jumping to 3. The five-sample analytic profile absorbs that
    // tiny overlap while the next genuinely undersized level is rejected.
    const float required = max(1.0, footprint * 0.94);
    for (uint level = 0; level < mipCount; ++level) {
        if (float(divisors[level]) >= required) {
            return level;
        }
    }
    return mipCount - 1;
}

inline uint audio_detail_bound_lod(
        float span, constant uint *divisors, uint mipCount) {
    if (mipCount == 0) {
        return 0;
    }
    for (uint level = 0; level < mipCount; ++level) {
        if (float(divisors[level]) >= max(1.0, span)) {
            return level;
        }
    }
    return mipCount - 1;
}

inline float monotone_slope(float before, float after) {
    if (before * after <= 0.0) {
        return 0.0;
    }
    return 2.0 * before * after / (before + after);
}

inline float monotone_hermite(
        float before, float left, float right, float after, float phase) {
    const float d0 = left - before;
    const float d1 = right - left;
    const float d2 = after - right;
    const float m0 = monotone_slope(d0, d1);
    const float m1 = monotone_slope(d1, d2);
    const float t = saturate(phase);
    const float t2 = t * t;
    const float t3 = t2 * t;
    const float value =
        (2.0 * t3 - 3.0 * t2 + 1.0) * left
        + (t3 - 2.0 * t2 + t) * m0
        + (-2.0 * t3 + 3.0 * t2) * right
        + (t3 - t2) * m1;
    return clamp(value, min(left, right), max(left, right));
}

inline float3 monotone_hermite(
        float3 before, float3 left, float3 right, float3 after, float phase) {
    return float3(
        monotone_hermite(
            before.r, left.r, right.r, after.r, phase),
        monotone_hermite(
            before.g, left.g, right.g, after.g, phase),
        monotone_hermite(
            before.b, left.b, right.b, after.b, phase));
}

inline WaveTap sharp_wave_tap(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint level) {
    WaveTap result{0.0, float3(0.0), 0.0, 0.0};
    if (levelCount == 0) {
        return result;
    }
    const uint divisor = mip_divisor(level);
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal) / float(divisor);
    const int whole = int(floor(local));
    const int left = int(anchor) + whole;
    if (left < 0 || left >= int(levelCount)) {
        return result;
    }
    WaveSample support[4];
    for (int tap = -1; tap <= 2; ++tap) {
        support[tap + 1] = samples[levelOffset + uint(clamp(
            left + tap, 0, int(levelCount) - 1))];
    }
    const float phase = local - float(whole);
    result.height = monotone_hermite(
        support[0].height, support[1].height,
        support[2].height, support[3].height, phase);
    result.weightedColor = monotone_hermite(
        float3(support[0].r, support[0].g, support[0].b)
            * support[0].height,
        float3(support[1].r, support[1].g, support[1].b)
            * support[1].height,
        float3(support[2].r, support[2].g, support[2].b)
            * support[2].height,
        float3(support[3].r, support[3].g, support[3].b)
            * support[3].height,
        phase);
    result.colorWeight = result.height;
    result.valid = 1.0;
    return result;
}

inline float3 linear_wave_color(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint level) {
    if (levelCount == 0) {
        return float3(0.0);
    }
    const uint divisor = mip_divisor(level);
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal - 0.5) / float(divisor);
    const int cell = int(anchor) + int(floor(local));
    if (cell < 0 || cell >= int(levelCount)) {
        return float3(0.0);
    }
    const WaveSample a = samples[levelOffset + uint(cell)];
    const WaveSample b = samples[levelOffset + uint(min(cell + 1, int(levelCount) - 1))];
    return mix(float3(a.r, a.g, a.b), float3(b.r, b.g, b.b), local - floor(local));
}

inline float3 recover_rgb_color(
        float3 weightedColor, float colorWeight, float3 authoredColor) {
    // PWV5's five-bit height may round to zero while its RGB code and the
    // independent signed detail remain valid. Zero weight is missing color
    // evidence, not black. Positive weights must cancel exactly, even when tiny.
    return colorWeight > 0.0 ? weightedColor / colorWeight : authoredColor;
}

inline WaveTap linear_wave_tap(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint level) {
    WaveTap result{0.0, float3(0.0), 0.0, 0.0};
    if (levelCount == 0) {
        return result;
    }
    const uint divisor = mip_divisor(level);
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal) / float(divisor);
    const int left = int(anchor) + int(floor(local));
    if (left < 0 || left >= int(levelCount)) {
        return result;
    }
    const int right = min(left + 1, int(levelCount) - 1);
    const float phase = local - floor(local);
    const WaveSample leftSample =
        samples[levelOffset + uint(left)];
    const WaveSample rightSample =
        samples[levelOffset + uint(right)];
    result.height = mix(
        leftSample.height, rightSample.height, phase);
    result.weightedColor = mix(
        float3(leftSample.r, leftSample.g, leftSample.b)
            * leftSample.height,
        float3(rightSample.r, rightSample.g, rightSample.b)
            * rightSample.height,
        phase);
    result.colorWeight = result.height;
    result.valid = 1.0;
    return result;
}

inline WaveTap three_band_detail_tap(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint level,
        float pixelsPerSecond) {
    return linear_wave_tap(samples, levelOffset, levelCount,
        baseAnchor, baseLocal - 1.0, level);
}

inline EnvelopeTap sharp_envelope_tap(
        const device AudioEnvelopeSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint divisor) {
    EnvelopeTap result{0.0, 0.0, 0.0, 0.0, 0.0};
    if (levelCount == 0) {
        return result;
    }
    const uint anchor = baseAnchor / divisor;
    const uint remainder = baseAnchor % divisor;
    const float local =
        (float(remainder) + baseLocal) / float(divisor);
    const int whole = int(floor(local));
    const int left = int(anchor) + whole;
    if (left < 0 || left >= int(levelCount)) {
        return result;
    }
    float4 support[4];
    for (int tap = -1; tap <= 2; ++tap) {
        const AudioEnvelopeSample sample = samples[levelOffset + uint(clamp(
            left + tap, 0, int(levelCount) - 1))];
        support[tap + 1] = float4(
            float(sample.upper) / 65535.0,
            float(sample.lower) / 65535.0,
            float(sample.minimum) / 32767.0,
            float(sample.maximum) / 32767.0);
    }
    const float phase = local - float(whole);
    result.upper = monotone_hermite(
        support[0].x, support[1].x, support[2].x, support[3].x, phase);
    result.lower = monotone_hermite(
        support[0].y, support[1].y, support[2].y, support[3].y, phase);
    result.minimum = monotone_hermite(
        support[0].z, support[1].z, support[2].z, support[3].z, phase);
    result.maximum = monotone_hermite(
        support[0].w, support[1].w, support[2].w, support[3].w, phase);
    result.valid = 1.0;
    return result;
}

inline EnvelopeTap held_envelope_tap(
        const device AudioEnvelopeSample *samples,
        uint levelOffset, uint levelCount,
        uint baseAnchor, float baseLocal, uint divisor) {
    // Signed extrema stay on their authored song cells. The final profile
    // integral supplies spatial antialiasing; no temporal or neighbor blur.
    EnvelopeTap result{0.0, 0.0, 0.0, 0.0, 0.0};
    const int cell = int(baseAnchor / divisor) + int(floor(
        (float(baseAnchor % divisor) + baseLocal) / float(divisor)));
    if (cell < 0 || cell >= int(levelCount)) return result;
    const AudioEnvelopeSample sample = samples[levelOffset + uint(cell)];
    result.upper = float(sample.upper) / 65535.0;
    result.lower = float(sample.lower) / 65535.0;
    result.minimum = float(sample.minimum) / 32767.0;
    result.maximum = float(sample.maximum) / 32767.0;
    result.valid = 1.0;
    return result;
}

inline WaveAttribute linear_attribute(
        const device WaveAttribute *attributes, uint count, float coordinate) {
    const float bounded = clamp(
        coordinate, 0.0, max(0.0, float(count) - 1.0));
    const int left = int(floor(bounded));
    const int right = min(left + 1, int(count) - 1);
    const float phase = bounded - float(left);
    WaveAttribute result;
    result.colorAndUpper = mix(
        attributes[uint(left)].colorAndUpper,
        attributes[uint(right)].colorAndUpper,
        phase);
    result.lowerAndColorWeight = mix(
        attributes[uint(left)].lowerAndColorWeight,
        attributes[uint(right)].lowerAndColorWeight,
        phase);
    return result;
}

inline float signed_interval_occupancy(
        const device AudioEnvelopeSample *samples,
        uint fineOffset, uint fineCount, uint fineDivisor,
        uint coarseCell, uint coarseDivisor,
        float coarseMinimum, float coarseMaximum, float minimumWidth) {
    if (fineCount == 0 || fineDivisor == 0 || coarseDivisor <= fineDivisor) return 1.0;
    const uint begin = coarseCell * coarseDivisor;
    const uint end = begin + coarseDivisor;
    const uint first = begin / fineDivisor;
    const uint last = min(fineCount, (end + fineDivisor - 1u) / fineDivisor);
    // The accepted close ladders need at most three overlapping fine cells.
    if (first >= last || last - first > 4u) return 1.0;
    float fineArea = 0.0;
    float coveredSpan = 0.0;
    for (uint cell = first; cell < last; ++cell) {
        const uint left = max(begin, cell * fineDivisor);
        const uint right = min(end, (cell + 1u) * fineDivisor);
        const float overlap = float(right - left);
        const AudioEnvelopeSample fine = samples[fineOffset + cell];
        const float width = max(abs(float(fine.maximum) - float(fine.minimum)) / 32767.0, minimumWidth);
        fineArea += width * overlap;
        coveredSpan += overlap;
    }
    const float coarseWidth = max(abs(coarseMaximum - coarseMinimum), minimumWidth);
    return saturate(fineArea / max(coveredSpan * coarseWidth, 1e-8));
}

inline float3 interval_occupancy_color(float3 color, float occupancy) {
    // Encode the represented area as contrast against the unchanged background.
    // Premultiplication below keeps this attached to the fixed song lattice.
    const float3 background = float3(13.0, 14.0, 18.0) / 255.0;
    return mix(background, color, occupancy);
}

kernel void wave_attribute_kernel(
        const device WaveSample *samples [[buffer(0)]],
        constant Uniforms &u [[buffer(1)]],
        const device AudioEnvelopeSample *audioDetailSamples [[buffer(4)]],
        device WaveAttribute *attributes [[buffer(5)]],
        const device WaveAttributeClock *clocks [[buffer(6)]],
        uint id [[thread_position_in_grid]]) {
    if (id >= u.waveAttributeCount
            || u.sampleCount == 0 || u.waveMipCount == 0) {
        return;
    }
    const WaveAttributeClock clock = clocks[id / kWaveAttributeClockBlock];
    const float scale = max(1.0, u.drawableScale);
    const float samplesPerPixel =
        max(1.0, u.waveAttributeSamplesPerPixel);
    const float secondOffset =
        float(id % kWaveAttributeClockBlock)
        / max(1.0, u.pixelsPerSecond * scale * samplesPerPixel);
    const float waveFootprint = u.samplesPerSecond
        / max(1.0, u.pixelsPerSecond * scale);
    const uint requestedWaveLevel = detail_lod(
        waveFootprint, u.waveMipCount);
    // Preserve original 150-Hz PWV7 rows at close zoom.
    const uint waveLevel = requestedWaveLevel;
    const WaveTap sharpWave = u.waveformMode == 3
        ? three_band_detail_tap(
            samples,
            u.waveMipOffsets[waveLevel],
            u.waveMipCounts[waveLevel],
            clock.waveIndex,
            clock.wavePhase + secondOffset * u.samplesPerSecond,
            waveLevel, u.pixelsPerSecond)
        : sharp_wave_tap(
            samples,
            u.waveMipOffsets[waveLevel],
            u.waveMipCounts[waveLevel],
            clock.waveIndex,
            clock.wavePhase + secondOffset * u.samplesPerSecond,
            waveLevel);
    if (sharpWave.valid == 0.0) {
        attributes[id].colorAndUpper = float4(0.0);
        attributes[id].lowerAndColorWeight = float4(0.0);
        return;
    }
    // Positive cubic reconstruction removes moving mip-lattice corners at
    // 24-384 px/s. At the close stops sharpWave owns geometry instead:
    // BLUE/RGB subsequently use linear signed detail, while 3BAND linearly
    // connects its independent PWV7 band samples. The latter is essential:
    // holding each row produces coarse stair steps instead of Rekordbox's
    // clean straight slopes. Zoom is fixed during playback, so the blend never
    // changes phase from frame to frame.
    const float stableKernel = 1.0 - smoothstep(
        384.0, 576.0, u.pixelsPerSecond);
    const WaveTap smoothWave = wave_tap(
        samples,
        u.waveMipOffsets[waveLevel],
        u.waveMipCounts[waveLevel],
        clock.waveIndex,
        clock.wavePhase + secondOffset * u.samplesPerSecond,
        waveLevel);
    // The renderer accepts only the thirteen canonical zoom levels. The
    // BLUE/RGB wide and midrange stops share the symmetric same-LOD
    // [1, 2, 1] reconstruction. Preserve 3BAND's independent wide profile
    // and the accepted close facets; never filter moving final pixels.
    const float midrangeFilter =
        (u.waveformMode != 3 || u.pixelsPerSecond >= 64.0) && u.pixelsPerSecond < 384.0
        ? 1.0 : 0.0;
    const float filterSeconds =
        0.5 / max(1.0, u.pixelsPerSecond * scale);
    const float waveFilterOffset =
        filterSeconds * u.samplesPerSecond;
    const float waveLocal =
        clock.wavePhase + secondOffset * u.samplesPerSecond;
    const WaveTap leftSmoothWave = wave_tap(
        samples,
        u.waveMipOffsets[waveLevel],
        u.waveMipCounts[waveLevel],
        clock.waveIndex,
        waveLocal - waveFilterOffset,
        waveLevel);
    const WaveTap rightSmoothWave = wave_tap(
        samples,
        u.waveMipOffsets[waveLevel],
        u.waveMipCounts[waveLevel],
        clock.waveIndex,
        waveLocal + waveFilterOffset,
        waveLevel);
    const float waveFilter = midrangeFilter
        * leftSmoothWave.valid * rightSmoothWave.valid;
    WaveTap stableWave = smoothWave;
    stableWave.height = mix(
        smoothWave.height,
        (leftSmoothWave.height
            + 2.0 * smoothWave.height
            + rightSmoothWave.height) * 0.25,
        waveFilter);
    stableWave.weightedColor = mix(
        smoothWave.weightedColor,
        (leftSmoothWave.weightedColor
            + 2.0 * smoothWave.weightedColor
            + rightSmoothWave.weightedColor) * 0.25,
        waveFilter);
    stableWave.colorWeight = mix(
        smoothWave.colorWeight,
        (leftSmoothWave.colorWeight
            + 2.0 * smoothWave.colorWeight
            + rightSmoothWave.colorWeight) * 0.25,
        waveFilter);
    const float heightWeight = mix(
        sharpWave.colorWeight, stableWave.colorWeight, stableKernel);
    const float3 weightedColor = mix(
        sharpWave.weightedColor,
        stableWave.weightedColor,
        stableKernel);
    float3 color = weightedColor
        / max(heightWeight, 0.0001);
    if (u.waveformMode == 2) {
        // Interpolate full-range PWV5 channels on their authored row clock.
        const float3 crispColor = linear_wave_color(
            samples,
            u.waveMipOffsets[waveLevel],
            u.waveMipCounts[waveLevel],
            clock.waveIndex,
            clock.wavePhase + secondOffset * u.samplesPerSecond,
            waveLevel);
        color = mix(
            recover_rgb_color(weightedColor, heightWeight, crispColor), crispColor,
            smoothstep(92.0, 184.0, u.pixelsPerSecond));
    }
    float height = mix(
        sharpWave.height, stableWave.height, stableKernel);
    float upperHeight = height;
    float lowerHeight = u.waveformMode == 3 ? height : -height;
    // BLUE and RGB use close-resolution audio geometry together with their
    // authored PWV3/PWV5 amplitude and color. 3BAND remains three native
    // PWV7 layers and bypasses the audio contour.
    if (u.waveformMode != 3
            && u.audioDetailSampleCount > 0
            && u.audioDetailMipCount > 0) {
        // BLUE and RGB both use the literal signed min/max trace at close
        // resolution. Filling separate positive/negative envelopes back to
        // zero creates a false center seam and solid mirrored lobes; the
        // signed interval preserves the actual carrier crossing instead.
        const float detailFootprint =
            u.audioDetailSamplesPerSecond
            / max(1.0, u.pixelsPerSecond * scale);
        const bool logicalRGBDetail = u.waveformMode == 2
            && (u.pixelsPerSecond == 576.0 || u.pixelsPerSecond == 864.0);
        const uint physicalDetailLevel = audio_detail_lod(
            detailFootprint,
            u.audioDetailMipDivisors, u.audioDetailMipCount);
        // Match the native logical-pixel footprint at the two accepted stops.
        // Keep the original level as the color-area reference, including 1x.
        const uint detailLevel = logicalRGBDetail
            ? audio_detail_lod(detailFootprint * scale,
                u.audioDetailMipDivisors, u.audioDetailMipCount)
            : physicalDetailLevel;
        const uint detailDivisor =
            u.audioDetailMipDivisors[detailLevel];
        const float detailLocal =
            clock.detailPhase
                + secondOffset * u.audioDetailSamplesPerSecond;
        // Held extrema retain their selected song cells. Compact RGB uses
        // 2-ms detail at 384/576/864 on Retina; BLUE and all other stops retain
        // their existing choices. Final coverage still spans one physical pixel.
        EnvelopeTap sharpDetail = u.pixelsPerSecond >= 384.0
            ? held_envelope_tap(
                audioDetailSamples,
                u.audioDetailMipOffsets[detailLevel],
                u.audioDetailMipCounts[detailLevel],
                clock.detailIndex,
                detailLocal,
                detailDivisor)
            : (u.pixelsPerSecond >= 184.0
            ? held_envelope_tap(
                audioDetailSamples,
                u.audioDetailMipOffsets[detailLevel],
                u.audioDetailMipCounts[detailLevel],
                clock.detailIndex,
                detailLocal,
                detailDivisor)
            : sharp_envelope_tap(
                audioDetailSamples,
                u.audioDetailMipOffsets[detailLevel],
                u.audioDetailMipCounts[detailLevel],
                clock.detailIndex,
                detailLocal,
                detailDivisor));
        // The signed interval owns BLUE/RGB geometry, so filtering only the
        // PWV color stream cannot stabilize its boundary. At the measured
        // problem stops, apply the same symmetric song-space [1 2 1]
        // reconstruction to minimum and maximum before they enter the fixed
        // song lattice. This removes undisplayable horizontal energy without
        // filtering the final pixels or changing neighboring zoom levels.
        if (midrangeFilter > 0.0) {
            const float detailPixel =
                detailFootprint * 1.5;
            const EnvelopeTap leftDetail =
                u.pixelsPerSecond >= 184.0
                ? held_envelope_tap(
                    audioDetailSamples,
                    u.audioDetailMipOffsets[detailLevel],
                    u.audioDetailMipCounts[detailLevel],
                    clock.detailIndex,
                    detailLocal - detailPixel,
                    detailDivisor)
                : sharp_envelope_tap(
                    audioDetailSamples,
                    u.audioDetailMipOffsets[detailLevel],
                    u.audioDetailMipCounts[detailLevel],
                    clock.detailIndex,
                    detailLocal - detailPixel,
                    detailDivisor);
            const EnvelopeTap rightDetail =
                u.pixelsPerSecond >= 184.0
                ? held_envelope_tap(
                    audioDetailSamples,
                    u.audioDetailMipOffsets[detailLevel],
                    u.audioDetailMipCounts[detailLevel],
                    clock.detailIndex,
                    detailLocal + detailPixel,
                    detailDivisor)
                : sharp_envelope_tap(
                    audioDetailSamples,
                    u.audioDetailMipOffsets[detailLevel],
                    u.audioDetailMipCounts[detailLevel],
                    clock.detailIndex,
                    detailLocal + detailPixel,
                    detailDivisor);
            if (leftDetail.valid > 0.0 && rightDetail.valid > 0.0) {
                const float4 filtered = (
                    float4(
                        leftDetail.upper, leftDetail.lower,
                        leftDetail.minimum, leftDetail.maximum)
                    + 2.0 * float4(
                        sharpDetail.upper, sharpDetail.lower,
                        sharpDetail.minimum, sharpDetail.maximum)
                    + float4(
                        rightDetail.upper, rightDetail.lower,
                        rightDetail.minimum, rightDetail.maximum)) * 0.25;
                sharpDetail.upper = mix(
                    sharpDetail.upper, filtered.x, midrangeFilter);
                sharpDetail.lower = mix(
                    sharpDetail.lower, filtered.y, midrangeFilter);
                sharpDetail.minimum = mix(
                    sharpDetail.minimum, filtered.z, midrangeFilter);
                sharpDetail.maximum = mix(
                    sharpDetail.maximum, filtered.w, midrangeFilter);
            }
        }
        if (sharpDetail.valid > 0.0) {
            // A BLUE/RGB column is a signed min/max interval, not two
            // envelopes filled back to zero. Quiet tonal bins therefore form
            // a thin oscillating trace while dense/noisy bins naturally widen
            // into the solid blocks visible in Rekordbox.
            upperHeight = sharpDetail.maximum;
            lowerHeight = sharpDetail.minimum;
            height = max(abs(upperHeight), abs(lowerHeight));
            if (logicalRGBDetail && detailLevel != physicalDetailLevel) {
                const int coarseCell = int(clock.detailIndex / detailDivisor)
                    + int(floor((float(clock.detailIndex % detailDivisor) + detailLocal)
                        / float(detailDivisor)));
                if (coarseCell >= 0) {
                    const float heightScale = max(4.0, (u.heightPoints - 49.0) * 0.5 - 4.0);
                    const float minimumWidth = 1.0 / (scale * heightScale);
                    const float occupancy = signed_interval_occupancy(
                        audioDetailSamples,
                        u.audioDetailMipOffsets[physicalDetailLevel],
                        u.audioDetailMipCounts[physicalDetailLevel],
                        u.audioDetailMipDivisors[physicalDetailLevel],
                        uint(coarseCell), detailDivisor,
                        lowerHeight, upperHeight, minimumWidth);
                    color = interval_occupancy_color(color, occupancy);
                }
            }
        }
    }
    // Store premultiplied RGB on the fixed lattice. Interpolating straight
    // color and height separately, then multiplying them in the fragment,
    // creates a quadratic signal whose five-point trapezoid changes slightly
    // with playback phase. Preserve the SAME weight alongside RGB: deriving
    // it again from interpolated signed bounds shrinks it at zero crossings
    // and where the dominant boundary changes, spuriously whitening colors.
    attributes[id].colorAndUpper =
        float4(color * height, upperHeight);
    attributes[id].lowerAndColorWeight =
        float4(lowerHeight, height, 0.0, 0.0);
}

// Eight midpoint taps per physical pixel, at the footprint-matched LOD.
//
// kLodBias MUST STAY 0. It was 2 -- deliberately selecting a mip two levels
// sharper -- on the theory that supersampling "buys" mip bias and recovers
// real detail. That was wrong, and it is what made the middle zooms fuzzy.
// A negative bias puts four to eight independent envelope values under a
// single physical pixel, and a pixel cannot display more than about one.
// The surplus does not become detail; it becomes content above the pixel
// Nyquist, which turns into per-frame flicker as the song sweeps past. On a
// real track, measured as the share of coverage energy above the pixel
// Nyquist, bias 2 scored 0.062 mean / 0.147 worst against bias 0's
// 0.029 / 0.066 -- worse at every one of the thirteen zooms.
//
// Cubic reconstruction uses four positive-weight support samples per tap.
// Eight taps therefore retain the same 32 envelope reads as the old sixteen
// two-sample linear taps while removing the lattice corners. The taps feed
// `profile_coverage`; they never buy detail the display cannot show.
constant uint kIntegrationTaps = 8u;
constant float kLodBias = 0.0;

inline float integration_offset(uint tap, uint count) {
    // Symmetric midpoints across the footprint. count == 4 would reproduce
    // the original {-0.375, -0.125, 0.125, 0.375} pattern exactly.
    return (float(tap) + 0.5) / float(count) - 0.5;
}

inline uint biased_lod(float lodFootprint, uint mipCount) {
    // ONE level, never a blend of two.
    //
    // Mip levels are song-anchored maxima on power-of-two lattices, so level
    // N quantizes every 2^N bins and level N+1 every 2^(N+1). Blending their
    // coverage scrolls two differently-quantized lattices at the same speed
    // and they beat against each other. The artifact tracked the fractional
    // part of the LOD exactly: at 32/64/128/256/576 px/s it lands near 0.62
    // and the edges were bad, while at 24/48/92/184/384 px/s it lands near
    // 0.05, one level dominates, and they were clean.
    //
    // Continuous blending only ever protected zoom *transitions*, and zoom is
    // thirteen fixed steps, so the footprint -- and this level -- is constant
    // for the whole of playback.
    // CEIL, not floor or round. Mip levels are powers of two but the thirteen
    // zoom steps are not, so flooring strands every other zoom at ~1.54
    // envelope values per pixel while its neighbours sit at ~1.03, and even
    // rounding leaves up to 1.41. Above one value per pixel the surplus is
    // undisplayable: it is the hairy comb along the contour that rewrites
    // itself every frame, measured on a real recording at 4.7 pixels of
    // column-to-column edge jump. Ceiling guarantees at most one envelope
    // value per physical pixel (ratio 0.5..1.0), which is the only density a
    // pixel can hold still. Measured aliasing across all thirteen zooms:
    // floor 0.029 mean / 0.066 worst, round 0.021 / 0.066, ceil 0.012 / 0.032.
    return uint(clamp(
        ceil(log2(max(1.0, lodFootprint)) - kLodBias),
        0.0, float(mipCount - 1)));
}

inline float profile_coverage(
        thread const float *heights, uint count,
        float distance, float heightScale, float antialias) {
    // Exact area coverage of the piecewise-linear silhouette across the
    // pixel, rather than the mean of N independent per-tap tests.
    //
    // Averaging is what made the contour wiggle. One tap's analytic band is
    // only about 1% of full height, so every tap is effectively a binary
    // inside/outside test, and the mean of eight of those can only take nine
    // values. As playback slides the taps, they cross the threshold one at a
    // time and the edge snaps 2/8 -> 3/8 -> 2/8 at every zoom. Integrating
    // the linear segment between adjacent taps instead makes coverage a
    // continuous function of the tap heights, so the edge glides.
    if (count < 2) {
        return 0.0;
    }
    const float scale = max(heightScale, 1e-5);
    const float threshold = distance / scale;
    const float band = max(antialias / scale, 1e-6);
    float total = 0.0;
    for (uint i = 0; i + 1 < count; ++i) {
        const float lo = min(heights[i], heights[i + 1]);
        const float hi = max(heights[i], heights[i + 1]);
        // A flat segment falls back to a one-pixel vertical antialias band;
        // a sloped one uses its exact linear crossing point.
        total += saturate(
            (hi + band - threshold) / max(hi - lo, 2.0 * band));
    }
    return total / float(count - 1);
}

inline float positioned_profile_coverage(
        thread const float *heights,
        thread const float *positions,
        uint count,
        float distance, float heightScale, float antialias) {
    if (count < 2 || count > kAttributeProfileCapacity) {
        return 0.0;
    }
    const float scale = max(heightScale, 1e-5);
    const float threshold = distance / scale;
    const float band = max(antialias / scale, 1e-6);
    float total = 0.0;
    float totalSpan = 0.0;
    for (uint i = 0; i + 1 < count; ++i) {
        const float span = max(0.0, positions[i + 1] - positions[i]);
        const float lo = min(heights[i], heights[i + 1]);
        const float hi = max(heights[i], heights[i + 1]);
        total += span * saturate(
            (hi + band - threshold) / max(hi - lo, 2.0 * band));
        totalSpan += span;
    }
    return total / max(totalSpan, 1e-6);
}

inline CoverageMoments clipped_linear_coverage_moments(
        float valueAtZero, float valueAtOne,
        float begin, float end) {
    // Integrate clamp(valueAtZero + t * slope, 0, 1) and its first
    // moment exactly over [begin, end]. The two clamp crossings partition
    // the interval into at most three constant/linear pieces.
    CoverageMoments result{0.0, 0.0};
    const float slope = valueAtOne - valueAtZero;
    float points[4] = {begin, end, end, end};
    uint pointCount = 2;
    if (abs(slope) > 1e-7) {
        const float zeroCrossing = -valueAtZero / slope;
        const float oneCrossing = (1.0 - valueAtZero) / slope;
        if (zeroCrossing > begin && zeroCrossing < end) {
            points[pointCount++] = zeroCrossing;
        }
        if (oneCrossing > begin && oneCrossing < end) {
            points[pointCount++] = oneCrossing;
        }
    }
    for (uint i = 1; i < pointCount; ++i) {
        const float value = points[i];
        uint insertion = i;
        while (insertion > 0 && points[insertion - 1] > value) {
            points[insertion] = points[insertion - 1];
            --insertion;
        }
        points[insertion] = value;
    }
    for (uint i = 0; i + 1 < pointCount; ++i) {
        const float left = points[i];
        const float right = points[i + 1];
        const float midpoint = (left + right) * 0.5;
        const float midpointValue =
            valueAtZero + slope * midpoint;
        if (midpointValue <= 0.0) {
            continue;
        }
        const float span = right - left;
        if (midpointValue >= 1.0) {
            result.area += span;
            result.firstMoment += span * midpoint;
            continue;
        }
        // Integrate around the local midpoint. Subtracting global squares
        // and cubes loses precision on tiny pieces near t=1, corrupting
        // their color centroid even when the coverage itself looks stable.
        const float area = midpointValue * span;
        result.area += area;
        result.firstMoment += area * midpoint
            + slope * span * span * span / 12.0;
    }
    return result;
}

inline IntervalSurface resolve_interval_surface(
        thread const float *minimums,
        thread const float *maximums,
        thread const float3 *encodedColors,
        thread const float *positions,
        uint count, float signedPosition,
        float heightScale, float antialias,
        bool blueMode) {
    IntervalSurface result{0.0, float3(0.0)};
    if (count < 2 || count > kAttributeProfileCapacity) {
        return result;
    }
    // Full minimum stroke is one physical pixel (two AA half-widths),
    // independent of Retina scale. A fixed point width bloats thin carriers.
    const float minimumHalfWidth =
        antialias / max(heightScale, 1e-5);
    const float band =
        max(antialias / max(heightScale, 1e-5), 1e-6);
    const float3 whiteLinear = display_srgb_to_linear(
        float3(237.0 / 255.0, 1.0, 1.0));
    const float3 cyanLinear = display_srgb_to_linear(
        float3(0.0, 1.0, 1.0));
    float totalSpan = 0.0;
    for (uint segment = 0; segment + 1 < count; ++segment) {
        const float segmentSpan =
            max(0.0, positions[segment + 1] - positions[segment]);
        if (segmentSpan <= 0.0) {
            continue;
        }
        totalSpan += segmentSpan;
        const float low0 = min(
            minimums[segment], maximums[segment]);
        const float high0 = max(
            minimums[segment], maximums[segment]);
        const float low1 = min(
            minimums[segment + 1], maximums[segment + 1]);
        const float high1 = max(
            minimums[segment + 1], maximums[segment + 1]);
        const float center0 = (low0 + high0) * 0.5;
        const float center1 = (low1 + high1) * 0.5;
        const float halfWidth0 = max(
            (high0 - low0) * 0.5, minimumHalfWidth);
        const float halfWidth1 = max(
            (high1 - low1) * 0.5, minimumHalfWidth);
        const float lower0 = center0 - halfWidth0;
        const float lower1 = center1 - halfWidth1;
        const float upper0 = center0 + halfWidth0;
        const float upper1 = center1 + halfWidth1;
        // RGB integrates authored display channels before decoding the final
        // opaque pixel. BLUE keeps its existing linear-light radial field.
        const float3 color0 = blueMode
            ? display_srgb_to_linear(saturate(encodedColors[segment]))
            : saturate(encodedColors[segment]);
        const float3 color1 = blueMode
            ? display_srgb_to_linear(saturate(encodedColors[segment + 1]))
            : saturate(encodedColors[segment + 1]);

        // Split at every change in coverage or BLUE's radial/color branch.
        // The color integral must follow the visible part of the pixel, not
        // evaluate a nonlinear field only at its coverage centroid.
        float points[12] = {0.0, 1.0};
        uint pointCount = 2;
        const float centerSlope = center1 - center0;
        if (abs(centerSlope) > 1e-7) {
            const float crossing = (signedPosition - center0) / centerSlope;
            if (crossing > 0.0 && crossing < 1.0) points[pointCount++] = crossing;
        }
        if (blueMode) {
            const float starts[7] = {
                lower0 - band, lower0, lower0 + band,
                upper0 - band, upper0, upper0 + band, halfWidth0};
            const float ends[7] = {
                lower1 - band, lower1, lower1 + band,
                upper1 - band, upper1, upper1 + band, halfWidth1};
            for (uint cut = 0; cut < 7; ++cut) {
                const float slope = ends[cut] - starts[cut];
                if (abs(slope) <= 1e-7) continue;
                const float target = cut == 6 ? 0.28 : signedPosition;
                const float crossing = (target - starts[cut]) / slope;
                if (crossing > 0.0 && crossing < 1.0) points[pointCount++] = crossing;
            }
        }
        for (uint i = 1; i < pointCount; ++i) {
            const float value = points[i];
            uint insertion = i;
            while (insertion > 0 && points[insertion - 1] > value) {
                points[insertion] = points[insertion - 1];
                --insertion;
            }
            points[insertion] = value;
        }
        for (uint piece = 0; piece + 1 < pointCount; ++piece) {
            const float begin = points[piece];
            const float end = points[piece + 1];
            if (end <= begin) {
                continue;
            }
            const float midpoint = (begin + end) * 0.5;
            const float localCenter =
                mix(center0, center1, midpoint);
            const bool upperBoundary =
                signedPosition >= localCenter;
            const float value0 = upperBoundary
                ? (upper0 + band - signedPosition) / (2.0 * band)
                : (-lower0 + band + signedPosition) / (2.0 * band);
            const float value1 = upperBoundary
                ? (upper1 + band - signedPosition) / (2.0 * band)
                : (-lower1 + band + signedPosition) / (2.0 * band);
            const CoverageMoments moments =
                clipped_linear_coverage_moments(
                    value0, value1, begin, end);
            if (moments.area <= 0.0) {
                continue;
            }
            result.coverage += moments.area * segmentSpan;
            if (!blueMode) {
                // PWV5 color and signed coverage are both piecewise linear
                // over this song-space segment. Integrate display channels
                // jointly so a hidden tap cannot tint a visible one.
                result.premultipliedColor +=
                    (color0 * moments.area
                        + (color1 - color0) * moments.firstMoment)
                    * segmentSpan;
                continue;
            }
            // Gauss-Legendre integrates the smooth color field on each
            // branch. Coverage remains the exact clipped linear integral.
            const float nodes[4] = {
                -0.8611363116, -0.3399810436, 0.3399810436, 0.8611363116};
            const float weights[4] = {
                0.3478548451, 0.6521451549, 0.6521451549, 0.3478548451};
            const float halfSpan = (end - begin) * 0.5;
            for (uint tap = 0; tap < 4; ++tap) {
                const float position = midpoint + halfSpan * nodes[tap];
                const float center = mix(center0, center1, position);
                const float halfWidth = mix(halfWidth0, halfWidth1, position);
                const float radial = saturate(
                    abs(signedPosition - center) / max(halfWidth, 1e-5));
                const float whiteStrength = smoothstep(0.0, 0.28, halfWidth);
                const float3 centerLinear = mix(
                    cyanLinear, whiteLinear, whiteStrength);
                const float radialBlend = smoothstep(0.0, 1.0, radial);
                const float3 edgeLinear = mix(color0, color1, position);
                const float coverage = saturate(mix(value0, value1, position));
                result.premultipliedColor += mix(
                    centerLinear, edgeLinear, radialBlend)
                    * (coverage * weights[tap] * halfSpan * segmentSpan);
            }
        }
    }
    const float normalization = 1.0 / max(totalSpan, 1e-6);
    result.coverage = saturate(result.coverage * normalization);
    result.premultipliedColor *= normalization;
    return result;
}

inline WaveSurface integrated_wave_level(
        const device WaveSample *samples,
        uint levelOffset, uint levelCount, uint level,
        uint baseAnchor, float baseLocal, float sampleFootprint,
        float distance, float heightScale, float antialias) {
    WaveSurface result{0.0, float3(0.0), 0.0, 0.0};
    // Integrate the song surface over each physical output pixel. Sampling
    // only its center aliases dense contours and adjacent RGB rows as the
    // fractional playback phase advances. These song-anchored midpoint taps
    // are shared by every zoom and by the selected song-anchored mip level.
    // Weights stay flat: a box across the pixel is exact area coverage, and
    // is what keeps a moving contour crisp rather than smeared.
    const uint tapCount = kIntegrationTaps;
    float heights[kIntegrationTaps];
    uint gathered = 0;
    float totalWeight = 0.0;
    for (uint tap = 0; tap < tapCount; ++tap) {
        const float offset = integration_offset(tap, tapCount);
        const float weight = 1.0;
        const WaveTap sample = wave_tap(
            samples, levelOffset, levelCount,
            baseAnchor,
            baseLocal + offset * sampleFootprint,
            level);
        if (sample.valid == 0.0) {
            continue;
        }
        heights[gathered++] = sample.height;
        result.weightedColor += sample.weightedColor * weight;
        result.colorWeight += sample.colorWeight * weight;
        totalWeight += weight;
        result.valid = 1.0;
    }
    if (totalWeight > 0.0) {
        result.weightedColor /= totalWeight;
        result.colorWeight /= totalWeight;
    }
    result.coverage = profile_coverage(
        heights, gathered, distance, heightScale, antialias);
    return result;
}

inline float integrated_envelope_level(
        const device AudioEnvelopeSample *samples,
        uint levelOffset, uint levelCount, uint divisor,
        uint baseAnchor, float baseLocal, float sampleFootprint,
        bool upperSide,
        float distance, float heightScale, float antialias) {
    const uint tapCount = kIntegrationTaps;
    float heights[kIntegrationTaps];
    uint gathered = 0;
    for (uint tap = 0; tap < tapCount; ++tap) {
        const float offset = integration_offset(tap, tapCount);
        const EnvelopeTap sample = envelope_tap(
            samples, levelOffset, levelCount,
            baseAnchor,
            baseLocal + offset * sampleFootprint,
            divisor);
        if (sample.valid > 0.0) {
            heights[gathered++] =
                upperSide ? sample.upper : sample.lower;
        }
    }
    return profile_coverage(
        heights, gathered, distance, heightScale, antialias);
}

inline float envelope_span_bound(
        const device AudioEnvelopeSample *samples,
        constant uint *offsets, constant uint *counts,
        constant uint *divisors, uint mipCount,
        uint baseAnchor, float baseLocal, float span, bool upperSide) {
    // Every pyramid level is a song-anchored maximum, so one coarse level
    // whose bins are at least as wide as the integration span bounds every
    // tap taken inside it. Probing both span endpoints covers the whole
    // window wherever the query lands inside a bin. This is exact, never
    // clips a real contour, and lets the wide close-zoom tap budget stay
    // affordable: fragments above the envelope cost two reads, not ninety.
    if (mipCount == 0) {
        return 0.0;
    }
    const uint coarse = audio_detail_bound_lod(
        span, divisors, mipCount);
    const float halfSpan = span * 0.5;
    float bound = 0.0;
    for (uint side = 0; side < 2; ++side) {
        const float2 supportMax = envelope_support_max(
            samples, offsets[coarse], counts[coarse],
            baseAnchor,
            baseLocal + (side == 0 ? -halfSpan : halfSpan),
            divisors[coarse]);
        bound = max(bound, upperSide ? supportMax.x : supportMax.y);
    }
    return bound;
}

inline WaveSurface filtered_wave_surface(
        const device WaveSample *samples,
        constant uint *offsets, constant uint *counts, uint mipCount,
        uint baseAnchor, float baseLocal,
        float lodFootprint, float integrationFootprint,
        float distance, float heightScale, float antialias) {
    WaveSurface result{0.0, float3(0.0), 0.0, 0.0};
    if (mipCount == 0) {
        return result;
    }
    // Geometry and RGB share one song-anchored level. Peak mips retain
    // transients; energy-integrated RGB mips stop adjacent colors from
    // alternating as a minified physical pixel moves.
    const uint level = biased_lod(lodFootprint, mipCount);
    return integrated_wave_level(
        samples, offsets[level], counts[level], level,
        baseAnchor, baseLocal, integrationFootprint,
        distance, heightScale, antialias);
}

inline float filtered_envelope_coverage(
        const device AudioEnvelopeSample *samples,
        constant uint *offsets, constant uint *counts,
        constant uint *divisors, uint mipCount,
        uint baseAnchor, float baseLocal,
        float lodFootprint, float integrationFootprint,
        bool upperSide,
        float distance, float heightScale, float antialias) {
    if (mipCount == 0) {
        return 0.0;
    }
    const float bound = envelope_span_bound(
        samples, offsets, counts, divisors, mipCount,
        baseAnchor, baseLocal, integrationFootprint, upperSide);
    if (distance > bound * heightScale + antialias) {
        return 0.0;
    }
    const uint level = audio_detail_lod(
        lodFootprint, divisors, mipCount);
    return integrated_envelope_level(
        samples, offsets[level], counts[level], divisors[level],
        baseAnchor, baseLocal, integrationFootprint,
        upperSide,
        distance, heightScale, antialias);
}

inline WaveSurface overview_wave_surface(
        const device WaveSample *samples,
        const device AudioEnvelopeSample *audioDetailSamples,
        constant Uniforms &u, float position, float footprint,
        float distance, float antialias) {
    const uint anchor = min(uint(floor(position)), u.sampleCount - 1);
    const float phase = position - floor(position);
    WaveSurface surface = filtered_wave_surface(
        samples, u.waveMipOffsets, u.waveMipCounts, u.waveMipCount,
        anchor, phase, footprint, footprint, distance, 11.0, antialias);
    if (surface.colorWeight > 0.0 || u.threeBandActive != 0
            || u.audioDetailSampleCount == 0 || u.audioDetailMipCount == 0) {
        return surface;
    }
    // Imported PWV5 height can quantize to zero with valid authored hue and
    // audible signed detail. The main lane already uses that independent
    // geometry; use it here too rather than treating compact zero as silence.
    const float ratio = u.audioDetailSamplesPerSecond / u.samplesPerSecond;
    const float audioPosition = position * ratio;
    const uint audioAnchor = uint(floor(audioPosition));
    const float audioPhase = audioPosition - floor(audioPosition);
    const float audioFootprint = footprint * ratio;
    const uint boundLevel = audio_detail_bound_lod(
        audioFootprint, u.audioDetailMipDivisors, u.audioDetailMipCount);
    const float2 bound = envelope_support_max(
        audioDetailSamples, u.audioDetailMipOffsets[boundLevel],
        u.audioDetailMipCounts[boundLevel], audioAnchor, audioPhase,
        u.audioDetailMipDivisors[boundLevel]);
    if (max(bound.x, bound.y) <= 0.0) {
        surface.coverage = 0.0;
        return surface;
    }
    const float upper = filtered_envelope_coverage(
        audioDetailSamples, u.audioDetailMipOffsets, u.audioDetailMipCounts,
        u.audioDetailMipDivisors, u.audioDetailMipCount, audioAnchor, audioPhase,
        audioFootprint, audioFootprint, true, distance, 11.0, antialias);
    const float lower = filtered_envelope_coverage(
        audioDetailSamples, u.audioDetailMipOffsets, u.audioDetailMipCounts,
        u.audioDetailMipDivisors, u.audioDetailMipCount, audioAnchor, audioPhase,
        audioFootprint, audioFootprint, false, distance, 11.0, antialias);
    surface.coverage = max(upper, lower);
    const uint colorLevel = biased_lod(footprint, u.waveMipCount);
    surface.weightedColor = linear_wave_color(
        samples, u.waveMipOffsets[colorLevel], u.waveMipCounts[colorLevel],
        anchor, phase, colorLevel);
    surface.colorWeight = 1.0;
    return surface;
}

fragment float4 wave_surface_fragment(
        Out in [[stage_in]],
        const device WaveSample *samples [[buffer(0)]],
        constant Uniforms &u [[buffer(1)]],
        const device AudioEnvelopeSample *audioDetailSamples [[buffer(4)]],
        const device WaveAttribute *attributes [[buffer(5)]]) {
    const float scale = max(1.0, u.drawableScale);
    const float xPoints = in.position.x / scale;
    const float yPoints = in.position.y / scale;
    const float3 encodedBackground = float3(
        13.0 / 255.0, 14.0 / 255.0, 18.0 / 255.0);
    const float3 background = display_srgb_to_linear(encodedBackground);
    if (u.frameTraceMarkerActive != 0) {
        // Two tiny repeated Gray-code strips bind a photographed panel frame
        // to this exact submitted drawable. Ten Gray bits cover the bounded
        // 600-frame trace; the alternating parity cell exposes an exposure
        // that blended adjacent 120 Hz refreshes. Repeating the code at both
        // main-lane edges also reveals a rolling-shutter phase change.
        const float markerHeight = max(4.0, round(4.0 * scale));
        const float markerGap = max(2.0, round(2.0 * scale));
        const float mainTop = 49.0 * scale;
        const float mainBottom = u.heightPoints * scale;
        const bool markerRow =
            (in.position.y >= mainTop + markerGap
                && in.position.y < mainTop + markerGap + markerHeight)
            || (in.position.y >= mainBottom - markerGap - markerHeight
                && in.position.y < mainBottom - markerGap);
        if (markerRow) {
            const uint cellWidth = max(8u, uint(round(6.0 * scale)));
            const uint cellCount = 14u;
            const uint codeWidth = cellWidth * cellCount;
            const uint groupSpan = codeWidth + cellWidth * 2u;
            const uint localX = uint(floor(in.position.x)) % groupSpan;
            if (localX >= codeWidth) {
                return float4(background, 1.0);
            }
            const uint cell = localX / cellWidth;
            const uint sequence = u.frameTraceSequence & 1023u;
            const uint gray = sequence ^ (sequence >> 1u);
            bool on = cell == 0u || cell == cellCount - 1u;
            if (cell == 2u) {
                on = (sequence & 1u) != 0u;
            } else if (cell >= 3u && cell < 13u) {
                on = ((gray >> (cell - 3u)) & 1u) != 0u;
            }
            return float4(on ? float3(1.0) : background, 1.0);
        }
    }
    const float antialias = 0.5 / scale;
    if (yPoints < 28.0) {
        if (u.sampleCount == 0 || u.waveMipCount == 0) {
            discard_fragment();
        }
        const float position =
            clamp(
                xPoints / max(1.0, u.widthPoints),
                0.0, 0.999999)
            * float(u.sampleCount);
        const float footprint =
            float(u.sampleCount)
            / max(1.0, float(u.drawableWidthPixels));
        const float distance = abs(yPoints - 14.0);
        const WaveSurface surface = overview_wave_surface(
            samples, audioDetailSamples, u, position, footprint,
            distance, antialias);
        if (surface.valid == 0.0 || surface.coverage <= 0.0
                || surface.colorWeight <= 0.0) {
            discard_fragment();
        }
        // Positive amplitude weights cancel exactly, including quiet tails.
        // Zero weight is silence: leave the background instead of painting
        // an antialiased black center line where no waveform exists.
        const float3 encodedColor =
            surface.weightedColor
            / surface.colorWeight;
        if (u.waveformMode == 2) {
            // Match the main RGB lane: help only faint coverage and blend in
            // display space. The old full-range cubic + linear-light blend
            // brightened already-visible overview columns as well as tails.
            float coverage = saturate(surface.coverage);
            if (coverage < 0.18) {
                const float fade = 1.0 - coverage / 0.18;
                coverage *= 1.0 + 1.5 * fade * fade;
            }
            return float4(display_srgb_to_linear(
                mix(encodedBackground, encodedColor, coverage)), 1.0);
        }
        const float3 color = display_srgb_to_linear(
            u.threeBandActive != 0
            ? three_band_color(encodedColor)
            : encodedColor);
        return float4(mix(
            background, color,
            vivid_wave_coverage(surface.coverage)), 1.0);
    }
    if (yPoints < 49.0) {
        discard_fragment();
    }
    const float waveCenter = 49.0 + (u.heightPoints - 49.0) * 0.5;
    const float distance = abs(yPoints - waveCenter);
    // Reclaim headroom at every zoom, with a little more breathing room close
    // up. Cached heights, the amplitude curve and reconstruction stay unchanged.
    const float verticalPadding = mix(
        2.0, 4.0, smoothstep(256.0, 384.0, u.pixelsPerSecond));
    const float heightScale =
        max(4.0, (u.heightPoints - 49.0) * 0.5 - verticalPadding);
    if (u.waveAttributeValid != 0 && u.waveAttributeCount >= 2) {
        // Height and hue live on one fixed, oversampled global song
        // lattice. Interpolate those attributes first, then perform vertical
        // antialiasing once at the drawable. Unlike a translated RGBA
        // waveform this never refilters coverage. BLUE uses a pixel-aligned
        // display offset; RGB and 3BAND stay continuous.
        const float samplesPerPixel =
            max(1.0, u.waveAttributeSamplesPerPixel);
        const float tileX =
            float(u.waveAttributeCount) * 0.5
            + (in.position.x - float(u.drawableWidthPixels) * 0.5)
                * samplesPerPixel
            + u.waveAttributeCurrentOffset;
        const float leftTile = tileX - samplesPerPixel * 0.5;
        const float rightTile = tileX + samplesPerPixel * 0.5;
        if (leftTile < 0.0
                || rightTile + 1.0
                    >= float(u.waveAttributeCount)) {
            discard_fragment();
        }
        // Integrate the fixed song profile over exactly one physical pixel.
        // The pixel endpoints plus every integer
        // attribute-lattice knot inside it are the complete piecewise-linear
        // profile. Fixed quarter-pixel probes miss one moving knot whenever
        // the song phase is fractional, causing colors and tips to pulse.
        // Every mode and zoom uses these complete corners. The former fixed
        // probes at mid/wide stops missed fractional-phase peaks. Source LOD
        // is conservative even at 24..48; the final footprint stays one pixel.
        float tilePositions[kAttributeProfileCapacity];
        float profilePositions[kAttributeProfileCapacity];
        uint profileCount = 0;
        tilePositions[profileCount++] = leftTile;
        const float firstKnot = floor(leftTile) + 1.0;
        for (uint i = 0; i + 2 < kAttributeProfileCapacity; ++i) {
            const float knot = firstKnot + float(i);
            if (knot >= rightTile) {
                break;
            }
            tilePositions[profileCount++] = knot;
        }
        tilePositions[profileCount++] = rightTile;
        float heights[kAttributeProfileCapacity];
        float outerHeights[kAttributeProfileCapacity];
        float midHeights[kAttributeProfileCapacity];
        float highHeights[kAttributeProfileCapacity];
        float lowHeights[kAttributeProfileCapacity];
        float blueMinimums[kAttributeProfileCapacity];
        float blueMaximums[kAttributeProfileCapacity];
        float3 attributeColors[kAttributeProfileCapacity];
        float3 intervalEncodedColors[kAttributeProfileCapacity];
        float3 weightedColor = float3(0.0);
        float colorWeight = 0.0;
        for (uint i = 0; i < profileCount; ++i) {
            profilePositions[i] =
                (tilePositions[i] - leftTile)
                / max(samplesPerPixel, 1e-5);
            const WaveAttribute attribute = linear_attribute(
                attributes, u.waveAttributeCount, tilePositions[i]);
            const float upperHeight = attribute.colorAndUpper.a;
            const float lowerHeight = attribute.lowerAndColorWeight.x;
            const float outerHeight = u.waveformMode != 3
                ? max(abs(upperHeight), abs(lowerHeight))
                : max(upperHeight, lowerHeight);
            const float sideHeight =
                yPoints <= waveCenter ? upperHeight : lowerHeight;
            heights[i] = sideHeight;
            outerHeights[i] = outerHeight;
            blueMinimums[i] = lowerHeight;
            blueMaximums[i] = upperHeight;
            // In 3BAND the premultiplied RGB attributes are the three absolute
            // PWV7 layer heights. Do not derive ratios from one shared outer
            // silhouette: each Pioneer band reaches its own vertical extent.
            lowHeights[i] = attribute.colorAndUpper.r;
            midHeights[i] = attribute.colorAndUpper.g;
            highHeights[i] = attribute.colorAndUpper.b;
            attributeColors[i] = attribute.colorAndUpper.rgb;
            intervalEncodedColors[i] =
                attribute.colorAndUpper.rgb
                    / max(attribute.lowerAndColorWeight.y, 0.0001);
        }
        for (uint i = 0; i + 1 < profileCount; ++i) {
            const float span =
                profilePositions[i + 1] - profilePositions[i];
            weightedColor +=
                (attributeColors[i] + attributeColors[i + 1])
                * (0.5 * span);
            colorWeight +=
                (outerHeights[i] + outerHeights[i + 1])
                * (0.5 * span);
        }
        if (u.waveformMode == 1 || u.waveformMode == 2) {
            const float signedPosition =
                (waveCenter - yPoints) / max(heightScale, 1e-5);
            IntervalSurface interval = resolve_interval_surface(
                blueMinimums, blueMaximums, intervalEncodedColors,
                profilePositions, profileCount,
                signedPosition, heightScale, antialias,
                u.waveformMode == 1);
            if (u.waveformMode == 2 && interval.coverage > 0.0
                    && u.pixelsPerSecond != 384.0 && interval.coverage < 0.18) {
                // Help only genuinely faint subpixel detail, regardless of hue.
                // The smooth, monotonic shoulder joins exact area at 18%:
                // no visibility floor, no broad occupied-column opacity boost.
                // Preserve accepted C's exact-area presentation at 384.
                const float fade = 1.0 - interval.coverage / 0.18;
                const float faintCoverage = interval.coverage * (1.0 + 1.5 * fade * fade);
                interval.premultipliedColor *= faintCoverage / interval.coverage;
                interval.coverage = faintCoverage;
            }
            if (interval.coverage <= 0.0) {
                discard_fragment();
            }
            // Visible color comes from the pointwise interval intersection.
            // Preserve the joint integral and its conditional color. Only faint
            // RGB detail outside 384 receives the bounded shoulder above.
            if (u.waveformMode == 2) {
                // Match the accepted B preview: retain both visibility lifts,
                // blend RGB edges in display space, then decode once for the
                // sRGB attachment. Geometry, BLUE and the overview stay intact.
                return float4(display_srgb_to_linear(
                    encodedBackground * (1.0 - interval.coverage)
                        + interval.premultipliedColor), 1.0);
            }
            return float4(
                background * (1.0 - interval.coverage)
                    + interval.premultipliedColor,
                1.0);
        }
        const float coverage = positioned_profile_coverage(
            heights, profilePositions, profileCount,
            distance, heightScale, antialias);
        if (u.threeBandActive == 0 && coverage <= 0.0) {
            discard_fragment();
        }
        if (u.threeBandActive != 0) {
            const float midCoverage = positioned_profile_coverage(
                midHeights, profilePositions, profileCount,
                distance, heightScale, antialias);
            const float highCoverage = positioned_profile_coverage(
                highHeights, profilePositions, profileCount,
                distance, heightScale, antialias);
            const float lowCoverage = positioned_profile_coverage(
                lowHeights, profilePositions, profileCount,
                distance, heightScale, antialias);
            const float low = saturate(lowCoverage);
            const float mid = saturate(midCoverage);
            const float high = saturate(highCoverage);
            const float none = (1.0 - low) * (1.0 - mid) * (1.0 - high);
            const float alpha = 1.0 - none;
            if (alpha <= 0.0) {
                discard_fragment();
            }
            const float3 lowColor =
                float3(0.0, 85.0 / 255.0, 225.0 / 255.0);
            const float3 midColor =
                float3(1.0, 166.0 / 255.0, 0.0);
            const float3 lowMidColor =
                float3(180.0 / 255.0, 105.0 / 255.0, 10.0 / 255.0);
            const float3 highColor = float3(1.0);
            const float3 lowHighColor =
                float3(210.0 / 255.0, 220.0 / 255.0, 250.0 / 255.0);
            const float3 midHighColor =
                float3(1.0, 240.0 / 255.0, 215.0 / 255.0);
            const float3 allColor =
                float3(245.0 / 255.0, 235.0 / 255.0, 215.0 / 255.0);
            // Rekordbox uses a seven-color membership palette, not alpha-
            // composited blue/orange/white paint. The product weights extend
            // that exact binary table continuously through antialiased edges.
            const float lowOnly = low * (1.0 - mid) * (1.0 - high);
            const float midOnly = (1.0 - low) * mid * (1.0 - high);
            const float lowMid = low * mid * (1.0 - high);
            const float highOnly = (1.0 - low) * (1.0 - mid) * high;
            const float lowHigh = low * (1.0 - mid) * high;
            const float midHigh = (1.0 - low) * mid * high;
            const float all = low * mid * high;
            const float3 displayColor = (
                lowOnly * lowColor
                + midOnly * midColor
                + lowMid * lowMidColor
                + highOnly * highColor
                + lowHigh * lowHighColor
                + midHigh * midHighColor
                + all * allColor) / max(alpha, 0.0001);
            return float4(mix(
                background,
                display_srgb_to_linear(displayColor),
                alpha), 1.0);
        }
        const float3 encodedColor =
            weightedColor / max(colorWeight, 0.0001);
        const float3 color = display_srgb_to_linear(encodedColor);
        return float4(mix(
            background, color,
            saturate(coverage)), 1.0);
    }
    const float secondOffset =
        (xPoints - u.widthPoints * 0.5)
        / max(1.0, u.pixelsPerSecond);
    const float waveLocal =
        u.waveCurrentPhase + secondOffset * u.samplesPerSecond;
    const float waveSpatialFootprint =
        u.samplesPerSecond
        / max(1.0, u.pixelsPerSecond * scale);
    // The integration window is the physical pixel and nothing else. A
    // temporal shutter was tried here and removed: the eye tracks a scrolling
    // waveform, so under smooth pursuit the waveform is stationary on the
    // retina and rendered blur simply smears it. The display's own
    // sample-and-hold already supplies the unavoidable persistence blur.
    const float waveIntegrationFootprint = waveSpatialFootprint;
    const WaveSurface colorSurface = filtered_wave_surface(
        samples, u.waveMipOffsets, u.waveMipCounts, u.waveMipCount,
        u.waveCurrentIndex, waveLocal,
        waveSpatialFootprint, waveIntegrationFootprint,
        distance, heightScale, antialias);
    if (colorSurface.valid == 0.0) {
        discard_fragment();
    }
    const float3 encodedColor =
        colorSurface.weightedColor
        / max(colorSurface.colorWeight, 0.0001);
    const float3 color = display_srgb_to_linear(encodedColor);
    float coverage = colorSurface.coverage;
    if (u.audioDetailSampleCount > 0
            && u.audioDetailMipCount > 0) {
        const float audioLocal =
            u.audioDetailCurrentPhase
            + secondOffset * u.audioDetailSamplesPerSecond;
        const float audioSpatialFootprint = u.audioDetailSamplesPerSecond
            / max(1.0, u.pixelsPerSecond * scale);
        const float audioIntegrationFootprint = audioSpatialFootprint;
        coverage = filtered_envelope_coverage(
            audioDetailSamples,
            u.audioDetailMipOffsets, u.audioDetailMipCounts,
            u.audioDetailMipDivisors,
            u.audioDetailMipCount,
            u.audioDetailCurrentIndex, audioLocal,
            audioSpatialFootprint, audioIntegrationFootprint,
            yPoints <= waveCenter,
            distance, heightScale, antialias);
    }
    if (coverage <= 0.0) {
        discard_fragment();
    }
    return float4(mix(
        background, color,
        saturate(coverage)), 1.0);
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
    o.lineDistancePixels =
        p.lineDistancePoints * max(1.0, u.drawableScale);
    o.lineHalfWidthPixels =
        p.lineHalfWidthPoints * max(1.0, u.drawableScale);
    o.laneY = y - 49.0;
    o.laneHeight = max(0.0, u.heightPoints - 49.0);
    o.beatStyle = float2(
        p.beatSpacingSeconds * u.pixelsPerSecond, float(p.beatKind));
    return o;
}
fragment float4 deck_fragment(Out in [[stage_in]]) {
    float alpha = in.color.a;
    if (in.beatStyle.y > 0.0) {
        // Keep timing guides continuous at every zoom. Density changes their
        // contrast, never their width or whether they cross the waveform.
        const bool downbeat = in.beatStyle.y > 1.5;
        const float edgeDistance = min(in.laneY, in.laneHeight - in.laneY);
        const float tick = 1.0 - smoothstep(
            downbeat ? 3.5 : 2.5, downbeat ? 4.0 : 3.0, edgeDistance);
        const float interior = mix(
            downbeat ? 0.58 : 0.28, downbeat ? 0.75 : 0.45,
            smoothstep(32.0, 80.0, in.beatStyle.x));
        alpha *= max(tick, interior);
        if (alpha <= 0.0) discard_fragment();
    }
    if (in.lineHalfWidthPixels > 0.0) {
        const float coverage = saturate(
            in.lineHalfWidthPixels + 0.5
            - abs(in.lineDistancePixels));
        alpha *= coverage;
    }
    return float4(display_srgb_to_linear(in.color.rgb), alpha);
}
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
        id<MTLFunction> attributeFunction =
            [library newFunctionWithName:@"wave_attribute_kernel"];
        attributePipeline_ = [device_
            newComputePipelineStateWithFunction:attributeFunction
            error:&error];

        MTLRenderPipelineDescriptor *grid = [wave copy];
        grid.vertexFunction = [library newFunctionWithName:@"grid_vertex"];
        grid.fragmentFunction = [library newFunctionWithName:@"deck_fragment"];
        grid.colorAttachments[0].blendingEnabled = YES;
        grid.colorAttachments[0].sourceRGBBlendFactor =
            MTLBlendFactorSourceAlpha;
        grid.colorAttachments[0].destinationRGBBlendFactor =
            MTLBlendFactorOneMinusSourceAlpha;
        gridPipeline_ = [device_ newRenderPipelineStateWithDescriptor:grid error:&error];
        if (!pipeline_ || !attributePipeline_ || !gridPipeline_) {
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
                // Follow the active display's negotiated cadence (including
                // ProMotion and a move between screens). Core Animation owns
                // that policy; pinning 120 Hz produced uneven delivery on
                // fixed-60 Hz and dynamically throttled displays.
                // Latency one bounds an interactive grab to at most the one
                // drawable already committed. Latency two allowed a visible
                // multi-frame run-on before a hold could take ownership.
                link.preferredFrameLatency = 1.0f;
                link.preferredFrameRateRange =
                    CAFrameRateRangeMake(60.0, 120.0, 120.0);
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
        uint64_t leadFrames = 0;
        uint64_t silenceRemaining = 0;
        while (!stopRequested_.load(std::memory_order_acquire)) {
            if (deviceChangeRequested_.exchange(
                    false, std::memory_order_acq_rel)) {
                restartAudioUnitForDefaultDevice();
            }
            std::string path;
            uint64_t generation = 0;
            uint64_t requestedLead = 0;
            uint64_t expectedFrames = 0;
            double expectedRate = 0;
            {
                std::lock_guard<std::mutex> lock(commandMutex_);
                if (pathPending_) {
                    path = pendingPath_;
                    requestedLead = pendingLeadFrames_;
                    expectedFrames = pendingExpectedFrames_;
                    expectedRate = pendingExpectedRate_;
                    generation = pendingGeneration_;
                    pathPending_ = false;
                }
            }
            if (!path.empty()) {
                @autoreleasepool {
                    file = nil;
                    const int readiness = sbCloudReadiness(path,
                        [&] { return sbReadSourceByte(path); },
                        [&] {
                            std::unique_lock<std::mutex> lock(wakeMutex_);
                            return !decodeWake_.wait_for(lock,
                                std::chrono::seconds(3), [&] {
                                    return stopRequested_.load()
                                        || generation != generation_.load();
                                });
                        });
                    if (stopRequested_.load() || generation != generation_.load()) {
                        continue;
                    }
                    if (readiness) {
                        playRequested_.store(false, std::memory_order_release);
                        const std::string name = std::filesystem::path(path).filename().string();
                        const std::string message = readiness == ENEEDAUTH
                            ? "Cloud storage cannot provide " + name
                                + ". Open your cloud storage app and check its sign-in/connection, then press Play to retry."
                            : "Cannot read " + name + ": " + std::strerror(readiness);
                        setError(-24, message.c_str());
                        continue;
                    }
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
                    if (!requestedLead && !expectedFrames) {
                        const auto origin = sbMP3Origin(path);
                        requestedLead = origin.lead;
                        expectedFrames = origin.frames;
                        expectedRate = origin.rate;
                    }
                    const double rate = file.processingFormat.sampleRate;
                    if (requestedLead && (expectedFrames != static_cast<uint64_t>(file.length)
                            || std::abs(expectedRate - rate) > 0.5)) {
                        setError(-23, "MP3 decoder clock differs from its validated header");
                        file = nil;
                        continue;
                    }
                    leadFrames = requestedLead;
                    silenceRemaining = leadFrames;
                    sourceLeadFrames_.store(leadFrames, std::memory_order_release);
                    waveformRevision_.fetch_add(1, std::memory_order_acq_rel);
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
                        static_cast<uint64_t>(file.length) + leadFrames,
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
                const uint64_t requested = requestedSeek_.load(std::memory_order_acquire);
                file.framePosition = static_cast<AVAudioFramePosition>(
                    requested > leadFrames ? requested - leadFrames : 0);
                silenceRemaining = requested < leadFrames ? leadFrames - requested : 0;
                decoderEnded_.store(false, std::memory_order_release);
                observedSeek = seekSerial;
                decodedSeekSerial_.store(
                    observedSeek, std::memory_order_release);
            }

            if (ring_.freeSpace() < kDecodeChunk) {
                waitForDecodeWork();
                continue;
            }

            if (silenceRemaining) {
                const uint32_t count = static_cast<uint32_t>(
                    std::min<uint64_t>(silenceRemaining, kDecodeChunk));
                const uint64_t write = ring_.write.load(std::memory_order_relaxed);
                for (uint32_t i = 0; i < count; ++i) {
                    const uint32_t index = static_cast<uint32_t>(write + i) & kRingMask;
                    ring_.left[index] = ring_.right[index] = 0.0f;
                }
                ring_.write.store(write + count, std::memory_order_release);
                silenceRemaining -= count;
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
    id<MTLComputePipelineState> attributePipeline_ = nil;
    id<MTLRenderPipelineState> gridPipeline_ = nil;
    id<MTLBuffer> waveAttributeBuffer_ = nil;
    id<MTLBuffer> waveBuffer_ = nil;
    id<MTLBuffer> detailBuffer_ = nil;
    id<MTLBuffer> audioDetailBuffer_ = nil;
    id<MTLBuffer> gridBuffer_ = nil;
    bool gridOverrideActive_ = false;
    id<MTLBuffer> cueBuffer_ = nil;
    id<MTLBuffer> sessionCueBuffer_ = nil;
    NSUInteger waveSampleCount_ = 0;
    NSUInteger detailSampleCount_ = 0;
    NSUInteger audioDetailSampleCount_ = 0;
    NSUInteger audioDetailMipCount_ = 0;
    std::array<uint32_t, kWaveMipLevels> waveMipOffsets_{};
    std::array<uint32_t, kWaveMipLevels> waveMipCounts_{};
    size_t waveMipCount_ = 0;
    std::array<uint32_t, kWaveMipLevels> audioDetailMipOffsets_{};
    std::array<uint32_t, kWaveMipLevels> audioDetailMipCounts_{};
    std::array<uint32_t, kWaveMipLevels> audioDetailMipDivisors_{};
    float waveSamplesPerSecond_ = 0.0f;
    float detailSamplesPerSecond_ = 0.0f;
    float audioDetailSamplesPerSecond_ = 0.0f;
    NSUInteger gridVertexCount_ = 0;
    NSUInteger cueVertexCount_ = 0;
    NSUInteger sessionCueVertexCount_ = 0;
    NSUInteger cueMarkerCount_ = 0;
    NSUInteger waveAttributeCount_ = 0;
    int64_t waveAttributeCenter_ = 0;
    uint64_t waveAttributeRevision_ = 0;
    float waveAttributeZoom_ = 0.0f;
    float waveAttributeScale_ = 0.0f;
    float waveAttributeSamplesPerPixel_ = 0.0f;
    bool threeBandActive_ = false;
    bool displayWaveformActive_ = false;
    uint32_t waveformMode_ = 3;
    std::array<DisplayWaveform, 3> displayWaveforms_{};
    std::atomic<uint64_t> waveformRevision_{1};
    std::vector<BeatPosition> beatPositions_;
    uint32_t beatCountMode_ = 0;
    std::vector<double> memoryCueSeconds_;
    // Display-thread-only storage: rebuilding the small dynamic overlay never
    // allocates on the playback frame path after the first frame.
    std::vector<GridVertex> overlayScratch_;
    std::mutex renderMutex_;
    SBDisplayDelegate *displayDelegate_ = nil;
    CAMetalDisplayLink *displayLink_ = nil;
    std::thread displayThread_;
    std::atomic<bool> displayStop_{false};
    std::atomic<bool> displayDirty_{true};
    std::mutex displayRunLoopMutex_;
    CFRunLoopRef displayRunLoop_ = nullptr;
    // Presentation callbacks, unlike display-link submissions, identify
    // drawables the display really showed. Shared ownership keeps callback
    // state valid if an in-flight presentation finishes during deck teardown.
    std::shared_ptr<PresentedFrameState> presentedFrames_ =
        std::make_shared<PresentedFrameState>();
    // Developer-only, explicitly bounded drawable capture. The shared state
    // is retained by in-flight Metal callbacks; no file I/O or image encoding
    // occurs on the display-link path.
    std::mutex frameTraceMutex_;
    std::shared_ptr<FrameTraceState> frameTrace_;
    std::atomic<bool> frameTraceArmed_{false};
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
    uint64_t pendingLeadFrames_ = 0;
    uint64_t pendingExpectedFrames_ = 0;
    double pendingExpectedRate_ = 0;
    std::atomic<uint64_t> sourceLeadFrames_{0};
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
    std::atomic<uint64_t> visualStartFrame_{0};
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
    std::atomic<uint32_t> waveformUpdateDepth_{0};
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

int sb_deck_load_timeline(SBDeckRef deck, const char *path,
                          uint64_t lead, uint64_t frames, double rate) {
    return deck ? static_cast<SBDeck *>(deck)->load(path, lead, frames, rate) : 0;
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

uint64_t sb_deck_begin_preview_hold(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->beginPreviewHold()
        : 0;
}

void sb_deck_begin_waveform_update(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->beginWaveformUpdate();
    }
}

void sb_deck_end_waveform_update(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->endWaveformUpdate();
    }
}

float sb_deck_set_zoom(SBDeckRef deck, float pixels_per_second) {
    if (deck) {
        auto *instance = static_cast<SBDeck *>(deck);
        return instance->setZoom(pixels_per_second);
    }
    return 0.0f;
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
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setPackedOverview(
        generation, heights, rgb, column_count, columns_per_second,
        beat_seconds, beat_in_bar, beat_count);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_packed_overview_u16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *heights,
    const uint8_t *rgb,
    size_t column_count,
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setPackedOverview16(
        generation, heights, rgb, column_count, columns_per_second,
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

int sb_deck_set_waveform_streams(
    SBDeckRef deck,
    const uint8_t *blue_rows,
    size_t blue_count,
    const uint8_t *rgb_rows,
    size_t rgb_count,
    const uint8_t *three_band_rows,
    size_t three_band_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setWaveformStreams(
        blue_rows, blue_count,
        rgb_rows, rgb_count,
        three_band_rows, three_band_count,
        samples_per_second);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_waveform_streams_for_generation(
    SBDeckRef deck, uint64_t generation,
    const uint8_t *blue_rows,
    size_t blue_count,
    const uint8_t *rgb_rows,
    size_t rgb_count,
    const uint8_t *three_band_rows,
    size_t three_band_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setWaveformStreams(
        blue_rows, blue_count,
        rgb_rows, rgb_count,
        three_band_rows, three_band_count,
        samples_per_second, generation);
    instance->requestDisplay();
    return result;
}

int sb_deck_set_waveform_mode(SBDeckRef deck, uint32_t mode) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setWaveformMode(mode);
    instance->requestDisplay();
    return result;
}

uint32_t sb_deck_waveform_mode(SBDeckRef deck) {
    return deck ? static_cast<SBDeck *>(deck)->waveformMode() : 0;
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

int sb_deck_set_audio_detail_u16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *heights,
    size_t sample_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAudioDetail16(
        heights, sample_count, samples_per_second, generation);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_audio_detail_polarity_u16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *upper_heights,
    const uint16_t *lower_heights,
    size_t sample_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAudioDetailPolarity16(
        upper_heights, lower_heights, sample_count,
        samples_per_second, generation);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_audio_detail_polarity_range_u16_i16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *upper_heights,
    const uint16_t *lower_heights,
    const int16_t *minimum_values,
    const int16_t *maximum_values,
    size_t sample_count,
    double samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAudioDetailPolarity16(
        upper_heights, lower_heights, sample_count,
        samples_per_second, generation,
        minimum_values, maximum_values);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_set_audio_detail_range_mips_i16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const int16_t *minimum_values,
    const int16_t *maximum_values,
    const uint32_t *level_counts,
    const uint32_t *level_divisors,
    size_t level_count,
    size_t base_sample_count,
    double base_samples_per_second) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setAudioDetailRangeMips16(
        minimum_values, maximum_values, level_counts, level_divisors,
        level_count, base_sample_count, base_samples_per_second, generation);
    if (result) instance->requestDisplay();
    return result;
}

int sb_deck_grid_position(
    SBDeckRef deck, double seconds, uint32_t *bar, uint8_t *beat) {
    return deck ? static_cast<SBDeck *>(deck)->gridPosition(seconds, bar, beat) : 0;
}

void sb_deck_set_beat_count_mode(SBDeckRef deck, uint32_t mode) {
    if (!deck) return;
    auto *instance = static_cast<SBDeck *>(deck);
    instance->setBeatCountMode(mode);
    instance->requestDisplay();
}

int sb_deck_set_grid_for_generation(
    SBDeckRef deck, uint64_t generation, const double *beat_seconds,
    const uint8_t *beat_in_bar, size_t beat_count, int pinned) {
    if (!deck) return 0;
    auto *instance = static_cast<SBDeck *>(deck);
    const int result = instance->setGrid(generation, beat_seconds, beat_in_bar, beat_count, pinned != 0);
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

int sb_deck_has_ended(SBDeckRef deck) {
    return deck && static_cast<SBDeck *>(deck)->hasEnded();
}

uint64_t sb_deck_visual_frame(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->visualFrameNow()
        : 0;
}

uint64_t sb_deck_presented_frame(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->latestPresentedFrame()
        : 0;
}

uint64_t sb_deck_scheduled_frame(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->latestScheduledFrame()
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

double sb_deck_presentation_error_mean_ms(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->presentationErrorMeanMs()
        : 0.0;
}

double sb_deck_presentation_error_stddev_ms(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->presentationErrorStdDevMs()
        : 0.0;
}

double sb_deck_presentation_error_max_ms(SBDeckRef deck) {
    return deck
        ? static_cast<SBDeck *>(deck)->presentationErrorMaxMs()
        : 0.0;
}

void sb_deck_reset_display_stats(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->resetDisplayStats();
    }
}

int sb_deck_begin_frame_trace(
        SBDeckRef deck,
        uint32_t max_frames,
        uint32_t crop_width_pixels) {
    return deck && static_cast<SBDeck *>(deck)->beginFrameTrace(
        max_frames, crop_width_pixels);
}

int sb_deck_frame_trace_status(
        SBDeckRef deck,
        SBDeckFrameTraceStatus *out_status) {
    return deck && static_cast<SBDeck *>(deck)->frameTraceStatus(out_status);
}

int sb_deck_finish_frame_trace(
        SBDeckRef deck,
        const char *utf8_output_directory) {
    return deck && static_cast<SBDeck *>(deck)->finishFrameTrace(
        utf8_output_directory);
}

void sb_deck_cancel_frame_trace(SBDeckRef deck) {
    if (deck) {
        static_cast<SBDeck *>(deck)->cancelFrameTrace();
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
