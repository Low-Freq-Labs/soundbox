#pragma once

#import <Foundation/Foundation.h>

// One source for the eagerly compiled waveform pipelines and offline math tests.
// Keep the shader literal byte-identical when changing its owning source file.
static inline NSString *soundboxWaveformShaderSource() {
    return @R"METAL(
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
inline float3 linear_to_display_srgb(float3 value) {
    value = saturate(value);
    const float3 lower = value * 12.92;
    const float3 upper = 1.055 * pow(value, float3(1.0 / 2.4)) - 0.055;
    return select(upper, lower, value <= float3(0.0031308));
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

inline float wide_interval_occupancy(
        const device AudioEnvelopeSample *samples,
        uint fineOffset, uint fineCount, uint fineDivisor,
        uint coarseCell, uint coarseDivisor,
        float coarseMinimum, float coarseMaximum, float minimumWidth) {
    if (fineCount == 0 || fineDivisor == 0 || coarseDivisor <= fineDivisor) return 1.0;
    const uint begin = coarseCell * coarseDivisor;
    const uint end = begin + coarseDivisor;
    const uint first = begin / fineDivisor;
    const uint last = min(fineCount, (end + fineDivisor - 1u) / fineDivisor);
    if (first >= last || last - first > 256u) return 1.0;
    float area = 0.0;
    float span = 0.0;
    for (uint cell = first; cell < last; ++cell) {
        const float overlap = float(min(end, (cell + 1u) * fineDivisor)
            - max(begin, cell * fineDivisor));
        const AudioEnvelopeSample fine = samples[fineOffset + cell];
        area += max(abs(float(fine.maximum) - float(fine.minimum)) / 32767.0, minimumWidth) * overlap;
        span += overlap;
    }
    const float width = max(abs(coarseMaximum - coarseMinimum), minimumWidth);
    return saturate(area / max(span * width, 1e-8));
}

inline float3 interval_occupancy_color(
        float3 color, float occupancy,
        float3 background = float3(13.0, 14.0, 18.0) / 255.0) {
    return mix(background, color, occupancy);
}

inline float3 reference_wave_color(
        const device WaveSample *samples,
        const device AudioEnvelopeSample *detail,
        constant Uniforms &u, WaveAttributeClock clock,
        float secondOffset, float scale) {
    // Sonny's reference is the 92 pt/s RGB display. Evaluate its palette on
    // the song clock at every zoom; geometry alone chooses a variable LOD.
    // Neither wider peak bins nor close-detail occupancy may brighten/dim it.
    const float referencePixels = 92.0;
    const uint level = detail_lod(u.samplesPerSecond / (referencePixels * scale), u.waveMipCount);
    const float local = clock.wavePhase + secondOffset * u.samplesPerSecond;
    const float offset = 0.5 * u.samplesPerSecond / (referencePixels * scale);
    const WaveTap center = wave_tap(samples,u.waveMipOffsets[level],u.waveMipCounts[level],clock.waveIndex,local,level);
    const WaveTap left = wave_tap(samples,u.waveMipOffsets[level],u.waveMipCounts[level],clock.waveIndex,local-offset,level);
    const WaveTap right = wave_tap(samples,u.waveMipOffsets[level],u.waveMipCounts[level],clock.waveIndex,local+offset,level);
    const bool neighbors = left.valid > 0.0 && right.valid > 0.0;
    const float3 weighted = neighbors ? (left.weightedColor + 2.0*center.weightedColor + right.weightedColor)*0.25 : center.weightedColor;
    const float weight = neighbors ? (left.colorWeight + 2.0*center.colorWeight + right.colorWeight)*0.25 : center.colorWeight;
    float3 color = recover_rgb_color(weighted,weight,
        linear_wave_color(samples,u.waveMipOffsets[level],u.waveMipCounts[level],clock.waveIndex,local,level));
    if (u.waveformMode != 2 || u.audioDetailSampleCount == 0 || u.audioDetailMipCount == 0) return color;
    const float footprint = u.audioDetailSamplesPerSecond / (referencePixels * scale);
    const uint detailLevel = audio_detail_lod(footprint,u.audioDetailMipDivisors,u.audioDetailMipCount);
    const uint divisor = u.audioDetailMipDivisors[detailLevel];
    const float detailLocal = clock.detailPhase + secondOffset*u.audioDetailSamplesPerSecond;
    const EnvelopeTap centerDetail = sharp_envelope_tap(detail,u.audioDetailMipOffsets[detailLevel],u.audioDetailMipCounts[detailLevel],clock.detailIndex,detailLocal,divisor);
    if (centerDetail.valid <= 0.0) return color;
    const EnvelopeTap leftDetail = sharp_envelope_tap(detail,u.audioDetailMipOffsets[detailLevel],u.audioDetailMipCounts[detailLevel],clock.detailIndex,detailLocal-footprint*1.5,divisor);
    const EnvelopeTap rightDetail = sharp_envelope_tap(detail,u.audioDetailMipOffsets[detailLevel],u.audioDetailMipCounts[detailLevel],clock.detailIndex,detailLocal+footprint*1.5,divisor);
    float minimum = centerDetail.minimum, maximum = centerDetail.maximum;
    if (leftDetail.valid > 0.0 && rightDetail.valid > 0.0) {
        minimum = min(minimum,(leftDetail.minimum+2.0*minimum+rightDetail.minimum)*0.25);
        maximum = max(maximum,(leftDetail.maximum+2.0*maximum+rightDetail.maximum)*0.25);
    }
    const int cell = int(clock.detailIndex/divisor) + int(floor((float(clock.detailIndex%divisor)+detailLocal)/float(divisor)));
    if (cell < 0) return color;
    const float halfHeight = max(4.0,(u.heightPoints-49.0)*0.5-4.0);
    const float occupancy = wide_interval_occupancy(detail,u.audioDetailMipOffsets[0],u.audioDetailMipCounts[0],u.audioDetailMipDivisors[0],uint(cell),divisor,minimum,maximum,1.0/(scale*halfHeight));
    const float3 background = display_srgb_to_linear(float3(13.0,14.0,18.0)/255.0);
    return linear_to_display_srgb(interval_occupancy_color(
        display_srgb_to_linear(color),mix(1.0,occupancy,0.25),background));
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
    if (u.waveformMode == 3 && midrangeFilter > 0.0) {
        // Retain each band's own center peak through the middle zooms, as
        // signed RGB already does. Keep PWV7's independent calibrated layers.
        stableWave.weightedColor = max(sharpWave.weightedColor, stableWave.weightedColor);
        stableWave.height = max(max(stableWave.weightedColor.r,
            stableWave.weightedColor.g), stableWave.weightedColor.b);
        stableWave.colorWeight = stableWave.height;
    }
    const float heightWeight = mix(
        sharpWave.colorWeight, stableWave.colorWeight, stableKernel);
    const float3 weightedColor = mix(
        sharpWave.weightedColor,
        stableWave.weightedColor,
        stableKernel);
    float3 color = weightedColor
        / max(heightWeight, 0.0001);
    if (u.waveformMode != 3) color = reference_wave_color(samples,audioDetailSamples,u,clock,secondOffset,scale);
    float height = mix(
        sharpWave.height, stableWave.height, stableKernel);
    float upperHeight = height;
    // Before the mode streams arrive, the compact overview uses the legacy
    // symmetric surface (mode 0). Its lower side is a positive magnitude;
    // only BLUE/RGB's signed-interval renderer consumes a negative minimum.
    float lowerHeight = u.waveformMode == 1 || u.waveformMode == 2
        ? -height : height;
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
                // Middle RGB reconstruction must not average away the
                // center tap's signed peaks. Retain its interval while
                // keeping the existing song-space neighbor reconstruction.
                const bool preserveSignedPeaks = u.waveformMode != 3
                    && u.pixelsPerSecond >= 64.0
                    && u.pixelsPerSecond <= 256.0;
                sharpDetail.minimum = preserveSignedPeaks
                    ? min(sharpDetail.minimum, filtered.z)
                    : mix(sharpDetail.minimum, filtered.z, midrangeFilter);
                sharpDetail.maximum = preserveSignedPeaks
                    ? max(sharpDetail.maximum, filtered.w)
                    : mix(sharpDetail.maximum, filtered.w, midrangeFilter);
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

        }
    }
    // Main-lane display headroom is independent of saved/overview amplitudes.
    // Retain PWV7's three independent layers and signed BLUE zero crossings.
    if (u.waveformMode == 3) {
        const float3 bands = min(color * height * 1.20, float3(1.0));
        height = max(max(bands.r, bands.g), bands.b);
        color = bands / max(height, 0.0001);
        upperHeight = lowerHeight = height;
    } else if (u.waveformMode == 1) {
        upperHeight = clamp(upperHeight * 1.06, -1.0, 1.0);
        lowerHeight = clamp(lowerHeight * 1.06, -1.0, 1.0);
        height = max(abs(upperHeight), abs(lowerHeight));
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

// The overview retains all three PWV7 layer heights. Its palette is the
// same seven membership colors as the main lane and pinned rbxport's
// src/canvas/waveform.ts; choosing only the dominant band loses the inner
// brown/cream stretches even though their original data is resident.
inline float4 three_band_overview_fragment(
        const device WaveSample *samples, constant Uniforms &u,
        float position, float footprint, float distance, float antialias,
        float3 background) {
    const uint anchor = min(uint(floor(position)), u.sampleCount - 1);
    const float phase = position - floor(position);
    const uint level = biased_lod(footprint, u.waveMipCount);
    float lows[kIntegrationTaps], mids[kIntegrationTaps], highs[kIntegrationTaps];
    uint count = 0;
    float3 peaks = float3(0.0);
    for (uint tap = 0; tap < kIntegrationTaps; ++tap) {
        const WaveTap sample = wave_tap(
            samples, u.waveMipOffsets[level], u.waveMipCounts[level],
            anchor, phase + integration_offset(tap, kIntegrationTaps) * footprint,
            level);
        if (sample.valid == 0.0) continue;
        // Three-band pyramid colors are premultiplied absolute layer heights.
        const float3 bands = max(sample.weightedColor, float3(0.0));
        lows[count] = bands.r; mids[count] = bands.g; highs[count] = bands.b;
        peaks = max(peaks, bands); ++count;
    }
    if (count < 2 || max(peaks.r, max(peaks.g, peaks.b)) == 0.0) {
        return float4(background, 1.0);
    }
    const float low = peaks.r > 0.0 ? profile_coverage(lows, count, distance, 11.0, antialias) : 0.0;
    const float mid = peaks.g > 0.0 ? profile_coverage(mids, count, distance, 11.0, antialias) : 0.0;
    const float high = peaks.b > 0.0 ? profile_coverage(highs, count, distance, 11.0, antialias) : 0.0;
    const float none = (1.0-low) * (1.0-mid) * (1.0-high);
    const float alpha = 1.0-none;
    if (alpha <= 0.0) return float4(background, 1.0);
    const float3 color = (
        low*(1.0-mid)*(1.0-high)*float3(0.0,85.0,225.0)
        + (1.0-low)*mid*(1.0-high)*float3(255.0,166.0,0.0)
        + low*mid*(1.0-high)*float3(180.0,105.0,10.0)
        + (1.0-low)*(1.0-mid)*high*float3(255.0)
        + low*(1.0-mid)*high*float3(210.0,220.0,250.0)
        + (1.0-low)*mid*high*float3(255.0,240.0,215.0)
        + low*mid*high*float3(245.0,235.0,215.0)) / (255.0*alpha);
    return float4(mix(background, display_srgb_to_linear(color),
                      vivid_wave_coverage(alpha)), 1.0);
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
        if (u.threeBandActive != 0) {
            return three_band_overview_fragment(samples,u,position,footprint,
                                                distance,antialias,background);
        }
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
        const float3 color = display_srgb_to_linear(encodedColor);
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
                    && interval.coverage < 0.18) {
                // Help only genuinely faint subpixel detail, regardless of hue.
                // The smooth, monotonic shoulder joins exact area at 18%:
                // no visibility floor, no broad occupied-column opacity boost.
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
}
