#ifndef SOUNDBOX_CLOUD_READINESS_H
#define SOUNDBOX_CLOUD_READINESS_H

#include <cerrno>
#include <cstdio>
#include <string>

// Decode-thread admission only, not an integrity proof or a durable cache.
// Read one byte so File Provider errors retain their POSIX meaning instead of
// becoming AVAudioFile's opaque 'wht?' error. Never probe unrelated sources.
template <typename Read, typename Wait>
int sbCloudReadiness(const std::string &path, Read read, Wait wait) {
    if (path.find("/Library/CloudStorage/") == std::string::npos) return 0;
    for (int attempt = 0; ; ++attempt) {
        const int error = read();
        if (error != ENEEDAUTH || attempt == 2) return error;
        if (!wait()) return ECANCELED;
    }
}

inline int sbReadSourceByte(const std::string &path) {
    FILE *file = std::fopen(path.c_str(), "rb");
    if (!file) return errno;
    errno = 0;
    const int value = std::fgetc(file);
    const int error = value == EOF && std::ferror(file) ? (errno ? errno : EIO) : 0;
    std::fclose(file);
    return error;
}

#endif
