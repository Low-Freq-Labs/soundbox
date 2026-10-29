//! Merge changed track rows into copied native catalogs; never open media.
use std::{collections::{BTreeMap, BTreeSet}, error::Error, path::{Path, PathBuf}};
use rbl_pdb::{Pdb, PageType};
use rusqlite::{Connection, types::Value, params_from_iter};
use serde::Deserialize;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[derive(Deserialize)]
pub struct Node { pub id:u32, pub parent:u32, pub name:String, pub folder:bool, pub tracks:Vec<u32> }
#[derive(Deserialize)]
pub struct Image { pub id:u32, pub path:String }
#[derive(Deserialize)]
pub struct Request {
    pub baseline:PathBuf, pub partial:Option<PathBuf>, pub output:PathBuf,
    pub playlists:Vec<Node>, pub track_ids:BTreeSet<u32>,
    pub artwork:BTreeMap<u32,Image>,
    pub my_tags:Vec<rbl_export::SourceMyTag>,
    #[serde(default)] pub preserve_playlists:BTreeSet<u32>,
    #[serde(default)] pub tag_assignments:BTreeMap<u32,Vec<u64>>,
}

fn read_rows(db:&Connection, table:&str) -> Result<(Vec<String>,Vec<Vec<Value>>)> {
    let mut statement=db.prepare(&format!("SELECT * FROM {table}"))?;
    let names=statement.column_names().iter().map(|s|s.to_string()).collect::<Vec<_>>();
    let n=names.len();
    let rows=statement.query_map([],|r|(0..n).map(|i|r.get(i)).collect::<rusqlite::Result<Vec<Value>>>())?
        .collect::<std::result::Result<Vec<_>,_>>()?;
    Ok((names,rows))
}
fn insert(db:&Connection, table:&str, row:&[Value]) -> Result<()> {
    db.execute(&format!("INSERT OR REPLACE INTO {table} VALUES ({})",vec!["?";row.len()].join(",")),params_from_iter(row))?;
    Ok(())
}
fn text(v:&Value)->Result<&str>{match v{Value::Text(s)=>Ok(s),_=>Err("non-text native field".into())}}
fn integer(v:&Value) -> Result<u32> {
    match v {Value::Integer(n)=>Ok(u32::try_from(*n)?),Value::Null=>Ok(0),_=>Err("non-integer native identity".into())}
}
fn remap(v:&mut Value, map:&BTreeMap<u32,u32>) -> Result<()> {
    let old=integer(v)?;
    if old!=0 {*v=Value::Integer(i64::from(*map.get(&old).ok_or("unresolved native lookup")?));}
    Ok(())
}
fn native_rows(bytes:&[u8], kind:PageType) -> Result<Vec<Vec<u8>>> {
    let pdb=Pdb::parse(bytes)?;
    let rows=pdb.rows(pdb.table(kind).ok_or("missing native table")?);
    rows.iter().map(|r| {
        let end=rows.iter().filter(|n|n.page_offset==r.page_offset && n.offset>r.offset)
            .map(|n|n.offset).min().unwrap_or(r.page_offset+40+usize::from(u16::from_le_bytes(bytes[r.page_offset+30..r.page_offset+32].try_into()?)));
        Ok(bytes.get(r.offset..end).ok_or("invalid native row boundary")?.to_vec())
    }).collect()
}
fn replace(bytes:&mut Vec<u8>, kind:u32, mut rows:Vec<Vec<u8>>, typed:bool) -> Result<()> {
    if typed {soundbox_cdj::number_track_slots(&mut rows)?;}
    *bytes=rbl_pdb::build::replace_table(bytes,kind,&rows).ok_or("cannot merge native table")?;
    Ok(())
}
fn word(row:&[u8],at:usize)->Result<u32>{Ok(u32::from_le_bytes(row.get(at..at+4).ok_or("short native row")?.try_into()?))}
fn put(row:&mut [u8],at:usize,value:u32){row[at..at+4].copy_from_slice(&value.to_le_bytes());}

fn classic(request:&Request, partial:Option<&[u8]>, output:&Path) -> Result<()> {
    use rbl_pdb::rows::*;
    let mut bytes=std::fs::read(output)?;
    if let Some(changed)=partial {
        let mut mappings=BTreeMap::new();
        for (kind,number) in [(PageType::Genres,1),(PageType::Artists,2),(PageType::Albums,3),(PageType::Labels,4),(PageType::Keys,5)] {
            let pdb=Pdb::parse(&bytes)?;
            let old=pdb.named_rows(pdb.table(kind).ok_or("missing lookup")?);
            let mut by_name=old.iter().map(|r|(r.name.clone(),r.id)).collect::<BTreeMap<_,_>>();
            let mut next=old.iter().map(|r|r.id).max().unwrap_or(0).checked_add(1).ok_or("lookup overflow")?;
            let source=Pdb::parse(changed)?;
            let mut rows=native_rows(&bytes,kind)?;
            let mut map=BTreeMap::new();
            for row in source.named_rows(source.table(kind).ok_or("missing changed lookup")?) {
                let id=if let Some(id)=by_name.get(&row.name){*id}else{
                    let id=next;next=next.checked_add(1).ok_or("lookup overflow")?;
                    rows.push(match kind {PageType::Artists=>artist_row(id,&row.name),PageType::Albums=>album_row(id,0,&row.name),PageType::Keys=>key_row(id,&row.name),_=>simple_named_row(id,&row.name)});
                    by_name.insert(row.name,id);id
                };
                map.insert(row.id,id);
            }
            replace(&mut bytes,number,rows,number==2 || number==3)?;
            mappings.insert(number,map);
        }
        let mut art=native_rows(&bytes,PageType::Artwork)?;
        let existing=art.iter().map(|r|word(r,0)).collect::<Result<BTreeSet<_>>>()?;
        let art_map=request.artwork.iter().map(|(id,image)|(*id,image.id)).collect::<BTreeMap<_,_>>();
        let mut added=existing;
        for image in request.artwork.values() {
            if added.insert(image.id){art.push(artwork_row(image.id,&image.path));}
        }
        replace(&mut bytes,13,art,false)?;
        let mut tracks=native_rows(&bytes,PageType::Tracks)?.into_iter().map(|r|Ok((word(&r,0x48)?,r)))
            .collect::<Result<BTreeMap<_,_>>>()?;
        for mut row in native_rows(changed,PageType::Tracks)? {
            for (at,kind) in [(0x0c,2),(0x24,2),(0x2c,2),(0x44,2),(0x40,3),(0x3c,1),(0x28,4),(0x20,5)] {
                let id=word(&row,at)?;
                if id!=0 {put(&mut row,at,*mappings[&kind].get(&id).ok_or("unresolved classic lookup")?);}
            }
            let id=word(&row,0x1c)?;
            if id!=0 {put(&mut row,0x1c,*art_map.get(&id).ok_or("unresolved classic artwork")?);}
            tracks.insert(word(&row,0x48)?,row);
        }
        tracks.retain(|id,_|request.track_ids.contains(id));
        replace(&mut bytes,0,tracks.into_values().collect(),true)?;
    } else {
        let tracks=native_rows(&bytes,PageType::Tracks)?.into_iter().filter(|r|word(r,0x48).is_ok_and(|id|request.track_ids.contains(&id))).collect();
        replace(&mut bytes,0,tracks,true)?;
    }
    let mut positions=BTreeMap::new();
    let old_nodes=native_rows(&bytes,PageType::PlaylistTree)?.into_iter().map(|row|Ok((word(&row,12)?,row))).collect::<Result<BTreeMap<_,_>>>()?;
    let old_entries=native_rows(&bytes,PageType::PlaylistEntries)?;
    let mut nodes=Vec::new();let mut entries=Vec::new();
    for node in &request.playlists {
        let seq=positions.entry(node.parent).or_insert(0u32);
        if request.preserve_playlists.contains(&node.id) {
            nodes.push(old_nodes.get(&node.id).ok_or("missing external playlist")?.clone());
            entries.extend(old_entries.iter().filter(|r|word(r,8).is_ok_and(|id|id==node.id)).cloned());
            *seq+=1;continue;
        }
        nodes.push(playlist_row(node.id,node.parent,*seq,node.folder,&node.name));*seq+=1;
        for (position,id) in node.tracks.iter().enumerate(){
            if !request.track_ids.contains(id){return Err("playlist references missing track".into());}
            entries.push(playlist_entry_row(u32::try_from(position+1)?,*id,node.id));
        }
    }
    replace(&mut bytes,7,nodes,false)?;replace(&mut bytes,8,entries,false)?;
    let mut property=Pdb::parse(&bytes)?.property().ok_or("missing native property")?;
    property.contents=u32::try_from(request.track_ids.len())?;
    replace(&mut bytes,19,vec![property_row(&property).ok_or("invalid native property")?],true)?;
    std::fs::write(output,bytes)?;
    Ok(())
}

fn plus(request:&Request, partial:Option<&Path>, output:&Path) -> Result<()> {
    let mut db=rbl_onelibrary::playlists::open_read_write(output)?;
    let transaction=db.transaction()?;
    if let Some(path)=partial {
        let source=rbl_onelibrary::ExportLibrary::open_read_only(path)?;
        let source=source.connection();
        let mut maps=BTreeMap::new();
        for table in ["artist","album","genre","label","key"] {
            let (_,old)=read_rows(&transaction,table)?;
            let mut names=old.iter().map(|r|(text(&r[1]).unwrap_or_default().to_owned(),integer(&r[0]).unwrap_or(0))).collect::<BTreeMap<_,_>>();
            let mut next=old.iter().map(|r|integer(&r[0])).collect::<Result<Vec<_>>>()?.into_iter().max().unwrap_or(0).checked_add(1).ok_or("lookup overflow")?;
            let (_,rows)=read_rows(source,table)?;let mut map=BTreeMap::new();
            for mut row in rows {
                let old_id=integer(&row[0])?;
                let name=text(&row[1])?.to_owned();
                let id=if let Some(id)=names.get(&name){*id}else{
                    let id=next;next=next.checked_add(1).ok_or("lookup overflow")?;row[0]=Value::Integer(i64::from(id));
                    insert(&transaction,table,&row)?;names.insert(name,id);id
                };
                map.insert(old_id,id);
            }
            maps.insert(table,map);
        }
        let art_map=request.artwork.iter().map(|(id,image)|(*id,image.id)).collect::<BTreeMap<_,_>>();
        for image in request.artwork.values(){transaction.execute("INSERT OR IGNORE INTO image VALUES (?1,?2)",rusqlite::params![image.id,image.path])?;}
        let (names,rows)=read_rows(source,"content")?;
        for mut row in rows {
            let id=integer(&row[0])?;
            for (at,name) in names.iter().enumerate() {
                let table=if name.starts_with("artist_id_"){Some("artist")}else{match name.as_str(){"album_id"=>Some("album"),"genre_id"=>Some("genre"),"label_id"=>Some("label"),"key_id"=>Some("key"),_=>None}};
                if let Some(table)=table{remap(&mut row[at],&maps[table])?;}
                if name=="image_id"{remap(&mut row[at],&art_map)?;}
            }
            transaction.execute("DELETE FROM cue WHERE content_id=?1",[id])?;
            transaction.execute("DELETE FROM myTag_content WHERE content_id=?1",[id])?;
            insert(&transaction,"content",&row)?;
        }
        let mut next:u32=transaction.query_row("SELECT COALESCE(MAX(cue_id),0)+1 FROM cue",[],|r|r.get(0))?;
        for mut row in read_rows(source,"cue")?.1 {row[0]=Value::Integer(i64::from(next));next=next.checked_add(1).ok_or("cue overflow")?;insert(&transaction,"cue",&row)?;}
    }
    // Tag IDs belong to a desktop library. The parent maps incoming tags into
    // the current USB namespace; never replace another library's definitions.
    for tag in &request.my_tags {
        transaction.execute("INSERT OR REPLACE INTO myTag VALUES (?1,?2,?3,?4,?5)",rusqlite::params![i64::try_from(tag.id)?,tag.seq,tag.name,tag.attribute,i64::try_from(tag.parent)?])?;
    }
    for (content,tags) in &request.tag_assignments {
        transaction.execute("DELETE FROM myTag_content WHERE content_id=?1",[content])?;
        for tag in tags {transaction.execute("INSERT INTO myTag_content VALUES (?1,?2)",rusqlite::params![i64::try_from(*tag)?,content])?;}
    }
    let ids=read_rows(&transaction,"content")?.1.into_iter().map(|r|integer(&r[0])).collect::<Result<Vec<_>>>()?;
    for id in ids.into_iter().filter(|id|!request.track_ids.contains(id)) {
        transaction.execute("DELETE FROM cue WHERE content_id=?1",[id])?;
        transaction.execute("DELETE FROM myTag_content WHERE content_id=?1",[id])?;
        transaction.execute("DELETE FROM content WHERE content_id=?1",[id])?;
    }
    let old_ids=read_rows(&transaction,"playlist")?.1.into_iter().map(|r|integer(&r[0])).collect::<Result<Vec<_>>>()?;
    for id in old_ids {
        if !request.preserve_playlists.contains(&id) {
            transaction.execute("DELETE FROM playlist_content WHERE playlist_id=?1",[id])?;
            transaction.execute("DELETE FROM playlist WHERE playlist_id=?1",[id])?;
        }
    }
    let mut positions=BTreeMap::new();
    for node in &request.playlists {
        let seq=positions.entry(node.parent).or_insert(0i64);*seq+=1;
        if request.preserve_playlists.contains(&node.id){continue;}
        transaction.execute("INSERT INTO playlist VALUES (?1,?2,?3,0,?4,?5)",rusqlite::params![node.id,*seq,node.name,i64::from(node.folder),node.parent])?;
        for (position,id) in node.tracks.iter().enumerate(){transaction.execute("INSERT INTO playlist_content VALUES (?1,?2,?3)",rusqlite::params![node.id,*id,i64::try_from(position+1)?])?;}
    }
    transaction.execute("UPDATE property SET numberOfContents=?1",[i64::try_from(request.track_ids.len())?])?;
    transaction.commit()?;
    Ok(())
}

pub fn merge(request:&Request) -> Result<()> {
    let dir=request.output.join("PIONEER/rekordbox");std::fs::create_dir_all(&dir)?;
    for name in ["export.pdb","exportExt.pdb","exportLibrary.db"] {std::fs::copy(request.baseline.join("PIONEER/rekordbox").join(name),dir.join(name))?;}
    let partial=request.partial.as_ref().map(|p|p.join("PIONEER/rekordbox"));
    let changed=partial.as_ref().map(|p|std::fs::read(p.join("export.pdb"))).transpose()?;
    classic(request,changed.as_deref(),&dir.join("export.pdb"))?;
    plus(request,partial.as_ref().map(|p|p.join("exportLibrary.db")).as_deref(),&dir.join("exportLibrary.db"))?;
    // Preserve native recommendation/history/options, alongside merged tags.
    let db=rbl_onelibrary::ExportLibrary::open_read_only(&dir.join("exportLibrary.db"))?;
    let tags=read_rows(db.connection(),"myTag")?.1.into_iter().map(|r|Ok(rbl_export::SourceMyTag{
        id:u64::from(integer(&r[0])?),seq:integer(&r[1])?.checked_add(1).ok_or("tag sequence overflow")?,name:text(&r[2])?.to_owned(),attribute:u8::try_from(integer(&r[3])?)?,parent:u64::from(integer(&r[4])?)})).collect::<Result<Vec<_>>>()?;
    let mut by_track:BTreeMap<u32,Vec<u64>>=BTreeMap::new();
    for row in read_rows(db.connection(),"myTag_content")?.1 {by_track.entry(integer(&row[1])?).or_default().push(u64::from(integer(&row[0])?));}
    let tagged=by_track.iter().map(|(id,tags)|(*id,tags.as_slice())).collect::<Vec<_>>();
    let master:u32=db.connection().query_row("SELECT myTagMasterDBID FROM property",[],|r|r.get(0))?;
    let generated=rbl_export::ext_pdb::build(&tags,&tagged,master);
    let ext=std::fs::read(dir.join("exportExt.pdb"))?;
    let baseline=rbl_onelibrary::ExportLibrary::open_read_only(&request.baseline.join("PIONEER/rekordbox/exportLibrary.db"))?;
    let old_tags=read_rows(baseline.connection(),"myTag")?.1.into_iter().map(|r|Ok((integer(&r[0])?,r))).collect::<Result<BTreeMap<_,_>>>()?;
    let new_tags=read_rows(db.connection(),"myTag")?.1.into_iter().map(|r|Ok((integer(&r[0])?,r))).collect::<Result<BTreeMap<_,_>>>()?;
    let raw_tags=native_rows(&ext,PageType::Albums)?.into_iter().map(|r|Ok((word(&r,20)?,r))).collect::<Result<BTreeMap<_,_>>>()?;
    let raw_members=native_rows(&ext,PageType::Labels)?.into_iter().map(|r|Ok(((word(&r,4)?,word(&r,8)?),r))).collect::<Result<BTreeMap<_,_>>>()?;
    let old_master:u32=baseline.connection().query_row("SELECT myTagMasterDBID FROM property",[],|r|r.get(0))?;
    let mut file=rbl_pdb::build::FileBuilder::new(4096);file.write_order(&[7,3]);
    for (number,kind) in [PageType::Tracks,PageType::Genres,PageType::Artists,PageType::Albums,
            PageType::Labels,PageType::Keys,PageType::Colors,PageType::PlaylistTree,PageType::PlaylistEntries].into_iter().enumerate() {
        let mut rows=native_rows(if [3,4,7].contains(&number){&generated}else{&ext},kind)?;
        for row in &mut rows {
            if number==3 {
                let id=word(row,20)?;
                if old_tags.get(&id)==new_tags.get(&id) {if let Some(original)=raw_tags.get(&id){*row=original.clone();}}
            } else if number==4 {
                if let Some(original)=raw_members.get(&(word(row,4)?,word(row,8)?)){*row=original.clone();}
            }
        }
        if number==7 && old_master==master {rows=native_rows(&ext,kind)?;}
        if number==4 {file.add_table_styled(number as u32,&rows,|_,_|{},rbl_pdb::build::PageStyle::LastRowIndex);}
        else {file.add_table_numbered(number as u32,&rows,|row,index|rbl_pdb::rows::set_index_shift(row,index));}
    }
    std::fs::write(dir.join("exportExt.pdb"),file.finish())?;
    if !rbl_export::verify_databases(&request.output)?.is_ok(){return Err("merged native databases disagree".into());}
    Ok(())
}
