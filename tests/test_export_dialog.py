"""Generated export selections and queued device identities; no live device I/O."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from desktop.export_dialog import ExportDialog, ExportSelectionMixin
from soundbox.library import Library, Playlist


class Host(ExportSelectionMixin,QWidget):
    def __init__(self,root):
        super().__init__()
        self.lib=Library({},[Playlist('First',['Folder'],pid='one'),Playlist('Second',['Folder'],pid='two')],
                         'synthetic-source',source_library_id='42')
        self._shutting_down=False;self._exporting=False;self._cdj_waiting=None
        self._device_profiles={};self._device_mount_tokens={};self.calls=[]
        self.cancel_btn=QWidget(self);self.progress=QWidget(self)
        self.tag_controller=SimpleNamespace(owned=False)
        targets=[]
        for name in ('USB A','USB B'):
            path=root/name;path.mkdir();dev=str(path)
            self._device_profiles[dev]=SimpleNamespace(kind='generic_usb')
            self._device_mount_tokens[dev]=path.stat().st_dev
            targets.append(dict(path=dev,name=name,token=path.stat().st_dev,uuid=name,kind='generic_usb'))
        self._export_batch=dict(binding=('synthetic-source','42'),keys=['one'],pending=targets,
            active=None,results=[],cancelled=False,error='',started=False)

    def _sync_playlists(self):return []
    def _device_operation_owned(self, target=None):return self._exporting or bool(getattr(self,"_usb_format_device","") and (target is None or target==self._usb_format_device))
    def _start_pulse(self,*_):pass
    def _stop_pulse(self):pass
    def _flash_status(self,*_args):pass
    def _update_device_action_state(self):pass
    def _refresh_devices(self,**_):pass
    def _begin_cdj_export(self,dev,playlists,**options):
        self.calls.append((dev,playlists,options));self._exporting=True
    def _begin_stock_ipod_sync(self,dev,playlists):self._begin_cdj_export(dev,playlists)
    def _begin_dual_ipod_sync(self,dev,playlists):self._begin_cdj_export(dev,playlists)


class ExportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_folder_checks_select_children_and_multiple_devices(self):
        playlists=[Playlist('A',['Folder'],pid='a'),Playlist('B',['Folder'],pid='b'),Playlist('Other',[],pid='other')]
        targets=[dict(path=str(i),name=f'USB {i}',label='USB',free=100) for i in (1,2)]
        dialog=ExportDialog(playlists,targets,preferred='1')
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.export_button.isEnabled())
        dialog.playlists.topLevelItem(0).setCheckState(0,Qt.Checked)
        dialog.devices.topLevelItem(1).setCheckState(0,Qt.Checked)
        keys,devices=dialog.selection()
        self.assertEqual(keys,['a','b']);self.assertEqual([t['path'] for t in devices],['1','2'])
        self.assertTrue(dialog.export_button.isEnabled())
        dialog.playlists.topLevelItem(0).child(0).setCheckState(0,Qt.Unchecked)
        self.assertEqual(dialog.playlists.topLevelItem(0).checkState(0),Qt.PartiallyChecked)
        self.assertEqual(dialog.selection()[0],['b'])

    def test_single_device_is_preselected_and_no_device_cannot_export(self):
        playlist=Playlist('A',[],pid='a')
        for targets in ([],[dict(path='usb',name='Only USB',label='USB',free=100)]):
            dialog=ExportDialog([playlist],targets);self.addCleanup(dialog.deleteLater)
            dialog.playlists.topLevelItem(0).setCheckState(0,Qt.Checked)
            self.assertEqual(dialog.export_button.isEnabled(),bool(targets))
            self.assertEqual(len(dialog.selection()[1]),len(targets))

    def test_nested_folders_start_collapsed_and_checking_them_keeps_them_collapsed(self):
        dialog=ExportDialog([Playlist('A',['Folder','Nested'],pid='a')],[])
        self.addCleanup(dialog.deleteLater)
        folder=dialog.playlists.topLevelItem(0);nested=folder.child(0)
        self.assertFalse(folder.isExpanded());self.assertFalse(nested.isExpanded())
        folder.setCheckState(0,Qt.Checked)
        self.assertEqual(dialog.selection()[0],['a'])
        self.assertFalse(folder.isExpanded());self.assertFalse(nested.isExpanded())

    def test_devices_have_large_distinct_icons_and_five_slots_resize_with_the_panel(self):
        targets=[dict(path=str(i),name=f'Device {i}',label='USB',free=100,
                      kind='stock_ipod' if i==1 else 'generic_usb') for i in range(5)]
        dialog=ExportDialog([],targets);self.addCleanup(dialog.deleteLater);self.addCleanup(dialog.close)
        dialog.show();self.app.processEvents()
        self.assertEqual(dialog.devices.iconSize().toTuple(),(44,44))
        self.assertFalse(dialog.devices.topLevelItem(0).icon(0).isNull())
        self.assertNotEqual(dialog.devices.topLevelItem(0).icon(0).pixmap(44,44).toImage(),
                            dialog.devices.topLevelItem(1).icon(0).pixmap(44,44).toImage())
        before=dialog.devices.visualItemRect(dialog.devices.topLevelItem(0)).height()
        self.assertGreaterEqual(before,68)
        self.assertLessEqual(dialog.devices.visualItemRect(dialog.devices.topLevelItem(4)).bottom(),
                             dialog.devices.viewport().height())
        dialog.resize(800,800);self.app.processEvents()
        self.assertGreater(dialog.devices.visualItemRect(dialog.devices.topLevelItem(0)).height(),before)

    def test_one_or_two_devices_fill_the_available_panel(self):
        for count in (1,2):
            targets=[dict(path=str(i),name=f'TEST-{i}',label='USB',free=100) for i in range(count)]
            dialog=ExportDialog([],targets);self.addCleanup(dialog.deleteLater);self.addCleanup(dialog.close)
            dialog.show();self.app.processEvents()
            row=dialog.devices.visualItemRect(dialog.devices.topLevelItem(0))
            self.assertAlmostEqual(row.height()*count,dialog.devices.viewport().height(),delta=count)

    def host(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        host=Host(Path(area.name));self.addCleanup(host.deleteLater)
        facts=lambda dev,**_:SimpleNamespace(volume_uuid=Path(dev).name,writable=True)
        patch=mock.patch('desktop.export_dialog.device_facts',side_effect=facts);patch.start();self.addCleanup(patch.stop)
        return host

    def drain(self):
        for _ in range(4):self.app.processEvents()

    def test_two_targets_serialize_and_rebind_selected_playlist_after_first_failure(self):
        host=self.host()
        with mock.patch('desktop.export_dialog.QMessageBox.warning') as message:
            host._start_next_export_target();self.assertEqual(len(host.calls),1)
            first=host.calls[0][0]
            host._export_target_result(first,ValueError('USB write failed'))
            host._exporting=False;host._maybe_finish_export_target()
            host.lib.playlists[0]=Playlist('Renamed',['Folder'],pid='one')
            self.drain()
            self.assertEqual(len(host.calls),2)
            self.assertEqual([p.name for p in host.calls[1][1]],['Renamed'])
            self.assertTrue(host.calls[1][2]['from_drop'])
            self.assertEqual(host.calls[1][2]['drop_target'],(Path(host.calls[1][0]).stat().st_dev,'USB B'))
            host._exporting=False;host._maybe_finish_export_target();self.drain()
            self.assertIsNone(host._export_batch)
            self.assertIn('USB A: USB write failed',message.call_args.args[2])
            self.assertNotIn('USB B: Export complete',message.call_args.args[2])
            self.assertIn(('USB B','Export complete'),host._last_export_batch_results)

    def test_remounted_target_is_skipped_without_redirecting_export(self):
        host=self.host();first=host._export_batch['pending'][0]
        host._device_mount_tokens[first['path']]+=1
        with mock.patch('desktop.export_dialog.QMessageBox.warning'):
            host._start_next_export_target();self.drain()
            self.assertEqual([Path(c[0]).name for c in host.calls],['USB B'])
            self.assertIn('Device disconnected or changed',host._export_batch['results'][0][1])
            host._exporting=False;host._maybe_finish_export_target();self.drain()

    def test_cancel_discards_remaining_targets_and_shutdown_admits_none(self):
        host=self.host()
        with mock.patch('desktop.export_dialog.QMessageBox.warning'):
            host._start_next_export_target()
            host._export_batch['cancelled']=True;host._export_batch['pending'].clear()
            host._exporting=False;host._maybe_finish_export_target();self.drain()
            self.assertEqual(len(host.calls),1);self.assertIsNone(host._export_batch)
        second=self.host();second._shutting_down=True
        second._start_next_export_target();self.assertEqual(second.calls,[])

    def test_finished_batch_does_not_start_another_device_scan(self):
        host=self.host()
        host._export_batch['pending'].clear()
        with mock.patch.object(host,'_refresh_devices',side_effect=AssertionError('Completion scan')), \
                mock.patch('desktop.export_dialog.QMessageBox.warning'):
            host._start_next_export_target()
        self.assertIsNone(host._export_batch)

    def test_formatting_first_target_does_not_block_export_to_second_or_stat_the_mount(self):
        host=self.host()
        first,second=host._export_batch['pending']
        host._usb_format_device=first['path']
        host._usb_format_volumes=(first['path'],)
        with mock.patch('desktop.export_dialog.QMessageBox.warning'), \
                mock.patch('desktop.export_dialog.Path.stat',side_effect=AssertionError('UI-thread USB I/O')):
            host._start_next_export_target();self.drain()
        self.assertEqual([call[0] for call in host.calls],[second['path']])
        self.assertIn('Device disconnected or changed',host._export_batch['results'][0][1])

    def test_export_dialog_omits_all_volumes_of_formatting_disk(self):
        host=self.host()
        first,second=host._export_batch['pending']
        host._export_batch=None
        host._usb_format_device=first['path']
        host._usb_format_volumes=(first['path'],)
        host._usb_device_rows={}
        for profile in host._device_profiles.values():
            profile.cdj_export=True;profile.stock_write=False;profile.rockbox_export=False;profile.label='USB'
        host._start_next_export_target=mock.Mock()
        captured=[]
        def choose(playlists,targets,**options):
            captured.extend(targets)
            return SimpleNamespace(exec=lambda:1,selection=lambda:(['one'],targets),deleteLater=mock.Mock())
        with mock.patch('desktop.export_dialog.ExportDialog',side_effect=choose), \
                mock.patch('desktop.export_dialog.device_facts',side_effect=lambda dev,**_:SimpleNamespace(
                    volume_uuid=Path(dev).name,writable=True,free_bytes=100)):
            host._show_export_dialog(second['path'])
        self.assertEqual([target['path'] for target in captured],[second['path']])
        host._start_next_export_target.assert_called_once()

    def test_rejected_export_dialog_retires_its_parented_widget_tree(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        host=self.host();host._export_batch=None;host._usb_device_rows={}
        for profile in host._device_profiles.values():
            profile.cdj_export=True;profile.stock_write=False;profile.rockbox_export=False;profile.label='USB'
        with mock.patch.object(ExportDialog,'exec',return_value=0), \
                mock.patch('desktop.export_dialog.device_facts',side_effect=lambda dev,**_:SimpleNamespace(
                    volume_uuid=Path(dev).name,writable=True,free_bytes=100)):
            for _ in range(5):host._show_export_dialog()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertEqual(host.findChildren(ExportDialog),[])

    def test_changed_source_or_deleted_selection_cannot_retarget_queued_work(self):
        for change in ('source','playlist'):
            host=self.host()
            if change=='source':host.lib.source='another-source'
            else:host.lib.playlists=[]
            with mock.patch('desktop.export_dialog.QMessageBox.warning'):
                host._start_next_export_target();self.drain()
            self.assertEqual(host.calls,[])

    def test_source_error_or_pause_ends_wait_and_preserves_other_device_progress(self):
        from copy import deepcopy
        for blocker in ('error','pause'):
            for other_owned in (False,True):
                with self.subTest(blocker=blocker,other_owned=other_owned):
                    host=self.host();timers=[]
                    host.metadata_controller=SimpleNamespace(pending=[{'error':''}],
                        queue_error=lambda:'',migration_pending=False,background_paused=False)
                    host._start_pulse=mock.Mock();host._stop_pulse=mock.Mock()
                    host._export_batch['results']=[('Earlier USB','Export complete')]
                    host._export_batch['outcomes']=['complete']
                    host.progress.setVisible(True)
                    with mock.patch('desktop.export_dialog.QTimer.singleShot',
                            side_effect=lambda _ms,fn:timers.append(fn)), \
                            mock.patch('desktop.export_dialog.QMessageBox.warning') as message:
                        host._start_next_export_target()
                        self.assertEqual(len(timers),1);self.assertEqual(host.calls,[])
                        if blocker=='error':
                            host.metadata_controller.pending[0]['error']='Selected song changed; refresh before retrying'
                            expected='Selected song changed'
                        else:
                            host.metadata_controller.background_paused=True;expected='resume'
                        intentions=deepcopy(host.metadata_controller.pending)
                        host._exporting=other_owned
                        timers.pop(0)()
                        self.assertEqual(timers,[]);self.assertEqual(host.calls,[])
                        self.assertIsNone(host._export_batch)
                        self.assertEqual(host.metadata_controller.pending,intentions)
                        summary=message.call_args.args[2]
                        self.assertNotIn('Earlier USB: Export complete',summary)
                        self.assertIn(('Earlier USB','Export complete'),host._last_export_batch_results)
                        self.assertIn('USB A:',summary);self.assertIn('USB B:',summary)
                        self.assertIn(expected,summary)
                        self.assertEqual(host._stop_pulse.called,not other_owned)
                        self.assertEqual(host.cancel_btn.isHidden(),not other_owned)
                        self.assertEqual(host.progress.isHidden(),not other_owned)

    def test_initial_paused_source_never_polls_or_starts_export(self):
        host=self.host();host.metadata_controller=SimpleNamespace(pending=[],queue_error=lambda:'',
            migration_pending=True,background_paused=True)
        host._start_pulse=mock.Mock()
        with mock.patch('desktop.export_dialog.QTimer.singleShot') as timer, \
                mock.patch('desktop.export_dialog.QMessageBox.warning') as message:
            host._start_next_export_target()
        self.assertIsNone(host._export_batch);self.assertEqual(host.calls,[])
        timer.assert_not_called();host._start_pulse.assert_not_called()
        self.assertIn('resume',message.call_args.args[2])

    def test_blocked_batch_preserves_another_mount_format_progress(self):
        host=self.host();host._usb_format_device=host._export_batch['pending'][0]['path']
        host._show_usb_format_status=mock.Mock();host._stop_pulse=mock.Mock()
        host.cancel_btn.setVisible(True);host.progress.setVisible(True)
        host.metadata_controller=SimpleNamespace(pending=[],queue_error=lambda:'',
            migration_pending=True,background_paused=True)
        with mock.patch('desktop.export_dialog.QTimer.singleShot') as timer, \
                mock.patch('desktop.export_dialog.QMessageBox.warning'):
            host._start_next_export_target()
        self.assertIsNone(host._export_batch);self.assertEqual(host.calls,[])
        timer.assert_not_called();host._stop_pulse.assert_not_called()
        self.assertFalse(host.cancel_btn.isHidden());self.assertFalse(host.progress.isHidden())
        host._show_usb_format_status.assert_called_once()

    def test_replayed_wait_callback_cannot_start_a_second_target_during_export(self):
        host=self.host();timers=[]
        host.metadata_controller=SimpleNamespace(pending=[{'error':''}],queue_error=lambda:'',
            migration_pending=False)
        with mock.patch('desktop.export_dialog.QTimer.singleShot',side_effect=lambda _ms,fn:timers.append(fn)):
            host._start_next_export_target();waiting=timers.pop(0)
            host.metadata_controller.pending=[];waiting()
            batch=host._export_batch;first=batch['active'];remaining=list(batch['pending'])
            # Even if the actor is between physical phases, its captured batch
            # target still owns admission until the normal result continuation.
            host._exporting=False;waiting()
        self.assertEqual(len(host.calls),1);self.assertIs(batch['active'],first)
        self.assertEqual(batch['pending'],remaining);self.assertEqual(timers,[])

    def test_mixed_usb_ipod_queue_routes_selected_playlists_to_each_existing_exporter(self):
        host=self.host();second=host._export_batch['pending'][1]
        second['kind']='stock_ipod';host._device_profiles[second['path']].kind='stock_ipod'
        with mock.patch.object(host,'_begin_stock_ipod_sync',wraps=host._begin_stock_ipod_sync) as stock, \
                mock.patch('desktop.export_dialog.QMessageBox.warning'):
            host._start_next_export_target();host._exporting=False
            host._maybe_finish_export_target();self.drain()
            stock.assert_called_once()
            self.assertEqual([p.pid for p in stock.call_args.args[1]],['one'])
            host._exporting=False;host._maybe_finish_export_target();self.drain()

    def test_existing_stock_playlist_sync_uses_displayed_values_without_waiting_for_native_backlog(self):
        for blocked in ('paused', 'error'):
            with self.subTest(blocked=blocked):
                host=self.host();target=host._export_batch['pending'][0]
                host._export_batch['pending']=[target];target['kind']='stock_ipod'
                host._device_profiles[target['path']].kind='stock_ipod'
                host._stock_view_cache={target['path']:(object(),None)}
                host.metadata_controller=SimpleNamespace(pending=[{'error':'Native save needs attention' if blocked=='error' else ''}],
                    queue_error=lambda:'',migration_pending=True,background_paused=blocked=='paused')
                selections=(('123','First',212),)
                def sync(owner, selected, **options):
                    self.assertIs(owner,host);self.assertEqual(selected,selections)
                    self.assertEqual(options['device'],target['path']);self.assertTrue(options['confirmed'])
                    self.assertEqual([p.pid for p in options['source_playlists']],['one'])
                    owner._exporting=True
                with (
                    mock.patch('desktop.stock_catalog_sync.source_sync_selection',return_value=selections),
                    mock.patch('desktop.stock_catalog_sync.request_sync',side_effect=sync) as start,
                    mock.patch('desktop.export_dialog.QTimer.singleShot') as timer,
                    mock.patch('desktop.export_dialog.QMessageBox.warning') as message,
                ):
                    host._start_next_export_target()
                    start.assert_called_once();timer.assert_not_called();message.assert_not_called()
                    self.assertIs(host._export_batch['active'],target)
                    self.assertEqual(host.calls,[])
                host._export_batch=None
