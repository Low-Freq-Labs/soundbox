//! Tempo-only projection of pinned rbxport b739d369's analyse_with pipeline.
//! Preserve its tempo/downbeat/boundary math while deferring key DSP to the
//! next durable component phase. The vendored source remains byte-exact.
use rbl_analysis::{attack, downbeat, onset, tempo, AnalysisOptions, Beat, TempoResult};

pub fn analyse(samples: &[f32], sample_rate: u32, options: AnalysisOptions) -> TempoResult {
    let (onsets, kicks, attacks, frames) = std::thread::scope(|scope| {
        let onsets = scope.spawn(|| onset::onset_envelope(samples, sample_rate));
        let kicks = scope.spawn(|| onset::onset_envelope_band(samples, sample_rate, onset::Band::LOW));
        let attacks = scope.spawn(|| match options.tempo.placement {
            tempo::Placement::Attack => Some(attack::AttackMap::new(samples, sample_rate, options.attacks)),
            tempo::Placement::Envelope => None,
        });
        let frames = scope.spawn(|| downbeat::band_frames(samples, sample_rate));
        (onsets.join().unwrap_or_else(|_| onset::OnsetEnvelope { values: Vec::new(), rate: 0.0, origin_secs: 0.0 }),
         kicks.join().ok(), attacks.join().ok().flatten(), frames.join().unwrap_or_default())
    });
    let mut tempo = tempo::detect_tempo_with(&onsets, kicks.as_ref(), attacks.as_ref(), options.tempo);
    // The grid comes back numbered from its first beat, and possibly on the
    // off-beat. The downbeat stage looks at the music's structure and says
    // both; the grid is moved if it has to be and renumbered so that 1 is
    // the downbeat.
    let beat_secs: Vec<f64> = tempo.beats.iter().map(|b| f64::from(b.time_ms) / 1000.0).collect();
    // The downbeat stage decides both which half of the beat the kicks
    // are on and which beat is 1. The fit on the kick's attacks has a view
    // on the first question too, but the phrase structure is the better
    // judge: on the golden playlist it tells the beat from the midpoint on
    // 153 of 155 rekordbox grids, the kick detector on 150 — the misses
    // being off-beat claps with a sharper transient than the kick.
    let grid = downbeat::grid_phase_from(&frames, sample_rate, &beat_secs);
    if grid.half_beat_off {
        for segment in &mut tempo.segments {
            *segment = segment.shifted_half_beat();
        }
        tempo.first_beat_secs = tempo.segments.first().map_or(0.0, tempo::Segment::start_secs);
    }
    // The beat nearest the chosen downbeat is beat 1, and the count runs
    // on from there through every segment, across every tempo change, as
    // rekordbox numbers a hand grid: on all ten changes in the multi-tempo
    // playlist the new tempo's first beat carries the number after the old
    // tempo's last, whatever the music does there. So beat 1 is decided on
    // the first tempo's own beats when it has phrases of its own: the
    // later tempos' downbeats are wherever the carried count puts them,
    // and a track-wide vote they outnumber would move the first tempo's
    // beat 1 onto theirs.
    let downbeat_secs = match tempo.segments.first() {
        Some(first) if tempo.segments.len() > 1 && first.beats() >= downbeat::MIN_BEATS_FOR_OWN_PHASE => {
            let own: Vec<f64> = tempo::beats_of(&tempo.segments[..1], 0).iter().map(|b| f64::from(b.time_ms) / 1000.0).collect();
            downbeat::grid_phase_from(&frames, sample_rate, &own).downbeat_secs
        }
        _ => grid.downbeat_secs,
    };
    tempo.beats = tempo::beats_of(&tempo.segments, phase_for(&tempo.segments, downbeat_secs));
    anchor_file_start(&mut tempo, samples, sample_rate, downbeat_secs);
    tempo
}

fn phase_for(segments: &[tempo::Segment], downbeat_secs: f64) -> usize {
    let unnumbered = tempo::beats_of(segments, 0);
    let index = unnumbered
        .iter()
        .enumerate()
        .min_by(|a, b| {
            let da = (f64::from(a.1.time_ms) / 1000.0 - downbeat_secs).abs();
            let db = (f64::from(b.1.time_ms) / 1000.0 - downbeat_secs).abs();
            da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
        })
        .map_or(0, |(i, _)| i);
    (4 - index % 4) % 4
}

fn anchor_file_start(tempo: &mut TempoResult, samples: &[f32], sample_rate: u32, downbeat_secs: f64) {
    let Some(first) = tempo.segments.first() else { return };
    let reach = attack::AttackOptions::default().reach_secs;
    if sample_rate == 0 || first.from_secs > reach || first.period_secs <= 0.0 || tempo.beats.is_empty() {
        return;
    }
    // Preserve a distinct later musical downbeat. The fallback downbeat
    // at the first emitted beat may itself be the boundary omission.
    let cut = opening_cut_secs(first, samples, sample_rate);
    let bar = 4.0 * first.period_secs;
    let downbeat_phase = (downbeat_secs + cut.max(0.0)).rem_euclid(bar);
    if downbeat_secs > first.start_secs() + reach && downbeat_phase.min(bar - downbeat_phase) > reach {
        return;
    }
    let phase = first.phase_secs.rem_euclid(first.period_secs);
    let clipped = cut > 0.5 / f64::from(sample_rate);
    if (clipped && cut >= 0.020 - 0.5 / f64::from(sample_rate))
        || (!clipped && phase.min(first.period_secs - phase) > reach)
    {
        return;
    }
    // A sample at exactly zero may be a waveform's zero crossing. Compare
    // the first millisecond with the local peak, allowing that crossing but
    // rejecting a later attack and negligible leading noise.
    let opening = (sample_rate as usize / 1000).max(1);
    let window = (f64::from(sample_rate) * reach).ceil() as usize;
    let peak = |n| samples.iter().take(n).fold(0.0_f32, |p, s| p.max(s.abs()));
    let local_peak = peak(window);
    if local_peak < 1e-5 || peak(opening) < local_peak * 0.1 {
        return;
    }
    if clipped {
        // Keep the fitted segment and every later beat in place. A one-beat
        // opening segment represents the shortened interval without changing
        // the track's BPM; serialization reads the explicit beat timestamps.
        let next = first.start_secs();
        let opening = tempo::Segment { from_secs: 0.0, to_secs: next, phase_secs: 0.0, ..*first };
        tempo.segments[0].from_secs = next;
        tempo.segments.insert(0, opening);
        tempo.first_beat_secs = 0.0;
        tempo.beats.insert(0, Beat { beat_number: 1, tempo_x100: tempo.beats[0].tempo_x100, time_ms: 0 });
        for (i, beat) in tempo.beats.iter_mut().enumerate() {
            beat.beat_number = (i % 4 + 1) as u16;
        }
        return;
    }
    // The envelope starts at its first frame centre, a few milliseconds
    // into the file. Include the recovered boundary beat in the segment.
    tempo.segments[0].from_secs = 0.0;
    tempo.segments[0].phase_secs = 0.0;
    tempo.first_beat_secs = 0.0;
    tempo.beats = tempo::beats_of(&tempo.segments, 0);
}

/// Estimate how far the opening beat precedes the file. An isolated next
/// attack can refine the estimate to a sample: the RMS attack fit is about
/// a millisecond early, which must not turn a 19 ms trim into a 20 ms one.
/// With continuous audio around that beat, keep the fitted estimate.
fn opening_cut_secs(first: &tempo::Segment, samples: &[f32], sample_rate: u32) -> f64 {
    let next = first.start_secs();
    let inferred = first.period_secs - next;
    if inferred <= 0.0 || inferred > 0.035 {
        return 0.0;
    }
    let rate = f64::from(sample_rate);
    let lo = ((next - 0.015).max(0.0) * rate) as usize;
    let hi = (((next + 0.015) * rate) as usize).min(samples.len());
    let Some(window) = samples.get(lo..hi) else { return inferred };
    let peak = window.iter().fold(0.0_f32, |p, s| p.max(s.abs()));
    let threshold = peak * 0.01;
    let Some(index) = window.iter().position(|s| s.abs() > threshold) else { return inferred };
    // Require quiet before this attack. Otherwise this could be a waveform
    // crossing in sustained audio rather than the start of the next beat.
    if index < (sample_rate as usize / 1000).max(1) || peak < 1e-5 {
        return inferred;
    }
    let now = f64::from(window[index].abs());
    let previous = f64::from(window[index - 1].abs());
    let onset_sample = (lo as f64 + index as f64 - now / (now - previous)).round();
    first.period_secs - onset_sample / rate
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tempo_only_matches_pinned_combined_analysis_including_boundary_phase() {
        let rate = 16000;
        for (bpm, cut_ms) in [(120.0, 0), (140.0, 13), (155.0, 19)] {
            let mut audio = vec![0.0; rate as usize * 12];
            let step = f64::from(rate) * 60.0 / bpm;
            for beat in 0..40 {
                let start = (f64::from(beat) * step).round() as usize;
                for n in 0..240 {
                    if start + n >= audio.len() { break; }
                    audio[start + n] += ((n as f32 * 0.17).sin()
                        * (1.0 - n as f32 / 240.0)) * 0.8;
                }
            }
            let audio = &audio[(cut_ms * rate as usize / 1000)..];
            let mut options = rbl_analysis::AnalysisPreset::Rbxport.options();
            options.tempo.placement = tempo::Placement::Attack;
            let combined = rbl_analysis::analyse_with(audio, rate, options).tempo;
            let separate = analyse(audio, rate, options);
            assert_eq!(separate.bpm.to_bits(), combined.bpm.to_bits());
            assert_eq!(separate.confidence.to_bits(), combined.confidence.to_bits());
            assert_eq!(separate.first_beat_secs.to_bits(), combined.first_beat_secs.to_bits());
            assert_eq!(separate.beats, combined.beats);
            assert_eq!(separate.segments, combined.segments);
        }
    }
}
