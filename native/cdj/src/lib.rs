use std::error::Error;
use std::path::{Path, PathBuf};
use serde::{Deserialize, Serialize};
use rbl_export::{SourceMyTag, SourceTrack};

#[derive(Deserialize, Serialize)]
pub struct Playlist {
    #[serde(default)]
    pub device_id: u32,
    pub id: u64,
    pub name: String,
    pub parent_id: u64,
    pub folder: bool,
    pub track_indices: Vec<usize>,
}

#[derive(Deserialize, Serialize)]
pub struct Request {
    pub native_profile: String,
    pub tracks: Vec<SourceTrack>,
    pub playlists: Vec<Playlist>,
    pub my_tags: Vec<SourceMyTag>,
    pub settings_database: PathBuf,
    #[serde(default)]
    pub settings_pdb: Option<PathBuf>,
    pub settings_files: Vec<(String, PathBuf)>,
    pub db_id: u64,
    pub tag_master_db_id: u32,
    pub classic_background: u8,
    pub track_extras: Vec<TrackExtra>,
}

#[derive(Deserialize, Serialize)]
pub struct TrackExtra {
    pub source_id: u64,
    pub content_link: u32,
    pub cue_update_count: i64,
    pub analysis_data_update_count: i64,
    pub information_update_count: i64,
    pub legacy_analysis_date: String,
    pub legacy_kuvo_public: bool,
}

/// Complete fields the upstream pipeline omits. Values come from explicit
/// input metadata, never from copying a reference output database into place.
pub fn complete_metadata(output: &Path, request: &Request) -> Result<(), Box<dyn Error>> {
    if request.native_profile != "rekordbox-6.8.7.0303" {
        return Err("unsupported fixture metadata profile".into());
    }
    let dbdir = output.join("PIONEER/rekordbox");
    let path = dbdir.join("export.pdb");
    let mut bytes = std::fs::read(&path)?;
    let (track_rows, property_rows) = {
        let pdb = rbl_pdb::Pdb::parse(&bytes)?;
        let tracks = pdb.rows(pdb.table(rbl_pdb::PageType::Tracks).ok_or("no track table")?);
        let property = pdb.rows(pdb.table(rbl_pdb::PageType::Property).ok_or("no property table")?);
        (tracks.into_iter().map(|row| (row.offset,
            (0..21).map(|slot| pdb.string_ref(row, 94 + slot*2)).collect::<Vec<_>>())).collect::<Vec<_>>(),
         property.into_iter().map(|row| row.offset).collect::<Vec<_>>())
    };
    let mut tagged = Vec::new();
    let mut completed_rows = Vec::new();
    for (offset, mut strings) in track_rows {
        let source_id = u32::from_le_bytes(bytes[offset+20..offset+24].try_into()?) as u64;
        let device_id = u32::from_le_bytes(bytes[offset+72..offset+76].try_into()?);
        let extra = request.track_extras.iter().find(|row| row.source_id == source_id).ok_or("missing track extra metadata")?;
        bytes[offset+4..offset+8].copy_from_slice(&extra.content_link.to_le_bytes());
        let track = request.tracks.iter().find(|row| row.id == source_id).ok_or("missing track input")?;
        tagged.push((device_id, track.my_tags.as_slice()));
        let counter = |value: i64| if value == 0 { String::new() } else { value.to_string() };
        strings[2] = counter(extra.information_update_count);
        strings[3] = counter(extra.analysis_data_update_count);
        strings[4] = counter(extra.cue_update_count);
        strings[6] = if extra.legacy_kuvo_public { "ON".into() } else { String::new() };
        strings[10] = track.metadata.date_created.clone();
        strings[15] = extra.legacy_analysis_date.clone();
        let mut row = bytes[offset..offset+136].to_vec();
        for (slot, value) in strings.iter().enumerate() {
            let at = rbl_pdb::build::push_device_sql_string(&mut row, value);
            row[94+slot*2..96+slot*2].copy_from_slice(&u16::try_from(at)?.to_le_bytes());
        }
        completed_rows.push(row);
    }
    if property_rows.len() != 1 { return Err("unexpected property row count".into()); }
    bytes[property_rows[0]+9] = request.classic_background;
    number_track_slots(&mut completed_rows)?;
    bytes = rbl_pdb::build::replace_table(&bytes, 0, &completed_rows).ok_or("cannot rebuild track table")?;
    let profile = rbl_onelibrary::settings::StickSettings::read(&request.settings_database)?;
    let mut sorts = profile.sorts.clone();
    sorts.sort_by_key(|row| (row.visible, if row.visible { row.seq } else { row.id }));
    let sort_rows: Vec<Vec<u8>> = sorts.iter().map(|row| -> Result<Vec<u8>, Box<dyn Error>> {
        let mut bytes = Vec::new();
        bytes.extend_from_slice(&u16::try_from(row.menu_item)?.to_le_bytes());
        bytes.extend_from_slice(&u16::try_from(row.id)?.to_le_bytes());
        bytes.push(u8::from(!row.visible) | if profile.sub_column == Some(row.menu_item) { 2 } else { 0 });
        bytes.push(u8::try_from(row.seq)?);
        bytes.extend_from_slice(&[0,0]);
        Ok(bytes)
    }).collect::<Result<_,_>>()?;
    bytes = rbl_pdb::build::replace_table(&bytes, 18, &sort_rows).ok_or("cannot rebuild sort table")?;
    if let Some(baseline) = &request.settings_pdb {
        let original=std::fs::read(baseline)?;
        for kind in [6,16,17,18] {
            let rows=browse_rows(&original,kind)?;
            bytes=rbl_pdb::build::replace_table(&bytes,kind,&rows).ok_or("cannot preserve classic browsing settings")?;
        }
    }
    if let Some(baseline)=&request.settings_pdb {
        let source=std::fs::read(baseline)?;
        let original=rbl_pdb::Pdb::parse(&source)?.property().ok_or("unknown classic property")?;
        let mut current=rbl_pdb::Pdb::parse(&bytes)?.property().ok_or("missing candidate property")?;
        current.device_name=original.device_name;
        let row=rbl_pdb::rows::property_row(&current).ok_or("cannot retain classic device name")?;
        bytes=rbl_pdb::build::replace_table(&bytes,19,&[row]).ok_or("cannot preserve classic device name")?;
    }
    std::fs::write(path, bytes)?;
    // Request sequences are canonical device sequences. The upstream legacy
    // encoder expects master sequences and subtracts one; compensate only at
    // that encoder boundary, leaving Plus and the shared model unchanged.
    let legacy_tags = request.my_tags.iter().cloned().map(|mut tag| -> Result<_, Box<dyn Error>> {
        tag.seq = tag.seq.checked_add(1).ok_or("tag sequence overflow")?;
        Ok(tag)
    }).collect::<Result<Vec<_>,_>>()?;
    std::fs::write(dbdir.join("exportExt.pdb"),
        rbl_export::ext_pdb::build(&legacy_tags, &tagged, request.tag_master_db_id))?;

    let mut conn = rbl_onelibrary::playlists::open_read_write(&dbdir.join("exportLibrary.db"))?;
    let transaction = conn.transaction()?;
    transaction.execute_batch("UPDATE album SET artist_id=COALESCE(artist_id,0), image_id=COALESCE(image_id,0), isComplation=COALESCE(isComplation,0), nameForSearch=COALESCE(nameForSearch,'');
        UPDATE artist SET nameForSearch='';
        UPDATE playlist SET image_id=COALESCE(image_id,0);
        UPDATE content SET titleForSearch='', subtitle=COALESCE(subtitle,''), artist_id_remixer=COALESCE(artist_id_remixer,0), artist_id_originalArtist=COALESCE(artist_id_originalArtist,0), artist_id_composer=COALESCE(artist_id_composer,0), artist_id_lyricist=COALESCE(artist_id_lyricist,0), image_id=COALESCE(image_id,0), isKuvoDeliverStatusOn=0, kuvoDeliveryComment=COALESCE(kuvoDeliveryComment,'');")?;
    for extra in &request.track_extras {
        let updated = transaction.execute("UPDATE content SET contentLink=?1, cueUpdateCount=?2, analysisDataUpdateCount=?3, informationUpdateCount=?4 WHERE masterContentId=?5 AND masterDbId=?6",
            rusqlite::params![extra.content_link, extra.cue_update_count, extra.analysis_data_update_count,
                extra.information_update_count, i64::try_from(extra.source_id)?, i64::try_from(request.db_id)?])?;
        if updated != 1 { return Err("extra metadata did not match exactly one exported track".into()); }
    }
    // Preserve raw browsing/profile rows, including language and unknown flag
    // values. General edits are explicit local patches; playlist sync must not
    // normalize an existing USB's custom lists through the upstream bool model.
    let baseline=rbl_onelibrary::ExportLibrary::open_read_only(&request.settings_database)?;
    for table in ["menuItem","category","sort","color"] {
        let mut statement=baseline.connection().prepare(&format!("SELECT * FROM {table}"))?;
        let columns=statement.column_count();
        let rows=statement.query_map([],|r|(0..columns).map(|i|r.get::<_,rusqlite::types::Value>(i)).collect::<rusqlite::Result<Vec<_>>>())?.collect::<Result<Vec<_>,_>>()?;
        transaction.execute(&format!("DELETE FROM {table}"),[])?;
        let marks=vec!["?";columns].join(",");
        for row in rows {transaction.execute(&format!("INSERT INTO {table} VALUES ({marks})"),rusqlite::params_from_iter(row))?;}
    }
    transaction.execute("UPDATE property SET myTagMasterDBID=?1", [request.tag_master_db_id])?;
    transaction.execute_batch("CREATE INDEX index_hotCueBankList_cue_hotCueBankList_id on hotCueBankList_cue(hotCueBankList_id);
        CREATE INDEX index_myTag_content_content_id on myTag_content(content_id);
        CREATE INDEX index_myTag_content_myTag_id on myTag_content(myTag_id);
        CREATE INDEX index_playlist_content_playlist_id on playlist_content(playlist_id);")?;
    transaction.commit()?;
    drop(conn);
    Ok(())
}


/// Match replace_table's page packing, numbering each typed row within its page.
/// Row growth changes page boundaries; original slot identities cannot survive it.
pub fn number_track_slots(rows: &mut [Vec<u8>]) -> Result<(), Box<dyn Error>> {
    let mut page = rbl_pdb::build::PageBuilder::new(4096, 1, 0, 0);
    for row in rows {
        if !page.fits(row.len()) {
            page = rbl_pdb::build::PageBuilder::new(4096, 1, 0, 0);
        }
        if !page.fits(row.len()) { return Err("track metadata exceeds a DeviceSQL page".into()); }
        rbl_pdb::rows::set_index_shift(row, u16::try_from(page.row_count())?);
        page.push_row(row);
    }
    Ok(())
}

/// Preserve native browse definitions and their unknown bytes during playlist sync.
pub fn browse_rows(bytes: &[u8], kind: u32) -> Result<Vec<Vec<u8>>,Box<dyn Error>> {
    let pdb=rbl_pdb::Pdb::parse(bytes)?;
    let page_type=match kind {6=>rbl_pdb::PageType::Colors,16=>rbl_pdb::PageType::Columns,17=>rbl_pdb::PageType::HistoryPlaylists,18=>rbl_pdb::PageType::HistoryEntries,_=>return Err("unsupported browse table".into())};
    let rows=pdb.rows(pdb.table(page_type).ok_or("missing native browse table")?);
    rows.iter().map(|r| {
        let end=rows.iter().filter(|n|n.page_offset==r.page_offset && n.offset>r.offset).map(|n|n.offset).min().unwrap_or(r.page_offset+40+usize::from(u16::from_le_bytes(bytes.get(r.page_offset+30..r.page_offset+32).ok_or("truncated browse page")?.try_into()?)));
        Ok(bytes.get(r.offset..end).ok_or("invalid browse row bounds")?.to_vec())
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn grown_rows_are_numbered_per_page_after_pagination() {
        let mut rows=(0..500).map(|i| {
            let mut row=vec![0_u8; 136+(i%17)*17];
            row[..2].copy_from_slice(&0x24_u16.to_le_bytes());
            row[72..76].copy_from_slice(&(i as u32+1).to_le_bytes());
            row
        }).collect::<Vec<_>>();
        number_track_slots(&mut rows).unwrap();
        let mut file=rbl_pdb::build::FileBuilder::new(4096);
        file.add_table(0,&rows);
        let original=file.finish();
        let raw=rbl_pdb::build::replace_table(&original,0,&rows).unwrap();
        let pdb=rbl_pdb::Pdb::parse(&raw).unwrap();
        let actual=pdb.rows(pdb.table(rbl_pdb::PageType::Tracks).unwrap());
        assert_eq!(actual.len(),500);
        let mut last=0;
        let mut slot=0;
        for row in actual {
            if row.page_offset!=last {last=row.page_offset;slot=0;}
            assert_eq!(u16::from_le_bytes(raw[row.offset+2..row.offset+4].try_into().unwrap()),slot*32);
            slot+=1;
        }
        assert!(number_track_slots(&mut [vec![0;9000]]).is_err());
    }
}
pub mod whole_tempo;
