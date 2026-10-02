#ifndef SOUNDBOX_DECK_H
#define SOUNDBOX_DECK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SB_DECK_ABI_VERSION 5u

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

typedef struct SBDeckFrameTraceStatus {
    uint32_t abi_version;
    uint32_t active;
    uint32_t max_frames;
    uint32_t submitted_frames;
    uint32_t gpu_completed_frames;
    uint32_t presentation_completed_frames;
    uint32_t crop_width_pixels;
    uint32_t crop_height_pixels;
    uint32_t bytes_per_row;
    uint32_t reserved;
    uint64_t capture_bytes;
} SBDeckFrameTraceStatus;

uint32_t sb_deck_abi_version(void);
int sb_deck_is_supported(void);

SBDeckRef sb_deck_create(void *parent_nsview);
void sb_deck_destroy(SBDeckRef deck);

int sb_deck_load(SBDeckRef deck, const char *utf8_path);
int sb_deck_load_timeline(SBDeckRef deck, const char *utf8_path,
    uint64_t lead_frames, uint64_t expected_frames, double expected_rate);
int sb_deck_begin_track(SBDeckRef deck, uint64_t generation);
void sb_deck_play(SBDeckRef deck);
void sb_deck_pause(SBDeckRef deck);
void sb_deck_stop(SBDeckRef deck);
void sb_deck_seek_frames(SBDeckRef deck, uint64_t source_frame);

void sb_deck_set_gain(SBDeckRef deck, float gain);
uint64_t sb_deck_ramp_gain(SBDeckRef deck, float gain, double duration_ms);

void sb_deck_set_preview_frame(
    SBDeckRef deck, uint64_t source_frame, int preview_active);
uint64_t sb_deck_begin_preview_hold(SBDeckRef deck);
void sb_deck_begin_waveform_update(SBDeckRef deck);
void sb_deck_end_waveform_update(SBDeckRef deck);
float sb_deck_set_zoom(SBDeckRef deck, float pixels_per_second);
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
    double columns_per_second,
    const double *beat_seconds,
    const uint8_t *beat_in_bar,
    size_t beat_count);
int sb_deck_set_packed_overview_u16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *heights,
    const uint8_t *rgb,
    size_t column_count,
    double columns_per_second,
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
int sb_deck_set_waveform_streams(
    SBDeckRef deck,
    const uint8_t *blue_rows,
    size_t blue_count,
    const uint8_t *rgb_rows,
    size_t rgb_count,
    const uint8_t *three_band_rows,
    size_t three_band_count,
    double samples_per_second);
int sb_deck_set_waveform_mode(SBDeckRef deck, uint32_t mode);
uint32_t sb_deck_waveform_mode(SBDeckRef deck);
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
int sb_deck_set_audio_detail_u16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *heights,
    size_t sample_count,
    double samples_per_second);
int sb_deck_set_audio_detail_polarity_u16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *upper_heights,
    const uint16_t *lower_heights,
    size_t sample_count,
    double samples_per_second);
int sb_deck_set_audio_detail_polarity_range_u16_i16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const uint16_t *upper_heights,
    const uint16_t *lower_heights,
    const int16_t *minimum_values,
    const int16_t *maximum_values,
    size_t sample_count,
    double samples_per_second);
int sb_deck_set_audio_detail_range_mips_i16_for_generation(
    SBDeckRef deck,
    uint64_t generation,
    const int16_t *minimum_values,
    const int16_t *maximum_values,
    const uint32_t *level_counts,
    const uint32_t *level_divisors,
    size_t level_count,
    size_t base_sample_count,
    double base_samples_per_second);
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
int sb_deck_has_ended(SBDeckRef deck);
uint64_t sb_deck_visual_frame(SBDeckRef deck);
uint64_t sb_deck_presented_frame(SBDeckRef deck);
uint64_t sb_deck_scheduled_frame(SBDeckRef deck);
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
double sb_deck_presentation_error_mean_ms(SBDeckRef deck);
double sb_deck_presentation_error_stddev_ms(SBDeckRef deck);
double sb_deck_presentation_error_max_ms(SBDeckRef deck);
void sb_deck_reset_display_stats(SBDeckRef deck);
int sb_deck_begin_frame_trace(
    SBDeckRef deck,
    uint32_t max_frames,
    uint32_t crop_width_pixels);
int sb_deck_frame_trace_status(
    SBDeckRef deck,
    SBDeckFrameTraceStatus *out_status);
int sb_deck_finish_frame_trace(
    SBDeckRef deck,
    const char *utf8_output_directory);
void sb_deck_cancel_frame_trace(SBDeckRef deck);
int sb_deck_view_geometry(
    SBDeckRef deck,
    double *parent_width_points,
    double *view_width_points,
    double *drawable_width_pixels);

#ifdef __cplusplus
}
#endif

#endif
