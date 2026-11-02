//! Bounded, read-only DSP over one captured local file; no source-library API.
use std::{error::Error, path::Path};
use serde::Deserialize;
use serde_json::json;

// Match the upstream application's thirty-minute analysis horizon. A separate
// sample budget bounds high-rate inputs; the remaining stream is still decoded
// and checked through EOF before extending its last measured tempo.
const MAX_SECONDS: usize = 1800;
const MAX_SAMPLES: usize = 96_000_000;
const MAX_DURATION: u64 = 86400;

#[derive(Deserialize)]
struct Request {
    audio: std::path::PathBuf,
    bpm: bool,
    key: bool,
    min_bpm: u16,
    max_bpm: u16,
}

pub fn run(root: &Path, request: &Path) -> Result<serde_json::Value, Box<dyn Error>> {
    let input: Request = serde_json::from_slice(&std::fs::read(crate::local(root, request)?)?)?;
    if ![(70,180),(48,95),(58,115),(68,135),(78,155),(88,175),(98,195),(108,215),(118,235),(128,255)]
        .contains(&(input.min_bpm, input.max_bpm)) || !(input.bpm || input.key) {
        return Err("invalid analysis settings".into());
    }
    let audio = crate::local(root, &input.audio)?;
    let mut stream = rbl_audio::MonoStream::open(&audio)?;
    let rate = stream.sample_rate();
    if !(8_000..=192_000).contains(&rate) || !(1..=8).contains(&stream.source_channels()) {
        return Err("unsupported analysis sample rate or channel count".into());
    }
    // Reserve only the bounded head, then stream the complete tail without
    // retaining it. A declared duration is checked even after the head fills.
    let cap = (MAX_SECONDS * rate as usize).min(MAX_SAMPLES);
    let expected = stream.expected_frames();
    let channels = stream.source_channels();
    if expected.is_some_and(|n| n > MAX_DURATION * u64::from(rate)) {
        return Err("unsupported analysis duration".into());
    }
    let mut samples = Vec::with_capacity(cap.min(expected.unwrap_or(cap as u64) as usize));
    let mut decoded: u64 = 0;
    while let Some(chunk) = stream.next_chunk() {
        if chunk.iter().any(|x| !x.is_finite()) {
            return Err("finite audio is required".into());
        }
        let valid = expected.map_or(chunk.len(), |n| n.saturating_sub(decoded).min(chunk.len() as u64) as usize);
        let count = valid.min(cap.saturating_sub(samples.len()));
        samples.extend_from_slice(&chunk[..count]);
        decoded = decoded.checked_add(chunk.len() as u64).ok_or("analysis duration overflow")?;
        if decoded > MAX_DURATION * u64::from(rate) {
            return Err("unsupported analysis duration".into());
        }
        if stream.sample_rate() != rate || stream.source_channels() != channels {
            return Err("audio format changed during decode".into());
        }
    }
    if samples.len() < rate as usize * 4 {
        return Err("at least four seconds of finite audio are required".into());
    }
    if expected.is_some_and(|n| decoded + 1 < n) {
        return Err("audio ended before its declared duration".into());
    }
    let frames = expected.unwrap_or(decoded);
    let duration = frames as f64 / f64::from(rate);
    if !duration.is_finite() || !(4.0..=MAX_DURATION as f64).contains(&duration) {
        return Err("unsupported analysis duration".into());
    }
    let (mut tempo, key) = if input.bpm {
        let mut options = rbl_analysis::AnalysisPreset::Rbxport.options();
        options.tempo.placement = rbl_analysis::tempo::Placement::Attack;
        options.tempo.min_bpm = f64::from(input.min_bpm);
        options.tempo.max_bpm = f64::from(input.max_bpm);
        let result = rbl_analysis::analyse_with(&samples, rate, options);
        (Some(result.tempo), if input.key { result.key } else { None })
    } else {
        (None, rbl_analysis::key::detect_key(&samples, rate))
    };
    let tempo_policy = if let Some(ref mut grid) = tempo {
        let measured = samples.len() as f64 / f64::from(rate);
        if measured >= duration && grid.segments.len() == 1 && (grid.bpm.round()-grid.bpm).abs() <= 0.25 {
            let env = rbl_analysis::onset::onset_envelope(&samples,rate);
            let before=grid.clone();
            let mut policy=soundbox_cdj::whole_tempo::prefer_integer(grid,&env,duration,
                f64::from(input.min_bpm),f64::from(input.max_bpm));
            if policy["selected"]==true {
                let attacks=rbl_analysis::attack::AttackMap::new(&samples,rate,Default::default());
                if let Some(offsets)=soundbox_cdj::whole_tempo::align_attacks(grid,&attacks,duration,&before) {
                    policy["attack_offsets_ms"]=json!(offsets);
                } else {
                    *grid=before;
                    policy["selected"]=json!(false);
                    policy["attack_fit_rejected"]=json!(true);
                }
            }
            Some(policy)
        } else { None }
    } else { None };
    if let Some(ref mut grid) = tempo {
        grid.extend_to(duration);
        soundbox_cdj::whole_tempo::restore_opening_beat(grid,&samples,rate);
    }
    let beats = tempo.as_ref().map(|g| g.beats.iter().map(|b|
        json!([b.beat_number, b.time_ms, f64::from(b.tempo_x100)/100.0])).collect::<Vec<_>>());
    Ok(json!({"bpm":tempo.as_ref().map(|g|g.bpm), "confidence":tempo.as_ref().map(|g|g.confidence),
        "beats":beats, "key":key.as_ref().map(|k|k.camelot()), "key_name":key.as_ref().map(|k|&k.name),
        "duration":duration, "tempo_policy":tempo_policy,
        "analyzed_seconds":samples.len() as f64/f64::from(rate),
        "decoded_frames":decoded, "analyzed_frames":samples.len(),
        "analysis_scope":if samples.len() as u64 >= frames { "full" } else { "head" }}))
}
