"""One desktop owner for tag/cue saves through finished delivery and join."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QThread, Signal, QEventLoop

from soundbox import rekordbox_db
from soundbox.rekordbox_cue_store import PilotCueStore, load_cue_authority
from soundbox.rekordbox_cues import CueEdit
from soundbox.rekordbox_grid_store import PilotGridStore, load_grid_authority
from soundbox.rekordbox_write import WriteProofError, _regular
from soundbox.rekordbox_tags import (
    LiveTagStore, SelectedTagTrack, TagWriteRequest, load_authority,
)
from .task_owner import TaskOwner
from .cue_session import display, intent_for, native_key, overlay


class _TagTask(QThread):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.callback()
        except Exception as exc:  # noqa: BLE001 - report on the GUI thread
            self.error = str(exc)


class TagEditController(QObject):
    changed = Signal()
    idle = Signal()
    saved = Signal(object)
    failed = Signal(str)
    cue_saved = Signal(object, str)
    cue_changed = Signal(str)

    def __init__(self, data_root: Path, parent=None, *, store_factory=None, loader=None,
                 cue_store_factory=None):
        super().__init__(parent)
        self.data_root = data_root
        self.owner = TaskOwner(self)
        self.lib = None
        self.verified = False
        self.busy = False
        self._pending = None
        self._store = None
        self._store_identity = None
        self._store_factory = store_factory or self._make_store
        self._requires_authority = store_factory is None
        self._authority = None
        self.checking = False
        self._ready = not self._requires_authority
        self._probe_error = ""
        self._loader = loader or rekordbox_db.load_live
        self._cue_store_factory = cue_store_factory or self._make_cue_store
        self._requires_cue_authority = cue_store_factory is None
        self._cue_authority = None
        self._cue_store = None
        self._cue_store_identity = None
        self._busy_kind = ''
        self.last_operation = 'tags'
        self._pending_cue = None
        self._cue_queue = []
        self._cue_aliases = {}
        self._closing = False
        self.grid_waiting = False
        self.grid_update_status = ''
        self.playlist_update_status = ''

    @property
    def status_text(self):
        if not self.busy:
            return ''
        return self.update_status_text or 'Checking saved grid…'

    @property
    def update_status_text(self):
        """Accepted edit intents only; a worker owning the lane is not a save."""
        if self.busy and self._busy_kind == 'playlists':
            return 'Updating rekordbox playlists…'
        if self.busy and self._busy_kind == 'tags':
            return 'Updating My Tags…'
        if self._cue_queue and self.busy and self._busy_kind == 'cues':
            page = self._cue_queue[0].page
            return 'Updating hot cue…' if page == 'hot' else 'Updating memory cue…'
        if self.grid_update_status:
            return self.grid_update_status
        if self._cue_queue:
            page = self._cue_queue[0].page
            return 'Updating hot cue…' if page == 'hot' else 'Updating memory cue…'
        return self.playlist_update_status

    @property
    def owned(self):
        return self.busy or self.checking or bool(self._cue_queue) or self.grid_waiting

    def configure(self, lib, verified: bool):
        identity = (str(lib.source), lib.source_library_id) if lib else None
        if identity != self._store_identity:
            self._store = None
            self._store_identity = identity
            self._ready = not self._requires_authority
            self._probe_error = ""
            self._cue_store = None
            self._cue_store_identity = None
        self.lib = lib
        self.verified = verified
        if self._requires_authority:
            self._authority = (load_authority(self.data_root / 'rekordbox-tag-authority.json',
                               Path(lib.source), lib.source_library_id)
                               if lib and verified and lib.source_kind == 'rekordbox-db' else None)
        if self._requires_cue_authority:
            self._cue_authority = (load_cue_authority(self.data_root / 'rekordbox-cue-authority.json',
                                   Path(lib.source), lib.source_library_id)
                                   if lib and verified and lib.source_kind == 'rekordbox-db' else None)
        self.changed.emit()

    def _write_root(self, lib):
        key = hashlib.sha256((str(lib.source) + '\0' + lib.source_library_id).encode()).hexdigest()[:24]
        return self.data_root / 'rekordbox_recovery' / 'tag-writes' / key

    def _make_store(self, lib):
        authority = load_authority(self.data_root / 'rekordbox-tag-authority.json',
                                   Path(lib.source), lib.source_library_id)
        if authority is None:
            raise RuntimeError('Tag editing is not enabled for this rekordbox library')
        return LiveTagStore(authority, self._write_root(lib))

    def _make_cue_store(self, lib, track_id):
        authority = load_cue_authority(self.data_root / 'rekordbox-cue-authority.json',
                                       Path(lib.source), lib.source_library_id)
        if authority is None:
            raise RuntimeError('Cue editing is not enabled for this rekordbox library')
        return PilotCueStore(authority.bind(track_id), self._write_root(lib))

    def _recover_pending(self, lib):
        root = self._write_root(lib)
        journal = root / 'transaction.json'
        if not journal.exists() and not journal.is_symlink():
            payloads = root / 'grid-transaction'
            if not payloads.exists() and not payloads.is_symlink():
                return
            # A crash can leave only verified private retirement after the
            # common journal was removed. The grid store owns that cleanup.
            receipt = root / 'grid-completed.json'
            _regular(receipt)
            record = json.loads(receipt.read_bytes())
            authority = load_grid_authority(self.data_root / 'rekordbox-grid-authority.json',
                                            Path(lib.source), lib.source_library_id)
            if authority is None or record.get('format') != 'soundbox-native-grid-v1':
                raise RuntimeError('Unrecognized grid retirement; evidence preserved')
            PilotGridStore(authority.bind(record['track_id'], record['request']['track_uuid']),
                           root).recover()
            return
        _regular(journal)
        record = json.loads(journal.read_bytes())
        if record.get('format') == 'soundbox-native-cues-v1':
            # The accepted library permission may bind the previous operation's
            # target even if a different track is now loaded. Its store still
            # verifies the complete journal/baseline before SQLite recovery.
            self._cue_store_factory(lib, record['track_id']).snapshot()
        elif record.get('format') == 'soundbox-native-mytag-v1':
            self._store_factory(lib).check_ready()
        elif record.get('format') == 'soundbox-native-playlists-v1':
            from soundbox.rekordbox_playlist_store import PlaylistStore, load_authority as load_playlist_authority
            authority = load_playlist_authority(self.data_root/'rekordbox-playlist-authority.json',
                                                Path(lib.source),lib.source_library_id)
            if authority is None:
                raise RuntimeError('Playlist recovery has no accepted library permission; evidence preserved')
            PlaylistStore(authority,root).recover()
        elif record.get('format') == 'soundbox-native-grid-v1':
            authority = load_grid_authority(self.data_root / 'rekordbox-grid-authority.json',
                                            Path(lib.source), lib.source_library_id)
            if authority is None:
                raise RuntimeError('Grid recovery has no accepted library permission; evidence preserved')
            PilotGridStore(authority.bind(record['track_id'], record['request']['track_uuid']),
                           self._write_root(lib)).recover()
        else:
            raise RuntimeError('Unrecognized rekordbox recovery state; evidence preserved')

    def recover_before_read(self, source):
        """Use an already-provisioned authority, never cache/source selection.

        Startup can have no verified library yet. A previous authorized
        transaction still needs exact recovery before copying dirty DB pages.
        This runs in the serialized source-refresh worker, not the GUI thread.
        """
        from soundbox.rekordbox_playlist_store import load_authority as load_playlist_authority
        source = Path(source)
        for filename, loader in (('rekordbox-cue-authority.json', load_cue_authority),
                                 ('rekordbox-tag-authority.json', load_authority),
                                 ('rekordbox-grid-authority.json', load_grid_authority),
                                 ('rekordbox-playlist-authority.json', load_playlist_authority)):
            path = self.data_root / filename
            if not path.exists() and not path.is_symlink():
                continue
            try:
                _regular(path)
                data = json.loads(path.read_bytes())
                authority = loader(path, source, data.get('library_id', ''))
            except (OSError, ValueError, TypeError, AttributeError, WriteProofError):
                continue  # invalid permission disables writing, not ordinary browsing
            if authority is not None:
                self._recover_pending(SimpleNamespace(source=source, source_library_id=authority.library_id))
                return

    def reason(self, selected_ids) -> str:
        if self.owner.stopping or self._closing:
            return 'Finishing tag work…'
        if self.busy:
            return self.status_text
        if self.checking:
            return 'Checking tag editing…'
        lib = self.lib
        if not lib or lib.source_kind != 'rekordbox-db' or not self.verified or not lib.source_library_id:
            return 'Refresh a rekordbox database to edit its tags'
        if not selected_ids:
            return 'Select tracks to edit tags'
        if self._requires_authority and self._authority is None:
            return 'Tag editing is not enabled for this rekordbox library'
        if any((t := lib.tracks_by_id.get(tid)) is None or t.imported or not t.source_uuid
               or t.source_tag_ids is None for tid in selected_ids):
            return 'Select only tracks from this rekordbox library'
        if not self._ready:
            return self._probe_error or 'Open My Tags to check editing availability'
        return ''

    def probe(self):
        if (self.owner.stopping or self._closing or self.busy or self.checking or self.lib is None
                or not self.verified or self.lib.source_kind != 'rekordbox-db'
                or (self._requires_authority and self._authority is None)):
            return
        self.checking = True
        self._ready = False
        self._probe_error = ""
        lib, identity = self.lib, self._store_identity

        def check():
            self._recover_pending(lib)
            store = self._store or self._store_factory(lib)
            store.check_ready()
            return store

        def finished(worker):
            self.checking = False
            if self.owner.stopping:
                return
            if identity == self._store_identity:
                self._probe_error = worker.error
                self._ready = not worker.error
                if self._ready:
                    self._store = worker.result
            self.changed.emit()
            self.idle.emit()

        self.changed.emit()
        worker = _TagTask(check)
        self.owner.adopt(worker, finished)
        worker.start()

    def selected_tags(self, ids):
        lib = self.lib
        if not lib or any(tid not in lib.tracks_by_id
                          or lib.tracks_by_id[tid].source_tag_ids is None for tid in ids):
            return []
        result = []
        pending = self._pending
        for tid in ids:
            tags = set(lib.tracks_by_id[tid].source_tag_ids or [])
            if pending and any(target.track_id == tid for target in pending.tracks):
                if pending.present:
                    tags.add(pending.tag_id)
                else:
                    tags.discard(pending.tag_id)
            result.append(tags)
        return result

    def request(self, selected_ids, tag_id: str, present: bool) -> bool:
        ids = tuple(selected_ids)
        if self.reason(ids):
            return False
        lib = self.lib
        tag = next((tag for tag in lib.source_tags if tag.tag_id == tag_id), None)
        if tag is None or not tag.source_uuid:
            return False
        request = TagWriteRequest(lib.source_library_id, tuple(
            SelectedTagTrack(tid, lib.tracks_by_id[tid].source_uuid,
                             tag_id in lib.tracks_by_id[tid].source_tag_ids)
            for tid in ids), tag_id, tag.source_uuid, bool(present), tag.name)
        self.busy = True
        self._busy_kind = 'tags'
        self._pending = request
        self.changed.emit()

        def save():
            self._recover_pending(lib)
            if self._store is None:
                self._store = self._store_factory(lib)
            self._store.apply(request)
            return self._loader(Path(lib.source))

        worker = _TagTask(save)
        self.owner.adopt(worker, self._finish)
        worker.start()
        return True

    def cue_reason(self, track_id) -> str:
        if self.owner.stopping or self._closing:
            return 'Finishing library edits…'
        if (self.busy and self._busy_kind not in ('cues', 'grid')) or self.checking:
            return self.status_text or 'Checking library editing…'
        lib = self.lib
        if not lib or not self.verified or lib.source_kind != 'rekordbox-db':
            return 'Refresh a rekordbox database to edit cues'
        if self._requires_cue_authority and self._cue_authority is None:
            return 'Cue editing is not enabled for this rekordbox library'
        track = lib.tracks_by_id.get(track_id)
        if (not track or track.imported or not track.source_uuid or track.source_cues is None
                or not track.source_cue_revision):
            return 'Cue editing is unavailable for this track; refresh the library if needed'
        return ''

    def request_cue(self, track_id, page, slot, operation, position_ms=None):
        if self.cue_reason(track_id):
            return False
        track = self.lib.tracks_by_id[track_id]
        intent = intent_for(track, overlay(track, self._cue_queue, self._cue_aliases),
                            page, slot, operation, position_ms)
        if intent is None:
            return False
        self._cue_queue.append(intent)
        # Publish only the small deck overlay. The authoritative Library/cache
        # stays unchanged until independent source read-back succeeds.
        self.cue_changed.emit(track_id)
        if not self.busy:
            self._start_next_cue()
        return True

    def session_cues(self, track_id):
        track = self.lib.tracks_by_id.get(track_id) if self.lib else None
        if track is None or track.source_cues is None:
            return None
        if not any(i.track_id == track_id for i in self._cue_queue):
            return track.source_cues
        return display(overlay(track, self._cue_queue, self._cue_aliases))

    def run_grid_job(self, callback, finished) -> bool:
        return self._run_source_job('grid',callback,finished)

    def run_playlist_job(self, callback, finished) -> bool:
        if self._cue_queue or self.grid_waiting:
            return False
        return self._run_source_job('playlists',callback,finished)

    def _run_source_job(self, kind, callback, finished) -> bool:
        """Grid work uses this same worker lane and finished/join ownership."""
        if self.busy or self.checking or self.owner.stopping or self._closing:
            return False
        self.busy = True
        self._busy_kind = kind
        self.changed.emit()

        def completed(worker):
            self.busy = False
            self._busy_kind = ''
            finished(worker)
            if self._cue_queue and not self.busy:
                self._start_next_cue()
            self.changed.emit()
            if not self.busy:
                self.idle.emit()

        worker = _TagTask(callback)
        self.owner.adopt(worker, completed)
        worker.start()
        return True

    def _start_next_cue(self):
        intent = self._cue_queue[0]
        lib, track_id = self.lib, intent.track_id
        track = lib.tracks_by_id[track_id]
        identity = (str(lib.source), lib.source_library_id, track_id)
        key = self._cue_aliases.get(intent.key, intent.key)
        ref = next((r for r in track.source_cue_refs if native_key(r) == key), None)
        slot = intent.slot
        if intent.operation == 'delete' and ref is not None:
            slot = ref['slot']
        elif intent.page == 'memory' and intent.operation == 'add':
            slot = sum(r['kind'] == 'memory' for r in track.source_cue_refs)
        request = CueEdit(track_id, intent.track_uuid, track.source_cue_revision,
                          intent.page, slot, intent.operation, intent.position_ms,
                          ref['id'] if ref else '', ref['uuid'] if ref else '')
        self.busy = True
        self._busy_kind = 'cues'
        self._pending_cue = (identity, request)
        self.changed.emit()

        def save():
            if track.source_uuid != intent.track_uuid or str(track.path) != intent.path:
                raise RuntimeError('Cue source changed before saving; refresh the library')
            if intent.operation == 'delete' and ref is None:
                raise RuntimeError('Cue identity changed before deletion; refresh the library')
            self._recover_pending(lib)
            if self._cue_store_identity != identity:
                self._cue_store = self._cue_store_factory(lib, track_id)
                self._cue_store_identity = identity
            self._cue_store.apply(request)
            snapshot = self._cue_store.snapshot()
            if (snapshot.track['ID'] != request.track_id or snapshot.track['UUID'] != request.track_uuid
                    or snapshot.track['FolderPath'] != str(track.path)):
                raise RuntimeError('Cue source changed during read-back; refresh the library')
            return rekordbox_db.cue_snapshot_fields(snapshot)

        worker = _TagTask(save)
        self.owner.adopt(worker, self._finish_cue)
        worker.start()

    def _finish_cue(self, worker):
        identity, request = self._pending_cue
        intent = self._cue_queue[0]
        self.last_operation = 'cues'
        lib = self.lib
        track = lib.tracks_by_id.get(request.track_id) if lib else None
        if (not worker.error and (not lib
                or (str(lib.source), lib.source_library_id, request.track_id) != identity
                or not track or track.source_uuid != request.track_uuid
                or track.source_cue_revision != request.revision)):
            worker.error = 'Cue source changed while saving; refresh the library'
        if not worker.error:
            edited = replace(track, **worker.result)
            if intent.operation == 'add':
                old_keys = {native_key(r) for r in track.source_cue_refs}
                added = [r for r in edited.source_cue_refs if native_key(r) not in old_keys]
                if len(added) == 1:
                    self._cue_aliases[intent.key] = native_key(added[0])
                else:
                    worker.error = 'Cue read-back did not identify the new cue; refresh the library'
            # Even a bad post-save identity must reconcile to the verified DB,
            # never replay this potentially committed mutation blindly.
            self.lib = replace(lib, tracks_by_id={**lib.tracks_by_id, request.track_id: edited})
        changed_tracks = {i.track_id for i in self._cue_queue}
        self._cue_queue.pop(0)
        self._pending_cue = None
        self.busy = False
        self._busy_kind = ''
        if worker.error:
            self._cue_queue.clear()
            self._ready = False
            self._probe_error = worker.error
        if self._cue_queue:
            self._start_next_cue()
        else:
            self._cue_aliases.clear()
        for track_id in changed_tracks:
            self.cue_changed.emit(track_id)
        if not worker.error:
            self.cue_saved.emit(self.lib, request.track_id)
        if not self._cue_queue:
            self.changed.emit()
            if worker.error:
                self.failed.emit(worker.error)
            self.idle.emit()

    def _finish(self, worker):
        self.last_operation = self._busy_kind or 'tags'
        self.busy = False
        self._busy_kind = ''
        self._pending = None
        if not self.owner.stopping:
            if worker.error:
                self._ready = False
                self._probe_error = worker.error
                self.failed.emit(worker.error)
            else:
                self.saved.emit(worker.result)
            self.changed.emit()
        self._pending_cue = None
        self.idle.emit()

    def shutdown(self):
        # Close admission, but finish every accepted session edit in order.
        # Keep Qt dispatching so retirement can start the next queued write.
        self._closing = True
        loop = QEventLoop()
        self.idle.connect(loop.quit)
        try:
            while self.busy or self.checking:
                loop.exec(QEventLoop.ExcludeUserInputEvents)
        finally:
            self.idle.disconnect(loop.quit)
        self.owner.drain()
