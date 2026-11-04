//! Local renderer/reader. Mounted paths are never accepted by this process.
use std::{error::Error, path::{Path, PathBuf}};
use rbl_export::{SourcePlaylist, SyncSource};
use serde_json::json;
use soundbox_cdj::{Request, complete_metadata};
mod merge;
mod preferences;
mod upgrade;
mod analysis;
mod saved_analysis;

fn local(root: &Path, path: &Path) -> Result<PathBuf, Box<dyn Error>> {
    let path = path.canonicalize()?;
    if !path.starts_with(root) || path.starts_with("/Volumes") || path.starts_with("/dev") {
        return Err("input must stay in the explicit local workspace".into());
    }
    Ok(path)
}

fn inspect(root: &Path) -> Result<serde_json::Value, Box<dyn Error>> {
    if !root.join("PIONEER/rekordbox/exportLibrary.db").exists() {return inspect_classic(root);}
    let db = rbl_onelibrary::ExportLibrary::open_read_only(&local(root, &root.join("PIONEER/rekordbox/exportLibrary.db"))?)?;
    let conn = db.connection();
    let expected:Vec<String>=serde_json::from_str(include_str!("schema.json"))?;
    let tables=db.tables()?;
    if tables.len()!=expected.len(){return Err("unsupported native database schema; existing files preserved".into());}
    let normalize=|s:&str|s.chars().filter(|c|!c.is_whitespace()).flat_map(char::to_lowercase).collect::<String>();
    for sql in expected {
        let name=sql.split('(').next().ok_or("invalid expected schema")?.trim_start_matches("CREATE TABLE ");
        let actual=db.schema_of(name)?.ok_or("native table is missing")?;
        if normalize(&actual)!=normalize(&sql){return Err("unsupported native database columns; existing files preserved".into());}
    }
    let properties:i64=conn.query_row("SELECT count(*) FROM property",[],|r|r.get(0))?;
    if properties!=1{return Err("ambiguous native library identity".into());}
    let version:String=conn.query_row("SELECT dbVersion FROM property",[],|r|r.get(0))?;
    if version!="1000"{return Err("unsupported native library version; existing files preserved".into());}
    let check: String = conn.query_row("PRAGMA quick_check", [], |r| r.get(0))?;
    if check != "ok" { return Err("OneLibrary integrity check failed".into()); }
    let count: i64 = conn.query_row("SELECT count(*) FROM content", [], |r| r.get(0))?;
    if count > 100_000 { return Err("library exceeds the display limit".into()); }
    let mut statement = conn.prepare("SELECT playlist_id, playlist_id_parent, name, attribute FROM playlist ORDER BY sequenceNo, playlist_id LIMIT 10001")?;
    let mut playlists = statement.query_map([], |r| Ok(json!({"id": r.get::<_,i64>(0)?.to_string(), "parent": r.get::<_,Option<i64>>(1)?.unwrap_or(0).to_string(), "title": r.get::<_,String>(2)?, "folder": r.get::<_,i64>(3)? != 0, "entries": []})))?.collect::<Result<Vec<_>,_>>()?;
    if playlists.len() > 10_000 { return Err("too many playlists".into()); }
    let mut statement = conn.prepare("SELECT p.playlist_id, c.title, c.path FROM playlist_content p JOIN content c ON p.content_id=c.content_id ORDER BY p.playlist_id, p.sequenceNo LIMIT 200001")?;
    let entries = statement.query_map([], |r| Ok((r.get::<_,i64>(0)?.to_string(), json!([r.get::<_,String>(1)?,r.get::<_,String>(2)?]))))?.collect::<Result<Vec<_>,_>>()?;
    if entries.len() > 200_000 { return Err("too many playlist entries".into()); }
    let positions: std::collections::HashMap<String,usize> = playlists.iter().enumerate().map(|(i,p)|(p["id"].as_str().unwrap_or_default().to_owned(),i)).collect();
    for (id, entry) in entries {
        let at = positions.get(&id).ok_or("entry references a missing playlist")?;
        playlists[*at]["entries"].as_array_mut().ok_or("missing entries")?.push(entry);
    }
    let settings = rbl_onelibrary::settings::StickSettings::read(&root.join("PIONEER/rekordbox/exportLibrary.db"))?;
    let mut classic_background = None;
    let mut classic_browse = Vec::new();
    let path = root.join("PIONEER/rekordbox/export.pdb");
    if path.exists() {
        let bytes = std::fs::read(local(root,&path)?)?;
        let pdb = rbl_pdb::Pdb::parse(&bytes)?;
        let table = pdb.table(rbl_pdb::PageType::Property).ok_or("missing classic property")?;
        let rows = pdb.rows(table);
        if rows.len()!=1 { return Err("ambiguous classic property".into()); }
        classic_background = bytes.get(rows[0].offset+9).copied();
        for kind in [16,17,18] {classic_browse.push(soundbox_cdj::browse_rows(&bytes,kind)?);}
    }
    let mut statement = conn.prepare("SELECT c.content_id, c.masterContentId, c.path, c.analysisDataFilePath, c.masterDbId, c.title, COALESCE(a.name,''), c.bpmx100, c.length, COALESCE(k.name,''), c.bitrate, COALESCE(g.name,''), c.dateAdded, COALESCE(i.path,''), COALESCE(c.fileSize,0), COALESCE(c.cueUpdateCount,0), COALESCE(c.analysisDataUpdateCount,0), COALESCE(c.informationUpdateCount,0), COALESCE(al.name,''), c.releaseYear, c.releaseDate, c.djComment, c.djPlayCount, c.rating, COALESCE(l.name,''), c.color_id, c.kuvoDeliveryComment, c.isHotCueAutoLoadOn, c.isKuvoDeliverStatusOn, c.dateCreated, c.samplingRate, c.bitDepth, COALESCE(ly.name,''), COALESCE((SELECT group_concat(t.name,', ') FROM myTag_content m JOIN myTag t ON t.myTag_id=m.myTag_id WHERE m.content_id=c.content_id),'') FROM content c LEFT JOIN artist a ON a.artist_id=c.artist_id_artist LEFT JOIN key k ON k.key_id=c.key_id LEFT JOIN genre g ON g.genre_id=c.genre_id LEFT JOIN image i ON i.image_id=c.image_id LEFT JOIN album al ON al.album_id=c.album_id LEFT JOIN label l ON l.label_id=c.label_id LEFT JOIN artist ly ON ly.artist_id=c.artist_id_lyricist ORDER BY c.content_id")?;
    let tracks = statement.query_map([], |r| {
        let mut info=serde_json::Map::new();
        for (key,at) in [("album",18),("year",19),("releaseDate",20),("comments",21),("playCount",22),("rating",23),
                ("label",24),("color",25),("message",26),("dateCreated",29),("sampleRate",30),("bitDepth",31),("lyricist",32),("myTags",33)] {
            let value=r.get::<_,rusqlite::types::Value>(at)?;
            let text=match value {rusqlite::types::Value::Integer(v)=>v.to_string(),rusqlite::types::Value::Text(v)=>v,_=>String::new()};
            info.insert(key.to_owned(),json!(text));
        }
        info.insert("hotCueAutoLoad".into(),json!(if r.get::<_,Option<i64>>(27)?.unwrap_or(0)!=0{"On"}else{"Off"}));
        info.insert("publishInfo".into(),json!(if r.get::<_,Option<i64>>(28)?.unwrap_or(0)!=0{"On"}else{"Off"}));
        info.insert("size".into(),json!(r.get::<_,i64>(14)?.to_string()));
        Ok(json!({"id":r.get::<_,i64>(0)?,"source_id":r.get::<_,i64>(1)?,"audio":r.get::<_,String>(2)?,"analysis":r.get::<_,String>(3)?,"master_db_id":r.get::<_,i64>(4)?,"title":r.get::<_,String>(5)?,"artist":r.get::<_,String>(6)?,"bpm":r.get::<_,i64>(7)?,"duration":r.get::<_,i64>(8)?,"key":r.get::<_,String>(9)?,"bitrate":r.get::<_,i64>(10)?,"genre":r.get::<_,String>(11)?,"date_added":r.get::<_,String>(12)?,"artwork":r.get::<_,String>(13)?,"file_size":r.get::<_,i64>(14)?,"cue_update_count":r.get::<_,i64>(15)?,"analysis_data_update_count":r.get::<_,i64>(16)?,"information_update_count":r.get::<_,i64>(17)?,"info":info}))})?.collect::<Result<Vec<_>,_>>()?;
    let slots=|rows:&[rbl_onelibrary::settings::MenuSlot]|rows.iter().map(|r|json!([r.id,r.menu_item,r.name,r.seq,r.visible])).collect::<Vec<_>>();
    let preferences=json!({"name":settings.device_name,"plus":settings.background_color_type,"classic":classic_background,
        "categories":slots(&settings.categories),"sorts":slots(&settings.sorts),"sub_column":settings.sub_column,
        "colors":settings.colors.iter().map(|c|json!([c.id,c.name])).collect::<Vec<_>>(),"classic_browse":classic_browse});
    let tags=conn.prepare("SELECT myTag_id, sequenceNo, name, attribute, myTag_id_parent FROM myTag ORDER BY myTag_id")?
        .query_map([],|r|Ok(json!({"id":r.get::<_,i64>(0)?,"seq":r.get::<_,i64>(1)?,"name":r.get::<_,String>(2)?,"attribute":r.get::<_,i64>(3)?,"parent":r.get::<_,Option<i64>>(4)?.unwrap_or(0)})))?
        .collect::<Result<Vec<_>,_>>()?;
    Ok(json!({"preferences":preferences,"track_rows":tracks,"playlists":playlists,"tracks":count,"name":settings.device_name,"native_tags":tags,
        "tag_master_id":conn.query_row("SELECT myTagMasterDBID FROM property", [], |r| r.get::<_,i64>(0))?,
        "background_plus":settings.background_color_type,"background_classic":classic_background}))
}

fn group_playlist_entries(entries:&[rbl_pdb::PlaylistEntry]) -> std::collections::HashMap<u32,Vec<&rbl_pdb::PlaylistEntry>> {
    let mut grouped:std::collections::HashMap<u32,Vec<&rbl_pdb::PlaylistEntry>>=std::collections::HashMap::new();
    for entry in entries {grouped.entry(entry.playlist_id).or_default().push(entry);}
    for rows in grouped.values_mut() {rows.sort_by_key(|e|e.entry_index);}
    grouped
}

fn inspect_classic(root: &Path) -> Result<serde_json::Value,Box<dyn Error>> {
    use rbl_pdb::PageType;
    let raw=std::fs::read(local(root,&root.join("PIONEER/rekordbox/export.pdb"))?)?;
    let pdb=rbl_pdb::Pdb::parse(&raw)?;
    let property=pdb.property().ok_or("unsupported classic property")?;
    let tracks=pdb.track_rows(pdb.table(PageType::Tracks).ok_or("missing classic tracks")?);
    let nodes=pdb.playlist_nodes(pdb.table(PageType::PlaylistTree).ok_or("missing classic playlists")?);
    let entries=pdb.playlist_entries(pdb.table(PageType::PlaylistEntries).ok_or("missing classic entries")?);
    if tracks.len()>100_000 || nodes.len()>10_000 || entries.len()>200_000 {return Err("classic library exceeds display limits".into());}
    let tracks_by_id=tracks.iter().map(|t|(t.id,t)).collect::<std::collections::HashMap<_,_>>();
    let by_playlist=group_playlist_entries(&entries);
    let mut playlists=Vec::new();
    for node in &nodes {
        let ordered=by_playlist.get(&node.id).map(Vec::as_slice).unwrap_or_default();
        let members=ordered.iter().map(|e| {
            let t=tracks_by_id.get(&e.track_id).ok_or("classic entry references missing track")?;
            Ok(json!([t.title,t.file_path]))
        }).collect::<Result<Vec<_>,Box<dyn Error>>>()?;
        playlists.push(json!({"id":node.id.to_string(),"parent":node.parent_id.to_string(),"title":node.name,"folder":node.is_folder,"entries":members}));
    }
    let names=|kind|pdb.table(kind).map(|t|pdb.named_rows(t).into_iter().map(|r|(r.id,r.name)).collect::<std::collections::HashMap<_,_>>()).unwrap_or_default();
    let artists=names(PageType::Artists);let keys=names(PageType::Keys);let genres=names(PageType::Genres);let artwork=names(PageType::Artwork);
    let name=|map:&std::collections::HashMap<u32,String>,id|map.get(&id).cloned().unwrap_or_default();
    let mut master=0;
    let ext=root.join("PIONEER/rekordbox/exportExt.pdb");
    if ext.exists() {
        let raw=std::fs::read(local(root,&ext)?)?;
        let pdb=rbl_pdb::Pdb::parse(&raw)?;
        if let Some(table)=pdb.table(PageType::PlaylistTree) {
            let rows=pdb.rows(table);
            if let Some(row)=rows.last(){master=pdb.u4_at(*row,24);}
        }
    }
    let browse=[16,17,18].iter().map(|k|soundbox_cdj::browse_rows(&raw,*k)).collect::<Result<Vec<_>,_>>()?;
    Ok(json!({"tracks":tracks.len(),"playlists":playlists,"track_rows":tracks.iter().map(|t|json!({"id":t.id,"source_id":t.master_content_id,"master_db_id":t.master_db_id,"audio":t.file_path,"analysis":t.analyze_path,"title":t.title,"artist":name(&artists,t.artist_id),"bpm":t.tempo_x100,"duration":t.duration_sec,"key":name(&keys,t.key_id),"genre":name(&genres,t.genre_id),"bitrate":t.bitrate,"date_added":t.date_added,"artwork":name(&artwork,t.artwork_id)})).collect::<Vec<_>>(),"name":property.device_name,
        "background_plus":null,"background_classic":property.background_color,"tag_master_id":master,
        "preferences":{"classic_browse":browse,"name":property.device_name,"classic":property.background_color}}))
}

fn render(root: &Path, input: &Path, output: &Path) -> Result<serde_json::Value, Box<dyn Error>> {
    let value:serde_json::Value=serde_json::from_slice(&std::fs::read(local(root,input)?)?)?;
    let pre_staged=value.get("pre_staged").and_then(|v|v.as_bool()).unwrap_or(false);
    let request: Request = serde_json::from_value(value)?;
    if request.native_profile != "rekordbox-6.8.7.0303" { return Err("unsupported metadata profile".into()); }
    if output.file_name().is_none() || output.exists()!=pre_staged { return Err("invalid local output staging state".into()); }
    local(root,output.parent().ok_or("missing output parent")?)?;
    if pre_staged {
        let output=local(root,output)?;
        let expected=request.tracks.iter().map(|t|local(root,&t.source_path)).collect::<Result<std::collections::BTreeSet<_>,_>>()?;
        if expected.len()!=request.tracks.len() || expected.iter().any(|p|!p.starts_with(output.join("Contents"))){return Err("invalid pre-staged audio set".into());}
        let mut pending=vec![output.clone()];let mut found=std::collections::BTreeSet::new();
        while let Some(dir)=pending.pop(){
            for entry in std::fs::read_dir(dir)? {
                let entry=entry?;let kind=entry.file_type()?;let path=entry.path();
                if kind.is_dir(){if !path.starts_with(output.join("Contents")){return Err("pre-staging contains metadata".into());}pending.push(path);}
                else if kind.is_file() && expected.contains(&local(root,&path)?){found.insert(local(root,&path)?);}
                else{return Err("unexpected pre-staged path".into());}
            }
        }
        if found!=expected{return Err("pre-staged audio is missing".into());}
    }
    for track in &request.tracks {
        local(root,&track.source_path)?;
        if track.device.as_ref().is_some_and(|d| d.preserve || d.id == 0 || d.master_content_id != track.id || d.master_db_id != request.db_id) || track.source_unavailable { return Err("retained device authority is not supported".into()); }
        if track.cues.is_none() { return Err("fresh authoritative cues are required".into()); }
        if let Some(path)=&track.analysis_source {
            for ext in ["DAT","EXT","2EX"] { local(root,&path.with_extension(ext))?; }
        } else { return Err("complete native analysis is required".into()); }
        if let Some(path)=&track.artwork { local(root,path)?; }
    }
    if let Some(path)=&request.settings_pdb {local(root,path)?;}
    let defaults = rbl_onelibrary::settings::StickSettings::read(&local(root,&request.settings_database)?)?;
    let settings = request.settings_files.iter().map(|(name,path)| {
        if !["DEVSETTING.DAT","MYSETTING.DAT","MYSETTING2.DAT","DJMMYSETTING.DAT","djprofile.nxs"].contains(&name.as_str()) { return Err("unknown settings destination".into()); }
        Ok((name.clone(),std::fs::read(local(root,path)?)?))
    }).collect::<Result<Vec<_>,Box<dyn Error>>>()?;
    let playlists = request.playlists.iter().map(|p| SourcePlaylist{device_id:p.device_id,id:p.id,name:p.name.clone(),parent_id:p.parent_id,folder:p.folder,track_indices:p.track_indices.clone(),..Default::default()}).collect::<Vec<_>>();
    let source = SyncSource{db_id:request.db_id,..Default::default()};
    let options = rbl_export::ExportOptions{defaults:Some(&defaults),sync:Some(&source),settings_files:&settings,verify_all:true,..Default::default()};
    if !pre_staged {std::fs::create_dir(output)?;}
    let report = rbl_export::export_with_options(output,&request.tracks,&playlists,&request.my_tags,&options,&mut |_| {})?;
    complete_metadata(output,&request)?;
    // Recovery-capable upstream verification is used only on this disposable candidate.
    if !rbl_export::verify(output)?.is_ok() { return Err("candidate verification failed".into()); }
    for name in [".rbxport-write.lock","PIONEER/rbxport/manifest.json","PIONEER/rekordbox/playlists3.sync","PIONEER/rekordbox/playlists3Plus.sync"] {
        let path=output.join(name);
        if path.is_file(){std::fs::remove_file(path)?;}
    }
    std::fs::remove_dir(output.join("PIONEER/rbxport"))?;
    let view=inspect(output)?;
    if view["tracks"].as_u64()!=Some(request.tracks.len() as u64) {return Err("candidate track count mismatch".into());}
    Ok(json!({"tracks":report.tracks,"playlists":report.playlists,"analysis_files":report.analysis_files,"view":view}))
}

fn settings(root: &Path, changes: &Path) -> Result<serde_json::Value,Box<dyn Error>> {
    let changes: serde_json::Value = serde_json::from_slice(&std::fs::read(local(root,changes)?)?)?;
    let changes=changes.as_object().ok_or("settings object required")?;
    if changes.keys().any(|k| !["device_name","background_plus","background_classic","categories","sorts","sub_column","colors"].contains(&k.as_str())) {return Err("unsupported USB setting".into());}
    if changes.keys().any(|k| ["categories","sorts","sub_column","colors"].contains(&k.as_str())) {preferences::apply(root,changes)?;}
    let path=root.join("PIONEER/rekordbox/exportLibrary.db");
    let has_plus=path.exists();
    let mut profile=if has_plus {rbl_onelibrary::settings::StickSettings::read(&local(root,&path)?)?} else {Default::default()};
    if let Some(value)=changes.get("device_name") {
        let value=value.as_str().ok_or("device name must be text")?;
        if value.is_empty()||value.len()>512||value.chars().any(char::is_control){return Err("invalid device name".into());}
        profile.device_name=value.to_owned();
    }
    if let Some(value)=changes.get("background_plus") {
        if !has_plus{return Err("export a playlist to initialize Device Library Plus".into());}
        let value=value.as_i64().filter(|v| (0..=8).contains(v)).ok_or("invalid background color")?;
        profile.background_color_type=value;
    }
    if has_plus {
        let mut connection=rbl_onelibrary::playlists::open_read_write(&path)?;
        let tx=connection.transaction()?;
        if changes.contains_key("device_name"){tx.execute("UPDATE property SET deviceName=?1",[&profile.device_name])?;}
        if changes.contains_key("background_plus"){tx.execute("UPDATE property SET backGroundColorType=?1",[profile.background_color_type])?;}
        tx.commit()?;
    }
    if root.join("PIONEER/rekordbox/export.pdb").exists() && (changes.contains_key("device_name")||changes.contains_key("background_classic")) {
        let path=local(root,&root.join("PIONEER/rekordbox/export.pdb"))?;
        let bytes=std::fs::read(&path)?;
        let pdb=rbl_pdb::Pdb::parse(&bytes)?;
        let mut property=pdb.property().ok_or("unsupported classic property")?;
        if changes.contains_key("device_name") {property.device_name=profile.device_name;}
        if let Some(value)=changes.get("background_classic") {property.background_color=u8::try_from(value.as_u64().filter(|v| *v<=8).ok_or("invalid classic background")?)?;}
        let row=rbl_pdb::rows::property_row(&property).ok_or("unsupported property encoding")?;
        let bytes=rbl_pdb::build::replace_table(&bytes,19,&[row]).ok_or("cannot update classic property")?;
        std::fs::write(path,bytes)?;
    }
    inspect(root)
}

fn main() -> Result<(),Box<dyn Error>> {
    let args=std::env::args().collect::<Vec<_>>();
    if args.len()<3 {return Err("usage: soundbox-cdj inspect|defaults|render LOCAL_WORKSPACE [REQUEST OUTPUT]".into());}
    let root=Path::new(&args[2]).canonicalize()?;
    if root.starts_with("/Volumes")||root.starts_with("/dev")||root.parent().is_none(){return Err("local workspace required".into());}
    let result=match args[1].as_str(){
        "analyze" if args.len()==4=>analysis::run(&root,Path::new(&args[3]))?,
        "saved-analysis" if args.len()==4=>saved_analysis::run(&root,Path::new(&args[3]))?,
        "inspect" if args.len()==3=>inspect(&root)?,
        "initialize" if args.len()==5=>{
            let request:serde_json::Value=serde_json::from_slice(&std::fs::read(local(&root,Path::new(&args[3]))?)?)?;
            let output=Path::new(&args[4]);
            if output.exists(){return Err("initialization needs a fresh local output".into());}
            local(&root,output.parent().ok_or("missing output parent")?)?;
            let mut profile=rbl_onelibrary::settings::StickSettings::default();
            profile.device_name=request["name"].as_str().ok_or("device name required")?.to_owned();
            rbl_export::create_library(output,Some(&profile),&[],None)?;
            inspect(output)?
        },
        "settings" if args.len()==4=>settings(&root,Path::new(&args[3]))?,
        "catalog-check" if args.len()==3=>{
            for name in ["export.pdb","exportExt.pdb","exportLibrary.db"]{local(&root,&root.join("PIONEER/rekordbox").join(name))?;}
            if !rbl_export::verify_databases(&root)?.is_ok(){return Err("native catalogs disagree; finish the export in rekordbox".into());}
            json!({"ok":true})
        },
        "upgrade" if args.len()==5=>{
            let baseline=local(&root,Path::new(&args[3]))?;
            let cues:serde_json::Value=serde_json::from_slice(&std::fs::read(local(&root,Path::new(&args[4]))?)?)?;
            upgrade::upgrade(&baseline,&cues)?;inspect(&baseline)?
        },
        "merge" if args.len()==4=>{
            let mut request:merge::Request=serde_json::from_slice(&std::fs::read(local(&root,Path::new(&args[3]))?)?)?;
            request.baseline=local(&root,&request.baseline)?;
            request.partial=request.partial.as_ref().map(|p|local(&root,p)).transpose()?;
            for directory in std::iter::once(&request.baseline).chain(request.partial.iter()) {
                for name in ["export.pdb","exportExt.pdb","exportLibrary.db"] {
                    local(&root,&directory.join("PIONEER/rekordbox").join(name))?;
                }
            }
            if request.output.exists(){return Err("merge output must be a new local directory".into());}
            local(&root,request.output.parent().ok_or("missing merge parent")?)?;
            merge::merge(&request)?;
            inspect(&request.output)?
        },
        "defaults" if args.len()==3 || args.len()==4=>{
            let path=root.join("settings.db");
            let mut profile=rbl_onelibrary::settings::StickSettings::default();
            profile.device_name="SoundBox".to_owned();
            if args.len()==4 {
                let bytes=std::fs::read(local(&root,Path::new(&args[3]))?)?;
                let pdb=rbl_pdb::Pdb::parse(&bytes)?;
                let property=pdb.property().ok_or("unknown classic settings")?;
                profile.device_name=property.device_name;
                profile.background_color_type=i64::from(property.background_color);
                if let Some(table)=pdb.table(rbl_pdb::PageType::Colors) {
                    for row in pdb.named_rows(table) {
                        if let Some(color)=profile.colors.iter_mut().find(|c|c.id==i64::from(row.id)){color.name=row.name;}
                    }
                }
            }
            rbl_onelibrary::build::Builder::create_with(&path,&profile)?.finish(&profile.device_name,"2026-10-28",0)?;
            json!({"settings_database":path})
        },
        "render" if args.len()==5=>render(&root,Path::new(&args[3]),Path::new(&args[4]))?,
        _=>return Err("invalid command arguments".into())
    };
    println!("{}",result);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn grouping_retains_duplicate_members_and_stable_equal_positions() {
        let entries=[rbl_pdb::PlaylistEntry{playlist_id:7,track_id:20,entry_index:2},
                     rbl_pdb::PlaylistEntry{playlist_id:9,track_id:30,entry_index:1},
                     rbl_pdb::PlaylistEntry{playlist_id:7,track_id:10,entry_index:1},
                     rbl_pdb::PlaylistEntry{playlist_id:7,track_id:21,entry_index:2},
                     rbl_pdb::PlaylistEntry{playlist_id:7,track_id:20,entry_index:2}];
        let grouped=group_playlist_entries(&entries);
        assert_eq!(grouped[&7].iter().map(|e|e.track_id).collect::<Vec<_>>(),vec![10,20,21,20]);
        assert_eq!(grouped[&9].iter().map(|e|e.track_id).collect::<Vec<_>>(),vec![30]);
        assert!(!grouped.contains_key(&8));
    }
}
