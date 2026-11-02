//! TEMP-only initial-analysis packet proof. No SQL, source publisher or discovery.
use std::{error::Error, path::{Path, PathBuf}};
use serde::Deserialize;
use serde_json::json;
use std::os::unix::fs::PermissionsExt;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Beat { number: u16, tempo: u16, time_ms: u32 }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    audio: PathBuf,
    original_name: String,
    beats: Vec<Beat>,
}

fn local(root: &Path, path: &Path) -> Result<PathBuf, Box<dyn Error>> {
    let canonical = path.canonicalize()?;
    if canonical != path || !canonical.starts_with(root) || !canonical.is_file() {
        return Err("packet inputs must be original regular local TEMP files".into());
    }
    Ok(canonical)
}

pub fn run(root: &Path, input: &Path) -> Result<serde_json::Value, Box<dyn Error>> {
    if root.canonicalize()? != root || !root.starts_with(std::env::temp_dir().canonicalize()?)
        || root.metadata()?.permissions().mode() & 0o077 != 0 {
        return Err("packet rendering requires an explicit canonical TEMP root".into());
    }
    let input = local(root, input)?;
    if input.metadata()?.len() > 1_048_576 { return Err("packet request budget exceeded".into()); }
    let request: Request = serde_json::from_slice(&std::fs::read(input)?)?;
    let audio = local(root, &request.audio)?;
    if request.original_name != audio.file_name().ok_or("missing audio name")?.to_string_lossy()
        || request.original_name.contains(['/', '\\', '\0']) {
        return Err("packet name must match the captured canonical filename".into());
    }
    let mut stream = rbl_audio::MonoStream::open(&audio)?;
    let rate = stream.sample_rate();
    if !(8_000..=192_000).contains(&rate) || !(1..=8).contains(&stream.source_channels()) {
        return Err("unsupported packet audio format".into());
    }
    let cap = (1800 * rate as usize).min(96_000_000);
    let expected = stream.expected_frames().ok_or("packet proof requires a measured endpoint")?;
    if expected == 0 || expected > cap as u64 {
        return Err("packet proof requires the complete bounded track".into());
    }
    let channels = stream.source_channels();
    let mut samples = Vec::with_capacity(expected as usize);
    let mut decoded = 0_u64;
    while let Some(chunk) = stream.next_chunk() {
        if chunk.iter().any(|s| !s.is_finite()) {
            return Err("audio contains non-finite samples".into());
        }
        let count = chunk.len().min(expected.saturating_sub(decoded) as usize);
        if samples.len().saturating_add(count) > cap { return Err("packet sample budget exceeded".into()); }
        samples.extend_from_slice(&chunk[..count]);
        decoded = decoded.checked_add(chunk.len() as u64).ok_or("frame count overflow")?;
        if stream.sample_rate() != rate || stream.source_channels() != channels {
            return Err("audio changed format".into());
        }
    }
    if decoded + 1 < expected || samples.len() as u64 != expected {
        return Err("packet audio ended before its declared endpoint".into());
    }
    let duration_ms = expected as f64 * 1000.0 / f64::from(rate);
    if request.beats.len() < 2 || request.beats.iter().any(|b|
        !(1..=4).contains(&b.number) || b.tempo == 0 || f64::from(b.time_ms) > duration_ms)
        || request.beats.windows(2).any(|w| w[0].time_ms >= w[1].time_ms || w[1].number != w[0].number % 4 + 1) {
        return Err("packet grid is invalid or exceeds the captured audio".into());
    }
    let wave = rbl_analysis::waveform::compute(&samples, rate);
    let columns = wave.columns.iter().map(|c| rbl_anlz::encode::BandColumn {
        low: c.low, mid: c.mid, high: c.high, peak: c.peak,
    }).collect::<Vec<_>>();
    let overview: [[u8; 3]; 1200] = wave.overview.try_into().map_err(|_| "invalid overview extent")?;
    let beats = request.beats.iter().map(|b| rbl_anlz::Beat {
        beat_number: b.number, tempo_x100: b.tempo, time_ms: b.time_ms,
    }).collect::<Vec<_>>();
    // Accepted native desktop header/opaque colour configuration. Musical values
    // always come from this captured audio and the explicit saved grid.
    let extra = [0,0,0,1,0,1,0,0,0,1,0,0,0,0,0,0];
    let dat = rbl_anlz::parse(&rbl_anlz::write::render(&extra,&[]))?;
    let ext = dat.clone();
    let two_ex = rbl_anlz::parse(&rbl_anlz::write::render(&extra,&[
        rbl_anlz::Section::new(b"PWVC",vec![0,0],vec![0,187,1,44,1,244])]))?;
    let mut files = rbl_anlz::encode::author_with_overview(
        &rbl_anlz::encode::share_tree_ppth(&request.original_name), &beats, &columns, Some(&overview),
        rbl_anlz::encode::Existing { dat: Some(&dat), ext: Some(&ext), two_ex: Some(&two_ex) });
    // Use the exact native invalidation shape, already independently observed
    // for manual primary-grid changes. Its suitability for *initial* analysis
    // remains a separate native acceptance question, not assumed by this proof.
    let mut parsed = rbl_anlz::parse(&files.ext)?;
    let index = parsed.sections.iter().position(|s| s.tag.0 == *b"PWV5").ok_or("missing colour detail")?;
    parsed.sections.insert(index, rbl_anlz::write::extended_grid_empty_section());
    files.ext = rbl_anlz::write::render(&parsed.header_extra, &parsed.sections);
    let output = root.join("packet"); std::fs::create_dir(&output)?;
    std::fs::write(output.join("ANLZ0000.DAT"), &files.dat)?;
    std::fs::write(output.join("ANLZ0000.EXT"), &files.ext)?;
    std::fs::write(output.join("ANLZ0000.2EX"), &files.two_ex)?;
    let peak = samples.iter().fold(0.0_f32, |p,s| p.max(s.abs()));
    let rms = (samples.iter().map(|s| f64::from(*s).powi(2)).sum::<f64>() / samples.len() as f64).sqrt();
    Ok(json!({ "frames": expected, "rate": rate, "channels": channels,
        "duration_ms": duration_ms, "detail_columns": columns.len(), "peak": peak, "rms": rms,
        "dat_bytes": files.dat.len(), "ext_bytes": files.ext.len(), "two_ex_bytes": files.two_ex.len(),
        "source_write_authority": false }))
}
