"""
Row-payload codecs for DeviceSQL .pdb rows (SoundBox CDJ-export prototype).
Decode a row body into fields AND re-encode it byte-identically — the proof
we can *generate* rows, not just carry them. Per Deep-Symmetry rekordbox_pdb.ksy.

Strings use rekordbox's device_sql_string: a leading length_and_kind byte
selects short-ASCII (odd byte), long-ASCII (0x40) or long-UTF16LE (0x90).
"""
import struct


# ---- device_sql_string ------------------------------------------------
def read_string(buf, pos):
    kind = buf[pos]
    if kind == 0x40:
        length = struct.unpack_from("<H", buf, pos + 1)[0]
        unknown = buf[pos + 3]
        text = buf[pos + 4: pos + length].decode("ascii", "replace")
        return text, ("long_ascii", unknown), length
    if kind == 0x90:
        length = struct.unpack_from("<H", buf, pos + 1)[0]
        unknown = buf[pos + 3]
        text = buf[pos + 4: pos + length].decode("utf-16-le", "replace")
        return text, ("long_utf16le", unknown), length
    length = kind >> 1                       # short ASCII
    text = buf[pos + 1: pos + length].decode("ascii", "replace")
    return text, ("short", None), length


def write_string(text, enc):
    kind_type, unknown = enc
    if kind_type == "short":
        b = text.encode("ascii")
        return bytes([((len(b) + 1) << 1) | 1]) + b
    if kind_type == "long_ascii":
        b = text.encode("ascii")
        return bytes([0x40]) + struct.pack("<H", len(b) + 4) + bytes([unknown]) + b
    b = text.encode("utf-16-le")             # long_utf16le
    return bytes([0x90]) + struct.pack("<H", len(b) + 4) + bytes([unknown]) + b


# ---- simple row schemas ----------------------------------------------
# each entry: (field_name, kind)  kind in {u1,u2,u4,rawN,string}
SCHEMAS = {
    "colors":  [("_pre", "raw5"), ("id", "u2"), ("_u", "u1"), ("name", "string")],
    "keys":    [("id", "u4"), ("id2", "u4"), ("name", "string")],
    "labels":  [("id", "u4"), ("name", "string")],
    "genres":  [("id", "u4"), ("name", "string")],
    "artists": [("subtype", "u2"), ("index_shift", "u2"), ("id", "u4"),
                ("_u", "u1"), ("ofs_name", "u1"), ("name", "string")],
    "albums":  [("subtype", "u2"), ("index_shift", "u2"), ("_a", "raw4"),
                ("artist_id", "u4"), ("id", "u4"), ("_b", "raw4"),
                ("_u", "u1"), ("ofs_name", "u1"), ("name", "string")],
    "playlist_tree":    [("parent_id", "u4"), ("_gap", "raw4"),
                ("sort_order", "u4"), ("id", "u4"),
                ("raw_is_folder", "u4"), ("name", "string")],
    "playlist_entries": [("entry_index", "u4"), ("track_id", "u4"),
                ("playlist_id", "u4")],
}
_FIXED = {"u1": 1, "u2": 2, "u4": 4}


def decode_row(table, body):
    """Return (fields dict, n_bytes_consumed) or None if we don't model it."""
    schema = SCHEMAS.get(table)
    if schema is None:
        return None
    f, pos = {}, 0
    # far-name subtype (0x64) puts the string elsewhere — carry those for now
    if table in ("artists", "albums"):
        subtype = struct.unpack_from("<H", body, 0)[0]
        if subtype & 0x04:
            return None
    for name, kind in schema:
        if kind == "string":
            # simple rows: string sits immediately after the fixed fields
            text, enc, n = read_string(body, pos)
            f[name] = text; f["_enc"] = enc; pos += n
        elif kind.startswith("raw"):
            n = int(kind[3:]); f[name] = body[pos:pos + n]; pos += n
        else:
            n = _FIXED[kind]
            f[name] = int.from_bytes(body[pos:pos + n], "little"); pos += n
    return f, pos


def encode_row(table, f):
    schema = SCHEMAS[table]
    out = bytearray()
    for name, kind in schema:
        if kind == "string":
            out += write_string(f[name], f["_enc"])
        elif kind.startswith("raw"):
            out += f[name]
        else:
            out += int(f[name]).to_bytes(_FIXED[kind], "little")
    return bytes(out)
