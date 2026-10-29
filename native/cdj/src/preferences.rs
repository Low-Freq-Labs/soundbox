//! Small native preference edits against captured local catalogs only.
use std::{error::Error,path::Path,collections::BTreeSet};
use serde_json::Value;
type Result<T> = std::result::Result<T,Box<dyn Error>>;

pub fn apply(root:&Path, changes:&serde_json::Map<String,Value>) -> Result<()> {
    let path=root.join("PIONEER/rekordbox/exportLibrary.db");
    if !path.exists(){return Err("Initialize Device Library Plus before editing browse preferences".into());}
    let settings=rbl_onelibrary::settings::StickSettings::read(&path)?;
    let mut db=rbl_onelibrary::playlists::open_read_write(&path)?;
    let tx=db.transaction()?;
    let classic=root.join("PIONEER/rekordbox/export.pdb");
    let mut bytes=std::fs::read(&classic)?;
    for (key,slots,kind,mandatory) in [
        ("categories",&settings.categories,17u32,vec![4,17,19,20,24]),
        ("sorts",&settings.sorts,18u32,vec![25,26])] {
        if let Some(order)=changes.get(key) {
            let order=order.as_array().ok_or("ordered list required")?.iter()
                .map(|v|v.as_i64().ok_or("menu identity required")).collect::<std::result::Result<Vec<_>,_>>()?;
            let allowed=slots.iter().map(|s|s.menu_item).collect::<BTreeSet<_>>();
            if order.len()!=order.iter().collect::<BTreeSet<_>>().len() || order.iter().any(|id|!allowed.contains(id))
                || mandatory.iter().any(|id|allowed.contains(id)&&!order.contains(id)) {return Err("invalid browse selection".into());}
            for slot in slots {
                let seq=order.iter().position(|id|*id==slot.menu_item).map_or(0,|n|n as i64+1);
                let table=if kind==17{"category"}else{"sort"};
                tx.execute(&format!("UPDATE {table} SET sequenceNo=?1,isVisible=?2 WHERE menuItem_id=?3"),
                    rusqlite::params![seq,i64::from(seq!=0),slot.menu_item])?;
            }
            let mut rows=soundbox_cdj::browse_rows(&bytes,kind)?;
            for row in &mut rows {
                if row.len()<8{return Err("short native browse row".into());}
                let id=i64::from(u16::from_le_bytes([row[0],row[1]]));
                if !allowed.contains(&id){return Err("classic/Plus browse identities disagree".into());}
                let seq=order.iter().position(|v|*v==id).map_or(0,|n|n+1);
                if kind==17 {row[5]=(row[5]&!1)|u8::from(seq==0);row[6..8].copy_from_slice(&u16::try_from(seq)?.to_le_bytes());}
                else {row[4]=(row[4]&!1)|u8::from(seq==0);row[5]=u8::try_from(seq)?;}
            }
            bytes=rbl_pdb::build::replace_table(&bytes,kind,&rows).ok_or("cannot update classic browse preferences")?;
        }
    }
    if let Some(value)=changes.get("sub_column") {
        let chosen=if value.is_null(){None}else{Some(value.as_i64().ok_or("column identity required")?)};
        if chosen.is_some_and(|id|id==25||id==26||!settings.sorts.iter().any(|s|s.menu_item==id)) {return Err("unsupported right column".into());}
        tx.execute("UPDATE sort SET isSelectedAsSubColumn=0",[])?;
        if let Some(id)=chosen {tx.execute("UPDATE sort SET isSelectedAsSubColumn=1 WHERE menuItem_id=?1",[id])?;}
        let mut rows=soundbox_cdj::browse_rows(&bytes,18)?;
        for row in &mut rows {
            if row.len()<8{return Err("short native sort row".into());}
            let id=i64::from(u16::from_le_bytes([row[0],row[1]]));
            row[4]=(row[4]&!2)|if chosen==Some(id){2}else{0};
        }
        bytes=rbl_pdb::build::replace_table(&bytes,18,&rows).ok_or("cannot update classic right column")?;
    }
    if let Some(colors)=changes.get("colors") {
        let colors=colors.as_object().ok_or("color names required")?;
        let mut names=settings.colors.clone();
        for (id,value) in colors {
            let id=id.parse::<i64>()?;let name=value.as_str().ok_or("color name must be text")?;
            if name.trim().is_empty()||name.len()>512||name.chars().any(char::is_control){return Err("invalid color name".into());}
            let color=names.iter_mut().find(|c|c.id==id).ok_or("unknown color identity")?;
            color.name=name.to_owned();
            tx.execute("UPDATE color SET name=?1 WHERE color_id=?2",rusqlite::params![name,id])?;
        }
        // Native color rows have a different prefix from ordinary named rows.
        let mut rows=soundbox_cdj::browse_rows(&bytes,6)?;
        for row in &mut rows {
            if row.len()<8{return Err("short native color row".into());}
            let id=i64::from(row[4]);
            if colors.contains_key(&id.to_string()) {
                let color=names.iter().find(|c|c.id==id).ok_or("unknown classic color")?;
                let mut updated=rbl_pdb::rows::color_row(u16::try_from(id)?,&color.name);
                updated[..8].copy_from_slice(&row[..8]);*row=updated;
            }
        }
        bytes=rbl_pdb::build::replace_table(&bytes,6,&rows).ok_or("cannot update classic color comments")?;
    }
    tx.commit()?;std::fs::write(classic,bytes)?;
    Ok(())
}
