"""Captured creation intents survive storage failures, shutdown and late delivery."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import time
import unittest
from unittest import mock

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from desktop.tag_creation_controller import QUEUE, TagDefinitionController
from soundbox import rekordbox_tag_definitions as definitions
from soundbox.library import Library, LibraryTag, Playlist, TagCategory


class Tags(QObject):
    changed = Signal()
    idle = Signal()

    def __init__(self, root):
        super().__init__()
        self.data_root = root
        self.verified = True
        self.prep_priority = False
        self.definition_update_status = ''
        self.busy = False
        self.admitted = []
        self._recover_pending = mock.Mock()
        self._write_root = lambda _lib: root / 'recovery'

    @property
    def owned(self):
        return self.busy

    @property
    def source_job_owned(self):
        return self.busy

    def _run_source_job(self, kind, callback, completed):
        if self.busy:
            return False
        self.busy = True
        self.admitted.append((kind, callback, completed))
        return True

    def deliver(self, *, error=''):
        _, callback, completed = self.admitted[-1]
        result = None
        if not error:
            try:
                result = callback()
            except Exception as exc:
                error = str(exc)
        self.busy = False
        completed(SimpleNamespace(error=error, result=result))
        self.changed.emit()
        self.idle.emit()


class Window(QWidget):
    def __init__(self, root):
        super().__init__()
        self.settings = {}
        self.lib = Library({}, [], '/synthetic/master.db', source_kind='rekordbox-db',
                           source_library_id='library', source_tag_categories=[TagCategory('1', 'Genre', '1')])
        self.tag_controller = Tags(root)
        self._shutting_down = False
        self._state_maintenance = False
        self._refresh_worker = None
        self._device_operation_owned = lambda: False
        self.saved = []
        self.published = []
        self.save_ok = True
        self._flash_status = mock.Mock()

    def _save_state(self):
        self.saved.append(deepcopy(self.settings))
        return self.save_ok

    def _commit_tag_state(self, replacements, removals, committed, failed, *, current, expected_queues=None):
        # Controller unit fixture delivers a sparse durable ACK immediately.
        # Actual held StateRepository workers are covered in source-admission tests.
        if not current():failed(OSError('The source changed'));return True
        previous=deepcopy(self.settings)
        for (root,key),value in replacements.items():
            assert root=='settings'
            self.settings[key]=deepcopy(value)
        if not self._save_state():
            self.settings=previous;failed(OSError('No space for pending intention'));return True
        committed();return True

    def _publish_created_library(self, library):
        self.published.append(library)
        self.lib = library


class CreationController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        area = self.enterContext(tempfile.TemporaryDirectory())
        self.window = Window(Path(area))
        self.addCleanup(self.window.deleteLater)
        self.enterContext(mock.patch('desktop.tag_creation_controller.assert_rekordbox_closed'))
        self.requests = []
        self.created = {}
        self.last = None
        owner = self
        class Store:
            def apply(self, request):
                owner.requests.append(deepcopy(request))
                owner.created.setdefault(request['operation_id'], dict(ID=request['tag_id']))
                owner.last = deepcopy(request)
                return owner.created[request['operation_id']]
        def load(_source):
            request = self.last
            return replace(self.window.lib, source_tags=[LibraryTag(request['tag_id'], request['name'], 'Genre',
                request['tag_uuid'], request['category_id'], request['category_uuid'])])
        self.controller = TagDefinitionController(self.window, store_factory=lambda _: Store(), loader=load)
        self.addCleanup(self.controller.stop)

    def request(self, name='New Tag'):
        return definitions.request_for('library', '1', '1', name)

    def begin(self, request):
        self.assertTrue(self.controller.enqueue(request))
        self.controller.timer.stop()
        self.controller.pump()
        self.assertEqual(len(self.window.tag_controller.admitted), 1)

    def test_shared_source_idle_replaces_busy_poll_but_preserves_explicit_native_owner_backoff(self):
        self.assertTrue(self.controller.enqueue(self.request()))
        tags=self.window.tag_controller;tags.busy=True
        self.controller.pump();self.assertGreater(self.controller.timer.remainingTime(),1000)
        tags.busy=False;tags.idle.emit()
        self.assertEqual(self.controller.timer.remainingTime(),0)
        self.controller.timer.stop();self.controller.pump()
        self.assertEqual(len(tags.admitted),1)
        # A native owner refusal is an explicit deadline, unlike a busy poll.
        tags.busy=False;self.controller.not_before=time.monotonic()+2
        self.controller.timer.start(2000);tags.idle.emit()
        self.assertGreater(self.controller.timer.remainingTime(),1000)

    def test_intent_persists_before_source_admission_and_verified_ack_precedes_presentation(self):
        request = self.request()
        self.begin(request)
        self.assertEqual(self.window.saved[0][QUEUE][0]['request'], request)
        self.assertFalse(self.requests)
        self.assertFalse(self.window.published)
        self.window.tag_controller.deliver()
        self.assertEqual(self.window.saved[-1][QUEUE], [])
        self.assertEqual(self.window.published[0].source_tags[0].name, 'New Tag')

    def test_discard_retains_an_admitted_head_until_its_normal_durable_ack(self):
        from desktop.tag_creation_controller import QMessageBox
        request=self.request();self.assertTrue(self.controller.enqueue(request));self.controller.timer.stop()
        before=deepcopy(self.controller.pending);saves=len(self.window.saved)
        def confirm(*_args):
            self.controller.not_before=0;self.controller.pump()
            self.assertTrue(self.window.tag_controller.owned)
            return QMessageBox.Yes
        with mock.patch.object(QMessageBox,'question',side_effect=confirm):
            self.assertFalse(self.controller.discard())
        self.assertEqual(self.controller.pending,before);self.assertEqual(len(self.window.saved),saves)
        self.window.tag_controller.deliver()
        self.assertEqual(self.controller.pending,[]);self.assertEqual(self.requests,[request])
        self.assertEqual(len(self.window.published),1)

    def test_discard_refuses_changed_queue_source_restore_and_state_owners(self):
        from desktop.tag_creation_controller import QMessageBox
        self.assertTrue(self.controller.enqueue(self.request()));self.controller.timer.stop()
        reviewed=deepcopy(self.controller.pending)
        for change in ('tail','in-place','source','kind','closing','maintenance','publication','refresh','restore'):
            with self.subTest(change=change):
                self.window.settings[QUEUE]=deepcopy(reviewed)
                self.window.lib.source_library_id='library';self.window.lib.source_kind='rekordbox-db'
                self.controller.closing=False;self.window._state_maintenance=False
                self.window._state_publication=False;self.window._refresh_worker=None
                self.window._state_repository=SimpleNamespace(generation=0,area=SimpleNamespace(generation=0))
                retained=[]
                def confirm(*_args):
                    if change=='tail':
                        self.assertTrue(self.controller.enqueue(self.request('Newer')));self.controller.timer.stop()
                    elif change=='in-place':self.controller.pending[0]['error']='New conflict'
                    elif change=='source':self.window.lib.source_library_id='replacement'
                    elif change=='kind':self.window.lib.source_kind='rekordbox-xml'
                    elif change=='closing':self.controller.closing=True
                    elif change=='maintenance':self.window._state_maintenance=True
                    elif change=='publication':self.window._state_publication=True
                    elif change=='refresh':self.window._refresh_worker=object()
                    elif change=='restore':self.window._state_repository.area.generation+=1
                    retained.extend(deepcopy(self.controller.pending))
                    return QMessageBox.Yes
                with mock.patch.object(QMessageBox,'question',side_effect=confirm):
                    self.assertFalse(self.controller.discard())
                self.assertEqual(self.controller.pending,retained)
        self.controller.closing=False

    def test_unchanged_discard_is_durable_and_storage_failure_retains_exact_queue(self):
        from desktop.tag_creation_controller import QMessageBox
        self.assertTrue(self.controller.enqueue(self.request()));self.controller.timer.stop()
        self.window._native_reconcile_source=mock.Mock();previous=self.controller.pending
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.No):
            self.assertFalse(self.controller.discard())
        self.assertIs(self.controller.pending,previous)
        self.window.save_ok=False
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.assertFalse(self.controller.discard())
        self.assertIs(self.controller.pending,previous)
        self.window._native_reconcile_source.assert_not_called()
        self.window.save_ok=True
        with mock.patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.assertTrue(self.controller.discard())
        self.assertEqual(self.window.saved[-1][QUEUE],[])
        self.assertEqual(self.controller.pending,[]);self.window._native_reconcile_source.assert_called_once()

    def test_continuous_ack_storage_failure_stops_readmission_until_explicit_retry(self):
        request = self.request()
        self.begin(request)
        self.window.save_ok = False
        self.window.tag_controller.deliver()
        self.assertFalse(self.window.published)
        self.assertEqual(self.controller.pending[0]['request'], request)
        self.assertTrue(self.controller.pending[0]['error'])
        for _ in range(10):
            self.window.tag_controller.changed.emit()
            self.controller.pump()
            self.app.processEvents()
        self.assertEqual(len(self.window.tag_controller.admitted), 1)
        self.assertEqual(len(self.created), 1)
        self.assertTrue(self.controller.retry());self.controller.ack.pump()
        self.assertTrue(self.controller.ack.error)
        self.window.save_ok = True
        self.assertTrue(self.controller.retry())
        self.controller.ack.pump()
        self.controller.timer.stop()
        self.controller.pump()
        self.assertEqual(self.controller.pending, [])
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.requests, [request])

    def test_duplicate_capture_coalesces_without_save_and_conflicting_same_operation_refuses(self):
        request = self.request()
        self.assertTrue(self.controller.enqueue(request))
        saves = len(self.window.saved)
        self.assertTrue(self.controller.enqueue(deepcopy(request)))
        self.assertEqual(len(self.window.saved), saves)
        self.assertFalse(self.controller.enqueue({**request, 'name': 'Different'}))
        self.assertEqual(len(self.controller.pending), 1)
        self.assertFalse(self.controller.queue_error())

    def test_late_success_or_error_cannot_mark_replacement_intent_or_publish_original_library(self):
        original = self.request()
        self.begin(original)
        replacement = self.request('Replacement')
        replacement_job = dict(source='/synthetic/master.db', library_id='library', request=replacement, error='')
        for error in ('', 'late source failure'):
            with self.subTest(error=error):
                self.window.settings[QUEUE] = [deepcopy(replacement_job)]
                self.window.tag_controller.deliver(error=error)
                self.controller.timer.stop()
                self.assertEqual(self.window.settings[QUEUE], [replacement_job])
                self.assertFalse(self.window.published)

    def test_source_switch_preserves_original_intent_and_does_not_publish_into_new_library(self):
        request = self.request()
        self.begin(request)
        switched = replace(self.window.lib, source='/different/master.db', source_library_id='different')
        self.window.lib = switched
        self.window.tag_controller.deliver(error='original source failure')
        self.assertIs(self.window.lib, switched)
        self.assertEqual(self.controller.pending[0]['request'], request)
        self.assertTrue(self.controller.pending[0]['error'])
        self.assertFalse(self.window.published)

    def test_prepared_prep_reservation_blocks_even_without_other_owner(self):
        self.assertTrue(self.controller.enqueue(self.request()))
        self.controller.timer.stop()
        self.window.tag_controller.prep_priority = True
        self.controller.pump()
        self.assertFalse(self.window.tag_controller.admitted)

        self.window.tag_controller.prep_priority = False
        self.controller.pump()
        self.assertEqual(len(self.window.tag_controller.admitted), 1)

    def test_closing_retains_successful_acknowledgement_without_starting_or_publishing_more(self):
        self.begin(self.request())
        self.controller.stop()
        self.window._shutting_down = True
        self.window.tag_controller.deliver()
        self.assertEqual(self.controller.pending, [])
        self.assertFalse(self.window.published)
        self.assertFalse(self.controller.timer.isActive())

    def test_failed_initial_persistence_retains_unsaved_intention_without_source_admission(self):
        self.window.save_ok = False
        request=self.request()
        self.assertTrue(self.controller.enqueue(request))
        self.assertNotIn(QUEUE, self.window.settings)
        self.assertEqual(self.controller.pending[0]['request'],request)
        self.assertTrue(self.controller.admission_error)
        self.controller.pump()
        self.assertFalse(self.window.tag_controller.admitted)


class SmartReadback(unittest.TestCase):
    def setup_readback(self):
        from soundbox.rekordbox_playlist_store import request_for
        from soundbox.rekordbox_playlist_plan import smart_tag_rule
        tags = [LibraryTag('11', 'Dubstep', 'Genre', 'tag-eleven', '1', '1'),
                LibraryTag('22', 'SONNY', 'Genre', 'tag-twenty-two', '1', '1')]
        base = dict(nodes=[], members=[], revision='a' * 64)
        request = request_for(base, 'create_smart', name='My Any Tags', tags=tags)
        node = dict(ID=request['playlist_id'], UUID=request['playlist_uuid'], Attribute=4,
                    rb_local_deleted=0, Name=request['name'], ParentID='root',
                    SmartList=smart_tag_rule(request['playlist_id'], ['11', '22']))
        catalog = dict(nodes=[node], members=[], revision='b' * 64)
        library = Library({}, [Playlist(request['name'], [], [], request['playlist_id'])], '/synthetic/master.db',
            source_kind='rekordbox-db', source_library_id='library', source_tags=tags,
            source_playlist_catalog=deepcopy(catalog))
        return library, catalog, request

    def test_full_fresh_source_readback_accepts_valid_empty_computed_playlist(self):
        from desktop.rekordbox_playlists import _verified_smart_readback
        library, catalog, request = self.setup_readback()
        self.assertTrue(_verified_smart_readback(library, catalog, request, '/synthetic/master.db', 'library'))

    def test_matching_store_and_reader_cannot_acknowledge_wrong_any_rule_or_stale_tag(self):
        from desktop.rekordbox_playlists import _verified_smart_readback
        for fault in ('all', 'negative', 'other-tag', 'missing-playlist', 'tag-replaced', 'other-source'):
            with self.subTest(fault=fault):
                library, catalog, request = self.setup_readback()
                if fault == 'all':
                    catalog['nodes'][0]['SmartList'] = catalog['nodes'][0]['SmartList'].replace('LogicalOperator="2"', 'LogicalOperator="1"')
                elif fault == 'negative':
                    catalog['nodes'][0]['SmartList'] = catalog['nodes'][0]['SmartList'].replace('Operator="8"', 'Operator="9"')
                elif fault == 'other-tag':
                    catalog['nodes'][0]['SmartList'] = catalog['nodes'][0]['SmartList'].replace('ValueLeft="22"', 'ValueLeft="33"')
                elif fault == 'missing-playlist':
                    library.playlists = []
                elif fault == 'tag-replaced':
                    library.source_tags[0] = replace(library.source_tags[0], source_uuid='replacement')
                else:
                    library.source = '/different/master.db'
                library.source_playlist_catalog = deepcopy(catalog)
                self.assertFalse(_verified_smart_readback(library, catalog, request, '/synthetic/master.db', 'library'))


class SmartIntentClocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self):
        from desktop.rekordbox_playlists import PlaylistEditController
        area = self.enterContext(tempfile.TemporaryDirectory())
        window = Window(Path(area))
        self.addCleanup(window.deleteLater)
        window.tag_controller.playlist_update_status = ''
        window._native_pending_changed = mock.Mock()
        folder = dict(ID='1000', UUID='00000000-0000-4000-8000-000000001000',
            Name='GENRES', ParentID='root', Seq=1, Attribute=1, SmartList=None,
            rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
            rb_local_synced=0, usn=None, rb_local_usn=10)
        child = dict(folder, ID='1001', UUID='00000000-0000-4000-8000-000000001001',
            Name='Existing Smart', ParentID='1000', Attribute=4, SmartList='<unchanged/>')
        catalog = dict(nodes=[folder, child], members=[], revision='a' * 64)
        window.lib.source_playlist_catalog = catalog
        controller = PlaylistEditController(window, store_factory=lambda _: None)
        self.addCleanup(controller.stop)
        controller.schedule = mock.Mock()
        tags = [LibraryTag('11', 'Dubstep', 'Genre', 'tag-eleven', '1', '1')]
        return window, controller, catalog, tags

    def test_causal_completion_renews_new_sibling_clocks_before_persisting_the_next_intent(self):
        from desktop.rekordbox_playlists import QUEUE as PLAYLIST_QUEUE
        from soundbox.rekordbox_playlist_plan import revision, smart_tag_rule
        from soundbox.rekordbox_playlist_store import request_for
        window, controller, catalog, tags = self.fixture()
        first = request_for(catalog, 'create_smart', name='First', parent='1000', tags=tags)
        second = request_for(catalog, 'create_smart', name='Second', parent='1000', tags=tags)
        self.assertTrue(controller.enqueue(first))
        self.assertTrue(controller.enqueue(second))
        rename = request_for(catalog, 'rename', identity='1000', name='Renamed Folder')
        self.assertTrue(controller.enqueue(rename))
        window.tag_controller.run_playlist_job = lambda work, done: window.tag_controller._run_source_job('playlists', work, done)
        controller.pump()
        self.assertEqual(len(window.tag_controller.admitted), 1)
        new_catalog = deepcopy(catalog)
        new_catalog['nodes'][1]['Seq'] = 2
        node = dict(new_catalog['nodes'][1], ID=first['playlist_id'], UUID=first['playlist_uuid'],
            Name=first['name'], Seq=1, SmartList=smart_tag_rule(first['playlist_id'], ['11']))
        new_catalog['nodes'].append(node)
        new_catalog['revision'] = revision(new_catalog['nodes'], new_catalog['members'])
        library = replace(window.lib, source_playlist_catalog=deepcopy(new_catalog), source_tags=tags,
            playlists=[Playlist(first['name'], [], [], first['playlist_id'])])
        window.tag_controller.busy = False
        completed = window.tag_controller.admitted[-1][2]
        from soundbox.rekordbox_playlist_store import renew_smart_request
        def later_clocks(request, current):
            renewed = renew_smart_request(request, current)
            renewed['smart_clocks']['parent_xml_timestamp'] = rename['xml_timestamp'] + 1000
            return renewed
        with mock.patch('soundbox.rekordbox_playlist_store.renew_smart_request', side_effect=later_clocks):
            completed(SimpleNamespace(error='', result=dict(catalog=new_catalog, library=library,
                transition=(first['revision'],new_catalog['revision']))))
        persisted = window.saved[-1][PLAYLIST_QUEUE]
        self.assertEqual(len(persisted), 2)
        renewed = persisted[0]['request']
        self.assertEqual(renewed['operation_id'], second['operation_id'])
        self.assertEqual(renewed['playlist_id'], second['playlist_id'])
        self.assertEqual(renewed['revision'], new_catalog['revision'])
        self.assertEqual(set(renewed['smart_clocks']['sibling_timestamps']), {'1001', first['playlist_id']})
        self.assertEqual(controller.pending[0]['request'], renewed)
        self.assertGreater(persisted[1]['request']['xml_timestamp'],
                           renewed['smart_clocks']['parent_xml_timestamp'])
        self.assertEqual(window.published, [library])

    def test_different_new_playlists_and_folder_rename_share_durably_advanced_parent_xml_clocks(self):
        from desktop.rekordbox_playlists import PlaylistEditController, QUEUE as PLAYLIST_QUEUE
        from soundbox.rekordbox_playlist_store import request_for
        with tempfile.TemporaryDirectory() as area:
            window = Window(Path(area))
            self.addCleanup(window.deleteLater)
            window.tag_controller.playlist_update_status = ''
            window._native_pending_changed = mock.Mock()
            folder = dict(ID='1000', UUID='00000000-0000-4000-8000-000000001000',
                Name='GENRES', ParentID='root', Seq=1, Attribute=1, SmartList=None,
                rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
                rb_local_synced=0, usn=None, rb_local_usn=10)
            child = dict(folder, ID='1001', UUID='00000000-0000-4000-8000-000000001001',
                Name='Existing Smart', ParentID='1000', Attribute=4, SmartList='<unchanged/>')
            catalog = dict(nodes=[folder, child], members=[], revision='a' * 64)
            window.lib.source_playlist_catalog = catalog
            controller = PlaylistEditController(window, store_factory=lambda _: None)
            self.addCleanup(controller.stop)
            controller.schedule = mock.Mock()
            tags = [LibraryTag('11', 'Dubstep', 'Genre', 'tag-eleven', '1', '1')]
            first = request_for(catalog, 'create_smart', name='First', parent='1000', tags=tags)
            second = request_for(catalog, 'create_smart', name='Second', parent='1000', tags=tags)
            second['xml_timestamp'] = first['xml_timestamp']
            second['smart_clocks']['parent_xml_timestamp'] = first['smart_clocks']['parent_xml_timestamp']
            self.assertTrue(controller.enqueue(first))
            self.assertTrue(controller.enqueue(second))
            persisted = window.saved[-1][PLAYLIST_QUEUE]
            self.assertGreater(persisted[1]['request']['smart_clocks']['parent_xml_timestamp'],
                               persisted[0]['request']['smart_clocks']['parent_xml_timestamp'])
            rename = request_for(catalog, 'rename', identity='1000', name='Renamed Folder')
            rename['xml_timestamp'] = first['smart_clocks']['parent_xml_timestamp']
            self.assertTrue(controller.enqueue(rename))
            persisted = window.saved[-1][PLAYLIST_QUEUE]
            self.assertGreater(persisted[2]['request']['xml_timestamp'],
                               persisted[1]['request']['smart_clocks']['parent_xml_timestamp'])
            self.assertFalse(window.tag_controller.admitted)


if __name__ == '__main__':
    unittest.main()
