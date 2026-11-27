"""USB drag intent and owned worker handoff, without devices or app state."""
from types import SimpleNamespace
import unittest
from unittest import mock

from desktop.cdj import CdjDeviceMixin
from soundbox.cdj_transaction import Change


class CdjDropIntent(unittest.TestCase):
    def settings_host(self, library, target):
        from pathlib import Path
        started = []
        host = SimpleNamespace(_shutting_down=False, lib=library,
            _defer_device_operation_until_scan_finishes=lambda *_args, **_kwargs:False,
            _device_operation_owned=lambda *_:False, tag_controller=SimpleNamespace(owned=False),
            _device_profiles={target:SimpleNamespace(kind='generic_usb',cdj_export=True,rockbox_export=True)},
            _device_mount_tokens={target:91}, _release_eject_ready=mock.Mock(),
            _data_file=Path('/synthetic/state/library.json'), _set_device_progress=mock.Mock(),
            _start_pulse=mock.Mock(), _sync_cue_editing=mock.Mock(),
            progress=mock.Mock(), cancel_btn=mock.Mock(),
            _start_device_worker=lambda worker,*_:started.append(worker))
        return host, started

    def test_settings_freezes_only_source_paths_without_copying_song_graphs(self):
        from pathlib import Path
        class UncopyableTrack:
            path = Path('/synthetic/music/song.wav')
            source_analysis_path = '/synthetic/analysis/ANLZ0000.DAT'
            source_native_path = '/synthetic/native/alias.wav'
            def __deepcopy__(self, _memo):
                raise AssertionError('Settings copied unrelated song/cue data')
        track = UncopyableTrack()
        library = SimpleNamespace(source='/synthetic/library/master.db', tracks_by_id={'17':track})
        host, started = self.settings_host(library, '/not-a-device')
        with mock.patch('desktop.cdj.device_facts', return_value=SimpleNamespace(
                volume_uuid='synthetic-volume', writable=True)), \
                mock.patch('soundbox.cdj_export.plan_settings') as settings:
            CdjDeviceMixin._begin_cdj_export(host, '/not-a-device', settings={'device_name':'Fixture'})
            library.source='/new/library/master.db'
            track.path=Path('/new/music/song.wav')
            track.source_analysis_path='/new/analysis/ANLZ0000.DAT'
            track.source_native_path='/new/native/alias.wav'
            started[0].operation(lambda:False, lambda *_:None)
        self.assertEqual(settings.call_args.kwargs['sources'], [
            Path('/synthetic/library/master.db'), Path('/synthetic/music/song.wav'),
            Path('/synthetic/analysis/ANLZ0000.DAT'), Path('/synthetic/native/alias.wav')])

    def test_settings_still_refuses_a_target_overlapping_source_analysis(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory).resolve()/'USB')
            Path(target).mkdir()
            track = SimpleNamespace(path=Path('/synthetic/music/song.wav'),
                source_analysis_path=str(Path(target)/'ANLZ0000.DAT'))
            library = SimpleNamespace(source='/synthetic/library/master.db',tracks_by_id={'17':track})
            host, started = self.settings_host(library, target)
            host._device_mount_tokens[target]=Path(target).stat().st_dev
            with mock.patch('desktop.cdj.device_facts',return_value=SimpleNamespace(
                    volume_uuid='synthetic-volume',writable=True)):
                CdjDeviceMixin._begin_cdj_export(host,target,settings={'device_name':'Fixture'})
                with self.assertRaisesRegex(RuntimeError,'overlaps source library'):
                    started[0].operation(lambda:False,lambda *_:None)
            self.assertEqual(list(Path(target).iterdir()),[])

    def test_playlist_export_freezes_selected_data_before_worker_dispatch(self):
        from pathlib import Path
        from soundbox.library import Library, Playlist, Track
        track = Track('17', 'Before', 'Artist', 'file:///synthetic/song.wav', source_uuid='uuid')
        other = Track('other', 'Unselected', 'Artist', 'file:///unselected/song.wav')
        playlist = Playlist('Selected', ['Folder'], ['17', '17'], 'selected')
        library = Library({'17': track, 'other': other}, [playlist], '/synthetic/master.db',
                          source_kind='rekordbox-db', source_library_id='42')
        host, started = self.settings_host(library, '/not-a-device')
        host.titles = {'17': 'Saved title'}; host.overrides = {'17': 'Saved artist'}
        host.settings = {'cdj_my_settings': {'PLAYER': {'fixture': 1}}}; host.art_cache = object()
        with mock.patch('desktop.cdj.device_facts', return_value=SimpleNamespace(
                volume_uuid='synthetic-volume', writable=True)), \
                mock.patch('soundbox.cdj_export.plan_export') as export:
            CdjDeviceMixin._begin_cdj_export(host, '/not-a-device', [playlist])
            track.name = 'Later'; playlist.keys.clear(); host.titles['17'] = 'Later'
            library.source_library_id = 'different-library'
            host.settings['cdj_my_settings']['PLAYER']['fixture'] = 2
            started[0].operation(lambda: False, lambda *_: None)
        frozen, selected = export.call_args.args[:2]
        self.assertIsNot(frozen, library)
        self.assertEqual(set(frozen.tracks_by_id), {'17'})
        self.assertEqual(frozen.tracks_by_id['17'].name, 'Before')
        self.assertEqual(frozen.source_library_id, '42')
        self.assertEqual(selected[0].keys, ['17', '17'])
        self.assertEqual(export.call_args.kwargs['titles'], {'17': 'Saved title'})
        self.assertEqual(export.call_args.kwargs['global_profile'], {'PLAYER': {'fixture': 1}})
        self.assertIn(Path('/unselected/song.wav'), frozen.export_source_paths)

    def test_format_source_separation_includes_external_analysis_locations(self):
        from pathlib import Path
        from soundbox.library import Library,Track
        track=Track('17','Fixture','Fixture','file:///synthetic/music/song.wav',
            source_analysis_path='/synthetic/analysis/ANLZ0000.DAT',source_native_path='/synthetic/native/song.wav')
        host=SimpleNamespace(_shutting_down=False,
            lib=Library({'17':track},[],'/synthetic/library/master.db',source_kind='rekordbox-db',source_library_id='42'),
            _defer_device_operation_until_scan_finishes=lambda _callback:False,_device_operation_owned=lambda *_:False,
            _device_profiles={'/not-a-device':SimpleNamespace(kind='generic_usb')},_device_mount_tokens={'/not-a-device':91},
            _release_eject_ready=mock.Mock(),_start_pulse=mock.Mock(),_finish_device_result_worker=mock.Mock(),
            _start_device_worker=mock.Mock(),_start_usb_format_worker=lambda worker,*_:setattr(host,"_usb_format_worker",worker))
        with mock.patch('desktop.cdj.device_facts',return_value=SimpleNamespace(volume_uuid='synthetic-volume')), \
             mock.patch('soundbox.usb_format.capture') as capture:
            CdjDeviceMixin._reformat_usb(host,'/not-a-device')
            host._usb_format_worker.operation(lambda:False,lambda *_:None)
        self.assertEqual(capture.call_args.kwargs['sources'],sorted([
            Path('/synthetic/library/master.db'),track.path,Path(track.source_analysis_path),
            Path(track.source_native_path)]))

    def host(self):
        started = []
        host = SimpleNamespace(_shutting_down=False,_cdj_plan=None,_cdj_worker=None,
            _set_device_progress=mock.Mock(),progress=mock.Mock(),_start_pulse=mock.Mock(),_finish_cdj=mock.Mock(),
            _start_device_worker=lambda w,dev,intent:started.append((w,dev,intent)))
        def finished(attr,worker,callback):
            self.assertIs(getattr(host,attr),worker)
            setattr(host,attr,None)
            callback(worker.result)
        host._finish_device_result_worker=finished
        return host,started

    def test_drop_publishes_without_a_second_dialog_and_keeps_plan_until_finished(self):
        host,started=self.host()
        plan=SimpleNamespace(changes=[Change('Contents/song.wav',None,'1'*64,100)],close=mock.Mock(),replaces_native_library=True)
        with mock.patch('desktop.cdj.QMessageBox',side_effect=AssertionError('Unexpected approval')), \
             mock.patch('soundbox.cdj_transaction.commit',return_value={'generation':'new'}) as commit:
            CdjDeviceMixin._review_cdj_plan(host,'/not-a-device',plan,from_drop=True)
            worker,dev,intent=started[0]
            self.assertEqual((dev,intent),('/not-a-device','commit-cdj'))
            commit.assert_not_called();plan.close.assert_not_called();host._finish_cdj.assert_not_called()
            worker.run()
            commit.assert_called_once();plan.close.assert_called_once()
            host._finish_cdj.assert_not_called()
            worker.finished.emit()
            host._finish_cdj.assert_called_once_with('/not-a-device',{'generation':'new'})

    def test_cancelled_drop_closes_preparation_without_publishing(self):
        host,started=self.host()
        plan=SimpleNamespace(changes=[Change('Contents/song.wav',None,'1'*64,100)],close=mock.Mock())
        with mock.patch('soundbox.cdj_transaction.commit') as commit:
            CdjDeviceMixin._review_cdj_plan(host,'/not-a-device',plan,cancelled=True,from_drop=True)
        self.assertEqual(started,[]);commit.assert_not_called();plan.close.assert_called_once()
        self.assertIsInstance(host._finish_cdj.call_args.args[1],RuntimeError)

    def test_drop_authorization_survives_discovery_deferral(self):
        pending=[]
        host=SimpleNamespace(_shutting_down=False,lib=object(),_begin_cdj_export=mock.Mock(),
            _defer_device_operation_until_scan_finishes=lambda callback,**_:pending.append(callback) or True)
        playlists=[object()]
        CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',playlists,from_drop=True,drop_target=(7,'fixture'))
        pending[0]()
        host._begin_cdj_export.assert_called_once_with('/not-a-device',playlists,settings=None,from_drop=True,drop_target=(7,'fixture'),standalone=(),target_role='generic_usb')

    def test_standalone_song_and_target_binding_survive_both_deferrals(self):
        for source_busy in (False,True):
            pending=[]
            host=SimpleNamespace(_shutting_down=False,lib=object(),_begin_cdj_export=mock.Mock(),
                _defer_device_operation_until_scan_finishes=lambda callback,**_:False if source_busy else pending.append(callback) or True,
                _device_operation_owned=lambda *_:False,tag_controller=SimpleNamespace(owned=source_busy),
                _start_pulse=mock.Mock(),cancel_btn=mock.Mock())
            track=object()
            with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:pending.append(callback)):
                CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',standalone=(track,),
                    from_drop=True,drop_target=(8,'fixture'))
            host.tag_controller.owned=False
            pending[0]()
            host._begin_cdj_export.assert_called_once_with('/not-a-device',(),settings=None,
                standalone=(track,),from_drop=True,drop_target=(8,'fixture'),target_role='generic_usb')

    def test_generic_playlist_drop_carries_its_own_authorization(self):
        from desktop.app import MainWindow
        playlists=[object()]
        host=SimpleNamespace(lib=object(),_resolve_jobs=lambda _:playlists,
            _device_profiles={'/not-a-device':SimpleNamespace(kind='generic_usb')},_begin_cdj_export=mock.Mock())
        MainWindow._on_device_drop(host,'/not-a-device',[('cust','Set')])
        host._begin_cdj_export.assert_called_once_with('/not-a-device',playlists,from_drop=True,target_role='generic_usb')

    def test_drop_authorization_survives_waiting_for_source_edits(self):
        from soundbox.library import Playlist
        pending=[]
        host=SimpleNamespace(_shutting_down=False,lib=object(),_begin_cdj_export=mock.Mock(),
            _defer_device_operation_until_scan_finishes=lambda _,**_options:False,
            _device_operation_owned=lambda *_:False,tag_controller=SimpleNamespace(owned=True),
            _start_pulse=mock.Mock(),cancel_btn=mock.Mock())
        playlists=[Playlist('Requested playlist',[],[])]
        with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:pending.append(callback)):
            CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',playlists,from_drop=True,drop_target=(7,'fixture'))
        host.tag_controller.owned=False
        pending[0]()
        host._begin_cdj_export.assert_called_once_with('/not-a-device',playlists,settings=None,from_drop=True,drop_target=(7,'fixture'),standalone=(),target_role='generic_usb')

    def test_pending_native_playlist_save_drains_before_export_and_rebinds_membership(self):
        from soundbox.library import Playlist
        old=Playlist('Before',[],['one','two'],'1000',export_key='cust:Original Set')
        new=Playlist('After',[],['one'],'1000')
        pending=[];controller=SimpleNamespace(pending=[object()],queue_error=lambda:'')
        lib=SimpleNamespace(source='/synthetic/master.db',source_library_id='synthetic',playlists=[old])
        host=SimpleNamespace(_shutting_down=False,lib=lib,_begin_cdj_export=mock.Mock(),
            _defer_device_operation_until_scan_finishes=lambda _,**_options:False,_device_operation_owned=lambda *_:False,
            tag_controller=SimpleNamespace(owned=False),playlist_controller=controller,
            _start_pulse=mock.Mock(),cancel_btn=mock.Mock())
        with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:pending.append(callback)):
            CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',[old],from_drop=True,drop_target=(7,'fixture'))
        host._begin_cdj_export.assert_not_called()
        controller.pending=[];lib.playlists=[new];pending[0]()
        expected=Playlist('After',[],['one'],'1000',export_key='cust:Original Set')
        host._begin_cdj_export.assert_called_once_with('/not-a-device',[expected],settings=None,
            from_drop=True,drop_target=(7,'fixture'),standalone=(),target_role='generic_usb')
        self.assertEqual(new.export_key,'')

    def test_durable_metadata_and_failed_tag_assignments_fence_export_without_an_active_worker(self):
        for name in ('metadata_controller','tag_controller'):
            with self.subTest(domain=name):
                callbacks=[]
                owner=SimpleNamespace(owned=False,pending=[dict(error='Save needs attention')],queue_error=lambda:'')
                host=SimpleNamespace(_shutting_down=False,lib=object(),_begin_cdj_export=mock.Mock(),
                    _defer_device_operation_until_scan_finishes=lambda _,**_options:False,
                    _device_operation_owned=lambda *_:False,tag_controller=SimpleNamespace(owned=False),
                    _start_pulse=mock.Mock(),_stop_pulse=mock.Mock(),_flash_status=mock.Mock(),cancel_btn=mock.Mock())
                setattr(host,name,owner)
                with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:callbacks.append(callback)):
                    CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',from_drop=True,drop_target=(7,'fixture'))
                    self.assertEqual(callbacks,[])
                    host._begin_cdj_export.assert_not_called()
                    host._start_pulse.assert_not_called()
                    self.assertIsNone(host._cdj_waiting)
                    self.assertEqual(owner.pending,[dict(error='Save needs attention')])
                    self.assertIn('Save needs attention',host._flash_status.call_args.args[0])

    def test_playlist_deleted_or_source_switched_while_waiting_cannot_export_old_rows(self):
        from soundbox.library import Playlist
        for switch in (False,True):
            with self.subTest(source_switched=switch):
                old=Playlist('Before',[],['one'],'1000');pending=[]
                controller=SimpleNamespace(pending=[object()],queue_error=lambda:'')
                lib=SimpleNamespace(source='/synthetic/master.db',source_library_id='synthetic',playlists=[old])
                host=SimpleNamespace(_shutting_down=False,lib=lib,_begin_cdj_export=mock.Mock(),
                    _defer_device_operation_until_scan_finishes=lambda _,**_options:False,_device_operation_owned=lambda *_:False,
                    tag_controller=SimpleNamespace(owned=False),playlist_controller=controller,
                    _start_pulse=mock.Mock(),_stop_pulse=mock.Mock(),_flash_status=mock.Mock(),cancel_btn=mock.Mock())
                with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:pending.append(callback)):
                    CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',[old],from_drop=True,drop_target=(7,'fixture'))
                controller.pending=[]
                if switch:lib.source_library_id='different-native-library'
                else:lib.playlists=[]
                pending[0]();host._begin_cdj_export.assert_not_called()
                self.assertIsNone(host._cdj_waiting);host._flash_status.assert_called_once()

    def test_same_numeric_playlist_replacement_during_source_wait_cannot_retarget_export(self):
        from soundbox.library import Playlist
        old=Playlist('Set',[],['one'],'1000');callbacks=[]
        controller=SimpleNamespace(pending=[object()],queue_error=lambda:'')
        node=dict(ID='1000',UUID='original-playlist',rb_local_deleted=0)
        lib=SimpleNamespace(source='/synthetic/master.db',source_kind='rekordbox-db',source_library_id='synthetic',
            playlists=[old],source_playlist_catalog={'nodes':[node]})
        host=SimpleNamespace(_shutting_down=False,lib=lib,_begin_cdj_export=mock.Mock(),
            _defer_device_operation_until_scan_finishes=lambda _,**_options:False,_device_operation_owned=lambda *_:False,
            tag_controller=SimpleNamespace(owned=False),playlist_controller=controller,
            _start_pulse=mock.Mock(),_stop_pulse=mock.Mock(),_flash_status=mock.Mock(),cancel_btn=mock.Mock())
        with mock.patch('desktop.cdj.QTimer.singleShot',side_effect=lambda _ms,callback:callbacks.append(callback)):
            CdjDeviceMixin._begin_cdj_export(host,'/not-a-device',[old],from_drop=True,drop_target=(7,'fixture'))
        controller.pending=[];node['UUID']='replacement-playlist'
        lib.playlists=[Playlist('Set',[],['wrong-song'],'1000')]
        callbacks[0]()
        host._begin_cdj_export.assert_not_called();self.assertIsNone(host._cdj_waiting)
        host._flash_status.assert_called_once()

    def test_local_analysis_fences_capture_before_native_publication_is_discovered(self):
        from desktop.cdj import _source_edits_pending
        host=SimpleNamespace(tag_controller=SimpleNamespace(owned=False),settings={'analysis_jobs':[{'id':'accepted'}]},
            native_analysis_controller=SimpleNamespace(pending=[]),_analysis_worker=None)
        self.assertTrue(_source_edits_pending(host))
        host.settings['analysis_jobs']=[];host._analysis_worker=object()
        self.assertTrue(_source_edits_pending(host))
        host._analysis_worker=None;host._analysis_owner=SimpleNamespace(pending=1)
        self.assertTrue(_source_edits_pending(host))
        host._analysis_owner.pending=0
        self.assertFalse(_source_edits_pending(host))

    def test_settings_and_update_actions_publish_without_a_second_review(self):
        host,started=self.host()
        plan=SimpleNamespace(changes=[Change('PIONEER/export.pdb','0'*64,'1'*64,1)],close=mock.Mock())
        with mock.patch('desktop.cdj.QMessageBox') as dialog:
            CdjDeviceMixin._review_cdj_plan(host,'/not-a-device',plan)
            dialog.assert_not_called()
        self.assertEqual(len(started),1)
        plan.close.assert_not_called()
