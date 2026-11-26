"""Two logical exports share one physical mount without sharing selection."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from desktop.devices import DeviceFacts
from desktop.export_dialog import ExportDialog
from soundbox.device_profile import DeviceProfile
from soundbox.library import Playlist
from tests import test_export_dialog as fixtures


class UsbTargetChoices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def targets(self,path):
        common=dict(path=path,name='Generated',free=1000,token=41,uuid='generated')
        return [dict(common,kind='stock_ipod',label='iTunes',role=''),
                dict(common,kind='ipod_usb',label='USB',role='ipod_usb')]

    def test_multiple_roles_start_unchecked_even_when_opened_from_usb(self):
        targets=self.targets('/generated');dialog=ExportDialog([Playlist('A',[],pid='a')],targets,
            preferred=('/generated','ipod_usb'));self.addCleanup(dialog.deleteLater)
        self.assertEqual(len(dialog._targets),2)
        self.assertEqual(dialog.selection()[1],[])
        self.assertIn('USB',dialog.devices.topLevelItem(1).text(0))
        dialog.playlists.topLevelItem(0).setCheckState(0,Qt.Checked)
        self.assertFalse(dialog.export_button.isEnabled())
        dialog.devices.topLevelItem(1).setCheckState(0,Qt.Checked)
        self.assertEqual([row['kind'] for row in dialog.selection()[1]],['ipod_usb'])
        dialog.devices.topLevelItem(0).setCheckState(0,Qt.Checked)
        self.assertEqual([row['kind'] for row in dialog.selection()[1]],['stock_ipod','ipod_usb'])
        self.assertEqual(dialog.selection()[0],['a'])

    def test_stock_preference_never_automatically_selects_one_of_multiple_targets(self):
        for preferred,expected in (('/generated',[]),('',[])):
            with self.subTest(preferred=preferred):
                dialog=ExportDialog([],self.targets('/generated'),preferred=preferred)
                self.addCleanup(dialog.deleteLater)
                self.assertEqual([row['kind'] for row in dialog.selection()[1]],expected)

    def host(self):
        folder=self.enterContext(tempfile.TemporaryDirectory());host=fixtures.Host(Path(folder))
        self.addCleanup(host.deleteLater);path=next(iter(host._device_profiles));token=host._device_mount_tokens[path]
        host._device_profiles={path:DeviceProfile('stock_ipod','iTunes','msdos',False,True,False,True,True)}
        host._device_mount_tokens={path:token};targets=self.targets(path)
        for row in targets:row['token']=token
        host._export_batch['pending']=[targets[1],targets[0]]
        self.enterContext(mock.patch('desktop.export_dialog.device_facts',return_value=
            DeviceFacts(volume_uuid='generated',writable=True)))
        return host,path

    def test_both_logical_targets_serialize_on_the_same_mount_with_distinct_dispatch(self):
        host,path=self.host();host._start_next_export_target()
        self.assertEqual(len(host.calls),1)
        self.assertEqual(host.calls[0][0],path)
        self.assertEqual(host.calls[0][2]['target_role'],'ipod_usb')
        host._maybe_finish_export_target();self.assertEqual(len(host.calls),1)
        host._exporting=False;host._export_target_result(path,None);host._maybe_finish_export_target()
        self.app.processEvents()
        self.assertEqual(len(host.calls),2)
        self.assertNotIn('target_role',host.calls[1][2])
        self.assertEqual(host._device_profiles[path].kind,'stock_ipod')
        self.assertEqual(host._export_batch['outcomes'],['complete'])
        host._exporting=False;host._export_target_result(path,None);host._maybe_finish_export_target()
        self.app.processEvents()
        self.assertIsNone(host._export_batch)
        self.assertEqual(len(host._last_export_batch_results),2)

    def test_remount_between_logical_targets_refuses_the_second_export(self):
        host,path=self.host();host._start_next_export_target()
        host._device_mount_tokens[path]+=1
        host._exporting=False;host._export_target_result(path,None)
        with mock.patch('desktop.export_dialog.QMessageBox.warning'):
            host._maybe_finish_export_target();self.app.processEvents();self.app.processEvents()
        self.assertEqual(len(host.calls),1)
        self.assertIsNone(host._export_batch)
        self.assertEqual(len(host._last_export_batch_results),2)
        self.assertIn('changed',host._last_export_batch_results[1][1])

    def test_a_normal_usb_remains_one_independent_target(self):
        rows=[dict(path='/generated-usb',name='Generated USB',label='USB',free=1000,kind='generic_usb')]
        dialog=ExportDialog([],rows);self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selection()[1],rows)

    def test_two_connected_entries_start_unchecked_even_when_only_one_can_export(self):
        rows=[dict(path='/generated-usb',name='USB',label='USB',free=1000,kind='generic_usb'),
              dict(path='/generated-readonly',name='Read only',label='USB',free=1000,kind='generic_usb',
                   selectable=False,reason='Read only')]
        dialog=ExportDialog([],rows,preferred='/generated-usb');self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selection()[1],[])
        dialog.devices.topLevelItem(0).setCheckState(0,Qt.Checked)
        self.assertEqual(dialog.selection()[1],[rows[0]])
