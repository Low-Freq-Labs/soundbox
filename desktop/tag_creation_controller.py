"""Durable tag-definition intents on the existing joined source-worker lane."""
from copy import deepcopy
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from soundbox import rekordbox_tag_definitions as definitions
from soundbox.rekordbox_write import WriteProofError, assert_rekordbox_closed

QUEUE = 'pending_tag_definitions'


class TagDefinitionController(QObject):
    def __init__(self, window, *, store_factory=None, deletion_store_factory=None, loader=None):
        super().__init__(window)
        self.window = window
        self.tags = window.tag_controller
        self.factory = store_factory
        self.deletion_factory = deletion_store_factory
        self.loader = loader or self.tags._loader
        self.closing = False
        self.not_before = 0.0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)
        self.tags.idle.connect(self.schedule)
        self.tags.changed.connect(self.schedule)

    @property
    def pending(self):
        queue = self.window.settings.get(QUEUE, [])
        return queue if isinstance(queue, list) else []

    def queue_error(self):
        queue = self.window.settings.get(QUEUE, [])
        try:
            if not isinstance(queue, list) or len(queue) > 256:
                raise ValueError('Invalid queue')
            seen, tag_ids, tag_uuids = set(), set(), set()
            for job in queue:
                if (not isinstance(job, dict) or set(job) != {'source', 'library_id', 'request', 'error'}
                        or not isinstance(job['source'], str) or not Path(job['source']).is_absolute()
                        or not isinstance(job['library_id'], str) or not job['library_id']
                        or not isinstance(job['error'], str)):
                    raise ValueError('Invalid intent')
                self._validate(job['request'])
                if (job['request']['library_id'] != job['library_id'] or job['request']['operation_id'] in seen
                        or job['request']['tag_id'] in tag_ids or job['request']['tag_uuid'] in tag_uuids):
                    raise ValueError('Invalid intent identity')
                seen.add(job['request']['operation_id'])
                tag_ids.add(job['request']['tag_id'])
                tag_uuids.add(job['request']['tag_uuid'])
        except (KeyError, TypeError, ValueError, WriteProofError):
            return 'Saved tag changes need attention; intentions are preserved'
        return ''

    def reason(self):
        w = self.window
        if self.queue_error():
            return self.queue_error()
        if self.closing or w._shutting_down or w._state_maintenance:
            return 'Library operation in progress'
        lib = w.lib
        if (not lib or lib.source_kind != 'rekordbox-db' or not self.tags.verified
                or not lib.source_library_id or not lib.source_tag_categories):
            return 'Refresh the rekordbox library before creating tags'
        if not self.factory and definitions.load_authority(
                self.tags.data_root / 'rekordbox-tag-definition-authority.json',
                Path(lib.source), lib.source_library_id) is None:
            return 'Creating tags is not enabled for this library yet'
        return ''

    @staticmethod
    def _deleting(request):
        return isinstance(request, dict) and request.get('action') == 'delete'

    @classmethod
    def _validate(cls, request):
        return (definitions.validate_deletion_request(request) if cls._deleting(request)
                else definitions.validate_request(request))

    def deletion_reason(self):
        w = self.window
        if self.queue_error():
            return self.queue_error()
        if self.closing or w._shutting_down or w._state_maintenance:
            return 'Library operation in progress'
        lib = w.lib
        if (not lib or lib.source_kind != 'rekordbox-db' or not self.tags.verified
                or not lib.source_library_id):
            return 'Refresh the rekordbox library before deleting tags'
        if not self.deletion_factory and definitions.load_deletion_authority(
                self.tags.data_root / 'rekordbox-tag-definition-delete-authority.json',
                Path(lib.source), lib.source_library_id) is None:
            return 'Deleting tags is not enabled for this library yet'
        if not lib.source_tags and not any(self._deleting(job['request']) for job in self.pending):
            return 'There are no My Tags to delete'
        return ''

    def enqueue_deletion(self, request):
        definitions.validate_deletion_request(request)
        return self.enqueue(request)

    def enqueue(self, request):
        self._validate(request)
        deleting = self._deleting(request)
        reason = self.deletion_reason() if deleting else self.reason()
        if reason:
            self.window._flash_status(reason, 10)
            return False
        w = self.window
        if request['library_id'] != w.lib.source_library_id:
            w._flash_status('The selected library changed; capture the tag change again', 10)
            return False
        if deleting:
            matches = [tag for tag in w.lib.source_tags
                       if (tag.tag_id, tag.source_uuid, tag.category_id, tag.category_uuid, tag.name)
                       == tuple(request[key] for key in ('tag_id', 'tag_uuid', 'category_id', 'category_uuid', 'name'))]
            if len(matches) != 1:
                w._flash_status('The selected tag changed; refresh the library', 10)
                return False
        elif any(tag.category_id == request['category_id'] and tag.name.casefold() == request['name'].casefold()
                 for tag in w.lib.source_tags):
            w._flash_status('A tag with that name already exists in this category', 10)
            return False
        for job in self.pending:
            if job['request']['operation_id'] == request['operation_id']:
                if (job['source'], job['library_id'], job['request']) == (str(w.lib.source), w.lib.source_library_id, request):
                    return True
                w._flash_status('That operation identity is already used by another intention', 10)
                return False
            if (job['request']['tag_id'] == request['tag_id'] or job['request']['tag_uuid'] == request['tag_uuid']
                    or (job['source'], job['library_id'], job['request']['category_id'], job['request']['name'].casefold())
                    == (str(w.lib.source), w.lib.source_library_id, request['category_id'], request['name'].casefold())):
                w._flash_status('That tag already has a pending change', 10)
                return False
        if len(self.pending) >= 256:
            w._flash_status('Wait for pending tag changes to finish', 10)
            return False
        missing = object()
        previous = w.settings.get(QUEUE, missing)
        job = dict(source=str(w.lib.source), library_id=w.lib.source_library_id,
                   request=deepcopy(request), error='')
        w.settings[QUEUE] = [*(previous if previous is not missing else []), job]
        if not w._save_state():
            if previous is missing:
                w.settings.pop(QUEUE, None)
            else:
                w.settings[QUEUE] = previous
            return False
        self.status('Deleting tag…' if deleting else 'Creating tag…')
        self.schedule()
        return True

    def status(self, message):
        if self.tags.definition_update_status != message:
            self.tags.definition_update_status = message
            self.tags.changed.emit()

    def schedule(self):
        if self.closing or not self.pending or self.timer.isActive():
            return
        self.timer.start(max(0, int((self.not_before - time.monotonic()) * 1000)))

    def pump(self):
        w = self.window
        if self.closing or not self.pending:
            return
        if self.queue_error():
            self.status(self.queue_error())
            return
        if self.pending[0]['error']:
            return
        if (self.tags.source_job_owned or self.tags.prep_priority or w._refresh_worker is not None
                or w._state_maintenance or w._device_operation_owned()):
            self.timer.start(2000)
            return
        imports = getattr(w, 'shared_import_controller', None)
        if imports and imports.source_priority:
            self.timer.start(2000)
            return
        if (self.deletion_reason() if self._deleting(self.pending[0]['request']) else self.reason()):
            return
        if time.monotonic() < self.not_before:
            self.schedule()
            return
        job = deepcopy(self.pending[0])
        lib = w.lib
        if (job['source'], job['library_id']) != (str(lib.source), lib.source_library_id):
            self.status('Switch to the original library to finish its tag change')
            return

        def work():
            try:
                assert_rekordbox_closed()
            except WriteProofError as exc:
                if str(exc) == 'Close rekordbox and its background agent before editing':
                    return dict(waiting=True)
                raise
            self.tags._recover_pending(lib)
            if self._deleting(job['request']):
                authority = definitions.load_deletion_authority(
                    self.tags.data_root / 'rekordbox-tag-definition-delete-authority.json',
                    Path(lib.source), lib.source_library_id)
                store = (self.deletion_factory(lib) if self.deletion_factory else
                         definitions.TagDeletionStore(authority, self.tags._write_root(lib)))
            else:
                authority = definitions.load_authority(
                    self.tags.data_root / 'rekordbox-tag-definition-authority.json',
                    Path(lib.source), lib.source_library_id)
                store = (self.factory(lib) if self.factory else
                         definitions.TagDefinitionStore(authority, self.tags._write_root(lib)))
            created = store.apply(job['request'])
            return dict(created=created, library=self.loader(Path(lib.source)))

        def completed(worker):
            if worker.error:
                self.fail(worker.error, job)
                return
            if worker.result.get('waiting'):
                self.not_before = time.monotonic() + 2
                self.status('Tag change pending — quit rekordbox to save')
                self.schedule()
                return
            head = self.pending[0] if self.pending else None
            if (not w.lib or (str(w.lib.source), w.lib.source_library_id) != (job['source'], job['library_id'])
                    or not isinstance(head, dict) or head.get('request') != job['request']):
                self.fail('Saved in the original library; switch back to finish the tag change', job)
                return
            library = worker.result['library']
            verified = (not any(tag.tag_id == job['request']['tag_id'] for tag in library.source_tags)
                        and not any(job['request']['tag_id'] in (track.source_tag_ids or ())
                                    for track in library.tracks_by_id.values())
                        if self._deleting(job['request']) else
                        any(tag.tag_id == job['request']['tag_id'] and tag.source_uuid == job['request']['tag_uuid']
                               and tag.name == job['request']['name']
                               and tag.category_id == job['request']['category_id']
                               and tag.category_uuid == job['request']['category_uuid']
                            for tag in library.source_tags))
            if (str(library.source), library.source_library_id) != (job['source'], job['library_id']) or not verified:
                self.fail('The tag change could not be verified; the intention is preserved', job)
                return
            previous = w.settings.get(QUEUE, [])
            w.settings[QUEUE] = list(previous[1:])
            if not w._save_state():
                w.settings[QUEUE] = previous
                self.fail('Tag change saved in rekordbox; local acknowledgement failed. Retry after fixing storage.', job)
                return
            self.not_before = 0
            self.status(('Deleting tag…' if self._deleting(self.pending[0]['request']) else 'Creating tag…')
                        if self.pending else '')
            if not self.closing and not w._shutting_down:
                w._publish_created_library(library)
            self.schedule()

        if not self.tags._run_source_job('tag-definitions', work, completed):
            self.timer.start(2000)

    def fail(self, message, job):
        self.timer.stop()
        owned = next((index for index, current in enumerate(self.pending)
                      if isinstance(current, dict) and (current.get('source'), current.get('library_id'), current.get('request'))
                      == (job['source'], job['library_id'], job['request'])), None)
        if owned is not None:
            previous = self.window.settings.get(QUEUE, [])
            updated = list(previous)
            updated[owned] = {**updated[owned], 'error': message}
            self.window.settings[QUEUE] = updated
            # Keep the RAM error even if storage still fails; retry must be
            # explicit instead of immediately re-admitting the committed save.
            self.window._save_state()
            self.status('Tag change needs attention — ' + message)
        self.window._flash_status(message, 12)

    def retry(self):
        if self.closing or self.tags.owned or not self.pending or self.queue_error():
            return False
        previous = self.window.settings.get(QUEUE, [])
        self.window.settings[QUEUE] = [{**previous[0], 'error': ''}, *previous[1:]]
        if not self.window._save_state():
            self.window.settings[QUEUE] = previous
            return False
        self.not_before = 0
        self.schedule()
        return True

    def discard(self):
        if self.closing or self.tags.owned or not self.pending or self.queue_error():
            return False
        w = self.window
        if QMessageBox.question(w, 'Discard pending tag changes',
                f'Discard {len(self.pending)} pending tag change(s)?\nChanges already saved in rekordbox remain saved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        previous = w.settings.get(QUEUE, [])
        w.settings[QUEUE] = []
        if not w._save_state():
            w.settings[QUEUE] = previous
            return False
        self.timer.stop()
        self.status('')
        w._native_reconcile_source()
        return True

    def stop(self):
        self.closing = True
        self.timer.stop()
