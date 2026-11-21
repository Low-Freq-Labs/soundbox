"""Small generated playback/catalog inputs; callers own disposable targets."""
import io
from pathlib import Path
import struct
import wave


def play_counts(entries):
    """Encode (plays, skips, rating, resume) rows in native track order."""
    header = b'mhdp' + struct.pack('<III', 96, 28, len(entries)) + bytes(80)
    return header + b''.join(struct.pack('<IIIIIII', plays, 3_800_000_000 if plays else 0,
        resume, rating, 0, skips, 3_800_000_010 if skips else 0)
        for plays, skips, rating, resume in entries)


def rewrite_catalog(root: Path, change=None, *, external=False, artwork=False):
    """Simulate another exporter using its native renderer, never SoundBox's planner."""
    from clickwheel.ipod import get_ipod_tracks, parse_itunesdb
    from clickwheel.ipod.ipod_models import capabilities_for_family_gen
    from clickwheel.ipod.itunesdb_writer import ChecksumType, PlaylistInfo, TrackInfo, write_itunesdb
    from clickwheel.ipod.sync import _existing_track_to_trackinfo

    database = root / 'iPod_Control/iTunes/iTunesDB'
    before = database.read_bytes()
    parsed = parse_itunesdb(io.BytesIO(before))
    raw_tracks = get_ipod_tracks(parsed)
    tracks = [_existing_track_to_trackinfo(row) for row in raw_tracks]
    for info, row in zip(tracks, raw_tracks):
        info.comment = row.get('Comment') or row.get('comment')
    by_track = {row['trackID']: row['dbid'] for row in raw_tracks}
    playlists = [PlaylistInfo(row['Title'], [by_track[item['trackID']] for item in row.get('items', ())],
                              playlist_id=row['playlistID'])
                 for row in parsed.get('mhlp', ()) if not row.get('isMaster')]
    added = None
    if external:
        dbid = max(track.dbid for track in tracks) + 1
        media = root / 'iPod_Control/Music/F00/EXTERNAL.wav'
        media.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(media), 'wb') as audio:
            audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(44100)
            audio.writeframes(bytes(256))
        added = TrackInfo('External song', ':iPod_Control:Music:F00:EXTERNAL.wav',
                          dbid=dbid, artist='External artist', filetype='wav', size=media.stat().st_size,
                          length=2, sample_rate=44100)
        tracks.append(added)
        playlist_id = max(row.playlist_id or 0 for row in playlists) + 1
        playlists.append(PlaylistInfo('External playlist', [dbid], playlist_id=playlist_id))
    if change is not None:
        change(tracks, playlists)
    if artwork:
        from PIL import Image
        from soundbox.stock_artwork import StockArtworkCache, render_stock_artwork, validate_stock_artwork
        assert added is not None
        image = root.parent / 'external-cover.jpg'
        Image.new('RGB', (40, 40), (80, 160, 30)).save(image)
        cache = StockArtworkCache(root.parent / 'external-art-cache')
        folder = root / 'iPod_Control/Artwork'
        rendered = render_stock_artwork(tracks, {added.dbid: cache.prepare(image)},
                                       cache=cache, existing_artwork_dir=folder)
        assert rendered is not None
        validate_stock_artwork(rendered, tracks)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'ArtworkDB').write_bytes(rendered.artwork_db)
        for item in rendered.ithmb_files:
            (root / item.relative_path).write_bytes(b''.join(segment.path.read_bytes() for segment in item.segments))
    assert write_itunesdb(str(root), tracks, backup=False, force_checksum=ChecksumType.NONE,
                         playlists=playlists, capabilities=capabilities_for_family_gen('iPod Video', '5.5th Gen'))
    # Another exporter preserves the library's stable identity across rewrites.
    after = bytearray(database.read_bytes())
    after[0x48:0x50] = before[0x48:0x50]
    database.write_bytes(after)
    return added
