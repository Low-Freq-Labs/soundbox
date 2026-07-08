"""
Standalone DeviceSQL (.pdb) reader + writer — SoundBox CDJ-export prototype.
NOT wired into SoundBox. Pure R&D: prove we can parse a real rekordbox
export.pdb into a model and re-serialize it BYTE-FOR-BYTE identical.

Format per Deep-Symmetry crate-digger rekordbox_pdb.ksy.
Integers little-endian. Pages are len_page bytes each, indexed from 0.
Page 0 holds the file header + table directory. Data pages hold rows in a
heap growing up from offset 40, plus a row index growing DOWN from the end
in groups of 16 (each group: 16 u2 offsets + u2 present-flags + u2 txn-flags,
0x24 bytes, base = len_page - group_index*0x24).
"""
import struct
from dataclasses import dataclass, field

PAGE_HEAP_POS = 40          # where the row heap starts within a page
ROW_GROUP_SIZE = 0x24       # 36 bytes per group of 16 rows

PAGE_TYPES = {0:"tracks",1:"genres",2:"artists",3:"albums",4:"labels",5:"keys",
6:"colors",7:"playlist_tree",8:"playlist_entries",9:"unknown_9",10:"unknown_10",
11:"history_playlists",12:"history_entries",13:"artwork",14:"unknown_14",
15:"unknown_15",16:"columns",17:"unknown_17",18:"unknown_18",19:"history"}


@dataclass
class Row:
    present: bool
    offset: int              # relative to heap_pos (only meaningful if present)
    body: bytes = b""        # raw row payload (carried opaque for round-trip)


@dataclass
class Page:
    index: int
    raw: bytes                       # original bytes (fallback / non-data pages)
    is_data: bool = False
    header40: bytes = b""            # fixed 40-byte page header (carried)
    num_row_offsets: int = 0
    rows: list = field(default_factory=list)   # list[Row] length == num_row_offsets

    @property
    def type_id(self):
        return struct.unpack_from("<I", self.raw, 8)[0]

    @property
    def type_name(self):
        return PAGE_TYPES.get(self.type_id, f"type{self.type_id}")

    @property
    def present_rows(self):
        return [r for r in self.rows if r.present]


@dataclass
class Table:
    type_id: int
    empty_candidate: int
    first_page: int
    last_page: int
    @property
    def name(self):
        return PAGE_TYPES.get(self.type_id, f"type{self.type_id}")


class PdbFile:
    def __init__(self, data: bytes):
        self.data = data
        self.len_page = struct.unpack_from("<I", data, 4)[0]
        (_zero, self.len_page, self.num_tables, self.next_unused_page,
         self._unk, self.sequence, self._gap) = struct.unpack_from("<7I", data, 0)
        # table directory
        self.tables = []
        off = 28
        for _ in range(self.num_tables):
            t, ec, fp, lp = struct.unpack_from("<4I", data, off); off += 16
            self.tables.append(Table(t, ec, fp, lp))
        # pages
        self.total_pages = len(data) // self.len_page
        self.pages = [self._parse_page(i) for i in range(self.total_pages)]

    # ---- parsing --------------------------------------------------------
    def _parse_page(self, idx):
        start = idx * self.len_page
        raw = self.data[start:start + self.len_page]
        p = Page(index=idx, raw=raw)
        if idx == 0:
            return p  # header page, carried structurally via header/tables
        page_flags = raw[27]
        is_data = (page_flags & 0x40) == 0
        p.is_data = is_data
        if not is_data:
            return p
        p.header40 = raw[:PAGE_HEAP_POS]
        # num_row_offsets / num_rows live in a 24-bit big-endian bitfield @24
        b = (raw[24] << 16) | (raw[25] << 8) | raw[26]
        num_row_offsets = b >> 11          # top 13 bits
        p.num_row_offsets = num_row_offsets
        if num_row_offsets == 0:
            return p
        num_groups = (num_row_offsets - 1) // 16 + 1
        rows = []
        for g in range(num_groups):
            base = self.len_page - g * ROW_GROUP_SIZE
            present_flags = struct.unpack_from("<H", raw, base - 4)[0]
            for ri in range(16):
                if len(rows) >= num_row_offsets:
                    break
                present = bool((present_flags >> ri) & 1)
                ofs = struct.unpack_from("<H", raw, base - (6 + 2 * ri))[0]
                rows.append(Row(present=present, offset=ofs))
        # extract row bodies: contiguous by ascending offset up to used region
        starts = sorted({r.offset for r in rows if r.present})
        used_size = struct.unpack_from("<H", raw, 30)[0]
        heap_end = PAGE_HEAP_POS + used_size
        bounds = starts + [heap_end - PAGE_HEAP_POS]
        span = {}
        for i, s in enumerate(starts):
            span[s] = (s, bounds[i + 1])
        for r in rows:
            if r.present and r.offset in span:
                a, b2 = span[r.offset]
                r.body = raw[PAGE_HEAP_POS + a: PAGE_HEAP_POS + b2]
        p.rows = rows
        return p

    # ---- serialising ----------------------------------------------------
    def serialize(self):
        out = bytearray()
        # page 0: rebuild header + table dir from the model, pad from original
        hdr = struct.pack("<7I", 0, self.len_page, self.num_tables,
                          self.next_unused_page, self._unk, self.sequence, self._gap)
        for t in self.tables:
            hdr += struct.pack("<4I", t.type_id, t.empty_candidate,
                               t.first_page, t.last_page)
        page0 = bytearray(self.pages[0].raw)   # keep exact padding
        page0[:len(hdr)] = hdr
        out += page0
        # remaining pages
        for p in self.pages[1:]:
            out += self._serialize_page(p)
        return bytes(out)

    def _serialize_page(self, p):
        if not p.is_data or p.num_row_offsets == 0:
            return p.raw
        # Carry the heap payload verbatim (incl. rekordbox's ghost bytes from
        # deleted rows); regenerate ONLY the structural row index from the model.
        buf = bytearray(p.raw)
        num_groups = (p.num_row_offsets - 1) // 16 + 1
        for g in range(num_groups):
            base = self.len_page - g * ROW_GROUP_SIZE
            present_flags = 0
            for ri in range(16):
                idx = g * 16 + ri
                if idx >= len(p.rows):
                    break
                r = p.rows[idx]
                if r.present:
                    present_flags |= (1 << ri)
                struct.pack_into("<H", buf, base - (6 + 2 * ri), r.offset)
            struct.pack_into("<H", buf, base - 4, present_flags)   # from model
        return bytes(buf)


# ---- assembling a FRESH file from scratch (the actual writer) ----------
def build_data_page(type_id, page_index, next_page, row_bodies, len_page=4096):
    """Pack rows into a brand-new data page: heap grows up from 40, the row
    index grows down from the end in groups of 16, rows 4-byte aligned."""
    buf = bytearray(len_page)
    heap = PAGE_HEAP_POS
    offsets = []
    for body in row_bodies:
        b = bytes(body)
        offsets.append(heap - PAGE_HEAP_POS)
        buf[heap:heap + len(b)] = b
        heap += len(b)
        while heap % 4:
            heap += 1
    used_size = heap - PAGE_HEAP_POS
    n = len(row_bodies)
    num_groups = (n - 1) // 16 + 1 if n else 0
    for g in range(num_groups):
        base = len_page - g * ROW_GROUP_SIZE
        present = 0
        for ri in range(16):
            idx = g * 16 + ri
            if idx >= n:
                break
            struct.pack_into("<H", buf, base - (6 + 2 * ri), offsets[idx])
            present |= (1 << ri)
        struct.pack_into("<H", buf, base - 4, present)
    # header
    struct.pack_into("<I", buf, 4, page_index)
    struct.pack_into("<I", buf, 8, type_id)
    struct.pack_into("<I", buf, 12, next_page)
    val = (n << 11) | n                       # num_row_offsets<<11 | num_rows (24-bit BE)
    buf[24], buf[25], buf[26] = (val >> 16) & 0xff, (val >> 8) & 0xff, val & 0xff
    buf[27] = 0x24                            # page_flags: data page (0x40 bit clear)
    struct.pack_into("<H", buf, 28, len_page - PAGE_HEAP_POS - used_size - num_groups * ROW_GROUP_SIZE)
    struct.pack_into("<H", buf, 30, used_size)
    return bytes(buf)


def build_file(tables, len_page=4096):
    """tables: list of (type_id, [row_bodies]). One data page per table (small
    library). Returns a complete .pdb byte string readable by PdbFile."""
    # layout: page 0 = header; then one page per table at odd indices? Keep it
    # simple: page 0 header, tables on consecutive pages starting at 1.
    n_tables = len(tables)
    pages = []
    table_ptrs = []
    pidx = 1
    for type_id, rows in tables:
        table_ptrs.append((type_id, pidx, pidx))
        pages.append(build_data_page(type_id, pidx, pidx + 1, rows, len_page))
        pidx += 1
    next_unused = pidx
    header = bytearray(len_page)
    struct.pack_into("<7I", header, 0, 0, len_page, n_tables, next_unused, 0, 0, 0)
    off = 28
    for type_id, fp, lp in table_ptrs:
        struct.pack_into("<4I", header, off, type_id, 0, fp, lp); off += 16
    return bytes(header) + b"".join(pages)
