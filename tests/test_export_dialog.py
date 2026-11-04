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
    def _device_operation_owned(self):return self._exporting
    def _start_pulse(self,*_):pass
    def _stop_pulse(self):pass
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
        with mock.patch('desktop.export_dialog.QMessageBox.information') as message:
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
            self.assertIn('USB B: Export complete',message.call_args.args[2])

    def test_remounted_target_is_skipped_without_redirecting_export(self):
        host=self.host();first=host._export_batch['pending'][0]
        host._device_mount_tokens[first['path']]+=1
        with mock.patch('desktop.export_dialog.QMessageBox.information'):
            host._start_next_export_target();self.drain()
            self.assertEqual([Path(c[0]).name for c in host.calls],['USB B'])
            self.assertIn('Device disconnected or changed',host._export_batch['results'][0][1])
            host._exporting=False;host._maybe_finish_export_target();self.drain()

    def test_cancel_discards_remaining_targets_and_shutdown_admits_none(self):
        host=self.host()
        with mock.patch('desktop.export_dialog.QMessageBox.information'):
            host._start_next_export_target()
            host._export_batch['cancelled']=True;host._export_batch['pending'].clear()
            host._exporting=False;host._maybe_finish_export_target();self.drain()
            self.assertEqual(len(host.calls),1);self.assertIsNone(host._export_batch)
        second=self.host();second._shutting_down=True
        second._start_next_export_target();self.assertEqual(second.calls,[])

    def test_changed_source_or_deleted_selection_cannot_retarget_queued_work(self):
        for change in ('source','playlist'):
            host=self.host()
            if change=='source':host.lib.source='another-source'
            else:host.lib.playlists=[]
            with mock.patch('desktop.export_dialog.QMessageBox.information'):
                host._start_next_export_target();self.drain()
            self.assertEqual(host.calls,[])

    def test_mixed_usb_ipod_queue_routes_selected_playlists_to_each_existing_exporter(self):
        host=self.host();second=host._export_batch['pending'][1]
        second['kind']='stock_ipod';host._device_profiles[second['path']].kind='stock_ipod'
        with mock.patch.object(host,'_begin_stock_ipod_sync',wraps=host._begin_stock_ipod_sync) as stock, \
                mock.patch('desktop.export_dialog.QMessageBox.information'):
            host._start_next_export_target();host._exporting=False
            host._maybe_finish_export_target();self.drain()
            stock.assert_called_once()
            self.assertEqual([p.pid for p in stock.call_args.args[1]],['one'])
            host._exporting=False;host._maybe_finish_export_target();self.drain()
