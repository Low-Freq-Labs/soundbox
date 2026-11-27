"""Explicit USB playlist deletion keeps confirmation and worker fences intact."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication,QDialog,QMessageBox

from desktop.cdj import CdjDeviceMixin,CdjDeleteChoices
from desktop.devices import DeviceFacts
from desktop.usb_device_view import UsbSnapshot
from soundbox.cdj_usb import CdjLibrary,CdjPlaylist
from soundbox.device_profile import DeviceProfile
from soundbox.library import Library
from soundbox.cdj_transaction import Change


class Host(CdjDeviceMixin):
    def __init__(self,root,role):
        self.dev=str(root/'device');self._data_file=root/'state/library.json'
        self.lib=Library({},[],root/'source/master.db',source_kind='rekordbox-db',source_library_id='42')
        self._current_device_path=self.dev;self._current_device_role='' if role=='generic_usb' else role
        self._device_profiles={self.dev:DeviceProfile('generic_usb','USB','msdos',False,False,False,False,False)
            if role=='generic_usb' else DeviceProfile('stock_ipod','iTunes','msdos',False,True,False,True,True)}
        self._device_mount_tokens={self.dev:7};self._usb_view_cache={};self._usb_view_requests=set()
        self._shutting_down=False;self._state_maintenance=False;self._exporting=False;self._export_batch=None
        self.started=[];self._cdj_worker=None
        self._defer_device_operation_until_scan_finishes=mock.Mock(return_value=False)
        self._device_operation_owned=mock.Mock(return_value=False)
        for name in ('_release_eject_ready','_start_pulse','_stop_pulse','_set_device_progress','_sync_cue_editing',
                     '_flash_status','_update_device_action_state','_present_usb_device'):
            setattr(self,name,mock.Mock())
        self.progress=mock.Mock();self.cancel_btn=mock.Mock()

    def _start_device_worker(self,worker,dev,intent):self.started.append((worker,dev,intent))

    def _finish_device_result_worker(self,attribute,worker,callback):
        if getattr(self,attribute) is worker:
            setattr(self,attribute,None);callback(worker.result)


class CdjDeleteUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.facts=DeviceFacts(volume_uuid='generated-volume',writable=True)
        self.native=CdjLibrary((CdjPlaylist('10','0','Folder',True,()),
            CdjPlaylist('11','10','Selected',False,()),CdjPlaylist('12','0','Keep',False,())),
            'USB',None,None,(),0,(('PIONEER/export.pdb',b'catalog-before'),))
        self.facts_mock=self.enterContext(mock.patch('desktop.cdj.device_facts',return_value=self.facts))
        self.reader=self.enterContext(mock.patch('desktop.usb_device_view.read_native_usb_snapshot'))
        self.question=self.enterContext(mock.patch('desktop.cdj.QMessageBox.question',return_value=QMessageBox.Yes))
        self.warning=self.enterContext(mock.patch('desktop.cdj.QMessageBox.warning'))
        self.plan=SimpleNamespace(changes=[Change('PIONEER/export.pdb','0'*64,'1'*64,1)],requires_commit=True,overwritten_playlists=(),close=mock.Mock())
        self.prepare=self.enterContext(mock.patch('soundbox.cdj_export.plan_playlist_delete',return_value=self.plan))
        self.commit=self.enterContext(mock.patch('soundbox.cdj_transaction.commit',return_value={'done':True}))
        self.workers=[]
        self.addCleanup(lambda:[worker.deleteLater() for worker in self.workers])

    def host(self,role='generic_usb'):
        host=Host(self.root,role)
        self.snapshot=UsbSnapshot(Path(host.dev),7,self.facts,native=self.native)
        host._usb_view_cache[host.dev]=self.snapshot;self.reader.return_value=self.snapshot
        return host

    def deliver(self,host):
        worker=host.started[-1][0];self.workers.append(worker)
        worker.run();worker.finished.emit();return worker

    def test_both_roles_read_fresh_confirm_once_then_plan_and_commit_captured_delete(self):
        for role in ('generic_usb','ipod_usb'):
            with self.subTest(role=role):
                host=self.host(role);self.prepare.reset_mock();self.commit.reset_mock();self.question.reset_mock()
                host._begin_cdj_delete(host.dev,['11'],target_role=role)
                self.reader.assert_not_called();self.prepare.assert_not_called();self.question.assert_not_called()
                self.deliver(host)
                self.question.assert_called_once()
                self.assertIn('Folder / Selected',self.question.call_args.args[2])
                self.assertEqual(self.question.call_args.args[-1],QMessageBox.Cancel)
                self.prepare.assert_not_called()
                self.deliver(host)
                self.assertEqual(self.prepare.call_args.args,(host.dev,('11',)))
                options=self.prepare.call_args.kwargs
                self.assertEqual((options['token'],options['volume_uuid'],options['target_role']),(7,'generated-volume',role))
                self.assertEqual(options['expected_catalog_files'],dict(self.native.files))
                self.assertIn(host.lib.source,options['sources'])
                self.commit.assert_not_called()
                self.deliver(host)
                self.commit.assert_called_once_with(self.plan,should_cancel=mock.ANY,on_progress=mock.ANY)
                self.assertEqual(host._flash_status.call_args.args[0],'Playlist delete complete ✓')
                self.reader.reset_mock()

    def test_cancel_confirmation_never_plans_or_commits(self):
        host=self.host();self.question.return_value=QMessageBox.Cancel
        host._begin_cdj_delete(host.dev,['11']);self.deliver(host)
        self.prepare.assert_not_called();self.commit.assert_not_called()
        self.assertEqual(len(host.started),1)

    def test_discovery_deferral_keeps_role_and_refuses_same_path_replacement(self):
        for replaced in (False,True):
            with self.subTest(replaced=replaced):
                host=self.host('ipod_usb');pending=[]
                host._defer_device_operation_until_scan_finishes.side_effect=lambda callback,**_:pending.append(callback) or True
                host._begin_cdj_delete(host.dev,['11'],target_role='ipod_usb')
                self.assertEqual(host.started,[])
                if replaced:host._device_mount_tokens[host.dev]=8
                host._defer_device_operation_until_scan_finishes.side_effect=None
                host._defer_device_operation_until_scan_finishes.return_value=False
                pending.pop()()
                self.assertEqual(bool(host.started),not replaced)
                if not replaced:
                    self.deliver(host);self.deliver(host)
                    self.assertEqual(self.prepare.call_args.kwargs['target_role'],'ipod_usb')
                    host.started[-1][0].cancel();self.deliver(host)

    def test_chooser_uses_fresh_folder_and_playlist_rows_and_cancel_never_shows_delete_confirmation(self):
        host=self.host();dialog=SimpleNamespace(exec=lambda:QDialog.Rejected,playlist_ids=(),deleteLater=mock.Mock())
        with mock.patch('desktop.cdj.CdjDeleteChoices',return_value=dialog) as chooser:
            host._begin_cdj_delete(host.dev);self.deliver(host)
        self.assertEqual(chooser.call_args.args[0],{'10':'Folder (folder)','11':'Folder / Selected','12':'Keep'})
        dialog.deleteLater.assert_called_once();self.question.assert_not_called();self.prepare.assert_not_called()

    def test_explicit_empty_folder_is_confirmed_and_sent_as_exact_root_for_both_roles(self):
        self.native=replace(self.native,playlists=(CdjPlaylist('4','0','SETS',True,()),
            CdjPlaylist('5','4','HAMDI',True,())))
        for role in ('generic_usb','ipod_usb'):
            with self.subTest(role=role):
                host=self.host(role);self.prepare.reset_mock();self.question.reset_mock()
                host._begin_cdj_delete(host.dev,['5'],target_role=role)
                self.deliver(host)
                self.assertIn('SETS / HAMDI (folder)',self.question.call_args.args[2])
                self.assertIn('subfolders',self.question.call_args.args[2])
                self.deliver(host)
                self.assertEqual(self.prepare.call_args.args,(host.dev,('5',)))
                host.started[-1][0].cancel();self.deliver(host)

    def test_stale_cached_catalog_blocks_selected_ids_before_confirmation(self):
        host=self.host();self.reader.return_value=replace(self.snapshot,native=replace(self.native,
            files=(('PIONEER/export.pdb',b'changed'),)))
        host._begin_cdj_delete(host.dev,['11']);self.deliver(host)
        self.question.assert_not_called();self.prepare.assert_not_called();self.commit.assert_not_called()
        self.warning.assert_called_once()

    def test_role_remount_source_and_restore_changes_block_after_read(self):
        for mutation in ('role','mount','source','restore'):
            with self.subTest(mutation=mutation):
                host=self.host('ipod_usb');self.question.reset_mock()
                host._state_repository=SimpleNamespace(generation=1,area=SimpleNamespace(generation=1))
                host._begin_cdj_delete(host.dev,['11'],target_role='ipod_usb')
                if mutation=='role':host._current_device_role=''
                elif mutation=='mount':host._device_mount_tokens[host.dev]=8
                elif mutation=='source':host.lib.source_library_id='different'
                else:host._state_repository.area.generation=2
                self.deliver(host);self.question.assert_not_called()
        self.prepare.assert_not_called();self.commit.assert_not_called()

    def test_change_during_confirmation_and_after_plan_prevents_commit_and_closes_plan(self):
        host=self.host('ipod_usb')
        def change(*_args):host._current_device_role='';return QMessageBox.Yes
        self.question.side_effect=change
        host._begin_cdj_delete(host.dev,['11'],target_role='ipod_usb');self.deliver(host)
        self.prepare.assert_not_called()
        self.question.side_effect=None;host=self.host('ipod_usb')
        host._begin_cdj_delete(host.dev,['11'],target_role='ipod_usb');self.deliver(host)
        plan_worker=host.started[-1][0];self.workers.append(plan_worker);plan_worker.run()
        host._device_mount_tokens[host.dev]=8;plan_worker.finished.emit()
        self.plan.close.assert_called_once();self.commit.assert_not_called()

    def test_late_role_change_before_commit_does_not_publish_or_repaint_stock_page(self):
        host=self.host('ipod_usb');host._begin_cdj_delete(host.dev,['11'],target_role='ipod_usb')
        self.deliver(host);self.deliver(host)
        host._current_device_role='';self.deliver(host)
        self.commit.assert_not_called();self.plan.close.assert_called_once()
        host._present_usb_device.assert_not_called()

    def test_success_keeps_delete_intent_without_repainting_new_stock_selection(self):
        host=self.host('ipod_usb');host._begin_cdj_delete(host.dev,['11'],target_role='ipod_usb')
        self.deliver(host);self.deliver(host)
        worker=host.started[-1][0];self.workers.append(worker);worker.run()
        host._current_device_role='';worker.finished.emit()
        self.assertEqual(host._flash_status.call_args.args[0],'Playlist delete complete ✓')
        host._present_usb_device.assert_not_called()

    def test_overwrite_cancel_closes_named_immutable_plan_and_starts_no_commit(self):
        host=self.host();plan=SimpleNamespace(changes=[Change('PIONEER/export.pdb','0'*64,'1'*64,1)],requires_commit=True,
            overwritten_playlists=('Existing Playlist',),close=mock.Mock())
        self.question.return_value=QMessageBox.Cancel
        host._review_cdj_plan(host.dev,plan)
        self.question.assert_called_once();self.assertIn('Existing Playlist',self.question.call_args.args[2])
        self.assertEqual(self.question.call_args.args[-1],QMessageBox.Cancel)
        plan.close.assert_called_once();self.assertEqual(host.started,[]);self.commit.assert_not_called()

    def test_overwrite_yes_commits_same_plan_and_no_overwrite_needs_no_confirmation(self):
        for names in (('Existing Playlist',),()):
            with self.subTest(names=names):
                host=self.host();self.question.reset_mock();self.commit.reset_mock()
                plan=SimpleNamespace(changes=[Change('PIONEER/export.pdb','0'*64,'1'*64,1)],requires_commit=True,overwritten_playlists=names,close=mock.Mock())
                host._review_cdj_plan(host.dev,plan)
                self.assertEqual(self.question.call_count,1 if names else 0)
                self.commit.assert_not_called();self.deliver(host)
                self.assertIs(self.commit.call_args.args[0],plan);plan.close.assert_called_once()

    def test_real_chooser_offers_only_supplied_leaf_ids(self):
        dialog=CdjDeleteChoices({'11':'Folder / Selected','12':'Keep'})
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.playlist_ids,())
        from PySide6.QtCore import Qt
        dialog.tree.topLevelItem(0).setCheckState(0,Qt.Checked)
        self.assertEqual(dialog.playlist_ids,('11',))
