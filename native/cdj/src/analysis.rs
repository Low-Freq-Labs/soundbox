//! Bounded, read-only DSP over one captured local file; no source-library API.
use std::{error::Error, path::Path};
use serde::Deserialize;
use serde_json::json;

const MAX_SECONDS: usize = 300;
const MAX_SAMPLES: usize = 32_000_000;

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
    // Check the decoded budget BEFORE allocating/collecting. Bound every append
    // even when a packet straddles the last sample. Long mixes keep one buffer.
    let cap = (MAX_SECONDS * rate as usize).min(MAX_SAMPLES);
    let expected = stream.expected_frames();
    let mut samples = Vec::with_capacity(cap.min(expected.unwrap_or(cap as u64) as usize));
    while samples.len() < cap {
        let Some(chunk) = stream.next_chunk() else { break };
        let count = chunk.len().min(cap - samples.len());
        samples.extend_from_slice(&chunk[..count]);
        if stream.sample_rate() != rate { return Err("sample rate changed during decode".into()); }
    }
    if samples.len() < rate as usize * 4 || samples.iter().any(|x| !x.is_finite()) {
        return Err("at least four seconds of finite audio are required".into());
    }
    if expected.is_some_and(|n| samples.len() < cap && (samples.len() as u64) + 1 < n) {
        return Err("audio ended before its declared duration".into());
    }
    let duration = expected.unwrap_or(samples.len() as u64) as f64 / f64::from(rate);
    if !duration.is_finite() || !(4.0..=86400.0).contains(&duration) {
        return Err("unsupported analysis duration".into());
    }
    let (mut tempo, key) = if input.bpm {
        let mut options = rbl_analysis::AnalysisPreset::Rekordbox.options();
        options.tempo.min_bpm = f64::from(input.min_bpm);
        options.tempo.max_bpm = f64::from(input.max_bpm);
        let result = rbl_analysis::analyse_with(&samples, rate, options);
        (Some(result.tempo), if input.key { result.key } else { None })
    } else {
        (None, rbl_analysis::key::detect_key(&samples, rate))
    };
    if let Some(ref mut grid) = tempo { grid.extend_to(duration); }
    let beats = tempo.as_ref().map(|g| g.beats.iter().map(|b|
        json!([b.beat_number, b.time_ms, f64::from(b.tempo_x100)/100.0])).collect::<Vec<_>>());
    Ok(json!({"bpm":tempo.as_ref().map(|g|g.bpm), "confidence":tempo.as_ref().map(|g|g.confidence),
        "beats":beats, "key":key.as_ref().map(|k|k.camelot()), "key_name":key.as_ref().map(|k|&k.name),
        "duration":duration, "analyzed_seconds":samples.len() as f64/f64::from(rate)}))
}
