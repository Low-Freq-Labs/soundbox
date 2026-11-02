//! A bounded integer-tempo prior, evaluated against song-wide onset evidence.
//! Kept outside the hash-pinned upstream tree. Never runs on existing user grids.
use rbl_analysis::{onset::OnsetEnvelope, tempo::{beats_of, Segment, TempoResult}};
use serde_json::{json, Value};

fn scores(env: &OnsetEnvelope, period: f64, phase: f64, duration: f64) -> [f64; 3] {
    let mut sums = [0.0; 3];
    let mut counts = [0_u32; 3];
    let first = phase.rem_euclid(period);
    let count = ((duration - first) / period).ceil().max(0.0) as usize;
    for i in 0..count {
        let time = first + i as f64 * period;
        let third = ((time / duration * 3.0) as usize).min(2);
        sums[third] += f64::from(env.sample_at((time - env.origin_secs) * env.rate));
        counts[third] += 1;
    }
    std::array::from_fn(|i| sums[i] / f64::from(counts[i].max(1)))
}

fn fit(env: &OnsetEnvelope, period: f64, phase: f64, duration: f64) -> (f64, [f64; 3]) {
    let mut best = (phase, [0.0; 3]);
    // A phase refit may move the beat, but cannot switch it onto the offbeat.
    let reach = (period * 0.24).min(0.15);
    let steps = (reach / 0.001).ceil() as i32;
    for step in -steps..=steps {
        let candidate = phase + f64::from(step) * 0.001;
        let score = scores(env, period, candidate, duration);
        if score.iter().sum::<f64>() > best.1.iter().sum::<f64>() {
            best = (candidate, score);
        }
    }
    best
}

pub fn prefer_integer(grid: &mut TempoResult, env: &OnsetEnvelope, duration: f64,
                      min_bpm: f64, max_bpm: f64) -> Value {
    let raw_bpm = grid.bpm;
    let integer = raw_bpm.round();
    let mut report = json!({"policy":"whole-onset-v1", "raw_bpm":raw_bpm,
                            "candidate_bpm":integer, "selected":false});
    if !raw_bpm.is_finite() || raw_bpm <= 0.0 || grid.confidence < 0.5
        || grid.segments.len() != 1 || grid.beats.len() < 32
        || !(24.0..=1800.0).contains(&duration) || env.is_empty()
        || (integer - raw_bpm).abs() > 0.25 || integer < min_bpm || integer > max_bpm {
        return report;
    }
    let original = grid.segments[0];
    let (_, raw_score) = fit(env, original.period_secs, original.phase_secs, duration);
    let period = 60.0 / integer;
    let (phase, score) = fit(env, period, original.phase_secs, duration);
    report["raw_onset_fit"] = json!(raw_score);
    report["integer_onset_fit"] = json!(score);
    // Every third must have evidence and remain competitive. A global average
    // alone can hide drift late in a song or a genuine local tempo change.
    let supported = score.iter().all(|s| *s > 0.01)
        && score.iter().sum::<f64>() >= raw_score.iter().sum::<f64>() * 0.97
        && score.iter().zip(raw_score).filter(|(new,old)| **new>=*old).count()>=2
        && score.iter().zip(raw_score).all(|(new, old)| *new >= old * 0.50);
    if !supported { return report; }
    // The caller aligns the selected phase to attacks in the captured PCM.
    // Subtracting a full-track raw refit here would reintroduce the original
    // slope error as a constant timing offset.
    let segment = Segment { period_secs:period, phase_secs:phase, ..original };
    let first = segment.start_secs();
    let number = grid.beats.iter().min_by_key(|b|
        (f64::from(b.time_ms) - first * 1000.0).abs().round() as u64)
        .map_or(1, |b| b.beat_number);
    grid.segments = vec![segment];
    grid.beats = beats_of(&grid.segments, usize::from(number - 1));
    grid.bpm = integer;
    grid.first_beat_secs = first;
    report["selected"] = json!(true);
    report["onset_phase_adjustment_ms"] = json!((phase - original.phase_secs) * 1000.0);
    report
}

pub fn align_attacks(grid: &mut TempoResult, attacks: &rbl_analysis::attack::AttackMap,
                     duration: f64, before: &TempoResult) -> Option<[f64; 3]> {
    let mut residuals = [Vec::new(), Vec::new(), Vec::new()];
    for beat in &grid.beats {
        let time = f64::from(beat.time_ms) / 1000.0;
        if let Some(attack) = attacks.attack_within(time,0.030) {
            let third = ((time / duration * 3.0) as usize).min(2);
            residuals[third].push((attack.secs-time,attack.height));
        }
    }
    let mut offsets = [0.0; 3];
    for (i, rows) in residuals.iter_mut().enumerate() {
        if rows.len() < 8 { return None; }
        rows.sort_by(|a,b| a.1.total_cmp(&b.1));
        let floor = rows[rows.len()/2].1;
        let mut strong: Vec<f64> = rows.iter().filter(|r|r.1>=floor).map(|r|r.0).collect();
        strong.sort_by(f64::total_cmp);
        offsets[i] = strong[strong.len()/2];
    }
    if offsets.iter().copied().fold(f64::NEG_INFINITY,f64::max)
        - offsets.iter().copied().fold(f64::INFINITY,f64::min) > 0.015 { return None; }
    let mut sorted=offsets;
    sorted.sort_by(f64::total_cmp);
    let original=grid.segments[0];
    let mut segment=Segment{phase_secs:original.phase_secs+sorted[1],..original};
    let phase=segment.phase_secs.rem_euclid(segment.period_secs);
    if before.beats.first().is_some_and(|b|b.time_ms==0)
        && phase.min(segment.period_secs-phase)<0.015 {
        segment.phase_secs=0.0;
    }
    let first=segment.start_secs();
    let number=before.beats.iter().min_by_key(|b|
        (f64::from(b.time_ms)-first*1000.0).abs().round() as u64)
        .map_or(1,|b|b.beat_number);
    grid.segments=vec![segment];
    grid.beats=beats_of(&grid.segments,usize::from(number-1));
    grid.first_beat_secs=first;
    Some(offsets.map(|s|s*1000.0))
}

/// Recover a serialized boundary beat without shifting any fitted later beat.
/// Beat 2 one period into a strong audio opening implies the omitted beat 1.
/// Silent intros and grids with a deliberate later first beat remain unchanged.
pub fn restore_opening_beat(grid: &mut TempoResult, samples: &[f32], rate: u32) -> bool {
    let Some(first)=grid.beats.first() else { return false; };
    if rate==0 || first.beat_number!=2 || grid.bpm<=0.0
        || (f64::from(first.time_ms)-60_000.0/grid.bpm).abs()>15.0 { return false; }
    let peak=|n:usize| samples.iter().take(n).fold(0.0_f32,|p,s|p.max(s.abs()));
    let local=peak((rate as usize*30/1000).max(1));
    if local<1e-5 || peak((rate as usize/1000).max(1))<local*0.01
        || peak((rate as usize*4/1000).max(1))<local*0.1 { return false; }
    let tempo=first.tempo_x100;
    grid.beats.insert(0,rbl_analysis::Beat{beat_number:1,tempo_x100:tempo,time_ms:0});
    grid.first_beat_secs=0.0;
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    fn grid(bpm: f64, duration: f64) -> TempoResult {
        let segment = Segment { from_secs:0.0, to_secs:duration, period_secs:60.0/bpm, phase_secs:0.2 };
        TempoResult { bpm, confidence:1.0, first_beat_secs:0.2,
            segments:vec![segment], beats:beats_of(&[segment],0) }
    }
    fn envelope(bpm: f64, duration: f64) -> OnsetEnvelope {
        let values = (0..(duration * 1000.0) as usize).map(|i| {
            let time = i as f64 / 1000.0;
            let offset = (time - 0.2 + 30.0/bpm).rem_euclid(60.0/bpm) - 30.0/bpm;
            (-0.5 * (offset/0.004).powi(2)).exp() as f32
        }).collect();
        OnsetEnvelope { values, rate:1000.0, origin_secs:0.0 }
    }
    #[test]
    fn missing_opening_downbeat_is_added_without_moving_later_rows_or_silent_intros() {
        let mut result=grid(130.0,12.0);
        result.beats=vec![rbl_analysis::Beat{beat_number:2,tempo_x100:13000,time_ms:461},
                         rbl_analysis::Beat{beat_number:3,tempo_x100:13000,time_ms:923}];
        let later=result.beats.clone();
        let mut silent=result.clone();
        assert!(!restore_opening_beat(&mut silent,&vec![0.0;2000],44100));
        assert_eq!(silent.beats,later);
        let mut attack=vec![0.0;2000];attack[1]=0.5;
        assert!(restore_opening_beat(&mut result,&attack,44100));
        assert_eq!(result.beats[0].time_ms,0);assert_eq!(result.beats[0].beat_number,1);
        assert_eq!(&result.beats[1..],later);
        assert!(!restore_opening_beat(&mut result,&attack,44100));
        let mut delayed=silent.clone();delayed.beats[0].time_ms=520;
        assert!(!restore_opening_beat(&mut delayed,&attack,44100));
        let mut rise=vec![0.016_f32;2000];rise[130]=0.411;rise[300]=0.740;
        assert!(restore_opening_beat(&mut silent.clone(),&rise,44100));
        let mut late=vec![0.001_f32;2000];late[900]=0.740;
        assert!(!restore_opening_beat(&mut silent,&late,44100));
    }
    #[test]
    fn biased_estimates_choose_whole_bpm_and_regenerate_phase_and_rows() {
        for raw in [119.85_f64,119.9,120.1,139.9,140.1] {
            let expected = raw.round();
            let mut result = grid(raw,132.0);
            let report = prefer_integer(&mut result,&envelope(expected,132.0),132.0,70.0,180.0);
            assert_eq!(report["selected"],true,"{raw}: {report}");
            assert_eq!(result.bpm,expected);
            assert!(result.beats.iter().all(|b| f64::from(b.tempo_x100)==expected*100.0));
            for beat in result.beats {
                let phase = (f64::from(beat.time_ms)/1000.0-0.2).rem_euclid(60.0/expected);
                assert!(phase.min(60.0/expected-phase)<0.004);
            }
        }
    }
    #[test]
    fn genuine_fractional_music_and_variable_or_truncated_analysis_are_retained() {
        for bpm in [119.85,119.9,120.1,120.13,139.9,140.1,127.5] {
            let mut result = grid(bpm,180.0);
            let before = result.beats.clone();
            let report = prefer_integer(&mut result,&envelope(bpm,180.0),180.0,70.0,180.0);
            assert_eq!(report["selected"],false,"{bpm}: {report}");
            assert_eq!(result.beats,before);
        }
        let mut variable=grid(119.9,132.0);
        variable.segments.push(Segment{from_secs:100.0,to_secs:132.0,period_secs:0.4,phase_secs:100.0});
        let before=variable.beats.clone();
        assert_eq!(prefer_integer(&mut variable,&envelope(120.0,132.0),132.0,70.0,180.0)["selected"],false);
        assert_eq!(variable.beats,before);
        let mut head=grid(119.9,1800.0);
        assert_eq!(prefer_integer(&mut head,&envelope(120.0,1.0),1801.0,70.0,180.0)["selected"],false);
    }
}
