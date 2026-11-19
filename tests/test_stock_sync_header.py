"""Cached stock-iPod action hints inspect current playlist headers only."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.app import MainWindow
from desktop.stock_catalog_sync import has_sync_source,request_device_sync,request_sync
from soundbox.library import Library,Playlist
from soundbox.stock_ipod_catalog_state import StockCatalogWitness
from soundbox.stock_ipod_playlist_binding import PlaylistBinding


class NoMembers(list):
    def __iter__(self):raise AssertionError('Header walked playlist members')
    def __len__(self):raise AssertionError('Header inspected playlist members')


class StockSyncHeader(unittest.TestCase):
    def setUp(self):
        self.device='/synthetic/IPOD'
        self.source=Playlist('Favorites',[],NoMembers(['one','two']),pid='42')
        self.library=Library({},[self.source],Path('/synthetic/source/master.db'),
            source_kind='rekordbox-db',source_library_id='library',
            source_playlist_catalog={'nodes':[dict(ID='42',UUID='uuid',rb_local_deleted=0)]})
        self.library.resolve=mock.Mock(side_effect=AssertionError('Header resolved songs'))
        self.target=self.header(7,'Favorites')
        self.snapshot=SimpleNamespace(identity_key='device',playlists=(self.target,))
        self.host=SimpleNamespace(lib=self.library,custom={},
            _stock_view_cache={self.device:(self.snapshot,None)},_stock_catalog_witness_cache={},
            _export_playlists=mock.Mock(side_effect=AssertionError('Header materialized export views')),
            _view_playlist=mock.Mock(side_effect=AssertionError('Header sorted a source view')),
            _row_for=mock.Mock(side_effect=AssertionError('Header derived a song')))
        self.host._shutting_down=False;self.host._state_maintenance=False
        self.host._device_profiles={self.device:SimpleNamespace(kind='stock_ipod')}
        self.host._show_export_dialog=mock.Mock()
        self.host._export_target_result=mock.Mock();self.host._maybe_finish_export_target=mock.Mock()
        self.binding=PlaylistBinding('42','42','uuid',7,'Favorites',(('one',101),('two',102)))

    @staticmethod
    def header(identity,title,kind='Playlist'):
        return SimpleNamespace(playlist_id=identity,title=title,kind=kind,
                               entries=NoMembers(['members must stay opaque']))

    def remember(self,*bindings):
        witness=StockCatalogWitness((str(self.library.source),self.library.source_kind,self.library.source_library_id),
            'device','a'*64,(),bindings=bindings)
        self.host._stock_catalog_witness_cache[self.device]=witness
        return witness

    def test_deleted_device_playlist_cannot_keep_empty_ipod_in_sync_mode(self):
        self.remember(self.binding)
        self.snapshot.playlists=(self.header(99,'Videos','System playlist'),)
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_recreated_same_name_never_borrows_missing_saved_device_id(self):
        self.remember(self.binding);self.snapshot.playlists=(self.header(8,'Favorites'),)
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_stale_source_uuid_blocks_same_name_bootstrap_for_remembered_key(self):
        self.remember(self.binding)
        self.library.source_playlist_catalog['nodes'][0]['UUID']='replacement'
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_valid_binding_survives_names_count_and_order_changes(self):
        self.remember(self.binding)
        self.source.name='New source title';self.source.keys=NoMembers(['new','two','one'])
        self.snapshot.playlists=(self.header(7,'Changed iPod title'),)
        self.assertTrue(has_sync_source(self.host,self.device))

    def test_duplicate_target_ids_or_saved_owners_are_ambiguous(self):
        self.remember(self.binding)
        self.snapshot.playlists=(self.target,self.header(7,'Other'))
        self.assertFalse(has_sync_source(self.host,self.device))
        self.snapshot.playlists=(self.target,)
        self.remember(self.binding,replace(self.binding,key='gone',native_id='gone'))
        self.assertFalse(has_sync_source(self.host,self.device))
        self.remember(self.binding,replace(self.binding,device_playlist_id=8))
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_invalid_native_identity_is_not_a_current_header_association(self):
        original=self.library.source_playlist_catalog['nodes'][0]
        for nodes in ([],[{**original,'rb_local_deleted':1}],
                      [original,dict(original)],[{**original,'UUID':''}]):
            with self.subTest(nodes=nodes):
                self.library.source_playlist_catalog['nodes']=nodes
                self.assertFalse(has_sync_source(self.host,self.device))

    def test_unique_manual_names_bootstrap_without_any_member_comparison(self):
        self.source.name='  FAVORITES  '
        self.assertTrue(has_sync_source(self.host,self.device))
        self.snapshot.playlists=(self.target,self.header(8,'Favorites'))
        self.assertFalse(has_sync_source(self.host,self.device))
        self.snapshot.playlists=(self.target,)
        self.library.playlists.append(Playlist('Favorites',[],NoMembers(),pid='another'))
        self.library.source_playlist_catalog['nodes'].append(dict(ID='another',UUID='another',rb_local_deleted=0))
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_duplicate_source_key_and_competing_saved_target_refuse(self):
        self.remember(self.binding)
        self.library.playlists.append(replace(self.source))
        self.assertFalse(has_sync_source(self.host,self.device))
        self.library.playlists.pop()
        self.remember(replace(self.binding,key='other',native_id='other'))
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_adopted_native_custom_shadow_is_one_current_source(self):
        self.source.export_key='cust:FAVORITES'
        self.source.name='Renamed native Favorites'
        self.host.custom={'FAVORITES':NoMembers(['one','two'])}
        self.remember(replace(self.binding,key='cust:FAVORITES'))
        self.assertTrue(has_sync_source(self.host,self.device))
        self.library.source_playlist_catalog['nodes'][0]['UUID']='replacement'
        self.assertFalse(has_sync_source(self.host,self.device))
        self.library.source_playlist_catalog['nodes'][0]['UUID']='uuid'
        self.library.playlists.append(replace(self.source))
        self.assertFalse(has_sync_source(self.host,self.device))

    def test_wrong_source_or_device_witness_does_not_supply_renamed_association(self):
        original=self.remember(self.binding);self.source.name='New name'
        for witness in (replace(original,device_key='another'),
                        replace(original,source=('/other/master.db','rekordbox-db','other'))):
            with self.subTest(witness=witness.source):
                self.host._stock_catalog_witness_cache[self.device]=witness
                self.assertFalse(has_sync_source(self.host,self.device))

    def test_header_never_resolves_sorts_reads_rows_or_performs_filesystem_io(self):
        self.remember(self.binding)
        with mock.patch.object(Path,'stat',side_effect=AssertionError('Header filesystem IO')), \
             mock.patch.object(Path,'read_bytes',side_effect=AssertionError('Header file read')), \
             mock.patch('builtins.open',side_effect=AssertionError('Header open')), \
             mock.patch('builtins.sorted',side_effect=AssertionError('Header sort')):
            self.assertTrue(has_sync_source(self.host,self.device))
        self.library.resolve.assert_not_called()
        self.host._export_playlists.assert_not_called();self.host._row_for.assert_not_called()

    def test_primary_action_is_export_when_clean_and_sync_only_for_pending_association(self):
        host = self.host
        self.remember(self.binding)
        host._current_device_path = self.device
        host._stock_has_sync_source = lambda dev:has_sync_source(host, dev)
        host._sync_device = mock.Mock()
        host._drift_cache = {self.device:0}
        MainWindow._device_primary_action(host)
        host._show_export_dialog.assert_called_once_with(self.device)
        host._sync_device.assert_not_called()
        host._show_export_dialog.reset_mock()
        host._drift_cache[self.device] = 1
        MainWindow._device_primary_action(host)
        host._sync_device.assert_called_once_with(self.device)
        host._show_export_dialog.assert_not_called()
        host._sync_device.reset_mock()
        self.library.source_playlist_catalog['nodes'][0]['UUID'] = 'replaced'
        MainWindow._device_primary_action(host)
        host._sync_device.assert_not_called()
        host._show_export_dialog.assert_called_once_with(self.device)

    def test_header_caption_uses_cached_pending_status_without_member_or_filesystem_work(self):
        host = self.host
        self.remember(self.binding)
        host._current_device_path = self.device
        host._stock_has_sync_source = lambda dev:has_sync_source(host, dev)
        host._device_operation_owned = lambda:False
        host._device_mount_tokens = {self.device:1}
        host._device_coordinator = SimpleNamespace(active=None)
        host._exporting = False
        host.device_sync_btn = mock.Mock()
        host.device_sync_btn.text.return_value = 'Previous label'
        with mock.patch('desktop.app.device_facts'), \
             mock.patch('desktop.devices.device_export_readiness',
                        return_value=SimpleNamespace(ready=True,reason='')), \
             mock.patch.object(Path,'stat',side_effect=AssertionError('Header filesystem IO')):
            for drift, state, caption in ((0,'up_to_date','Export'), (2,'up_to_date','Sync'),
                    (None,'up_to_date','Export'), (0,'local_changes','Export'),
                    (None,'local_changes','Sync')):
                with self.subTest(drift=drift,state=state):
                    host._drift_cache = {self.device:drift}
                    self.snapshot.sync_state = state
                    MainWindow._update_device_action_state(host)
                    host.device_sync_btn.setText.assert_called_with(caption)
        self.library.resolve.assert_not_called()
        host._row_for.assert_not_called()

    def test_unassociated_direct_requests_open_the_shared_export_chooser(self):
        with mock.patch('desktop.stock_catalog_sync.default_sync_selection',return_value=()):
            request_device_sync(self.host,self.device)
        self.host._show_export_dialog.assert_called_once_with(preferred=self.device)
        self.host._show_export_dialog.reset_mock()
        with mock.patch('desktop.stock_catalog_sync._associations',return_value=()):
            request_sync(self.host,('7','Favorites',2),device=self.device)
        self.host._show_export_dialog.assert_called_once_with(preferred=self.device)

    def test_unassociated_owned_batch_fails_only_its_target_without_nested_chooser(self):
        pending=[{'path':'/synthetic/Friend'}]
        self.host._export_batch=dict(active={'path':self.device},pending=pending)
        with mock.patch('desktop.stock_catalog_sync._associations',return_value=()):
            request_sync(self.host,('7','Favorites',2),device=self.device)
        self.host._show_export_dialog.assert_not_called()
        self.host._maybe_finish_export_target.assert_called_once_with()
        self.assertEqual(self.host._export_target_result.call_args.args[0],self.device)
        self.assertIsInstance(self.host._export_target_result.call_args.args[1],RuntimeError)
        self.assertIs(self.host._export_batch['pending'],pending)
        self.assertEqual(pending,[{'path':'/synthetic/Friend'}])

    def test_other_batch_and_shutdown_never_open_nested_or_late_chooser(self):
        self.host._export_batch=dict(active={'path':'/synthetic/Other'})
        with mock.patch('desktop.stock_catalog_sync.default_sync_selection',return_value=()):
            request_device_sync(self.host,self.device)
        self.host._show_export_dialog.assert_not_called()
        self.host._export_target_result.assert_not_called()
        self.host._export_batch=None;self.host._shutting_down=True
        request_device_sync(self.host,self.device)
        self.host._show_export_dialog.assert_not_called()
