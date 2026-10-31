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
    reference_dat: PathBuf,
    reference_ext: PathBuf,
    reference_2ex: PathBuf,
}

fn local(root: &Path, path: &Path) -> Result<PathBuf, Box<dyn Error>> {
    let canonical = path.canonicalize()?;
    if canonical != path || !canonical.starts_with(root) || !canonical.is_file() {
        return Err("packet inputs must be original regular local TEMP files".into());
    }
    Ok(canonical)
}

fn run(root: &Path, input: &Path) -> Result<(), Box<dyn Error>> {
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
    // The explicit reference supplies opaque headers/PWVC, not musical grid or
    // waveform values. The Python proof binds it to the captured native case.
    let reference = |path: &Path| -> Result<rbl_anlz::Anlz, Box<dyn Error>> {
        let path = local(root, path)?;
        if path.metadata()?.len() > 64 * 1024 * 1024 { return Err("reference packet budget exceeded".into()); }
        Ok(rbl_anlz::Anlz::read(&path)?)
    };
    let dat = reference(&request.reference_dat)?;
    let ext = reference(&request.reference_ext)?;
    let two_ex = reference(&request.reference_2ex)?;
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
    println!("{}", json!({ "frames": expected, "rate": rate, "channels": channels,
        "duration_ms": duration_ms, "detail_columns": columns.len(), "peak": peak, "rms": rms,
        "dat_bytes": files.dat.len(), "ext_bytes": files.ext.len(), "two_ex_bytes": files.two_ex.len(),
        "source_write_authority": false }));
    Ok(())
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = std::env::args_os().skip(1).map(PathBuf::from).collect::<Vec<_>>();
    if args.len() != 2 { return Err("usage: soundbox-analysis-proof TEMP_ROOT REQUEST_JSON".into()); }
    run(&args[0], &args[1])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(root: &Path) -> PathBuf {
        std::fs::set_permissions(root, std::fs::Permissions::from_mode(0o700)).unwrap();
        let audio = root.join("synthetic.wav");
        let data_size = 44_100_u32 * 4 * 4;
        let mut raw = b"RIFF".to_vec(); raw.extend((data_size + 36).to_le_bytes()); raw.extend(b"WAVEfmt ");
        raw.extend(16_u32.to_le_bytes()); raw.extend(1_u16.to_le_bytes()); raw.extend(2_u16.to_le_bytes());
        raw.extend(44_100_u32.to_le_bytes()); raw.extend(176_400_u32.to_le_bytes()); raw.extend(4_u16.to_le_bytes());
        raw.extend(16_u16.to_le_bytes()); raw.extend(b"data"); raw.extend(data_size.to_le_bytes());
        raw.resize(44 + data_size as usize, 0); std::fs::write(&audio, raw).unwrap();
        for ext in ["DAT", "EXT", "2EX"] {
            let mut builder = rbl_anlz::AnlzBuilder::new(); builder.path("?/original.wav");
            if ext == "2EX" {
                builder.copy_section(&rbl_anlz::Section::new(b"PWVC", vec![0,0], vec![0,187,1,44,1,244]));
            }
            std::fs::write(root.join(format!("ref.{ext}")), builder.finish()).unwrap();
        }
        let request = root.join("request.json");
        std::fs::write(&request, serde_json::to_vec(&json!({
            "audio":audio, "original_name":"synthetic.wav",
            "beats":[{"number":1,"tempo":12000,"time_ms":0},{"number":2,"tempo":12000,"time_ms":500}],
            "reference_dat":root.join("ref.DAT"),"reference_ext":root.join("ref.EXT"),"reference_2ex":root.join("ref.2EX")
        })).unwrap()).unwrap();
        request
    }

    #[test]
    fn authors_supplied_grid_and_retains_opaque_frame_without_writing_inputs() {
        let temp = tempfile::tempdir().unwrap(); let root = temp.path().canonicalize().unwrap();
        let request = fixture(&root); let before = std::fs::read(root.join("synthetic.wav")).unwrap();
        run(&root, &request).unwrap();
        let dat = rbl_anlz::Anlz::read(&root.join("packet/ANLZ0000.DAT")).unwrap();
        let grid = dat.beat_grid().unwrap(); assert_eq!(grid[1].time_ms,500); assert_eq!(grid[1].tempo_x100,12000);
        let ext = rbl_anlz::Anlz::read(&root.join("packet/ANLZ0000.EXT")).unwrap();
        let secondary = ext.section(b"PQT2").unwrap();
        assert_eq!(secondary.len_tag(),56); assert!(secondary.payload.is_empty());
        assert_eq!(&secondary.header[..12],&[0,0,0,0,1,0,0,2,0,0,0,0]);
        assert!(secondary.header[12..].iter().all(|b| *b==0));
        let extra = rbl_anlz::Anlz::read(&root.join("packet/ANLZ0000.2EX")).unwrap();
        assert_eq!(extra.section(b"PWVC").unwrap().payload,vec![0,187,1,44,1,244]);
        assert_eq!(std::fs::read(root.join("synthetic.wav")).unwrap(),before);
        assert!(run(&root, &request).is_err()); // never replace an existing packet
    }

    #[test]
    fn refuses_aliases_unknown_units_and_grid_outside_the_source() {
        let temp = tempfile::tempdir().unwrap(); let root = temp.path().canonicalize().unwrap();
        let request = fixture(&root);
        let original: serde_json::Value = serde_json::from_slice(&std::fs::read(&request).unwrap()).unwrap();
        for edit in ["name","endpoint","number","unknown"] {
            let mut value = original.clone();
            match edit {
                "name" => value["original_name"] = json!("../other.wav"),
                "endpoint" => value["beats"][1]["time_ms"] = json!(5000),
                "number" => value["beats"][1]["number"] = json!(4),
                _ => value["publish_to_source"] = json!(true),
            }
            std::fs::write(&request,serde_json::to_vec(&value).unwrap()).unwrap();
            assert!(run(&root,&request).is_err()); assert!(!root.join("packet").exists());
        }
        std::os::unix::fs::symlink(root.join("synthetic.wav"),root.join("alias.wav")).unwrap();
        assert!(local(&root,&root.join("alias.wav")).is_err());
        assert!(run(&root.parent().unwrap().join(".."),&request).is_err());
    }
}
