//! Add Plus to a classic-only local capture without copying any media.
use std::{path::Path,error::Error,collections::BTreeMap};
use rbl_pdb::{Pdb,PageType};
use rbl_onelibrary::build::{Builder,Track,LookupTable};

pub fn upgrade(root:&Path,cues:&serde_json::Value)->Result<(),Box<dyn Error>> {
    let bytes=std::fs::read(root.join("PIONEER/rekordbox/export.pdb"))?;
    let pdb=Pdb::parse(&bytes)?;let property=pdb.property().ok_or("missing classic property")?;
    let names=|kind|pdb.table(kind).map(|t|pdb.named_rows(t).into_iter().map(|r|(r.id,r.name)).collect::<BTreeMap<_,_>>()).unwrap_or_default();
    let artists=names(PageType::Artists);let albums=names(PageType::Albums);let genres=names(PageType::Genres);
    let labels=names(PageType::Labels);let keys=names(PageType::Keys);
    let mut profile=rbl_onelibrary::settings::StickSettings::default();
    profile.device_name=property.device_name.clone();profile.background_color_type=i64::from(property.background_color);
    for (kind,slots) in [(17,&mut profile.categories),(18,&mut profile.sorts)] {
        for row in soundbox_cdj::browse_rows(&bytes,kind)? {
            if row.len()<8{return Err("short classic browse row".into());}
            let id=i64::from(u16::from_le_bytes([row[0],row[1]]));
            if let Some(slot)=slots.iter_mut().find(|s|s.menu_item==id) {
                slot.visible=if kind==17{row[5]&1==0}else{row[4]&1==0};
                slot.seq=if kind==17{i64::from(u16::from_le_bytes([row[6],row[7]]))}else{i64::from(row[5])};
            }
        }
    }
    profile.sub_column=soundbox_cdj::browse_rows(&bytes,18)?.iter().find(|r|r.len()>=8&&r[4]&2!=0)
        .map(|r|i64::from(u16::from_le_bytes([r[0],r[1]])));
    for row in soundbox_cdj::browse_rows(&bytes,6)? {
        if row.len()<9{return Err("short classic color row".into());}
        let id=i64::from(row[4]);
        if let Some(color)=profile.colors.iter_mut().find(|c|c.id==id) {
            // Read the native string using its offset in the original page.
            let table=pdb.table(PageType::Colors).ok_or("missing colors")?;
            for source in pdb.rows(table) {
                let id=*bytes.get(source.offset+4).ok_or("short classic color row")?;
                if id==row[4] {color.name=pdb.string_at(source.offset+8);break;}
            }
        }
    }
    let path=root.join("PIONEER/rekordbox/exportLibrary.db");
    let mut builder=Builder::create_with(&path,&profile)?;
    for image in names(PageType::Artwork){builder.add_image(i64::from(image.0),&image.1)?;}
    for t in pdb.track_rows(pdb.table(PageType::Tracks).ok_or("missing tracks")?) {
        let mut record=Track{content_id:i64::from(t.id),master_db_id:i64::from(t.master_db_id),master_content_id:i64::from(t.master_content_id),
            title:t.title,path:t.file_path.clone(),file_name:t.filename,analysis_path:t.analyze_path,rating:i64::from(t.rating),
            year:i64::from(t.year),release_date:t.release_date,bitrate:i64::from(t.bitrate),sample_rate:i64::from(t.sample_rate),
            file_type:i64::from(rbl_pdb::rows::audio_file_type(&t.file_path)),
            file_size:i64::from(t.file_size),bpm_x100:i64::from(t.tempo_x100),length:i64::from(t.duration_sec),
            track_no:i64::from(t.track_number),comment:t.comment,date_added:t.date_added,
            color_id:Some(i64::from(t.color_id)),image_id:if t.artwork_id==0{None}else{Some(i64::from(t.artwork_id))},..Default::default()};
        for (kind,id,map,dest) in [(LookupTable::Artist,t.artist_id,&artists,&mut record.artist_id),
                (LookupTable::Album,t.album_id,&albums,&mut record.album_id),(LookupTable::Genre,t.genre_id,&genres,&mut record.genre_id),
                (LookupTable::Label,t.label_id,&labels,&mut record.label_id),(LookupTable::Key,t.key_id,&keys,&mut record.key_id)] {
            *dest=Some(builder.intern(kind,map.get(&id).map(String::as_str).unwrap_or_default())?);
        }
        record.metadata.bit_depth=t.sample_depth;record.metadata.disc_number=t.disc_number;
        record.metadata.track_number=t.track_number;record.metadata.play_count=u32::from(t.play_count);
        record.metadata.hot_cue_auto_load=t.hot_cue_auto_load;
        builder.add_track(&record)?;
        if let Some(value)=cues.get(t.id.to_string()) {
            let cues:Vec<_>=serde_json::from_value(value.clone())?;
            builder.replace_cues(i64::from(t.id),&cues)?;
        }
    }
    for n in pdb.playlist_nodes(pdb.table(PageType::PlaylistTree).ok_or("missing playlists")?) {
        builder.add_playlist_node(i64::from(n.id),&n.name,i64::from(n.parent_id),i64::from(n.sort_order)+1,n.is_folder)?;
    }
    for e in pdb.playlist_entries(pdb.table(PageType::PlaylistEntries).ok_or("missing playlist entries")?) {
        builder.add_to_playlist(i64::from(e.playlist_id),i64::from(e.track_id),i64::from(e.entry_index))?;
    }
    let ext_path=root.join("PIONEER/rekordbox/exportExt.pdb");
    if !ext_path.exists(){std::fs::write(&ext_path,rbl_export::ext_pdb::build(&[],&[],0))?;}
    let ext_bytes=std::fs::read(ext_path)?;let ext=Pdb::parse(&ext_bytes)?;
    let master=ext.table(PageType::PlaylistTree).and_then(|t|ext.rows(t).first().copied()).map_or(0,|r|ext.u4_at(r,24));
    if let Some(table)=ext.table(PageType::Albums) {
        for r in ext.rows(table) {
            let string_offset=*ext_bytes.get(r.offset+29).ok_or("short classic tag row")?;
            let name=ext.string_at(r.offset+usize::from(string_offset));
            builder.add_my_tag(i64::from(ext.u4_at(r,20)),i64::from(ext.u4_at(r,16)),&name,
                i64::from(ext_bytes[r.offset+27]),i64::from(ext.u4_at(r,12)))?;
        }
    }
    if let Some(table)=ext.table(PageType::Labels) {
        for r in ext.rows(table){builder.tag_track(i64::from(ext.u4_at(r,8)),i64::from(ext.u4_at(r,4)))?;}
    }
    builder.finish(&property.device_name,&property.created_date,master)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncated_captured_tag_row_returns_an_error_without_panicking() {
        let root = tempfile::tempdir().unwrap();
        rbl_export::create_library(root.path(), None, &[], None).unwrap();
        let catalogs = root.path().join("PIONEER/rekordbox");
        std::fs::remove_file(catalogs.join("exportLibrary.db")).unwrap();

        let mut file = rbl_pdb::build::FileBuilder::new(4096);
        file.add_table(3, &[vec![0; 30]]);
        let mut bytes = file.finish();
        let page = {
            let pdb = Pdb::parse(&bytes).unwrap();
            pdb.rows(pdb.table(PageType::Albums).unwrap())[0].page_offset
        };
        bytes.truncate(page + 4096);
        // The pinned reader admits a present row whose first byte is in the
        // file, without proving that its fixed fields also fit before EOF.
        bytes[page+4096-6..page+4096-4]
            .copy_from_slice(&(4096_u16-41).to_le_bytes());
        let pdb = Pdb::parse(&bytes).unwrap();
        assert_eq!(pdb.rows(pdb.table(PageType::Albums).unwrap())[0].offset,
                   bytes.len()-1);
        std::fs::write(catalogs.join("exportExt.pdb"), &bytes).unwrap();

        let result = std::panic::catch_unwind(|| upgrade(root.path(), &serde_json::json!({})));
        let error = result.expect("malformed local rows must fail without a panic")
            .expect_err("truncated tag row must be rejected");
        assert_eq!(error.to_string(), "short classic tag row");
        assert_eq!(std::fs::read(catalogs.join("exportExt.pdb")).unwrap(), bytes);
    }
}
