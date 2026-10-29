"""Read the LIVE rekordbox database (read-only) into our Library model.

SAFETY: never touches the live master.db. It copies a stable DB + WAL pair to a
temp directory (never the process-local SHM file) and reads only that disposable
copy via pyrekordbox — the same posture as the XML import, sourced from a live
snapshot instead of a manual export. Never writes to rekordbox.

Normalizes into the same source-neutral `Library` model as the XML adapter, so
the UI, export, and overrides work unchanged. rekordbox content IDs == XML
TrackIDs, so artist/title overrides and SoundBox playlists carry over.
"""

from __future__ import annotations

import shutil
import tempfile
import logging
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from .library import Library, LibraryTag, Playlist, Track
from .rekordbox_analysis import resolve_dat_path

REKORDBOX_DB = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"

# rekordbox musical key name -> Camelot (matches the XML's Tonality the user knows).
_CAMELOT = {
    "C": "8B", "Db": "3B", "C#": "3B", "D": "10B", "Eb": "5B", "D#": "5B",
    "E": "12B", "F": "7B", "Gb": "2B", "F#": "2B", "G": "9B", "Ab": "4B",
    "G#": "4B", "A": "11B", "Bb": "6B", "A#": "6B", "B": "1B",
    "Cm": "5A", "Dbm": "12A", "C#m": "12A", "Dm": "7A", "Ebm": "2A", "D#m": "2A",
    "Em": "9A", "Fm": "4A", "Gbm": "11A", "F#m": "11A", "Gm": "6A", "Abm": "1A",
    "G#m": "1A", "Am": "8A", "Bbm": "3A", "A#m": "3A", "Bm": "10A",
}

_HOT_CUE_COLORS = (
    [255, 55, 111], [69, 172, 219], [125, 193, 61], [170, 114, 255],
    [48, 210, 110], [224, 100, 27], [48, 90, 255], [195, 175, 4],
)


def db_available(path: str | Path | None = None) -> bool:
    """True if a rekordbox database exists to read."""
    return Path(path or REKORDBOX_DB).is_file()


def _camelot(scale: str) -> str:
    return _CAMELOT.get((scale or "").strip(), scale or "")


def load_live(path: str | Path | None = None) -> Library:
    """Snapshot the live rekordbox DB and parse it (read-only). Raises on failure."""
    source = Path(path or REKORDBOX_DB).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"rekordbox database not found: {source}")
    from pyrekordbox import Rekordbox6Database  # imported lazily (heavy dep)

    tmp = Path(tempfile.mkdtemp(prefix="soundbox_rb_"))
    copy = tmp / "master.db"
    try:
        # READ-ONLY snapshot: copy stable database/WAL bytes; never open the
        # originals. The SHM file is process-local lock/index state and must not
        # be carried into the disposable reader.
        _copy_stable_snapshot(source, copy)

        db = Rekordbox6Database(path=str(copy))
        try:
            tags, memberships = _read_tags(db)
            identities = list(db.get_property())
            library_id = str(identities[0].DBID) if len(identities) == 1 else ""
            active_ids = _active_track_ids(db)
            playlists = _read_playlists(db, active_ids)
            # Preserve playlist scope before collecting edit identities. This
            # metadata never adds tracks or schedules preparation.
            referenced = {k for pl in playlists for k in pl.keys}
            tracks_by_id = _read_tracks(db, source, memberships, track_ids=referenced)
            try:
                _populate_cue_edit_state(db, tracks_by_id)
            except Exception as exc:  # optional write identity must not disable browsing
                logging.getLogger(__name__).warning('Cue edit identity unavailable: %s', exc)
                for track in tracks_by_id.values():
                    track.source_cue_revision = ''
                    track.source_cue_refs = []
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
        # Soundbox is a PLAYLIST exporter — keep only tracks that are actually in a
        # playlist. Drops orphan collection entries (added to rekordbox but in no
        # playlist, files usually long gone) so the count matches the real library.
        return Library(
            tracks_by_id=tracks_by_id,
            playlists=playlists,
            source=source,
            source_kind="rekordbox-db",
            source_tags=tags,
            source_library_id=library_id,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _snapshot_identity(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def _populate_cue_edit_state(db, tracks: dict[str, Track]) -> None:
    """Batch raw identity reads on the disposable snapshot, never live SQL.

    Raw driver rows keep SQL timestamps/opaque fields identical to the writer's
    revision representation. Display dictionaries and waveform hashes stay intact.
    """
    from .rekordbox_cues import CueSnapshot
    if not tracks:
        return
    conn = db.session.connection()
    def rows(table):
        return conn.exec_driver_sql('SELECT * FROM "' + table + '"').mappings()
    grouped: dict[str, list[dict]] = {}
    aggregates = {}
    for row in rows('djmdCue'):
        if row['ContentID'] in tracks:
            grouped.setdefault(row['ContentID'], []).append(dict(row))
    for row in rows('contentCue'):
        if row['ContentID'] in tracks:
            # An ambiguous source never receives a usable revision.
            key = row['ContentID']
            aggregates[key] = dict(row) if key not in aggregates else None
    for row in rows('djmdContent'):
        track = tracks.get(row['ID'])
        if track is None or row['FileType'] not in (1, 11, 12):
            continue
        if row['ID'] in aggregates and aggregates[row['ID']] is None:
            continue
        cues = grouped.get(row['ID'], [])
        snapshot = CueSnapshot(dict(row), tuple(cues), aggregates.get(row['ID']))
        track.source_cue_revision = snapshot.revision
        track.source_cue_refs = _cue_edit_refs(cues)


def _cue_edit_refs(cues):
    from .rekordbox_cues import _memory_order
    refs = []
    active = [r for r in cues if not r['rb_local_deleted']]
    for native in active:
        slot = _hot_cue_slot(native['Kind'])
        if slot is not None:
            refs.append(dict(kind='hot', slot=slot, id=native['ID'], uuid=native['UUID']))
    for slot, native in enumerate(sorted((r for r in active if r['Kind'] == 0),
                                          key=lambda r: (*_memory_order(r), r['ID']))):
        refs.append(dict(kind='memory', slot=slot, id=native['ID'], uuid=native['UUID']))
    return refs


def cue_snapshot_fields(snapshot) -> dict:
    """Small verified post-save update; no whole-library refresh or audio I/O."""
    grouped = _normalize_cue_rows(SimpleNamespace(**row) for row in snapshot.cues)
    return dict(source_cues=grouped.get(snapshot.track['ID'], []),
                source_cue_revision=snapshot.revision,
                source_cue_refs=_cue_edit_refs(snapshot.cues))


def _copy_stable_snapshot(source: Path, destination: Path, retries: int = 5) -> None:
    """Copy a consistent main DB + WAL pair without opening the live database.

    A checkpoint can rewrite ``master.db`` while the old implementation copied
    it and then copy a reset WAL, producing a mixed-time snapshot. Capture both
    file identities before and after each attempt and accept only a fully quiet
    interval. A busy rekordbox session therefore causes a bounded honest retry
    instead of a plausible stale/partial library.
    """
    wal = source.with_name(source.name + "-wal")
    destination_wal = destination.with_name(destination.name + "-wal")
    for _attempt in range(max(1, retries)):
        _assert_snapshot_journal_inactive(source)
        before = (_snapshot_identity(source), _snapshot_identity(wal))
        if before[0] is None:
            raise FileNotFoundError(f"rekordbox database not found: {source}")
        try:
            shutil.copy2(source, destination)
            if before[1] is not None:
                shutil.copy2(wal, destination_wal)
            elif destination_wal.exists():
                destination_wal.unlink()
        except FileNotFoundError:
            # A WAL checkpoint may remove/reset the WAL after ``before`` but
            # before copy2 opens it. That is an unstable interval, not a hard
            # missing-database failure; retry the complete pair.
            if destination_wal.exists():
                destination_wal.unlink()
            continue
        _assert_snapshot_journal_inactive(source)
        after = (_snapshot_identity(source), _snapshot_identity(wal))
        if before == after:
            return
    raise RuntimeError(
        "rekordbox database kept changing while SoundBox took its read-only "
        "snapshot; wait a moment and refresh again")


def _assert_snapshot_journal_inactive(source: Path) -> None:
    """Never copy potentially spilled, uncommitted pages without their journal.

    Owned transactions recover before refresh. Unknown recovery stays with
    rekordbox. Zero-header inactive journals require no rollback and are safe
    to leave in place; this reader never changes or deletes a journal.
    """
    journal = source.with_name(source.name + '-journal')
    try:
        with journal.open('rb') as handle:
            header = handle.read(8)
    except FileNotFoundError:
        return
    if any(header):
        raise RuntimeError('The rekordbox database needs recovery before it can be refreshed')


def _deleted(row) -> bool:
    # rekordbox soft-deletes (keeps the row for cloud/device sync) — hide those,
    # exactly as rekordbox itself does.
    return bool(getattr(row, "rb_local_deleted", 0))


def _read_tags(db) -> tuple[list[LibraryTag], dict[str, list[str]]]:
    """Flatten existing active My Tags; retain stable IDs and source order."""
    definitions = [row for row in db.get_my_tag() if not _deleted(row)]
    categories = {str(row.ID): row for row in definitions if row.Attribute == 1}
    leaves = [row for row in definitions if row.Attribute == 0]
    leaves.sort(key=lambda row: (
        _integer(getattr(categories.get(str(row.ParentID)), "Seq", 0)),
        _integer(row.Seq), str(row.ID)))
    tags = [LibraryTag(str(row.ID), row.Name or "",
                       getattr(categories.get(str(row.ParentID)), "Name", "") or "",
                       str(getattr(row, "UUID", "") or ""))
            for row in leaves]
    active = {tag.tag_id for tag in tags}
    memberships: dict[str, set[str]] = {}
    for link in db.get_my_tag_songs():
        if not _deleted(link) and str(link.MyTagID) in active:
            memberships.setdefault(str(link.ContentID), set()).add(str(link.MyTagID))
    return tags, {tid: [tag.tag_id for tag in tags if tag.tag_id in assigned]
                  for tid, assigned in memberships.items()}


def _active_track_ids(db) -> set[str]:
    from pyrekordbox.db6.tables import DjmdContent
    from sqlalchemy import func
    query = db.get_content()
    if hasattr(query, 'with_entities'):
        return {str(row[0]) for row in query.with_entities(DjmdContent.ID).filter(
            func.coalesce(DjmdContent.rb_local_deleted, 0) == 0)}
    return {str(row.ID) for row in query if not _deleted(row)}


def _content_rows(db, track_ids):
    from pyrekordbox.db6.tables import DjmdContent
    from sqlalchemy.orm import joinedload
    query = db.get_content()
    if not hasattr(query, 'options'):  # source-neutral reader fixtures
        yield from query
        return
    query = query.options(joinedload(DjmdContent.Artist),
                          joinedload(DjmdContent.Key), joinedload(DjmdContent.Genre),
                          joinedload(DjmdContent.Album),joinedload(DjmdContent.Label))
    if track_ids is None:
        yield from query
    else:
        # Bound parameters for libraries larger than SQLite's variable limit.
        ids = sorted(track_ids)
        for start in range(0, len(ids), 500):
            yield from query.filter(DjmdContent.ID.in_(ids[start:start + 500]))


def _read_tracks(db, source: Path | None = None,
                 tag_memberships: dict[str, list[str]] | None = None, *,
                 track_ids: set[str] | None = None) -> dict[str, Track]:
    cues_by_track = _read_cues(db)
    out: dict[str, Track] = {}
    share = ((source or REKORDBOX_DB).expanduser().parent / 'share').resolve()
    for c in _content_rows(db, track_ids):
        if _deleted(c) or (track_ids is not None and str(c.ID) not in track_ids):
            continue
        loc = c.FolderPath or ""
        uri = "file://localhost" + quote(loc) if loc else ""
        try:
            bpm = f"{(c.BPM or 0) / 100:.2f}" if c.BPM else ""
        except (TypeError, ValueError):
            bpm = ""
        # created_at = when this row was written = when the track was added to
        # rekordbox (real time-of-day precision). DateCreated is a stale tag date
        # that can predate the add by weeks, so it's display-only; created_at
        # drives the "recently added" sort.
        try:
            added_ts = c.created_at.timestamp() if c.created_at else 0.0
        except (AttributeError, ValueError, OSError):
            added_ts = 0.0
        out[str(c.ID)] = Track(
            track_id=str(c.ID),
            name=c.Title or "",
            artist=(c.Artist.Name if c.Artist else ""),
            location_uri=uri,
            bpm=bpm,
            key=_camelot(c.Key.ScaleName if c.Key else ""),
            total_time=int(c.Length or 0),
            date_added=str(c.DateCreated or "")[:10],
            added_ts=added_ts,
            genre=(c.Genre.Name if c.Genre else ""),
            size=int(c.FileSize or 0),
            bitrate=str(c.BitRate or ""),
            rating=str(c.Rating or ""),
            year=str(c.ReleaseYear or ""),
            info=dict(album=c.Album.Name if getattr(c,'Album',None) else '',
                label=c.Label.Name if getattr(c,'Label',None) else '',
                comments=str(getattr(c,'Commnt','') or ''),
                releaseDate=str(getattr(c,'ReleaseDate','') or ''),
                playCount=str(getattr(c,'DJPlayCount',0) or 0),
                sampleRate=str(getattr(c,'SampleRate','') or ''),
                bitDepth=str(getattr(c,'BitDepth','') or ''),
                color=str(getattr(c,'ColorID',0) or 0),
                dateCreated=str(getattr(c,'DateCreated','') or '')[:10],
                hotCueAutoLoad='On' if str(getattr(c,'HotCueAutoLoad','')).lower()=='on' else 'Off',
                publishInfo='On' if str(getattr(c,'DeliveryControl','')).lower()=='on' else 'Off',
                message=str(getattr(c,'DeliveryComment','') or ''),
                lyricist=str(getattr(c,'Lyricist','') or '')),
            source_analysis_path=str(resolve_dat_path(
                source or REKORDBOX_DB, getattr(c, "AnalysisDataPath", "") or "",
                resolved_share=share) or ""),
            source_cues=cues_by_track.get(str(c.ID), []),
            source_uuid=str(getattr(c, "UUID", "") or ""),
            source_tag_ids=(list(tag_memberships.get(str(c.ID), []))
                            if tag_memberships is not None else None),
        )
    return out


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cue_color(row, slot: int) -> list[int]:
    """Return stable display RGB without pretending undocumented DB indices
    are RGB values.

    Rekordbox stores a palette index for most hot cues, not an RGB triplet.
    Until that palette is verified independently, SoundBox uses its established
    deterministic slot colors. Explicit RGB-like row fields are accepted when
    a future pyrekordbox schema exposes them.
    """
    explicit = [
        _integer(getattr(row, name, 0))
        for name in ("ColorRed", "ColorGreen", "ColorBlue")
    ]
    if any(explicit):
        return [max(0, min(255, value)) for value in explicit]
    if slot >= 0:
        return list(_HOT_CUE_COLORS[slot % len(_HOT_CUE_COLORS)])
    return [126, 132, 148]


def _hot_cue_slot(kind_number: int) -> int | None:
    """Translate rekordbox ``djmdCue.Kind`` to its canonical A–P slot.

    The live database does *not* use a contiguous one-based sequence. A/B/C
    are 1/2/3, value 4 is reserved by rekordbox, and D–P are 5–17. Treating the
    field as ``Kind - 1`` shifts every cue from D onward and makes D impossible
    to display. Unknown/reserved values are rejected instead of being assigned
    a plausible-but-wrong letter.
    """
    if 1 <= kind_number <= 3:
        return kind_number - 1
    if 5 <= kind_number <= 17:
        return kind_number - 2
    return None


def _read_cues(db) -> dict[str, list[dict]]:
    """Read every active cue once and group it by rekordbox ContentID."""
    try:
        rows = db.get_cue()
    except (AttributeError, TypeError):
        return {}
    return _normalize_cue_rows(rows)


def _normalize_cue_rows(rows) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    memory: dict[str, list[dict]] = {}
    for row in rows:
        if _deleted(row):
            continue
        content_id = str(getattr(row, "ContentID", "") or "")
        if not content_id:
            continue
        kind_number = _integer(getattr(row, "Kind", 0))
        if kind_number == 0:
            kind = "memory"
            slot = -1
        else:
            kind = "hot"
            mapped_slot = _hot_cue_slot(kind_number)
            if mapped_slot is None:
                continue
            slot = mapped_slot
        position = max(0, _integer(getattr(row, "InMsec", 0)))
        end = _integer(getattr(row, "OutMsec", -1), -1)
        is_loop = end > position
        cue = {
            "kind": kind,
            "slot": slot,
            "position_ms": position,
            "end_ms": end if is_loop else -1,
            "type": "loop" if is_loop else "single",
            "label": str(getattr(row, "Comment", "") or "").strip(),
            "color": _cue_color(row, slot),
        }
        if kind == "memory":
            memory.setdefault(content_id, []).append(cue)
        else:
            grouped.setdefault(content_id, []).append(cue)
    for content_id, rows in memory.items():
        rows.sort(key=lambda cue: (
            cue["position_ms"], cue["end_ms"], cue["label"]))
        for slot, cue in enumerate(rows):
            cue["slot"] = slot
        grouped.setdefault(content_id, []).extend(rows)
    for rows in grouped.values():
        rows.sort(key=lambda cue: (
            0 if cue["kind"] == "hot" else 1,
            cue["slot"], cue["position_ms"]))
    return grouped


def _smart_playlist_contents(db, playlist):
    """Return smart-playlist rows without resurrecting removed My Tags.

    Rekordbox keeps removed ``djmdSongMyTag`` associations as soft-deleted
    history.  pyrekordbox 0.4.4's My Tag smart-list predicate does not filter
    that flag, so a removed tag continues to satisfy ``contains`` (and can
    incorrectly defeat ``not contains``).  Rebuild only playlists containing a
    My Tag condition, retaining pyrekordbox's predicates for every other
    condition and requiring the association itself to be active.
    """
    from pyrekordbox.db6.smartlist import (
        LogicalOperator, Operator, Property, SmartList,
    )
    from pyrekordbox.db6.tables import DjmdContent, DjmdMyTag, DjmdSongMyTag
    from sqlalchemy import and_, exists, func, not_, or_

    source = getattr(playlist, "SmartList", "") or ""
    if not source:  # compatibility with older/mocked pyrekordbox rows
        return db.get_playlist_contents(playlist)
    smartlist = SmartList()
    smartlist.parse(source)
    if smartlist.logical_operator not in (
            LogicalOperator.ALL, LogicalOperator.ANY):
        raise ValueError(
            f"Unknown smart-playlist logical operator: {smartlist.logical_operator}")
    if not smartlist.conditions:
        raise ValueError("Smart playlist has no conditions")
    unsupported = [
        cond.property for cond in smartlist.conditions
        if cond.property in (Property.GROUPING, Property.MIX_NAME)
    ]
    if unsupported:
        # pyrekordbox maps Grouping to ColorID and has no Mix Name mapping. A
        # guessed query is worse than an explicit refresh failure because it
        # can silently publish the wrong playlist to a device.
        raise ValueError(
            "Unverified rekordbox smart-playlist field(s): "
            + ", ".join(unsupported))
    if not any(cond.property == Property.MYTAG for cond in smartlist.conditions):
        return db.get_playlist_contents(playlist)

    clauses = []
    for cond in smartlist.conditions:
        if cond.property != Property.MYTAG:
            single = SmartList(logical_operator=LogicalOperator.ALL)
            single.conditions = [cond]
            clauses.append(single.filter_clause())
            continue

        tag_id = int(cond.value_left)
        if tag_id < 0:  # rekordbox stores unsigned IDs as signed XML values
            tag_id += 2**32
        active_tag = exists().where(
            DjmdMyTag.ID == str(tag_id),
            func.coalesce(DjmdMyTag.rb_local_deleted, 0) == 0,
        )
        active_link = and_(
            active_tag,
            exists().where(
                DjmdSongMyTag.ContentID == DjmdContent.ID,
                DjmdSongMyTag.MyTagID == str(tag_id),
                func.coalesce(DjmdSongMyTag.rb_local_deleted, 0) == 0,
            ),
        )
        if cond.operator == Operator.CONTAINS:
            clauses.append(active_link)
        elif cond.operator == Operator.NOT_CONTAINS:
            clauses.append(not_(active_link))
        else:
            raise ValueError(f"Unsupported My Tag operator: {cond.operator}")

    combine = and_ if smartlist.logical_operator == LogicalOperator.ALL else or_
    return db.query(DjmdContent).filter(and_(
        func.coalesce(DjmdContent.rb_local_deleted, 0) == 0,
        combine(*clauses),
    ))


def _read_playlists(
        db, active_track_ids: set[str] | None = None) -> list[Playlist]:
    from pyrekordbox.db6.tables import DjmdSongPlaylist

    # Group nodes by parent and sort by Seq so we match rekordbox's exact order.
    children: dict[str, list] = {}
    active_nodes = {}
    for p in db.get_playlist():
        if _deleted(p):  # skip rekordbox-deleted playlists/folders
            continue
        if p.Attribute not in (0, 1, 4, -128):
            raise ValueError(
                f"Unknown active rekordbox playlist type {p.Attribute} for {p.Name!r}")
        active_nodes[str(p.ID)] = p
        if p.Attribute != -128:  # cloud is not part of SoundBox's visible tree
            children.setdefault(str(p.ParentID), []).append(p)
    for kids in children.values():
        sequences = [p.Seq for p in kids]
        if any(sequence is None for sequence in sequences):
            raise ValueError("Active rekordbox playlist node has no sequence")
        if len(sequences) != len(set(sequences)):
            raise ValueError("Active rekordbox sibling nodes have duplicate sequences")
        kids.sort(key=lambda n: (n.Seq if n.Seq is not None else 0))

    for p in active_nodes.values():
        if p.Attribute == -128 or str(p.ParentID) == "root":
            continue
        parent = active_nodes.get(str(p.ParentID))
        if parent is None or parent.Attribute != 1:
            raise ValueError(
                f"Active rekordbox playlist {p.Name!r} has no active parent folder")

    # SACRED ORDER: a normal playlist's order is DjmdSongPlaylist.TrackNo — NOT
    # the order get_playlist_contents returns (which is ContentID-based). Only
    # rows whose playlist endpoint is an active normal node participate. Deleted
    # playlists keep historical active membership rows; validating those would
    # let invisible history break an otherwise valid refresh.
    normal_ids = {
        node_id for node_id, node in active_nodes.items() if node.Attribute == 0
    }
    tracks_by_pl: dict[str, list] = {}
    for sp in db.query(DjmdSongPlaylist).all():
        playlist_id = str(sp.PlaylistID)
        if _deleted(sp) or playlist_id not in normal_ids:
            continue
        if (active_track_ids is not None
                and str(sp.ContentID) not in active_track_ids):
            continue
        tracks_by_pl.setdefault(playlist_id, []).append(sp)
    for rows in tracks_by_pl.values():
        track_numbers = [r.TrackNo for r in rows]
        if any(number is None for number in track_numbers):
            raise ValueError("Active rekordbox playlist membership has no TrackNo")
        if len(track_numbers) != len(set(track_numbers)):
            raise ValueError("Active rekordbox playlist has duplicate TrackNo values")
        rows.sort(key=lambda r: (r.TrackNo if r.TrackNo is not None else 0))

    playlists: list[Playlist] = []
    visited: set[str] = set()

    def walk(parent_id: str, path: list[str]):
        for p in children.get(parent_id, []):
            if p.Attribute == 1:  # folder → recurse, extending the path
                walk(str(p.ID), path + [p.Name])
            elif p.Attribute == 0:  # normal playlist → sacred manual order (TrackNo)
                keys = [str(sp.ContentID) for sp in tracks_by_pl.get(str(p.ID), [])]
                playlists.append(Playlist(
                    name=p.Name, folder_path=list(path), keys=keys, pid=str(p.ID)))
                visited.add(str(p.ID))
            elif p.Attribute == 4:  # SMART/dynamic playlist → contents are computed
                try:                # from the tag/filter criteria, not a membership table
                    keys = [str(s.ID) for s in _smart_playlist_contents(db, p)]
                except Exception as exc:  # noqa: BLE001 - add playlist identity
                    raise RuntimeError(
                        f"Could not evaluate rekordbox smart playlist "
                        f"{' / '.join(path + [p.Name])!r}: {exc}") from exc
                if active_track_ids is not None:
                    keys = [key for key in keys if key in active_track_ids]
                playlists.append(Playlist(
                    name=p.Name, folder_path=list(path), keys=keys, pid=str(p.ID)))
                visited.add(str(p.ID))
            # else: skip special nodes (e.g. Attribute -128 = cloud)

    walk("root", [])
    expected = {
        node_id for node_id, node in active_nodes.items()
        if node.Attribute in (0, 4)
    }
    if visited != expected:
        missing = sorted(expected - visited)
        raise ValueError(
            f"Active rekordbox playlists are unreachable from root: {missing}")
    pids = [playlist.pid for playlist in playlists]
    if len(pids) != len(set(pids)):
        raise ValueError("Active rekordbox playlists contain duplicate stable IDs")
    display_paths = [playlist.display_path for playlist in playlists]
    if len(display_paths) != len(set(display_paths)):
        raise ValueError(
            "Active rekordbox playlists contain duplicate display paths; "
            "SoundBox cannot safely select them by name")
    return playlists
