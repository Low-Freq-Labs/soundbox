"""Read complete native cue metadata from already captured USB ANLZ files."""
from pathlib import Path


def read(path):
    from pyrekordbox.anlz import AnlzFile
    cues={}
    for extension,tag_name in (('DAT','PCOB'),('EXT','PCOB'),('DAT','PCO2'),('EXT','PCO2')):
        file=Path(path).with_suffix('.'+extension)
        if not file.is_file():continue
        parsed=AnlzFile.parse_file(file)
        for tag in parsed.getall_tags(tag_name):
            for entry in tag.content.entries:
                slot=int(entry.hot_cue)
                kind=slot+1 if slot>=4 else slot
                start=int(entry.time);end=int(entry.loop_time)
                key=(kind,start if slot==0 else 0)
                cues[key]=dict(kind=kind,time_ms=start,loop_time_ms=end if start<end<0xffffffff else None,
                    color_id=int(getattr(entry,'color_id',0)),color_code=int(getattr(entry,'color_code',0)),
                    comment=str(getattr(entry,'comment','')),active_loop=int(getattr(entry,'status',0))==4 and end<0xffffffff,
                    loop_numerator=int(getattr(entry,'loop_enumerator',0)),loop_denominator=int(getattr(entry,'loop_denominator',0)))
    return list(cues.values())
