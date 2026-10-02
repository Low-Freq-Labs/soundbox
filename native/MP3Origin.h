#ifndef SOUNDBOX_MP3_ORIGIN_H
#define SOUNDBOX_MP3_ORIGIN_H

#include <array>
#include <cstdint>
#include <cstdio>
#include <string>
#include <algorithm>
#include <cctype>

struct SBMP3Origin {
    uint64_t lead = 0;
    uint64_t frames = 0;
    double rate = 0;
};

// Decode-thread only. Keep the accepted header subset in sync with
// soundbox.waveform_detail.mp3_origin; unknown headers retain the old clock.
inline SBMP3Origin sbMP3Origin(const std::string &path) {
    if (path.size() < 4) return {};
    std::string suffix = path.substr(path.size() - 4);
    std::transform(suffix.begin(), suffix.end(), suffix.begin(),
        [](unsigned char c) { return std::tolower(c); });
    if (suffix != ".mp3") return {};
    FILE *file = std::fopen(path.c_str(), "rb");
    if (!file) return {};
    std::array<uint8_t, 4096> raw{};
    const size_t head = std::fread(raw.data(), 1, 10, file);
    long offset = 0;
    if (head == 10 && raw[0] == 'I' && raw[1] == 'D' && raw[2] == '3') {
        for (int i = 6; i < 10; ++i) {
            if (raw[i] & 128) { std::fclose(file); return {}; }
            offset = (offset << 7) | raw[i];
        }
        offset += 10 + ((raw[5] & 0x10) ? 10 : 0);
    }
    if (std::fseek(file, offset, SEEK_SET)) { std::fclose(file); return {}; }
    const size_t size = std::fread(raw.data(), 1, raw.size(), file);
    std::fclose(file);
    if (size < 200) return {};
    auto u32 = [&](size_t p) -> uint32_t {
        if (p + 4 > size) return 0;
        return (uint32_t(raw[p]) << 24) | (uint32_t(raw[p+1]) << 16)
             | (uint32_t(raw[p+2]) << 8) | raw[p+3];
    };
    const uint32_t word = u32(0);
    if ((word & 0xfffe0000u) != 0xfffa0000u) return {};
    const uint32_t bitrateIndex = (word >> 12) & 15;
    const uint32_t rateIndex = (word >> 10) & 3;
    if (!bitrateIndex || bitrateIndex == 15 || rateIndex == 3) return {};
    constexpr uint32_t rates[] = {44100, 48000, 32000};
    constexpr uint32_t bitrates[] = {0,32,40,48,56,64,80,96,112,128,160,192,224,256,320};
    const uint32_t rate = rates[rateIndex];
    const size_t frameSize = 144000 * bitrates[bitrateIndex] / rate + ((word >> 9) & 1);
    size_t cursor = 4 + (((word >> 6) & 3) == 3 ? 17 : 32);
    const uint32_t marker = u32(cursor);
    if (marker != 0x496e666fu && marker != 0x58696e67u) return {};
    const uint32_t flags = u32(cursor + 4);
    if (!(flags & 1) || (flags & ~15u)) return {};
    const uint64_t frameCount = u32(cursor + 8);
    cursor += 12;
    if (flags & 2) cursor += 4;
    if (flags & 4) cursor += 100;
    if (flags & 8) cursor += 4;
    if (cursor + 36 > frameSize || cursor + 36 > size || u32(cursor) != 0x4c414d45u)
        return {};
    if ((u32(frameSize) & 0xfffe0c00u) != (word & 0xfffe0c00u)) return {};
    const uint32_t packed = (uint32_t(raw[cursor+21]) << 16)
        | (uint32_t(raw[cursor+22]) << 8) | raw[cursor+23];
    const uint32_t delay = packed >> 12, padding = packed & 4095;
    if (!delay || delay > 2304 || padding < 529 || frameCount * 1152 <= delay + padding)
        return {};
    return {1152u + delay + 529u, frameCount * 1152 - delay - padding, double(rate)};
}
#endif
