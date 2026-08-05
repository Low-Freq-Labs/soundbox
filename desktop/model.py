"""Qt table model for the track browser (fast for thousands of rows)."""

from __future__ import annotations

import re

from PySide6.QtCore import QAbstractTableModel, QMimeData, Qt, Signal

from soundbox.paths import ARTIST_MAX, TITLE_MAX

# Manual edits are clamped to the DEVICE budgets so the title/artist shown in
# the app is exactly what the exported file is named — a 500-char paste can't
# silently diverge from the 80-char on-device name.
_EDIT_MAX = {"title": TITLE_MAX, "artist": ARTIST_MAX}

# (row-key, header label, hidden-by-default)
COLUMNS: list[tuple[str, str, bool]] = [
    ("title", "Track Title", False),
    ("artist", "Artist", False),
    ("art", "Art", False),
    ("bpm", "BPM", False),
    ("key", "Key", False),
    ("time", "Time", False),
    ("format", "Format", False),
    ("bitrate", "Bitrate", True),
    ("genre", "Genre", True),
    ("dateAdded", "Date Added", True),
]

_RIGHT = {"bpm", "time", "bitrate"}


_EDITABLE = {"title", "artist"}

# Fields the search bar looks in — the whole musically-meaningful row, not just
# the title. Matching an artist, genre, key, BPM or format is the whole point
# (searching "rezz" must find Rezz's sets even when her name isn't in the title).
_SEARCH_FIELDS = ("title", "artist", "genre", "key", "bpm", "format")


def row_matches(r: dict, terms: list[str]) -> bool:
    """True when every search term hits some searchable field of the row.
    Shared by the table filter and the Artists-tree filter so both browsers
    search identically."""
    hay = _haystack(r)
    return all(t in hay for t in terms)


def _haystack(r: dict) -> str:
    """One lowercase blob of a row's searchable fields, cached on the row so a
    fast typist doesn't rebuild it per keystroke (rebuilt whenever rows are set).
    BPM is also stored bare ('140' as well as '140.00') so a plain-number search
    hits."""
    hay = r.get("_hay")
    if hay is None:
        parts = [str(r.get(f) or "") for f in _SEARCH_FIELDS]
        bpm = str(r.get("bpm") or "")
        if "." in bpm:
            parts.append(bpm.split(".", 1)[0])  # "140.00" also searchable as "140"
        hay = " ".join(parts).lower()
        r["_hay"] = hay
    return hay

# Art status ("art" row key) → glyph + tooltip. Status set by the app from the
# art cache; sorting groups covers → avatars → managed defaults → blanks.
ART_COL = next(i for i, c in enumerate(COLUMNS) if c[0] == "art")
_ART_GLYPH = {"cover": "●", "avatar": "◐", "default": "◆",
              "miss": "○", "blocked": "⊘", "": ""}
_ART_TIP = {"cover": "Cover art found",
            "avatar": "Artist avatar (no track cover found — improve the title/artist "
                      "and re-scan, or set art manually)",
            "default": "Managed default photo (the real cover is missing or was "
                       "deliberately removed)",
            "miss": "No art found (improve the title/artist and re-scan, or set art manually)",
            "blocked": "Art removed by you — auto-fetch skips this track "
                       "(Choose/Fetch cover art overrides)",
            "": "Not fetched yet"}
_ART_RANK = {"cover": 0, "avatar": 1, "default": 2,
             "miss": 3, "blocked": 4, "": 5}

_CAMELOT = re.compile(r"^\s*(\d{1,2})\s*([ABab])\s*$")
_DURATION = re.compile(r"^\s*(?:(\d+):)?(\d+):(\d{2})\s*$")


def row_display_value(row: dict, key: str):
    """Shared display text for the table and Artists-tree track rows."""
    val = row.get(key, "")
    if key == "art":
        return _ART_GLYPH.get(val, "")
    if key == "artist" and not val:
        return "—"
    return val


def _sort_value(key: str, row: dict):
    """Type-aware sort key: numbers as numbers, M:SS/H:MM:SS as seconds,
    Camelot keys on the wheel (2A < 10A), art by usefulness; empties group."""
    v = row.get(key) or ""
    if key == "art":
        return (0, _ART_RANK.get(v, 4))
    s = str(v).strip()
    if not s:
        return (1, 0)  # empties group at the end of ascending
    if key in ("bpm", "bitrate"):
        try:
            return (0, float(s))
        except ValueError:
            return (1, 0)
    if key == "time":
        m = _DURATION.match(s)
        if m:
            hours = int(m.group(1) or 0)
            return (0, hours * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
        return (1, 0)
    if key == "key":
        m = _CAMELOT.match(s)
        if m:
            return (0, (int(m.group(1)), m.group(2).upper()))
        return (0, (99, s.lower()))  # non-Camelot keys after the wheel
    return (0, s.lower())


class TrackModel(QAbstractTableModel):
    cell_edited = Signal(str, str, str)  # (field, track_id, new_value) for overrides

    def __init__(self, rows: list[dict] | None = None):
        super().__init__()
        self._all_rows = list(rows or [])   # full playlist, in sacred rekordbox order
        self._rows = list(self._all_rows)   # what's shown (after filter + sort)
        self._filter = ""
        self._sort: tuple[str, bool] | None = None  # (row key, descending) — view-only

    def set_rows(self, rows: list[dict]) -> None:
        self._all_rows = list(rows)
        for r in self._all_rows:
            r.pop("_hay", None)  # force a fresh search index (title/artist may have changed)
        self._apply_filter()

    def set_filter(self, text: str) -> None:
        """Live-filter the visible rows. Multi-field (title/artist/genre/key/bpm/
        format) and multi-term: each whitespace-separated word must match SOME
        field, so 'rezz edc' finds a Rezz set titled '… @ EDC'. Case-insensitive
        substring per term."""
        self._filter = (text or "").strip().lower()
        self._apply_filter()

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        """Header-click sort — VIEW ONLY (export always uses rekordbox order).
        column -1 (indicator cleared) restores the original playlist order."""
        self._sort = None if column < 0 else (COLUMNS[column][0], order == Qt.DescendingOrder)
        self._apply_filter()

    def _apply_filter(self) -> None:
        self.beginResetModel()
        if self._filter:
            terms = self._filter.split()
            self._rows = [r for r in self._all_rows if row_matches(r, terms)]
        else:
            self._rows = list(self._all_rows)
        if self._sort:
            key, desc = self._sort
            self._rows.sort(key=lambda r: _sort_value(key, r), reverse=desc)
        self.endResetModel()

    def refresh_art(self, kind_for_id) -> None:
        """Update the art-status column in place as the background fill progresses
        (kind_for_id: track_id -> cover/avatar/default/miss/blocked/'').
        Cheap; no reset."""
        changed = False
        for r in self._all_rows:
            k = kind_for_id(r.get("id", ""))
            if r.get("art", "") != k:
                r["art"] = k
                changed = True
        if changed and self._rows:
            self.dataChanged.emit(self.index(0, ART_COL),
                                  self.index(len(self._rows) - 1, ART_COL),
                                  [Qt.DisplayRole, Qt.ToolTipRole])

    def rowCount(self, parent=None) -> int:
        return len(self._rows)

    def columnCount(self, parent=None) -> int:
        return len(COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        key = COLUMNS[index.column()][0]
        if role == Qt.DisplayRole:
            return row_display_value(self._rows[index.row()], key)
        if role == Qt.EditRole:  # editing starts from the real value, not "—"
            return self._rows[index.row()].get(key, "")
        if role == Qt.ToolTipRole and key == "art":
            return _ART_TIP.get(self._rows[index.row()].get(key, ""), "")
        if role == Qt.TextAlignmentRole:
            if key == "art":
                return int(Qt.AlignHCenter | Qt.AlignVCenter)
            if key in _RIGHT:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        key = COLUMNS[index.column()][0]
        if key not in _EDITABLE:
            return False
        v = str(value).strip()[:_EDIT_MAX.get(key, 255)].rstrip()
        row = self._rows[index.row()]
        row[key] = v
        self.cell_edited.emit(key, row.get("id", ""), v)
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        return True

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section][1]
        return None

    # --- drag support: rows carry their track ids ---
    def flags(self, index):
        f = super().flags(index)
        if index.isValid():
            f |= Qt.ItemIsDragEnabled
            if COLUMNS[index.column()][0] in _EDITABLE:  # double-click to edit
                f |= Qt.ItemIsEditable
        return f

    def mimeTypes(self):
        return ["text/plain"]

    def mimeData(self, indexes):
        ids = []
        seen = set()
        for idx in indexes:
            r = idx.row()
            if r not in seen:
                seen.add(r)
                ids.append(self._rows[r].get("id", ""))
        md = QMimeData()
        md.setText("trk\t" + ",".join(i for i in ids if i))
        return md
