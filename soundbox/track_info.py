"""SoundBox metadata overrides. They never retag audio or edit source databases."""
FIELDS = (
    ('album','Album'),('year','Year'),('releaseDate','Release Date'),('comments','Comments'),
    ('playCount','DJ Play Count'),('rating','Rating'),('label','Label'),('color','Color'),
    ('message','Message'),('hotCueAutoLoad','Hot Cue Auto Load'),('publishInfo','Publish Track Information'),
    ('size','Size'),('dateCreated','Date Created'),('sampleRate','Sample Rate'),
    ('bitDepth','Bit Depth'),('location','Location'),('myTags','My Tag'),
    ('lyricist','Lyricist'),
)
EDITABLE = {'album','year','releaseDate','comments','playCount','rating','label','color',
            'hotCueAutoLoad','genre'}


def validate(changes):
    result = {}
    for key,value in changes.items():
        if key not in EDITABLE or not isinstance(value,str) or '\0' in value or len(value)>4096:
            raise ValueError('Unsupported track information')
        if key in ('year','playCount','rating','color'):
            limit = {'year':65535,'playCount':0xffffffff,'rating':5,'color':8}[key]
            if not value.isdigit() or not 0<=int(value)<=limit:
                raise ValueError('Invalid '+dict(FIELDS).get(key,key))
        if key=='hotCueAutoLoad' and value not in ('On','Off'):
            raise ValueError('Invalid hot cue auto load')
        if key=='releaseDate' and value:
            from datetime import date
            date.fromisoformat(value)
        result[key]=value
    return result


def exported(dto, overrides):
    changes = validate(overrides)
    mapping = {'album':'album','genre':'genre','label':'label','comments':'comment',
               'releaseDate':'release_date','year':'year','rating':'rating','color':'color_id'}
    for key,value in changes.items():
        if key in mapping:
            dto[mapping[key]] = int(value) if key in ('year','rating','color') else value
        elif key=='playCount':dto['metadata']['play_count']=int(value)
        elif key=='hotCueAutoLoad':dto['metadata']['hot_cue_auto_load']=value=='On'
