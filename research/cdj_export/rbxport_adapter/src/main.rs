//! Offline dual-format fixture export. All inputs/outputs stay in an explicit
//! private workspace; this program never discovers or writes mounted devices.
use std::error::Error;
use std::path::{Path, PathBuf};
use rbl_export::{SourcePlaylist, SyncSource};
use soundbox_cdj::{Request, complete_metadata};
#[cfg(test)]
use soundbox_cdj::TrackExtra;
#[cfg(test)]
use rbl_export::SourceTrack;

fn confined(root: &Path, path: &Path) -> Result<PathBuf, Box<dyn Error>> {
    let canonical = path.canonicalize()?;
    if !canonical.starts_with(root) || canonical.starts_with("/Volumes") {
        return Err(format!("path outside explicit offline workspace: {}", path.display()).into());
    }
    Ok(canonical)
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 4 {
        return Err("usage: soundbox-cdj-fixture-export WORKSPACE REQUEST_JSON NEW_OUTPUT_DIRECTORY".into());
    }
    let root = Path::new(&args[1]).canonicalize()?;
    if root.starts_with("/Volumes") || root.parent().is_none() {
        return Err("workspace must be an explicit local research directory".into());
    }
    let request_path = confined(&root, Path::new(&args[2]))?;
    let request: Request = serde_json::from_slice(&std::fs::read(request_path)?)?;
    if request.native_profile != "rekordbox-6.8.7.0303" {
        return Err("unsupported fixture metadata profile".into());
    }
    let output = PathBuf::from(&args[3]);
    if output.exists() || output.file_name().is_none() {
        return Err("output must be a new directory".into());
    }
    confined(&root, output.parent().ok_or("output has no parent")?)?;
    for track in &request.tracks {
        confined(&root, &track.source_path)?;
        if let Some(path) = &track.analysis_source {
            confined(&root, path)?;
            for extension in ["DAT", "EXT", "2EX"] {
                let sibling = path.with_extension(extension);
                if sibling.symlink_metadata().is_ok() { confined(&root, &sibling)?; }
            }
        }
        if let Some(path) = &track.artwork { confined(&root, path)?; }
        if track.device.is_some() || track.fallback.is_some() {
            return Err("fixture requests cannot supply retained-device/fallback authority".into());
        }
    }
    let settings_path = confined(&root, &request.settings_database)?;
    let defaults = rbl_onelibrary::settings::StickSettings::read(&settings_path)?;
    let settings: Vec<(String, Vec<u8>)> = request.settings_files.iter().map(|(name, path)| {
        if !["DEVSETTING.DAT", "MYSETTING.DAT", "MYSETTING2.DAT", "DJMMYSETTING.DAT", "djprofile.nxs"].contains(&name.as_str()) {
            return Err("unrecognized settings-file destination".into());
        }
        let path = confined(&root, path)?;
        Ok((name.clone(), std::fs::read(path)?))
    }).collect::<Result<_, Box<dyn Error>>>()?;
    let playlists: Vec<SourcePlaylist> = request.playlists.iter().map(|p| SourcePlaylist {
        id: p.id, name: p.name.clone(), parent_id: p.parent_id, folder: p.folder,
        track_indices: p.track_indices.clone(), ..Default::default()
    }).collect();
    let source = SyncSource { db_id: request.db_id, ..Default::default() };
    std::fs::create_dir(&output)?;
    let options = rbl_export::ExportOptions {
        defaults: Some(&defaults), sync: Some(&source), settings_files: &settings,
        verify_all: true, ..Default::default()
    };
    let report = rbl_export::export_with_options(&output, &request.tracks, &playlists,
        &request.my_tags, &options, &mut |_| {})?;
    let verified = rbl_export::verify(&output)?;
    if !verified.is_ok() { return Err(format!("generated export failed verification: {verified:?}").into()); }
    complete_metadata(&output, &request)?;
    // Keep upstream bookkeeping outside the native-layout fixture. This is a
    // fresh one-shot renderer, not a production ownership/recovery contract.
    let records = output.with_file_name(format!("{}-build-records", output.file_name().ok_or("output name missing")?.to_string_lossy()));
    std::fs::create_dir(&records)?;
    for name in [".rbxport-write.lock", "PIONEER/rbxport/manifest.json", "PIONEER/rekordbox/playlists3.sync", "PIONEER/rekordbox/playlists3Plus.sync"] {
        let source = output.join(name);
        if source.exists() {
            let target = records.join(name);
            std::fs::create_dir_all(target.parent().ok_or("record parent missing")?)?;
            std::fs::rename(source, target)?;
        }
    }
    std::fs::remove_dir(output.join("PIONEER/rbxport"))?;
    println!("{}", serde_json::json!({"output": output, "tracks": report.tracks,
        "playlists": report.playlists, "analysis_files": report.analysis_files,
        "upstream_stage_verified": true, "metadata_completed": true, "native_parity_not_implied": true}));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_escape_and_external_symlink_are_refused() {
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let path = outside.path().join("source.mp3");
        std::fs::write(&path, b"not a source authority").unwrap();
        assert!(confined(root.path(), &path).is_err());
        std::os::unix::fs::symlink(&path, root.path().join("redirect")).unwrap();
        assert!(confined(root.path(), &root.path().join("redirect")).is_err());
    }

    #[test]
    fn completion_renders_metadata_indexes_and_keeps_media_unchanged() {
        let work = tempfile::tempdir().unwrap();
        let settings = work.path().join("settings.db");
        let profile = rbl_onelibrary::settings::StickSettings::default();
        rbl_onelibrary::build::Builder::create_with(&settings, &profile).unwrap()
            .finish("fixture", "2026-10-28", 42).unwrap();
        let audio = work.path().join("source.mp3");
        std::fs::write(&audio, vec![7; 1024]).unwrap();
        let track = SourceTrack {
            id: 17, source_path: audio.clone(), title: "fixture".into(),
            bpm_x100: 12000, duration_sec: 10, sample_rate: 44100,
            date_added: "2026-10-27".into(),
            metadata: serde_json::from_value(serde_json::json!({"track_number":0,
                "disc_number":0,"bit_depth":16,"play_count":0,"analysed":41,
                "hot_cue_auto_load":true,"date_created":"2026-10-26","isrc":""})).unwrap(),
            ..Default::default()
        };
        let source = SyncSource { db_id: 9, ..Default::default() };
        let request = Request {
            native_profile: "rekordbox-6.8.7.0303".into(),
            tracks: vec![track], playlists: vec![], my_tags: vec![],
            settings_database: settings, settings_pdb: None, settings_files: vec![], db_id: 9,
            tag_master_db_id: 42, classic_background: 8,
            track_extras: vec![TrackExtra { source_id: 17, content_link: 123,
                cue_update_count: 2, analysis_data_update_count: 3, information_update_count: 4,
                legacy_analysis_date: "2026-10-25".into(), legacy_kuvo_public: true }],
        };
        let output = work.path().join("output");
        std::fs::create_dir(&output).unwrap();
        let options = rbl_export::ExportOptions { defaults: Some(&profile), sync: Some(&source), ..Default::default() };
        rbl_export::export_with_options(&output, &request.tracks, &[], &[], &options, &mut |_| {}).unwrap();
        complete_metadata(&output, &request).unwrap();
        let db = rbl_onelibrary::ExportLibrary::open_read_only(&output.join("PIONEER/rekordbox/exportLibrary.db")).unwrap();
        let row: (i64,i64,i64) = db.connection().query_row("SELECT contentLink,cueUpdateCount,masterContentId FROM content", [],
            |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?))).unwrap();
        assert_eq!(row, (123,2,17));
        assert_eq!(db.connection().query_row("SELECT count(*) FROM sqlite_master WHERE type='index'", [], |row| row.get::<_,i64>(0)).unwrap(), 4);
        let raw = std::fs::read(output.join("PIONEER/rekordbox/export.pdb")).unwrap();
        let pdb = rbl_pdb::Pdb::parse(&raw).unwrap();
        assert_eq!(pdb.property().unwrap().background_color, 8);
        let row = pdb.rows(pdb.table(rbl_pdb::PageType::Tracks).unwrap())[0];
        assert_eq!(pdb.string_ref(row, 94+2*2), "4");
        assert_eq!(pdb.string_ref(row, 94+10*2), "2026-10-26");
        assert_eq!(pdb.string_ref(row, 94+15*2), "2026-10-25");
        assert_eq!(std::fs::read(audio).unwrap(), vec![7;1024]);
    }
}
