#ifndef SOUNDBOX_DECK_H
#define SOUNDBOX_DECK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SB_DECK_ABI_VERSION 1u

typedef void *SBDeckRef;

enum SBDeckPlaybackState {
    SB_DECK_STOPPED = 0,
    SB_DECK_PLAYING = 1,
    SB_DECK_PAUSED = 2,
};

typedef struct SBDeckSnapshot {
    uint32_t abi_version;
    uint32_t playback_state;
    uint64_t position_frames;
    uint64_t duration_frames;
    double sample_rate;
    uint64_t underruns;
    uint64_t generation;
    uint64_t ramp_completed;
    int32_t error_code;
    char error_message[256];
} SBDeckSnapshot;

uint32_t sb_deck_abi_version(void);
int sb_deck_is_supported(void);

SBDeckRef sb_deck_create(void *parent_nsview);
void sb_deck_destroy(SBDeckRef deck);

int sb_deck_load(SBDeckRef deck, const char *utf8_path);
int sb_deck_begin_track(SBDeckRef deck, uint64_t generation);
void sb_deck_play(SBDeckRef deck);
void sb_deck_pause(SBDeckRef deck);
void sb_deck_stop(SBDeckRef deck);
void sb_deck_seek_frames(SBDeckRef deck, uint64_t source_frame);

void sb_deck_set_gain(SBDeckRef deck, float gain);
uint64_t sb_deck_ramp_gain(SBDeckRef deck, float gain, double duration_ms);

void sb_deck_set_preview_frame(
    SBDeckRef deck, uint64_t source_frame, int preview_active);
void sb_deck_set_zoom(SBDeckRef deck, float pixels_per_second);
void sb_deck_request_display(SBDeckRef deck);
void sb_deck_sync_view_geometry(SBDeckRef deck);
int sb_deck_set_analysis(
    SBDeckRef deck,
    const float *heights,
    const uint8_t *rgb,
    size_t column_count,
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count);
int sb_deck_set_packed_analysis_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint8_t *heights,
    const uint8_t *rgb,
    size_t column_count,
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count);
int sb_deck_set_packed_overview_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint8_t *heights,
    const uint8_t *rgb,
    size_t column_count,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count);
int sb_deck_set_detail_analysis(
    SBDeckRef deck,
    const float *heights,
    const uint8_t *rgb,
    size_t sample_count,
    double samples_per_second);
int sb_deck_set_detail_bands(
    SBDeckRef deck, const uint8_t *bands, size_t sample_count);
int sb_deck_set_spectral_analysis(
    SBDeckRef deck,
    const uint8_t *bands,
    size_t frame_count,
    size_t bands_per_frame,
    double frames_per_second);  // legacy no-op; RGB is supplied explicitly
int sb_deck_set_audio_detail(
    SBDeckRef deck,
    const uint8_t *heights,
    size_t sample_count,
    double samples_per_second);
int sb_deck_set_audio_detail_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint8_t *heights,
    size_t sample_count,
    double samples_per_second);
int sb_deck_set_cues(
    SBDeckRef deck,
    const uint32_t *position_millis,
    const uint8_t *kinds,
    const uint8_t *slots,
    const uint8_t *rgb,
    size_t cue_count);
int sb_deck_set_session_cue(
    SBDeckRef deck, uint32_t position_millis, int active);
int sb_deck_snapshot(SBDeckRef deck, SBDeckSnapshot *out_snapshot);
uint64_t sb_deck_visual_frame(SBDeckRef deck);
uint64_t sb_deck_analysis_sample_count(SBDeckRef deck);
double sb_deck_analysis_sample_rate(SBDeckRef deck);
uint64_t sb_deck_detail_sample_count(SBDeckRef deck);
double sb_deck_detail_sample_rate(SBDeckRef deck);
double sb_deck_detail_peak_height(SBDeckRef deck);
uint64_t sb_deck_audio_detail_mip_count(SBDeckRef deck);
uint64_t sb_deck_audio_detail_buffer_bytes(SBDeckRef deck);
uint64_t sb_deck_cue_marker_count(SBDeckRef deck);
double sb_deck_pixels_per_second(SBDeckRef deck);
uint64_t sb_deck_display_callback_count(SBDeckRef deck);
double sb_deck_display_average_fps(SBDeckRef deck);
double sb_deck_display_max_gap_ms(SBDeckRef deck);
void sb_deck_reset_display_stats(SBDeckRef deck);
int sb_deck_view_geometry(
    SBDeckRef deck,
    double *parent_width_points,
    double *view_width_points,
    double *drawable_width_pixels);

#ifdef __cplusplus
}
#endif

#endif
