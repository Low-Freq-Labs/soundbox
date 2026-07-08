"""
track_row codec (SoundBox CDJ-export prototype).
136-byte scalar header + 21 device_sql_string pointers, per Deep-Symmetry
rekordbox_pdb.ksy. Two paths:
  * decode_track  -> full semantic fields + 21 strings (proves understanding)
  * encode_track  -> build a VALID row with our own string packing
    (byte-identical to rekordbox is NOT expected: rekordbox's string layout
     is internal; a CDJ only needs offsets that resolve correctly. We prove
     correctness via encode->decode round-trip equality.)
"""
import struct
from rows import read_string, write_string

HDR = 136
FMT = "<HHIIIIIHHIIIIIIIIIIIIHHHHHHBBHH" + "H" * 21
SCALARS = ["subtype", "index_shift", "bitmask", "sample_rate", "composer_id",
    "file_size", "_u1", "_u2", "_u3", "artwork_id", "key_id",
    "original_artist_id", "label_id", "remixer_id", "bitrate", "track_number",
    "tempo", "genre_id", "album_id", "artist_id", "id", "disc_number",
    "play_count", "year", "sample_depth", "duration", "_u4", "color_id",
    "rating", "_u5", "_u6"]
STRINGS = ["isrc", "texter", "unk2", "unk3", "unk4", "message", "kuvo_public",
    "autoload_hotcues", "unk5", "unk6", "date_added", "release_date",
    "mix_name", "unk7", "analyze_path", "analyze_date", "comment", "title",
    "unk8", "filename", "file_path"]

assert struct.calcsize(FMT) == HDR


def decode_track(body):
    vals = struct.unpack_from(FMT, body, 0)
    f = dict(zip(SCALARS, vals[:len(SCALARS)]))
    ofs = vals[len(SCALARS):]
    f["_ofs"] = list(ofs)
    strings, enc = {}, {}
    for name, o in zip(STRINGS, ofs):
        if o == 0 or o >= len(body):
            strings[name] = None; enc[name] = None; continue
        text, e, _ = read_string(body, o)
        strings[name] = text; enc[name] = e
    f["strings"] = strings
    f["_enc"] = enc
    return f


def encode_track(f):
    """Build a valid track row: 136B header, then strings packed in index order
    (4-byte aligned), offsets written into ofs_strings."""
    blobs, offsets = [], []
    pos = HDR
    for name in STRINGS:
        text = f["strings"].get(name)
        e = f["_enc"].get(name)
        if text is None:
            offsets.append(0); continue
        b = write_string(text, e)
        offsets.append(pos)
        blobs.append(b)
        pos += len(b)
    scalars = [f[n] for n in SCALARS]
    header = struct.pack(FMT, *scalars, *offsets)
    row = bytearray(header + b"".join(blobs))
    while len(row) % 4:            # 4-byte row alignment (learned from metadata rows)
        row += b"\x00"
    return bytes(row)


def secs(d):
    return f"{d//60}:{d%60:02d}"
